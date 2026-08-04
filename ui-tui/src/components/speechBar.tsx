import { Box, Text } from '@digit/ink'
import { useStore } from '@nanostores/react'

import { $speech } from '../app/speechStore.js'

import type { Theme } from '../theme.js'

/**
 * The TUI's share of "Digit talks": a meter and the sentence behind it.
 *
 * The classic CLI can only rewrite one line under a finished answer; the TUI
 * repaints every frame, so it can afford a row that lives in the chrome — the
 * band meter on the left, the sentence currently leaving the speaker on the
 * right, both disappearing the moment the speaker goes quiet.
 *
 * The sentence is the function: it says where the agent is in its own reply,
 * which is what makes interrupting it a decision rather than a guess. The
 * meter is decoration, and an honest one — the levels are measured from the
 * audio on its way to the device, so a provider with no chunked audio shows a
 * flat baseline instead of a pretend animation.
 */

const BLOCKS = '▁▂▃▄▅▆▇█'

/** Render quantised band steps as one row of blocks.
 *
 *  Steps arrive as whole numbers because the terminal has nine glyphs and
 *  nothing finer survives being drawn. Out-of-range values are clamped rather
 *  than dropped: a meter that loses cells changes width, and a status row
 *  that changes width makes everything beside it jump.
 */
export function speechBlocks(levels: number[], steps = BLOCKS.length): string {
  return levels
    .map(level => {
      const value = Number.isFinite(level) ? Math.round(level) : 0

      return BLOCKS[Math.min(steps - 1, Math.max(0, value))]
    })
    .join('')
}

/** Trim a sentence to the room available, ending in an ellipsis when cut. */
export function fitCue(text: string, width: number): string {
  const flat = text.replace(/\s+/gu, ' ').trim()

  if (width <= 1) {
    return ''
  }

  return flat.length <= width ? flat : `${flat.slice(0, width - 1)}…`
}

export function SpeechBar({ t, width }: { t: Theme; width: number }) {
  const speech = useStore($speech)

  if (!speech.speaking) {
    return null
  }

  const bars = speechBlocks(speech.levels)
  const room = width - bars.length - 2
  const cue = room > 1 ? fitCue(speech.text, room) : ''

  return (
    <Box>
      <Text color={t.color.accent}>{bars}</Text>
      {cue ? <Text color={t.color.muted}> {cue}</Text> : null}
    </Box>
  )
}
