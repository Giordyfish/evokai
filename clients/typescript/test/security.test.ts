import { EvokaiConsole, EvokaiServer } from '../src/server'
import { json, mockFetch } from './helpers/fetch'

const BASE = 'https://agents.test/agent'

describe('security, privacy and the audit log', () => {
  test('privacy requests send the subject in the body, never in the address', async () => {
    const m = mockFetch({
      'POST /agent/v1/privacy/export': () => json({ subject: 'u/1', conversations: [], memories: [], usage: [], securityEvents: [], bugReports: [] }),
      'POST /agent/v1/privacy/erase': () => json({ ok: true, conversations: 2, memories: 1, bugReports: 0, usageDeidentified: 3, securityEventsDeidentified: 1 }),
      'POST /agent/v1/privacy/revoke-sessions': () => json({ ok: true }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', fetch: m.fetch })
    expect((await srv.privacy.export('u/1')).subject).toBe('u/1')
    expect((await srv.privacy.erase('u/1')).usageDeidentified).toBe(3)
    await srv.privacy.revokeSessions('u/1')
    for (const call of m.calls) {
      expect(call.body).toEqual({ subject: 'u/1' })
      expect(call.url).not.toContain('u%2F1')
    }
  })

  test('the security policy, events with filters and the audit log', async () => {
    const m = mockFetch({
      'PUT /agent/v1/admin/security': () => json({ enabled: true, policy: { mode: 'monitor' }, effective: { modes: {}, settings: {} }, controls: [], agents: [], eventTtlDays: null, defaultEventTtlDays: 180 }),
      'GET /agent/v1/admin/security/events': () => json({ items: [], next: null }),
      'GET /agent/v1/admin/audit': () => json({ items: [], next: null }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 'srv', adminKey: 'adm', fetch: m.fetch })
    expect((await srv.admin.security.put({ mode: 'monitor' })).defaultEventTtlDays).toBe(180)
    await srv.admin.security.events({ control: 'tools.', action: 'blocked', limit: 20 })
    await srv.admin.audit({ action: 'agent.', actor: 'bob' })
    expect(m.calls[0]!.headers.authorization).toBe('Bearer adm')
    expect(m.calls[1]!.url).toContain('control=tools.')
    expect(m.calls[1]!.url).toContain('action=blocked')
    expect(m.calls[2]!.url).toContain('action=agent.')
  })

  test('the console: single sign-on and the deployment views', async () => {
    const m = mockFetch({
      'GET /agent/v1/console/sso': () => json({ enabled: true, label: 'Example SSO' }),
      'PUT /agent/v1/console/sso/config': () => json({ source: 'console', config: null, redirectUri: null }),
      'PATCH /agent/v1/console/users/u1': () => json({ id: 'u1', email: 'a@b.c', name: 'A', roles: {}, disabled: true, createdAt: 'x', lastLoginAt: null }),
      'GET /agent/v1/console/audit': () => json({ items: [], next: null }),
    })
    const c = new EvokaiConsole({ baseUrl: BASE, fetch: m.fetch })
    expect(await c.sso()).toEqual({ enabled: true, label: 'Example SSO' })
    expect(c.ssoStartUrl()).toBe(`${BASE}/v1/console/sso/start`)
    await c.deployment.putSsoConfig({ issuer: 'https://login.example.com', client_id: 'x' })
    expect((await c.deployment.setUserDisabled('u1', true)).disabled).toBe(true)
    await c.deployment.audit({ application: 'bookshop' })
    expect(m.calls.every((x) => x.headers['x-evokai-console'] === '1')).toBe(true)
    expect(m.calls[2]!.body).toEqual({ disabled: true })
    expect(m.calls[3]!.url).toContain('application=bookshop')
  })
})
