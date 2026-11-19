/**
 * Voice on the server: Pipecat's JS client with the SmallWebRTC transport. The offer goes to
 * `/v1/session/voice/offer` with the session token; the server runs STT → agent → TTS and sends every Evokai event as
 * an RTVI server message `{evokai: event}`, which lands in the same session store as the text stream.
 *
 * `@pipecat-ai/client-js` and `@pipecat-ai/small-webrtc-transport` are optional peer dependencies, imported only
 * when the engine starts.
 */
import type { EvokaiEvent } from '../../core/types'
import type { EvokaiSession } from '../session'
import type { TurnPhase } from '../../core/types'
import { playMicSound } from '../audio/mic-sounds'
import { AudioSignals, meterAnalyser, type AudioSource } from '../audio/signals'
import { Emitter, type VoiceCommonOptions, type VoiceEngine, type VoiceEvents, type VoiceState } from './types'

/** The parts of Pipecat's client this engine uses (structural, so tests can pass fakes). */
export interface PipecatClientLike {
  connect(params?: unknown): Promise<unknown>
  disconnect(): Promise<void>
  enableMic?(enable: boolean): void
}
export interface PipecatCallbacks {
  onServerMessage?: (data: unknown) => void
  onBotStartedSpeaking?: () => void
  onBotStoppedSpeaking?: () => void
  onUserStartedSpeaking?: () => void
  onUserStoppedSpeaking?: () => void
  onUserTranscript?: (data: { text: string; final: boolean }) => void
  onConnected?: () => void
  onDisconnected?: () => void
  onError?: (message: unknown) => void
  /** A media track started (the bot's audio is measured for the agent signal). */
  onTrackStarted?: (track: MediaStreamTrack, participant?: { local?: boolean }) => void
}
export interface PipecatModules {
  PipecatClient: new (opts: { transport: unknown; enableMic?: boolean; enableCam?: boolean; callbacks?: PipecatCallbacks }) => PipecatClientLike
  SmallWebRTCTransport: new (opts?: { iceServers?: RTCIceServer[]; waitForICEGathering?: boolean }) => unknown
}

export interface ServerVoiceOptions extends Omit<VoiceCommonOptions, 'microphone'> {
  session: EvokaiSession
  iceServers?: RTCIceServer[]
  /** Load Pipecat (default: dynamic import of the two optional peer dependencies). */
  loadPipecat?: () => Promise<PipecatModules>
  /** Reload the conversation after each spoken turn (shows what the user said; default true). */
  reloadAfterTurn?: boolean
  /** The audio context that measures the call's tracks (default: created on first track). */
  audioContext?: AudioContext
}

/**
 * The default loader imports the packages by a computed name, so that applications that never use server voice
 * build without them. It works where bare specifiers resolve at run time (Node, import maps). With a bundler, pass
 * `loadPipecat` with literal imports (see the README) so the bundler includes them.
 */
async function importPipecat(): Promise<PipecatModules> {
  const names = ['@pipecat-ai/client-js', '@pipecat-ai/small-webrtc-transport']
  try {
    const [c, t] = (await Promise.all(
      names.map((n) => import(/* @vite-ignore */ /* webpackIgnore: true */ n)),
    )) as Array<Record<string, unknown>>
    return { PipecatClient: c?.PipecatClient, SmallWebRTCTransport: t?.SmallWebRTCTransport } as PipecatModules
  } catch {
    throw new Error(
      'ServerVoiceEngine needs @pipecat-ai/client-js and @pipecat-ai/small-webrtc-transport: install them and pass loadPipecat',
    )
  }
}

/** A loader from the two modules, for `loadPipecat` (keeps the literal imports in the application's code). */
export function pipecatModules(client: { PipecatClient: unknown }, transport: { SmallWebRTCTransport: unknown }): PipecatModules {
  return { PipecatClient: client.PipecatClient, SmallWebRTCTransport: transport.SmallWebRTCTransport } as PipecatModules
}

/** The Evokai event inside an RTVI server message, if it carries one. */
export function evokaiEventOf(data: unknown): EvokaiEvent | null {
  if (!data || typeof data !== 'object') return null
  const e = (data as { evokai?: unknown }).evokai
  return e && typeof e === 'object' && typeof (e as { type?: unknown }).type === 'string' ? (e as EvokaiEvent) : null
}

export class ServerVoiceEngine implements VoiceEngine {
  readonly kind = 'server' as const
  readonly signals = new AudioSignals()
  private current: VoiceState = 'idle'
  private isMuted = false
  private unsubs: Array<() => void> = []
  private meters: Array<() => void> = []
  private ctx: AudioContext | null = null
  private readonly events = new Emitter<VoiceEvents>()
  private readonly session: EvokaiSession
  private readonly opts: ServerVoiceOptions
  private client: PipecatClientLike | null = null

  constructor(opts: ServerVoiceOptions) {
    this.session = opts.session
    this.opts = opts
  }

  get state(): VoiceState {
    return this.current
  }

  get phase(): TurnPhase {
    return this.session.state.phase
  }

  get muted(): boolean {
    return this.isMuted
  }

  on<K extends keyof VoiceEvents>(event: K, fn: (value: VoiceEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  setMuted(muted: boolean): void {
    this.isMuted = muted
    this.client?.enableMic?.(!muted)
  }

  /** Cut the agent off: cancel the running turn (the server stops its speech with it). */
  async interrupt(): Promise<void> {
    const id = this.session.state.currentTurn?.turnId
    this.session.markInterrupted()
    this.events.emit('bargeIn', { turnId: id ?? null, heard: '' })
    if (id) await this.session.cancel(id).catch(() => false)
  }

  private set(state: VoiceState): void {
    if (state === this.current) return
    this.current = state
    this.events.emit('state', state)
  }

  /** Connect: the offer is posted with the session's bearer token. */
  async start(): Promise<void> {
    if (this.client) return
    if (typeof RTCPeerConnection === 'undefined' && !this.opts.loadPipecat) {
      this.set('unsupported')
      this.events.emit('error', { code: 'unsupported', message: 'WebRTC is not available.' })
      return
    }
    this.set('starting')
    try {
      const { PipecatClient, SmallWebRTCTransport } = await (this.opts.loadPipecat ?? importPipecat)()
      const transport = new SmallWebRTCTransport({ ...(this.opts.iceServers ? { iceServers: this.opts.iceServers } : {}) })
      const client = new PipecatClient({ transport, enableMic: true, enableCam: false, callbacks: this.callbacks() })
      this.client = client
      const token = await this.session.getAccessToken()
      const history = await this.session.history()
      this.unsubs = [this.session.on('phase', (e) => this.events.emit('phase', e.phase))]
      await client.connect({
        webrtcRequestParams: {
          endpoint: this.session.url('/v1/session/voice/offer'),
          headers: new Headers({ Authorization: `Bearer ${token}` }),
          // External storage: the conversation so far (the transport nests it under requestData).
          ...(history ? { requestData: { history } } : {}),
        },
      })
      await playMicSound(this.opts.micSound ?? 'goccia', 'on').catch(() => undefined)
      this.set('active')
      this.session.setVoiceActivity('listening')
    } catch (e) {
      this.client = null
      this.set('error')
      this.events.emit('error', { code: 'connect', message: e instanceof Error ? e.message : String(e) })
    }
  }

  async stop(): Promise<void> {
    const c = this.client
    this.client = null
    for (const u of this.unsubs) u()
    this.unsubs = []
    for (const m of this.meters) m()
    this.meters = []
    if (c) await c.disconnect().catch(() => undefined)
    this.session.setVoiceActivity(null)
    this.signals.silence('agent')
    if (c) await playMicSound(this.opts.micSound ?? 'goccia', 'off').catch(() => undefined)
    this.set('idle')
  }

  /**
   * Show what the user said: spoken turns carry no user text in their events, so the conversation is reloaded once
   * the service has stored the turn (its snapshot reaches the turn's last event).
   */
  private async reload(seq: number): Promise<void> {
    for (const wait of [250, 750, 1500]) {
      await new Promise((r) => setTimeout(r, wait))
      try {
        const c = await this.session.loadConversation()
        if ((c.lastSeq || 0) >= seq) return
      } catch {
        return
      }
    }
  }

  /** Measure a call track: the bot's audio is the `agent` signal, the local microphone the `user` one. */
  private meterTrack(track: MediaStreamTrack, source: AudioSource): void {
    if (track.kind !== 'audio' || typeof MediaStream === 'undefined') return
    if (!this.ctx) {
      const AC = typeof window !== 'undefined' ? window.AudioContext : undefined
      this.ctx = this.opts.audioContext ?? (AC ? new AC() : null)
    }
    if (!this.ctx) return
    const an = this.ctx.createAnalyser()
    an.fftSize = 1024
    an.smoothingTimeConstant = 0.5
    this.ctx.createMediaStreamSource(new MediaStream([track])).connect(an)
    this.meters.push(meterAnalyser(an, source, this.signals))
  }

  private callbacks(): PipecatCallbacks {
    return {
      onTrackStarted: (track, participant) => this.meterTrack(track, participant?.local ? 'user' : 'agent'),
      onServerMessage: (data) => {
        const e = evokaiEventOf(data)
        if (!e) return
        this.session.ingest(e)
        if (e.type === 'done') {
          if (this.opts.reloadAfterTurn !== false && !e.messages) void this.reload(e.seq ?? 0)
        }
      },
      onBotStartedSpeaking: () => {
        this.session.setVoiceActivity('speaking')
      },
      onBotStoppedSpeaking: () => {
        if (this.client) this.session.setVoiceActivity('listening')
      },
      onUserTranscript: (d) => {
        this.events.emit('interim', d.final ? '' : d.text)
        if (d.final) this.events.emit('heard', d.text)
      },
      onDisconnected: () => {
        if (this.client) {
          this.client = null
          this.session.setVoiceActivity(null)
          this.set('idle')
        }
      },
      onError: (m) => {
        this.events.emit('error', { code: 'rtvi', message: typeof m === 'string' ? m : JSON.stringify(m) })
      },
    }
  }
}
