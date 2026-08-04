import { useStore } from '@nanostores/react'
import { useEffect, useRef } from 'react'

import { $speechAnalyser, foldBands, SPEECH_BAND_COUNT } from '@/lib/speech-analyser'
import { createRendererLoopPauseController } from '@/lib/renderer-loop-pause'
import { cn } from '@/lib/utils'

/**
 * The desktop's share of "Digit talks": a spectrum drawn on canvas from the
 * audio actually leaving the speaker.
 *
 * Decoration, unlike the highlight beside it — but decoration that has to be
 * true. The bars come off an `AnalyserNode` inserted in the playback graph,
 * so they move because sound is moving, and they are simply absent when
 * nothing is playing. There is no idle animation and no synthetic waveform:
 * a bar chart that dances while the machine is silent teaches the user to
 * distrust it.
 *
 * The loop stops when the analyser goes away and when the window loses focus,
 * which is the codebase's rule for every persistent renderer loop.
 */

const BAR_GAP = 2
const MIN_BAR_HEIGHT = 2
const CORNER = 1

export function SpeechBars({ className, height = 24 }: { className?: string; height?: number }) {
  const analyser = useStore($speechAnalyser)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current

    if (!analyser || !canvas) {
      return
    }

    const context = canvas.getContext('2d')

    if (!context) {
      return
    }

    const bins = new Uint8Array(analyser.frequencyBinCount)
    let frame = 0

    const pauseController = createRendererLoopPauseController(
      () => {
        if (!pauseController.isPaused() && !frame) {
          frame = window.requestAnimationFrame(draw)
        }
      },
      { pauseWhenUnfocused: true }
    )

    function draw() {
      frame = 0

      // A hidden or unfocused window animating a spectrum nobody is looking
      // at is the classic renderer battery leak; the loop simply stops and
      // the controller restarts it on focus.
      if (pauseController.isPaused() || !canvas || !context) {
        return
      }

      const ratio = window.devicePixelRatio || 1
      const width = canvas.clientWidth

      if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
        canvas.width = Math.round(width * ratio)
        canvas.height = Math.round(height * ratio)
      }

      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      context.clearRect(0, 0, width, height)

      analyser?.getByteFrequencyData(bins)

      const bands = foldBands(bins, SPEECH_BAND_COUNT)
      const barWidth = Math.max(1, (width - BAR_GAP * (bands.length - 1)) / bands.length)

      // currentColor, so the bars inherit whatever tone the surrounding
      // message uses in either theme — no palette of its own to keep in sync.
      context.fillStyle = getComputedStyle(canvas).color

      bands.forEach((level, index) => {
        const barHeight = Math.max(MIN_BAR_HEIGHT, level * height)
        const x = index * (barWidth + BAR_GAP)
        const y = height - barHeight

        context.beginPath()
        context.roundRect(x, y, barWidth, barHeight, CORNER)
        context.fill()
      })

      frame = window.requestAnimationFrame(draw)
    }

    frame = window.requestAnimationFrame(draw)

    return () => {
      if (frame) {
        window.cancelAnimationFrame(frame)
      }

      pauseController.dispose()
    }
  }, [analyser, height])

  if (!analyser) {
    return null
  }

  return (
    <canvas
      aria-hidden="true"
      className={cn('w-full text-muted-foreground', className)}
      ref={canvasRef}
      style={{ height: `${height}px` }}
    />
  )
}
