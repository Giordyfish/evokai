/**
 * Optional sounds when the microphone opens and closes. Built-in recipes are synthesised with Web Audio (no files);
 * `{on, off}` plays your own files.
 *
 * Identifiers (display names in the locale packs):
 * - `goccia` (Drop): sine 988 Hz 0–0.14 s + 1319 Hz 0.08–0.22 s, reversed to close; gain 0.18.
 * - `classico` (Classic): sine 587 Hz + 880 Hz 56 ms later, each falling 30 dB in 0.12 s then fading over 0.8 s;
 *   to close, 784 Hz then 523 Hz 100 ms later, about 80 ms each; gain 0.2.
 * - `vetro` (Glass): an FM bell (modulator × 3.5, index 2.2 decaying), 1568 Hz 0.6 s / 1046 Hz 0.5 s; gain 0.12.
 * - `respiro` (Breath): band-pass (Q 6) noise, 0.35 s, sweeping 500 → 2600 Hz (reversed to close); gain 0.5.
 * - `cristallo` (Crystal): triangle arpeggio 880/1109/1319 Hz, 45 ms apart, 0.32 s, up / down; gain 0.11.
 * - `clic` (Soft click): sine glide 600 → 1200 Hz (reversed to close), 0.09 s; gain 0.22.
 * Notes have an 8 ms attack (bell 4 ms) and an exponential decay.
 */

export type MicSoundName = 'goccia' | 'classico' | 'vetro' | 'respiro' | 'cristallo' | 'clic'
/** A built-in sound, `none`, or your own file URLs. */
export type MicSound = MicSoundName | 'none' | { on: string; off: string }

export const MIC_SOUNDS: readonly MicSoundName[] = ['goccia', 'classico', 'vetro', 'respiro', 'cristallo', 'clic']
export const DEFAULT_MIC_SOUND: MicSound = 'goccia'

/** The parts of a `BaseAudioContext` the recipes use (an `OfflineAudioContext` works). */
export type AudioContextLike = Pick<
  BaseAudioContext,
  'currentTime' | 'sampleRate' | 'destination' | 'createGain' | 'createOscillator' | 'createBuffer' | 'createBufferSource' | 'createBiquadFilter' | 'decodeAudioData'
> & { state?: AudioContextState; resume?: () => Promise<void> }

export interface MicSoundOptions {
  /** The audio context to play in (default: a shared one, resumed on demand). */
  context?: AudioContextLike
  /** Overall volume multiplier (default 1). */
  volume?: number
  /** Fetch for file sounds (default the global one). */
  fetch?: (input: string) => Promise<Response>
}

let shared: AudioContext | null = null
/** The page's one AudioContext (mic sounds and Piper play through it), created on first use. */
export function sharedContext(): AudioContext | null {
  if (shared) return shared
  const AC = typeof window !== 'undefined' ? (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) : undefined
  shared = AC ? new AC() : null
  return shared
}

function out(ctx: AudioContextLike, gain: number): GainNode {
  const g = ctx.createGain()
  g.gain.value = gain
  g.connect(ctx.destination)
  return g
}

interface NoteSpec {
  f: number
  f2?: number
  t?: number
  dur?: number
  type?: OscillatorType
  vol?: number
  attack?: number
}

/** A soft note with attack, exponential decay and an optional pitch glide. */
function note(ctx: AudioContextLike, dest: AudioNode, { f, f2 = f, t = 0, dur = 0.18, type = 'sine', vol = 1, attack = 0.008 }: NoteSpec): void {
  const now = ctx.currentTime
  const o = ctx.createOscillator()
  const g = ctx.createGain()
  o.type = type
  o.frequency.setValueAtTime(f, now + t)
  if (f2 !== f) o.frequency.exponentialRampToValueAtTime(f2, now + t + dur * 0.7)
  g.gain.setValueAtTime(0, now + t)
  g.gain.linearRampToValueAtTime(vol, now + t + attack)
  g.gain.exponentialRampToValueAtTime(0.0008, now + t + dur)
  o.connect(g)
  g.connect(dest)
  o.start(now + t)
  o.stop(now + t + dur + 0.05)
}

/** A sine chime: a fast fall (30 dB in `knee` seconds) and then a long, quiet tail. */
function chime(ctx: AudioContextLike, dest: AudioNode, { f, t = 0, vol = 1, knee = 0.12, dur = 0.8 }: { f: number; t?: number; vol?: number; knee?: number; dur?: number }): void {
  const now = ctx.currentTime
  const o = ctx.createOscillator()
  const g = ctx.createGain()
  o.type = 'sine'
  o.frequency.setValueAtTime(f, now + t)
  g.gain.setValueAtTime(0, now + t)
  g.gain.linearRampToValueAtTime(vol, now + t + 0.003)
  g.gain.exponentialRampToValueAtTime(vol * 0.03, now + t + 0.003 + knee)
  g.gain.exponentialRampToValueAtTime(0.0003, now + t + dur)
  o.connect(g)
  g.connect(dest)
  o.start(now + t)
  o.stop(now + t + dur + 0.05)
}

/** A tiny FM bell: a carrier with a decaying modulator. */
function bell(ctx: AudioContextLike, dest: AudioNode, { f, t = 0, dur = 0.5, vol = 1, ratio = 3.5, index = 2.2 }: { f: number; t?: number; dur?: number; vol?: number; ratio?: number; index?: number }): void {
  const now = ctx.currentTime
  const c = ctx.createOscillator()
  const m = ctx.createOscillator()
  const mg = ctx.createGain()
  const g = ctx.createGain()
  c.frequency.value = f
  m.frequency.value = f * ratio
  mg.gain.setValueAtTime(f * index, now + t)
  mg.gain.exponentialRampToValueAtTime(1, now + t + dur)
  m.connect(mg)
  mg.connect(c.frequency)
  g.gain.setValueAtTime(0, now + t)
  g.gain.linearRampToValueAtTime(vol, now + t + 0.004)
  g.gain.exponentialRampToValueAtTime(0.0008, now + t + dur)
  c.connect(g)
  g.connect(dest)
  c.start(now + t)
  m.start(now + t)
  c.stop(now + t + dur + 0.05)
  m.stop(now + t + dur + 0.05)
}

/** Band-pass filtered noise sweeping up (open) or down (close). */
function breath(ctx: AudioContextLike, dest: AudioNode, { up = true, dur = 0.35, vol = 0.6 }: { up?: boolean; dur?: number; vol?: number }): void {
  const now = ctx.currentTime
  const len = Math.floor(ctx.sampleRate * dur)
  const b = ctx.createBuffer(1, len, ctx.sampleRate)
  const d = b.getChannelData(0)
  for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1
  const s = ctx.createBufferSource()
  const f = ctx.createBiquadFilter()
  const g = ctx.createGain()
  s.buffer = b
  f.type = 'bandpass'
  f.Q.value = 6
  f.frequency.setValueAtTime(up ? 500 : 2600, now)
  f.frequency.exponentialRampToValueAtTime(up ? 2600 : 500, now + dur)
  g.gain.setValueAtTime(0, now)
  g.gain.linearRampToValueAtTime(vol, now + dur * 0.35)
  g.gain.exponentialRampToValueAtTime(0.0008, now + dur)
  s.connect(f)
  f.connect(g)
  g.connect(dest)
  s.start()
  s.stop(now + dur + 0.05)
}

type Recipe = (ctx: AudioContextLike, volume: number) => void

/** The synthesised sounds, as in the reference page: exact frequencies, timings and gains. */
export const MIC_SOUND_RECIPES: Record<MicSoundName, { on: Recipe; off: Recipe }> = {
  goccia: {
    on: (c, v) => {
      const o = out(c, 0.18 * v)
      note(c, o, { f: 988, t: 0, dur: 0.14 })
      note(c, o, { f: 1319, t: 0.08, dur: 0.22 })
    },
    off: (c, v) => {
      const o = out(c, 0.18 * v)
      note(c, o, { f: 1319, t: 0, dur: 0.12 })
      note(c, o, { f: 988, t: 0.07, dur: 0.22 })
    },
  },
  classico: {
    on: (c, v) => {
      const o = out(c, 0.2 * v)
      chime(c, o, { f: 587.33, vol: 0.72 })
      chime(c, o, { f: 880, t: 0.056 })
    },
    off: (c, v) => {
      const o = out(c, 0.2 * v)
      note(c, o, { f: 783.99, dur: 0.075, attack: 0.002 })
      note(c, o, { f: 523.25, t: 0.1, dur: 0.08, attack: 0.002 })
    },
  },
  vetro: {
    on: (c, v) => bell(c, out(c, 0.12 * v), { f: 1568, dur: 0.6 }),
    off: (c, v) => bell(c, out(c, 0.12 * v), { f: 1046, dur: 0.5 }),
  },
  respiro: {
    on: (c, v) => breath(c, out(c, 0.5 * v), { up: true }),
    off: (c, v) => breath(c, out(c, 0.5 * v), { up: false }),
  },
  cristallo: {
    on: (c, v) => {
      const o = out(c, 0.11 * v)
      ;[880, 1109, 1319].forEach((f, i) => note(c, o, { f, t: i * 0.045, dur: 0.32, type: 'triangle' }))
    },
    off: (c, v) => {
      const o = out(c, 0.11 * v)
      ;[1319, 1109, 880].forEach((f, i) => note(c, o, { f, t: i * 0.045, dur: 0.3, type: 'triangle' }))
    },
  },
  clic: {
    on: (c, v) => note(c, out(c, 0.22 * v), { f: 600, f2: 1200, dur: 0.09 }),
    off: (c, v) => note(c, out(c, 0.22 * v), { f: 1200, f2: 600, dur: 0.09 }),
  },
}

const buffers = new Map<string, Promise<AudioBuffer>>()

async function playFile(ctx: AudioContextLike, url: string, volume: number, fetchFn: (u: string) => Promise<Response>): Promise<void> {
  let p = buffers.get(url)
  if (!p) {
    p = fetchFn(url).then(async (r) => ctx.decodeAudioData(await r.arrayBuffer()))
    buffers.set(url, p)
    p.catch(() => buffers.delete(url))
  }
  const buf = await p
  const s = ctx.createBufferSource()
  s.buffer = buf
  s.connect(out(ctx, 0.9 * volume))
  s.start()
}

/** Play the open (`on`) or close (`off`) sound. Resolves once it is scheduled. */
export async function playMicSound(sound: MicSound, which: 'on' | 'off', opts: MicSoundOptions = {}): Promise<void> {
  if (sound === 'none') return
  const ctx = opts.context ?? sharedContext()
  if (!ctx) return
  if (ctx.state === 'suspended') await ctx.resume?.()
  const volume = opts.volume ?? 1
  const fetchFn = opts.fetch ?? ((u: string) => fetch(u))
  if (typeof sound === 'object') return playFile(ctx, sound[which], volume, fetchFn)
  MIC_SOUND_RECIPES[sound][which](ctx, volume)
}
