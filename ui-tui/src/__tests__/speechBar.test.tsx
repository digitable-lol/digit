import { createElement } from 'react'
import { beforeEach, describe, expect, it } from 'vitest'

import { renderToScreen } from '../../packages/digit-ink/src/ink/render-to-screen.js'
import { charInCellAt, type Screen } from '../../packages/digit-ink/src/ink/screen.js'
import { $speech, clearSpeech, setSpeechCue, setSpeechLevels, SPEECH_IDLE } from '../app/speechStore.js'
import { fitCue, SpeechBar, speechBlocks } from '../components/speechBar.js'
import { DEFAULT_THEME } from '../theme.js'

const rowText = (screen: Screen, row: number): string => {
  let out = ''

  for (let col = 0; col < screen.width; col += 1) {
    out += charInCellAt(screen, col, row) ?? ' '
  }

  return out
}

const draw = (width: number): string => {
  const { screen } = renderToScreen(createElement(SpeechBar, { t: DEFAULT_THEME, width }), width)

  return rowText(screen, 0)
}

beforeEach(() => {
  $speech.set(SPEECH_IDLE)
})

describe('speechStore', () => {
  it('starts silent', () => {
    expect($speech.get()).toEqual(SPEECH_IDLE)
  })

  it('a cue starts speaking and carries the sentence', () => {
    setSpeechCue('The answer is forty two.')
    expect($speech.get().speaking).toBe(true)
    expect($speech.get().text).toBe('The answer is forty two.')
  })

  it('levels arriving before any cue are ignored', () => {
    setSpeechLevels([1, 2, 3])
    expect($speech.get()).toEqual(SPEECH_IDLE)
  })

  it('levels update without disturbing the sentence', () => {
    setSpeechCue('The answer is forty two.')
    setSpeechLevels([1, 2, 3])
    expect($speech.get().levels).toEqual([1, 2, 3])
    expect($speech.get().text).toBe('The answer is forty two.')
  })

  it('a new cue keeps the meter running rather than blanking it', () => {
    setSpeechCue('First sentence.')
    setSpeechLevels([4, 4, 4])
    setSpeechCue('Second sentence.')
    expect($speech.get().levels).toEqual([4, 4, 4])
  })

  it('clearing returns to silence', () => {
    setSpeechCue('Something spoken.')
    setSpeechLevels([5])
    clearSpeech()
    expect($speech.get()).toEqual(SPEECH_IDLE)
  })

  it('a level frame arriving after silence cannot resurrect the meter', () => {
    setSpeechCue('Something spoken.')
    clearSpeech()
    setSpeechLevels([8, 8, 8])
    expect($speech.get()).toEqual(SPEECH_IDLE)
  })

  it('clearing twice is harmless', () => {
    clearSpeech()
    clearSpeech()
    expect($speech.get()).toEqual(SPEECH_IDLE)
  })
})

describe('speechBlocks', () => {
  it('renders one block per band', () => {
    expect(speechBlocks([0, 1, 2, 3])).toHaveLength(4)
  })

  it('maps the floor and the ceiling to the end glyphs', () => {
    expect(speechBlocks([0, 8])).toBe('▁█')
  })

  it('clamps out-of-range steps instead of dropping cells', () => {
    expect(speechBlocks([-4, 99])).toBe('▁█')
  })

  it('treats a broken value as the floor', () => {
    expect(speechBlocks([Number.NaN, Number.POSITIVE_INFINITY])).toBe('▁▁')
  })

  it('renders nothing for no bands', () => {
    expect(speechBlocks([])).toBe('')
  })
})

describe('fitCue', () => {
  it('leaves a short sentence alone', () => {
    expect(fitCue('Short one.', 40)).toBe('Short one.')
  })

  it('flattens whitespace', () => {
    expect(fitCue('two   words\nhere', 40)).toBe('two words here')
  })

  it('marks a trimmed sentence with an ellipsis', () => {
    expect(fitCue('a'.repeat(50), 10)).toBe(`${'a'.repeat(9)}…`)
  })

  it('gives up rather than printing one character', () => {
    expect(fitCue('anything', 1)).toBe('')
  })
})

describe('SpeechBar', () => {
  it('draws nothing while the speaker is quiet', () => {
    expect(draw(40).trim()).toBe('')
  })

  it('draws the meter and the sentence once speech starts', () => {
    setSpeechCue('The answer is forty two.')
    setSpeechLevels([0, 2, 4, 6])

    const row = draw(40)

    expect(row).toContain('▁▃▅▇')
    expect(row).toContain('The answer is forty two.')
  })

  it('drops the words before the meter on a narrow terminal', () => {
    setSpeechCue('A sentence that will never fit here.')
    setSpeechLevels([1, 2, 3, 4, 5, 6])

    const row = draw(8)

    expect(row).toContain('▁▂▃▄▅▆')
    expect(row).not.toContain('sentence')
  })

  it('disappears again when the speaker goes quiet', () => {
    setSpeechCue('Something spoken.')
    clearSpeech()

    expect(draw(40).trim()).toBe('')
  })
})
