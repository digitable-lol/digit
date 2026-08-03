// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { $terminalFontFamily, setTerminalFontFamilyFromConfig } from '@/app/right-sidebar/terminal/terminal-font'
import { getDigitConfig } from '@/digit'
import { persistString } from '@/lib/storage'
import {
  $currentCwd,
  $currentFastMode,
  $currentReasoningEffort,
  $defaultReasoningEffort,
  markComposerSelectionManual,
  setCurrentCwd,
  setCurrentFastMode,
  setCurrentModelSource,
  setCurrentReasoningEffort,
  setDefaultReasoningEffort
} from '@/store/session'

import { useDigitConfig } from './use-digit-config'

vi.mock('@/digit', () => ({
  getDigitConfig: vi.fn(),
  getDigitConfigDefaults: vi.fn().mockResolvedValue({})
}))

const WORKSPACE_CWD_KEY = 'digit.desktop.workspace-cwd'

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

const mockConfig = (config: Record<string, unknown>) =>
  vi.mocked(getDigitConfig).mockResolvedValue(config as Awaited<ReturnType<typeof getDigitConfig>>)

describe('useDigitConfig refreshDigitConfig', () => {
  beforeEach(() => {
    // Reset atoms and localStorage between tests
    setCurrentCwd('')
    setCurrentFastMode(false)
    setCurrentModelSource('')
    setCurrentReasoningEffort('')
    setDefaultReasoningEffort('')
    setTerminalFontFamilyFromConfig('')
    persistString(WORKSPACE_CWD_KEY, null)
  })

  // Regression: the composer keeps a manual model pick sticky, which skips the
  // composer reseed. The profile default must still be published, because the
  // model picker resolves "the default effort" from it when applying a model's
  // preset — otherwise selecting a model silently downgrades a configured
  // `agent.reasoning_effort: high` to Digit' built-in medium.
  it('publishes the profile default effort even when a manual pick blocks the composer reseed', async () => {
    setCurrentModelSource('manual')
    setCurrentReasoningEffort('low')

    mockConfig({ agent: { reasoning_effort: 'high' } })
    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: null } }))

    await act(async () => {
      await result.current.refreshDigitConfig()
    })

    expect($defaultReasoningEffort.get()).toBe('high')
    // The manual pick itself is still respected.
    expect($currentReasoningEffort.get()).toBe('low')
  })

  it('does not let terminal.cwd replace an inactive selected workspace', async () => {
    setCurrentCwd('/Users/example/repo/.worktrees/feature')

    mockConfig({ terminal: { cwd: '/Users/example/new-workspace' } })
    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: null } }))

    await act(async () => {
      await result.current.refreshDigitConfig()
    })

    expect($currentCwd.get()).toBe('/Users/example/repo/.worktrees/feature')
  })

  it('does not let terminal.cwd replace an active session workspace', async () => {
    setCurrentCwd('/Users/example/repo/.worktrees/attached')

    mockConfig({ terminal: { cwd: '/Users/example/new-workspace' } })
    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: 'session-1' } }))

    await act(async () => {
      await result.current.refreshDigitConfig()
    })

    expect($currentCwd.get()).toBe('/Users/example/repo/.worktrees/attached')
  })

  it('does not let a stale forced config refresh overwrite newer draft selector intent', async () => {
    const profileConfig = deferred<Awaited<ReturnType<typeof getDigitConfig>>>()
    vi.mocked(getDigitConfig).mockReturnValueOnce(profileConfig.promise)

    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: null } }))

    let pendingRefresh!: Promise<void>
    act(() => {
      pendingRefresh = result.current.refreshDigitConfig(true)
    })
    expect(getDigitConfig).toHaveBeenCalled()

    // The user turns Fast off and chooses a different effort while the profile
    // defaults are still loading. That newer picker intent owns the composer.
    markComposerSelectionManual()
    setCurrentReasoningEffort('high')
    setCurrentFastMode(false)
    profileConfig.resolve({
      agent: { reasoning_effort: 'low', service_tier: 'priority' }
    } as Awaited<ReturnType<typeof getDigitConfig>>)

    await act(async () => {
      await pendingRefresh
    })

    expect($currentReasoningEffort.get()).toBe('high')
    expect($currentFastMode.get()).toBe(false)
  })

  it('does not let an older profile config overwrite a newer profile', async () => {
    const profileB = deferred<Awaited<ReturnType<typeof getDigitConfig>>>()
    const profileC = deferred<Awaited<ReturnType<typeof getDigitConfig>>>()
    vi.mocked(getDigitConfig).mockReturnValueOnce(profileB.promise).mockReturnValueOnce(profileC.promise)

    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: null } }))

    let refreshB!: Promise<void>
    let refreshC!: Promise<void>
    act(() => {
      refreshB = result.current.refreshDigitConfig(true)
      refreshC = result.current.refreshDigitConfig(true)
    })

    profileC.resolve({ agent: { reasoning_effort: 'low', service_tier: 'normal' } })
    await act(async () => {
      await refreshC
    })
    profileB.resolve({ agent: { reasoning_effort: 'high', service_tier: 'priority' } })
    await act(async () => {
      await refreshB
    })

    expect($currentReasoningEffort.get()).toBe('low')
    expect($currentFastMode.get()).toBe(false)
  })

  it('loads the profile terminal font for already-mounted terminal surfaces', async () => {
    mockConfig({ terminal: { font_family: 'MesloLGS NF' } })
    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: null } }))

    await act(async () => {
      await result.current.refreshDigitConfig()
    })

    expect($terminalFontFamily.get()).toBe('MesloLGS NF')
  })

  it('does not let an older profile response restore its terminal font', async () => {
    const profileB = deferred<Awaited<ReturnType<typeof getDigitConfig>>>()
    const profileC = deferred<Awaited<ReturnType<typeof getDigitConfig>>>()
    vi.mocked(getDigitConfig).mockReturnValueOnce(profileB.promise).mockReturnValueOnce(profileC.promise)
    const { result } = renderHook(() => useDigitConfig({ activeSessionIdRef: { current: null } }))

    let refreshB!: Promise<void>
    let refreshC!: Promise<void>
    act(() => {
      refreshB = result.current.refreshDigitConfig(true)
      refreshC = result.current.refreshDigitConfig(true)
    })

    profileC.resolve({ terminal: { font_family: 'Hack Nerd Font' } })
    await act(async () => {
      await refreshC
    })
    profileB.resolve({ terminal: { font_family: 'MesloLGS NF' } })
    await act(async () => {
      await refreshB
    })

    expect($terminalFontFamily.get()).toBe('Hack Nerd Font')
  })
})
