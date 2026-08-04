import { atom } from 'nanostores'

/**
 * The analyser node of whatever is speaking right now, or null when nothing
 * is.
 *
 * There is exactly one speaker, so there is exactly one slot. Playback paths
 * publish here on their way to the destination; the visualiser subscribes and
 * reads frequency data straight off the node. Keeping the node in a store
 * rather than passing it through props means a visualiser can mount and
 * unmount freely — including not existing at all, which is what happens on a
 * machine with no audio.
 */
export const $speechAnalyser = atom<AnalyserNode | null>(null)

/** Bands the visualiser draws. Enough to read as a voice, few enough that
 *  each bar is wide enough to see. */
export const SPEECH_BAND_COUNT = 24

export const setSpeechAnalyser = (node: AnalyserNode | null) => {
  if ($speechAnalyser.get() !== node) {
    $speechAnalyser.set(node)
  }
}

/**
 * Attach an analyser to *context* and publish it.
 *
 * Returns the node to route audio through, or null when the browser has no
 * analyser to give — in which case the caller connects straight to the
 * destination and simply gets no bars. Sound first, decoration second.
 */
export function openSpeechAnalyser(context: AudioContext): AnalyserNode | null {
  if (typeof context.createAnalyser !== 'function') {
    return null
  }

  const analyser = context.createAnalyser()

  // 512 bins over 24 kHz is ~47 Hz per bin: fine enough to separate a voice's
  // fundamental from its formants, coarse enough to stay cheap every frame.
  analyser.fftSize = 512
  // Speech is bursty; without smoothing the bars flicker rather than move.
  analyser.smoothingTimeConstant = 0.7
  analyser.connect(context.destination)
  setSpeechAnalyser(analyser)

  return analyser
}

/**
 * Fold raw FFT bins into *count* logarithmic bands, each 0…1.
 *
 * Logarithmic because hearing is: a linear split would spend most of the row
 * on frequencies a voice never reaches. Exported separately from any canvas
 * so the mapping can be tested without a rendering context.
 */
export function foldBands(bins: Uint8Array, count = SPEECH_BAND_COUNT): number[] {
  if (!bins.length || count < 1) {
    return []
  }

  const bands: number[] = []
  const top = bins.length

  for (let index = 0; index < count; index += 1) {
    // Geometric edges over the usable bin range, clamped so no band is empty.
    const from = Math.floor(top ** (index / count))
    const to = Math.max(from + 1, Math.floor(top ** ((index + 1) / count)))
    let peak = 0

    for (let bin = from; bin < Math.min(to, top); bin += 1) {
      peak = Math.max(peak, bins[bin] ?? 0)
    }

    bands.push(peak / 255)
  }

  return bands
}
