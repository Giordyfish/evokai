/**
 * Dictation: the user speaks and the text goes into the application's own input (nothing is sent to the agent).
 * On-device recognition only; the same microphone, sounds and signals as a conversation.
 */
import { playMicSound } from '../audio/mic-sounds'
import { AudioSignals, meterAnalyser } from '../audio/signals'
import { Microphone } from '../mic/microphone'
import { OnDeviceStt, type OnDeviceSttOptions } from '../stt/on-device'
import { localePack } from '../../locales'
import { Emitter, type SttEngine, type VoiceCommonOptions, type VoiceError } from './types'

export type DictationState = 'idle' | 'starting' | 'listening' | 'unsupported' | 'error'

export interface DictationEvents {
  state: DictationState
  /** The transcript so far (interim). */
  text: string
  /** The final transcript of the phrase. */
  final: string
  error: VoiceError
}

export interface DictationOptions extends Omit<VoiceCommonOptions, 'bargeIn'>, Pick<OnDeviceSttOptions, 'recognition'> {
  stt?: SttEngine
  /** Keep listening across phrases until `stop()` (default false: one phrase). */
  continuous?: boolean
  /** Capture the microphone for the user's audio signal (default true). */
  meterMicrophone?: boolean
  /** Use the browser's online recogniser where there is no on-device one (default false; see BrowserVoiceOptions). */
  allowOnlineRecognition?: boolean
}

export class Dictation {
  readonly signals = new AudioSignals()
  readonly microphone: Microphone
  readonly stt: SttEngine
  private readonly events = new Emitter<DictationEvents>()
  private readonly opts: DictationOptions
  private current: DictationState = 'idle'
  private unsubs: Array<() => void> = []
  private stopMeter: (() => void) | null = null

  constructor(opts: DictationOptions = {}) {
    this.opts = opts
    this.microphone = opts.microphone ?? new Microphone()
    this.stt = opts.stt ?? new OnDeviceStt({ lang: opts.lang ?? localePack(opts.locale).lang, recognition: opts.recognition, allowOnline: opts.allowOnlineRecognition })
  }

  get state(): DictationState {
    return this.current
  }

  on<K extends keyof DictationEvents>(event: K, fn: (value: DictationEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  private set(s: DictationState): void {
    if (s === this.current) return
    this.current = s
    this.events.emit('state', s)
  }

  /** Start (from a user gesture). */
  async start(): Promise<void> {
    if (this.current === 'listening' || this.current === 'starting') return
    this.set('starting')
    if ((await this.stt.support()) === 'unsupported') {
      this.set('unsupported')
      this.events.emit('error', { code: 'unsupported', message: 'On-device speech recognition is not available.' })
      return
    }
    this.unsubs = [
      this.stt.on('interim', (t) => t && this.events.emit('text', t)),
      this.stt.on('final', (t) => {
        this.events.emit('text', t)
        this.events.emit('final', t)
      }),
      this.stt.on('end', () => void this.finish()),
      this.stt.on('error', (e) => {
        this.events.emit('error', e)
        void this.finish('error')
      }),
    ]
    if (this.opts.meterMicrophone !== false) {
      await this.microphone.start().catch(() => undefined)
      const an = this.microphone.analyser()
      if (an) this.stopMeter = meterAnalyser(an, 'user', this.signals)
    }
    try {
      await this.stt.start({ continuous: this.opts.continuous ?? false, track: this.microphone.track })
    } catch {
      return this.finish('error')
    }
    await playMicSound(this.opts.micSound ?? 'goccia', 'on').catch(() => undefined)
    this.set('listening')
  }

  /** Stop, keeping what was heard. */
  stop(): void {
    this.stt.stop()
  }

  /** Stop and discard. */
  cancel(): void {
    this.stt.abort()
    void this.finish()
  }

  private async finish(state: DictationState = 'idle'): Promise<void> {
    const was = this.current === 'listening'
    for (const u of this.unsubs) u()
    this.unsubs = []
    this.stopMeter?.()
    this.stopMeter = null
    if (!this.opts.microphone) this.microphone.stop()
    if (was) await playMicSound(this.opts.micSound ?? 'goccia', 'off').catch(() => undefined)
    this.set(state)
  }
}
