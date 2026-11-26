/**
 * Voice interfaces. A conversation engine (`browser` or `server`) drives turns by voice; speech-to-text and
 * text-to-speech engines sit behind their own interfaces so the device can choose; audio signals describe whatever
 * is sounding (the user's microphone or the agent's voice) for any visual.
 */
import type { TurnPhase } from '../../core/types'
import type { LocaleCode, LocalePack } from '../../locales'
import type { AudioSignal, AudioSignals } from '../audio/signals'
import type { MicSound } from '../audio/mic-sounds'
import type { Microphone } from '../mic/microphone'
import type { MarkTiming, VisemeTiming } from '../speech-server'

// ── conversation engines ────────────────────────────────────────────────────────────────────────────────────

/** Engine lifecycle (the turn phase is separate: `TurnPhase`). */
export type VoiceState =
  /** Not started. */
  | 'idle'
  /** Checking support, downloading a language pack or a voice, connecting. */
  | 'starting'
  /** Running: listening, thinking or speaking (see the phase). */
  | 'active'
  /** This device cannot do it (no on-device recognition, no WebRTC…). */
  | 'unsupported'
  /** Stopped by an error (microphone refused, connection lost…). */
  | 'error'

/** How the user talks: dictation fills the application's input; conversation runs turns by voice. */
export type VoiceMode = 'dictation' | 'conversation'

export interface VoiceError {
  /** `unsupported`, `not-allowed` (microphone refused), `no-device`, `no_local_voice`, `connect`, `tts`, … */
  code: string
  message: string
}

/** One spoken segment of the agent (a `say` event being played). */
export interface SpokenSegment {
  turnId: string | null
  /** The `say` index within the turn. */
  index: number
  /** What is shown (the subtitle): the `say` text, digits and all. */
  text: string
  /** What the voice says, when it differs from `text` (numbers in words, «1.000» → «mille»). */
  spoken?: string
}

export interface VoiceEvents {
  state: VoiceState
  /** The turn phase as this engine sees it (also mirrored to the session's `phase`). */
  phase: TurnPhase
  /** What the recogniser is hearing (interim); '' when the phrase is final. */
  interim: string
  /** A final phrase of the user, after the echo guard (sent to the agent). */
  heard: string
  /** The agent starts a spoken segment (subtitles). */
  segmentStart: SpokenSegment
  /**
   * Word boundary inside the segment being spoken (`charIndex` into `segment.text`, the shown text, even when the
   * voice says a `spoken` form), when the engine knows it.
   */
  boundary: { segment: SpokenSegment; charIndex: number }
  segmentEnd: SpokenSegment & { cancelled: boolean }
  /** A cue of the answer (`say.marks`) reached by the voice, with server speech. */
  mark: { segment: SpokenSegment; mark: MarkTiming }
  /** The user talked over the agent: speech stopped and the turn was cancelled. */
  bargeIn: { turnId: string | null; heard: string }
  /** The agent paused because the user may be starting to talk (`true`), or carried on (`false`). */
  hold: boolean
  /** The microphone opened (`true`) or closed (`false`): in strict turns it is closed during the agent's turn. */
  mic: boolean
  error: VoiceError
}

export interface BargeInOptions {
  /** Interrupt the agent when the user talks over it (default true). */
  enabled?: boolean
  /** Interim words needed to interrupt (default 3; the echo guard applies). */
  minWords?: number
}

export interface VoiceEngine {
  readonly kind: 'browser' | 'server'
  readonly state: VoiceState
  readonly phase: TurnPhase
  /** Audio signals of this engine (user's microphone and agent's voice). */
  readonly signals: AudioSignals
  /** Start a voice conversation (from a user gesture). */
  start(): Promise<void>
  stop(): Promise<void>
  /** Cut the agent off by hand (a stop button): silence and cancel the running turn. */
  interrupt(): Promise<void>
  /** Mute or unmute the user's microphone without leaving the conversation. */
  setMuted(muted: boolean): void
  readonly muted: boolean
  on<K extends keyof VoiceEvents>(event: K, fn: (value: VoiceEvents[K]) => void): () => void
}

/** Options shared by the engines. */
export interface VoiceCommonOptions {
  /** Language pack (default: the session's, else `en`): recognition and voice language, syllable rules. */
  locale?: LocaleCode | LocalePack | (string & {})
  /** BCP 47 language (default: the locale's, e.g. `en-US`, `it-IT`). */
  lang?: string
  /** Sound played when the microphone opens and closes (default `goccia`; `none` for silence). */
  micSound?: MicSound
  /** The microphone to use (default: a new `Microphone`). */
  microphone?: Microphone
  bargeIn?: BargeInOptions
}

// ── speech to text ────────────────────────────────────────────────────────────────────────────────────────────

export type SttSupport = 'ready' | 'downloadable' | 'unsupported'

/** Where speech started or ended, in milliseconds of the audio sent so far (recognisers that know it). */
export interface SpeechMark {
  ms: number
}

export interface SttEvents {
  /** Partial transcript of the current phrase. */
  interim: string
  /** A final phrase. */
  final: string
  /** The recogniser heard speech start (barge-in, a listening indicator). */
  speechstart: SpeechMark | null
  /** The recogniser heard speech end; the phrase's `final` follows. */
  speechend: SpeechMark | null
  /** The recogniser stopped (continuous engines restart themselves while running). */
  end: null
  /** Something was ignored and recognition goes on (`commit_too_short`). */
  warning: VoiceError
  error: VoiceError
}

/**
 * Speech recognition: on the device (`OnDeviceStt`), or by the server when the token chose a recognition engine
 * (`ServerStt`, the session's engine through Evokai).
 */
export interface SttEngine {
  readonly kind: 'on-device' | 'server'
  readonly lang: string
  readonly running: boolean
  /** Whether recognition can run on this device, or after downloading the language pack. */
  support(): Promise<SttSupport>
  /** Download the language pack (from a user gesture). */
  install(): Promise<boolean>
  /**
   * Start recognising. `continuous` keeps it running across phrases (conversation); otherwise one phrase (dictation).
   * `track`: the selected microphone, where the browser accepts one.
   */
  start(opts: { continuous: boolean; track?: MediaStreamTrack | null }): Promise<void>
  /** Stop, delivering what was heard. */
  stop(): void
  /** Stop and discard. */
  abort(): void
  on<K extends keyof SttEvents>(event: K, fn: (value: SttEvents[K]) => void): () => void
}

// ── text to speech ────────────────────────────────────────────────────────────────────────────────────────────

/** `piper`: neural voice in the browser (desktop); `browser`: local `speechSynthesis` voices; `server`: Evokai's engine. */
export type TtsKind = 'piper' | 'browser' | 'server'

export interface TtsProgress {
  /** Bytes downloaded so far and the total, when known. */
  loaded: number
  total: number | null
  /** 0–1 when the total is known. */
  fraction: number | null
}

export interface TtsStatus {
  /** `idle` (not prepared), `loading` (downloading or warming up), `ready`, `unavailable` (not on this device), `error`. */
  state: 'idle' | 'loading' | 'ready' | 'unavailable' | 'error'
  progress?: TtsProgress
  /** The model came from the cache (no download). */
  cached?: boolean
  error?: VoiceError
}

export interface TtsSpeakOptions {
  /** Speech rate (1 = normal). */
  rate?: number
  signal?: AbortSignal
  /** The `say` segment this text is (server speech speaks it by turn and index, with its cues). */
  say?: { turnId: string; index: number }
}

export interface TtsUtterance {
  readonly text: string
  /** Resolves when the utterance ends (or is cancelled). */
  readonly done: Promise<{ cancelled: boolean }>
  cancel(): void
}

export interface TtsEvents {
  status: TtsStatus
  /** Model download progress (Piper's first use). */
  progress: TtsProgress
  /** The engine can speak (for Piper: the model is downloaded and loaded). */
  ready: null
  start: { text: string }
  /** A word starts (`charIndex` into `text`), when the engine reports it. */
  boundary: { text: string; charIndex: number }
  end: { text: string; cancelled: boolean }
  /** Audio measurement of what is being spoken (measured from audio, or estimated from text). */
  signal: AudioSignal
  /** A cue of the segment reached by the voice (server speech). */
  mark: { text: string; mark: MarkTiming }
  /** A mouth shape, when the engine gives them (server speech). */
  viseme: { text: string; viseme: VisemeTiming }
}

export interface TtsEngine {
  readonly kind: TtsKind
  readonly status: TtsStatus
  /** True when the engine plays audio the client can measure (Piper, server); false for `speechSynthesis`. */
  readonly measurable: boolean
  /** Download / warm up (Piper); resolves when ready. Safe to call more than once. */
  prepare(): Promise<void>
  /** Queue a text; utterances play in order. */
  speak(text: string, opts?: TtsSpeakOptions): TtsUtterance
  /** Stop and drop everything queued. */
  cancel(): void
  readonly speaking: boolean
  on<K extends keyof TtsEvents>(event: K, fn: (value: TtsEvents[K]) => void): () => void
}

// ── emitter ───────────────────────────────────────────────────────────────────────────────────────────────────

/** A tiny typed emitter shared by the engines. */
export class Emitter<E> {
  private readonly map = new Map<keyof E, Set<(v: never) => void>>()

  on<K extends keyof E>(event: K, fn: (value: E[K]) => void): () => void {
    const set = this.map.get(event) ?? new Set()
    set.add(fn as (v: never) => void)
    this.map.set(event, set)
    return () => set.delete(fn as (v: never) => void)
  }

  emit<K extends keyof E>(event: K, value: E[K]): void {
    for (const fn of [...(this.map.get(event) ?? [])]) (fn as (v: E[K]) => void)(value)
  }

  clear(): void {
    this.map.clear()
  }
}
