import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $speechAnalyser, foldBands, openSpeechAnalyser, setSpeechAnalyser, SPEECH_BAND_COUNT } from './speech-analyser'

const fakeContext = (withAnalyser = true) => {
  const analyser = { connect: vi.fn(), fftSize: 0, smoothingTimeConstant: 0 }

  return {
    analyser,
    context: {
      createAnalyser: withAnalyser ? () => analyser : undefined,
      destination: {}
    } as unknown as AudioContext
  }
}

beforeEach(() => {
  setSpeechAnalyser(null)
})

describe('the analyser slot', () => {
  it('starts empty — nothing is speaking', () => {
    expect($speechAnalyser.get()).toBeNull()
  })

  it('publishes the node it attaches', () => {
    const { analyser, context } = fakeContext()

    expect(openSpeechAnalyser(context)).toBe(analyser)
    expect($speechAnalyser.get()).toBe(analyser)
  })

  it('routes the analyser to the destination, not around it', () => {
    const { analyser, context } = fakeContext()

    openSpeechAnalyser(context)
    expect(analyser.connect).toHaveBeenCalledWith(context.destination)
  })

  it('hands back nothing when the browser cannot analyse, and publishes nothing', () => {
    const { context } = fakeContext(false)

    expect(openSpeechAnalyser(context)).toBeNull()
    expect($speechAnalyser.get()).toBeNull()
  })

  it('setting the same node twice does not churn subscribers', () => {
    const { analyser, context } = fakeContext()
    const seen: unknown[] = []

    openSpeechAnalyser(context)
    $speechAnalyser.subscribe(value => seen.push(value))
    setSpeechAnalyser(analyser)

    expect(seen).toHaveLength(1)
  })
})

describe('foldBands', () => {
  const bins = (length: number, fill: (index: number) => number) =>
    Uint8Array.from({ length }, (_, index) => fill(index))

  it('returns the requested number of bands', () => {
    expect(
      foldBands(
        bins(256, () => 0),
        12
      )
    ).toHaveLength(12)
  })

  it('normalises to 0..1', () => {
    const bands = foldBands(
      bins(256, () => 255),
      8
    )

    expect(bands.every(level => level >= 0 && level <= 1)).toBe(true)
    expect(Math.max(...bands)).toBe(1)
  })

  it('silence folds to silence', () => {
    expect(
      foldBands(
        bins(256, () => 0),
        8
      )
    ).toEqual(Array.from({ length: 8 }, () => 0))
  })

  it('energy at the top of the spectrum lands in a high band', () => {
    const bands = foldBands(
      bins(256, index => (index > 200 ? 255 : 0)),
      8
    )

    expect(bands.indexOf(Math.max(...bands))).toBeGreaterThan(3)
  })

  it('energy at the bottom lands in a low band', () => {
    const bands = foldBands(
      bins(256, index => (index < 4 ? 255 : 0)),
      8
    )

    expect(bands.indexOf(Math.max(...bands))).toBeLessThan(4)
  })

  it('no bins fold to no bands', () => {
    expect(foldBands(new Uint8Array(), 8)).toEqual([])
  })

  it('a nonsense band count folds to no bands', () => {
    expect(
      foldBands(
        bins(64, () => 10),
        0
      )
    ).toEqual([])
  })

  it('defaults to the shared band count', () => {
    expect(foldBands(bins(256, () => 5))).toHaveLength(SPEECH_BAND_COUNT)
  })
})
