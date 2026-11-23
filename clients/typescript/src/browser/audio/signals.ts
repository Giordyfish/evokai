/**
 * One audio measurement for every source (the user's microphone, the agent's voice from Piper or the server) on the
 * same scale, so any visual (an orb, a waveform, a meter) can consume it. When the browser speaks with `speechSynthesis` there
 * is no audio to read: the signal is estimated from the text (syllables), and marked `estimated`.
 */

/** Number of spectrum bins in a signal. */
export const SPECTRUM_BINS = 48
/** RMS is multiplied by this and clamped to 1, for every source. */
export const RMS_GAIN = 3.2
/** A syllable onset: RMS rose by more than this since the previous frame. */
export const ONSET_JUMP = 0.04

export type AudioSource = 'user' | 'agent'

export interface AudioSignal {
  source: AudioSource
  /** Loudness: RMS × 3.2, clamped to 0–1 (same scale for every source). */
  rms: number
  /** A display level 0.1–0.9 derived from `rms` (0.1 + rms × 0.85, capped). */
  level: number
  /** A syllable starts in this frame (RMS jump > 0.04, or an estimated syllable). */
  onset: boolean
  /** Strength of the syllable starting now (0–1): stressed syllables are stronger; 0 when `onset` is false. */
  intensity: number
  /** Spectrum, `SPECTRUM_BINS` values 0–1; null when estimated from text. */
  spectrum: Float32Array | null
  /** True when estimated from text (browser `speechSynthesis`), false when measured from audio. */
  estimated: boolean
  /** `performance.now()` of the measurement. */
  time: number
}

/** The part of an `AnalyserNode` `measure` reads (structural, for tests). */
export interface AnalyserLike {
  fftSize: number
  getFloatTimeDomainData(array: Float32Array): void
  getByteFrequencyData(array: Uint8Array): void
}

export interface Measurement {
  rms: number
  level: number
  onset: boolean
  spectrum: Float32Array
}

/**
 * Measure one frame of an analyser: RMS × 3.2 (clamped), the display level, a syllable onset against the previous
 * frame's RMS, and `SPECTRUM_BINS` spectrum bins (from bin 2 up, 0–1).
 */
export function measure(an: AnalyserLike, prevRms: number, buffers?: { td?: Float32Array; fd?: Uint8Array }): Measurement {
  const td = buffers?.td && buffers.td.length >= an.fftSize ? buffers.td : new Float32Array(an.fftSize)
  const fd = buffers?.fd && buffers.fd.length >= an.fftSize / 2 ? buffers.fd : new Uint8Array(an.fftSize / 2)
  an.getFloatTimeDomainData(td)
  let sum = 0
  for (let i = 0; i < an.fftSize; i++) sum += (td[i] as number) ** 2
  const rms = Math.min(1, Math.sqrt(sum / an.fftSize) * RMS_GAIN)
  an.getByteFrequencyData(fd)
  const spectrum = new Float32Array(SPECTRUM_BINS)
  for (let i = 0; i < SPECTRUM_BINS; i++) spectrum[i] = (fd[2 + i] ?? 0) / 255
  return { rms, level: Math.min(0.9, 0.1 + rms * 0.85), onset: rms - prevRms > ONSET_JUMP, spectrum }
}

export type SignalListener = (signal: AudioSignal) => void

/**
 * The live signals of a voice session: the latest per source, and a subscription that fires on every frame of an
 * active source (about 60 per second while something sounds). Visuals should read in their own animation frame.
 */
export class AudioSignals {
  private readonly latest = new Map<AudioSource, AudioSignal>()
  private readonly listeners = new Set<SignalListener>()

  /** The latest signal of a source (or of whichever sounded last), or null. */
  current(source?: AudioSource): AudioSignal | null {
    if (source) return this.latest.get(source) ?? null
    let best: AudioSignal | null = null
    for (const s of this.latest.values()) if (!best || s.time > best.time) best = s
    return best
  }

  subscribe(fn: SignalListener): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  /** Publish a frame (used by the engines; an application feeding its own audio may call it too). */
  push(signal: AudioSignal): void {
    this.latest.set(signal.source, signal)
    for (const fn of [...this.listeners]) fn(signal)
  }

  /** A source fell silent: publish a zero frame so visuals settle. */
  silence(source: AudioSource): void {
    this.push({ source, rms: 0, level: 0, onset: false, intensity: 0, spectrum: null, estimated: false, time: now() })
  }
}

export const now = (): number => (typeof performance !== 'undefined' ? performance.now() : Date.now())

/**
 * Read an analyser every animation frame and publish signals until stopped. Returns the stop function.
 */
export function meterAnalyser(an: AnalyserLike, source: AudioSource, signals: AudioSignals): () => void {
  let prev = 0
  let alive = true
  const td = new Float32Array(an.fftSize)
  const fd = new Uint8Array(an.fftSize / 2)
  const raf: (cb: () => void) => unknown =
    typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb) => setTimeout(cb, 16)
  const tick = () => {
    if (!alive) return
    const m = measure(an, prev, { td, fd })
    signals.push({
      source,
      rms: m.rms,
      level: m.level,
      onset: m.onset,
      intensity: m.onset ? Math.min(1, 0.25 + m.rms * 1.1) : 0,
      spectrum: m.spectrum,
      estimated: false,
      time: now(),
    })
    prev = m.rms
    raf(tick)
  }
  raf(tick)
  return () => {
    alive = false
    signals.silence(source)
  }
}
