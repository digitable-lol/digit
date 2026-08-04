import { atom } from 'nanostores'

/**
 * The sentence Digit is saying out loud, and where it sits in the reply.
 *
 * The backend sends a `mark` frame down the speak-stream socket immediately
 * before the audio of that sentence. Position in the stream *is* the timing:
 * the client already schedules every PCM buffer, so it knows exactly when the
 * next one becomes audible and hangs the cue off the same schedule. Nothing
 * is aligned after the fact and no second clock is involved.
 *
 * `start`/`end` index the reply text as the client sent it, and are null when
 * the backend could not trace the sentence back — an untraced sentence is
 * announced but not highlighted, which is a normal state rather than an error.
 */
export interface SpeechCue {
  end: null | number
  index: number
  messageId: null | string
  start: null | number
  text: string
}

export const $speechCue = atom<SpeechCue | null>(null)

export const setSpeechCue = (cue: SpeechCue | null) => $speechCue.set(cue)

export const clearSpeechCue = () => {
  if ($speechCue.get()) {
    $speechCue.set(null)
  }
}
