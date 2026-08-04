import { beforeEach, describe, expect, it } from 'vitest'

import { $speechCue, clearSpeechCue, setSpeechCue } from './speech-cue'

beforeEach(() => {
  $speechCue.set(null)
})

describe('the spoken-sentence slot', () => {
  it('starts empty — nothing is being said', () => {
    expect($speechCue.get()).toBeNull()
  })

  it('carries the sentence and where it sits in the reply', () => {
    setSpeechCue({ end: 27, index: 0, messageId: 'm1', start: 0, text: 'The first sentence is here.' })

    expect($speechCue.get()?.text).toBe('The first sentence is here.')
    expect($speechCue.get()?.start).toBe(0)
  })

  it('accepts a sentence that could not be traced back', () => {
    setSpeechCue({ end: null, index: 3, messageId: 'm1', start: null, text: 'Untraceable.' })

    expect($speechCue.get()?.start).toBeNull()
    expect($speechCue.get()?.text).toBe('Untraceable.')
  })

  it('clearing returns to silence', () => {
    setSpeechCue({ end: 5, index: 0, messageId: 'm1', start: 0, text: 'Hello' })
    clearSpeechCue()

    expect($speechCue.get()).toBeNull()
  })

  it('clearing when already silent does not churn subscribers', () => {
    const seen: unknown[] = []

    $speechCue.subscribe(value => seen.push(value))
    clearSpeechCue()

    expect(seen).toHaveLength(1)
  })
})
