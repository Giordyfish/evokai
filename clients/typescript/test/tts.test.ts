import { BrowserTts, type SynthesisLike, type UtteranceLike, type VoiceLike } from '../src/browser/tts/browser'
import { chooseTts, detectDevice, DeviceTts } from '../src/browser/tts/device'
import {
  createPiperRuntime,
  modelStore,
  phonemeIds,
  PiperTts,
  sentences,
  type ModelStore,
  type PiperRuntime,
  type PiperVoiceConfig,
} from '../src/browser/tts/piper'
import type { AudioSignal } from '../src/browser/audio/signals'
import type { TtsProgress, TtsStatus } from '../src/browser/voice/types'
import { FakeAudioContext, type FakeSource } from './helpers/audio'

const UA = {
  chromeDesktop: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36',
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
  android: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/140.0 Mobile Safari/537.36',
  ipad: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15',
}

describe('device-based selection', () => {
  test('detectDevice: phones, tablets and iPadOS are phones; desktops are desktops', () => {
    expect(detectDevice({ userAgent: UA.chromeDesktop, maxTouchPoints: 0, width: 1440 })).toBe('desktop')
    expect(detectDevice({ userAgent: UA.iphone })).toBe('phone')
    expect(detectDevice({ userAgent: UA.android })).toBe('phone')
    expect(detectDevice({ userAgent: UA.ipad, maxTouchPoints: 5 })).toBe('phone')
    expect(detectDevice({ userAgent: UA.chromeDesktop, maxTouchPoints: 10, width: 600 })).toBe('phone')
    expect(detectDevice({ userAgent: UA.chromeDesktop, mobileHint: true })).toBe('phone')
  })

  test('chooseTts: Piper on desktop for a language with a voice, the browser voice otherwise', () => {
    const desktopIt = chooseTts({ device: 'desktop', locale: 'it', browser: { synthesis: null } })
    expect(desktopIt).toBeInstanceOf(DeviceTts)
    expect(((desktopIt as DeviceTts).primary as PiperTts).voice).toBe('it_IT-serena-high')
    expect(((desktopIt as DeviceTts).primary as PiperTts).modelUrl).toBe('/evokai/voices/it_IT-serena-high.onnx')
    expect(chooseTts({ device: 'phone', locale: 'it' })).toBeInstanceOf(BrowserTts)
    expect(chooseTts({ device: 'desktop', locale: 'en' })).toBeInstanceOf(BrowserTts)
    expect(chooseTts({ device: 'desktop', locale: 'it', piper: false })).toBeInstanceOf(BrowserTts)
    const custom = chooseTts({ device: 'desktop', locale: 'en', piper: { voice: 'en_US-x-medium', modelUrl: '/v/x.onnx' } }) as DeviceTts
    expect((custom.primary as PiperTts).configUrl).toBe('/v/x.onnx.json')
  })
})

function synthesis(voices: VoiceLike[]) {
  const said: UtteranceLike[] = []
  const synth: SynthesisLike = { speak: (u) => said.push(u), cancel: () => undefined, getVoices: () => voices }
  class Utterance implements UtteranceLike {
    lang = ''
    rate = 1
    voice: VoiceLike | null = null
    onstart: (() => void) | null = null
    onend: (() => void) | null = null
    onerror: (() => void) | null = null
    onboundary: ((e: { name?: string; charIndex: number }) => void) | null = null
    constructor(public text: string) {}
  }
  return { synth, said, Utterance }
}

describe('BrowserTts', () => {
  test('estimates signals from the text and re-syncs on word boundaries', async () => {
    const { synth, said, Utterance } = synthesis([{ name: 'Alice', lang: 'it-IT', localService: true }])
    const tts = new BrowserTts({ locale: 'it', synthesis: synth, Utterance })
    const signals: AudioSignal[] = []
    const boundaries: number[] = []
    tts.on('signal', (s) => signals.push(s))
    tts.on('boundary', (b) => boundaries.push(b.charIndex))
    const u = tts.speak('Buongiorno a tutti.')
    expect(said[0]!.lang).toBe('it-IT')
    said[0]!.onstart?.()
    await vi.waitFor(() => expect(signals.some((s) => s.onset && s.estimated)).toBe(true))
    said[0]!.onboundary?.({ name: 'word', charIndex: 13 })
    expect(boundaries).toEqual([13])
    said[0]!.onend?.()
    expect(await u.done).toEqual({ cancelled: false })
    expect(signals.at(-1)).toMatchObject({ level: 0, onset: false })
    expect(tts.measurable).toBe(false)
  })

  test('only local voices: none for the language means unavailable', async () => {
    const { synth, Utterance } = synthesis([{ name: 'Cloud', lang: 'en-US', localService: false }])
    const tts = new BrowserTts({ synthesis: synth, Utterance })
    await tts.prepare()
    expect(tts.status.state).toBe('unavailable')
    expect(await tts.speak('Hello.').done).toEqual({ cancelled: true })
  })
})

// ── Piper ─────────────────────────────────────────────────────────────────────────────────────────────────────

const CONFIG: PiperVoiceConfig = {
  audio: { sample_rate: 22050 },
  espeak: { voice: 'it' },
  inference: { noise_scale: 0.6, length_scale: 1, noise_w: 0.4 },
  num_speakers: 1,
  phoneme_id_map: { _: [0], '^': [1], $: [2], ' ': [3], a: [14], k: [23], ˈ: [120], '.': [10] },
}

function memoryStore(): ModelStore & { data: Map<string, ArrayBuffer> } {
  const data = new Map<string, ArrayBuffer>()
  return { data, get: async (k) => data.get(k) ?? null, put: async (k, v) => void data.set(k, v) }
}

function modelServer(size: number) {
  const calls: string[] = []
  const fetchFn = async (url: string) => {
    calls.push(url)
    if (url.endsWith('.json')) return new Response(JSON.stringify(CONFIG))
    const chunk = 1000
    let sent = 0
    const body = new ReadableStream<Uint8Array>({
      pull(ctrl) {
        if (sent >= size) return ctrl.close()
        const n = Math.min(chunk, size - sent)
        sent += n
        ctrl.enqueue(new Uint8Array(n).fill(7))
      },
    })
    return new Response(body, { headers: { 'Content-Length': String(size) } })
  }
  return { fetchFn, calls }
}

const fakeRuntime = (): PiperRuntime & { loaded: number[] } => {
  const loaded: number[] = []
  return {
    loaded,
    async load(model) {
      loaded.push(model.byteLength)
      return { sampleRate: 22050, synthesize: async (text) => new Float32Array(text.length * 10).fill(0.2) }
    },
  }
}

describe('PiperTts', () => {
  test('first use downloads with progress, caches the model, then plays and measures the audio', async () => {
    const server = modelServer(3500)
    const store = memoryStore()
    const ctx = new FakeAudioContext()
    const runtime = fakeRuntime()
    const tts = new PiperTts({ fetch: server.fetchFn, store, loadRuntime: async () => runtime, audioContext: ctx as unknown as AudioContext })
    const progress: TtsProgress[] = []
    const states: TtsStatus['state'][] = []
    let ready = 0
    tts.on('progress', (p) => progress.push(p))
    tts.on('status', (s) => states.push(s.state))
    tts.on('ready', () => ready++)
    await tts.prepare()
    expect(server.calls.sort()).toEqual(['/evokai/voices/it_IT-serena-high.onnx', '/evokai/voices/it_IT-serena-high.onnx.json'])
    expect(progress.map((p) => p.loaded)).toEqual([1000, 2000, 3000, 3500])
    expect(progress.at(-1)).toEqual({ loaded: 3500, total: 3500, fraction: 1 })
    expect(states[0]).toBe('loading')
    expect(states.at(-1)).toBe('ready')
    expect(tts.status).toEqual({ state: 'ready', cached: false })
    expect(ready).toBe(1)
    expect(store.data.get('/evokai/voices/it_IT-serena-high.onnx')?.byteLength).toBe(3500)
    expect(runtime.loaded).toEqual([3500])

    const signals: AudioSignal[] = []
    tts.on('signal', (s) => signals.push(s))
    const started: string[] = []
    tts.on('start', (e) => started.push(e.text))
    const u = tts.speak('Ciao.')
    await vi.waitFor(() => expect(started).toEqual(['Ciao.']))
    const source = ctx.of<FakeSource>('source')[0]!
    await vi.waitFor(() => expect(signals.some((s) => s.source === 'agent' && !s.estimated && s.spectrum)).toBe(true))
    source.onended?.()
    expect(await u.done).toEqual({ cancelled: false })
  })

  test('a second engine reads the cached model: no download, `cached` status', async () => {
    const store = memoryStore()
    store.data.set('/evokai/voices/it_IT-serena-high.onnx', new ArrayBuffer(42))
    const server = modelServer(3500)
    const tts = new PiperTts({ fetch: server.fetchFn, store, loadRuntime: async () => fakeRuntime() })
    const progress: TtsProgress[] = []
    tts.on('progress', (p) => progress.push(p))
    await tts.prepare()
    expect(server.calls).toEqual(['/evokai/voices/it_IT-serena-high.onnx.json'])
    expect(progress).toEqual([])
    expect(tts.status).toEqual({ state: 'ready', cached: true })
  })

  test('a failed download is an error status, and prepare can be retried', async () => {
    let fail = true
    const tts = new PiperTts({
      store: memoryStore(),
      loadRuntime: async () => fakeRuntime(),
      fetch: async (u) => (fail && u.endsWith('.onnx') ? new Response('no', { status: 404 }) : modelServer(10).fetchFn(u)),
    })
    await expect(tts.prepare()).rejects.toThrow(/404/)
    expect(tts.status.state).toBe('error')
    fail = false
    await tts.prepare()
    expect(tts.status.state).toBe('ready')
  })

  test('cancel stops playback and drops the queue', async () => {
    const ctx = new FakeAudioContext()
    const tts = new PiperTts({ fetch: modelServer(10).fetchFn, store: memoryStore(), loadRuntime: async () => fakeRuntime(), audioContext: ctx as unknown as AudioContext })
    const a = tts.speak('Uno.')
    const b = tts.speak('Due.')
    await vi.waitFor(() => expect(ctx.of<FakeSource>('source')).toHaveLength(1))
    tts.cancel()
    expect(await a.done).toEqual({ cancelled: true })
    expect(await b.done).toEqual({ cancelled: true })
    expect(ctx.of<FakeSource>('source')[0]!.stopped).toHaveLength(1)
    expect(tts.speaking).toBe(false)
  })

  test('model stores: Cache Storage by default', async () => {
    const entries = new Map<string, Response>()
    const caches = { open: async () => ({ match: async (k: string) => entries.get(k), put: async (k: string, r: Response) => void entries.set(k, r) }) }
    const store = modelStore('cache-storage', { caches: caches as unknown as CacheStorage })
    expect(await store.get('/m.onnx')).toBeNull()
    await store.put('/m.onnx', new Uint8Array([1, 2]).buffer)
    expect(new Uint8Array((await store.get('/m.onnx'))!)).toEqual(new Uint8Array([1, 2]))
    expect(await modelStore('none').get('/m.onnx')).toBeNull()
  })
})

describe('Piper runtime', () => {
  test('phoneme ids: BOS, each phoneme then the pad, EOS; unknown phonemes skipped', () => {
    expect(phonemeIds('ˈka?', CONFIG.phoneme_id_map)).toEqual([1, 0, 120, 0, 23, 0, 14, 0, 2])
    expect(sentences('Ciao. Come stai? Bene')).toEqual([
      { text: 'Ciao.', end: '.' },
      { text: 'Come stai?', end: '?' },
      { text: 'Bene', end: '' },
    ])
  })

  test('createPiperRuntime feeds the model as Piper expects, sentence by sentence, one run at a time', async () => {
    const feeds: Array<Record<string, { type: string; data: unknown; dims: number[] }>> = []
    const ort = {
      Tensor: class {
        constructor(
          public type: string,
          public data: unknown,
          public dims: number[],
        ) {}
      },
      InferenceSession: {
        create: async () => ({
          run: async (f: Record<string, unknown>) => {
            feeds.push(f as never)
            return { output: { data: new Float32Array([0.1, 0.2]) } }
          },
        }),
      },
    }
    const phonemize = vi.fn(async (text: string) => [text.toLowerCase().includes('ka') ? 'ˈka' : 'a'])
    const synth = await createPiperRuntime({ ort, phonemize }).load(new ArrayBuffer(4), CONFIG)
    const audio = await synth.synthesize('Ka. A.', { rate: 2 })
    expect(audio).toEqual(new Float32Array([0.1, 0.2, 0.1, 0.2]))
    expect(phonemize).toHaveBeenCalledWith('Ka.', 'it')
    const f = feeds[0]!
    expect(f.input!.type).toBe('int64')
    expect(f.input!.dims).toEqual([1, 11])
    expect(Array.from(f.input!.data as BigInt64Array, Number)).toEqual([1, 0, 120, 0, 23, 0, 14, 0, 10, 0, 2])
    expect(Array.from(f.input_lengths!.data as BigInt64Array, Number)).toEqual([11])
    expect(Array.from(f.scales!.data as Float32Array)).toEqual([expect.closeTo(0.6), 0.5, expect.closeTo(0.4)])
    expect(f.sid).toBeUndefined()
  })
})

describe('DeviceTts', () => {
  test('speaks with the browser voice until Piper is ready, then with Piper', async () => {
    const { synth, said, Utterance } = synthesis([{ name: 'Alice', lang: 'it-IT', localService: true }])
    const ctx = new FakeAudioContext()
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const piper = new PiperTts({
      fetch: modelServer(10).fetchFn,
      store: memoryStore(),
      loadRuntime: async () => {
        await gate
        return fakeRuntime()
      },
      audioContext: ctx as unknown as AudioContext,
    })
    const tts = new DeviceTts(piper, new BrowserTts({ locale: 'it', synthesis: synth, Utterance, estimate: false }))
    const states: string[] = []
    tts.on('status', (s) => states.push(s.state))
    await tts.prepare()
    expect(tts.status.state).toBe('loading')
    tts.speak('Prima.')
    expect(said.map((u) => u.text)).toEqual(['Prima.'])
    release()
    await vi.waitFor(() => expect(tts.status.state).toBe('ready'))
    expect(tts.kind).toBe('piper')
    tts.speak('Dopo.')
    await vi.waitFor(() => expect(ctx.of<FakeSource>('source')).toHaveLength(1))
    expect(said).toHaveLength(1)
    expect(states).toContain('ready')
  })
})
