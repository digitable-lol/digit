import { useEffect, useRef, useState } from 'react'

import { createRendererLoopPauseController } from '@/lib/renderer-loop-pause'
import {
  $petActivity,
  $petState,
  deriveDigitmorfForm,
  type DigitmorfForm,
  type PetInfo,
  type PetState
} from '@/store/pet'
import { $busy } from '@/store/session'

import {
  blendDigitmorfWeights,
  DIGITMORF_FORMS,
  DIGITMORF_LOOK_DIRECTIONS,
  digitmorfLookDirection,
  type DigitmorfLookDirection,
  type DigitmorfMotionSemantic,
  DigitmorfRigAdapter,
  type DigitmorfRigNode,
  type DigitmorfWeights,
  exactDigitmorfWeights,
  proceduralDigitmorfWeights,
  sampleDigitmorfMotion
} from './digitmorf-runtime'

const assetPath = (path: string) => {
  const clean = path.replace(/^\/+/, '')
  const pageUrl = typeof window === 'undefined' ? import.meta.url : window.location.href

  // Resolve from the document rather than this emitted JS chunk. In packaged
  // Electron the document is dist/index.html while the chunk is dist/assets/*;
  // in Vite dev BASE_URL keeps the same helper rooted at the public directory.
  return new URL(`${import.meta.env.BASE_URL}${clean}`, pageUrl).href
}

const MODEL_URL = assetPath('digitmorf/digitmorf-morphrig-v1.glb')
const THREE_URL = assetPath('digitmorf/vendor/three.module.min.js')
const GLTF_LOADER_URL = assetPath('digitmorf/vendor/GLTFLoader.js')
const FALLBACK_URL = assetPath('digitmorf/digitmorf-core.webp')

// Identity palette in scene-linear numeric form. These are 3D lights, not UI
// chrome, so CSS theme tokens cannot represent them.
const LIGHT_CYAN = 0x31f5ff
const LIGHT_PALE = 0xe5fbff
const LIGHT_VOID = 0x02060a

const TRANSITION_MS = 760
const PROCEDURAL_MIX = 0.14

const semanticForState = (state: PetState, row?: string): DigitmorfMotionSemantic => {
  if (row === 'running-left' || row === 'running-right') {
    return row
  }

  if (state === 'wave') {
    return 'waving'
  }

  if (state === 'jump') {
    return 'jumping'
  }

  if (state === 'failed') {
    return 'failed'
  }

  if (state === 'waiting') {
    return 'waiting'
  }

  if (state === 'review') {
    return 'review'
  }

  if (state === 'run') {
    return 'running'
  }

  return 'idle'
}

const seedFor = (value: string) => {
  let hash = 2166136261

  for (const char of value) {
    hash = Math.imul(hash ^ char.charCodeAt(0), 16777619)
  }

  return hash >>> 0
}

interface DigitmorfPetProps {
  info: PetInfo
  drawW: number
  drawH: number
  pauseWhenUnfocused: boolean
  stateOverride?: PetState
  rowOverride?: string
}

/** Production GLB renderer for the bundled Digitmorf pet. */
export function DigitmorfPet({
  info,
  drawW,
  drawH,
  pauseWhenUnfocused,
  stateOverride,
  rowOverride
}: DigitmorfPetProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const stateOverrideRef = useRef(stateOverride)
  const rowOverrideRef = useRef(rowOverride)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)

  // eslint-disable-next-line no-restricted-syntax -- hot animation props stay current without rebuilding the GLB scene
  useEffect(() => {
    stateOverrideRef.current = stateOverride
  }, [stateOverride])

  // eslint-disable-next-line no-restricted-syntax -- hot animation props stay current without rebuilding the GLB scene
  useEffect(() => {
    rowOverrideRef.current = rowOverride
  }, [rowOverride])

  useEffect(() => {
    const canvas = canvasRef.current

    if (!canvas) {
      return
    }

    let disposed = false
    let raf = 0
    let renderer: { dispose: () => void; render: (scene: unknown, camera: unknown) => void } | null = null
    let scene: { traverse: (visit: (node: RendererNode) => void) => void } | null = null
    let pauseController: ReturnType<typeof createRendererLoopPauseController> | null = null

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const proceduralSeed = seedFor(info.slug ?? 'digitmorf')

    let wake: () => void = () => undefined

    const unsubs = [$petState.listen(() => wake()), $petActivity.listen(() => wake()), $busy.listen(() => wake())]

    type RendererNode = DigitmorfRigNode & {
      geometry?: { dispose?: () => void }
      material?: { dispose?: () => void } | Array<{ dispose?: () => void }>
      position?: DigitmorfRigNode['position'] & { set?: (x: number, y: number, z: number) => void }
      rotation?: DigitmorfRigNode['rotation']
      traverse: (visit: (node: RendererNode) => void) => void
    }

    const boot = async () => {
      try {
        const [THREE, loaderModule] = await Promise.all([
          import(/* @vite-ignore */ THREE_URL),
          import(/* @vite-ignore */ GLTF_LOADER_URL)
        ])

        if (disposed) {
          return
        }

        const nextScene = new THREE.Scene()
        const camera = new THREE.PerspectiveCamera(38, drawW / drawH, 0.05, 100)
        camera.position.set(0, 0.15, 7)
        camera.lookAt(0, 0, 0)

        const nextRenderer = new THREE.WebGLRenderer({
          alpha: true,
          antialias: true,
          canvas,
          powerPreference: 'high-performance'
        })

        nextRenderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5))
        nextRenderer.setSize(drawW, drawH, false)
        nextRenderer.outputColorSpace = THREE.SRGBColorSpace
        nextRenderer.toneMapping = THREE.ACESFilmicToneMapping
        nextRenderer.toneMappingExposure = 1.46
        renderer = nextRenderer
        scene = nextScene

        nextScene.add(new THREE.HemisphereLight(0xb7f8ff, LIGHT_VOID, 2.25))
        const cyan = new THREE.DirectionalLight(LIGHT_CYAN, 4.15)
        cyan.position.set(-4, 3, 4)
        nextScene.add(cyan)
        const pale = new THREE.DirectionalLight(LIGHT_PALE, 3.25)
        pale.position.set(4, 2, 5)
        nextScene.add(pale)

        const gltf = await new Promise<{ scene: RendererNode }>((resolve, reject) => {
          new loaderModule.GLTFLoader().load(MODEL_URL, resolve, undefined, reject)
        })

        if (disposed) {
          nextRenderer.dispose()

          return
        }

        const model = gltf.scene
        const box = new THREE.Box3().setFromObject(model)
        const center = box.getCenter(new THREE.Vector3())
        const size = box.getSize(new THREE.Vector3())
        model.position?.set?.(-center.x, -center.y, -center.z)

        const motionRoot = new THREE.Group()
        motionRoot.add(model)
        const baseScale = 7.5 / Math.max(size.x, size.y, size.z)
        motionRoot.scale.setScalar(baseScale)
        nextScene.add(motionRoot)

        const rig = new DigitmorfRigAdapter(model)

        if (!rig.diagnostics.complete) {
          throw new Error(`Incomplete Digitmorf rig: ${JSON.stringify(rig.diagnostics)}`)
        }

        const orbitNodes: Array<{ rotation: { y: number } }> = []
        model.traverse((node: { name?: string; rotation?: { y: number } }) => {
          if (
            node.rotation &&
            String(node.name ?? '')
              .toLowerCase()
              .includes('spatial_orbit')
          ) {
            orbitNodes.push(node as { rotation: { y: number } })
          }
        })

        let activeForm: DigitmorfForm = deriveDigitmorfForm($petActivity.get(), $busy.get())
        let fromWeights = exactDigitmorfWeights(activeForm)
        let targetWeights = exactDigitmorfWeights(activeForm)
        let displayedWeights = exactDigitmorfWeights(activeForm)
        let transitionStart = performance.now()
        let pointerDirection: DigitmorfLookDirection = DIGITMORF_LOOK_DIRECTIONS[0]
        let pointerLookUntil = 0

        const changeForm = (next: DigitmorfForm, now: number) => {
          if (next === activeForm) {
            return
          }

          activeForm = next
          canvas.dataset.digitmorfForm = next
          fromWeights = { ...displayedWeights }
          targetWeights = exactDigitmorfWeights(next)
          transitionStart = now
        }

        const onPointerMove = (event: PointerEvent) => {
          const rect = canvas.getBoundingClientRect()

          if (!rect.width || !rect.height) {
            return
          }

          const x = ((event.clientX - rect.left) / rect.width) * 2 - 1
          const y = 1 - ((event.clientY - rect.top) / rect.height) * 2
          pointerDirection = digitmorfLookDirection(x, y)
          canvas.dataset.digitmorfLook = pointerDirection
          pointerLookUntil = performance.now() + 1400
          wake()
        }

        canvas.addEventListener('pointermove', onPointerMove)

        const renderFrame = (now: number) => {
          raf = 0

          if (disposed || pauseController?.isPaused()) {
            return
          }

          const state = stateOverrideRef.current ?? $petState.get()
          const activity = $petActivity.get()
          let nextForm = deriveDigitmorfForm(activity, $busy.get())

          if (nextForm === 'core' && state === 'run') {
            nextForm = 'cursor'
          }

          if (nextForm === 'core' && state === 'jump') {
            nextForm = 'lantern'
          }

          changeForm(nextForm, now)

          const transition = reducedMotion.matches ? 1 : (now - transitionStart) / TRANSITION_MS
          displayedWeights = blendDigitmorfWeights(fromWeights, targetWeights, transition)

          if (!reducedMotion.matches && nextForm === 'core' && transition >= 1) {
            const procedural = proceduralDigitmorfWeights(proceduralSeed, now / 1000)
            const live = { ...displayedWeights } as DigitmorfWeights

            for (const form of DIGITMORF_FORMS) {
              live[form] = displayedWeights[form] * (1 - PROCEDURAL_MIX) + procedural[form] * PROCEDURAL_MIX
            }

            rig.applyWeights(live)
          } else {
            rig.applyWeights(displayedWeights)
          }

          const semantic = semanticForState(state, rowOverrideRef.current)
          const motion = sampleDigitmorfMotion(semantic, now / 1000, activity.toolRunning ? 1 : 0.72)
          canvas.dataset.digitmorfMotion = semantic

          if (reducedMotion.matches) {
            motionRoot.position.y = 0
            motionRoot.rotation.set(0, 0, 0)
            motionRoot.scale.setScalar(baseScale)
          } else {
            motionRoot.position.y = motion.bob
            motionRoot.rotation.z = motion.lean
            motionRoot.rotation.y = motion.spin
            motionRoot.scale.set(baseScale * motion.scaleX, baseScale * motion.scaleY, baseScale * motion.scaleZ)
            orbitNodes.forEach((node, index) => {
              node.rotation.y += (index % 2 ? -1 : 1) * 0.0012 * (index + 1)
            })
          }

          const autoIndex = Math.floor((now / 1000) * 1.2) % DIGITMORF_LOOK_DIRECTIONS.length
          rig.applyLook(
            now < pointerLookUntil ? pointerDirection : DIGITMORF_LOOK_DIRECTIONS[autoIndex],
            now < pointerLookUntil ? 1 : 0.34
          )
          nextRenderer.render(nextScene, camera)

          if (!reducedMotion.matches || transition < 1) {
            raf = window.requestAnimationFrame(renderFrame)
          }
        }

        wake = () => {
          if (!disposed && !pauseController?.isPaused() && raf === 0) {
            raf = window.requestAnimationFrame(renderFrame)
          }
        }

        pauseController = createRendererLoopPauseController(wake, { pauseWhenUnfocused })
        canvas.dataset.digitmorfForm = activeForm
        canvas.dataset.digitmorfLook = pointerDirection
        canvas.dataset.digitmorfReady = 'true'
        setReady(true)
        wake()

        return () => canvas.removeEventListener('pointermove', onPointerMove)
      } catch (error) {
        if (!disposed) {
          console.warn('Digitmorf 3D renderer fell back to its Cycles poster', error)
          canvas.dataset.digitmorfError = 'true'
          setFailed(true)
        }

        scene?.traverse(node => {
          node.geometry?.dispose?.()
          const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : []
          materials.forEach(material => material.dispose?.())
        })
        renderer?.dispose()
        scene = null
        renderer = null

        return undefined
      }
    }

    let removePointer: (() => void) | undefined
    void boot().then(cleanup => {
      removePointer = cleanup
    })

    return () => {
      disposed = true

      if (raf) {
        window.cancelAnimationFrame(raf)
      }

      removePointer?.()
      pauseController?.dispose()

      for (const unsub of unsubs) {
        unsub()
      }

      scene?.traverse(node => {
        node.geometry?.dispose?.()
        const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : []
        materials.forEach(material => material.dispose?.())
      })
      renderer?.dispose()
    }
  }, [drawH, drawW, info.slug, pauseWhenUnfocused])

  return (
    <span style={{ display: 'block', height: drawH, position: 'relative', width: drawW }}>
      <img
        alt=""
        aria-hidden
        src={FALLBACK_URL}
        style={{
          height: drawH,
          inset: 0,
          objectFit: 'contain',
          opacity: ready && !failed ? 0 : 1,
          position: 'absolute',
          width: drawW
        }}
      />
      <canvas
        aria-label={info.displayName ? `${info.displayName} pet` : 'pet'}
        height={drawH}
        ref={canvasRef}
        style={{ height: drawH, opacity: ready && !failed ? 1 : 0, position: 'relative', width: drawW }}
        width={drawW}
      />
    </span>
  )
}
