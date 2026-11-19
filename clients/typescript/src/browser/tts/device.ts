/**
 * The TTS engine chosen by device: on desktops the in-browser neural voice (Piper) for languages that have one, with
 * the browser voice while it downloads; on phones the browser's local voices only. A server-TTS slot is reserved
 * (`TtsKind` 'server').
 */
import { localePack, type LocaleCode, type LocalePack } from '../../locales'
import { Emitter, type TtsEngine, type TtsEvents, type TtsSpeakOptions, type TtsStatus, type TtsUtterance } from '../voice/types'
import { BrowserTts, type BrowserTtsOptions } from './browser'
import { DEFAULT_PIPER_VOICES, PiperTts, type PiperTtsOptions } from './piper'

export type DeviceClass = 'desktop' | 'phone'

/** A phone or tablet (mobile user agent, iPadOS, or touch-first and narrow), else a desktop. */
export function detectDevice(env: { userAgent?: string; maxTouchPoints?: number; width?: number; mobileHint?: boolean } = {}): DeviceClass {
  const nav = typeof navigator !== 'undefined' ? navigator : undefined
  const ua = env.userAgent ?? nav?.userAgent ?? ''
  const hint = env.mobileHint ?? (env.userAgent === undefined ? (nav as unknown as { userAgentData?: { mobile?: boolean } } | undefined)?.userAgentData?.mobile : undefined)
  if (hint) return 'phone'
  if (/Android|iPhone|iPad|iPod|Mobile|Silk|Kindle/i.test(ua)) return 'phone'
  const touch = env.maxTouchPoints ?? nav?.maxTouchPoints ?? 0
  const width = env.width ?? (typeof window !== 'undefined' ? window.innerWidth : 1280)
  // iPadOS reports a desktop Safari user agent.
  if (/Macintosh/.test(ua) && touch > 1) return 'phone'
  return touch > 1 && width < 900 ? 'phone' : 'desktop'
}

export interface ChooseTtsOptions {
  /** Language of the voice (default `en`); picks the default Piper voice and the browser voice language. */
  locale?: LocaleCode | LocalePack | (string & {})
  /** Force a device class (default: detected). */
  device?: DeviceClass | 'auto'
  /** Piper settings (desktop); `false` disables Piper. Without `voice`, the locale's default voice is used, if any. */
  piper?: PiperTtsOptions | false
  browser?: BrowserTtsOptions
}

/**
 * The engine for this device: on desktop, a `DeviceTts` that speaks with the browser voice until Piper is ready (when
 * the language has a Piper voice); otherwise a `BrowserTts`.
 */
export function chooseTts(opts: ChooseTtsOptions = {}): TtsEngine {
  const pack = localePack(opts.locale)
  const device = !opts.device || opts.device === 'auto' ? detectDevice() : opts.device
  const browser = new BrowserTts({ locale: pack, ...opts.browser })
  if (device === 'phone' || opts.piper === false) return browser
  const voice = opts.piper?.voice ?? DEFAULT_PIPER_VOICES[pack.code]
  if (!voice) return browser
  return new DeviceTts(new PiperTts({ ...opts.piper, voice }), browser)
}

/**
 * Speaks with `fallback` until `primary` is ready, then with `primary`. Status and progress are the primary's while it
 * loads; speech events come from whichever engine speaks.
 */
export class DeviceTts implements TtsEngine {
  private readonly events = new Emitter<TtsEvents>()
  private last: TtsStatus

  constructor(
    readonly primary: TtsEngine,
    readonly fallback: TtsEngine,
  ) {
    this.last = this.status
    for (const e of ['start', 'boundary', 'end', 'signal'] as const) {
      primary.on(e, (v) => this.events.emit(e, v as never))
      fallback.on(e, (v) => this.events.emit(e, v as never))
    }
    primary.on('progress', (p) => this.events.emit('progress', p))
    primary.on('ready', () => this.events.emit('ready', null))
    const onStatus = () => {
      const s = this.status
      if (s !== this.last) {
        this.last = s
        this.events.emit('status', s)
      }
    }
    primary.on('status', onStatus)
    fallback.on('status', onStatus)
  }

  /** The engine speaking new text now. */
  get active(): TtsEngine {
    return this.primary.status.state === 'ready' ? this.primary : this.fallback
  }

  get kind() {
    return this.active.kind
  }

  get measurable() {
    return this.active.measurable
  }

  get status(): TtsStatus {
    return this.primary.status.state === 'loading' || this.primary.status.state === 'ready' ? this.primary.status : this.fallback.status
  }

  get speaking() {
    return this.primary.speaking || this.fallback.speaking
  }

  /** Ready with the fallback at once; the primary prepares in the background. */
  async prepare(): Promise<void> {
    void this.primary.prepare().catch(() => undefined)
    await this.fallback.prepare()
  }

  speak(text: string, opts?: TtsSpeakOptions): TtsUtterance {
    return this.active.speak(text, opts)
  }

  cancel(): void {
    this.primary.cancel()
    this.fallback.cancel()
  }

  on<K extends keyof TtsEvents>(event: K, fn: (value: TtsEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }
}
