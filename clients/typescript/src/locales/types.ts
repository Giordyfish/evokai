/**
 * A locale pack: the words the client needs for one language. Speech rules mirror the server's packs
 * (`src/evokai/locales/<code>.json`), so a spoken "yes" or "the second" means the same on both sides.
 */
import type { MicSoundName } from '../browser/audio/mic-sounds'

export type LocaleCode = 'en' | 'it'

export interface SpeechRules {
  /** Spoken confirmations, matched at the start of a phrase (normalised: lower case, no accents). */
  yes: string[]
  /** Spoken refusals, matched at the start of a phrase. */
  no: string[]
  /** Ordinal and number words → 1–5 ("the second" picks option 2). */
  ordinals: Record<string, number>
  /** Headings that start a sources section, never read aloud. */
  sources: string[]
  /** Abbreviations whose full stop does not end a sentence. */
  abbreviations: string[]
  /** Words spoken instead of personal identifiers. */
  redactions: {
    taxCode: string
    iban: string
    vat: string
    email: string
    phone: string
  }
}

/** The lines an application may show for voice states. */
export interface VoiceText {
  /** While the in-browser voice downloads (first use only). `persona`: the agent's name, if you show one. */
  preparingVoice: (persona?: string) => string
  /** The voice is ready. */
  voiceReady: (persona?: string) => string
  /** On-device recognition is missing. */
  recognitionUnsupported: string
  /** The language pack downloads at first use. */
  recognitionDownload: string
  /** The microphone was refused. */
  micDenied: string
  /** No microphone is connected. */
  micMissing: string
}

/** Texts about the conversation itself: why a confirmation is asked, why a rewrite was refused. */
export interface ChatText {
  /** Why a call waits for the person, by `ConfirmReason.key`; a rule's own label is shown when it has one. */
  confirmReasons: { 'confirm.why.writes': string; 'confirm.why.rule': string; 'confirm.why.policy': string }
  /** The refusals of `EvokaiSession.rewrite`, by error code (the service also sends its own message). */
  rewriteErrors: {
    rewrite_after_write: string
    rewrite_not_last: string
    rewrite_nothing: string
    rewrite_running: string
  }
}

export interface LocalePack {
  code: LocaleCode | (string & {})
  /** BCP 47 language for recognition and synthesis. */
  lang: string
  speech: SpeechRules
  voiceText: VoiceText
  /** Display names and descriptions of the built-in microphone sounds (the identifiers never change). */
  micSounds: Record<MicSoundName, { name: string; description: string }>
  /** Conversation texts (optional in packs of your own: English is used when missing). */
  chatText?: ChatText
}
