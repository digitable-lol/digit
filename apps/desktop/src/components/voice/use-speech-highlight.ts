import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { clearSpeechHighlight, paintSpeechHighlight } from '@/lib/speech-highlight'
import { $speechCue } from '@/store/speech-cue'

/**
 * Paint the sentence Digit is currently saying inside this message.
 *
 * Returns a ref to put on the element that holds the rendered reply. Only the
 * message the cue names is painted, so several transcripts on screen cannot
 * argue over one highlight, and the paint is cleared whenever speech stops or
 * this message unmounts.
 */
export function useSpeechHighlight(messageId: string) {
  const cue = useStore($speechCue)
  const ref = useRef<HTMLDivElement | null>(null)

  const speaking = cue?.text ?? ''
  const owns = !cue?.messageId || cue.messageId === messageId

  useEffect(() => {
    if (!speaking || !owns) {
      return
    }

    paintSpeechHighlight(ref.current, speaking)

    return () => clearSpeechHighlight()
  }, [owns, speaking])

  return ref
}
