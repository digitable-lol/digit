/**
 * Turn a digitdisk `status --json` payload into rows the `/machine` panel draws.
 *
 * This is deliberately a pure module with no Ink imports: the panel component
 * stays a thin mapping from these rows to `<Text>`, and the interesting logic
 * — unit choice, thresholds, which of 16 mounts are worth a line, what to say
 * when the tool is absent — is unit-testable without mounting a terminal.
 *
 * digit renders digitdisk's numbers; it never recomputes them.  Anything not
 * in the payload is reported as not measured rather than guessed at.
 */

import type {
  DigitdiskDisk,
  DigitdiskGpu,
  DigitdiskProc,
  DigitdiskSnapshot,
  DigitdiskStatusResponse
} from '../gatewayTypes.js'

/** Severity band; the component maps these onto theme status colors. */
export type Tone = 'bad' | 'critical' | 'good' | 'muted' | 'plain' | 'warn'

/** A label + value line, e.g. `kernel   7.0.0-29-generic (x86_64)`. */
export interface TextRow {
  /**
   * Put the value on its own indented, wrapped line instead of in a column.
   * For rows whose label is prose rather than a field name — a GPU model, or
   * digitdisk's "why this was not measured" notes — where a fixed label
   * column would either truncate the label or shove the value off-screen.
   */
  block?: boolean
  kind: 'text'
  label: string
  tone?: Tone
  value: string
}

/** A label + bar + value line, e.g. memory or a disk mount. */
export interface MeterRow {
  kind: 'meter'
  label: string
  /** 0..1; drives the bar fill and the tone. */
  ratio: number
  tone: Tone
  value: string
}

/** A pre-aligned table line (header or body) — disks, network, processes. */
export interface ColumnsRow {
  kind: 'columns'
  header?: boolean
  text: string
  tone?: Tone
}

/** One glyph per CPU core, wrapped into terminal-width chunks. */
export interface CoresRow {
  cells: { glyph: string; tone: Tone }[]
  kind: 'cores'
}

export type MachineRow = ColumnsRow | CoresRow | MeterRow | TextRow

export interface MachineSection {
  /** Shown under the title when the section has nothing to show. */
  empty?: string
  rows: MachineRow[]
  title: string
}

export interface MachineView {
  /** Provenance line: which binary, which version, when the sample was taken. */
  provenance: string
  sections: MachineSection[]
  /** Non-fatal caveat shown under the header, e.g. an unverified dev build. */
  warning?: string
}

/** What to draw when there is no snapshot to draw. */
export interface MachineNotice {
  detail: string[]
  title: string
  tone: Tone
}

const KIB = 1024

/** `[` + 10 bar cells + `] ` — what a meter row inserts before its value. */
export const METER_PREFIX = 13

/** Bytes in IEC units, matching digitdisk's own choice of GiB over GB. */
export function formatBytes(bytes: null | number | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes)) {
    return '—'
  }

  if (bytes < KIB) {
    return `${Math.round(bytes)} B`
  }

  const units = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let value = bytes / KIB
  let unit = 0

  while (value >= KIB && unit < units.length - 1) {
    value /= KIB
    unit += 1
  }

  return `${value.toFixed(value >= 100 ? 0 : digits)} ${units[unit]}`
}

export function formatPercent(value: null | number | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? '—' : `${value.toFixed(digits)}%`
}

/** Thousands separators, so `789243174` reads as a packet count. */
export function formatCount(value: null | number | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : Math.round(value).toLocaleString('en-US')
}

/**
 * Load bands. Deliberately the same four everywhere (CPU, memory, disk) so a
 * colour means one thing across the whole panel: green is fine, red is not.
 */
export function toneForRatio(ratio: number): Tone {
  if (!Number.isFinite(ratio)) {
    return 'muted'
  }

  if (ratio >= 0.95) {
    return 'critical'
  }

  if (ratio >= 0.85) {
    return 'bad'
  }

  if (ratio >= 0.6) {
    return 'warn'
  }

  return 'good'
}

const SPARK = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']

/** One glyph per core: height AND colour carry the load, so it reads in mono. */
export function coreGlyph(percent: number): string {
  const r = Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0)) / 100

  return SPARK[Math.min(SPARK.length - 1, Math.round(r * (SPARK.length - 1)))]!
}

const pad = (text: string, width: number) =>
  text.length >= width ? text.slice(0, width) : text + ' '.repeat(width - text.length)

const padStart = (text: string, width: number) =>
  text.length >= width ? text.slice(text.length - width) : ' '.repeat(width - text.length) + text

/** Middle-elide, so a long path keeps both its root and its leaf. */
export function elide(text: string, width: number): string {
  if (width <= 1 || text.length <= width) {
    return text
  }

  if (width <= 3) {
    return text.slice(0, width)
  }

  const head = Math.ceil((width - 1) / 2)

  return `${text.slice(0, head)}…${text.slice(text.length - (width - head - 1))}`
}

const ratioOf = (used: null | number | undefined, total: null | number | undefined) =>
  total == null || total <= 0 || used == null ? Number.NaN : Math.max(0, Math.min(1, used / total))

// ── Sections ─────────────────────────────────────────────────────────

function hostSection(snap: DigitdiskSnapshot): MachineSection {
  const h = snap.host ?? {}
  const load = snap.load ?? {}
  const cpu = [h.cpu_model, load.cpu_count ? `× ${load.cpu_count}` : ''].filter(Boolean).join(' ')
  const kernel = [h.kernel_release, h.machine ? `(${h.machine})` : ''].filter(Boolean).join(' ')
  const rows: MachineRow[] = []

  const push = (label: string, value: string | undefined) => {
    if (value) {
      rows.push({ kind: 'text', label, value })
    }
  }

  push('host', h.hostname)
  push('system', h.distro)
  push('model', h.model)
  push('cpu', cpu || undefined)
  push('kernel', kernel || undefined)
  push('uptime', h.uptime_human)

  return { rows, title: 'system' }
}

function cpuSection(snap: DigitdiskSnapshot): MachineSection {
  const load = snap.load ?? {}
  const rows: MachineRow[] = []
  const avg = [load['1min'], load['5min'], load['15min']]

  if (avg.some(v => v != null)) {
    rows.push({
      kind: 'text',
      label: 'load avg',
      value: `${avg.map(v => (v == null ? '—' : v.toFixed(2))).join('  ')}   (1 / 5 / 15 min)`
    })
  }

  const busy = load.busy_percent

  if (busy != null) {
    const sample = load.sample_millis ? ` · ${load.sample_millis} ms sample` : ''

    rows.push({
      kind: 'meter',
      label: 'busy',
      ratio: busy / 100,
      tone: toneForRatio(busy / 100),
      value: `${formatPercent(busy)}${sample}`
    })
  }

  const cores = load.cores ?? []

  if (cores.length) {
    const pcts = cores.map(c => (typeof c.busy_percent === 'number' ? c.busy_percent : 0))
    const sorted = [...pcts].sort((a, b) => a - b)
    const median = sorted[Math.floor(sorted.length / 2)] ?? 0
    const max = sorted[sorted.length - 1] ?? 0
    const hot = pcts.filter(p => p >= 50).length

    rows.push({
      kind: 'text',
      label: 'cores',
      value:
        `${cores.length} · min ${formatPercent(sorted[0] ?? 0, 0)} · median ${formatPercent(median, 0)} ` +
        `· max ${formatPercent(max, 0)} · ${hot} over half`
    })
    rows.push({ cells: pcts.map(p => ({ glyph: coreGlyph(p), tone: toneForRatio(p / 100) })), kind: 'cores' })
  }

  const p = snap.processes ?? {}

  if (p.total != null) {
    rows.push({
      kind: 'text',
      label: 'processes',
      value: `${formatCount(p.total)} total · ${formatCount(p.threads)} threads · ${formatCount(p.running)} running`
    })
  }

  return { empty: 'digitdisk reported no cpu figures', rows, title: 'cpu' }
}

function memorySection(snap: DigitdiskSnapshot): MachineSection {
  const m = snap.memory ?? {}
  const rows: MachineRow[] = []
  const usedRatio = ratioOf(m.used_bytes, m.total_bytes)

  if (Number.isFinite(usedRatio)) {
    rows.push({
      kind: 'meter',
      label: 'used',
      ratio: usedRatio,
      tone: toneForRatio(usedRatio),
      value: `${formatBytes(m.used_bytes)} of ${formatBytes(m.total_bytes)}  (${formatPercent(usedRatio * 100)})`
    })
  }

  if (m.available_bytes != null) {
    rows.push({ kind: 'text', label: 'available', value: formatBytes(m.available_bytes) })
  }

  if (m.buff_cache_bytes != null) {
    rows.push({ kind: 'text', label: 'cache/buffers', value: formatBytes(m.buff_cache_bytes) })
  }

  const swapRatio = ratioOf(m.swap_used_bytes, m.swap_total_bytes)

  if (m.swap_total_bytes) {
    rows.push({
      kind: 'meter',
      label: 'swap',
      ratio: Number.isFinite(swapRatio) ? swapRatio : 0,
      tone: toneForRatio(swapRatio),
      value: `${formatBytes(m.swap_used_bytes)} of ${formatBytes(m.swap_total_bytes)}`
    })
  }

  return { empty: 'digitdisk reported no memory figures', rows, title: 'memory' }
}

const procLabel = (p: DigitdiskProc) => p.cmdline?.trim() || p.comm || '—'

function processSection(snap: DigitdiskSnapshot, cols: number): MachineSection {
  const p = snap.processes ?? {}
  const top = (p.top_by_memory ?? []).slice(0, 5)
  const rows: MachineRow[] = []
  const cmdWidth = Math.max(20, cols - 30)

  if (top.length) {
    rows.push({
      header: true,
      kind: 'columns',
      text: `${padStart('pid', 9)}  ${pad('user', 8)}  ${padStart('memory', 10)}  command`
    })

    for (const proc of top) {
      rows.push({
        kind: 'columns',
        text:
          `${padStart(String(proc.pid ?? '—'), 9)}  ${pad(proc.user ?? '—', 8)}  ` +
          `${padStart(formatBytes(proc.rss_bytes), 10)}  ${elide(procLabel(proc), cmdWidth)}`
      })
    }
  }

  return { empty: 'digitdisk listed no processes', rows, title: 'heaviest processes' }
}

/** Mounts worth a line, biggest first — a 1 MiB credentials tmpfs is noise. */
export function rankDisks(disks: DigitdiskDisk[]): DigitdiskDisk[] {
  return [...disks]
    .filter(d => (d.total_bytes ?? 0) > 16 * KIB * KIB)
    .sort((a, b) => (b.used_bytes ?? 0) - (a.used_bytes ?? 0))
}

function diskSection(snap: DigitdiskSnapshot, cols: number): MachineSection {
  const all = snap.disks ?? []
  const ranked = rankDisks(all)
  const rows: MachineRow[] = []
  const mountWidth = Math.max(12, Math.min(28, cols - 46))

  if (ranked.length) {
    rows.push({
      header: true,
      kind: 'columns',
      // The meter row inserts `[bar] ` between label and value, so the header
      // has to skip the same width or every number sits under the wrong word.
      text:
        `${pad('mount', mountWidth)}${' '.repeat(METER_PREFIX)}` +
        `${padStart('size', 9)}  ${padStart('used', 9)}  ${padStart('free', 9)}  ${padStart('use', 5)}`
    })
  }

  for (const d of ranked) {
    const ratio = d.use_percent != null ? d.use_percent / 100 : ratioOf(d.used_bytes, d.total_bytes)

    rows.push({
      kind: 'meter',
      label: pad(elide(d.mount_point ?? '—', mountWidth), mountWidth),
      ratio: Number.isFinite(ratio) ? ratio : 0,
      tone: toneForRatio(ratio),
      value:
        `${padStart(formatBytes(d.total_bytes), 9)}  ${padStart(formatBytes(d.used_bytes), 9)}  ` +
        `${padStart(formatBytes(d.available_bytes), 9)}  ${padStart(formatPercent(d.use_percent, 0), 5)}`
    })
  }

  const hidden = all.length - ranked.length

  if (hidden > 0) {
    rows.push({ kind: 'text', label: '', tone: 'muted', value: `+${hidden} mounts under 16 MiB not shown` })
  }

  return { empty: 'digitdisk reported no mounts', rows, title: 'disks' }
}

function networkSection(snap: DigitdiskSnapshot): MachineSection {
  const nets = [...(snap.network ?? [])].sort(
    (a, b) => (b.rx_bytes ?? 0) + (b.tx_bytes ?? 0) - ((a.rx_bytes ?? 0) + (a.tx_bytes ?? 0))
  )

  const rows: MachineRow[] = []

  if (nets.length) {
    rows.push({
      header: true,
      kind: 'columns',
      text: `${pad('interface', 12)}  ${pad('state', 8)}  ${padStart('received', 11)}  ${padStart('sent', 11)}`
    })
  }

  for (const n of nets) {
    const state = n.oper_state ?? '—'

    rows.push({
      kind: 'columns',
      text:
        `${pad(n.name ?? '—', 12)}  ${pad(state, 8)}  ` +
        `${padStart(formatBytes(n.rx_bytes), 11)}  ${padStart(formatBytes(n.tx_bytes), 11)}`,
      tone: state === 'up' ? 'good' : state === 'down' ? 'muted' : 'plain'
    })
  }

  return { empty: 'digitdisk reported no interfaces', rows, title: 'network' }
}

/** GPU detail digitdisk could read; drivers vary wildly in what they expose. */
export function gpuDetail(g: DigitdiskGpu): string {
  const bits: string[] = []

  if (g.busy_percent != null) {
    bits.push(`busy ${formatPercent(g.busy_percent, 0)}`)
  }

  if (g.memory_used_bytes != null || g.memory_total_bytes != null) {
    bits.push(`memory ${formatBytes(g.memory_used_bytes)} of ${formatBytes(g.memory_total_bytes)}`)
  }

  if (g.celsius != null) {
    bits.push(`${g.celsius.toFixed(0)}°C`)
  }

  // Saying WHY there are no numbers beats an empty row: the usual cause is a
  // driver that publishes nothing, not a failure on our side.
  return bits.length ? bits.join(' · ') : `driver ${g.driver ?? '?'} publishes no load or memory`
}

function gpuSection(snap: DigitdiskSnapshot): MachineSection {
  const rows: MachineRow[] = (snap.gpus ?? []).map(g => ({
    block: true,
    kind: 'text' as const,
    label: g.name ?? g.vendor ?? '—',
    value: gpuDetail(g)
  }))

  return { empty: 'no graphics cards found', rows, title: 'graphics' }
}

function missingSection(snap: DigitdiskSnapshot): MachineSection {
  const entries = Object.entries(snap.missing ?? {})

  return {
    rows: entries.map(([label, why]) => ({
      block: true,
      kind: 'text' as const,
      label,
      tone: 'muted' as const,
      value: why
    })),
    title: 'not measured'
  }
}

// ── Entry points ─────────────────────────────────────────────────────

/**
 * Build the whole panel body from an `ok` reply.
 *
 * `cols` only affects column widths; every figure comes from the payload.
 */
export function buildMachineView(res: DigitdiskStatusResponse, cols = 80): MachineView {
  const snap = res.snapshot ?? {}

  const sections = [
    hostSection(snap),
    cpuSection(snap),
    memorySection(snap),
    processSection(snap, cols),
    diskSection(snap, cols),
    networkSection(snap),
    gpuSection(snap),
    missingSection(snap)
  ].filter(s => s.rows.length || s.empty)

  const version = res.version ?? (res.version_known === false ? 'dev build' : 'unknown version')
  const taken = snap.taken_at ? ` · sampled ${snap.taken_at}` : ''

  return {
    provenance: `digitdisk ${version} · ${res.binary ?? 'digitdisk'}${taken}`,
    sections,
    warning:
      res.version_known === false
        ? `this digitdisk reports no release version, so it was not checked against the ${res.required} floor this panel needs`
        : undefined
  }
}

/**
 * What to render when there is no snapshot: the reason, in words, plus the
 * version needed as a number.  Never a blank panel and never a stack trace —
 * "digitdisk is not installed" is an answer, and it should look like one.
 */
export function buildMachineNotice(res: DigitdiskStatusResponse): MachineNotice | null {
  if (res.state === 'ok') {
    return null
  }

  if (res.state === 'missing') {
    return {
      detail: [
        `this panel renders the machine snapshot from digitdisk ${res.required} or newer.`,
        res.hint ?? ''
      ].filter(Boolean),
      title: 'digitdisk is not installed',
      tone: 'warn'
    }
  }

  if (res.state === 'outdated') {
    return {
      detail: [
        `found ${res.version ?? 'an older build'} at ${res.binary ?? 'digitdisk'}; this panel needs ${res.required} or newer.`,
        'its snapshot was not read — the payload shape changes between versions.',
        res.hint ?? ''
      ].filter(Boolean),
      title: `digitdisk ${res.version ?? ''} is too old`.replace(/\s+/g, ' '),
      tone: 'bad'
    }
  }

  return {
    detail: [res.error ?? 'digitdisk did not return a snapshot.', `binary: ${res.binary ?? 'digitdisk'}`],
    title: 'digitdisk could not be read',
    tone: 'bad'
  }
}
