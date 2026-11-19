import { EvokaiServer, EvokaiConsole } from '../src/server'
import { json, mockFetch } from './helpers/fetch'

const BASE = 'https://agents.test/agent'

describe('workflows', () => {
  test('a backend starts a run idempotently, lists approvals for its users and decides', async () => {
    const m = mockFetch({
      'POST /agent/v1/workflows/returns-reminder/runs': () =>
        json({ id: 'r1', workflow: 'returns-reminder', status: 'running', capPct: 0, created: true }, 202),
      'GET /agent/v1/workflows/approvals': () => json({ items: [{ id: 'a1', question: 'Send?', summary: ['Send 12 reminders'] }] }),
      'POST /agent/v1/workflows/approvals/a1': () => json({ id: 'a1', status: 'approved' }),
      'GET /agent/v1/workflows/runs/r1': () => json({ id: 'r1', status: 'done', steps: [] }),
      'POST /agent/v1/workflows/runs/r1/cancel': () => json({ id: 'r1', status: 'cancelled' }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv_x', fetch: m.fetch })
    const run = await srv.workflows.start('returns-reminder', { input: { week: '2026-W45' }, subject: 'c-1', idempotencyKey: 'w45' })
    expect(run.created).toBe(true)
    expect(m.calls[0]!.body).toEqual({ input: { week: '2026-W45' }, subject: 'c-1', idempotencyKey: 'w45' })
    expect(m.calls[0]!.headers.authorization).toBe('Bearer srv_x')

    const items = await srv.workflows.approvals({ groups: ['customer-service', 'ops'] })
    expect(items[0]!.summary).toEqual(['Send 12 reminders'])
    const url = new URL(m.calls[1]!.url)
    expect(url.searchParams.getAll('group')).toEqual(['customer-service', 'ops'])
    expect(url.searchParams.has('status')).toBe(false)

    const decided = await srv.workflows.decide('a1', { decision: 'approve', comment: 'ok', by: { name: 'Ada', groups: ['customer-service'] } })
    expect(decided.status).toBe('approved')
    expect(m.calls[2]!.body).toEqual({ decision: 'approve', comment: 'ok', by: { name: 'Ada', groups: ['customer-service'] } })
    expect((await srv.workflows.run('r1')).status).toBe('done')
    expect((await srv.workflows.cancel('r1')).status).toBe('cancelled')
  })

  test('the admin API validates, saves, starts, retries and decides', async () => {
    const m = mockFetch({
      'POST /agent/v1/admin/workflows/validate': () =>
        json({ ok: false, problems: [{ path: 'steps[0].agent', message: 'unknown agent x', line: 4 }], definition: null, version: null, outline: [] }),
      'PUT /agent/v1/admin/workflows/triage': () => json({ version: 'abc123def456', created: true }),
      'POST /agent/v1/admin/workflows/triage/runs': () => json({ id: 'r2', status: 'running' }, 202),
      'POST /agent/v1/admin/workflows/runs/r2/retry': () => json({ id: 'r2', status: 'running' }),
      'GET /agent/v1/admin/workflows/runs': () => json({ items: [], next: null }),
      'POST /agent/v1/admin/workflows/approvals/a9': () => json({ id: 'a9', status: 'rejected' }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 's', adminKey: 'adm_x', fetch: m.fetch })
    const v = await srv.admin.workflows.validate({ text: 'name: triage', format: 'yaml' })
    expect(v.problems[0]!.line).toBe(4)
    expect(await srv.admin.workflows.put('triage', { steps: [] })).toEqual({ version: 'abc123def456', created: true })
    expect(m.calls[1]!.headers.authorization).toBe('Bearer adm_x')
    await srv.admin.workflows.start('triage')
    expect(m.calls[2]!.body).toEqual({ input: {} })
    await srv.admin.workflows.retry('r2')
    await srv.admin.workflows.runs({ status: 'failed', workflow: 'triage' })
    expect(new URL(m.calls[4]!.url).search).toBe('?status=failed&workflow=triage')
    expect((await srv.admin.workflows.decide('a9', { decision: 'reject' })).status).toBe('rejected')
  })

  test('the console reaches the same routes with its cookie', async () => {
    const m = mockFetch({ 'GET /agent/v1/admin/workflows': () => json({ items: [], pendingApprovals: 2, notifyAllowed: false }) })
    const c = new EvokaiConsole({ baseUrl: BASE, fetch: m.fetch })
    expect((await c.admin.workflows.list()).pendingApprovals).toBe(2)
    expect(m.calls[0]!.headers['x-evokai-console']).toBe('1')
  })
})
