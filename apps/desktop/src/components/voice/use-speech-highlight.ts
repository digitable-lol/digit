import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { clearSpeechHighlight, paintSpeechHighlight } from '@/lib/speech-highlight'
import { $speechCue } from '@/store/speech-cue'

/**
 * Whether a cue belongs to this message.
 *
 * Read-aloud and auto-speak name the message they are reading, so the answer
 * is an id comparison. Voice conversation does not — it speaks the reply as
 * it arrives, which is always the last message — so an unnamed cue falls to
 * whoever is last. Without that fallback an unnamed cue would match *every*
 * message and the whole transcript would light up at once.
 */
export function cueBelongsHere(
  cue: null | { messageId: null | string },
  messageId: string,
  isLastMessage: boolean
): boolean {
  if (!cue) {
    return false
  }

  return cue.messageId ? cue.messageId === messageId : isLastMessage
}

/**
 * Paint the sentence Digit is currently saying inside this message.
 *
 * Returns a ref to put on the element holding the rendered reply. Only one
 * message paints, so several on screen cannot argue over the single registered
 * highlight, and the paint is cleared whenever speech stops or this message
 * unmounts.
 */
export function useSpeechHighlight(messageId: string, isLastMessage: boolean) {
  const cue = useStore($speechCue)
  const ref = useRef<HTMLDivElement | null>(null)

  const speaking = cue?.text ?? ''
  const owns = cueBelongsHere(cue, messageId, isLastMessage)

  useEffect(() => {
    if (!speaking || !owns) {
      return
    }

    paintSpeechHighlight(ref.current, speaking)

    return () => clearSpeechHighlight()
  }, [owns, speaking])

  return ref
}
