import { EvokaiSession } from '../src/browser/session'
import { fromStored, initialState, reduce } from '../src/browser/store'
import { json, mockFetch } from './helpers/fetch'

const BASE = 'https://agents.test/agent'

describe('connections', () => {
  test('list, start and disconnect go to the session API with the token', async () => {
    const m = mockFetch({
      'GET /agent/v1/session/connections': () =>
        json({ connections: [{ provider: 'microsoft365', title: 'Microsoft 365', status: 'not_connected', scopes: [], families: ['mail'] }] }),
      'POST /agent/v1/session/connections/microsoft365/start': () =>
        json({ provider: 'microsoft365', authorizeUrl: 'https://login.example/authorize?state=s', expiresAt: 1 }),
      'DELETE /agent/v1/session/connections/microsoft365': () => new Response(null, { status: 204 }),
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    const list = await s.connections()
    expect(list[0]!.status).toBe('not_connected')
    const started = await s.startConnection('microsoft365', 'https://app.test/back')
    expect(started.authorizeUrl).toContain('state=s')
    expect(m.calls[1]!.body).toEqual({ returnTo: 'https://app.test/back' })
    await s.disconnect('microsoft365')
    expect(m.calls.map((c) => c.headers.authorization)).toEqual(['Bearer tok', 'Bearer tok', 'Bearer tok'])
  })

  test('a connect event becomes a card part, live and from a stored conversation', () => {
    let s = reduce(initialState(), {
      type: 'event',
      event: { type: 'conversation', id: 'c', turnStarted: true, turnId: 't', seq: 1 },
    })
    s = reduce(s, {
      type: 'event',
      event: { type: 'connect', provider: 'google', title: 'Google Workspace', server: 'gws', turnId: 't', seq: 2 },
    })
    expect(s.messages.at(-1)!.parts).toEqual([{ kind: 'connect', provider: 'google', title: 'Google Workspace' }])
    const stored = fromStored(
      [{ id: 'a', role: 'assistant', text: 'Connect first.', events: [{ type: 'connect', provider: 'microsoft365', title: 'Microsoft 365', server: 'm365' }], feedback: null }],
      null,
    )
    expect(stored[0]!.parts[1]).toEqual({ kind: 'connect', provider: 'microsoft365', title: 'Microsoft 365' })
  })
})
