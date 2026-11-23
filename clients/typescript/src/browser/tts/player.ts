/**
 * Plays server speech (`session.speechEvents`) with the Web Audio API and emits its timings in step with what is
 * heard: `word`, `mark` and `viseme` fire when the audio reaches them, whatever order the stream delivered them in.
 * PCM only (ask for `format: 'pcm'`, the default).
 */
import type { MarkTiming, SpeechStart, SpeechStreamEvent, VisemeTiming, WordTiming } from '../speech-server'
import { Emitter } from '../voice/types'

/** The parts of `AudioContext` the player uses (a fake in tests). */
export interface PlaybackContext {
  readonly currentTime: number
  readonly destination: unknown
  createBuffer(channels: number, length: number, sampleRate: number): { getChannelData(channel: number): Float32Array }
  createBufferSource(): {
    buffer: unknown
    connect(node: unknown): unknown
    start(when?: number): void
    stop(when?: number): void
    /** Called when the source has played (the player ends with the last one). */
    onended?: (() => void) | null
  }
  resume?(): Promise<void>
}

export interface PlayerEvents {
  start: SpeechStart
  word: WordTiming
  mark: MarkTiming
  viseme: VisemeTiming
  end: { cancelled: boolean }
}

export interface TimedSpeechPlayerOptions {
  /** The audio context (default: a new `AudioContext`). */
  context?: PlaybackContext
  /** How far ahead of now the first sample is scheduled (ms, default 60). */
  leadMs?: number
}

export class TimedSpeechPlayer {
  private readonly events = new Emitter<PlayerEvents>()
  private ctx: PlaybackContext | null
  private readonly leadMs: number
  private sources: Array<{ stop(when?: number): void }> = []
  private timers: Array<ReturnType<typeof setTimeout>> = []
  private finish: ((cancelled: boolean) => void) | null = null

  constructor(opts: TimedSpeechPlayerOptions = {}) {
    this.ctx = opts.context ?? null
    this.leadMs = opts.leadMs ?? 60
  }

  on<K extends keyof PlayerEvents>(event: K, fn: (value: PlayerEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  private context(): PlaybackContext {
    if (!this.ctx) this.ctx = new AudioContext() as unknown as PlaybackContext
    return this.ctx
  }

  private at(t0: number, ms: number, fn: () => void): void {
    const ctx = this.context()
    const wait = Math.max(0, (t0 + ms / 1000 - ctx.currentTime) * 1000)
    this.timers.push(setTimeout(fn, wait))
  }

  /** Play a speech stream; resolves when the audio ends (or `stop` cuts it). */
  async play(stream: AsyncIterable<SpeechStreamEvent>): Promise<{ cancelled: boolean }> {
    this.stop()
    const ctx = this.context()
    await ctx.resume?.()
    // The clock starts with the first audio (an engine takes a while for its first byte); a chunk
    // that arrives after it was due plays at once and shifts everything after it by the same delay.
    let t0: number | null = null
    let shift = 0
    let rate = 24000
    let samples = 0
    let last: { onended?: (() => void) | null } | null = null
    const waiting: Array<() => void> = []
    const at = (ms: number, fn: () => void) => {
      if (t0 === null) waiting.push(() => this.at((t0 ?? 0) + shift, ms, fn))
      else this.at(t0 + shift, ms, fn)
    }
    const ended = new Promise<boolean>((resolve) => (this.finish = resolve))
    let done = false
    try {
      for await (const e of stream) {
        if (!this.finish) break
        if (e.type === 'start') {
          if (e.format !== 'pcm') throw new Error('TimedSpeechPlayer plays PCM: ask for format "pcm"')
          rate = e.sampleRate
          const { type: _t, ...start } = e
          at(0, () => this.events.emit('start', start))
        } else if (e.type === 'audio') {
          const pcm = new Int16Array(e.data.buffer, e.data.byteOffset, Math.floor(e.data.byteLength / 2))
          if (!pcm.length) continue
          if (t0 === null) {
            t0 = ctx.currentTime + this.leadMs / 1000
            for (const fn of waiting.splice(0)) fn()
          }
          let when = t0 + shift + samples / rate
          if (when < ctx.currentTime) {
            shift += ctx.currentTime - when
            when = ctx.currentTime
          }
          const buffer = ctx.createBuffer(1, pcm.length, rate)
          const channel = buffer.getChannelData(0)
          for (let i = 0; i < pcm.length; i++) channel[i] = (pcm[i] ?? 0) / 32768
          const source = ctx.createBufferSource()
          source.buffer = buffer
          source.connect(ctx.destination)
          source.start(when)
          this.sources.push(source)
          last = source
          samples += pcm.length
        } else if (e.type === 'word') {
          const { type: _t, ...w } = e
          at(w.start, () => this.events.emit('word', w))
        } else if (e.type === 'mark') {
          const { type: _t, ...m } = e
          at(m.time, () => this.events.emit('mark', m))
        } else if (e.type === 'viseme') {
          const { type: _t, ...v } = e
          at(v.time, () => this.events.emit('viseme', v))
        } else if (e.type === 'done') {
          done = true
          if (t0 === null) {
            // Nothing to play (a segment of cues alone): its timings fire now.
            t0 = ctx.currentTime
            for (const fn of waiting.splice(0)) fn()
            this.at(t0, 0, () => this.end(false))
          } else if (last && 'onended' in last) {
            last.onended = () => this.end(false)
          } else {
            this.at(t0 + shift, Math.max(e.durationMs, (samples / rate) * 1000), () => this.end(false))
          }
        }
      }
      if (!done) this.end(true)
    } catch (err) {
      this.end(true)
      throw err
    }
    const cancelled = await ended
    return { cancelled }
  }

  private end(cancelled: boolean): void {
    const finish = this.finish
    if (!finish) return
    this.finish = null
    this.events.emit('end', { cancelled })
    finish(cancelled)
  }

  /** Stop what is playing and drop the timings still to come. */
  stop(): void {
    for (const t of this.timers) clearTimeout(t)
    this.timers = []
    for (const s of this.sources) {
      try {
        s.stop()
      } catch {
        /* already stopped */
      }
    }
    this.sources = []
    this.end(true)
  }
}
