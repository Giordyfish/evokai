/**
 * Browser entry point: the headless core. A session over HTTP + SSE with typed events and phases, the microphone,
 * voice (dictation and conversation, barge-in), speech engines chosen by device, audio signals for any visual,
 * optional microphone sounds, usage as percentages. It draws nothing: the application builds its own interface.
 */
export * from '../core/types'
export {
  EvokaiError,
  EvokaiAuthError,
  EvokaiConflictError,
  EvokaiNotFoundError,
  EvokaiRateLimitError,
  EvokaiValidationError,
} from '../core/http'
export type { FetchLike } from '../core/http'
export { markupPattern, stripMarkup } from '../core/markup'

// session and state
export { EvokaiSession, isLimitError } from './session'
export type { ChatInput, ClientToolHandler, EvokaiSessionOptions, TokenInfo, TokenRequestContext } from './session'
export { SessionStore, reduce, initialState, fromStored, fromHistory, phaseOf, usageOf, seen } from './store'
export type { Action, ChatMessage, ConfirmState, OpenAsk, Part, SessionState } from './store'
export { confirmReasonText } from './reasons'
export { SseParser, readSse } from './sse'
export { runAgui, interruptsOf } from './agui'
export type { AguiEvent, AguiInterrupt, AguiMessage, AguiResume, RunAgentInput, RunAguiOptions } from './agui'
export type { SseMessage } from './sse'

// speech text rules (shared with the server)
export {
  FILLER_WORDS,
  SentenceSplitter,
  isEcho,
  meaningfulWords,
  norm,
  redact,
  speakable,
  spokenDecision,
  spokenOption,
  stripEcho,
  tailWords,
} from './speech'

// microphone
export { Microphone } from './mic/microphone'
export type { MicDevice, MicEvents, MicPermission, MicState, MicrophoneOptions } from './mic/microphone'

// audio signals, syllables, microphone sounds
export { AudioSignals, measure, meterAnalyser, SPECTRUM_BINS, RMS_GAIN, ONSET_JUMP } from './audio/signals'
export type { AnalyserLike, AudioSignal, AudioSource, Measurement, SignalListener } from './audio/signals'
export { syllabify, stressIndex, timeline, syllableDriver, syllableRules, SYLLABLE_SECONDS, DRIVER_DECAY } from './audio/syllables'
export type { DriverOptions, SyllableDriver, SyllableEvent, SyllableRules, SyllableTimeline, TimelineOptions } from './audio/syllables'
export { playMicSound, MIC_SOUNDS, MIC_SOUND_RECIPES, DEFAULT_MIC_SOUND } from './audio/mic-sounds'
export { unlockAudio } from './audio/unlock'
export type { UnlockTargets } from './audio/unlock'
export type { AudioContextLike, MicSound, MicSoundName, MicSoundOptions } from './audio/mic-sounds'

// speech to text
export { OnDeviceStt, recognitionSupport, recognitionIsLocal, installRecognition, ensureRecognition } from './stt/on-device'
export type {
  OnDeviceAvailability,
  OnDeviceOptions,
  OnDeviceSttOptions,
  RecognitionResultEventLike,
  RecognitionResultLike,
  RecognitionSupport,
  SpeechRecognitionLike,
  SpeechRecognitionStatic,
} from './stt/on-device'

// text to speech
export { BrowserTts, loadedVoices, pickLocalVoice, pickVoice } from './tts/browser'
export type { BrowserTtsOptions, SynthesisLike, UtteranceLike, VoiceLike } from './tts/browser'
export {
  PiperTts,
  createPiperRuntime,
  phonemeIds,
  sentences,
  modelStore,
  download,
  DEFAULT_PIPER_VOICE,
  DEFAULT_PIPER_VOICES,
  DEFAULT_VOICE_BASE,
} from './tts/piper'
export type { ModelCache, ModelStore, OrtLike, PiperRuntime, PiperSynth, PiperTtsOptions, PiperVoiceConfig } from './tts/piper'
export { chooseTts, detectDevice, DeviceTts } from './tts/device'
export type { ChooseTtsOptions, DeviceClass } from './tts/device'

// voice
export { Emitter } from './voice/types'
export type {
  BargeInOptions,
  SpokenSegment,
  SttEngine,
  SttEvents,
  SttSupport,
  TtsEngine,
  TtsEvents,
  TtsKind,
  TtsProgress,
  TtsSpeakOptions,
  TtsStatus,
  TtsUtterance,
  VoiceCommonOptions,
  VoiceEngine,
  VoiceError,
  VoiceEvents,
  VoiceMode,
  VoiceState,
} from './voice/types'
export { BrowserVoiceEngine } from './voice/browser'
export type { BrowserVoiceOptions } from './voice/browser'
export { ServerVoiceEngine, evokaiEventOf, pipecatModules } from './voice/server'
export type { PipecatCallbacks, PipecatClientLike, PipecatModules, ServerVoiceOptions } from './voice/server'
export { Dictation } from './voice/dictation'
export type { DictationEvents, DictationOptions, DictationState } from './voice/dictation'
export { locales, localePack } from '../locales'
export type { LocaleCode, LocalePack, SpeechRules, VoiceText } from '../locales'
export type { LocaleInput } from './speech'

import type { EvokaiSession } from './session'
import { BrowserVoiceEngine, type BrowserVoiceOptions } from './voice/browser'
import { ServerVoiceEngine, type ServerVoiceOptions } from './voice/server'
import type { VoiceEngine } from './voice/types'

export type CreateVoiceOptions = Omit<BrowserVoiceOptions, 'session'> & Omit<ServerVoiceOptions, 'session'>

/** The conversation engine the session's token chose (`browser` or `server`). */
export function createVoiceEngine(
  session: EvokaiSession,
  opts: CreateVoiceOptions = {},
  kind: 'browser' | 'server' = session.voiceEngine,
): VoiceEngine {
  return kind === 'server' ? new ServerVoiceEngine({ ...opts, session }) : new BrowserVoiceEngine({ ...opts, session })
}
