/**
 * Text to speech by the server (`POST /v1/session/speech`) with the session's engine, played with timings. A `say`
 * segment is spoken by its turn and index (`say` in the speak options), so its cues come back as `mark` events at the
 * time the voice reaches them; any other text is spoken as text.
 */
import { speechEvents, type SpeechSession } from '../speech-server'
import { Emitter, type TtsEngine, type TtsEvents, type TtsSpeakOptions, type TtsStatus, type TtsUtterance } from '../voice/types'
import { TimedSpeechPlayer, type TimedSpeechPlayerOptions } from './player'

export interface ServerTtsOptions extends TimedSpeechPlayerOptions {
  session: SpeechSession
  /** PCM sample rate asked for (default 24000). */
  sampleRate?: 16000 | 22050 | 24000 | 44100 | 48000
}

export class ServerTts implements TtsEngine {
  readonly kind = 'server' as const
  readonly measurable = false
  private readonly events = new Emitter<TtsEvents>()
  private readonly player: TimedSpeechPlayer
  private readonly session: SpeechSession
  private readonly rate: ServerTtsOptions['sampleRate']
  private chain: Promise<unknown> = Promise.resolve()
  private queued = 0
  private ctrl: AbortController | null = null
  private status_: TtsStatus = { state: 'ready' }
  /** Bumped by `cancel`: utterances queued before it are dropped. */
  private generation = 0

  constructor(opts: ServerTtsOptions) {
    this.session = opts.session
    this.rate = opts.sampleRate ?? 24000
    this.player = new TimedSpeechPlayer(opts)
  }

  get status(): TtsStatus {
    return this.status_
  }

  get speaking(): boolean {
    return this.queued > 0
  }

  async prepare(): Promise<void> {
    /* nothing to download */
  }

  on<K extends keyof TtsEvents>(event: K, fn: (value: TtsEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  speak(text: string, opts: TtsSpeakOptions = {}): TtsUtterance {
    let stopped = false
    let cancel = () => {
      stopped = true
    }
    this.queued++
    const generation = this.generation
    const done = (this.chain = this.chain.then(async () => {
      let cancelled = stopped || generation !== this.generation
      const ctrl = (this.ctrl = new AbortController())
      cancel = () => {
        cancelled = true
        ctrl.abort()
        this.player.stop()
      }
      if (opts.signal?.aborted) cancelled = true
      opts.signal?.addEventListener('abort', cancel, { once: true })
      try {
        if (cancelled) return { cancelled: true }
        const input = opts.say ? { turnId: opts.say.turnId, index: opts.say.index } : { text }
        const offs = [
          this.player.on('start', () => this.events.emit('start', { text })),
          this.player.on('word', (w) => this.events.emit('boundary', { text, charIndex: w.at })),
          this.player.on('mark', (m) => this.events.emit('mark', { text, mark: m })),
          this.player.on('viseme', (v) => this.events.emit('viseme', { text, viseme: v })),
        ]
        try {
          const r = await this.player.play(speechEvents(this.session, { ...input, format: 'pcm', sampleRate: this.rate, signal: ctrl.signal }))
          cancelled = cancelled || r.cancelled
        } finally {
          for (const off of offs) off()
        }
      } catch (e) {
        if (!cancelled) {
          this.status_ = { state: 'error', error: { code: 'tts', message: e instanceof Error ? e.message : String(e) } }
          this.events.emit('status', this.status_)
        }
        cancelled = true
      } finally {
        this.queued--
        opts.signal?.removeEventListener('abort', cancel)
        this.events.emit('end', { text, cancelled })
      }
      return { cancelled }
    })) as Promise<{ cancelled: boolean }>
    return { text, done, cancel: () => cancel() }
  }

  cancel(): void {
    this.generation++
    this.ctrl?.abort()
    this.player.stop()
  }
}
