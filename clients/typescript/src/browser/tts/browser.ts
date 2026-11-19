/**
 * Text to speech with the browser's `speechSynthesis`, local voices only (`localService === true`): a network voice
 * is never used. There is no audio to measure: signals are estimated from the text (`syllableDriver`), re-aligned on
 * every word boundary the browser reports.
 */
import { localePack, type LocaleCode, type LocalePack } from '../../locales'
import { syllableDriver, type SyllableDriver } from '../audio/syllables'
import { Emitter, type TtsEngine, type TtsEvents, type TtsSpeakOptions, type TtsStatus, type TtsUtterance } from '../voice/types'

export interface VoiceLike {
  name: string
  lang: string
  localService: boolean
  default?: boolean
}
export interface UtteranceLike {
  text: string
  lang: string
  rate: number
  voice: VoiceLike | null
  onstart?: (() => void) | null
  onend: (() => void) | null
  onerror: (() => void) | null
  onboundary?: ((e: { name?: string; charIndex: number }) => void) | null
}
export interface SynthesisLike {
  speak(u: UtteranceLike): void
  cancel(): void
  getVoices(): VoiceLike[]
  addEventListener?(type: 'voiceschanged', fn: () => void): void
  removeEventListener?(type: 'voiceschanged', fn: () => void): void
}

/** The voices, once the browser has loaded them (some load them lazily and fire `voiceschanged`). */
export function loadedVoices(synth: SynthesisLike, timeoutMs = 1500): Promise<VoiceLike[]> {
  const now = synth.getVoices()
  if (now.length || !synth.addEventListener) return Promise.resolve(now)
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer)
      synth.removeEventListener?.('voiceschanged', done)
      resolve(synth.getVoices())
    }
    const timer = setTimeout(done, timeoutMs)
    synth.addEventListener?.('voiceschanged', done)
  })
}

/** The best voice for the language that runs on this device, or null. */
export function pickLocalVoice(voices: VoiceLike[], lang = 'en-US'): VoiceLike | null {
  const base = lang.toLowerCase().split('-')[0] ?? lang
  const local = voices.filter((v) => v.localService === true && v.lang.toLowerCase().replace('_', '-').startsWith(base))
  if (!local.length) return null
  const score = (v: VoiceLike) => (/natural|neural|enhanced|premium/i.test(v.name) ? 2 : 0) + (v.lang === lang ? 1 : 0)
  return [...local].sort((a, b) => score(b) - score(a))[0] ?? null
}

/**
 * The voice to speak with: an on-device one first; with `online` also the browser's online voices for the language
 * (the text then goes to the browser vendor's speech service).
 */
export function pickVoice(voices: VoiceLike[], lang = 'en-US', online = false): VoiceLike | null {
  const local = pickLocalVoice(voices, lang)
  if (local || !online) return local
  const base = lang.toLowerCase().split('-')[0] ?? lang
  const any = voices.filter((v) => v.lang.toLowerCase().replace('_', '-').startsWith(base))
  return any.find((v) => v.lang === lang) ?? any[0] ?? null
}

export interface BrowserTtsOptions {
  /** Language pack (default `en`): the voice language and the syllable rules of the estimate. */
  locale?: LocaleCode | LocalePack | (string & {})
  /** BCP 47 language (default: the locale's). */
  lang?: string
  /** Estimate signals from the text (default true). */
  estimate?: boolean
  /** Default rate (1.05). */
  rate?: number
  /**
   * Also use the browser's online voices when the device has no voice for the language (default false: on-device
   * only). Online voices send the text to the browser vendor's speech service (Google, Apple, Microsoft): an
   * application turning this on lists that service among its sub-processors.
   */
  allowOnlineVoices?: boolean
  /** Override `speechSynthesis` / `SpeechSynthesisUtterance` (tests). */
  synthesis?: SynthesisLike | null
  Utterance?: new (text: string) => UtteranceLike
}

export class BrowserTts implements TtsEngine {
  readonly kind = 'browser' as const
  readonly measurable = false
  private readonly events = new Emitter<TtsEvents>()
  private readonly opts: BrowserTtsOptions
  private queue: Array<{ text: string; rate: number; resolve: (v: { cancelled: boolean }) => void }> = []
  private playing: { text: string; resolve: (v: { cancelled: boolean }) => void } | null = null
  private generation = 0
  private current: TtsStatus
  private readonly pack: LocalePack
  private readonly lang: string
  private driver: SyllableDriver | null = null
  private waitedForVoices = false
  private loadingVoices = false

  constructor(opts: BrowserTtsOptions = {}) {
    this.opts = opts
    this.pack = localePack(opts.locale)
    this.lang = opts.lang ?? this.pack.lang
    this.current = { state: this.synth ? 'idle' : 'unavailable' }
  }

  private get synth(): SynthesisLike | null {
    if (this.opts.synthesis !== undefined) return this.opts.synthesis
    return typeof window !== 'undefined' && window.speechSynthesis ? (window.speechSynthesis as unknown as SynthesisLike) : null
  }

  get status(): TtsStatus {
    return this.current
  }

  get speaking(): boolean {
    return this.playing !== null || this.queue.length > 0
  }

  on<K extends keyof TtsEvents>(event: K, fn: (value: TtsEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  private setStatus(s: TtsStatus): void {
    this.current = s
    this.events.emit('status', s)
  }

  /** Ready when a local voice for the language exists (voices load lazily in some browsers). */
  async prepare(): Promise<void> {
    const synth = this.synth
    if (!synth) return this.setStatus({ state: 'unavailable' })
    const voices = await loadedVoices(synth)
    if (pickVoice(voices, this.lang, this.opts.allowOnlineVoices) || (this.opts.allowOnlineVoices && !voices.length)) {
      if (this.current.state !== 'ready') {
        this.setStatus({ state: 'ready' })
        this.events.emit('ready', null)
      }
      return
    }
    this.setStatus({ state: 'unavailable', error: { code: 'no_local_voice', message: 'No on-device voice for this language.' } })
  }

  speak(text: string, opts: TtsSpeakOptions = {}): TtsUtterance {
    let resolve!: (v: { cancelled: boolean }) => void
    const done = new Promise<{ cancelled: boolean }>((r) => (resolve = r))
    const item = { text, rate: opts.rate ?? this.opts.rate ?? 1.05, resolve }
    this.queue.push(item)
    opts.signal?.addEventListener('abort', () => this.drop(item))
    if (!this.playing && !this.loadingVoices) this.next()
    return { text, done, cancel: () => this.drop(item) }
  }

  private drop(item: { text: string; resolve: (v: { cancelled: boolean }) => void }): void {
    if (this.playing === item) {
      this.driver?.stop()
      this.driver = null
      this.generation++
      this.synth?.cancel()
      this.finish(true)
      this.next()
      return
    }
    const i = this.queue.indexOf(item as (typeof this.queue)[number])
    if (i >= 0) {
      this.queue.splice(i, 1)
      item.resolve({ cancelled: true })
    }
  }

  private finish(cancelled: boolean): void {
    const p = this.playing
    this.playing = null
    if (p) {
      p.resolve({ cancelled })
      this.events.emit('end', { text: p.text, cancelled })
    }
  }

  private next(): void {
    const synth = this.synth
    const item = this.queue.shift()
    if (!item) return
    const voices = synth ? synth.getVoices() : []
    const voice = synth ? pickVoice(voices, this.lang, this.opts.allowOnlineVoices) : null
    if (synth && !voice && !voices.length && !this.waitedForVoices) {
      // The browser has not loaded its voices yet: wait for them once, then try again.
      this.waitedForVoices = true
      this.loadingVoices = true
      this.queue.unshift(item)
      void loadedVoices(synth).then(() => {
        this.loadingVoices = false
        if (!this.playing) this.next()
      })
      return
    }
    // With online voices allowed and no list at all, the browser picks its own voice for the language.
    const browserChooses = !!synth && !voice && !!this.opts.allowOnlineVoices
    if (!synth || (!voice && !browserChooses)) {
      // No voice on the device: nothing is spoken, never a network voice.
      item.resolve({ cancelled: true })
      for (const q of this.queue.splice(0)) q.resolve({ cancelled: true })
      if (this.current.state !== 'unavailable') {
        this.setStatus({ state: 'unavailable', error: { code: 'no_local_voice', message: 'No on-device voice for this language.' } })
      }
      return
    }
    const u = this.opts.Utterance
      ? new this.opts.Utterance(item.text)
      : typeof SpeechSynthesisUtterance !== 'undefined'
        ? (new SpeechSynthesisUtterance(item.text) as unknown as UtteranceLike)
        : null
    if (!u) return
    const gen = this.generation
    if (voice) u.voice = voice
    u.lang = voice?.lang ?? this.lang
    u.rate = item.rate
    u.onstart = () => {
      if (gen !== this.generation) return
      this.driver?.stop()
      if (this.opts.estimate !== false) {
        this.driver = syllableDriver(item.text, { rate: item.rate, locale: this.pack, emit: (s) => this.events.emit('signal', s) })
      }
      this.events.emit('start', { text: item.text })
    }
    u.onboundary = (e) => {
      if (gen !== this.generation || (e.name && e.name !== 'word')) return
      this.driver?.resync(e.charIndex)
      this.events.emit('boundary', { text: item.text, charIndex: e.charIndex })
    }
    const end = () => {
      if (gen !== this.generation) return
      this.driver?.stop()
      this.driver = null
      this.finish(false)
      this.next()
    }
    u.onend = end
    u.onerror = end
    this.playing = item
    synth.speak(u)
  }

  cancel(): void {
    this.driver?.stop()
    this.driver = null
    this.generation++
    for (const q of this.queue.splice(0)) q.resolve({ cancelled: true })
    this.synth?.cancel()
    this.finish(true)
  }
}
