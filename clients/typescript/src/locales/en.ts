/** English (the default). */
import type { LocalePack } from './types'

export const en: LocalePack = {
  code: 'en',
  lang: 'en-US',
  speech: {
    yes: ['yes', 'sure', 'confirm', 'confirmed', 'ok', 'okay', 'go ahead', 'proceed', 'do it', 'right'],
    no: ['no', 'cancel', 'never mind', 'stop', "don't", 'do not', 'nothing'],
    ordinals: { one: 1, first: 1, two: 2, second: 2, three: 3, third: 3, four: 4, fourth: 4, five: 5, fifth: 5 },
    sources: ['sources', 'source', 'references'],
    abbreviations: ['e.g', 'i.e', 'etc', 'vs', 'mr', 'mrs', 'ms', 'dr', 'prof', 'st', 'no', 'fig', 'approx'],
    redactions: {
      taxCode: 'the tax code',
      iban: 'the IBAN',
      vat: 'the VAT number',
      email: 'the email address',
      phone: 'the phone number',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Preparing ${p}’s voice: this only happens the first time` : 'Preparing the voice: this only happens the first time'),
    voiceReady: (p) => (p ? `${p}’s voice is ready` : 'The voice is ready'),
    recognitionUnsupported: 'On-device speech recognition is not available in this browser',
    recognitionDownload: 'The first use downloads on-device speech recognition',
    micDenied: 'The microphone is blocked: allow it in the browser settings',
    micMissing: 'No microphone connected',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'This changes data',
      'confirm.why.rule': 'A rule of this assistant asks for your confirmation',
      'confirm.why.policy': 'The security policy asks for your confirmation',
    },
    rewriteErrors: {
      rewrite_after_write: 'This message can no longer be edited: its answer already changed data.',
      rewrite_not_last: 'Only your last message can be edited.',
      rewrite_nothing: 'There is no message to edit yet.',
      rewrite_running: 'Wait for the answer, or stop it, before editing your message.',
    },
  },
  micSounds: {
    goccia: { name: 'Drop', description: 'Two short sine notes, B–E rising to open, E–B falling to close.' },
    classico: { name: 'Classic', description: 'Two soft tones with a long tail (an audio file).' },
    vetro: { name: 'Glass', description: 'A short crystalline bell, higher when opening.' },
    respiro: { name: 'Breath', description: 'A filtered breath sweeping up or down: no notes, very discreet.' },
    cristallo: { name: 'Crystal', description: 'A fast A-major arpeggio, rising or falling.' },
    clic: { name: 'Soft click', description: 'A tiny gliding tick, almost tactile.' },
  },
}
