/**
 * The user's microphone: permission, capture, mute, the device list and the chosen device, device changes, and an
 * analyser for the audio signals. One instance can serve dictation and conversation.
 */
import { Emitter } from '../voice/types'

export type MicPermission = 'granted' | 'denied' | 'prompt' | 'unsupported'

export interface MicDevice {
  deviceId: string
  /** Empty until permission is granted (browsers hide labels before). */
  label: string
  groupId: string
  /** The system default input. */
  isDefault: boolean
}

export interface MicState {
  permission: MicPermission
  /** Capturing now. */
  active: boolean
  muted: boolean
  /** The chosen device (null = the system default). */
  deviceId: string | null
  devices: MicDevice[]
}

export interface MicEvents {
  state: MicState
  /** Devices were plugged or unplugged (the list after the change). */
  devicechange: MicDevice[]
  /** The chosen device disappeared; capture fell back to the default. */
  devicelost: { deviceId: string }
  error: { code: 'not-allowed' | 'no-device' | 'busy' | 'unsupported' | 'unknown'; message: string }
}

export interface MicrophoneOptions {
  /** Extra audio constraints (default: echo cancellation, noise suppression, auto gain on). */
  constraints?: MediaTrackConstraints
  /** The device to use (default: the system default). */
  deviceId?: string | null
  /** Override `navigator.mediaDevices` / `navigator.permissions` (tests). */
  mediaDevices?: Pick<MediaDevices, 'getUserMedia' | 'enumerateDevices' | 'addEventListener' | 'removeEventListener'> | null
  permissions?: Pick<Permissions, 'query'> | null
  /** The audio context for the analyser (default: created on first use). */
  audioContext?: AudioContext
}

const DEFAULT_CONSTRAINTS: MediaTrackConstraints = { echoCancellation: true, noiseSuppression: true, autoGainControl: true }

function errorCode(e: unknown): MicEvents['error']['code'] {
  const name = (e as { name?: string } | null)?.name
  if (name === 'NotAllowedError' || name === 'SecurityError') return 'not-allowed'
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'no-device'
  if (name === 'NotReadableError' || name === 'AbortError') return 'busy'
  return 'unknown'
}

export class Microphone {
  private readonly events = new Emitter<MicEvents>()
  private readonly opts: MicrophoneOptions
  private current: MicState
  private mediaStream: MediaStream | null = null
  private ctx: AudioContext | null
  private node: AnalyserNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private readonly onDeviceChange = () => void this.refreshDevices(true)

  constructor(opts: MicrophoneOptions = {}) {
    this.opts = opts
    this.ctx = opts.audioContext ?? null
    this.current = { permission: this.md ? 'prompt' : 'unsupported', active: false, muted: false, deviceId: opts.deviceId ?? null, devices: [] }
    this.md?.addEventListener?.('devicechange', this.onDeviceChange)
  }

  private get md() {
    if (this.opts.mediaDevices !== undefined) return this.opts.mediaDevices
    return typeof navigator !== 'undefined' && navigator.mediaDevices ? navigator.mediaDevices : null
  }

  get state(): MicState {
    return this.current
  }

  /** The live stream while capturing. */
  get stream(): MediaStream | null {
    return this.mediaStream
  }

  /** The live audio track while capturing (pass it to recognisers that accept one). */
  get track(): MediaStreamTrack | null {
    return this.mediaStream?.getAudioTracks()[0] ?? null
  }

  on<K extends keyof MicEvents>(event: K, fn: (value: MicEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  private set(patch: Partial<MicState>): void {
    this.current = { ...this.current, ...patch }
    this.events.emit('state', this.current)
  }

  /** The permission as the browser reports it, without prompting. */
  async permission(): Promise<MicPermission> {
    if (!this.md) return 'unsupported'
    const perms = this.opts.permissions !== undefined ? this.opts.permissions : typeof navigator !== 'undefined' ? navigator.permissions : null
    try {
      const s = await perms?.query({ name: 'microphone' as PermissionName })
      if (s) {
        const p = s.state as MicPermission
        if (p !== this.current.permission) this.set({ permission: p })
        return p
      }
    } catch {
      /* not queryable in this browser */
    }
    return this.current.permission
  }

  /** Ask for the microphone (opens and closes it once); resolves to the resulting permission. */
  async requestPermission(): Promise<MicPermission> {
    if (this.current.active) return 'granted'
    try {
      await this.start()
      this.stop()
      return 'granted'
    } catch {
      return this.current.permission
    }
  }

  /** Start capturing (from a user gesture the first time). */
  async start(deviceId: string | null = this.current.deviceId): Promise<MediaStream> {
    const md = this.md
    if (!md) {
      this.events.emit('error', { code: 'unsupported', message: 'No microphone API in this browser.' })
      throw new Error('unsupported')
    }
    if (this.mediaStream && deviceId === this.current.deviceId) return this.mediaStream
    this.release()
    try {
      const audio: MediaTrackConstraints = { ...DEFAULT_CONSTRAINTS, ...this.opts.constraints, ...(deviceId ? { deviceId: { exact: deviceId } } : {}) }
      const stream = await md.getUserMedia({ audio })
      this.mediaStream = stream
      const track = stream.getAudioTracks()[0]
      if (track) {
        track.enabled = !this.current.muted
        track.addEventListener?.('ended', () => {
          if (this.mediaStream === stream) {
            const lost = this.current.deviceId
            this.release()
            this.set({ active: false })
            if (lost) this.events.emit('devicelost', { deviceId: lost })
          }
        })
      }
      this.set({ permission: 'granted', active: true, deviceId })
      void this.refreshDevices(false)
      return stream
    } catch (e) {
      const code = errorCode(e)
      if (code === 'not-allowed') this.set({ permission: 'denied', active: false })
      this.events.emit('error', { code, message: e instanceof Error ? e.message : String(e) })
      throw e
    }
  }

  /** Stop capturing (the device light goes off). */
  stop(): void {
    this.release()
    if (this.current.active) this.set({ active: false })
  }

  private release(): void {
    this.source?.disconnect()
    this.source = null
    this.node = null
    this.mediaStream?.getTracks().forEach((t) => t.stop())
    this.mediaStream = null
  }

  /** Mute keeps the capture open but sends silence. */
  setMuted(muted: boolean): void {
    const t = this.track
    if (t) t.enabled = !muted
    if (muted !== this.current.muted) this.set({ muted })
  }

  mute(): void {
    this.setMuted(true)
  }

  unmute(): void {
    this.setMuted(false)
  }

  /** The audio inputs (labels appear once permission is granted). */
  async devices(): Promise<MicDevice[]> {
    return this.refreshDevices(false)
  }

  private async refreshDevices(changed: boolean): Promise<MicDevice[]> {
    const md = this.md
    if (!md) return []
    let list: MediaDeviceInfo[]
    try {
      list = await md.enumerateDevices()
    } catch {
      return this.current.devices
    }
    const devices = list
      .filter((d) => d.kind === 'audioinput')
      .map((d) => ({ deviceId: d.deviceId, label: d.label, groupId: d.groupId, isDefault: d.deviceId === 'default' }))
    this.set({ devices })
    if (changed) {
      this.events.emit('devicechange', devices)
      const chosen = this.current.deviceId
      if (chosen && !devices.some((d) => d.deviceId === chosen)) {
        this.events.emit('devicelost', { deviceId: chosen })
        this.set({ deviceId: null })
        if (this.current.active) await this.start(null).catch(() => undefined)
      }
    }
    return devices
  }

  /** Choose a device; capture moves to it if active. */
  async select(deviceId: string | null): Promise<void> {
    if (this.current.active) await this.start(deviceId)
    else this.set({ deviceId })
  }

  /** An analyser on the live capture (for the user's audio signal), or null when not capturing. */
  analyser(): AnalyserNode | null {
    if (!this.mediaStream) return null
    if (this.node) return this.node
    if (!this.ctx) {
      const AC = typeof window !== 'undefined' ? (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext) : undefined
      if (!AC) return null
      this.ctx = new AC()
    }
    const an = this.ctx.createAnalyser()
    an.fftSize = 1024
    an.smoothingTimeConstant = 0.5
    this.source = this.ctx.createMediaStreamSource(this.mediaStream)
    this.source.connect(an)
    this.node = an
    return an
  }

  /** Stop capturing and stop listening to device changes. */
  dispose(): void {
    this.stop()
    this.md?.removeEventListener?.('devicechange', this.onDeviceChange)
    this.events.clear()
  }
}
