/**
 * Thin React hooks over `/browser`: the provider holds the session (and optionally a voice engine) in context; the
 * hooks subscribe to their state. Nothing here draws anything.
 */
import * as React from 'react'
import { createVoiceEngine, type CreateVoiceOptions } from '../browser'
import type { AudioSignal, AudioSignals, AudioSource } from '../browser/audio/signals'
import type { MicState } from '../browser/mic/microphone'
import { EvokaiSession, type EvokaiSessionOptions } from '../browser/session'
import type { SessionState } from '../browser/store'
import { Dictation, type DictationOptions, type DictationState } from '../browser/voice/dictation'
import type { VoiceEngine, VoiceError, VoiceState } from '../browser/voice/types'
import type { TurnPhase, UsageShares } from '../core/types'

export interface EvokaiContextValue {
  session: EvokaiSession | null
  /** The shared voice engine, created on first `useVoice` when not given. */
  voice: VoiceEngine | null
}

const Ctx = React.createContext<EvokaiContextValue>({ session: null, voice: null })

export interface EvokaiProviderProps {
  /** A session you created, or the options to create one. */
  session?: EvokaiSession
  options?: EvokaiSessionOptions
  /** A voice engine you created, or options for the one the token chooses. */
  voice?: VoiceEngine | CreateVoiceOptions
  children?: React.ReactNode
}

const isEngine = (v: unknown): v is VoiceEngine => !!v && typeof (v as VoiceEngine).start === 'function'

/** Context only: provides the session (and a voice engine) to the hooks below. */
export function EvokaiProvider({ session, options, voice, children }: EvokaiProviderProps) {
  const [own] = React.useState(() => (session || !options ? null : new EvokaiSession(options)))
  const s = session ?? own
  React.useEffect(() => () => own?.dispose(), [own])
  const kind = React.useSyncExternalStore(
    s ? s.store.subscribe : subscribeNothing,
    () => s?.store.getState().voiceEngine ?? null,
    () => s?.store.getState().voiceEngine ?? null,
  )
  const voiceRef = React.useRef(voice)
  voiceRef.current = voice
  const given = isEngine(voice) ? voice : null
  const engine = React.useMemo<VoiceEngine | null>(() => {
    if (given) return given
    if (!s) return null
    const v = voiceRef.current
    return createVoiceEngine(s, isEngine(v) ? {} : (v ?? {}), kind ?? s.voiceEngine)
  }, [s, kind, given])
  React.useEffect(() => () => void engine?.stop(), [engine])
  const value = React.useMemo<EvokaiContextValue>(() => ({ session: s, voice: engine }), [s, engine])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

const subscribeNothing = () => () => undefined

export function useEvokaiContext(): EvokaiContextValue {
  return React.useContext(Ctx)
}

function useSession(explicit?: EvokaiSession | null): EvokaiSession {
  const ctx = React.useContext(Ctx)
  const s = explicit ?? ctx.session
  if (!s) throw new Error('No Evokai session: wrap in <EvokaiProvider> or pass one')
  return s
}

/** The session and its live state (messages, phase, pending confirm, open ask, usage…). */
export function useEvokaiSession(explicit?: EvokaiSession | null) {
  const session = useSession(explicit)
  const state: SessionState = React.useSyncExternalStore(session.store.subscribe, session.store.getState, session.store.getState)
  return { session, state }
}

/** The turn phase: idle, listening, thinking, speaking, interrupted. */
export function usePhase(explicit?: EvokaiSession | null): TurnPhase {
  const session = useSession(explicit)
  return React.useSyncExternalStore(
    session.store.subscribe,
    () => session.store.getState().phase,
    () => session.store.getState().phase,
  )
}

/** The usage shares (percentages only), or null until known. */
export function useUsage(explicit?: EvokaiSession | null): UsageShares | null {
  const session = useSession(explicit)
  return React.useSyncExternalStore(
    session.store.subscribe,
    () => session.store.getState().usage,
    () => session.store.getState().usage,
  )
}

/** The voice engine (from the provider, or the one you pass): state, phase, mic, start/stop, interrupt. */
export function useVoice(explicit?: VoiceEngine | null) {
  const ctx = React.useContext(Ctx)
  const engine = explicit ?? ctx.voice
  const [state, setState] = React.useState<VoiceState>(engine?.state ?? 'idle')
  const [interim, setInterim] = React.useState('')
  const [error, setError] = React.useState<VoiceError | null>(null)
  const [muted, setMuted] = React.useState(engine?.muted ?? false)
  const [phase, setPhase] = React.useState<TurnPhase>(engine?.phase ?? 'idle')
  React.useEffect(() => {
    if (!engine) return
    setState(engine.state)
    setPhase(engine.phase)
    const offs = [engine.on('state', setState), engine.on('interim', setInterim), engine.on('error', setError), engine.on('phase', setPhase)]
    return () => offs.forEach((off) => off())
  }, [engine])
  return {
    engine,
    state,
    phase,
    interim,
    error,
    muted,
    active: state === 'active' || state === 'starting',
    start: React.useCallback(async () => {
      setError(null)
      await engine?.start()
    }, [engine]),
    stop: React.useCallback(async () => {
      await engine?.stop()
      setInterim('')
    }, [engine]),
    interrupt: React.useCallback(() => engine?.interrupt(), [engine]),
    setMuted: React.useCallback(
      (m: boolean) => {
        engine?.setMuted(m)
        setMuted(m)
      },
      [engine],
    ),
  }
}

/** The microphone state of a voice engine or dictation that exposes one (permission, devices, mute). */
export function useMicrophone(source: { microphone?: { state: MicState; on: (e: 'state', fn: (s: MicState) => void) => () => void } } | null) {
  const mic = source?.microphone ?? null
  const [state, setState] = React.useState<MicState | null>(mic?.state ?? null)
  React.useEffect(() => {
    if (!mic) return
    setState(mic.state)
    return mic.on('state', setState)
  }, [mic])
  return state
}

/**
 * Audio signals as React state, throttled to `fps` (default 30) so a visual re-renders at a sane rate. For per-frame
 * work (a WebGL orb), use `signals.subscribe` / `signals.current()` in your own animation loop instead.
 */
export function useAudioSignals(signals: AudioSignals | null | undefined, opts: { source?: AudioSource; fps?: number } = {}): AudioSignal | null {
  const [value, setValue] = React.useState<AudioSignal | null>(signals?.current(opts.source) ?? null)
  const fps = opts.fps ?? 30
  React.useEffect(() => {
    if (!signals) return
    let last = 0
    return signals.subscribe((s) => {
      if (opts.source && s.source !== opts.source) return
      if (s.time - last < 1000 / fps && !s.onset) return
      last = s.time
      setValue(s)
    })
  }, [signals, opts.source, fps])
  return value
}

/** Dictation into your own input: `start()` from a click; `text` is the transcript so far. */
export function useDictation(opts: DictationOptions & { onFinal?: (text: string) => void } = {}) {
  const optsRef = React.useRef(opts)
  optsRef.current = opts
  const [dictation] = React.useState(() => new Dictation(opts))
  const [state, setState] = React.useState<DictationState>('idle')
  const [text, setText] = React.useState('')
  React.useEffect(() => {
    const offs = [
      dictation.on('state', setState),
      dictation.on('text', setText),
      dictation.on('final', (t) => optsRef.current.onFinal?.(t)),
    ]
    return () => {
      offs.forEach((off) => off())
      dictation.cancel()
    }
  }, [dictation])
  return {
    dictation,
    state,
    text,
    start: React.useCallback(() => {
      setText('')
      return dictation.start()
    }, [dictation]),
    stop: React.useCallback(() => dictation.stop(), [dictation]),
    cancel: React.useCallback(() => dictation.cancel(), [dictation]),
  }
}
