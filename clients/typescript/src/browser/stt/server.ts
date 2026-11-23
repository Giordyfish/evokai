/**
 * Speech recognition by the server, as the user speaks (`WS /v1/session/transcribe/stream`), with the session's
 * engine. The session token travels in the WebSocket subprotocol, never in the URL. The microphone is captured with
 * an AudioWorklet, turned into 16 kHz 16-bit mono PCM and sent as binary messages; the server answers `interim` and
 * `final` transcripts.
 */
import { transcribeSocketUrl, type SpeechSession } from '../speech-server'
import { Emitter, type SttEngine, type SttEvents, type SttSupport } from '../voice/types'

export const STREAM_RATE = 16000

/** The parts of `WebSocket` used (a fake in tests). */
export interface SocketLike {
  binaryType: string
  readyState: number
  onopen: ((e: unknown) => void) | null
  onmessage: ((e: { data: unknown }) => void) | null
  onclose: ((e: { code: number; reason?: string }) => void) | null
  onerror: ((e: unknown) => void) | null
  send(data: string | ArrayBufferLike | ArrayBufferView): void
  close(code?: number): void
}

/** Turns captured audio into PCM chunks: `start(onChunk)` resolves once capturing, `stop()` releases it. */
export interface PcmSource {
  start(onChunk: (pcm: Int16Array) => void, track?: MediaStreamTrack | null): Promise<void>
  stop(): void
}

export interface ServerSttOptions {
  session: SpeechSession
  /** BCP 47 language (default: the server picks the session's). */
  lang?: string
  /** Voice activity detection on the server (seconds and thresholds). */
  vad?: { confidence?: number; startSecs?: number; stopSecs?: number; minVolume?: number }
  /** For tests and custom transports. */
  socket?: (url: string, protocols: string[]) => SocketLike
  source?: PcmSource
}

/** Downsample float audio to 16-bit PCM at `to` Hz (simple decimation with averaging). */
export function toPcm16(input: Float32Array, from: number, to = STREAM_RATE): Int16Array {
  const ratio = from / to
  const out = new Int16Array(Math.floor(input.length / ratio))
  for (let i = 0; i < out.length; i++) {
    const a = Math.floor(i * ratio)
    const b = Math.min(input.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = a; j < b; j++) sum += input[j] ?? 0
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)))
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff
  }
  return out
}

const WORKLET = `registerProcessor('evokai-pcm', class extends AudioWorkletProcessor {
  process(inputs) { const c = inputs[0] && inputs[0][0]; if (c) this.port.postMessage(c.slice(0)); return true }
})`

/** The microphone through an AudioWorklet (browsers). */
export class MicrophonePcm implements PcmSource {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null

  async start(onChunk: (pcm: Int16Array) => void, track?: MediaStreamTrack | null): Promise<void> {
    this.stream = track ? new MediaStream([track]) : await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
    // At 16 kHz the browser resamples the microphone itself: no samples are dropped between chunks.
    const ctx = (this.ctx = new AudioContext({ sampleRate: STREAM_RATE }))
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' }))
    try {
      await ctx.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    const node = new AudioWorkletNode(ctx, 'evokai-pcm')
    node.port.onmessage = (e: MessageEvent<Float32Array>) => onChunk(toPcm16(e.data, ctx.sampleRate))
    ctx.createMediaStreamSource(this.stream).connect(node)
  }

  stop(): void {
    void this.ctx?.close()
    this.ctx = null
    if (this.stream) for (const t of this.stream.getTracks()) t.stop()
    this.stream = null
  }
}

export class ServerStt implements SttEngine {
  readonly kind = 'server' as const
  readonly lang: string
  private readonly events = new Emitter<SttEvents>()
  private readonly opts: ServerSttOptions
  private ws: SocketLike | null = null
  private source: PcmSource | null = null
  private running_ = false
  private continuous = false

  constructor(opts: ServerSttOptions) {
    this.opts = opts
    this.lang = opts.lang ?? ''
  }

  get running(): boolean {
    return this.running_
  }

  async support(): Promise<SttSupport> {
    return typeof WebSocket !== 'undefined' || this.opts.socket ? 'ready' : 'unsupported'
  }

  async install(): Promise<boolean> {
    return true
  }

  on<K extends keyof SttEvents>(event: K, fn: (value: SttEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  async start(opts: { continuous: boolean; track?: MediaStreamTrack | null }): Promise<void> {
    if (this.running_) return
    this.continuous = opts.continuous
    const token = await this.opts.session.getAccessToken()
    const url = transcribeSocketUrl(this.opts.session)
    const protocols = ['evokai.v1', `evokai.token.${token}`]
    const ws = (this.ws = this.opts.socket ? this.opts.socket(url, protocols) : (new WebSocket(url, protocols) as unknown as SocketLike))
    ws.binaryType = 'arraybuffer'
    this.running_ = true
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve()
      ws.onerror = () => reject(new Error('connect'))
      ws.onclose = (e) => reject(new Error(e.reason || `closed ${e.code}`))
    }).catch((e: Error) => {
      this.running_ = false
      this.events.emit('error', { code: 'connect', message: e.message })
      throw e
    })
    ws.onmessage = (e) => this.onMessage(e.data)
    ws.onclose = () => this.finish()
    ws.onerror = () => this.events.emit('error', { code: 'connect', message: 'The recognition connection failed' })
    ws.send(JSON.stringify({ type: 'start', sampleRate: STREAM_RATE, ...(this.lang ? { language: this.lang } : {}), ...(this.opts.vad ? { vad: this.opts.vad } : {}) }))
    const source = (this.source = this.opts.source ?? new MicrophonePcm())
    try {
      await source.start((pcm) => {
        if (this.ws && this.ws.readyState === 1) this.ws.send(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength))
      }, opts.track)
    } catch (e) {
      // No microphone (refused, missing): free the server's socket, or every retry would find it taken.
      const message = e instanceof Error ? e.message : String(e)
      this.events.emit('error', { code: message === 'not-allowed' || /denied|allowed/i.test(message) ? 'not-allowed' : 'no-device', message })
      this.abort()
      throw e
    }
  }

  private onMessage(data: unknown): void {
    if (typeof data !== 'string') return
    let msg: { type?: string; text?: string; code?: string; message?: string }
    try {
      msg = JSON.parse(data) as typeof msg
    } catch {
      return
    }
    if (msg.type === 'interim') this.events.emit('interim', msg.text ?? '')
    else if (msg.type === 'final' && msg.text?.trim()) {
      this.events.emit('final', msg.text)
      if (!this.continuous) this.stop()
    } else if (msg.type === 'error') this.events.emit('error', { code: msg.code ?? 'stt', message: msg.message ?? 'Recognition failed' })
  }

  /** Stop: the server delivers what it heard, then closes. */
  stop(): void {
    this.source?.stop()
    this.source = null
    if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: 'stop' }))
  }

  /** Stop and discard. */
  abort(): void {
    this.source?.stop()
    this.source = null
    const ws = this.ws
    if (ws) {
      ws.onmessage = null
      ws.close()
    }
    this.finish()
  }

  private finish(): void {
    if (!this.running_) return
    this.running_ = false
    this.ws = null
    this.events.emit('end', null)
  }
}
