import { EvokaiServer } from '../src/server'
import { json, mockFetch } from './helpers/fetch'

const BASE = 'https://agents.test/agent'

describe('admin.compliance', () => {
  test('packs as JSON and as text, snapshots and their diff', async () => {
    const m = mockFetch({
      'GET /agent/v1/admin/compliance/gdpr': [
        () => json({ pack: 'gdpr', lang: 'it', title: 'Fascicolo GDPR', sections: [], footnotes: [] }),
        () => new Response('# Fascicolo GDPR\n', { status: 200, headers: { 'Content-Type': 'text/markdown' } }),
      ],
      'POST /agent/v1/admin/compliance/snapshots': () => json({ id: '2026111610000000', createdAt: 'x', label: 'v1' }, 201),
      'GET /agent/v1/admin/compliance/snapshots/diff': () => json({ counts: { statements: 2, facts: 1 } }),
      'PUT /agent/v1/admin/compliance/profile': () => json({ profile: { controller: 'Acme S.p.A.' } }),
    })
    const srv = new EvokaiServer({ baseUrl: BASE, serverKey: 's', adminKey: 'adm', fetch: m.fetch })
    const pack = await srv.admin.compliance.pack('gdpr', 'it')
    expect(pack.title).toBe('Fascicolo GDPR')
    expect(m.calls[0]!.url).toContain('format=json')
    expect(m.calls[0]!.url).toContain('lang=it')
    const md = await srv.admin.compliance.packText('gdpr', 'md', 'it')
    expect(md).toBe('# Fascicolo GDPR\n')
    expect(m.calls[1]!.headers.accept).toContain('text/markdown')
    expect((await srv.admin.compliance.snapshot('v1')).label).toBe('v1')
    expect(m.calls[2]!.body).toEqual({ label: 'v1' })
    const d = await srv.admin.compliance.diff('a', 'b', 'en')
    expect(d.counts.statements).toBe(2)
    expect(m.calls[3]!.url).toContain('from=a')
    const p = await srv.admin.compliance.putProfile({ controller: 'Acme S.p.A.' })
    expect(p.profile.controller).toBe('Acme S.p.A.')
    expect(m.calls[4]!.headers.authorization).toBe('Bearer adm')
  })
})
