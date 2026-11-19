/**
 * The panel's four views and how its one orb travels between them, with no drawing: a sequencer that turns a change of
 * view into an ordered series of looks (what is on screen) and flights.
 *
 *   closed     the launcher orb, bottom right
 *   chat       the panel open on the conversation, the orb in the header
 *   voice      the panel open on voice mode, the orb in the middle
 *   minimized  voice goes on with the panel closed: the launcher orb speaks, with a bubble beside it
 *
 * There is only ever one orb on screen: it rests in one of three places (`launcher`, `head`, `center`) or flies.
 * Opening moves the orb first and the panel's box appears around it once it has landed; closing shuts the box first
 * and the orb travels after. Switching between chat and voice inside an open panel flies the orb while the two layers
 * crossfade. Without motion (reduced motion, or no animation support) every change is instant.
 */

export type View = 'closed' | 'chat' | 'voice' | 'minimized'
export type Rest = 'launcher' | 'head' | 'center'
/** The panel's box: not there, laid out but invisible (to measure it), opening, open, closing. */
export type Box = 'none' | 'pre' | 'in' | 'open' | 'out'

export interface Look {
  /** The view this look belongs to (the one being left, until a step completes). */
  view: View
  box: Box
  /** The layer inside the box. */
  layer: 'chat' | 'voice'
  /** Where the orb is, or that it is between two places. */
  orb: Rest | 'flying'
  /** The speech bubble beside the launcher (minimized). */
  bubble: boolean
}

/** A place on screen: the centre and the diameter, in viewport pixels. */
export interface Spot {
  x: number
  y: number
  size: number
}

export const restOf = (v: View): Rest => (v === 'chat' ? 'head' : v === 'voice' ? 'center' : 'launcher')
export const boxed = (v: View): boolean => v === 'chat' || v === 'voice'
const layerOf = (v: View): Look['layer'] => (v === 'voice' ? 'voice' : 'chat')

/** The look of a view at rest. */
export function settled(v: View): Look {
  return { view: v, box: boxed(v) ? 'open' : 'none', layer: layerOf(v), orb: restOf(v), bubble: v === 'minimized' }
}

/** The view the panel should show. `own` is true when the voice running was started or shown in this panel. */
export function viewOf(open: boolean, voiceOn: boolean, own: boolean): View {
  if (open) return voiceOn ? 'voice' : 'chat'
  return voiceOn && own ? 'minimized' : 'closed'
}

/** How long each flight lasts (ms). */
export function flightMs(from: View, to: View): number {
  if (from === 'closed' && to === 'chat') return 700
  if (from === 'voice' && to === 'minimized') return 820
  if (boxed(from) && boxed(to)) return 720
  return 760
}

export const BOX_IN_MS = 320
export const BOX_OUT_MS = 240

/** What the sequencer needs from the page. */
export interface FlowIO {
  /** Show a look; the returned promise resolves once it is laid out (so its places can be measured). */
  show(look: Look): Promise<void> | void
  /** Where a resting place is now (null when it is not on screen). */
  spot(rest: Rest): Spot | null
  fly(from: Spot, to: Spot, ms: number): Promise<void>
  wait(ms: number): Promise<void>
  /** False for instant changes. */
  motion(): boolean
}

export class Flow {
  private look_: Look
  private target: View
  private running = false
  private idle: Array<() => void> = []

  constructor(
    view: View,
    private readonly io: FlowIO,
  ) {
    this.look_ = settled(view)
    this.target = view
  }

  get look(): Look {
    return this.look_
  }

  /** A transition is under way. */
  get moving(): boolean {
    return this.running
  }

  /** Go to a view; a change of mind during a step is taken up when the step ends. */
  go(view: View): void {
    this.target = view
    if (!this.running) void this.run()
  }

  /** Resolves when the look has settled on the last view asked for. */
  settledOn(): Promise<void> {
    return this.running ? new Promise((r) => this.idle.push(r)) : Promise.resolve()
  }

  private async set(p: Partial<Look>): Promise<void> {
    this.look_ = { ...this.look_, ...p }
    await this.io.show(this.look_)
  }

  private async run(): Promise<void> {
    this.running = true
    try {
      while (this.look_.view !== this.target) await this.step(this.look_.view, this.target)
    } finally {
      this.running = false
      for (const r of this.idle.splice(0)) r()
    }
  }

  private async step(from: View, to: View): Promise<void> {
    const io = this.io
    if (!io.motion() || restOf(from) === restOf(to)) {
      // Instant, or the orb stays where it is (closed and minimized both rest in the launcher).
      await this.set(settled(to))
      return
    }
    const ms = flightMs(from, to)
    const a = io.spot(restOf(from))
    if (boxed(from) && boxed(to)) {
      // Inside the open panel: the layers crossfade while the orb flies.
      await this.set({ layer: layerOf(to), orb: 'flying' })
      const b = io.spot(restOf(to))
      if (a && b) await io.fly(a, b, ms)
    } else if (boxed(to)) {
      // Opening: the box is laid out unseen, the orb flies to its place, then the box opens around it.
      await this.set({ box: 'pre', layer: layerOf(to), orb: 'flying', bubble: false })
      const b = io.spot(restOf(to))
      if (a && b) await io.fly(a, b, ms)
      await this.set({ box: 'in', orb: restOf(to) })
      await io.wait(BOX_IN_MS)
    } else {
      // Closing: the box shuts around the orb, then the orb flies down to the launcher.
      await this.set({ box: 'out' })
      await io.wait(BOX_OUT_MS)
      await this.set({ box: 'none', orb: 'flying' })
      const b = io.spot(restOf(to))
      if (a && b) await io.fly(a, b, ms)
    }
    await this.set(settled(to))
  }
}

/** A CSS cubic-bezier easing as a function of time (0 to 1). */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (t: number) => number {
  const cx = 3 * x1
  const bx = 3 * (x2 - x1) - cx
  const ax = 1 - cx - bx
  const cy = 3 * y1
  const by = 3 * (y2 - y1) - cy
  const ay = 1 - cy - by
  const x = (s: number) => ((ax * s + bx) * s + cx) * s
  const y = (s: number) => ((ay * s + by) * s + cy) * s
  const dx = (s: number) => (3 * ax * s + 2 * bx) * s + cx
  return (t) => {
    if (t <= 0) return 0
    if (t >= 1) return 1
    let s = t
    for (let i = 0; i < 6; i++) {
      const d = dx(s)
      if (Math.abs(d) < 1e-6) break
      s -= (x(s) - t) / d
    }
    // Bisection when Newton strays.
    if (s < 0 || s > 1 || Math.abs(x(s) - t) > 1e-4) {
      let lo = 0
      let hi = 1
      s = t
      for (let i = 0; i < 30; i++) {
        if (x(s) < t) lo = s
        else hi = s
        s = (lo + hi) / 2
      }
    }
    return y(s)
  }
}

const EASE = cubicBezier(0.45, 0.05, 0.25, 1)

/**
 * Where the orb is at time `t` (0 to 1) of a flight from `a` to `b`: a slight arc (lifted halfway), a small overshoot
 * before landing, eased as a whole.
 */
export function flightAt(a: Spot, b: Spot, t: number): Spot {
  const p = EASE(Math.min(1, Math.max(0, t)))
  const dx = a.x - b.x
  const dy = a.y - b.y
  const s = a.size / b.size
  const lift = Math.min(90, Math.hypot(dx, dy) * 0.18) * (dy > 0 ? -1 : 1)
  // Keyframes (offset, dx, dy, scale), as offsets from where it lands.
  const keys: Array<[number, number, number, number]> = [
    [0, dx, dy, s],
    [0.5, dx * 0.42 + lift * 0.6, dy * 0.42 + lift, s + (1 - s) * 0.7],
    [0.86, 0, 0, 1.06],
    [1, 0, 0, 1],
  ]
  let i = 0
  while (i < keys.length - 2 && p > (keys[i + 1] as [number, number, number, number])[0]) i++
  const k0 = keys[i] as [number, number, number, number]
  const k1 = keys[i + 1] as [number, number, number, number]
  const f = (p - k0[0]) / (k1[0] - k0[0])
  const mix = (u: number, v: number) => u + (v - u) * f
  return { x: b.x + mix(k0[1], k1[1]), y: b.y + mix(k0[2], k1[2]), size: b.size * mix(k0[3], k1[3]) }
}
