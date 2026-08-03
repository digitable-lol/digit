import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { $desktopOnboarding, type DesktopOnboardingState, type OnboardingContext } from '@/store/onboarding'
import type { OAuthProvider } from '@/types/digit'

import { Picker } from '.'

function provider(id: string, name = id): OAuthProvider {
  return {
    cli_command: `digit login ${id}`,
    docs_url: `https://example.com/${id}`,
    flow: 'pkce',
    id,
    name,
    status: { logged_in: false }
  }
}

function setProviders(providers: OAuthProvider[]) {
  $desktopOnboarding.set({
    configured: false,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  } satisfies DesktopOnboardingState)
}

const ctx: OnboardingContext = { requestGateway: async () => undefined as never }

afterEach(() => {
  cleanup()

  try {
    window.localStorage.clear()
  } catch {
    // jsdom localStorage should always be present; ignore if not.
  }

  $desktopOnboarding.set({
    configured: null,
    flow: { status: 'idle' },
    mode: 'oauth',
    providers: null,
    reason: null,
    requested: false,
    firstRunSkipped: false,
    manual: false,
    localEndpoint: false
  })
})

describe('onboarding Picker', () => {
  it('hides the upstream vendor cloud and shows visible providers directly', () => {
    setProviders([provider('anthropic', 'Anthropic Claude'), provider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(screen.queryByText('Recommended')).toBeNull()
    expect(screen.getByText('Fireworks AI')).toBeTruthy()
    expect(screen.getByText('Anthropic API Key')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Other providers' })).toBeNull()
  })

  it('shows Fireworks ahead of other visible OAuth providers', () => {
    setProviders([
      provider('openai-codex', 'OpenAI Codex / ChatGPT'),
      provider('minimax-oauth', 'MiniMax'),
      provider('nous', 'Nous Portal')
    ])
    render(<Picker ctx={ctx} />)

    const labels = screen
      .getAllByRole('button')
      .map(el => el.textContent ?? '')
      .filter(text => /Fireworks AI|OpenAI OAuth|MiniMax|OpenRouter/.test(text))

    const indexOf = (needle: string) => labels.findIndex(text => text.includes(needle))
    expect(screen.queryByText('Nous Portal')).toBeNull()
    expect(indexOf('Fireworks AI')).toBeGreaterThanOrEqual(0)
    expect(indexOf('OpenAI OAuth')).toBeGreaterThan(indexOf('Fireworks AI'))
    expect(indexOf('MiniMax')).toBeGreaterThan(indexOf('OpenAI OAuth'))
  })

  it('shows every provider directly when Nous Portal is absent', () => {
    setProviders([provider('anthropic', 'Anthropic Claude'), provider('openai-codex', 'OpenAI Codex / ChatGPT')])
    render(<Picker ctx={ctx} />)

    expect(screen.getByText('Fireworks AI')).toBeTruthy()
    expect(screen.getByText('Anthropic API Key')).toBeTruthy()
    expect(screen.getByText('OpenAI OAuth (ChatGPT)')).toBeTruthy()
    expect(screen.queryByText('Other sign-in options')).toBeNull()
    expect(screen.queryByText('Recommended')).toBeNull()
  })

  it('offers "choose later" on first run and persists the skip', () => {
    setProviders([provider('nous', 'Nous Portal')])
    render(<Picker ctx={ctx} />)

    const skip = screen.getByRole('button', { name: "I'll choose a provider later" })

    fireEvent.click(skip)

    expect($desktopOnboarding.get().firstRunSkipped).toBe(true)
    expect(window.localStorage.getItem('digit-onboarding-skipped-v1')).toBe('1')
  })

  it('hides "choose later" in manual (add-provider) mode', () => {
    setProviders([provider('nous', 'Nous Portal')])
    $desktopOnboarding.set({ ...$desktopOnboarding.get(), manual: true })
    render(<Picker ctx={ctx} />)

    expect(screen.queryByRole('button', { name: "I'll choose a provider later" })).toBeNull()
  })
})
