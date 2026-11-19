/** A fake Evokai behind `fetch`: a token endpoint, the session routes and scripted turns streamed as SSE. */
import type { Connection, EvokaiEvent, Memory, MemoryState } from '@evokai/evokai-client/browser'

export interface Call {
  method: string
  url: string
  body: unknown
}

export function sseBody(events: Array<Partial<EvokaiEvent> & { type: string }>): string {
  return events.map((e, i) => `event: ${e.type}\ndata: ${JSON.stringify({ turnId: 't1', seq: i + 1, ...e })}\n\n`).join('')
}

export interface FakeMemory {
  mode: MemoryState['mode']
  consent: MemoryState['consent']
  items: Memory[]
}

export function memory(id: string, text: string, kind: Memory['kind'], extra: Partial<Memory> = {}): Memory {
  return { id, text, kind, agent: 'helper', source: 'agent', createdAt: '2026-11-03T10:00:00Z', updatedAt: null, expiresAt: null, lastUsedAt: null, uses: 0, ...extra }
}

/** The memory state the service would answer for a mode and a consent. */
export function memoryState(m: { mode: MemoryState['mode']; consent: MemoryState['consent'] }): MemoryState {
  const active = m.mode === 'on' ? m.consent !== 'declined' : m.mode === 'ask' ? m.consent === 'granted' : false
  return { mode: m.mode, consent: m.consent, active, ask: m.mode === 'ask' && m.consent === 'unknown' }
}

export function fakeEvokai(
  turns: Array<Array<Partial<EvokaiEvent> & { type: string }>>,
  mem: FakeMemory = { mode: 'on', consent: 'unknown', items: [] },
  connections: Connection[] = [],
) {
  const calls: Call[] = []
  let seq = 0
  const usage = { dayPct: 12.5, monthPct: 4, companyPct: 2, monthOut: false }
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, url, body })
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
    if (url.endsWith('/token')) return json({ token: 'tok', conversationId: 'c1', expiresAt: Date.now() / 1000 + 600, usage, suggestions: ['How did last week go?', 'What ships this week?'] })
    if (url.endsWith('/v1/session')) return json({ conversationId: 'c1', agent: 'helper', expiresAt: Date.now() / 1000 + 600, usage, memories: [], memory: memoryState(mem) })
    if (url.endsWith('/v1/session/memories') && method === 'GET') return json({ memory: memoryState(mem), items: memoryState(mem).active ? mem.items : [] })
    if (url.endsWith('/v1/session/memory-consent')) {
      if (mem.mode === 'off') return json({ error: 'memory_off', message: 'Memory is off for this application.' }, 409)
      const granted = (body as { granted: boolean }).granted
      mem.consent = granted ? 'granted' : 'declined'
      const deleted = granted ? 0 : mem.items.length
      if (!granted) mem.items = []
      return json({ memory: memoryState(mem), deleted })
    }
    const one = /\/v1\/session\/memories\/([^/?]+)$/.exec(url)
    if (one && method === 'PATCH') {
      const text = (body as { text: string }).text
      if (/password/i.test(text)) return json({ error: 'memory_refused', message: 'A memory may not hold instructions, secrets or personal data.' }, 400)
      const at = mem.items.findIndex((x) => x.id === one[1])
      if (at < 0) return json({ error: 'unknown_memory' }, 404)
      const next = { ...mem.items[at]!, text, source: 'person' as const, updatedAt: '2026-11-15T09:00:00Z' }
      mem.items[at] = next
      return json(next)
    }
    if (method === 'DELETE' && url.includes('/v1/session/memories')) {
      const before = mem.items.length
      mem.items = one ? mem.items.filter((x) => x.id !== one[1]) : []
      return json({ deleted: before - mem.items.length })
    }
    if (url.endsWith('/v1/session/connections') && method === 'GET') return json({ connections })
    const conn = /\/v1\/session\/connections\/([^/]+)(\/start)?$/.exec(url)
    if (conn && method === 'POST') return json({ provider: conn[1], authorizeUrl: `https://login.test/authorize?p=${conn[1]}`, expiresAt: 1 })
    if (conn && method === 'DELETE') {
      const c = connections.find((x) => x.provider === conn[1])
      if (c) Object.assign(c, { status: 'not_connected', account: null, lastUsedAt: null })
      return new Response(null, { status: 204 })
    }
    if (url.endsWith('/v1/session/conversation')) return json({ messages: [], pending: null, lastSeq: 0, runningTurnId: null })
    if (url.endsWith('/v1/session/chat')) {
      const events = (turns.shift() ?? [{ type: 'text', delta: 'ok' }]).map((e) => ({ ...e, seq: ++seq, turnId: `t${seq}` }))
      const all = [{ type: 'conversation', id: 'c1', turnStarted: true, seq: ++seq, turnId: 'tt' }, ...events, { type: 'done', conversationId: 'c1', reason: 'end_turn', seq: ++seq, turnId: 'tt' }]
      const text = all.map((e) => `event: ${e.type}\ndata: ${JSON.stringify({ ...e, turnId: 'tt' })}\n\n`).join('')
      return new Response(text, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }
    if (url.includes('/feedback')) return json({ ok: true })
    if (url.endsWith('/conversations')) return json({ items: [{ id: 'c0', title: 'Order 1042' }, { id: 'c9', title: 'Conversation' }] })
    return json({ error: 'not_found' }, 404)
  }
  return { fetch, calls, usage, mem, connections }
}

export const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms))
