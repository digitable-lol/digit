import type { DigitmorfForm } from '@/store/pet'

export const DIGITMORF_FORMS = [
  'core',
  'cursor',
  'trace',
  'archivist',
  'weaver',
  'forge',
  'sentinel',
  'lantern'
] as const satisfies readonly DigitmorfForm[]

export const DIGITMORF_MORPH_TARGET_NAMES: Readonly<Record<Exclude<DigitmorfForm, 'core'>, string>> = {
  cursor: 'Cursor',
  trace: 'Trace',
  archivist: 'Archivist',
  weaver: 'Weaver',
  forge: 'Forge',
  sentinel: 'Sentinel',
  lantern: 'Lantern'
}

export const DIGITMORF_MOTION_SEMANTICS = [
  'idle',
  'running-right',
  'running-left',
  'waving',
  'jumping',
  'failed',
  'waiting',
  'running',
  'review'
] as const

export type DigitmorfMotionSemantic = (typeof DIGITMORF_MOTION_SEMANTICS)[number]

export const DIGITMORF_LOOK_DIRECTIONS = [
  'n',
  'nne',
  'ne',
  'ene',
  'e',
  'ese',
  'se',
  'sse',
  's',
  'ssw',
  'sw',
  'wsw',
  'w',
  'wnw',
  'nw',
  'nnw'
] as const

export type DigitmorfLookDirection = (typeof DIGITMORF_LOOK_DIRECTIONS)[number]
export type DigitmorfWeights = Record<DigitmorfForm, number>

export interface DigitmorfVector3 {
  x: number
  y: number
  z: number
}

export interface DigitmorfRigNode {
  name?: string
  children?: readonly DigitmorfRigNode[]
  morphTargetDictionary?: Readonly<Record<string, number>>
  morphTargetInfluences?: number[]
  position?: DigitmorfVector3
  rotation?: DigitmorfVector3
  scale?: DigitmorfVector3
}

const clamp01 = (value: number) => Math.max(0, Math.min(1, value))

const smoothstep = (value: number) => {
  const amount = clamp01(value)

  return amount * amount * (3 - 2 * amount)
}

export const zeroDigitmorfWeights = (): DigitmorfWeights => ({
  core: 0,
  cursor: 0,
  trace: 0,
  archivist: 0,
  weaver: 0,
  forge: 0,
  sentinel: 0,
  lantern: 0
})

export const exactDigitmorfWeights = (form: DigitmorfForm): DigitmorfWeights => {
  const weights = zeroDigitmorfWeights()
  weights[form] = 1

  return weights
}

export const blendDigitmorfWeights = (
  from: Readonly<DigitmorfWeights>,
  to: Readonly<DigitmorfWeights>,
  transition: number
): DigitmorfWeights => {
  const amount = smoothstep(transition)
  const result = zeroDigitmorfWeights()

  for (const form of DIGITMORF_FORMS) {
    result[form] = from[form] + (to[form] - from[form]) * amount
  }

  return result
}

/** Stable mixed form used while idle; it never changes topology or palette. */
export function proceduralDigitmorfWeights(seed: number, elapsedSeconds: number): DigitmorfWeights {
  const left = DIGITMORF_FORMS[Math.abs(seed) % DIGITMORF_FORMS.length]
  const right = DIGITMORF_FORMS[(Math.abs(seed * 5 + 3) % (DIGITMORF_FORMS.length - 1)) + 1]
  const amount = 0.25 + ((Math.sin(elapsedSeconds * 0.21 + seed) + 1) / 2) * 0.5
  const weights = zeroDigitmorfWeights()
  weights[left] += 1 - amount
  weights[right] += amount

  return weights
}

export function digitmorfLookDirection(x: number, y: number): DigitmorfLookDirection {
  const angle = Math.atan2(y, x)
  const compass = (Math.PI / 2 - angle + Math.PI * 2) % (Math.PI * 2)

  return DIGITMORF_LOOK_DIRECTIONS[Math.round(compass / (Math.PI / 8)) % 16]
}

export function digitmorfLookVector(direction: DigitmorfLookDirection): DigitmorfVector3 {
  const index = DIGITMORF_LOOK_DIRECTIONS.indexOf(direction)
  const angle = Math.PI / 2 - index * (Math.PI / 8)

  return { x: Math.cos(angle), y: Math.sin(angle), z: 0 }
}

const MOTION_FORMS: Readonly<Record<DigitmorfMotionSemantic, DigitmorfForm>> = {
  idle: 'core',
  'running-right': 'cursor',
  'running-left': 'cursor',
  waving: 'weaver',
  jumping: 'lantern',
  failed: 'sentinel',
  waiting: 'lantern',
  running: 'forge',
  review: 'archivist'
}

export function sampleDigitmorfMotion(semantic: DigitmorfMotionSemantic, elapsedSeconds: number, energy = 0.7) {
  const e = clamp01(energy)
  const phase = elapsedSeconds * Math.PI * 2
  const direction = semantic === 'running-left' ? -1 : 1
  const running = semantic === 'running' || semantic.startsWith('running-')
  const jumping = semantic === 'jumping'
  const failed = semantic === 'failed'
  const waiting = semantic === 'waiting'
  const waving = semantic === 'waving'
  const review = semantic === 'review'
  const frequency = running ? 1.85 : jumping ? 1.2 : waiting ? 0.28 : 0.62
  const wave = Math.sin(phase * frequency)
  const lift = jumping ? Math.max(0, Math.sin(phase * frequency)) : wave * (running ? 0.055 : 0.025)
  const squash = jumping ? Math.max(0, -Math.sin(phase * frequency)) * 0.1 : 0

  return {
    form: MOTION_FORMS[semantic],
    bob: failed ? -0.055 + Math.abs(wave) * 0.015 : lift * (0.55 + e * 0.45),
    lean: running ? direction * (0.08 + e * 0.08) : waving ? wave * 0.055 : 0,
    spin: review ? Math.sin(phase * 0.3) * 0.12 : running ? direction * Math.sin(phase * 0.45) * 0.1 : wave * 0.012,
    scaleX: 1 + squash + (running ? Math.abs(wave) * 0.025 : 0),
    scaleY: 1 + (failed ? -0.035 + wave * 0.01 : jumping ? lift * 0.08 - squash : 0),
    scaleZ: 1 + (waiting ? wave * 0.012 : waving ? Math.abs(wave) * 0.025 : 0)
  }
}

const walk = (node: DigitmorfRigNode, visit: (item: DigitmorfRigNode) => void) => {
  visit(node)
  node.children?.forEach(child => walk(child, visit))
}

export class DigitmorfRigAdapter {
  private readonly morphMeshes: DigitmorfRigNode[] = []
  private readonly leptonNodes: DigitmorfRigNode[] = []
  private readonly hadronNodes: DigitmorfRigNode[] = []
  private readonly bases = new Map<DigitmorfRigNode, DigitmorfVector3>()

  constructor(root: DigitmorfRigNode) {
    walk(root, node => {
      const dictionary = node.morphTargetDictionary

      if (
        dictionary &&
        node.morphTargetInfluences &&
        Object.values(DIGITMORF_MORPH_TARGET_NAMES).every(name => name in dictionary)
      ) {
        this.morphMeshes.push(node)
      }

      const name = node.name?.toLowerCase() ?? ''

      if (name.includes('lepton')) {
        this.leptonNodes.push(node)
      }

      if (name.includes('hadron') || name.includes('quark')) {
        this.hadronNodes.push(node)
      }

      if (node.rotation) {
        this.bases.set(node, { ...node.rotation })
      }
    })
  }

  get diagnostics() {
    return {
      morphMeshes: this.morphMeshes.length,
      leptonNodes: this.leptonNodes.length,
      hadronNodes: this.hadronNodes.length,
      complete: this.morphMeshes.length === 8 && this.leptonNodes.length >= 1 && this.hadronNodes.length >= 4
    }
  }

  applyWeights(weights: Readonly<DigitmorfWeights>) {
    for (const mesh of this.morphMeshes) {
      const dictionary = mesh.morphTargetDictionary as Readonly<Record<string, number>>
      const influences = mesh.morphTargetInfluences as number[]

      for (const [form, target] of Object.entries(DIGITMORF_MORPH_TARGET_NAMES) as [
        Exclude<DigitmorfForm, 'core'>,
        string
      ][]) {
        influences[dictionary[target]] = clamp01(weights[form])
      }
    }
  }

  applyLook(direction: DigitmorfLookDirection, strength = 1) {
    const vector = digitmorfLookVector(direction)
    const amount = clamp01(strength)

    for (const node of this.leptonNodes) {
      const base = this.bases.get(node)

      if (node.rotation && base) {
        node.rotation.x = base.x - vector.y * 0.34 * amount
        node.rotation.z = base.z + vector.x * 0.44 * amount
      }
    }

    for (const node of this.hadronNodes) {
      const base = this.bases.get(node)

      if (node.rotation && base) {
        node.rotation.x = base.x + vector.y * 0.12 * amount
        node.rotation.z = base.z - vector.x * 0.16 * amount
      }
    }
  }
}
