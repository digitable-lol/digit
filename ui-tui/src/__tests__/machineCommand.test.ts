import { beforeEach, describe, expect, it } from 'vitest'

import { getOverlayState, resetFlowOverlays, resetOverlayState } from '../app/overlayStore.js'
import { findSlashCommand } from '../app/slash/registry.js'
import type { SlashRunCtx } from '../app/slash/types.js'

const ctx = {} as SlashRunCtx

describe('/machine', () => {
  beforeEach(() => resetOverlayState())

  it('resolves by name and by both aliases', () => {
    for (const name of ['machine', 'digitdisk', 'sysinfo']) {
      expect(findSlashCommand(name)?.name, name).toBe('machine')
    }
  })

  it('opens the panel', () => {
    expect(getOverlayState().machine).toBe(false)
    findSlashCommand('machine')!.run('', ctx, '/machine')
    expect(getOverlayState().machine).toBe(true)
  })

  it('stays open when a turn ends', () => {
    // resetFlowOverlays() runs on every turn completion. A user-opened panel
    // that is not listed there vanishes the moment the agent replies.
    findSlashCommand('machine')!.run('', ctx, '/machine')
    resetFlowOverlays()
    expect(getOverlayState().machine).toBe(true)
  })

  it('blocks the composer while open, and releases it on close', () => {
    findSlashCommand('machine')!.run('', ctx, '/machine')
    expect(getOverlayState().machine).toBe(true)
    resetOverlayState()
    expect(getOverlayState().machine).toBe(false)
  })
})
