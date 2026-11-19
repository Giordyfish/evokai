// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { EvokaiSession } from '../src/browser/session'
import { AudioSignals } from '../src/browser/audio/signals'
import { EvokaiProvider, useAudioSignals, useDictation, useEvokaiSession, usePhase, useUsage, useVoice } from '../src/react'
import type { SpeechRecognitionStatic } from '../src/browser/stt/on-device'
import { mockFetch } from './helpers/fetch'

afterEach(cleanup)

function Probe() {
  const { state } = useEvokaiSession()
  const phase = usePhase()
  const usage = useUsage()
  const voice = useVoice()
  return (
    <output>
      {state.messages.length}|{phase}|{usage?.dayPct ?? '-'}|{voice.engine?.kind}|{voice.state}
    </output>
  )
}

describe('hooks', () => {
  test('state, phase, usage and the token’s voice engine follow the session', () => {
    const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: { token: 't', voiceEngine: 'server' }, fetch: mockFetch({}).fetch })
    render(
      <EvokaiProvider session={session} voice={{ micSound: 'none' }}>
        <Probe />
      </EvokaiProvider>,
    )
    expect(screen.getByRole('status').textContent).toBe('0|idle|-|server|idle')
    act(() => session.ingest({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't', seq: 1 }))
    act(() => session.ingest({ type: 'usage', dayPct: 42, monthPct: 1, companyPct: 0, monthOut: false, turnId: 't', seq: 2 }))
    act(() => session.ingest({ type: 'text', delta: 'x', turnId: 't', seq: 3 }))
    expect(screen.getByRole('status').textContent).toBe('1|thinking|42|server|idle')
  })

  test('without a provider or session the hooks say so', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    expect(() => render(<Probe />)).toThrow(/EvokaiProvider/)
    err.mockRestore()
  })

  test('useAudioSignals throttles frames but never drops an onset', () => {
    const signals = new AudioSignals()
    const seen: number[] = []
    function Meter() {
      const s = useAudioSignals(signals, { source: 'agent', fps: 10 })
      if (s) seen.push(s.time)
      return <output>{s?.level ?? 0}</output>
    }
    render(<Meter />)
    const frame = (time: number, onset = false, source: 'agent' | 'user' = 'agent') =>
      act(() => signals.push({ source, rms: time / 1000, level: time / 1000, onset, intensity: 0, spectrum: null, estimated: false, time }))
    frame(100)
    frame(116) // within 100 ms: skipped
    frame(132, true) // an onset: kept
    frame(150, false, 'user') // another source: ignored
    frame(250)
    expect(seen).toEqual([100, 132, 250])
    expect(screen.getByRole('status').textContent).toBe('0.25')
  })

  test('useDictation fills the text and reports the final phrase', async () => {
    const instances: Array<{ onresult: ((e: unknown) => void) | null; onend: (() => void) | null; stop: () => void }> = []
    class Rec {
      lang = ''
      continuous = false
      interimResults = false
      onresult: ((e: unknown) => void) | null = null
      onerror = null
      onend: (() => void) | null = null
      constructor() {
        instances.push(this)
      }
      start() {}
      stop() {
        this.onend?.()
      }
      abort() {}
    }
    const C = Object.assign(Rec, { available: async () => 'available' as const }) as unknown as SpeechRecognitionStatic
    const finals: string[] = []
    let api: ReturnType<typeof useDictation> | null = null
    function Box() {
      api = useDictation({ recognition: C, meterMicrophone: false, micSound: 'none', onFinal: (t) => finals.push(t) })
      return <output>{`${api.state}:${api.text}`}</output>
    }
    render(<Box />)
    await act(async () => {
      await api!.start()
    })
    expect(screen.getByRole('status').textContent).toBe('listening:')
    const result = (text: string, isFinal: boolean) => ({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal })] })
    act(() => instances[0]!.onresult?.(result('hello', false)))
    expect(screen.getByRole('status').textContent).toBe('listening:hello')
    act(() => instances[0]!.onresult?.(result('hello world', true)))
    await act(async () => api!.stop())
    expect(finals).toEqual(['hello world'])
    expect(screen.getByRole('status').textContent).toBe('idle:hello world')
  })
})
