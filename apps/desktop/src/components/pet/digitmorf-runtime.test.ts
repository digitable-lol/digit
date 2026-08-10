import { describe, expect, it } from 'vitest'

import {
  DIGITMORF_FORMS,
  DIGITMORF_LIVING_CLIPS,
  DIGITMORF_LIVING_PACK_STATUS,
  DIGITMORF_LOOK_DIRECTIONS,
  DIGITMORF_MORPH_TARGET_NAMES,
  DIGITMORF_MOTION_SEMANTICS,
  digitmorfClipForMotion,
  digitmorfLivingAssetRelativePath,
  digitmorfLivingFormGraphRelativePath,
  digitmorfLivingManifestRelativePath,
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
  it('publishes the approved living pack and its renderer paths', () => {
    expect(DIGITMORF_LIVING_PACK_STATUS).toBe('approved-modular-hero')
    expect(new Set(Object.values(DIGITMORF_LIVING_CLIPS))).toEqual(
      new Set(['digitmorf_idle', 'digitmorf_attention', 'digitmorf_inspect', 'digitmorf_form_resonance'])
    )

    for (const form of DIGITMORF_FORMS) {
      expect(digitmorfLivingAssetRelativePath(form)).toBe(`digitmorf/living-v1/${form}/digitmorf-${form}-living-v1.glb`)
      expect(digitmorfLivingManifestRelativePath(form)).toContain(`/living-v1/${form}/`)
      expect(digitmorfLivingFormGraphRelativePath(form)).toContain(`/living-v1/${form}/form-graph.json`)
    }
  })

  it('maps every pet motion onto an action shipped by every living form', () => {
    const shipped = new Set(Object.values(DIGITMORF_LIVING_CLIPS))

    for (const semantic of DIGITMORF_MOTION_SEMANTICS) {
      expect(shipped.has(digitmorfClipForMotion(semantic))).toBe(true)
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
