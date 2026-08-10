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
  digitmorfClipForMotion,
  digitmorfLivingAssetRelativePath,
  digitmorfLookDirection,
  type DigitmorfMotionSemantic
} from './digitmorf-runtime'

const assetPath = (path: string) => {
  const clean = path.replace(/^\/+/, '')
  const pageUrl = typeof window === 'undefined' ? import.meta.url : window.location.href

  return new URL(`${import.meta.env.BASE_URL}${clean}`, pageUrl).href
}

const THREE_URL = assetPath('digitmorf/vendor/three.module.min.js')
const GLTF_LOADER_URL = assetPath('digitmorf/vendor/GLTFLoader.js')
const FALLBACK_URL = assetPath('digitmorf/digitmorf-core.webp')

// Scene-linear lighting belongs to the 3D identity, not the application theme.
const LIGHT_CYAN = 0x31f5ff
const LIGHT_PALE = 0xe5fbff
const LIGHT_VOID = 0x02060a
const TRANSITION_MS = 760

const FORM_YAW: Readonly<Record<DigitmorfForm, number>> = {
  core: -Math.PI / 2,
  cursor: 0,
  trace: 0,
  archivist: 0,
  weaver: 0,
  forge: 0,
  sentinel: -Math.PI / 2,
  lantern: 0
}

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

const resolvedForm = (state: PetState) => {
  let form = deriveDigitmorfForm($petActivity.get(), $busy.get())

  if (form === 'core' && state === 'run') {
    form = 'cursor'
  }

  if (form === 'core' && state === 'jump') {
    form = 'lantern'
  }

  return form
}

const smoothstep = (value: number) => {
  const amount = Math.max(0, Math.min(1, value))

  return amount * amount * (3 - 2 * amount)
}

interface RendererNode {
  geometry?: { dispose?: () => void }
  material?: { dispose?: () => void } | Array<{ dispose?: () => void }>
  name?: string
  position?: { set?: (x: number, y: number, z: number) => void }
  traverse: (visit: (node: RendererNode) => void) => void
}

interface LivingClip {
  name: string
}

interface LivingEntry {
  form: DigitmorfForm
  group: { scale: { setScalar: (value: number) => void } }
  motionRoot: {
    position: { y: number }
    rotation: { x: number; y: number; z: number }
    scale: { set: (x: number, y: number, z: number) => void }
  }
  model: RendererNode
  mixer: {
    clipAction: (clip: LivingClip) => {
      enabled: boolean
      paused: boolean
      play: () => void
      reset: () => void
    }
    setTime: (seconds: number) => void
    stopAllAction: () => void
    update: (seconds: number) => void
  }
  clips: LivingClip[]
  activeClip?: string
  baseScale: number
}

interface DigitmorfPetProps {
  info: PetInfo
  drawW: number
  drawH: number
  pauseWhenUnfocused: boolean
  stateOverride?: PetState
  rowOverride?: string
}

/** Production renderer for the independently authored Digitmorf living forms. */
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

  // eslint-disable-next-line no-restricted-syntax -- hot animation props must not rebuild the WebGL scene
  useEffect(() => {
    stateOverrideRef.current = stateOverride
  }, [stateOverride])

  // eslint-disable-next-line no-restricted-syntax -- hot animation props must not rebuild the WebGL scene
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
    let removePointer: (() => void) | undefined
    let wake: () => void = () => undefined
    let requestGeneration = 0

    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const unsubs = [$petState.listen(() => wake()), $petActivity.listen(() => wake()), $busy.listen(() => wake())]

    const disposeNode = (root: RendererNode) => {
      root.traverse(node => {
        node.geometry?.dispose?.()
        const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : []
        materials.forEach(material => material.dispose?.())
      })
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

        const loadEntry = async (form: DigitmorfForm): Promise<LivingEntry> => {
          const gltf = await new Promise<{ scene: RendererNode; animations: LivingClip[] }>((resolve, reject) => {
            new loaderModule.GLTFLoader().load(
              assetPath(digitmorfLivingAssetRelativePath(form)),
              resolve,
              undefined,
              reject
            )
          })

          const model = gltf.scene
          const box = new THREE.Box3().setFromObject(model)
          const center = box.getCenter(new THREE.Vector3())
          const size = box.getSize(new THREE.Vector3())
          model.position?.set?.(-center.x, -center.y, -center.z)

          const group = new THREE.Group()
          const motionRoot = new THREE.Group()
          motionRoot.add(model)
          group.add(motionRoot)

          const baseScale = 7.5 / Math.max(size.x, size.y, size.z)
          motionRoot.scale.set(baseScale, baseScale, baseScale)
          motionRoot.rotation.y = FORM_YAW[form]

          return {
            form,
            group,
            motionRoot,
            model,
            mixer: new THREE.AnimationMixer(model),
            clips: gltf.animations,
            baseScale
          }
        }

        const activateClip = (entry: LivingEntry, clipName: string) => {
          if (entry.activeClip === clipName) {
            return
          }

          const clip = entry.clips.find(candidate => candidate.name === clipName)

          if (!clip) {
            throw new Error(`Digitmorf ${entry.form} is missing ${clipName}`)
          }

          entry.mixer.stopAllAction()
          const action = entry.mixer.clipAction(clip)
          action.reset()
          action.enabled = true
          action.play()
          action.paused = reducedMotion.matches

          if (reducedMotion.matches) {
            entry.mixer.setTime(0)
          }

          entry.activeClip = clipName
        }

        const disposeEntry = (entry: LivingEntry, removeFromScene = true) => {
          entry.mixer.stopAllAction()

          if (removeFromScene) {
            nextScene.remove(entry.group)
          }

          disposeNode(entry.model)
        }

        const initialState = stateOverrideRef.current ?? $petState.get()
        let targetForm = resolvedForm(initialState)
        let current = await loadEntry(targetForm)

        if (disposed) {
          disposeEntry(current, false)
          nextRenderer.dispose()

          return
        }

        nextScene.add(current.group)
        current.group.scale.setScalar(1)
        activateClip(current, digitmorfClipForMotion(semanticForState(initialState, rowOverrideRef.current)))

        let requestedForm: DigitmorfForm | null = null
        let lastTimestamp = performance.now()
        let pointerX = 0
        let pointerY = 0
        let pointerLookUntil = 0
        let transition: { from: LivingEntry; to: LivingEntry; start: number; removed: boolean } | undefined

        const requestForm = (form: DigitmorfForm) => {
          targetForm = form

          if (form === current.form || requestedForm || transition) {
            return
          }

          requestedForm = form
          const generation = ++requestGeneration
          void loadEntry(form)
            .then(next => {
              requestedForm = null

              if (disposed || generation !== requestGeneration || targetForm !== form) {
                disposeEntry(next, false)
                wake()

                return
              }

              const previous = current
              current = next
              current.group.scale.setScalar(reducedMotion.matches ? 1 : 0.001)
              nextScene.add(current.group)
              activateClip(previous, 'digitmorf_form_resonance')
              activateClip(
                current,
                digitmorfClipForMotion(
                  semanticForState(stateOverrideRef.current ?? $petState.get(), rowOverrideRef.current)
                )
              )

              if (reducedMotion.matches) {
                disposeEntry(previous)
              } else {
                transition = { from: previous, to: current, start: performance.now(), removed: false }
              }

              canvas.dataset.digitmorfForm = form
              wake()
            })
            .catch(error => {
              requestedForm = null
              console.warn(`Digitmorf living form ${form} failed to load`, error)
              canvas.dataset.digitmorfDegraded = form
              wake()
            })
        }

        const onPointerMove = (event: PointerEvent) => {
          const rect = canvas.getBoundingClientRect()

          if (!rect.width || !rect.height) {
            return
          }

          pointerX = ((event.clientX - rect.left) / rect.width) * 2 - 1
          pointerY = 1 - ((event.clientY - rect.top) / rect.height) * 2
          canvas.dataset.digitmorfLook = digitmorfLookDirection(pointerX, pointerY)
          pointerLookUntil = performance.now() + 1400
          wake()
        }

        canvas.addEventListener('pointermove', onPointerMove)
        removePointer = () => canvas.removeEventListener('pointermove', onPointerMove)

        const renderFrame = (now: number) => {
          raf = 0

          if (disposed || pauseController?.isPaused()) {
            return
          }

          const delta = Math.min(0.05, Math.max(0, (now - lastTimestamp) / 1000))
          lastTimestamp = now
          const state = stateOverrideRef.current ?? $petState.get()
          const nextForm = resolvedForm(state)
          requestForm(nextForm)

          const semantic = semanticForState(state, rowOverrideRef.current)

          if (!transition) {
            activateClip(current, digitmorfClipForMotion(semantic))
          }

          if (!reducedMotion.matches) {
            current.mixer.update(delta)
          }

          if (transition && !transition.removed) {
            transition.from.mixer.update(delta)
          }

          if (transition) {
            const amount = Math.min(1, (now - transition.start) / TRANSITION_MS)

            if (amount < 0.5) {
              transition.from.group.scale.setScalar(1 - smoothstep(amount * 2))
            } else {
              if (!transition.removed) {
                disposeEntry(transition.from)
                transition.removed = true
              }

              transition.to.group.scale.setScalar(smoothstep((amount - 0.5) * 2))
            }

            if (amount >= 1) {
              transition.to.group.scale.setScalar(1)
              transition = undefined
            }
          }

          const activity = $petActivity.get()
          const energy = activity.toolRunning ? 1 : 0.72
          const motion = semanticForState(state, rowOverrideRef.current)
          canvas.dataset.digitmorfMotion = motion

          if (reducedMotion.matches) {
            current.motionRoot.position.y = 0
            current.motionRoot.rotation.x = 0
            current.motionRoot.rotation.y = FORM_YAW[current.form]
            current.motionRoot.rotation.z = 0
            current.motionRoot.scale.set(current.baseScale, current.baseScale, current.baseScale)
          } else {
            const phase = now / 1000
            const bob = Math.sin(phase * (motion.startsWith('running') ? 7.2 : 2.4)) * 0.025 * energy
            const pointerActive = now < pointerLookUntil
            current.motionRoot.position.y = bob
            current.motionRoot.rotation.x = pointerActive ? -pointerY * 0.08 : 0
            current.motionRoot.rotation.y =
              FORM_YAW[current.form] + (pointerActive ? pointerX * 0.16 : Math.sin(phase * 0.35) * 0.06)
            current.motionRoot.rotation.z = motion === 'failed' ? -0.04 : 0
            current.motionRoot.scale.set(current.baseScale, current.baseScale, current.baseScale)
          }

          nextRenderer.render(nextScene, camera)

          if (!reducedMotion.matches || transition || requestedForm) {
            raf = window.requestAnimationFrame(renderFrame)
          }
        }

        wake = () => {
          if (!disposed && !pauseController?.isPaused() && raf === 0) {
            raf = window.requestAnimationFrame(renderFrame)
          }
        }

        pauseController = createRendererLoopPauseController(wake, { pauseWhenUnfocused })
        canvas.dataset.digitmorfForm = current.form
        canvas.dataset.digitmorfLook = 'n'
        canvas.dataset.digitmorfReady = 'true'
        setReady(true)
        wake()
      } catch (error) {
        if (!disposed) {
          console.warn('Digitmorf living-form renderer fell back to its Cycles poster', error)
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
      }
    }

    void boot()

    return () => {
      disposed = true
      requestGeneration += 1

      if (raf) {
        window.cancelAnimationFrame(raf)
      }

      removePointer?.()
      pauseController?.dispose()
      unsubs.forEach(unsub => unsub())
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
