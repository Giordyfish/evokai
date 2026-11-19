import { EvokaiSession } from '../src/browser/session'
import { ServerVoiceEngine, evokaiEventOf, type PipecatCallbacks, type PipecatModules } from '../src/browser/voice/server'
import { json, mockFetch } from './helpers/fetch'

function fakePipecat() {
  const made: { callbacks?: PipecatCallbacks; connectParams?: unknown; transportOpts?: unknown; disconnected: boolean } = { disconnected: false }
  const modules: PipecatModules = {
    SmallWebRTCTransport: class {
      readonly kind = 'transport'
      constructor(opts?: unknown) {
        made.transportOpts = opts
      }
    },
    PipecatClient: class {
      constructor(opts: { callbacks?: PipecatCallbacks }) {
        made.callbacks = opts.callbacks
      }
      async connect(p?: unknown) {
        made.connectParams = p
        return {}
      }
      async disconnect() {
        made.disconnected = true
      }
    },
  }
  return { made, modules }
}

describe('ServerVoiceEngine', () => {
  test('posts the offer to the voice endpoint with the bearer token and maps RTVI messages into the store', async () => {
    const { made, modules } = fakePipecat()
    const m = mockFetch({
      'GET /agent/v1/session/conversation': () => json({ messages: [], pending: null, lastSeq: 0, runningTurnId: null }),
    })
    const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: { token: 'tok', voiceEngine: 'server' }, fetch: m.fetch })
    const engine = new ServerVoiceEngine({ session, loadPipecat: async () => modules, iceServers: [{ urls: 'stun:x' }], reloadAfterTurn: false, micSound: 'none' })
    const states: string[] = []
    engine.on('state', (s) => states.push(s))
    const phases: string[] = []
    session.on('phase', (e) => phases.push(e.phase))
    await engine.start()

    const p = made.connectParams as { webrtcRequestParams: { endpoint: string; headers: Headers } }
    expect(p.webrtcRequestParams.endpoint).toBe('https://a.test/agent/v1/session/voice/offer')
    expect(p.webrtcRequestParams.headers.get('Authorization')).toBe('Bearer tok')
    expect(made.transportOpts).toEqual({ iceServers: [{ urls: 'stun:x' }] })

    const cb = made.callbacks!
    cb.onServerMessage?.({ evokai: { type: 'conversation', id: 'c', turnStarted: true, turnId: 'v1', seq: 1 } })
    cb.onServerMessage?.({ evokai: { type: 'text', delta: 'Ciao.', turnId: 'v1', seq: 2 } })
    cb.onServerMessage?.({ somethingElse: true })
    cb.onBotStartedSpeaking?.()
    cb.onServerMessage?.({ evokai: { type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 'v1', seq: 3 } })
    cb.onBotStoppedSpeaking?.()
    cb.onServerMessage?.({ evokai: { type: 'speech', enabled: false, reason: 'tts_unavailable', message: 'Voice is unavailable.' } })

    expect(session.state.messages[0]!.parts).toEqual([{ kind: 'text', text: 'Ciao.' }])
    expect(session.state.speech).toEqual({ enabled: false, message: 'Voice is unavailable.' })
    expect(states).toEqual(['starting', 'active'])
    expect(phases).toEqual(['listening', 'thinking', 'speaking', 'listening'])

    await engine.stop()
    expect(made.disconnected).toBe(true)
    expect(engine.state).toBe('idle')
    expect(session.phase).toBe('idle')
  })

  test('after a spoken turn the conversation is reloaded, so what the user said appears', async () => {
    const { made, modules } = fakePipecat()
    const m = mockFetch({
      'GET /agent/v1/session/conversation': [
        // The first snapshot is taken before the turn was stored; the next one has it.
        () => json({ messages: [], pending: null, lastSeq: 1, runningTurnId: null }),
        () =>
          json({
            messages: [
              { id: 'u1', role: 'user', text: 'Where is order 1042?', events: [], feedback: null },
              { id: 'a1', role: 'assistant', text: 'Closed.', events: [], feedback: null },
            ],
            pending: null,
            lastSeq: 3,
            runningTurnId: null,
          }),
      ],
    })
    const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: 'tok', fetch: m.fetch })
    const engine = new ServerVoiceEngine({ session, loadPipecat: async () => modules })
    await engine.start()
    const cb = made.callbacks!
    cb.onServerMessage?.({ evokai: { type: 'conversation', id: 'c', turnStarted: true, turnId: 'v1', seq: 1 } })
    cb.onServerMessage?.({ evokai: { type: 'text', delta: 'Closed.', turnId: 'v1', seq: 2 } })
    cb.onServerMessage?.({ evokai: { type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 'v1', seq: 3 } })
    await vi.waitFor(() => expect(session.state.messages.map((x) => x.role)).toEqual(['user', 'assistant']), { timeout: 3000 })
    expect(session.state.messages[1]!.messageId).toBe('a1')
    await engine.stop()
  })

  test('external storage: the history goes with the offer under requestData', async () => {
    const { made, modules } = fakePipecat()
    const history = [{ role: 'user' as const, text: 'before', content: [] }]
    const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: 'tok', fetch: mockFetch({}).fetch, getHistory: () => history })
    const engine = new ServerVoiceEngine({ session, loadPipecat: async () => modules, micSound: 'none' })
    await engine.start()
    expect((made.connectParams as { webrtcRequestParams: { requestData: unknown } }).webrtcRequestParams.requestData).toEqual({ history })
    await engine.stop()
  })

  test('a failed connection is an error state', async () => {
    const { modules } = fakePipecat()
    modules.PipecatClient = class {
      async connect() {
        throw new Error('409 voice_engine')
      }
      async disconnect() {}
    }
    const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: 'tok', fetch: mockFetch({}).fetch })
    const engine = new ServerVoiceEngine({ session, loadPipecat: async () => modules })
    const errors: string[] = []
    engine.on('error', (e) => errors.push(e.message))
    await engine.start()
    expect(engine.state).toBe('error')
    expect(errors).toEqual(['409 voice_engine'])
  })

  test('evokaiEventOf reads only {evokai: event}', () => {
    expect(evokaiEventOf({ evokai: { type: 'text', delta: 'x' } })).toEqual({ type: 'text', delta: 'x' })
    expect(evokaiEventOf({ evokai: 'x' })).toBeNull()
    expect(evokaiEventOf(null)).toBeNull()
  })
})
