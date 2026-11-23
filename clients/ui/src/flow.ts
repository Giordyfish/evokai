/**
 * The panel's four views and how its one orb travels between them, with no drawing: a sequencer that turns a change of
 * view into an ordered series of looks (what is on screen) and flights.
 *
 *   closed     the launcher orb, bottom right
 *   chat       the panel open on the conversation, the orb in the header
 *   voice      the panel open on voice mode, the orb in the middle
 *   minimized  voice goes on with the panel closed: the launcher orb speaks, with a bubble beside it
 *
 * There is only ever one orb on screen: it rests in one of three places (`launcher`, `head`, `center`) or is between
 * two (`flying`). It never travels across the page: it fades out where it was and fades in where it goes (a hop).
 * Opening fades the launcher orb out while the panel's box rises into place, and the orb appears in its new place;
 * closing lowers the box while the orb fades out, and it appears again in the launcher. Switching between chat and
 * voice inside an open panel hops the orb while the two layers crossfade. Without motion (reduced motion, or no
 * animation support) every change is instant.
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

/** How long a hop lasts (ms): the orb fades out where it was, then fades in where it goes. */
export const HOP_MS = 460
/** The box rises into place (opening) or sinks away (closing). */
export const BOX_IN_MS = 320
export const BOX_OUT_MS = 240
/** Opening: the box starts rising this long after the orb started fading out of the launcher. */
const BOX_AFTER_MS = 120

/** What the sequencer needs from the page. */
export interface FlowIO {
  /** Show a look; the returned promise resolves once it is laid out (so its places can be measured). */
  show(look: Look): Promise<void> | void
  /** Where a resting place is now (null when it is not on screen). */
  spot(rest: Rest): Spot | null
  /** Move the orb between two places (a hop: out at one, in at the other). */
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
    const a = io.spot(restOf(from))
    const hop = async (to_: Rest) => {
      const b = io.spot(to_)
      if (a && b) await io.fly(a, b, HOP_MS)
    }
    if (boxed(from) && boxed(to)) {
      // Inside the open panel: the layers crossfade while the orb hops.
      await this.set({ layer: layerOf(to), orb: 'flying' })
      await hop(restOf(to))
    } else if (boxed(to)) {
      // Opening: the box is laid out unseen (to measure where the orb goes), the launcher orb fades out, the box rises
      // into place and the orb fades in on it.
      await this.set({ box: 'pre', layer: layerOf(to), orb: 'flying', bubble: false })
      const moved = hop(restOf(to))
      await io.wait(BOX_AFTER_MS)
      await this.set({ box: 'in' })
      await Promise.all([moved, io.wait(BOX_IN_MS)])
    } else {
      // Closing: the box sinks away while the orb fades out, and the orb fades in again in the launcher.
      await this.set({ box: 'out', orb: 'flying' })
      await Promise.all([hop(restOf(to)), io.wait(BOX_OUT_MS)])
    }
    await this.set(settled(to))
  }
}

const easeIn = (t: number) => t * t
const easeOut = (t: number) => 1 - (1 - t) * (1 - t)
/** The hop's parts, as fractions of it: fading out until `OUT`, unseen until `IN`, then fading in. */
const OUT = 0.39
const IN = 0.48

/** Where the orb is at time `t` (0 to 1) of a hop from `a` to `b`, how large (a factor of the place) and how opaque. */
export function hopAt(a: Spot, b: Spot, t: number): { spot: Spot; scale: number; opacity: number } {
  const u = Math.min(1, Math.max(0, t))
  if (u < OUT) {
    const k = easeIn(u / OUT)
    return { spot: a, scale: 1 - 0.4 * k, opacity: 1 - k }
  }
  if (u < IN) return { spot: b, scale: 0.6, opacity: 0 }
  const k = easeOut((u - IN) / (1 - IN))
  return { spot: b, scale: 0.6 + 0.4 * k, opacity: k }
}
