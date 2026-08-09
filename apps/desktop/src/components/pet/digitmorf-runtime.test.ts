import { describe, expect, it } from 'vitest'

import {
  DIGITMORF_FORMS,
  DIGITMORF_LIVING_CLIPS,
  DIGITMORF_LIVING_PACK_STATUS,
  DIGITMORF_LOOK_DIRECTIONS,
  DIGITMORF_MORPH_TARGET_NAMES,
  DIGITMORF_MOTION_SEMANTICS,
  digitmorfLivingAssetRelativePath,
  digitmorfLookDirection,
  digitmorfLookVector,
  DigitmorfRigAdapter,
  type DigitmorfRigNode,
  exactDigitmorfWeights,
  proceduralDigitmorfWeights,
  sampleDigitmorfMotion
} from './digitmorf-runtime'

const dictionary = Object.fromEntries(Object.values(DIGITMORF_MORPH_TARGET_NAMES).map((name, index) => [name, index]))

function makeRig() {
  const meshes = Array.from({ length: 8 }, (_, index) => ({
    name: `morph-${index}`,
    morphTargetDictionary: dictionary,
    morphTargetInfluences: Array(7).fill(0)
  }))

  const lepton = { name: 'lepton_signal', rotation: { x: 0, y: 0, z: 0 } }

  const hadron = ['hadron', 'quark-1', 'quark-2', 'quark-3'].map(name => ({
    name,
    rotation: { x: 0, y: 0, z: 0 }
  }))

  const root: DigitmorfRigNode = { children: [...meshes, lepton, ...hadron], name: 'Digitmorf' }

  return { hadron, lepton, meshes, root }
}

describe('Digitmorf morph-rig runtime', () => {
  it('keeps the Blender living pack explicit and opt-in', () => {
    expect(DIGITMORF_LIVING_PACK_STATUS).toBe('work-in-progress')
    expect(Object.keys(DIGITMORF_LIVING_CLIPS)).toHaveLength(5)

    for (const form of DIGITMORF_FORMS) {
      expect(digitmorfLivingAssetRelativePath(form)).toBe(`digitmorf/living-v1/${form}/digitmorf-${form}-living-v1.glb`)
    }
  })

  it('drives all eight fixed-topology morph meshes', () => {
    const { meshes, root } = makeRig()
    const rig = new DigitmorfRigAdapter(root)
    rig.applyWeights(exactDigitmorfWeights('forge'))

    expect(rig.diagnostics).toEqual({ complete: true, hadronNodes: 4, leptonNodes: 1, morphMeshes: 8 })

    for (const mesh of meshes) {
      expect(mesh.morphTargetInfluences[dictionary.Forge]).toBe(1)
    }
  })

  it('quantizes attention into sixteen unique quantum directions', () => {
    const vectors = DIGITMORF_LOOK_DIRECTIONS.map(digitmorfLookVector)
    expect(new Set(vectors.map(vector => `${vector.x.toFixed(4)}:${vector.y.toFixed(4)}`)).size).toBe(16)
    expect(digitmorfLookDirection(0, 1)).toBe('n')
    expect(digitmorfLookDirection(1, 0)).toBe('e')

    const { hadron, lepton, root } = makeRig()
    const rig = new DigitmorfRigAdapter(root)
    rig.applyLook('e')
    expect(lepton.rotation.z).toBeGreaterThan(0)
    expect(hadron[0].rotation.z).toBeLessThan(0)
  })

  it('covers all nine motion semantics and produces bounded procedural mixtures', () => {
    expect(DIGITMORF_MOTION_SEMANTICS).toHaveLength(9)

    for (const semantic of DIGITMORF_MOTION_SEMANTICS) {
      const sample = sampleDigitmorfMotion(semantic, 0.37, 0.8)
      expect(Object.values(sample).every(value => typeof value === 'string' || Number.isFinite(value))).toBe(true)
    }

    const weights = proceduralDigitmorfWeights(42, 3.7)
    expect(Object.values(weights).reduce((sum, value) => sum + value, 0)).toBeCloseTo(1)
    expect(Object.values(weights).filter(value => value > 0)).toHaveLength(2)
  })
})
