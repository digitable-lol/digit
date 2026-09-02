import { PassThrough } from 'stream'

import { renderSync } from '@digit/ink'
import React from 'react'
import { describe, expect, it, vi } from 'vitest'

const inputHarness = vi.hoisted(() => ({
  handler: undefined as undefined | ((input: string, key: Record<string, boolean>) => void)
}))

// Stub useInput: PassThrough stdin can't enter raw mode under renderSync.
// Box/Text/ScrollBox pass through to real Ink so the output is the real thing.
vi.mock('@digit/ink', async importOriginal => {
  const mod = await importOriginal()

  return {
    ...mod,
    useInput: (handler: (input: string, key: Record<string, boolean>) => void) => {
      inputHarness.handler = handler
    }
  }
})

import { MachinePanel } from '../components/machinePanel.js'
import type { GatewayClient } from '../gatewayClient.js'
import type { DigitdiskStatusResponse } from '../gatewayTypes.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'

const GIB = 1024 * 1024 * 1024

/** A trimmed but shape-faithful `digitdisk status --json` payload. */
const SNAPSHOT: DigitdiskStatusResponse = {
  binary: '/usr/local/bin/digitdisk',
  required: '0.6.0',
  snapshot: {
    disks: [
      {
        available_bytes: 291 * GIB,
        fs_type: 'ext4',
        mount_point: '/',
        source: '/dev/mapper/vg-root',
        total_bytes: 1800 * GIB,
        use_percent: 83.6,
        used_bytes: 1400 * GIB
      },
      {
        available_bytes: 222 * GIB,
        fs_type: 'tmpfs',
        mount_point: '/tmp',
        source: 'tmpfs',
        total_bytes: 249 * GIB,
        use_percent: 10.8,
        used_bytes: 27 * GIB
      }
    ],
    gpus: [{ driver: 'mgag200', memory_total_bytes: null, name: 'Matrox G200eW3', vendor: 'Matrox' }],
    host: {
      cpu_model: 'AMD EPYC 7742 64-Core Processor',
      distro: 'Ubuntu 26.04 LTS',
      hostname: 'dev',
      kernel_release: '7.0.0-29-generic',
      machine: 'x86_64',
      model: 'Dell Inc. PowerEdge C6525',
      uptime_human: '26д 05:22'
    },
    load: {
      '15min': 48.59,
      '1min': 59.26,
      '5min': 52.5,
      busy_percent: 10.29,
      cores: [{ busy_percent: 100 }, { busy_percent: 2.5 }, { busy_percent: 1.7 }, { busy_percent: 60 }],
      cpu_count: 4,
      sample_millis: 200
    },
    memory: {
      available_bytes: 410 * GIB,
      buff_cache_bytes: 314 * GIB,
      swap_total_bytes: 678 * GIB,
      swap_used_bytes: 46 * GIB,
      total_bytes: 499 * GIB,
      used_bytes: 125 * GIB
    },
    missing: { 'рабочий стол': 'no desktop session in this login' },
    network: [
      { name: 'eno33', oper_state: 'up', rx_bytes: 488 * GIB, tx_bytes: 383 * GIB },
      { name: 'eno1', oper_state: 'down', rx_bytes: 0, tx_bytes: 0 }
    ],
    processes: {
      running: 18,
      threads: 7878,
      top_by_memory: [
        { cmdline: '/srv/bootstrap/flang emit compiler.flang', pid: 3459468, rss_bytes: 33 * GIB, user: 'a' }
      ],
      total: 4509
    },
    taken_at: '2026-09-02T20:15:26Z'
  },
  state: 'ok',
  version: '0.6.0',
  version_known: true
}

const gatewayReturning = (payload: unknown) =>
  ({ off: vi.fn(), on: vi.fn(), request: vi.fn(() => Promise.resolve(payload)) }) as unknown as GatewayClient

/** Mount MachinePanel over a fake gateway and read back the drawn text. */
function mount(gw: GatewayClient) {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()

  let output = ''

  Object.assign(stdout, { columns: 100, isTTY: false, rows: 44 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', chunk => {
    output += chunk.toString()
  })

  inputHarness.handler = undefined
  const element = React.createElement(MachinePanel, { gw, onClose: () => {}, t: DEFAULT_THEME })

  const instance = renderSync(element, {
    patchConsole: false,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })

  return {
    cleanup: () => {
      instance.unmount()
      instance.cleanup()
    },
    output: () => stripAnsi(output),
    settle: async () => {
      await new Promise(r => setTimeout(r, 10))
    }
  }
}

describe('MachinePanel', () => {
  it('shows a spinner line before the snapshot lands, so the TUI never looks hung', () => {
    const panel = mount(gatewayReturning(SNAPSHOT))

    expect(panel.output()).toContain('running digitdisk status…')
    panel.cleanup()
  })

  it('draws every section from a fresh digitdisk', async () => {
    const panel = mount(gatewayReturning(SNAPSHOT))

    await panel.settle()
    const out = panel.output()

    for (const section of ['system', 'cpu', 'memory', 'disks', 'network', 'graphics']) {
      expect(out, section).toContain(section)
    }

    expect(out).toContain('AMD EPYC 7742')
    expect(out).toContain('digitdisk 0.6.0')
    expect(out).toContain('eno33')
    // Per-core strip: one glyph per core.
    expect(out).toMatch(/[▁▂▃▄▅▆▇█]/)
    panel.cleanup()
  })

  it('names the required version when digitdisk is not installed', async () => {
    const panel = mount(
      gatewayReturning({
        hint: 'install digitdisk 0.6.0 or newer: `brew install digitable-lol/tap/digitdisk`',
        required: '0.6.0',
        state: 'missing'
      })
    )

    await panel.settle()
    const out = panel.output()

    expect(out).toContain('digitdisk is not installed')
    expect(out).toContain('0.6.0')
    expect(out).toContain('brew install')
    panel.cleanup()
  })

  it('refuses to render an outdated digitdisk, and says both numbers', async () => {
    const panel = mount(
      gatewayReturning({
        binary: '/usr/local/bin/digitdisk',
        hint: 'upgrade it',
        required: '0.6.0',
        state: 'outdated',
        version: '0.4.0'
      })
    )

    await panel.settle()
    const out = panel.output()

    expect(out).toContain('0.4.0')
    expect(out).toContain('0.6.0')
    expect(out).not.toContain('AMD EPYC')
    panel.cleanup()
  })

  it('reports a broken run instead of blanking', async () => {
    const panel = mount(
      gatewayReturning({
        binary: '/usr/local/bin/digitdisk',
        error: 'timed out after 30s',
        required: '0.6.0',
        state: 'failed'
      })
    )

    await panel.settle()
    expect(panel.output()).toContain('timed out after 30s')
    panel.cleanup()
  })

  it('surfaces an RPC failure rather than spinning forever', async () => {
    const gw = {
      off: vi.fn(),
      on: vi.fn(),
      request: vi.fn(() => Promise.reject(new Error('gateway went away')))
    } as unknown as GatewayClient

    const panel = mount(gw)

    await panel.settle()
    const out = panel.output()

    // The harness keeps every frame, so assert on ORDER: the error must be
    // drawn after the spinner, i.e. the spinner was replaced, not left up.
    expect(out).toContain('gateway went away')
    expect(out.lastIndexOf('gateway went away')).toBeGreaterThan(out.lastIndexOf('running digitdisk status…'))
    panel.cleanup()
  })

  it('asks the gateway, not the terminal, for the snapshot', () => {
    const gw = gatewayReturning(SNAPSHOT)
    const panel = mount(gw)

    expect(gw.request).toHaveBeenCalledWith('digitdisk.status', {})
    panel.cleanup()
  })
})
