import type { EvokaiEvent } from '../src/core/types'
import { fromStored, initialState, reduce, type SessionState } from '../src/browser/store'

const ev = (e: EvokaiEvent): { type: 'event'; event: EvokaiEvent } => ({ type: 'event', event: e })
const run = (s: SessionState, ...actions: Parameters<typeof reduce>[1][]) => actions.reduce(reduce, s)

describe('store reducers', () => {
  test('a typed turn: local echo, binding, streamed parts, done', () => {
    let s = run(initialState(), { type: 'user', local: 'L1', text: 'How did it go?' })
    expect(s.busy).toBe(true)
    expect(s.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    s = run(
      s,
      { type: 'bind', local: 'L1', turnId: 't1' },
      ev({ type: 'conversation', id: 'c1', turnStarted: true, turnId: 't1', seq: 1 }),
      ev({ type: 'tool', name: 'read', status: 'start', turnId: 't1', seq: 2 }),
      ev({ type: 'tool', name: 'read', status: 'done', turnId: 't1', seq: 3 }),
      ev({ type: 'text', delta: 'Fine, ', turnId: 't1', seq: 4 }),
      ev({ type: 'text', delta: 'thanks.', turnId: 't1', seq: 5 }),
      ev({ type: 'usage', dayPct: 20, monthPct: 5, companyPct: 1.5, monthOut: false, turnId: 't1', seq: 6 }),
    )
    expect(s.conversationId).toBe('c1')
    expect(s.currentTurn).toEqual({ turnId: 't1' })
    expect(s.messages).toHaveLength(2)
    expect(s.messages[1]!.parts).toEqual([
      { kind: 'tool', name: 'read', status: 'done' },
      { kind: 'text', text: 'Fine, thanks.' },
    ])
    expect(s.usage).toEqual({ dayPct: 20, monthPct: 5, companyPct: 1.5, monthOut: false })
    s = run(s, ev({ type: 'done', conversationId: 'c1', reason: 'end_turn', turnId: 't1', seq: 7 }), { type: 'settled', local: 'L1' })
    expect(s.busy).toBe(false)
    expect(s.phase).toBe('idle')
    expect(s.currentTurn).toBeNull()
    expect(s.lastSeq).toBe(7)
    expect(s.messages.every((m) => m.local === undefined)).toBe(true)
  })

  test('events already seen (seq), or inside the loaded snapshot, are ignored', () => {
    const t = ev({ type: 'text', delta: 'a', turnId: 't', seq: 3 })
    let s = run(initialState(), t, t)
    expect(s.messages[0]!.parts).toEqual([{ kind: 'text', text: 'a' }])
    s = run(
      initialState(),
      { type: 'loaded', conversation: { messages: [], pending: null, lastSeq: 5, runningTurnId: null } },
      ev({ type: 'text', delta: 'old', turnId: 't', seq: 4 }),
      ev({ type: 'text', delta: 'new', turnId: 't', seq: 6 }),
    )
    expect(s.messages[0]!.parts).toEqual([{ kind: 'text', text: 'new' }])
  })

  test('two interleaved streams: every event once, even out of order', () => {
    const s = run(
      initialState(),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 'new', seq: 11 }),
      ev({ type: 'text', delta: 'old', turnId: 'old', seq: 10 }),
      ev({ type: 'text', delta: 'old', turnId: 'old', seq: 10 }),
      ev({ type: 'done', conversationId: 'c', reason: 'superseded', turnId: 'old', seq: 12 }),
    )
    expect(s.lastSeq).toBe(12)
    expect(s.busy).toBe(true)
    expect(s.currentTurn).toEqual({ turnId: 'new' })
    const old = s.messages.find((m) => m.turnId === 'old')!
    expect(old.parts).toEqual([{ kind: 'text', text: 'old' }])
    expect(old.interrupted).toBe(true)
  })

  test('a confirmation is pending until decided', () => {
    let s = run(initialState(), ev({ type: 'confirm', actionId: 'a1', items: ['Classify 3 differences'], turnId: 't', seq: 1 }))
    expect(s.pendingConfirm).toEqual({ actionId: 'a1', items: ['Classify 3 differences'] })
    s = reduce(s, { type: 'decided', actionId: 'a1', approve: true })
    expect(s.pendingConfirm).toBeNull()
    expect(s.messages[0]!.parts[0]).toMatchObject({ kind: 'confirm', state: 'approved' })
  })

  test('typing over an open confirmation closes it; typing answers an open question', () => {
    let s = run(
      initialState(),
      ev({ type: 'confirm', actionId: 'a1', items: ['x'], turnId: 't', seq: 1 }),
      ev({ type: 'ask', askId: 'q1', question: 'Which one?', options: ['Paperback', 'Hardback'], multiple: false, other: false, turnId: 't', seq: 2 }),
    )
    expect(s.openAsk?.askId).toBe('q1')
    s = reduce(s, { type: 'user', local: 'L', text: 'Hardback' })
    expect(s.pendingConfirm).toBeNull()
    expect(s.openAsk).toBeNull()
    const parts = s.messages[0]!.parts
    expect(parts[0]).toMatchObject({ kind: 'confirm', state: 'closed' })
    expect(parts[1]).toMatchObject({ kind: 'ask', answered: ['Hardback'] })
  })

  test('an interrupted turn is marked; an empty one disappears', () => {
    const s = run(
      initialState(),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 }),
      ev({ type: 'text', delta: 'So', turnId: 't1', seq: 2 }),
      ev({ type: 'done', conversationId: 'c', reason: 'barge_in', turnId: 't1', seq: 3 }),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't2', seq: 4 }),
      ev({ type: 'done', conversationId: 'c', reason: 'cancelled', turnId: 't2', seq: 5 }),
    )
    expect(s.messages).toHaveLength(1)
    expect(s.messages[0]!.interrupted).toBe(true)
    expect(s.phase).toBe('interrupted')
    expect(reduce(s, { type: 'user', local: 'x', text: 'again' }).phase).toBe('thinking')
  })

  test('phase follows the turn and the voice activity', () => {
    let s = reduce(initialState(), { type: 'voice', activity: 'listening' })
    expect(s.phase).toBe('listening')
    s = reduce(s, ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't', seq: 1 }))
    expect(s.phase).toBe('thinking')
    s = reduce(s, { type: 'voice', activity: 'speaking' })
    expect(s.phase).toBe('speaking')
    s = reduce(s, { type: 'interrupted' })
    expect(s.phase).toBe('interrupted')
    s = run(s, ev({ type: 'done', conversationId: 'c', reason: 'barge_in', turnId: 't', seq: 2 }), { type: 'voice', activity: 'speaking' })
    expect(s.phase).toBe('speaking')
    s = reduce(s, { type: 'voice', activity: null })
    expect(s.phase).toBe('idle')
  })

  test('usage keeps percentages only, whatever else arrives', () => {
    const s = reduce(initialState(), { type: 'usage', usage: { dayPct: 10, monthPct: null, companyPct: 99, monthOut: false, costUsd: 3 } as never })
    expect(s.usage).toEqual({ dayPct: 10, monthPct: null, companyPct: 99, monthOut: false })
  })

  test('usage keeps the weekly share, the binding cap and its reset, nothing more inside them', () => {
    const week = { limit: 'subject_week', scope: 'subject', period: 'week', usedPct: 100, leftPct: 0, resetsAt: '2026-11-20T23:00:00+00:00' }
    const s = reduce(initialState(), {
      type: 'usage',
      usage: { dayPct: null, weekPct: 100, monthPct: null, companyPct: 3, monthOut: false, out: true, binding: { ...week, amount: 5 }, resetsAt: week.resetsAt, caps: [week, { limit: 'x' }] } as never,
    })
    expect(s.usage).toEqual({ dayPct: null, weekPct: 100, monthPct: null, companyPct: 3, monthOut: false, out: true, binding: week, resetsAt: week.resetsAt, caps: [week] })
  })

  test('a turn started elsewhere (server voice) shows what the user said from the returned messages', () => {
    const s = run(
      initialState(),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 'v1', seq: 1 }),
      ev({ type: 'text', delta: 'Here it is.', turnId: 'v1', seq: 2 }),
      ev({
        type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 'v1', seq: 3,
        messages: [{ role: 'user', text: 'Show me order 1042', content: [] }, { role: 'assistant', text: 'Here it is.', content: [] }],
      }),
    )
    expect(s.messages.map((m) => [m.role, m.parts[0]])).toEqual([
      ['user', { kind: 'text', text: 'Show me order 1042' }],
      ['assistant', { kind: 'text', text: 'Here it is.' }],
    ])
  })

  test('a failed request leaves an error on its message and stops waiting', () => {
    let s = reduce(initialState(), { type: 'user', local: 'L', text: 'hello' })
    s = run(s, { type: 'failed', local: 'L', code: 'network', message: 'offline' }, { type: 'settled', local: 'L' })
    expect(s.busy).toBe(false)
    expect(s.error).toEqual({ code: 'network', message: 'offline' })
    expect(s.messages[1]!.parts).toEqual([{ kind: 'error', code: 'network', message: 'offline' }])
  })

  test('a loaded conversation: confirmation outcome, answered question, open ask, running turn', () => {
    const s = reduce(initialState(), {
      type: 'loaded',
      conversation: {
        lastSeq: 40,
        runningTurnId: 'tr',
        pending: { actionId: 'a2', items: ['y'] },
        messages: [
          { id: 'm1', role: 'assistant', text: 'Shall I proceed?', events: [{ type: 'confirm', actionId: 'a1', items: ['x'] }], feedback: null },
          { id: 'm2', role: 'user', text: '', events: [{ type: 'confirmed', approve: false }], feedback: null },
          { id: 'm3', role: 'assistant', text: '', events: [{ type: 'ask', askId: 'q', question: 'Which one?', options: ['A', 'B'], multiple: true, other: false }], feedback: 1 },
          { id: 'm4', role: 'user', text: 'A, B', events: [], feedback: null },
          { id: 'm5', role: 'assistant', text: 'Ok', events: [{ type: 'confirm', actionId: 'a2', items: ['y'] }], feedback: null },
        ],
      },
    })
    expect(s.messages.map((m) => m.id)).toEqual(['m1', 'm3', 'm4', 'm5'])
    expect(s.messages[0]!.parts[1]).toMatchObject({ kind: 'confirm', state: 'rejected' })
    expect(s.messages[1]!.parts[0]).toMatchObject({ kind: 'ask', answered: ['A', 'B'] })
    expect(s.messages[1]!.feedback).toBe(1)
    expect(s.messages[2]!.answer).toBe(true)
    expect(s.messages[3]!.parts[1]).toMatchObject({ kind: 'confirm', state: 'open' })
    expect(s.pendingConfirm?.actionId).toBe('a2')
    expect(s.busy).toBe(true)
    expect(s.lastSeq).toBe(40)
  })

  test('a conversation snapshot older than what the client already shows is dropped', () => {
    const streaming = run(initialState(), { type: 'user', local: 'L', text: 'hello' })
    const empty = { messages: [], pending: null, lastSeq: 0, runningTurnId: null }
    expect(reduce(streaming, { type: 'loaded', conversation: empty })).toBe(streaming)
    const seen = run(initialState(), ev({ type: 'text', delta: 'x', turnId: 't', seq: 5 }))
    expect(reduce(seen, { type: 'loaded', conversation: { ...empty, lastSeq: 4 } })).toBe(seen)
    expect(reduce(seen, { type: 'loaded', conversation: { ...empty, lastSeq: 5 } }).messages).toEqual([])
  })

  test('fromStored keeps the stored id of every message (feedback on answers, rewrite of the last question)', () => {
    const out = fromStored(
      [
        { id: 'u', role: 'user', text: 'a', events: [], feedback: null, rewritable: true },
        { id: 'x', role: 'assistant', text: 'b', events: [], feedback: null },
      ],
      null,
    )
    expect(out.map((m) => m.messageId)).toEqual(['u', 'x'])
    expect(out.map((m) => m.rewritable)).toEqual([true, undefined])
  })

  test('citations: live with their turn, and from stored messages onto the answer that follows the search', () => {
    const cite = (index: number, turnId?: string, seq?: number) => ({
      type: 'citation' as const,
      index,
      kb: 'policies',
      documentId: `d${index}`,
      title: `Doc ${index}`,
      location: { page: 3, section: 'Art. 2', charStart: 0, charEnd: 10 },
      snippet: 'testo',
      url: 'https://x/d',
      ...(turnId ? { turnId, seq } : {}),
    })
    let s = run(
      initialState(),
      ev({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't', seq: 1 }),
      ev(cite(2, 't', 2)),
      ev(cite(1, 't', 3)),
      ev(cite(1, 't', 4)),
      ev({ type: 'text', delta: 'Vedi [1][2].', turnId: 't', seq: 5 }),
    )
    expect(s.messages[0]!.citations!.map((c) => c.index)).toEqual([1, 2])
    expect(s.messages[0]!.citations![0]!.location.page).toBe(3)
    const stored = fromStored(
      [
        { id: 'u', role: 'user', text: 'domanda', events: [], feedback: null },
        { id: 'a1', role: 'assistant', text: '', events: [cite(1) as never], feedback: null },
        { id: 'r', role: 'user', text: '', events: [], feedback: null },
        { id: 'a2', role: 'assistant', text: 'Risposta [1].', events: [], feedback: null },
      ],
      null,
    )
    expect(stored.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(stored[1]!.citations).toEqual([{ ...cite(1) }])
    s = run(initialState(), ev({ type: 'citation', index: 1, kb: 'k', documentId: 'd', title: 't', location: { charStart: 0, charEnd: 1 }, snippet: '', url: null, turnId: 'z', seq: 1 }))
    expect(s.messages[0]!.citations![0]!.url).toBeNull()
  })
})
