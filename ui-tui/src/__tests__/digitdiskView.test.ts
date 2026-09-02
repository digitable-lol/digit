import { describe, expect, it } from 'vitest'

import type { DigitdiskStatusResponse } from '../gatewayTypes.js'
import {
  buildMachineNotice,
  buildMachineView,
  coreGlyph,
  elide,
  formatBytes,
  formatPercent,
  gpuDetail,
  rankDisks,
  toneForRatio
} from '../lib/digitdiskView.js'

const MIB = 1024 * 1024
const GIB = 1024 * MIB

describe('formatting', () => {
  it('uses IEC units, matching digitdisk itself', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1023)).toBe('1023 B')
    expect(formatBytes(1024)).toBe('1.0 KiB')
    expect(formatBytes(535951720448)).toBe('499 GiB')
    expect(formatBytes(1.5 * GIB)).toBe('1.5 GiB')
  })

  it('says "—" rather than 0 for a figure the tool did not report', () => {
    // A GPU driver that publishes no memory must not read as "0 bytes used".
    expect(formatBytes(null)).toBe('—')
    expect(formatBytes(undefined)).toBe('—')
    expect(formatPercent(null)).toBe('—')
  })

  it('middle-elides so a long path keeps its root and its leaf', () => {
    expect(elide('/run/credentials/systemd-resolved', 20)).toBe('/run/crede…-resolved')
    expect(elide('/boot', 20)).toBe('/boot')
  })
})

describe('thresholds', () => {
  it('bands a ratio the same way everywhere', () => {
    expect(toneForRatio(0.1)).toBe('good')
    expect(toneForRatio(0.6)).toBe('warn')
    expect(toneForRatio(0.85)).toBe('bad')
    expect(toneForRatio(0.99)).toBe('critical')
    expect(toneForRatio(Number.NaN)).toBe('muted')
  })

  it('maps core load onto a rising glyph', () => {
    expect(coreGlyph(0)).toBe('▁')
    expect(coreGlyph(100)).toBe('█')
    expect(coreGlyph(-5)).toBe('▁')
    expect(coreGlyph(500)).toBe('█')
  })
})

describe('rankDisks', () => {
  it('drops the 1 MiB credential tmpfs noise and sorts by used space', () => {
    const ranked = rankDisks([
      { mount_point: '/run/credentials/x', total_bytes: MIB, used_bytes: 0 },
      { mount_point: '/', total_bytes: 1800 * GIB, used_bytes: 1400 * GIB },
      { mount_point: '/boot', total_bytes: 1.4 * GIB, used_bytes: 130 * MIB }
    ])

    expect(ranked.map(d => d.mount_point)).toEqual(['/', '/boot'])
  })
})

describe('gpuDetail', () => {
  it('says why there are no numbers instead of showing an empty row', () => {
    expect(gpuDetail({ busy_percent: null, driver: 'mgag200', memory_total_bytes: null })).toBe(
      'driver mgag200 publishes no load or memory'
    )
  })

  it('renders what the driver does publish', () => {
    expect(gpuDetail({ busy_percent: 42, celsius: 61, memory_total_bytes: 8 * GIB, memory_used_bytes: 2 * GIB })).toBe(
      'busy 42% · memory 2.0 GiB of 8.0 GiB · 61°C'
    )
  })
})

describe('buildMachineView', () => {
  const ok: DigitdiskStatusResponse = {
    binary: '/usr/local/bin/digitdisk',
    required: '0.6.0',
    snapshot: {
      disks: [
        {
          available_bytes: 291 * GIB,
          mount_point: '/',
          total_bytes: 1800 * GIB,
          use_percent: 83.6,
          used_bytes: 1400 * GIB
        }
      ],
      gpus: [{ driver: 'mgag200', name: 'Matrox G200eW3' }],
      host: { cpu_model: 'AMD EPYC 7742', distro: 'Ubuntu 26.04 LTS', hostname: 'dev', uptime_human: '26д 05:22' },
      load: { '1min': 59.26, busy_percent: 10.29, cores: [{ busy_percent: 100 }, { busy_percent: 2 }], cpu_count: 2 },
      memory: { available_bytes: 410 * GIB, total_bytes: 499 * GIB, used_bytes: 125 * GIB },
      missing: { desktop: 'no desktop session' },
      network: [{ name: 'eno33', oper_state: 'up', rx_bytes: 488 * GIB, tx_bytes: 383 * GIB }],
      processes: {
        running: 18,
        threads: 7878,
        top_by_memory: [{ pid: 1, rss_bytes: 33 * GIB, user: 'a' }],
        total: 4509
      },
      taken_at: '2026-09-02T20:15:26Z'
    },
    state: 'ok',
    version: '0.6.0',
    version_known: true
  }

  it('names the binary and version it drew from', () => {
    expect(buildMachineView(ok).provenance).toContain('digitdisk 0.6.0')
    expect(buildMachineView(ok).provenance).toContain('/usr/local/bin/digitdisk')
  })

  it('builds every section the owner asked for', () => {
    expect(buildMachineView(ok).sections.map(s => s.title)).toEqual([
      'system',
      'cpu',
      'memory',
      'heaviest processes',
      'disks',
      'network',
      'graphics',
      'not measured'
    ])
  })

  it('emits one core cell per core', () => {
    const cpu = buildMachineView(ok).sections.find(s => s.title === 'cpu')!
    const cores = cpu.rows.find(r => r.kind === 'cores')

    expect(cores && cores.kind === 'cores' && cores.cells).toHaveLength(2)
  })

  it('flags a source build as unverified rather than claiming it passed the floor', () => {
    const view = buildMachineView({ ...ok, version: null, version_known: false })

    expect(view.warning).toContain('0.6.0')
    expect(view.provenance).toContain('dev build')
  })

  it('has no warning for a released build', () => {
    expect(buildMachineView(ok).warning).toBeUndefined()
  })

  it('survives a payload with nothing in it', () => {
    const view = buildMachineView({ required: '0.6.0', snapshot: {}, state: 'ok' })

    expect(view.sections.every(s => s.rows.length === 0 || s.empty)).toBe(true)
  })
})

describe('buildMachineNotice', () => {
  it('is null when there is a snapshot to draw', () => {
    expect(buildMachineNotice({ required: '0.6.0', snapshot: {}, state: 'ok' })).toBeNull()
  })

  it('names the version needed when digitdisk is absent', () => {
    const n = buildMachineNotice({ hint: 'brew install …', required: '0.6.0', state: 'missing' })!

    expect(n.title).toBe('digitdisk is not installed')
    expect(n.detail.join(' ')).toContain('0.6.0')
    expect(n.detail.join(' ')).toContain('brew install')
  })

  it('names both numbers when digitdisk is too old, and says the payload went unread', () => {
    const n = buildMachineNotice({
      binary: '/usr/local/bin/digitdisk',
      hint: 'upgrade',
      required: '0.6.0',
      state: 'outdated',
      version: '0.4.0'
    })!

    expect(n.title).toContain('0.4.0')
    expect(n.detail.join(' ')).toContain('0.6.0')
    expect(n.detail.join(' ')).toContain('was not read')
  })

  it('reports a broken run without pretending it has data', () => {
    const n = buildMachineNotice({ error: 'timed out after 30s', required: '0.6.0', state: 'failed' })!

    expect(n.detail.join(' ')).toContain('timed out')
  })
})
