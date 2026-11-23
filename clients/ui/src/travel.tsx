/**
 * The panel's one orb. A single WebGL canvas, drawn at the largest size the orb takes (the middle of the voice layer)
 * and scaled with a transform, rests on one of three empty places in the layout (the launcher, the header, the voice
 * layer) and follows it; from one to another it hops (`fly`): it fades out where it was and fades in where it goes,
 * transform and opacity only, never crossing the page. The plasma's scale follows the size on screen, so the small orb
 * looks as it always did and the big one too.
 *
 * Its motion: idle it drifts (faster while a turn runs); in voice mode it follows the voice as the stage did (the user's
 * voice swells it, the agent's moves the plasma), in the launcher too while the panel is minimized, with the thinking
 * ring around it.
 *
 * The orb can be replaced, in this order: by one element of the page (`slot="orb"`) or by images, one per chat state
 * (`OrbImages`). Either travels in the same box with the same flight; the component draws nothing of its own on it (no
 * plasma, no ring, no swell): the element follows the host's `data-state`, `data-view` and `data-orb`.
 */
import type { VNode } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'

import type { ChatState } from './chatstate'
import type { Controller } from './controller'
import { hopAt, type Rest, type Spot } from './flow'
import { createOrb, newMotion, orbFallback, orbZoom, reducedMotion, stepMotion, swell, type OrbPhase } from './orb'
import { orbFromStyle, orbImageFor, orbTransition, type OrbColors, type OrbImages } from './theme'

/** The largest size the orb takes (CSS pixels): the canvas is drawn at this size. */
export const ORB_MAX = 168

export interface TravelHandle {
  fly(from: Spot, to: Spot, ms: number): Promise<void>
}

/** Where a resting place is: its centre and its height (a place is square, and may be narrower while it opens). */
export function spotOf(el: Element | null | undefined): Spot | null {
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (!r.height) return null
  return { x: r.left + r.height / 2, y: r.top + r.height / 2, size: r.height }
}

export interface TravelProps {
  handle: { current: TravelHandle | null }
  controller: Controller
  /** Where the orb rests, or that it is travelling. */
  at: Rest | 'flying'
  /** The element of a resting place. */
  find: (r: Rest) => Element | null
  /** Hold still (the box around it is opening or closing). */
  frozen: boolean
  /** Something else stands where it rests (a logo, a slotted avatar). */
  hidden: boolean
  phase: OrbPhase
  busy: boolean
  muted: boolean
  colors?: Partial<OrbColors> | undefined
  /** An element of the page stands in for the orb (`slot="orb"`). */
  custom?: boolean
  /** Images per chat state stand in for the orb (when no element does). */
  images?: OrbImages | null | undefined
  /** The conversation's state, for the images. */
  state?: ChatState
}

/** The image of the state (or the one-shot image of the change that led to it); a still frame with reduced motion. */
function OrbImage({ images, state }: { images: OrbImages; state: ChatState }): VNode {
  const prev = useRef(state)
  const [once, setOnce] = useState<string | null>(null)
  const still = reducedMotion()
  // Before the paint: the state's own image never shows for a frame ahead of the one-shot image.
  useLayoutEffect(() => {
    const from = prev.current
    prev.current = state
    const t = from === state || still ? null : orbTransition(images, from, state)
    setOnce(t?.src ?? null)
    if (!t) return
    const id = setTimeout(() => setOnce(null), t.ms)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state])
  // Every image is fetched once, so a change of state shows the next one at once.
  const all = JSON.stringify(images)
  useEffect(() => {
    if (typeof Image === 'undefined') return
    for (const src of [images.idle, images.still, images.listening, images.thinking, images.tool, images.confirm, images.speaking, images.done, ...Object.values(images.transitions ?? {}).map((t) => t?.src)])
      if (src) new Image().src = src
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all])
  const src = once ?? orbImageFor(images, state)
  if (still && images.still) return <img class="orbimg" part="orb-image" src={images.still} alt="" data-state={state} />
  if (still) return <StillFrame src={src} state={state} />
  return <img class="orbimg" part="orb-image" src={src} alt="" data-state={state} />
}

/** The first frame of an image (an animated one stands still): drawn on a canvas, or the image itself without one. */
function StillFrame({ src, state }: { src: string; state: ChatState }): VNode {
  const cv = useRef<HTMLCanvasElement>(null)
  const [drawn, setDrawn] = useState(true)
  useEffect(() => {
    const canvas = cv.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx || typeof Image === 'undefined') {
      setDrawn(false)
      return
    }
    let live = true
    const img = new Image()
    img.onload = () => {
      if (!live) return
      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      ctx.drawImage(img, 0, 0)
    }
    img.src = src
    return () => {
      live = false
    }
  }, [src])
  return drawn ? <canvas ref={cv} class="orbimg" part="orb-image" data-state={state} /> : <img class="orbimg" part="orb-image" src={src} alt="" data-state={state} />
}

interface Flight {
  a: Spot
  b: Spot
  start: number
  ms: number
  done: () => void
}

export function TravelOrb(props: TravelProps): VNode {
  const host = useRef<HTMLDivElement>(null)
  const core = useRef<HTMLDivElement>(null)
  const cv = useRef<HTMLCanvasElement>(null)
  const ring = useRef<HTMLCanvasElement>(null)
  const live = useRef(props)
  live.current = props
  const flight = useRef<Flight | null>(null)
  const pos = useRef<Spot | null>(null)
  const redraw = useRef<(() => void) | null>(null)
  const recolor = useRef<(() => void) | null>(null)
  const kind = props.custom ? 'element' : props.images ? 'images' : 'orb'
  const key = `${kind}:${JSON.stringify(props.colors ?? {})}`

  /** Rest on a place; `scale` and `opacity` shrink and fade it about its centre (a hop). */
  const place = (p: Spot, scale = 1, opacity = 1) => {
    const el = host.current
    if (!el) return
    pos.current = p
    const size = p.size * scale
    const k = size / ORB_MAX
    el.style.transform = `translate(${(p.x - size / 2).toFixed(2)}px,${(p.y - size / 2).toFixed(2)}px) scale(${k.toFixed(4)})`
    el.style.opacity = opacity === 1 ? '' : opacity.toFixed(3)
    el.style.setProperty('--k', (1 / k).toFixed(3))
  }

  props.handle.current = {
    fly(a, b, ms) {
      flight.current?.done()
      return new Promise<void>((resolve) => {
        let over = false
        const done = () => {
          if (over) return
          over = true
          if (flight.current?.done === done) flight.current = null
          resolve()
        }
        flight.current = { a, b, start: performance.now(), ms, done }
        place(a)
        // A hidden tab pauses the frames: the hop still ends.
        setTimeout(done, ms + 400)
      })
    },
  }

  useEffect(() => {
    const canvas = cv.current
    const rc = ring.current
    const el = host.current
    if (!el) return
    // An element or images in place of the orb: no WebGL, only the places and the hops.
    const orb = canvas ? createOrb(canvas, ORB_MAX, live.current.colors) : null
    const ctx = rc?.getContext('2d') ?? null
    const dpr = Math.min(2, (typeof devicePixelRatio === 'number' && devicePixelRatio) || 1)
    const RS = rc ? (rc.width = rc.height = Math.round(ORB_MAX * 2 * dpr)) : 0
    const accent = (typeof getComputedStyle === 'function' && getComputedStyle(el).getPropertyValue('--ek-accent').trim()) || '#a78bfa'
    const m = newMotion()
    const still = reducedMotion()
    let last = performance.now()
    let raf = 0
    let ringOn = false
    const frame = (now: number) => {
      const dt = Math.min(0.1, Math.max(0, (now - last) / 1000))
      last = now
      const L = live.current
      const f = flight.current
      if (f) {
        const t = (now - f.start) / f.ms
        if (t >= 1) {
          place(f.b)
          f.done()
        } else {
          const h = hopAt(f.a, f.b, t)
          place(h.spot, h.scale, h.opacity)
        }
      } else if (L.at !== 'flying' && !L.frozen) {
        const p = spotOf(L.find(L.at))
        if (p) place(p)
      }
      el.style.visibility = pos.current && !(L.hidden && !f && L.at !== 'flying') ? '' : 'hidden'
      if (!canvas) {
        if (!still) raf = requestAnimationFrame(frame)
        return
      }
      const size = pos.current?.size ?? ORB_MAX
      const voice = L.phase !== 'idle'
      const { frame: fr, mode } = stepMotion(m, { phase: L.phase, busy: L.busy, muted: L.muted, stirred: !!f, signals: voice ? L.controller.voice?.signals : null, now, dt })
      orb?.draw({ ...fr, zoom: orbZoom(size) })
      // Smaller in the launcher, so the orb does not grow over the page.
      const sw = voice ? 1 + (swell(m.level, mode) - 1) * (size < 100 ? 0.5 : 1) : 1
      if (core.current) core.current.style.transform = sw === 1 ? '' : `scale(${sw.toFixed(3)})`
      const thinking = L.phase === 'thinking' && !L.hidden
      if (ctx && (thinking || ringOn)) {
        ctx.clearRect(0, 0, RS, RS)
        ringOn = thinking
        if (thinking) {
          const k = size / ORB_MAX
          const u = RS / (ORB_MAX * 2)
          const gap = 7 + 7 * Math.min(1, Math.max(0, (size - 60) / 100))
          const a0 = now / 380
          ctx.strokeStyle = accent
          ctx.globalAlpha = 0.85
          ctx.lineWidth = (2 / k) * u
          ctx.lineCap = 'round'
          ctx.beginPath()
          ctx.arc(RS / 2, RS / 2, ((ORB_MAX / 2) * sw + gap / k) * u, a0, a0 + 1.1)
          ctx.stroke()
        }
      }
      if (!still) raf = requestAnimationFrame(frame)
    }
    redraw.current = () => {
      if (still) frame(performance.now())
    }
    recolor.current = () => {
      if (orb) orb.recolor(live.current.colors)
      else if (canvas) canvas.style.background = orbFallback(orbFromStyle(canvas, live.current.colors))
    }
    const onResize = () => redraw.current?.()
    addEventListener('resize', onResize)
    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      removeEventListener('resize', onResize)
      redraw.current = null
      recolor.current = null
      flight.current?.done()
      orb?.dispose()
    }
  }, [key])

  // The colours may come from a stylesheet or a scheme that changed since: read them again on every move.
  useEffect(() => recolor.current?.(), [props.at])

  // Without motion the frame is drawn once per change; with motion the loop follows on its own, but the first place is
  // set at once (no frame at the corner of the page).
  useLayoutEffect(() => {
    if (!flight.current && props.at !== 'flying' && !props.frozen) {
      const p = spotOf(props.find(props.at))
      if (p) place(p)
    }
    if (host.current) host.current.style.visibility = pos.current && !(props.hidden && props.at !== 'flying') ? '' : 'hidden'
    redraw.current?.()
  })

  const bg = orbFallback(props.colors)
  return (
    <div
      ref={host}
      class="orbfly"
      part="orb"
      aria-hidden="true"
      data-at={props.at}
      data-phase={props.phase}
      data-kind={kind}
      style={{ position: 'fixed', left: 0, top: 0, width: `${ORB_MAX}px`, height: `${ORB_MAX}px`, transformOrigin: '0 0', pointerEvents: 'none', zIndex: 'calc(var(--ek-z) + 1)' }}
    >
      {kind === 'element' ? (
        <slot name="orb" />
      ) : kind === 'images' && props.images ? (
        <OrbImage images={props.images} state={props.state ?? 'idle'} />
      ) : (
        <>
          <canvas ref={ring} class="ringfx" />
          <div ref={core} class="orbcore">
            <canvas ref={cv} class="orbgl" style={{ background: bg }} />
            <span class="orbr" />
          </div>
        </>
      )}
    </div>
  )
}
