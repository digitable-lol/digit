import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearSpeechHighlight,
  locateInProjection,
  longestMatch,
  MIN_ANCHOR,
  paintSpeechHighlight,
  projectElement,
  projectText,
  rangeForSpan,
  SPEECH_HIGHLIGHT_NAME
} from './speech-highlight'

const mount = (html: string): HTMLElement => {
  const host = document.createElement('div')

  host.innerHTML = html
  document.body.append(host)

  return host
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('projectText', () => {
  it('keeps only letters and digits, lowercased', () => {
    expect(projectText('**Hello**, world 42!')).toBe('helloworld42')
  })

  it('survives emoji and punctuation the cleaner would have dropped', () => {
    expect(projectText('Done ✅ — really.')).toBe('donereally')
  })

  it('keeps non-latin letters', () => {
    expect(projectText('Привет, мир')).toBe('приветмир')
  })

  it('projects nothing from nothing', () => {
    expect(projectText('  —  ')).toBe('')
  })
})

describe('projectElement', () => {
  it('walks every text node in document order', () => {
    const host = mount('<p>The <strong>first</strong> sentence.</p>')

    expect(projectElement(host).text).toBe('thefirstsentence')
  })

  it('remembers where each character came from', () => {
    const host = mount('<p>ab<em>cd</em></p>')
    const projection = projectElement(host)

    expect(projection.nodes).toHaveLength(4)
    expect(projection.offsets).toEqual([0, 1, 0, 1])
    expect(projection.nodes[0]).not.toBe(projection.nodes[2])
  })

  it('projects an empty element to nothing', () => {
    expect(projectElement(mount('<p></p>')).text).toBe('')
  })
})

describe('longestMatch', () => {
  it('finds the whole needle when it is present', () => {
    expect(longestMatch('abcdefghijklmno', 'zzabcdefghijklmno', 0, 4)).toEqual([2, 15])
  })

  it('falls back to the longest prefix that survives', () => {
    const [at, length] = longestMatch('abcdefghijklmnoXYZ', 'abcdefghijklmno', 0, 4)

    expect(at).toBe(0)
    expect(length).toBe(15)
  })

  it('reports nothing below the minimum anchor', () => {
    expect(longestMatch('abcdefgh', 'zzzz', 0, 4)).toEqual([-1, 0])
  })

  it('can anchor from the end', () => {
    const [at, length] = longestMatch('XYZabcdefghij', 'abcdefghij', 0, 4, true)

    expect(at).toBe(0)
    expect(length).toBe(10)
  })
})

describe('locateInProjection', () => {
  const rendered = projectText('The first sentence is here. The second sentence follows it.')

  it('finds a sentence that survived the cleaner untouched', () => {
    const span = locateInProjection('The second sentence follows it.', rendered)

    expect(span).not.toBeNull()
    expect(rendered.slice(span![0], span![1])).toBe(projectText('The second sentence follows it.'))
  })

  it('finds a sentence the cleaner rewrote in the middle', () => {
    const source = projectText('Tomorrow it reaches 18 °C in the shade, bring a jacket.')
    const span = locateInProjection('Tomorrow it reaches 18 degrees Celsius in the shade, bring a jacket.', source)

    expect(span).not.toBeNull()
    expect(span![0]).toBe(0)
    expect(span![1]).toBe(source.length)
  })

  it('searches forward from the cursor so a repeat does not rewind', () => {
    const repeated = projectText('Repeat this line exactly. Filler. Repeat this line exactly.')
    const first = locateInProjection('Repeat this line exactly.', repeated)
    const second = locateInProjection('Repeat this line exactly.', repeated, first![1])

    expect(second![0]).toBeGreaterThan(first![0])
  })

  it('refuses to guess from too short an anchor', () => {
    expect(locateInProjection('ok', 'somethingelseentirely')).toBeNull()
  })

  it('reports nothing when the sentence is absent', () => {
    expect(locateInProjection('nothing like this was ever rendered', rendered)).toBeNull()
  })

  it('reports nothing for an empty sentence', () => {
    expect(locateInProjection('', rendered)).toBeNull()
  })

  it('needs at least the minimum anchor to fall back', () => {
    expect(MIN_ANCHOR).toBeGreaterThan(4)
  })
})

describe('rangeForSpan', () => {
  it('spans the characters it was given', () => {
    const host = mount('<p>The first sentence is here.</p>')
    const projection = projectElement(host)
    const range = rangeForSpan(projection, [0, 3])

    expect(range).not.toBeNull()
    expect(range!.toString()).toBe('The')
  })

  it('crosses element boundaries', () => {
    const host = mount('<p>The <strong>first</strong> sentence.</p>')
    const projection = projectElement(host)
    const range = rangeForSpan(projection, [0, 8])

    expect(range!.toString()).toBe('The first')
  })

  it('refuses an out-of-range span rather than throwing', () => {
    const projection = projectElement(mount('<p>ab</p>'))

    expect(rangeForSpan(projection, [0, 99])).toBeNull()
    expect(rangeForSpan(projection, [2, 1])).toBeNull()
  })
})

describe('paintSpeechHighlight', () => {
  it('does nothing, and says so, without the highlight API', () => {
    // jsdom has no CSS.highlights — this is the "old browser" path, and the
    // point is that it degrades to no highlight rather than to an exception.
    const host = mount('<p>The first sentence is here.</p>')

    expect(paintSpeechHighlight(host, 'The first sentence is here.')).toBe(false)
  })

  it('is a no-op for a missing element', () => {
    expect(paintSpeechHighlight(null, 'anything at all')).toBe(false)
  })

  it('is a no-op for an empty sentence', () => {
    expect(paintSpeechHighlight(mount('<p>text</p>'), '')).toBe(false)
  })

  it('clearing is safe when nothing was ever painted', () => {
    expect(() => clearSpeechHighlight()).not.toThrow()
  })

  it('names one highlight, so surfaces cannot collide', () => {
    expect(SPEECH_HIGHLIGHT_NAME).toBe('digit-speech')
  })
})
