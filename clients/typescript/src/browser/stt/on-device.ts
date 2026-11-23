/**
 * Speech recognition on the device: the Web Speech API with `processLocally = true`, after checking the on-device
 * language pack (`SpeechRecognition.available` / `install`). Where on-device recognition is not available it reports
 * `unsupported`, unless the application allows the browser's online recogniser (`allowOnline`, e.g. Safari and every
 * iOS browser, where the audio goes to the browser vendor's speech service): then that one is used, and only there.
 */
import { norm } from '../speech'
import { Emitter, type SttEngine, type SttEvents, type SttSupport } from '../voice/types'

export interface RecognitionResultLike {
  readonly isFinal: boolean
  readonly length: number
  [index: number]: { transcript: string } | undefined
}
export interface RecognitionResultEventLike {
  resultIndex: number
  results: ArrayLike<RecognitionResultLike>
}
export interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  processLocally?: boolean
  start(track?: MediaStreamTrack): void
  stop(): void
  abort(): void
  onresult: ((e: RecognitionResultEventLike) => void) | null
  onerror: ((e: { error: string; message?: string }) => void) | null
  onend: (() => void) | null
  onspeechstart?: (() => void) | null
}
export type OnDeviceAvailability = 'available' | 'downloadable' | 'downloading' | 'unavailable'
export interface SpeechRecognitionStatic {
  new (): SpeechRecognitionLike
  available?: (o: { langs: string[]; processLocally: boolean }) => Promise<OnDeviceAvailability>
  install?: (o: { langs: string[]; processLocally: boolean }) => Promise<boolean>
}

export type RecognitionSupport = SttSupport

export interface OnDeviceOptions {
  /** BCP 47 language (default `en-US`). */
  lang?: string
  /**
   * Use the browser's online recogniser where no on-device one exists (default false). The audio then goes to the
   * browser vendor's speech service (Apple on iOS and Safari, Google on Chrome without on-device support): an
   * application turning this on lists that service among its sub-processors. On-device recognition always comes first.
   */
  allowOnline?: boolean
  /** Override the recogniser class (tests). */
  recognition?: SpeechRecognitionStatic | null
}

function recognitionClass(override?: SpeechRecognitionStatic | null): SpeechRecognitionStatic | null {
  if (override !== undefined) return override
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: SpeechRecognitionStatic; webkitSpeechRecognition?: SpeechRecognitionStatic }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/** `ready`, `downloadable` (language pack), or `unsupported` (also when only a cloud recogniser exists). */
export async function recognitionSupport(opts: OnDeviceOptions = {}): Promise<RecognitionSupport> {
  const C = recognitionClass(opts.recognition)
  if (!C) return 'unsupported'
  const local = await onDeviceAvailability(C, opts.lang ?? 'en-US')
  if (local === 'available') return 'ready'
  if (local === 'downloadable' || local === 'downloading') return 'downloadable'
  return opts.allowOnline ? 'ready' : 'unsupported'
}

async function onDeviceAvailability(C: SpeechRecognitionStatic, lang: string): Promise<OnDeviceAvailability> {
  if (typeof C.available !== 'function') return 'unavailable'
  try {
    return await C.available({ langs: [lang], processLocally: true })
  } catch {
    return 'unavailable'
  }
}

/** Whether recognition runs on the device (false: the browser's online recogniser, allowed by `allowOnline`). */
export async function recognitionIsLocal(opts: OnDeviceOptions = {}): Promise<boolean> {
  const C = recognitionClass(opts.recognition)
  return !!C && (await onDeviceAvailability(C, opts.lang ?? 'en-US')) === 'available'
}

/** Download the on-device language pack (from a user gesture). */
export async function installRecognition(opts: OnDeviceOptions = {}): Promise<boolean> {
  const C = recognitionClass(opts.recognition)
  if (!C?.install) return false
  try {
    return await C.install({ langs: [opts.lang ?? 'en-US'], processLocally: true })
  } catch {
    return false
  }
}

/** Ready, installing the language pack when it is downloadable; false when on-device recognition is impossible. */
export async function ensureRecognition(opts: OnDeviceOptions = {}): Promise<boolean> {
  const s = await recognitionSupport(opts)
  if (s === 'ready') return true
  if (s === 'downloadable') return (await installRecognition(opts)) && (await recognitionSupport(opts)) === 'ready'
  return false
}

const FATAL = new Set(['not-allowed', 'service-not-allowed', 'language-not-supported', 'audio-capture'])

export interface OnDeviceSttOptions extends OnDeviceOptions {
  /** Restart delay of a continuous recogniser (ms, default 150). */
  restartMs?: number
  /**
   * A continuous recogniser that ends this soon after starting, having heard nothing, failed to start (ms, default
   * 1000). Three such ends in a row stop the restarts with the error `stalled`: the next start (from a user gesture,
   * where a browser may require one) tries again.
   */
  minSessionMs?: number
}

/** Fast ends in a row after which a continuous recogniser stops restarting itself. */
const STALL_AFTER = 3

/**
 * A result without the words a recogniser already gave as final in this session. Some recognisers (WebKit is
 * reported to) repeat the earlier phrases at the start of a later result; those words were reported once already.
 */
export function withoutRepeat(text: string, before: string): string {
  const said = norm(before).split(' ').filter(Boolean)
  if (!said.length) return text
  const words = text.trim().split(/\s+/).filter(Boolean)
  if (words.length <= said.length) return text
  const head = norm(words.slice(0, said.length).join(' '))
  return head === said.join(' ') ? words.slice(said.length).join(' ') : text
}

/** The on-device `SttEngine`. */
export class OnDeviceStt implements SttEngine {
  readonly kind = 'on-device' as const
  readonly lang: string
  private readonly events = new Emitter<SttEvents>()
  private readonly opts: OnDeviceSttOptions
  private rec: SpeechRecognitionLike | null = null
  private active = false
  private continuous = false
  private track: MediaStreamTrack | null = null
  /** Continuous recognisers in a row that ended at once without hearing anything. */
  private fastEnds = 0
  /** Counts starts and stops: a start still checking support gives way to a later start, stop or abort. */
  private gen = 0
  /** On the device (processLocally), or the browser's online recogniser where only that one is allowed and exists. */
  private local = true

  constructor(opts: OnDeviceSttOptions = {}) {
    this.opts = opts
    this.lang = opts.lang ?? 'en-US'
  }

  get running(): boolean {
    return this.active
  }

  on<K extends keyof SttEvents>(event: K, fn: (value: SttEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  support(): Promise<SttSupport> {
    return recognitionSupport({ ...this.opts, lang: this.lang })
  }

  install(): Promise<boolean> {
    return installRecognition({ ...this.opts, lang: this.lang })
  }

  async start(opts: { continuous: boolean; track?: MediaStreamTrack | null }): Promise<void> {
    if (this.active) return
    const gen = ++this.gen
    if (!(await ensureRecognition({ ...this.opts, lang: this.lang }))) {
      this.events.emit('error', { code: 'unsupported', message: 'On-device speech recognition is not available.' })
      throw new Error('unsupported')
    }
    const local = await recognitionIsLocal({ ...this.opts, lang: this.lang })
    if (gen !== this.gen || this.active) return
    this.local = local
    this.active = true
    this.continuous = opts.continuous
    this.track = opts.track ?? null
    this.fastEnds = 0
    this.listen()
  }

  private listen(): void {
    if (!this.active) return
    const C = recognitionClass(this.opts.recognition)
    if (!C) return
    const r = new C()
    r.lang = this.lang
    r.continuous = this.continuous
    r.interimResults = true
    if (this.local) r.processLocally = true
    this.rec = r
    const startedAt = Date.now()
    /** This session's final text (to drop it when repeated), the phrase heard but not final yet, and any result. */
    let finals = ''
    let pending = ''
    let heard = false
    r.onresult = (e) => {
      if (this.rec !== r) return
      if (!this.continuous) {
        // One phrase: report the whole transcript so far.
        let t = ''
        let final = true
        for (let k = 0; k < e.results.length; k++) {
          t += e.results[k]?.[0]?.transcript ?? ''
          final &&= !!e.results[k]?.isFinal
        }
        this.events.emit(final ? 'final' : 'interim', t)
        return
      }
      for (let k = e.resultIndex; k < e.results.length; k++) {
        const res = e.results[k]
        if (!res) continue
        const text = withoutRepeat(res[0]?.transcript ?? '', finals)
        if (text.trim()) heard = true
        if (res.isFinal) {
          finals = `${finals} ${text}`
          pending = ''
          this.events.emit('interim', '')
          this.events.emit('final', text)
        } else {
          pending = text
          this.events.emit('interim', text)
        }
      }
    }
    r.onspeechstart = () => {
      heard = true
      this.events.emit('speechstart', null)
    }
    r.onerror = (e) => {
      if (this.rec !== r) return
      if (FATAL.has(e.error)) {
        this.active = false
        this.events.emit('error', { code: e.error, message: e.message ?? e.error })
      }
    }
    r.onend = () => {
      if (this.rec !== r) return
      if (this.active && this.continuous) {
        // A session may end with its last phrase still interim (WebKit's continuous recognition may never mark one
        // final, and Apple's recogniser ends a task after about a minute): it is delivered, not dropped by the restart.
        if (pending.trim()) {
          this.events.emit('interim', '')
          this.events.emit('final', pending)
        }
        this.fastEnds = !heard && Date.now() - startedAt < (this.opts.minSessionMs ?? 1000) ? this.fastEnds + 1 : 0
        if (this.fastEnds >= STALL_AFTER) {
          // Restarting outside a user gesture can fail at once, over and over: stop, and say so.
          this.active = false
          this.rec = null
          this.events.emit('error', { code: 'stalled', message: 'Speech recognition keeps ending as soon as it starts.' })
          this.events.emit('end', null)
          return
        }
        setTimeout(() => this.listen(), this.opts.restartMs ?? 150)
      } else {
        this.active = false
        this.rec = null
        this.events.emit('end', null)
      }
    }
    try {
      // A microphone track is Chrome's on-device API; the online recognisers (WebKit) capture on their own.
      if (this.track && this.local) r.start(this.track)
      else r.start()
    } catch {
      /* already started */
    }
  }

  stop(): void {
    this.gen++
    this.active = false
    this.rec?.stop()
  }

  abort(): void {
    this.gen++
    this.active = false
    const r = this.rec
    this.rec = null
    try {
      r?.abort()
    } catch {
      /* already stopped */
    }
    if (r) this.events.emit('end', null)
  }
}
