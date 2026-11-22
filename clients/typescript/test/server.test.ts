import { EvokaiConsole, EvokaiServer, EvokaiConflictError, EvokaiNotFoundError, EvokaiRateLimitError, EvokaiValidationError, EvokaiError, isExpiredConversation, toUsageShares } from '../src/server'
import { json, mockFetch } from './helpers/fetch'

const BASE = 'https://agents.test/agent'

describe('EvokaiServer', () => {
  test('mints a session token with the server key', async () => {
    const m = mockFetch({
      'POST /agent/v1/session-tokens': () =>
        json({ token: 'tok', expiresAt: 1_900_000_000, conversationId: 'c1', voiceEngine: 'server' }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv_x', fetch: m.fetch })
    const t = await srv.mintSessionToken({
      subject: 'u-42',
      agent: 'assistant',
      tags: ['beta'],
      ttlSeconds: 600,
      limits: { subject_daily_usd: 1 },
      voiceEngine: 'server',
      conversationId: undefined,
    })
    expect(t).toEqual({ token: 'tok', expiresAt: 1_900_000_000, conversationId: 'c1', voiceEngine: 'server' })
    const call = m.calls[0]!
    expect(call.headers.authorization).toBe('Bearer srv_x')
    expect(call.body).toEqual({ subject: 'u-42', agent: 'assistant', tags: ['beta'], ttlSeconds: 600, limits: { subject_daily_usd: 1 }, voiceEngine: 'server' })
  })

  test('mints a token with prompt modules and a locale, sent unchanged', async () => {
    const m = mockFetch({ 'POST /agent/v1/session-tokens': () => json({ token: 'tok', expiresAt: 1, conversationId: 'c1' }) })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv_x', fetch: m.fetch })
    const modules = { tone: { params: { style: 'detailed', shelf_name: 'New arrivals' } }, reader_level: { on: false } }
    await srv.mintSessionToken({ subject: 'u-42', agent: 'assistant', modules, locale: 'it-IT' })
    expect(m.calls[0]!.body).toEqual({ subject: 'u-42', agent: 'assistant', modules, locale: 'it-IT' })
  })

  test('errors are typed from {error, message}', async () => {
    const m = mockFetch({
      'POST /agent/v1/session-tokens': [
        () => json({ error: 'unknown_conversation', message: 'Unknown conversation.' }, 404),
        () => json({ error: 'closed', message: 'This session is closed.' }, 409),
        () => json({ error: 'bad_request', message: 'Invalid request.', fields: ['body.subject'] }, 400),
      ],
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'k', fetch: m.fetch })
    const req = { subject: 's', agent: 'a', conversationId: 'zz' }
    await expect(srv.mintSessionToken(req)).rejects.toMatchObject({ code: 'unknown_conversation', status: 404 })
    await expect(srv.mintSessionToken(req)).rejects.toBeInstanceOf(EvokaiConflictError)
    const e = await srv.mintSessionToken(req).catch((x: unknown) => x)
    expect(e).toBeInstanceOf(EvokaiValidationError)
    expect((e as EvokaiValidationError).fields).toEqual(['body.subject'])
    expect(e).toBeInstanceOf(EvokaiError)
  })

  test('conversations, usage and memories carry the subject in the query', async () => {
    const m = mockFetch({
      'GET /agent/v1/conversations': () => json({ items: [{ id: 'c1', title: 'T', agent: 'a', updatedAt: 'x' }] }),
      'DELETE /agent/v1/conversations/c%2F1': () => json({ ok: true }),
      'GET /agent/v1/usage': () => json({ rows: [], application: { monthlyCapUsd: 10, dailyCapUsd: null, monthUsd: 0, dayUsd: 0 } }),
      'DELETE /agent/v1/memories': () => json({ deleted: 3 }),
      'GET /agent/v1/conversations/c1': () => json({ error: 'not_found', message: 'Not found.' }, 404),
    })
    const srv = new EvokaiServer({ baseUrl: `${BASE}/`, serverKey: 'k', fetch: m.fetch })
    expect(await srv.conversations.list({ subject: 'u 1', limit: 5 })).toHaveLength(1)
    expect(m.calls[0]!.url).toBe(`${BASE}/v1/conversations?subject=u+1&limit=5`)
    await srv.conversations.delete('c/1', { subject: 'u' })
    await srv.usage({ from: new Date('2026-11-01T00:00:00Z'), groupBy: ['subject', 'day'], tag: 'beta' })
    const usageUrl = new URL(m.calls[2]!.url)
    expect(usageUrl.searchParams.get('from')).toBe('2026-11-01T00:00:00.000Z')
    expect(usageUrl.searchParams.get('groupBy')).toBe('subject,day')
    expect(await srv.memories.forget({ subject: 'u' })).toBe(3)
    await expect(srv.conversations.get('c1', { subject: 'u' })).rejects.toBeInstanceOf(EvokaiNotFoundError)
  })

  test('chatJson asks for JSON with the session token', async () => {
    const m = mockFetch({
      'POST /agent/v1/session/chat': () =>
        json({ conversationId: 'c', turnId: 't', reason: 'end_turn', text: 'Ciao', events: [], messages: [{ role: 'user', text: 'hi', content: [] }] }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'k', fetch: m.fetch })
    const r = await srv.chatJson('session-tok', { text: 'hi', history: [] })
    expect(r.messages).toHaveLength(1)
    expect(m.calls[0]!.headers.authorization).toBe('Bearer session-tok')
    expect(m.calls[0]!.headers.accept).toBe('application/json')
    expect(m.calls[0]!.body).toEqual({ mode: 'text', text: 'hi', history: [] })
  })

  test('admin calls use the admin key, and need one', async () => {
    const m = mockFetch({
      'PATCH /agent/v1/admin/application': () => json({ ok: true }),
      'PUT /agent/v1/admin/agents/helper': () => json({ version: 'v2', created: true }),
      'PUT /agent/v1/admin/prompts/p': () => json({ version: 4 }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    await srv.admin.application.patch({ voice_engine: 'server', storage_mode: 'external', allowed_origins: ['https://app.test'] })
    expect(m.calls[0]!.headers.authorization).toBe('Bearer adm')
    expect(await srv.admin.agents.put({ name: 'helper', model: 'anthropic:x' })).toEqual({ version: 'v2', created: true })
    expect(await srv.admin.prompts.put('p', 'text')).toBe(4)
    expect(m.calls.at(-1)!.body).toEqual({ text: 'text' })
    const parameters = { type: 'object', properties: { style: { type: 'string', enum: ['brief'] } } }
    expect(await srv.admin.prompts.put('p', { text: '{{lookup data style}}', parameters, data: { brief: 'Brief.' } })).toBe(4)
    expect(m.calls.at(-1)!.body).toEqual({ text: '{{lookup data style}}', parameters, data: { brief: 'Brief.' } })
    const noAdmin = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', fetch: m.fetch })
    await expect(noAdmin.admin.keys.list()).rejects.toMatchObject({ code: 'no_admin_key' })
  })

  test('a prompt preview posts the sample values and returns the text', async () => {
    const m = mockFetch({ 'POST /agent/v1/admin/prompts/tone/preview': () => json({ text: 'Brief.' }) })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    expect(await srv.admin.prompts.preview('tone', { params: { style: 'brief' } })).toBe('Brief.')
    expect(m.calls[0]!.body).toEqual({ params: { style: 'brief' } })
  })

  test('a transport failure is an EvokaiError with status 0', async () => {
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'k', fetch: () => Promise.reject(new TypeError('fetch failed')) })
    await expect(srv.agents()).rejects.toMatchObject({ status: 0, code: 'network' })
  })
})

describe('runs, console views and the console client', () => {
  test('runAgent posts to /v1/agents/{name}/run and types the result', async () => {
    const m = mockFetch({
      'POST /agent/v1/agents/triage/run': () =>
        json({ runId: 'r1', output: { priority: 'high' }, model: 'm', usage: { inputTokens: 10, outputTokens: 5, cacheReadTokens: 0, cacheWriteTokens: 0, costUsd: 0.001, toolCalls: 0 }, conversationId: null }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', fetch: m.fetch })
    const r = await srv.runAgent('triage', { input: [{ role: 'user', text: 'Printer on fire' }], attachments: [{ name: 'ticket.txt', text: 'body' }], subject: 's1' })
    expect(r.output).toEqual({ priority: 'high' })
    expect(m.calls[0]!.body).toEqual({ input: [{ role: 'user', text: 'Printer on fire' }], attachments: [{ name: 'ticket.txt', text: 'body' }], subject: 's1' })
  })

  test('run errors: channel, limit, invalid_output; expired conversations are recognisable', async () => {
    const m = mockFetch({
      'POST /agent/v1/agents/a/run': [
        () => json({ error: 'channel', message: 'This agent cannot be run from the server.' }, 403),
        () => json({ error: 'limit', message: 'Budget used up.' }, 429),
        () => json({ error: 'invalid_output', message: 'Invalid output.' }, 502),
      ],
      'POST /agent/v1/session-tokens': () => json({ error: 'expired_conversation', message: 'This conversation has expired.' }, 404),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', fetch: m.fetch })
    await expect(srv.runAgent('a', { input: 'x' })).rejects.toMatchObject({ status: 403, code: 'channel' })
    await expect(srv.runAgent('a', { input: 'x' })).rejects.toBeInstanceOf(EvokaiRateLimitError)
    await expect(srv.runAgent('a', { input: 'x' })).rejects.toMatchObject({ status: 502, code: 'invalid_output' })
    const e = await srv.mintSessionToken({ subject: 's', agent: 'a', conversationId: 'old' }).catch((x: unknown) => x)
    expect(isExpiredConversation(e)).toBe(true)
  })

  test('admin views: versions, MCP test, conversations, health', async () => {
    const m = mockFetch({
      'GET /agent/v1/admin/agents/helper/versions': () => json({ items: [{ version: 'v2', active: true, createdAt: 'x', definition: { name: 'helper', model: 'm' } }] }),
      'POST /agent/v1/admin/mcp-servers/crm/test': () => json({ ok: true, tools: [] }),
      'GET /agent/v1/admin/conversations': () => json({ storage: 'evokai', items: [] }),
      'GET /agent/v1/admin/health': () => json({ database: 'ok', voiceEnabled: false, modelAllow: [], version: '0.1.0' }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    expect((await srv.admin.agents.versions('helper'))[0]!.version).toBe('v2')
    expect((await srv.admin.mcpServers.test('crm', 'user-token')).ok).toBe(true)
    expect(m.calls[1]!.body).toEqual({ credential: 'user-token' })
    await srv.admin.conversations.list({ subject: 'u1' })
    expect(m.calls[2]!.url).toContain('subject=u1')
    expect((await srv.admin.health()).database).toBe('ok')
    expect(m.calls.every((c) => c.headers.authorization === 'Bearer adm')).toBe(true)
  })

  test('traces, version stats and conversation review with notes', async () => {
    const m = mockFetch({
      'GET /agent/v1/admin/traces': () => json({ items: [{ id: 't1', durationMs: 900, capPct: 0.1 }], next: null, storage: 'evokai' }),
      'GET /agent/v1/admin/traces/t%2F1': () => json({ id: 't/1', spans: [{ id: 'a', kind: 'turn', name: 'invoke_agent x', start: 0, ms: 10, status: 'ok' }], conversation: null, notes: [] }),
      'GET /agent/v1/admin/agents/helper/version-stats': () => json({ agent: 'helper', items: [{ version: 'v2', turns: 3, usageShare: 75 }], from: 'a', to: 'b' }),
      'GET /agent/v1/admin/conversations': () => json({ storage: 'evokai', items: [], next: null }),
      'POST /agent/v1/admin/conversations/c1/notes': () => json({ id: 'n1', turnId: 't1', label: 'good', note: '', author: 'x', createdAt: 'y' }, 201),
      'DELETE /agent/v1/admin/conversations/c1/notes/n1': () => json({ ok: true }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    const page = await srv.admin.traces.list({ agent: 'helper', guard: true, errors: false, feedback: 'down', minMs: 5000, from: new Date('2026-11-01T00:00:00Z') })
    expect(page.items[0]!.id).toBe('t1')
    const url = new URL(m.calls[0]!.url)
    expect(Object.fromEntries(url.searchParams)).toEqual({ agent: 'helper', guard: 'true', feedback: 'down', minMs: '5000', from: '2026-11-01T00:00:00.000Z' })
    expect((await srv.admin.traces.get('t/1')).spans[0]!.kind).toBe('turn')
    const versions = await srv.admin.traces.versions('helper', { from: '2026-11-01' })
    expect(versions.items[0]!.usageShare).toBe(75)
    expect(m.calls[2]!.url).toContain('from=2026-11-01')
    await srv.admin.conversations.list({ feedback: 'down', noted: true, agent: 'helper' })
    expect(m.calls[3]!.url).toContain('feedback=down')
    expect(m.calls[3]!.url).toContain('noted=true')
    expect((await srv.admin.conversations.addNote('c1', { turnId: 't1', label: 'good' })).id).toBe('n1')
    expect(m.calls[4]!.body).toEqual({ turnId: 't1', label: 'good' })
    await srv.admin.conversations.deleteNote('c1', 'n1')
    expect(m.calls[5]!.method).toBe('DELETE')
  })

  test('grants, key rotation, skills, activity, usage summary and export', async () => {
    const view = { groups: [], summary: {}, overrides: 1, modelAllow: [], applicationModels: [], storageMode: 'evokai' }
    const m = mockFetch({
      'GET /agent/v1/admin/grants': () => json(view),
      'PATCH /agent/v1/admin/grants': () => json({ ...view, overrides: 2 }),
      'POST /agent/v1/admin/keys/k1/rotate': () => json({ id: 'k2', key: 'evk_srv_x_y', replaces: 'k1' }),
      'GET /agent/v1/admin/skills/returns-policy': () => json({ name: 'returns-policy', description: 'd', body: 'b', size: 1, updatedAt: 'x' }),
      'GET /agent/v1/admin/activity': () => json({ items: [{ at: 'x', kind: 'grant', name: 'client:memory', detail: false, by: null }] }),
      'GET /agent/v1/admin/usage/summary': () => json({ turns: 3, fallbackTurns: 1, medianFirstTokenMs: 900, subjects: 2, tags: ['team:ops'], from: 'a', to: 'b' }),
      'GET /agent/v1/admin/export': () => json({ yaml: 'application: {}', filename: 'bookshop.yaml' }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    expect((await srv.admin.grants.get()).overrides).toBe(1)
    const changed = await srv.admin.grants.change([{ kind: 'client', name: 'memory', value: false }, { kind: 'model', name: 'eu.x', value: null }])
    expect(changed.overrides).toBe(2)
    expect(m.calls[1]!.body).toEqual({ changes: [{ kind: 'client', name: 'memory', value: false }, { kind: 'model', name: 'eu.x', value: null }] })
    expect((await srv.admin.keys.rotate('k1')).replaces).toBe('k1')
    expect((await srv.admin.skills.get('returns-policy')).body).toBe('b')
    expect((await srv.admin.activity({ limit: 5 }))[0]!.kind).toBe('grant')
    expect(m.calls[4]!.url).toContain('limit=5')
    expect((await srv.admin.usageSummary({ tag: 'team:ops' })).fallbackTurns).toBe(1)
    expect(m.calls[5]!.url).toContain('tag=team%3Aops')
    expect((await srv.admin.export()).filename).toBe('bookshop.yaml')
  })

  test('EvokaiConsole: applications and health with the cookie', async () => {
    const m = mockFetch({
      'GET /agent/v1/console/applications': () => json({ items: [], canCreate: true }),
      'GET /agent/v1/console/health': () => json({ database: { status: 'ok' } }),
    })
    const c = new EvokaiConsole({ baseUrl: BASE, fetch: m.fetch })
    expect((await c.applications()).canCreate).toBe(true)
    await c.health(true)
    expect(m.calls[1]!.url).toContain('check=true')
    expect(m.calls.every((x) => x.headers['x-evokai-console'] === '1')).toBe(true)
  })

  test('EvokaiConsole: cookie mode, console header, operator application, no Authorization', async () => {
    const seen: RequestInit[] = []
    const m = mockFetch({
      'POST /agent/v1/console/login': () => new Response(null, { status: 204 }),
      'GET /agent/v1/console/me': () => json({ kind: 'op', applicationId: null, expiresAt: 1, modelAllow: ['anthropic:*'], voiceEnabled: false }),
      'GET /agent/v1/operator/applications': () => json({ items: [{ id: 'app1', slug: 'a', name: 'A', status: 'active', storage_mode: 'evokai', monthly_cap_usd: 10, daily_cap_usd: null }] }),
      'GET /agent/v1/admin/application': () => json({ id: 'app1' }),
    })
    const fetchFn = (u: string, init?: RequestInit) => {
      seen.push(init ?? {})
      return m.fetch(u, init)
    }
    const c = new EvokaiConsole({ baseUrl: BASE, fetch: fetchFn })
    await c.login('adm_secret')
    expect((await c.me()).kind).toBe('op')
    const apps = await c.operator.applications.list()
    c.application = apps[0]!.id
    await c.admin.application.get()
    expect(seen.every((i) => i.credentials === 'same-origin')).toBe(true)
    expect(m.calls.every((x) => x.headers['x-evokai-console'] === '1' && !x.headers.authorization)).toBe(true)
    expect(m.calls.at(-1)!.headers['x-evokai-application']).toBe('app1')
    expect(m.calls[0]!.body).toEqual({ key: 'adm_secret' })
  })

  test('EvokaiConsole: preferences are read, saved whole and reset with the cookie', async () => {
    const prefs = {
      version: 1 as const,
      language: 'it' as const,
      dateFormat: 'auto' as const,
      timeFormat: '24h' as const,
      theme: 'dark' as const,
      density: 'comfortable' as const,
      motion: 'reduce' as const,
      mascot: 'still' as const,
      textSize: 125 as const,
      contrast: 'more' as const,
      focusAlways: true,
      underlineLinks: false,
    }
    const m = mockFetch({
      'GET /agent/v1/console/me/preferences': () => json({ preferences: { ...prefs, language: 'auto' }, updatedAt: null }),
      'PUT /agent/v1/console/me/preferences': () => json({ preferences: prefs, updatedAt: '2026-11-15T10:00:00+00:00' }),
      'DELETE /agent/v1/console/me/preferences': () => json({ preferences: { ...prefs, language: 'auto' }, updatedAt: null }),
    })
    const c = new EvokaiConsole({ baseUrl: BASE, fetch: m.fetch })
    expect((await c.preferences.get()).updatedAt).toBeNull()
    expect((await c.preferences.put(prefs)).preferences.textSize).toBe(125)
    expect(m.calls[1]!.body).toEqual(prefs)
    expect((await c.preferences.reset()).updatedAt).toBeNull()
    expect(m.calls.map((x) => x.method)).toEqual(['GET', 'PUT', 'DELETE'])
    expect(m.calls.every((x) => x.headers['x-evokai-console'] === '1' && !x.headers.authorization)).toBe(true)
  })

  test('EvokaiConsole: a one-time code signs in with the cookie, a spent one is a login_code error', async () => {
    let used = false
    const m = mockFetch({
      'POST /agent/v1/console/login': () => {
        if (used) return json({ error: 'login_code', message: 'This sign-in link has expired.' }, 401)
        used = true
        return new Response(null, { status: 204 })
      },
    })
    const c = new EvokaiConsole({ baseUrl: BASE, fetch: m.fetch })
    await c.loginWithCode('one-time-code')
    expect(m.calls[0]!.body).toEqual({ code: 'one-time-code' })
    expect(m.calls[0]!.headers['x-evokai-console']).toBe('1')
    expect(m.calls[0]!.headers.authorization).toBeUndefined()
    await expect(c.loginWithCode('one-time-code')).rejects.toMatchObject({ status: 401, code: 'login_code' })
  })
})

test('toUsageShares keeps tokens and calls, turns money into shares of the cap, and drops amounts', () => {
  const r = toUsageShares({
    rows: [{ subject: 'a', costUsd: 2.5, inputTokens: 100, outputTokens: 20, cacheReadTokens: 60, cacheWriteTokens: 10, characters: 0, audioMs: 0, textUnits: 0, calls: 3 }],
    application: { monthlyCapUsd: 10, dailyCapUsd: null, monthUsd: 4, dayUsd: 1 },
  })
  expect(r).toEqual({
    rows: [{ subject: 'a', inputTokens: 100, outputTokens: 20, cacheReadTokens: 60, cacheWriteTokens: 10, characters: 0, audioMs: 0, textUnits: 0, calls: 3, capPct: 25 }],
    application: { monthPct: 40, dayPct: null },
  })
  expect(JSON.stringify(r)).not.toMatch(/usd|cost/i)
})

describe('knowledge bases', () => {
  test('upload sends the file as is, with its name, access groups and metadata in the query', async () => {
    const doc = { id: 'd1', source: 'a.pdf', status: 'queued' }
    const m = mockFetch({
      'POST /agent/v1/admin/knowledge/policies/documents': () => json({ document: doc, outcome: 'created' }),
      'POST /agent/v1/admin/knowledge/policies/search': () => json({ items: [], retrieval: { top_k: 5 } }),
      'PUT /agent/v1/admin/knowledge/policies': () => json({ name: 'policies', reingested: 0 }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 's', adminKey: 'adm_x', fetch: m.fetch })
    const bytes = new Uint8Array([37, 80, 68, 70])
    const r = await srv.admin.knowledge.upload('policies', bytes, {
      filename: 'a.pdf',
      contentType: 'application/pdf',
      access: ['finance', 'staff'],
      metadata: { year: 2026 },
    })
    expect(r.outcome).toBe('created')
    const call = m.calls[0]!
    const url = new URL(call.url)
    expect(url.searchParams.get('filename')).toBe('a.pdf')
    expect(url.searchParams.get('access')).toBe('finance,staff')
    expect(JSON.parse(url.searchParams.get('metadata')!)).toEqual({ year: 2026 })
    expect(call.headers['content-type']).toBe('application/pdf')
    expect(call.headers.authorization).toBe('Bearer adm_x')
    expect(call.body).toBe(bytes)
    await srv.admin.knowledge.search('policies', { query: 'returns', groups: ['staff'] })
    expect(m.calls[1]!.body).toEqual({ query: 'returns', groups: ['staff'] })
    await srv.admin.knowledge.put('policies', { title: 'Shop policies', dimensions: 512 })
    expect(m.calls[2]!.body).toEqual({ title: 'Shop policies', dimensions: 512 })
  })

  test('server runs pass groups and return citations', async () => {
    const m = mockFetch({
      'POST /agent/v1/agents/triage/run': () =>
        json({ runId: 'r', output: 'ok [1]', model: 'm', usage: {}, conversationId: null, citations: [{ type: 'citation', index: 1 }] }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 's', fetch: m.fetch })
    const r = await srv.runAgent('triage', { input: 'x', groups: ['finance'] })
    expect(r.citations[0]!.index).toBe(1)
    expect(m.calls[0]!.body).toEqual({ input: 'x', groups: ['finance'] })
  })
})
