import { Box, NoSelect, ScrollBox, type ScrollBoxHandle, Text, useInput, useStdout } from '@digit/ink'
import { useCallback, useEffect, useRef, useState } from 'react'

import type { GatewayClient } from '../gatewayClient.js'
import type { DigitdiskStatusResponse } from '../gatewayTypes.js'
import {
  buildMachineNotice,
  buildMachineView,
  type MachineRow,
  type MachineSection,
  type Tone
} from '../lib/digitdiskView.js'
import { rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { barCells } from './overlayPrimitives.js'
import { OverlayScrollbar } from './overlayScrollbar.js'

/**
 * `/machine` — the host snapshot digitdisk measures, drawn with digit's own
 * primitives.
 *
 * The numbers are not ours: the gateway runs the installed `digitdisk` CLI
 * and hands back its `status --json` payload, so digit and digitdisk can never
 * disagree about the same machine.  Everything here is presentation.
 *
 * The fetch is a plain `gw.request` — the handler is in the gateway's
 * `_LONG_HANDLERS` pool, so the ~1.6 s digitdisk takes cannot stall the
 * reader thread, and Ink keeps drawing the spinner throughout.
 */

const LABEL_WIDTH = 15

const toneColor = (tone: Tone | undefined, t: Theme): string => {
  switch (tone) {
    case 'critical':
      return t.color.statusCritical

    case 'bad':
      return t.color.statusBad

    case 'warn':
      return t.color.statusWarn

    case 'good':
      return t.color.statusGood

    case 'muted':
      return t.color.muted

    default:
      return t.color.text
  }
}

function Row({ row, t }: { row: MachineRow; t: Theme }) {
  if (row.kind === 'cores') {
    // One glyph per core. Colour and height both carry the load so the strip
    // still reads on a monochrome terminal.
    return (
      <Text wrap="wrap">
        {row.cells.map((c, i) => (
          <Text color={toneColor(c.tone, t)} key={i}>
            {c.glyph}
          </Text>
        ))}
      </Text>
    )
  }

  if (row.kind === 'columns') {
    return (
      <Text color={row.header ? t.color.muted : toneColor(row.tone, t)} wrap="truncate-end">
        {row.text}
      </Text>
    )
  }

  if (row.kind === 'meter') {
    const { bar } = barCells(row.ratio)

    return (
      <Text wrap="truncate-end">
        <Text color={t.color.label}>{row.label.padEnd(LABEL_WIDTH)}</Text>
        <Text color={t.color.muted}>[</Text>
        <Text color={toneColor(row.tone, t)}>{bar}</Text>
        <Text color={t.color.muted}>] </Text>
        <Text color={t.color.text}>{row.value}</Text>
      </Text>
    )
  }

  if (row.block) {
    // Prose, not a field: the label gets its own line and the value wraps
    // under it, so a long GPU model or a "why not measured" sentence stays
    // readable instead of being cut off at the label column.
    return (
      <Box flexDirection="column">
        <Text color={t.color.label} wrap="truncate-end">
          {row.label}
        </Text>
        <Text color={toneColor(row.tone, t)} wrap="wrap">
          {'  '}
          {row.value}
        </Text>
      </Box>
    )
  }

  return (
    <Text wrap="truncate-end">
      {/* The trailing space keeps a label wider than the column from running
          straight into its value. */}
      <Text color={t.color.label}>{`${row.label} `.padEnd(LABEL_WIDTH)}</Text>
      <Text color={toneColor(row.tone, t)}>{row.value}</Text>
    </Text>
  )
}

function Section({ section, t }: { section: MachineSection; t: Theme }) {
  return (
    <Box flexDirection="column" marginBottom={1}>
      <Text bold color={t.color.primary}>
        {section.title}
      </Text>
      {section.rows.length ? (
        section.rows.map((row, i) => <Row key={i} row={row} t={t} />)
      ) : (
        <Text color={t.color.muted}>{section.empty ?? 'nothing to show'}</Text>
      )}
    </Box>
  )
}

/**
 * The not-ok states: tool absent, tool too old, tool broke.  Each one names
 * the required version as a number and says what to do, because "nothing
 * rendered" is the one outcome that teaches the user nothing.
 */
function Notice({ res, t }: { res: DigitdiskStatusResponse; t: Theme }) {
  const notice = buildMachineNotice(res)

  if (!notice) {
    return null
  }

  return (
    <Box flexDirection="column" marginTop={1}>
      <Text bold color={toneColor(notice.tone, t)}>
        {notice.title}
      </Text>
      {notice.detail.map((line, i) => (
        <Text color={t.color.muted} key={i} wrap="wrap">
          {line}
        </Text>
      ))}
    </Box>
  )
}

export function MachinePanel({ gw, onClose, t }: { gw: GatewayClient; onClose: () => void; t: Theme }) {
  const { stdout } = useStdout()
  const cols = stdout?.columns ?? 80
  const [res, setRes] = useState<DigitdiskStatusResponse | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)
  const [tick, setTick] = useState(0)
  const scrollRef = useRef<null | ScrollBoxHandle>(null)

  useEffect(() => {
    // `alive` guards the state writes: a snapshot in flight when the user
    // closes the panel must not resurrect it or overwrite a newer refresh.
    let alive = true

    setLoading(true)
    gw.request<DigitdiskStatusResponse>('digitdisk.status', {})
      .then(r => {
        if (!alive) {
          return
        }

        setRes(r)
        setErr('')
        setLoading(false)
        setTick(n => n + 1)
      })
      .catch((e: unknown) => {
        if (!alive) {
          return
        }

        setErr(rpcErrorMessage(e))
        setLoading(false)
      })

    return () => {
      alive = false
    }
  }, [gw, reloadKey])

  const refresh = useCallback(() => setReloadKey(n => n + 1), [])

  useInput((ch, key) => {
    if (key.escape || ch === 'q') {
      return onClose()
    }

    if (ch === 'r') {
      return refresh()
    }

    const box = scrollRef.current

    if (!box) {
      return
    }

    if (key.downArrow || ch === 'j') {
      box.scrollBy(1)
    } else if (key.upArrow || ch === 'k') {
      box.scrollBy(-1)
    } else if (key.pageDown || ch === ' ') {
      box.scrollBy(box.getViewportHeight())
    } else if (key.pageUp) {
      box.scrollBy(-box.getViewportHeight())
    } else if (ch === 'g') {
      box.scrollTo(0)
    }

    setTick(n => n + 1)
  })

  const view = res?.state === 'ok' ? buildMachineView(res, cols) : null

  return (
    <Box alignItems="stretch" flexDirection="column" flexGrow={1} paddingX={1} paddingY={1}>
      <Box flexDirection="column" marginBottom={1}>
        <Text wrap="truncate-end">
          <Text bold color={t.color.primary}>
            ▚ Machine
          </Text>
          <Text color={t.color.muted}> host snapshot via digitdisk</Text>
        </Text>
        {view ? (
          <Text color={t.color.muted} wrap="truncate-end">
            {view.provenance}
          </Text>
        ) : null}
        {view?.warning ? (
          <Text color={t.color.statusWarn} wrap="truncate-end">
            {view.warning}
          </Text>
        ) : null}
      </Box>

      <Box flexDirection="row" flexGrow={1} flexShrink={1} minHeight={0}>
        <ScrollBox flexDirection="column" flexGrow={1} flexShrink={1} ref={scrollRef}>
          {loading ? <Text color={t.color.muted}>running digitdisk status…</Text> : null}
          {err ? <Text color={t.color.error}>{err}</Text> : null}
          {res && res.state !== 'ok' ? <Notice res={res} t={t} /> : null}
          {view ? view.sections.map(section => <Section key={section.title} section={section} t={t} />) : null}
        </ScrollBox>
        <NoSelect flexShrink={0} marginLeft={1}>
          <OverlayScrollbar scrollRef={scrollRef} t={t} tick={tick} />
        </NoSelect>
      </Box>

      <Box flexDirection="column" marginTop={1}>
        <Text color={t.color.muted} wrap="truncate-end">
          ↑↓/jk scroll · space page · g top · r refresh · q close
        </Text>
      </Box>
    </Box>
  )
}
