import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DigitReadDirResult } from '@/global'
import type * as DigitModule from '@/digit'

import { discoverRuntimePlugins, watchRuntimePlugins } from './runtime-loader'

// getStatus would supply the connected backend's digit_home — a REMOTE path in
// remote mode. The disk scanner must NOT derive the plugin root from it (#66899).
const getStatus = vi.fn(async () => ({ digit_home: '/remote/box/.digit' }))

vi.mock('@/digit', async importActual => ({
  ...(await importActual<typeof DigitModule>()),
  getStatus: () => getStatus()
}))

const desktopPluginsRoot = vi.fn<() => Promise<string>>()
const readDir = vi.fn<(path: string) => Promise<DigitReadDirResult>>()
const watchDirectory = vi.fn<(path: string) => Promise<{ id: string }>>()
const onPreviewFileChanged = vi.fn()

beforeEach(() => {
  desktopPluginsRoot.mockReset()
  readDir.mockReset()
  watchDirectory.mockReset()
  onPreviewFileChanged.mockReset()
  getStatus.mockClear()
  ;(window as unknown as { digitDesktop: unknown }).digitDesktop = {
    desktopPluginsRoot,
    onPreviewFileChanged,
    readDir,
    watchDirectory
  }
})

afterEach(() => {
  delete (window as unknown as { digitDesktop?: unknown }).digitDesktop
})

describe('scanDiskPlugins (#66899)', () => {
  it('scans the Electron-resolved local root, never the backend digit_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.digit/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })

    await discoverRuntimePlugins()

    expect(desktopPluginsRoot).toHaveBeenCalled()
    expect(readDir).toHaveBeenCalledWith('/local/.digit/desktop-plugins')
    // The remote backend's digit_home must never feed the local plugin scan.
    expect(getStatus).not.toHaveBeenCalled()
    expect(readDir).not.toHaveBeenCalledWith('/remote/box/.digit/desktop-plugins')
  })

  it('no-ops when the resolver yields no local root', async () => {
    desktopPluginsRoot.mockResolvedValue('')

    await discoverRuntimePlugins()

    expect(readDir).not.toHaveBeenCalled()
  })
})

describe('watchRuntimePlugins dir watch (#66899)', () => {
  it('watches the Electron-resolved local root, never the backend digit_home', async () => {
    desktopPluginsRoot.mockResolvedValue('/local/.digit/desktop-plugins')
    readDir.mockResolvedValue({ entries: [] })
    watchDirectory.mockResolvedValue({ id: 'watch-1' })

    watchRuntimePlugins()
    // Drain the async scan + startDirWatch chains.
    await vi.waitFor(() => expect(watchDirectory).toHaveBeenCalled())

    expect(watchDirectory).toHaveBeenCalledWith('/local/.digit/desktop-plugins')
    expect(watchDirectory).not.toHaveBeenCalledWith('/remote/box/.digit/desktop-plugins')
    expect(getStatus).not.toHaveBeenCalled()
  })
})
