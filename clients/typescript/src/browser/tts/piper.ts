/**
 * A neural voice in the browser (Piper) for desktops. The model (~114 MB for `it_IT-serena-high`) is downloaded on
 * first use from a path your application serves (never a third-party CDN by default), cached (Cache Storage or OPFS),
 * and reported with `progress` and `ready` events. The audio it plays is measured like any other source.
 *
 * The runtime (ONNX inference + espeak-ng phonemes) is an optional dynamic import and never part of the main entry:
 * espeak-ng is GPL-3.0-or-later (see API.md, "Licences").
 */
import { sharedContext } from '../audio/mic-sounds'
import { meterAnalyser, AudioSignals } from '../audio/signals'
import { Emitter, type TtsEngine, type TtsEvents, type TtsProgress, type TtsSpeakOptions, type TtsStatus, type TtsUtterance } from '../voice/types'

/** Default voices by language; a language without one uses the browser voice. */
export const DEFAULT_PIPER_VOICES: Record<string, string> = { it: 'it_IT-serena-high' }
export const DEFAULT_PIPER_VOICE = 'it_IT-serena-high'
/** Default asset path: your application serves `<voice>.onnx` and `<voice>.onnx.json` here. */
export const DEFAULT_VOICE_BASE = '/evokai/voices/'
const CACHE_NAME = 'evokai-voices-v1'

/** The parts of a Piper voice config (`<voice>.onnx.json`) the runtime uses. */
export interface PiperVoiceConfig {
  audio: { sample_rate: number }
  espeak?: { voice: string }
  inference?: { noise_scale?: number; length_scale?: number; noise_w?: number }
  phoneme_type?: string
  num_speakers?: number
  phoneme_id_map: Record<string, number[]>
}

export interface PiperSynth {
  readonly sampleRate: number
  /** Audio samples (mono, -1…1) for a text. */
  synthesize(text: string, opts?: { rate?: number }): Promise<Float32Array>
}

export interface PiperRuntime {
  load(model: ArrayBuffer, config: PiperVoiceConfig): Promise<PiperSynth>
}

/** The parts of `onnxruntime-web` the runtime uses. */
export interface OrtLike {
  InferenceSession: { create(model: ArrayBuffer | Uint8Array, opts?: unknown): Promise<{ run(feeds: Record<string, unknown>): Promise<Record<string, { data: unknown }>> }> }
  Tensor: new (type: string, data: unknown, dims: number[]) => unknown
}

/** Phonemes (espeak IPA) to Piper ids: BOS, then each phoneme followed by the pad, then EOS. Unknown phonemes are skipped. */
export function phonemeIds(phonemes: string, map: Record<string, number[]>): number[] {
  const pad = map._ ?? []
  const ids = [...(map['^'] ?? []), ...pad]
  for (const ch of phonemes.normalize('NFD')) {
    const id = map[ch]
    if (!id) continue
    ids.push(...id, ...pad)
  }
  ids.push(...(map.$ ?? []))
  return ids
}

/** Split text into sentences, keeping each one's final punctuation (Piper speaks sentence by sentence). */
export function sentences(text: string): Array<{ text: string; end: string }> {
  const out: Array<{ text: string; end: string }> = []
  for (const m of text.matchAll(/[^.!?…]+([.!?…]+|$)/g)) {
    const t = m[0].trim()
    if (t) out.push({ text: t, end: (m[1] ?? '').slice(0, 1) === '…' ? '.' : (m[1] ?? '').slice(0, 1) })
  }
  return out
}

/**
 * A Piper runtime from `onnxruntime-web` and an espeak-ng phonemizer (`phonemize(text, language)` → IPA strings, as
 * the `phonemizer` package exports). Keep the imports in your application's code (they are optional dependencies).
 */
export function createPiperRuntime(mods: { ort: OrtLike; phonemize: (text: string, language?: string) => Promise<string[]> }): PiperRuntime {
  return {
    async load(model, config) {
      const session = await mods.ort.InferenceSession.create(new Uint8Array(model))
      const lang = config.espeak?.voice ?? 'en-us'
      const inf = config.inference ?? {}
      const multi = (config.num_speakers ?? 1) > 1
      let chain: Promise<unknown> = Promise.resolve()
      const run = async (ids: number[], rate: number): Promise<Float32Array> => {
        const feeds: Record<string, unknown> = {
          input: new mods.ort.Tensor('int64', BigInt64Array.from(ids.map(BigInt)), [1, ids.length]),
          input_lengths: new mods.ort.Tensor('int64', BigInt64Array.from([BigInt(ids.length)]), [1]),
          scales: new mods.ort.Tensor('float32', Float32Array.from([inf.noise_scale ?? 0.667, (inf.length_scale ?? 1) / rate, inf.noise_w ?? 0.8]), [3]),
        }
        if (multi) feeds.sid = new mods.ort.Tensor('int64', BigInt64Array.from([0n]), [1])
        const out = await session.run(feeds)
        return (out.output?.data as Float32Array) ?? new Float32Array(0)
      }
      return {
        sampleRate: config.audio.sample_rate,
        synthesize(text, opts = {}) {
          // One inference at a time per session.
          const job = chain.then(async () => {
            const parts: Float32Array[] = []
            for (const s of sentences(text)) {
              const phonemes = (await mods.phonemize(s.text, lang)).join(' ') + s.end
              parts.push(await run(phonemeIds(phonemes, config.phoneme_id_map), opts.rate ?? 1))
            }
            const total = parts.reduce((n, p) => n + p.length, 0)
            const all = new Float32Array(total)
            let at = 0
            for (const p of parts) {
              all.set(p, at)
              at += p.length
            }
            return all
          })
          chain = job.catch(() => undefined)
          return job
        },
      }
    },
  }
}

/**
 * The default runtime loader: imports `onnxruntime-web` and `phonemizer` by computed name, so applications without
 * Piper build without them. With a bundler, pass `loadRuntime: async () => createPiperRuntime({ ort: await
 * import('onnxruntime-web'), phonemize: (await import('phonemizer')).phonemize })`.
 */
async function importRuntime(): Promise<PiperRuntime> {
  const names = ['onnxruntime-web', 'phonemizer']
  try {
    const [ort, ph] = (await Promise.all(names.map((n) => import(/* @vite-ignore */ /* webpackIgnore: true */ n)))) as [OrtLike, { phonemize: (t: string, l?: string) => Promise<string[]> }]
    return createPiperRuntime({ ort, phonemize: ph.phonemize })
  } catch {
    throw new Error('PiperTts needs onnxruntime-web and phonemizer: install them and pass loadRuntime')
  }
}

// ── storage ─────────────────────────────────────────────────────────────────────────────────────────────────

export type ModelCache = 'cache-storage' | 'opfs' | 'none'

export interface ModelStore {
  get(key: string): Promise<ArrayBuffer | null>
  put(key: string, data: ArrayBuffer): Promise<void>
}

/** Cache Storage (default) or the origin-private file system; `none` keeps nothing. */
export function modelStore(kind: ModelCache, env: { caches?: CacheStorage; storage?: StorageManager } = {}): ModelStore {
  if (kind === 'cache-storage') {
    const cs = env.caches ?? (typeof caches !== 'undefined' ? caches : undefined)
    if (cs) {
      return {
        async get(key) {
          const r = await (await cs.open(CACHE_NAME)).match(key)
          return r ? r.arrayBuffer() : null
        },
        async put(key, data) {
          await (await cs.open(CACHE_NAME)).put(key, new Response(data))
        },
      }
    }
  }
  if (kind === 'opfs') {
    const st = env.storage ?? (typeof navigator !== 'undefined' ? navigator.storage : undefined)
    if (st?.getDirectory) {
      const file = (key: string) => key.replace(/[^a-zA-Z0-9._-]/g, '_')
      return {
        async get(key) {
          try {
            const dir = await st.getDirectory()
            const h = await dir.getFileHandle(file(key))
            return (await h.getFile()).arrayBuffer()
          } catch {
            return null
          }
        },
        async put(key, data) {
          const dir = await st.getDirectory()
          const h = await dir.getFileHandle(file(key), { create: true })
          const w = await h.createWritable()
          await w.write(data)
          await w.close()
        },
      }
    }
  }
  return { get: async () => null, put: async () => undefined }
}

/** Download with progress (`total` from Content-Length when the server sends it). */
export async function download(fetchFn: (u: string) => Promise<Response>, url: string, onProgress: (p: TtsProgress) => void): Promise<ArrayBuffer> {
  const r = await fetchFn(url)
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`)
  const total = Number(r.headers.get('content-length')) || null
  if (!r.body) {
    const b = await r.arrayBuffer()
    onProgress({ loaded: b.byteLength, total: b.byteLength, fraction: 1 })
    return b
  }
  const reader = r.body.getReader()
  const chunks: Uint8Array[] = []
  let loaded = 0
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    chunks.push(value)
    loaded += value.byteLength
    onProgress({ loaded, total, fraction: total ? Math.min(1, loaded / total) : null })
  }
  const all = new Uint8Array(loaded)
  let at = 0
  for (const c of chunks) {
    all.set(c, at)
    at += c.byteLength
  }
  return all.buffer
}

// ── engine ──────────────────────────────────────────────────────────────────────────────────────────────────

export interface PiperTtsOptions {
  /** Voice name (default `it_IT-serena-high`). */
  voice?: string
  /** Model URL (default `${DEFAULT_VOICE_BASE}${voice}.onnx`). */
  modelUrl?: string
  /** Config URL (default `${modelUrl}.json`). */
  configUrl?: string
  /** Where the downloaded model is kept (default `cache-storage`). */
  cache?: ModelCache
  /** A store of your own (tests, IndexedDB…); overrides `cache`. */
  store?: ModelStore
  /** Load the runtime (default: dynamic import of `onnxruntime-web` and `phonemizer`). */
  loadRuntime?: () => Promise<PiperRuntime>
  /** The audio context for playback and measurement (default: created on first use). */
  audioContext?: AudioContext
  /** Default rate. */
  rate?: number
  /**
   * A higher (above 1) or lower voice at the same pace (default 1): the synthesis is stretched by this factor
   * (`length_scale`) and played back faster by the same factor (`playbackRate`), so the length stays and the pitch
   * moves. 1.15 makes a small, bright voice.
   */
  pitchShift?: number
  fetch?: (input: string) => Promise<Response>
}

interface Item {
  text: string
  rate: number
  audio: Promise<Float32Array>
  resolve: (v: { cancelled: boolean }) => void
  cancelled: boolean
}

export class PiperTts implements TtsEngine {
  readonly kind = 'piper' as const
  readonly measurable = true
  readonly voice: string
  readonly modelUrl: string
  readonly configUrl: string
  private readonly events = new Emitter<TtsEvents>()
  private readonly opts: PiperTtsOptions
  private current: TtsStatus = { state: 'idle' }
  private loading: Promise<PiperSynth> | null = null
  private synth: PiperSynth | null = null
  private ctx: AudioContext | null
  private queue: Item[] = []
  private playing: { item: Item; source: AudioBufferSourceNode; stopMeter: () => void } | null = null
  private readonly signals = new AudioSignals()

  constructor(opts: PiperTtsOptions = {}) {
    this.opts = opts
    this.voice = opts.voice ?? DEFAULT_PIPER_VOICE
    this.modelUrl = opts.modelUrl ?? `${DEFAULT_VOICE_BASE}${this.voice}.onnx`
    this.configUrl = opts.configUrl ?? `${this.modelUrl}.json`
    this.ctx = opts.audioContext ?? null
    this.signals.subscribe((s) => this.events.emit('signal', s))
  }

  get status(): TtsStatus {
    return this.current
  }

  get speaking(): boolean {
    return this.playing !== null || this.queue.length > 0
  }

  on<K extends keyof TtsEvents>(event: K, fn: (value: TtsEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  private setStatus(s: TtsStatus): void {
    this.current = s
    this.events.emit('status', s)
  }

  /** Download (first use) or read from the cache, then load the runtime. Safe to call more than once. */
  prepare(): Promise<void> {
    return this.load().then(() => undefined)
  }

  private load(): Promise<PiperSynth> {
    if (this.loading) return this.loading
    const fetchFn = this.opts.fetch ?? ((u: string) => fetch(u))
    const store = this.opts.store ?? modelStore(this.opts.cache ?? 'cache-storage')
    this.setStatus({ state: 'loading' })
    this.loading = (async () => {
      const [runtime, config] = await Promise.all([
        (this.opts.loadRuntime ?? importRuntime)(),
        fetchFn(this.configUrl).then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status} for ${this.configUrl}`)
          return r.json() as Promise<PiperVoiceConfig>
        }),
      ])
      let model = await store.get(this.modelUrl)
      const cached = model !== null
      if (!model) {
        model = await download(fetchFn, this.modelUrl, (p) => {
          this.events.emit('progress', p)
          this.setStatus({ state: 'loading', progress: p })
        })
        await store.put(this.modelUrl, model).catch(() => undefined)
      }
      const synth = await runtime.load(model, config)
      this.synth = synth
      this.setStatus({ state: 'ready', cached })
      this.events.emit('ready', null)
      return synth
    })()
    this.loading.catch((e: unknown) => {
      this.loading = null
      this.setStatus({ state: 'error', error: { code: 'tts', message: e instanceof Error ? e.message : String(e) } })
    })
    return this.loading
  }

  speak(text: string, opts: TtsSpeakOptions = {}): TtsUtterance {
    let resolve!: (v: { cancelled: boolean }) => void
    const done = new Promise<{ cancelled: boolean }>((r) => (resolve = r))
    const rate = opts.rate ?? this.opts.rate ?? 1
    // Synthesis starts now (in order), playback follows the queue.
    const shift = this.opts.pitchShift ?? 1
    const audio = this.load().then((s) => s.synthesize(text, { rate: rate / shift }))
    audio.catch(() => undefined)
    const item: Item = { text, rate, audio, resolve, cancelled: false }
    this.queue.push(item)
    opts.signal?.addEventListener('abort', () => this.drop(item))
    if (!this.playing) void this.next()
    return { text, done, cancel: () => this.drop(item) }
  }

  private audioContext(): AudioContext | null {
    if (this.ctx) return this.ctx
    // The page's shared context: unlockAudio() resumes it from the gesture that starts voice mode, so a sentence
    // played seconds later is heard (a context created outside a gesture stays suspended in Chrome and Safari).
    this.ctx = sharedContext()
    return this.ctx
  }

  private pumping = false

  /** Play the head of the queue (one pump at a time). */
  private async next(): Promise<void> {
    if (this.pumping) return
    this.pumping = true
    try {
      await this.pump()
    } finally {
      this.pumping = false
    }
    if (!this.playing && this.queue.length) void this.next()
  }

  private async pump(): Promise<void> {
    const item = this.queue[0]
    if (!item || this.playing) return
    let samples: Float32Array
    try {
      samples = await item.audio
    } catch (e) {
      this.queue.shift()
      item.resolve({ cancelled: true })
      this.events.emit('end', { text: item.text, cancelled: true })
      this.setStatus({ state: 'error', error: { code: 'tts', message: e instanceof Error ? e.message : String(e) } })
      return
    }
    if (item.cancelled || this.queue[0] !== item) return
    const ctx = this.audioContext()
    if (!ctx || !this.synth) {
      this.queue.shift()
      item.resolve({ cancelled: true })
      return
    }
    if (ctx.state === 'suspended') await ctx.resume().catch(() => undefined)
    const buf = ctx.createBuffer(1, Math.max(1, samples.length), this.synth.sampleRate)
    buf.getChannelData(0).set(samples)
    const source = ctx.createBufferSource()
    const an = ctx.createAnalyser()
    an.fftSize = 1024
    an.smoothingTimeConstant = 0.5
    source.buffer = buf
    const shift = this.opts.pitchShift ?? 1
    if (shift !== 1) source.playbackRate.value = shift
    source.connect(an)
    an.connect(ctx.destination)
    const stopMeter = meterAnalyser(an, 'agent', this.signals)
    this.playing = { item, source, stopMeter }
    source.onended = () => {
      if (this.playing?.source !== source) return
      this.finish(false)
      void this.next()
    }
    this.events.emit('start', { text: item.text })
    source.start()
  }

  private finish(cancelled: boolean): void {
    const p = this.playing
    if (!p) return
    this.playing = null
    p.stopMeter()
    if (this.queue[0] === p.item) this.queue.shift()
    p.item.resolve({ cancelled })
    this.events.emit('end', { text: p.item.text, cancelled })
  }

  private drop(item: Item): void {
    item.cancelled = true
    if (this.playing?.item === item) {
      const s = this.playing.source
      this.finish(true)
      try {
        s.stop()
      } catch {
        /* not started */
      }
      void this.next()
      return
    }
    const i = this.queue.indexOf(item)
    if (i >= 0) {
      this.queue.splice(i, 1)
      item.resolve({ cancelled: true })
    }
    if (i === 0) void this.next()
  }

  cancel(): void {
    const rest = this.queue.filter((q) => q !== this.playing?.item)
    this.queue = this.playing ? [this.playing.item] : []
    for (const q of rest) {
      q.cancelled = true
      q.resolve({ cancelled: true })
    }
    if (this.playing) this.drop(this.playing.item)
  }
}
