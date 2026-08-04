import { atom } from 'nanostores'

/**
 * What Digit is saying out loud, as reported by the gateway's `voice.speech`
 * event.
 *
 * The TUI redraws on every frame anyway, so speech state does not need to
 * travel through the app's prop chain — the one component that draws it
 * subscribes here directly, the way the pet and the FPS counter do.
 *
 * `text` is the sentence currently leaving the speaker, announced by the
 * playback worker rather than by the synthesis queue: synthesis runs a
 * sentence or two ahead, and a marker that follows the queue would point at
 * words nobody has heard yet.
 *
 * `levels` are band energies already quantised to whole block steps (0…8) by
 * the backend. Integers, because the terminal has exactly nine glyphs to draw
 * them with and anything finer is thrown away on arrival.
 */
export interface SpeechState {
  levels: number[]
  speaking: boolean
  text: string
}

export const SPEECH_IDLE: SpeechState = { levels: [], speaking: false, text: '' }

export const $speech = atom<SpeechState>(SPEECH_IDLE)

/** The sentence now audible. Starts a speaking state if one wasn't running. */
export const setSpeechCue = (text: string) => {
  const current = $speech.get()

  $speech.set({ levels: current.levels, speaking: true, text })
}

/** Fresh band energies. Ignored when nothing is speaking, so a late frame
 *  arriving after a barge-in cannot resurrect the meter. */
export const setSpeechLevels = (levels: number[]) => {
  const current = $speech.get()

  if (!current.speaking) {
    return
  }

  $speech.set({ levels, speaking: true, text: current.text })
}

/** Silence — the reply finished, or the user talked over it. */
export const clearSpeech = () => {
  if ($speech.get().speaking) {
    $speech.set(SPEECH_IDLE)
  }
}
