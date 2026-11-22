import { BrowserTts, type SynthesisLike, type UtteranceLike, type VoiceLike } from '../src/browser/tts/browser'
import { type ModelStore, type PiperRuntime, PiperTts } from '../src/browser/tts/piper'
import { FakeAudioContext, type FakeSource } from './helpers/audio'

function synthesis(voices: VoiceLike[]) {
  const said: UtteranceLike[] = []
  const synth: SynthesisLike = { speak: (u) => said.push(u), cancel: () => undefined, getVoices: () => voices }
  class Utterance implements UtteranceLike {
    lang = ''
    rate = 1
    pitch = 1
    voice: VoiceLike | null = null
    onstart: (() => void) | null = null
    onend: (() => void) | null = null
    onerror: (() => void) | null = null
    constructor(public text: string) {}
  }
  return { synth, said, Utterance }
}

describe('BrowserTts pitch', () => {
  const voices = [{ name: 'Google italiano', lang: 'it-IT', localService: true }]

  test('is 1 by default', () => {
    const { synth, said, Utterance } = synthesis(voices)
    new BrowserTts({ locale: 'it', synthesis: synth, Utterance }).speak('Ciao.')
    expect(said[0]!.pitch).toBe(1)
  })

  test('is set on every utterance, with the rate', () => {
    const { synth, said, Utterance } = synthesis(voices)
    const tts = new BrowserTts({ locale: 'it', synthesis: synth, Utterance, pitch: 1.3, rate: 1.05 })
    tts.speak('Ciao.')
    expect(said[0]!.pitch).toBe(1.3)
    expect(said[0]!.rate).toBe(1.05)
  })

  test('a preferred voice wins when present, only among the voices allowed', () => {
    const both = [
      { name: 'Alice', lang: 'it-IT', localService: true },
      { name: 'Google italiano', lang: 'it-IT', localService: false },
    ]
    const online = synthesis(both)
    new BrowserTts({ locale: 'it', synthesis: online.synth, Utterance: online.Utterance, preferVoice: 'google', allowOnlineVoices: true }).speak('Ciao.')
    expect(online.said[0]!.voice?.name).toBe('Google italiano')
    const local = synthesis(both)
    new BrowserTts({ locale: 'it', synthesis: local.synth, Utterance: local.Utterance, preferVoice: 'google' }).speak('Ciao.')
    expect(local.said[0]!.voice?.name).toBe('Alice')
  })
})

const CONFIG = {
  audio: { sample_rate: 22050 },
  espeak: { voice: 'it' },
  inference: { noise_scale: 0.6, length_scale: 1, noise_w: 0.4 },
  num_speakers: 1,
  phoneme_id_map: { _: [0], '^': [1], $: [2] },
}

function setup(pitchShift?: number) {
  const rates: number[] = []
  const runtime: PiperRuntime = {
    async load() {
      return {
        sampleRate: 22050,
        async synthesize(_text: string, opts?: { rate?: number }) {
          rates.push(opts?.rate ?? 1)
          return new Float32Array(100)
        },
      }
    },
  }
  const store: ModelStore = { get: async () => new ArrayBuffer(8), put: async () => undefined }
  const fetchFn = async () => new Response(JSON.stringify(CONFIG))
  const ctx = new FakeAudioContext()
  const tts = new PiperTts({
    fetch: fetchFn,
    store,
    loadRuntime: async () => runtime,
    audioContext: ctx as unknown as AudioContext,
    ...(pitchShift ? { pitchShift } : {}),
  })
  return { tts, ctx, rates }
}

describe('PiperTts pitchShift', () => {
  test('stretches the synthesis and plays it faster by the same factor (higher, same length)', async () => {
    const { tts, ctx, rates } = setup(1.15)
    tts.speak('Ciao.')
    await vi.waitFor(() => expect(ctx.of<FakeSource>('source').length).toBe(1))
    const source = ctx.of<FakeSource>('source')[0]! as FakeSource & { playbackRate?: { value: number } }
    // length_scale is divided by the rate: a rate of 1 / 1.15 multiplies it by 1.15.
    expect(rates[0]).toBeCloseTo(1 / 1.15, 5)
    expect(source.playbackRate?.value).toBeCloseTo(1.15, 5)
  })

  test('is 1 by default: nothing changes', async () => {
    const { tts, ctx, rates } = setup()
    tts.speak('Ciao.')
    await vi.waitFor(() => expect(ctx.of<FakeSource>('source').length).toBe(1))
    const source = ctx.of<FakeSource>('source')[0]! as FakeSource & { playbackRate?: { value: number } }
    expect(rates[0]).toBe(1)
    expect(source.playbackRate?.value ?? 1).toBe(1)
  })
})
