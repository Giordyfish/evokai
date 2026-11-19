import { EvokaiSession } from '../src/browser/session'
import { EvokaiServer } from '../src/server'
import { initialState, reduce, type SessionState } from '../src/browser/store'
import type { EvokaiEvent, SessionConversation } from '../src/core/types'
import { localePack } from '../src/locales'
import { confirmReasonText } from '../src/browser/reasons'
import { frame, json, mockFetch, sse } from './helpers/fetch'

const BASE = 'https://agents.test/agent'
const ev = (e: EvokaiEvent): { type: 'event'; event: EvokaiEvent } => ({ type: 'event', event: e })
const run = (s: SessionState, ...actions: Parameters<typeof reduce>[1][]) => actions.reduce(reduce, s)

const stored: SessionConversation = {
  messages: [
    { id: 'q1', role: 'user', text: 'One', events: [], feedback: null, rewritable: false },
    { id: 'a1', role: 'assistant', text: 'First.', events: [], feedback: null },
    { id: 'q2', role: 'user', text: 'Two', events: [], feedback: null, rewritable: true },
    { id: 'a2', role: 'assistant', text: 'Second.', events: [], feedback: null },
  ],
  pending: null,
  lastSeq: 10,
  runningTurnId: null,
}
const shown = (s: SessionState) =>
  s.messages.filter((m) => !m.replacing).map((m) => m.parts.map((p) => (p.kind === 'text' ? p.text : '')).join(''))

describe('rewriting the last question: the store', () => {
  test('the edited question is hidden at once, then replaced when the service says so', () => {
    let s = run(initialState(), { type: 'loaded', conversation: stored })
    expect(s.messages.map((m) => m.rewritable)).toEqual([false, undefined, true, undefined])
    s = run(s, { type: 'user', local: 'L', text: 'Two, rather three', rewrite: true }, { type: 'bind', local: 'L', turnId: 't3' })
    expect(shown(s)).toEqual(['One', 'First.', 'Two, rather three', ''])
    s = run(
      s,
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't3', seq: 11 }),
      ev({ type: 'rewritten', replaced: ['t2'], messageId: 'q2', turnId: 't3', seq: 12 }),
      ev({ type: 'text', delta: 'Third.', turnId: 't3', seq: 13 }),
      ev({ type: 'done', conversationId: 'c', reason: 'end_turn', rewritable: true, turnId: 't3', seq: 14 }),
      { type: 'settled', local: 'L' },
    )
    expect(shown(s)).toEqual(['One', 'First.', 'Two, rather three', 'Third.'])
    expect(s.messages.filter((m) => m.role === 'user').map((m) => m.rewritable)).toEqual([false, true])
  })

  test('done says when the last question can no longer be rewritten', () => {
    const s = run(
      initialState(),
      { type: 'loaded', conversation: stored },
      ev({ type: 'done', conversationId: 'c', reason: 'end_turn', rewritable: false, turnId: 't9', seq: 11 }),
    )
    expect(s.messages.filter((m) => m.role === 'user').map((m) => m.rewritable)).toEqual([false, false])
  })

  test('a refused rewrite brings back what it would have replaced', () => {
    let s = run(initialState(), { type: 'loaded', conversation: stored }, { type: 'user', local: 'L', text: 'Edited', rewrite: true })
    s = run(s, { type: 'failed', local: 'L', code: 'rewrite_after_write', message: 'No.' }, { type: 'settled', local: 'L' })
    expect(s.messages.some((m) => m.replacing)).toBe(false)
    expect(shown(s).slice(0, 4)).toEqual(['One', 'First.', 'Two', 'Second.'])
    expect(s.error?.code).toBe('rewrite_after_write')
  })

  test('another tab drops the replaced turn by its question id, or the last question shown', () => {
    const live = run(
      initialState(),
      { type: 'loaded', conversation: stored },
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't3', seq: 11 }),
    )
    const byId = run(live, ev({ type: 'rewritten', replaced: [], messageId: 'q2', turnId: 't3', seq: 12 }))
    expect(byId.messages.map((m) => m.messageId ?? m.turnId)).toEqual(['q1', 'a1', 't3'])
    const external = run(live, ev({ type: 'rewritten', replaced: [], replacedFrom: 2, turnId: 't3', seq: 12 }))
    expect(external.messages.map((m) => m.messageId ?? m.turnId)).toEqual(['q1', 'a1', 't3'])
  })

  test('a replay after a rewrite shows the new turn only', () => {
    // The replaced turn's events are gone from the replay; the rewritten event is in place.
    const s = run(
      initialState(),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 }),
      ev({ type: 'text', delta: 'Kept.', turnId: 't1', seq: 2 }),
      ev({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 't1', seq: 3 }),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't3', seq: 9 }),
      ev({ type: 'rewritten', replaced: ['t2'], messageId: 'q2', turnId: 't3', seq: 10 }),
      ev({ type: 'text', delta: 'New.', turnId: 't3', seq: 11 }),
    )
    expect(shown(s)).toEqual(['Kept.', 'New.'])
  })
})

describe('confirmation reasons', () => {
  test('a confirmation carries why it is asked, and the text says it', () => {
    const why = [{ key: 'confirm.why.rule', rule: 'vip', level: 'application' as const, label: 'Key accounts' }]
    const s = run(initialState(), ev({ type: 'confirm', actionId: 'x', items: ['Read VIP-1'], why, turnId: 't', seq: 1 }))
    expect(s.pendingConfirm).toEqual({ actionId: 'x', items: ['Read VIP-1'], why })
    expect(s.messages[0]!.parts[0]).toMatchObject({ kind: 'confirm', why })
    expect(confirmReasonText(why[0], localePack('it'))).toBe('Key accounts')
    expect(confirmReasonText({ key: 'confirm.why.rule' }, localePack('it'))).toBe('Una regola di questo assistente chiede la tua conferma')
    expect(confirmReasonText({ key: 'confirm.why.writes' }, localePack('en'))).toBe('This changes data')
    expect(confirmReasonText({ key: 'confirm.why.other' }, localePack('en'))).toBe('')
    expect(confirmReasonText(undefined, localePack('en'))).toBe('')
  })
})

describe('EvokaiServer: rewrite with external storage, and the ask rules', () => {
  test('chatJson sends the rewrite and returns where the history is replaced from', async () => {
    const m = mockFetch({
      'POST /agent/v1/session/chat': () =>
        json({ conversationId: 'c', turnId: 't', reason: 'end_turn', text: 'Ok.', events: [], messages: [], replacedFrom: 2, rewritable: true }),
      'PUT /agent/v1/admin/ask-rules': (call) => json({ rules: (call.body as { rules: unknown[] }).rules, deployment: [], agents: [], tools: [] }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    const r = await srv.chatJson('tok', { text: 'Two again', rewrite: {}, history: [] })
    expect(r.replacedFrom).toBe(2)
    expect(m.calls[0]!.body).toEqual({ mode: 'text', text: 'Two again', rewrite: {}, history: [] })
    const view = await srv.admin.askRules.put([{ action: 'crm_*', argument: 'code', resource: 'VIP-*', effect: 'ask' }])
    expect(view.rules).toHaveLength(1)
    expect(m.calls[1]!.headers.authorization).toBe('Bearer adm')
  })
})

describe('EvokaiSession.rewrite', () => {
  const loaded = {
    messages: [
      { id: 'q1', role: 'user', text: 'One', events: [], feedback: null, rewritable: true },
      { id: 'a1', role: 'assistant', text: 'First.', events: [], feedback: null },
    ],
    pending: null,
    lastSeq: 5,
    runningTurnId: null,
  }

  test('names the question it edits and replaces the turn', async () => {
    const events: EvokaiEvent[] = [
      { type: 'conversation', id: 'c1', turnStarted: true, turnId: 't2', seq: 6 },
      { type: 'rewritten', replaced: ['t1'], messageId: 'q1', turnId: 't2', seq: 7 },
      { type: 'text', delta: 'Again.', turnId: 't2', seq: 8 },
      { type: 'done', conversationId: 'c1', reason: 'end_turn', rewritable: true, turnId: 't2', seq: 9 },
    ]
    const m = mockFetch({
      'GET /agent/v1/session/conversation': () => json(loaded),
      'POST /agent/v1/session/chat': () => sse(events.map(frame), { turnId: 't2' }),
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    await s.loadConversation()
    const done = await s.rewrite('One, again')
    expect(done?.rewritable).toBe(true)
    expect(m.calls[1]!.body).toEqual({ mode: 'text', text: 'One, again', rewrite: { messageId: 'q1' } })
    expect(s.state.messages.map((x) => [x.role, x.parts.map((p) => (p.kind === 'text' ? p.text : '')).join('')])).toEqual([
      ['user', 'One, again'],
      ['assistant', 'Again.'],
    ])
  })

  test('a refusal is a conflict error and the conversation is left as it was', async () => {
    const m = mockFetch({
      'GET /agent/v1/session/conversation': () => json(loaded),
      'POST /agent/v1/session/chat': () => json({ error: 'rewrite_after_write', message: 'Already changed data.' }, 409),
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    await s.loadConversation()
    expect(await s.rewrite('Edited')).toBeNull()
    expect(s.state.error?.code).toBe('rewrite_after_write')
    expect(s.state.messages.filter((x) => x.role === 'user').map((x) => x.parts)).toEqual([[{ kind: 'text', text: 'One' }]])
  })
})
