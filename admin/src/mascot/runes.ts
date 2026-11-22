/**
 * The mascot's listening style: at each syllable the person says a rune appears beside the face and goes to orbit the
 * hat; on a pause the runes fly into the hat tip with a flash ("got it"). Positions are in the body image's space
 * (1024 px, `public/mascot/meta.json`).
 */

/** The ring the runes orbit, just above the brim, and the curled tip they fly into. */
export const RING = { x: 559.6, y: 269.2, rx: 250, ry: 42 }
export const HAT_TIP: [number, number] = [759.6, 270.8]
/** Where a new rune starts: beside the face. */
export const RUNE_START: [number, number] = [324, 500]
export const MAX_RUNES = 14

/** Invented runes: a stave with one or two branches (segments in -1..1). */
const BRANCHES: number[][] = [
  [0, -1, 0.6, -0.45],
  [0, -1, -0.6, -0.45],
  [0, 0, 0.6, 0.55],
  [0, 0, -0.6, 0.55],
  [0, -0.3, 0.6, -0.9],
  [0, 0.25, -0.6, -0.35],
  [0, -1, 0.55, -0.5, 0, 0],
  [0, 0.1, 0.55, 0.55, 0, 1],
  [-0.5, -0.2, 0.5, 0.4],
  [-0.5, 0.4, 0.5, -0.2],
]

export interface Rune {
  strokes: number[][]
  ang: number
  life: number
  /** Seconds since it started flying into the hat; -1 while it orbits. */
  suck: number
  x: number
  y: number
}

export function newRune(random: () => number = Math.random): Rune {
  const a = BRANCHES[Math.floor(random() * BRANCHES.length)]!
  const b = BRANCHES[Math.floor(random() * BRANCHES.length)]!
  const strokes = [[0, -1, 0, 1], a]
  if (random() < 0.55 && b !== a) strokes.push(b)
  return { strokes, ang: Math.PI * 0.85 + (random() - 0.5) * 0.5, life: 0, suck: -1, x: RUNE_START[0], y: RUNE_START[1] }
}

/** A point of the ring: x, y and depth (1 in front, -1 behind). */
export function orbit(ang: number): [number, number, number] {
  return [RING.x + Math.cos(ang) * RING.rx, RING.y + Math.sin(ang) * RING.ry, Math.sin(ang)]
}

/**
 * Syllables and pauses from the microphone's level (0..1), one reading per frame: a syllable is a rise above the voice
 * threshold, a pause is 0.35 s below it after speech.
 */
export class Listener {
  private prev = 0
  private lastSyllable = -9
  private voicedUntil = -9
  private paused = true

  constructor(private readonly on: { onSyllable: () => void; onPause: () => void }) {}

  step(level: number, now: number): void {
    if (level > 0.32) this.voicedUntil = now
    if (level > 0.3 && level - this.prev > 0.025 && now - this.lastSyllable > 0.14) {
      this.lastSyllable = now
      this.on.onSyllable()
    }
    if (now - this.voicedUntil > 0.35 && !this.paused) {
      this.paused = true
      this.on.onPause()
    }
    if (now - this.voicedUntil < 0.05) this.paused = false
    this.prev = level
  }
}

/** Draw one rune: a glowing violet stroke with a pale core. */
export function drawRune(ctx: CanvasRenderingContext2D, r: Rune, x: number, y: number, size: number, alpha: number): void {
  ctx.save()
  ctx.translate(x, y)
  ctx.globalAlpha = alpha
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  const path = () => {
    ctx.beginPath()
    for (const st of r.strokes) {
      ctx.moveTo(st[0]! * size * 0.5, st[1]! * size * 0.5)
      for (let i = 2; i < st.length; i += 2) ctx.lineTo(st[i]! * size * 0.5, st[i + 1]! * size * 0.5)
    }
  }
  ctx.shadowColor = '#c77dff'
  ctx.shadowBlur = 18
  ctx.strokeStyle = '#c77dff'
  ctx.lineWidth = size * 0.2
  path()
  ctx.stroke()
  ctx.shadowBlur = 0
  ctx.strokeStyle = '#fbf3ff'
  ctx.lineWidth = size * 0.09
  path()
  ctx.stroke()
  ctx.restore()
}

/**
 * Move and draw the runes for one frame; returns the flash left at the hat tip (0..1). Runes still orbiting when the
 * listening ends fly into the hat.
 */
export function stepRunes(ctx: CanvasRenderingContext2D, runes: Rune[], dt: number, listening: boolean, flash: number, scale = 1): number {
  const [px, py] = HAT_TIP
  for (let i = runes.length - 1; i >= 0; i--) {
    const r = runes[i]!
    r.life += dt
    let x: number
    let y: number
    let depth: number
    let size = 40 * scale
    let a = Math.min(1, r.life / 0.25)
    if (r.suck >= 0) {
      r.suck += dt
      const u = Math.min(1, r.suck / 0.5)
      const e = u * u * (3 - 2 * u)
      const [ox, oy, d0] = orbit(r.ang)
      x = ox + (px - ox) * e
      y = oy + (py - oy) * e - Math.sin(Math.PI * u) * 40
      depth = d0 * (1 - e) + e
      size = 40 * scale * (1 - 0.75 * e)
      a *= 1 - 0.2 * e
      if (u >= 1) {
        runes.splice(i, 1)
        flash = 1
        continue
      }
    } else {
      r.ang += dt * 1.1
      const [ox, oy, d] = orbit(r.ang)
      const k = Math.min(1, r.life / 0.55)
      const e = k * k * (3 - 2 * k)
      x = r.x + (ox - r.x) * e
      y = r.y + (oy - r.y) * e
      depth = d * e + (1 - e)
      if (!listening) r.suck = 0
    }
    const front = 0.5 + 0.5 * depth
    drawRune(ctx, r, x, y, size * (0.7 + 0.3 * front), a * (0.35 + 0.65 * front))
  }
  if (flash > 0.01) {
    const g = ctx.createRadialGradient(px, py, 0, px, py, 110)
    g.addColorStop(0, `rgba(251,243,255,${0.95 * flash})`)
    g.addColorStop(0.4, `rgba(199,125,255,${0.5 * flash})`)
    g.addColorStop(1, 'rgba(199,125,255,0)')
    ctx.globalAlpha = 1
    ctx.fillStyle = g
    ctx.beginPath()
    ctx.arc(px, py, 110, 0, Math.PI * 2)
    ctx.fill()
    flash *= Math.exp(-dt / 0.22)
  }
  return flash
}
