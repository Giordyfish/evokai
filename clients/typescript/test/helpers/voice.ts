/** Fakes for the voice engines: on-device recognition, local speech synthesis, and a ready engine. */
import { EvokaiSession } from '../../src/browser/session'
import { BrowserVoiceEngine, type BrowserVoiceOptions } from '../../src/browser/voice/browser'
import {
  OnDeviceStt,
  type OnDeviceAvailability,
  type RecognitionResultEventLike,
  type SpeechRecognitionLike,
  type SpeechRecognitionStatic,
} from '../../src/browser/stt/on-device'
import { BrowserTts, type SynthesisLike, type UtteranceLike, type VoiceLike } from '../../src/browser/tts/browser'
import { Microphone } from '../../src/browser/mic/microphone'
import type { EvokaiEvent, TurnPhase } from '../../src/core/types'
import { json, mockFetch, sse } from './fetch'


export function fakeRecognition(availability: OnDeviceAvailability | null, installs = true) {
  const instances: FakeRec[] = []
  const calls: Array<{ fn: string; langs: string[]; processLocally: boolean }> = []
  let state = availability
  class FakeRec implements SpeechRecognitionLike {
    lang = ''
    continuous = false
    interimResults = false
    processLocally?: boolean
    started = false
    onresult: ((e: RecognitionResultEventLike) => void) | null = null
    onerror: ((e: { error: string }) => void) | null = null
    onend: (() => void) | null = null
    onspeechstart: (() => void) | null = null
    constructor() {
      instances.push(this)
    }
    start() {
      this.started = true
    }
    stop() {
      this.started = false
      this.onend?.()
    }
    abort() {
      this.started = false
    }
    /** Simulate a result. */
    say(text: string, isFinal: boolean) {
      const res = Object.assign([{ transcript: text }], { isFinal })
      this.onresult?.({ resultIndex: 0, results: [res] })
    }
  }
  const C = FakeRec as unknown as SpeechRecognitionStatic & { prototype: FakeRec }
  if (availability !== null) {
    C.available = async (o) => {
      calls.push({ fn: 'available', ...o })
      return state as OnDeviceAvailability
    }
    C.install = async (o) => {
      calls.push({ fn: 'install', ...o })
      if (installs) state = 'available'
      return installs
    }
  }
  return { C, instances, calls }
}

export function fakeSynthesis(voices: VoiceLike[]) {
  const spoken: string[] = []
  /** Every utterance handed to the synthesiser (to fire its word boundaries). */
  const utterances: UtteranceLike[] = []
  let current: UtteranceLike | null = null
  const queue: UtteranceLike[] = []
  const synth: SynthesisLike & { finish(): void; cancelled: number } = {
    cancelled: 0,
    getVoices: () => voices,
    speak(u) {
      spoken.push(u.text)
      utterances.push(u)
      if (current) queue.push(u)
      else {
        current = u
        u.onstart?.()
      }
    },
    cancel() {
      this.cancelled++
      current = null
      queue.length = 0
    },
    /** The utterance being spoken ends. */
    finish() {
      const u = current
      current = queue.shift() ?? null
      current?.onstart?.()
      u?.onend?.()
    },
  }
  class Utterance implements UtteranceLike {
    onstart: (() => void) | null = null
    lang = ''
    rate = 1
    voice: VoiceLike | null = null
    onend: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor(public text: string) {}
  }
  return { synth, spoken, utterances, Utterance }
}

export const EN_LOCAL: VoiceLike = { name: 'Ava (Natural)', lang: 'en-US', localService: true }
export const EN_CLOUD: VoiceLike = { name: 'Cloud English', lang: 'en-US', localService: false }

export function setup(
  opts: { availability?: OnDeviceAvailability | null; voices?: VoiceLike[]; installs?: boolean; engine?: Partial<BrowserVoiceOptions> } = {},
) {
  const rec = fakeRecognition(opts.availability === undefined ? 'available' : opts.availability, opts.installs)
  const tts = fakeSynthesis(opts.voices ?? [EN_CLOUD, EN_LOCAL])
  const m = mockFetch({
    'POST /agent/v1/session/chat': () => sse([]),
    'POST /agent/v1/session/turns/t1/cancel': () => json({ ok: true, cancelled: true }),
  })
  const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: 'tok', fetch: m.fetch })
  const engine = new BrowserVoiceEngine({
    session,
    stt: new OnDeviceStt({ recognition: rec.C, restartMs: 0 }),
    tts: new BrowserTts({ synthesis: tts.synth, Utterance: tts.Utterance }),
    microphone: new Microphone({ mediaDevices: null }),
    meterMicrophone: false,
    micSound: 'none',
    ...opts.engine,
  })
  const phases: TurnPhase[] = []
  session.on('phase', (e) => phases.push(e.phase))
  return { rec, tts, m, session, engine, phases }
}

export const ingestAll = (s: EvokaiSession, events: EvokaiEvent[]) => events.forEach((e) => s.ingest(e))

