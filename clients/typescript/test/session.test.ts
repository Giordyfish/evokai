import { EvokaiSession, isLimitError } from '../src/browser/session'
import type { EvokaiEvent } from '../src/core/types'
import { frame, json, mockFetch, sse } from './helpers/fetch'

const BASE = 'https://agents.test/agent'

const turn = (turnId: string, from: number): EvokaiEvent[] => [
  { type: 'conversation', id: 'c1', turnStarted: true, turnId, seq: from },
  { type: 'text', delta: 'Order 1042 is ', turnId, seq: from + 1 },
  { type: 'text', delta: 'shipped.', turnId, seq: from + 2 },
  { type: 'usage', dayPct: 30, monthPct: 10, companyPct: 2, monthOut: false, turnId, seq: from + 3 },
  { type: 'done', conversationId: 'c1', reason: 'end_turn', turnId, seq: from + 4 },
]

describe('EvokaiSession', () => {
  test('streams a turn into the store, with the bearer token and the page', async () => {
    const body = turn('t1', 1).map(frame).join(': ping\n\n')
    // Split the stream into awkward pieces: mid-field, mid-character, mid-CRLF.
    const chunks = [body.slice(0, 7), body.slice(7, 61), body.slice(61, 200), body.slice(200)]
    const m = mockFetch({ 'POST /agent/v1/session/chat': () => sse(chunks, { turnId: 't1' }) })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch, getPage: () => ({ path: '/x', title: 'X' }) })
    const seen: string[] = []
    s.on('*', (e) => e.type !== 'phase' && seen.push(e.type))
    const phases: string[] = []
    s.on('phase', (e) => phases.push(e.phase))
    const done = await s.chat({ text: ' Where is order 1042? ' })
    expect(done?.reason).toBe('end_turn')
    expect(seen).toEqual(['conversation', 'text', 'text', 'usage', 'done'])
    expect(phases).toEqual(['thinking', 'idle'])
    const call = m.calls[0]!
    expect(call.headers.authorization).toBe('Bearer tok')
    expect(call.headers.accept).toBe('text/event-stream')
    expect(call.body).toEqual({ mode: 'text', text: 'Where is order 1042?', page: { path: '/x', title: 'X' } })
    const st = s.state
    expect(st.busy).toBe(false)
    expect(st.usage?.dayPct).toBe(30)
    expect(st.messages.map((x) => [x.role, x.parts])).toEqual([
      ['user', [{ kind: 'text', text: 'Where is order 1042?' }]],
      ['assistant', [{ kind: 'text', text: 'Order 1042 is shipped.' }]],
    ])
  })

  test('a dropped stream is completed by replaying the events after the last seq', async () => {
    const events = turn('t1', 11)
    const m = mockFetch({
      // The connection dies after two events.
      'POST /agent/v1/session/chat': () => sse(events.slice(0, 2).map(frame), { turnId: 't1', fail: true }),
      // The first replay overlaps (seq 12 again) and has not reached the end; the second has it.
      'GET /agent/v1/session/events': [
        () => json({ events: events.slice(1, 3) }),
        () => json({ events: events.slice(3) }),
      ],
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch, replay: { attempts: 5, delayMs: 1 } })
    const texts: string[] = []
    s.on('text', (e) => texts.push(e.delta))
    const done = await s.chat({ text: 'hello' })
    expect(done?.turnId).toBe('t1')
    const replays = m.calls.filter((c) => c.url.includes('/events'))
    expect(new URL(replays[0]!.url).searchParams.get('after')).toBe('12')
    expect(new URL(replays[1]!.url).searchParams.get('after')).toBe('13')
    expect(texts).toEqual(['Order 1042 is ', 'shipped.'])
    expect(s.state.messages[1]!.parts).toEqual([{ kind: 'text', text: 'Order 1042 is shipped.' }])
    expect(s.state.busy).toBe(false)
    expect(s.state.lastSeq).toBe(15)
  })

  test('cancel posts to the running turn only', async () => {
    let release: () => void = () => undefined
    const gate = new Promise<void>((r) => (release = r))
    const m = mockFetch({
      'POST /agent/v1/session/chat': () => {
        const enc = new TextEncoder()
        let step = 0
        const stream = new ReadableStream<Uint8Array>({
          async pull(ctrl) {
            if (step++ === 0) ctrl.enqueue(enc.encode(frame({ type: 'conversation', id: 'c', turnStarted: true, turnId: 'tX', seq: 1 })))
            else {
              await gate
              ctrl.enqueue(enc.encode(frame({ type: 'done', conversationId: 'c', reason: 'cancelled', turnId: 'tX', seq: 2 })))
              ctrl.close()
            }
          },
        })
        return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
      },
      'POST /agent/v1/session/turns/tX/cancel': () => {
        release()
        return json({ ok: true, cancelled: true })
      },
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    const pending = s.chat({ text: 'stop me' })
    await vi.waitFor(() => expect(s.state.currentTurn?.turnId).toBe('tX'))
    expect(await s.cancel()).toBe(true)
    const done = await pending
    expect(done?.reason).toBe('cancelled')
  })

  test('a refused token is replaced via getToken and the request retried', async () => {
    const m = mockFetch({
      'GET /agent/v1/session': [
        () => json({ error: 'unauthorized', message: 'Invalid session.' }, 401),
        (c) => json({ conversationId: 'c9', agent: 'a', expiresAt: 0, usage: { dayPct: 12, monthPct: null, companyPct: 40, monthOut: false }, memories: [], auth: c.headers.authorization }),
      ],
    })
    const asked: string[] = []
    const s = new EvokaiSession({
      baseUrl: BASE,
      fetch: m.fetch,
      getToken: ({ reason }) => {
        asked.push(reason)
        return { token: `tok-${asked.length}`, conversationId: 'c9', voiceEngine: 'server' }
      },
    })
    const info = await s.info()
    expect(asked).toEqual(['initial', 'refresh'])
    expect(m.calls.map((c) => c.headers.authorization)).toEqual(['Bearer tok-1', 'Bearer tok-2'])
    expect(info.usage.dayPct).toBe(12)
    expect(s.usage?.companyPct).toBe(40)
    expect(s.voiceEngine).toBe('server')
    expect(s.state.voiceEngine).toBe('server')
  })

  test('confirm and answer helpers send the right bodies', async () => {
    const m = mockFetch({
      'POST /agent/v1/session/chat': [
        () => sse([frame({ type: 'conversation', id: 'c', turnStarted: true, turnId: 'a', seq: 1 }), frame({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 'a', seq: 2 })]),
        () => sse([frame({ type: 'conversation', id: 'c', turnStarted: true, turnId: 'b', seq: 3 }), frame({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 'b', seq: 4 })]),
      ],
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch, getHistory: () => [{ role: 'user', text: 'before', content: [] }] })
    await s.confirm('act1', false, { mode: 'voice' })
    await s.answer('q1', ['Paperback', 'Hardback'])
    expect(m.calls[0]!.body).toEqual({ mode: 'voice', confirm: { actionId: 'act1', approve: false }, history: [{ role: 'user', text: 'before', content: [] }] })
    expect(m.calls[1]!.body).toMatchObject({ mode: 'text', text: 'Paperback, Hardback' })
    expect(s.state.messages.find((x) => x.role === 'user')?.answer).toBe(true)
  })

  test('handleTool runs a client tool and posts the result of one that waits', async () => {
    const call = (callId: string, name: string, wait: boolean, seq: number) =>
      frame({ type: 'client_tool', callId, name, args: { on: true }, wait, turnId: 't', seq })
    const m = mockFetch({
      'POST /agent/v1/session/chat': () =>
        sse([
          call('c1', 'kiosk_light', true, 1),
          call('c2', 'kiosk_light', false, 2),
          call('c3', 'kiosk_print', true, 3),
          call('c4', 'nobody', true, 4),
          frame({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 't', seq: 5 }),
        ]),
      'POST /agent/v1/session/tool-results': () => json({ ok: true }),
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    const lit: unknown[] = []
    s.handleTool('kiosk_light', (args) => {
      lit.push(args)
      return { lit: true }
    })
    s.handleTool('kiosk_print', () => {
      throw new Error('No paper')
    })
    await s.chat({ text: 'go' })
    await new Promise((r) => setTimeout(r, 0))
    expect(lit).toEqual([{ on: true }, { on: true }])
    const posted = m.calls.filter((c) => c.url.endsWith('/tool-results')).map((c) => c.body)
    expect(posted).toEqual([
      { callId: 'c1', result: { lit: true } },
      { callId: 'c3', error: 'No paper' },
      { callId: 'c4', error: 'No handler for nobody in this client.' },
    ])
  })

  test('external storage: the turn’s new messages reach onMessages', async () => {
    const msgs = [{ role: 'user' as const, text: 'hi', content: [] }]
    const m = mockFetch({
      'POST /agent/v1/session/chat': () => sse([frame({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 'x', seq: 1, messages: msgs })]),
    })
    const got: unknown[] = []
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch, onMessages: (x) => got.push(x) })
    await s.chat({ text: 'hi' })
    expect(got).toEqual([msgs])
  })

  test('an HTTP error becomes the session error and the message error', async () => {
    const m = mockFetch({ 'POST /agent/v1/session/chat': () => json({ error: 'origin', message: 'Origin not allowed.' }, 403) })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    expect(await s.chat({ text: 'x' })).toBeNull()
    expect(s.state.error).toEqual({ code: 'origin', message: 'Origin not allowed.' })
    expect(s.state.busy).toBe(false)
    expect(s.state.messages[1]!.parts).toEqual([{ kind: 'error', code: 'origin', message: 'Origin not allowed.' }])
  })

  test('switching conversation asks for a token bound to it and loads it', async () => {
    const m = mockFetch({
      'GET /agent/v1/session/conversation': () =>
        json({ messages: [{ id: 'm1', role: 'user', text: 'old', events: [], feedback: null }], pending: null, lastSeq: 9, runningTurnId: null }),
    })
    const asked: Array<string | null> = []
    const s = new EvokaiSession({
      baseUrl: BASE,
      fetch: m.fetch,
      getToken: ({ conversationId }) => {
        asked.push(conversationId)
        return { token: 'tok', conversationId: conversationId ?? 'new' }
      },
    })
    await s.switchConversation('old')
    expect(asked).toEqual(['old'])
    expect(s.state.conversationId).toBe('old')
    expect(s.state.lastSeq).toBe(9)
    expect(s.state.messages).toHaveLength(1)
    await s.newConversation()
    expect(asked).toEqual(['old', null])
    expect(s.state.conversationId).toBe('new')
    expect(s.state.messages).toHaveLength(0)
  })

  test('usage: from the token, then from events; subscribers hear every change; limit errors are typed', async () => {
    const m = mockFetch({
      'POST /agent/v1/session/chat': () =>
        sse([
          frame({ type: 'error', code: 'limit', limit: 'subject_day', message: 'You have reached today’s limit.', turnId: 't', seq: 1 }),
          frame({ type: 'usage', dayPct: 100, monthPct: 40, companyPct: 3, monthOut: false, turnId: 't', seq: 2 }),
          frame({ type: 'done', conversationId: 'c', reason: 'limit', turnId: 't', seq: 3 }),
        ]),
    })
    const s = new EvokaiSession({
      baseUrl: BASE,
      fetch: m.fetch,
      getToken: () => ({ token: 't', usage: { dayPct: 90, monthPct: 40, companyPct: 3, monthOut: false } }),
    })
    const heard: number[] = []
    s.subscribeUsage((u) => heard.push(u.dayPct ?? -1))
    const limits: string[] = []
    s.on('error', (e) => isLimitError(e) && limits.push(e.limit))
    await s.chat({ text: 'again' })
    expect(heard).toEqual([90, 100])
    expect(limits).toEqual(['subject_day'])
  })
})
