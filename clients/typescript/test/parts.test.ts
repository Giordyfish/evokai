import type { EvokaiEvent, StoredMessage } from '../src/core/types'
import { fromHistory, fromStored, initialState, reduce, type ChatMessage, type SessionState } from '../src/browser/store'

const chart = { type: 'donut', title: 'Q', subtitle: '', unit: 'count', points: [{ label: 'A', value: 3 }, { label: 'B', value: 1 }] }

/** A turn as it streamed: text, a tool, more text and a chart, a citation, a last sentence. */
const live: EvokaiEvent[] = [
  { type: 'conversation', id: 'c', turnStarted: true },
  { type: 'text', delta: 'Let me ' },
  { type: 'text', delta: 'check. ' },
  { type: 'tool', name: 'orders_month', status: 'start' },
  { type: 'tool', name: 'orders_month', status: 'done' },
  { type: 'text', delta: '24 orders [1]. ' },
  { type: 'citation', index: 1, kb: 'kb', documentId: 'd', title: 'Doc', location: { charStart: 0, charEnd: 4 }, snippet: 's', url: null },
  { type: 'chart', chart },
  { type: 'text', delta: 'Done.' },
  { type: 'done', conversationId: 'c', reason: 'end_turn' },
] as unknown as EvokaiEvent[]

/** The same turn as the service stores it: one assistant message per model step, each with its parts in order. */
const stored: StoredMessage[] = [
  { id: 'u1', turnId: 't', role: 'user', text: 'Go', events: [], feedback: null },
  {
    id: 'a1',
    turnId: 't',
    role: 'assistant',
    text: 'Let me check. ',
    events: [
      { type: 'text', delta: 'Let me check. ' },
      { type: 'tool', name: 'orders_month', status: 'done' },
    ],
    feedback: null,
  },
  { id: 'r1', turnId: 't', role: 'user', text: '', events: [], feedback: null },
  {
    id: 'a2',
    turnId: 't',
    role: 'assistant',
    text: '24 orders [1]. ',
    events: [
      { type: 'text', delta: '24 orders [1]. ' },
      live[6]!,
      { type: 'chart', chart },
    ],
    feedback: null,
  },
  { id: 'a3', turnId: 't', role: 'assistant', text: 'Done.', events: [{ type: 'text', delta: 'Done.' }], feedback: 1 },
] as unknown as StoredMessage[]

function streamed(events: EvokaiEvent[]): SessionState {
  let s = reduce(initialState(), { type: 'user', local: 'l1', text: 'Go' })
  s = reduce(s, { type: 'bind', local: 'l1', turnId: 't' })
  let seq = 0
  for (const e of events) s = reduce(s, { type: 'event', event: { ...e, turnId: 't', seq: ++seq } as EvokaiEvent })
  return reduce(s, { type: 'settled', local: 'l1' })
}

const shown = (msgs: ChatMessage[]) => msgs.map((m) => ({ role: m.role, parts: m.parts, citations: m.citations ?? [] }))

describe('ordered parts', () => {
  test('a reloaded turn shows the same parts, in the same order, as it did live', () => {
    const now = streamed(live)
    const reloaded = fromStored(stored, null)
    expect(shown(reloaded)).toEqual(shown(now.messages))
    expect(reloaded[1]!.parts.map((p) => p.kind)).toEqual(['text', 'tool', 'text', 'chart', 'text'])
    // One message per turn: thumbs go to the last stored message with text.
    expect(reloaded[1]).toMatchObject({ messageId: 'a3', feedback: 1, turnId: 't' })
  })

  test('older messages show their text first, then their events, one message per turn', () => {
    const old = [
      { id: 'u1', role: 'user', text: 'Go', events: [], feedback: null },
      { id: 'a1', role: 'assistant', text: 'Let me check. ', events: [], feedback: null },
      { id: 'a2', role: 'assistant', text: '24 orders. ', events: [{ type: 'chart', chart }], feedback: null },
      { id: 'u2', role: 'user', text: 'Thanks', events: [], feedback: null },
      { id: 'a3', role: 'assistant', text: 'You are welcome.', events: [], feedback: null },
    ] as unknown as StoredMessage[]
    const msgs = fromStored(old, null)
    expect(msgs.map((m) => [m.role, m.parts.map((p) => p.kind)])).toEqual([
      ['user', ['text']],
      ['assistant', ['text', 'text', 'chart']],
      ['user', ['text']],
      ['assistant', ['text']],
    ])
  })

  test('different turns never join, even without a question between them (a decision)', () => {
    const rows = [
      { id: 'u1', turnId: 't1', role: 'user', text: 'Refund', events: [], feedback: null },
      { id: 'a1', turnId: 't1', role: 'assistant', text: '', events: [{ type: 'confirm', actionId: 'x', items: ['Refund'] }], feedback: null },
      { id: 'u2', turnId: 't2', role: 'user', text: '', events: [{ type: 'confirmed', approve: true }], feedback: null },
      { id: 'a2', turnId: 't2', role: 'assistant', text: 'Refunded.', events: [{ type: 'tool', name: 'order_refund', status: 'done' }, { type: 'text', delta: 'Refunded.' }], feedback: null },
    ] as unknown as StoredMessage[]
    const msgs = fromStored(rows, null)
    expect(msgs.map((m) => m.parts.map((p) => p.kind))).toEqual([['text'], ['confirm'], ['tool', 'text']])
    expect(msgs[1]!.parts[0]).toMatchObject({ kind: 'confirm', state: 'approved' })
  })

  test('a client tool that waited shows how it ended; an error and an unfinished call are kept as stored', () => {
    const rows = [
      {
        id: 'a1',
        turnId: 't',
        role: 'assistant',
        text: '',
        events: [
          { type: 'client_tool', callId: 'k', name: 'open_cart', args: { id: 1 }, wait: true, status: 'done' },
          { type: 'error', code: 'model', message: 'Unavailable' },
        ],
        feedback: null,
      },
    ] as unknown as StoredMessage[]
    expect(fromStored(rows, null)[0]!.parts).toEqual([
      { kind: 'tool', name: 'open_cart', status: 'done', client: true, callId: 'k', args: { id: 1 } },
      { kind: 'error', code: 'model', message: 'Unavailable' },
    ])
  })

  test('an application history with parts reloads like the service storage', () => {
    const history = stored
      .filter((m) => m.id !== 'u1')
      .map((m) => ({ role: m.role, text: m.text, events: m.events as unknown as Record<string, unknown>[], content: [] }))
    const msgs = fromHistory([{ role: 'user', text: 'Go', events: [], content: [] }, ...history])
    expect(msgs[1]!.parts.map((p) => p.kind)).toEqual(['text', 'tool', 'text', 'chart', 'text'])
  })
})

describe('reasoning and tool arguments (0.3)', () => {
  const turn: EvokaiEvent[] = [
    { type: 'conversation', id: 'c', turnStarted: true },
    { type: 'reasoning', delta: 'I look up ' },
    { type: 'reasoning', delta: 'October.' },
    { type: 'tool', name: 'orders_month', id: 'a', status: 'args', args: {} },
    { type: 'tool', name: 'orders_month', id: 'a', status: 'args', args: { period: '2026-10' } },
    { type: 'tool', name: 'order_refund', id: 'r', status: 'args', args: { reason: 'dam' } },
    { type: 'tool', name: 'orders_month', id: 'a', status: 'start' },
    { type: 'tool', name: 'orders_month', id: 'a', status: 'done' },
    { type: 'tool', name: 'order_refund', id: 'r', status: 'waiting' },
    { type: 'done', conversationId: 'c', reason: 'confirm' },
  ] as unknown as EvokaiEvent[]
  /** As the service stores it (`evokai.sessions.parts`): consecutive reasoning joined, one card per call. */
  const kept: StoredMessage[] = [
    { id: 'u1', turnId: 't', role: 'user', text: 'Go', events: [], feedback: null },
    {
      id: 'a1',
      turnId: 't',
      role: 'assistant',
      text: '',
      events: [
        { type: 'reasoning', delta: 'I look up October.' },
        { type: 'tool', name: 'orders_month', id: 'a', status: 'done', args: { period: '2026-10' } },
        { type: 'tool', name: 'order_refund', id: 'r', status: 'waiting', args: { reason: 'dam' } },
      ],
      feedback: null,
    },
  ] as unknown as StoredMessage[]

  test('a reload shows the reasoning and the cards as they ended live', () => {
    const now = streamed(turn)
    expect(shown(fromStored(kept, null))).toEqual(shown(now.messages))
    expect(now.messages[1]!.parts).toEqual([
      { kind: 'reasoning', text: 'I look up October.' },
      { kind: 'tool', name: 'orders_month', status: 'done', callId: 'a', args: { period: '2026-10' } },
      { kind: 'tool', name: 'order_refund', status: 'waiting', callId: 'r', args: { reason: 'dam' } },
    ])
  })

  test('a call still being written when the turn ends is dropped, as the service drops it', () => {
    const cut = [
      { type: 'conversation', id: 'c', turnStarted: true },
      { type: 'text', delta: 'Wait.' },
      { type: 'tool', name: 'orders_month', id: 'a', status: 'args', args: { per: null } },
      { type: 'done', conversationId: 'c', reason: 'cancelled' },
    ] as unknown as EvokaiEvent[]
    expect(streamed(cut).messages[1]!.parts).toEqual([{ kind: 'text', text: 'Wait.' }])
  })
})
