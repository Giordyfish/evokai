import type { Connection } from '@evokai/evokai-client/browser'
import { defineElements, type EvokaiChatElement } from '../src/elements'
import { fakeEvokai, tick } from './fake'

defineElements()

const CONNS = (): Connection[] => [
  {
    provider: 'microsoft365',
    title: 'Microsoft 365',
    status: 'connected',
    account: 'ada@contoso.example',
    scopes: ['Mail.Read'],
    families: ['mail', 'calendar'],
    connectedAt: '2026-11-10T08:00:00Z',
    lastUsedAt: '2026-11-15T08:00:00Z',
  },
  { provider: 'google', title: 'Google Workspace', status: 'not_connected', account: null, scopes: [], families: ['files'], connectedAt: null, lastUsedAt: null },
]

async function mounted(conns: Connection[], turns: Parameters<typeof fakeEvokai>[0] = [], lang = 'en') {
  const evokai = fakeEvokai(turns, { mode: 'off', consent: 'unknown', items: [] }, conns)
  const el = document.createElement('evokai-chat') as EvokaiChatElement
  el.fetcher = evokai.fetch
  el.setAttribute('base-url', 'https://agents.test/agent')
  el.setAttribute('token-url', '/api/agent/token')
  el.setAttribute('title', 'Ada')
  el.setAttribute('locale', lang)
  document.body.append(el)
  await tick()
  const $ = (sel: string) => el.shadowRoot!.querySelector<HTMLElement>(sel)
  const $$ = (sel: string) => [...el.shadowRoot!.querySelectorAll<HTMLElement>(sel)]
  return { el, evokai, $, $$ }
}

async function until(fn: () => unknown, ms = 1500) {
  const end = Date.now() + ms
  for (;;) {
    let v: unknown
    try {
      v = fn()
    } catch {
      v = undefined
    }
    if (v) return v
    if (Date.now() > end) throw new Error('timed out')
    await tick(10)
  }
}

async function openView(m: Awaited<ReturnType<typeof mounted>>) {
  m.el.open()
  await until(() => m.$('[part~="menu-button"]'))
  m.$('[part~="menu-button"]')!.click()
  await until(() => m.$('[part~="connections-menu-item"]'))
  m.$('[part~="connections-menu-item"]')!.click()
  await until(() => m.$$('[part~="connection-item"]').length === 2)
}

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('the connections view', () => {
  test('the header menu offers it; each provider shows its state, account and families', async () => {
    const m = await mounted(CONNS(), [], 'it')
    m.el.open()
    await until(() => m.$('[part~="menu-button"]'))
    m.$('[part~="menu-button"]')!.click()
    await until(() => m.$('[part~="connections-menu-item"]'))
    expect(m.$('[part~="connections-menu-item"]')!.textContent).toContain('Collegamenti')
    m.$('[part~="connections-menu-item"]')!.click()
    await until(() => m.$$('[part~="connection-item"]').length === 2)
    expect(m.$('[part~="connections-title"]')!.textContent).toBe('Collegamenti')
    const [ms, google] = m.$$('[part~="connection-item"]')
    expect(ms!.dataset.status).toBe('connected')
    expect(ms!.textContent).toContain('Collegato')
    expect(ms!.textContent).toContain('come ada@contoso.example')
    expect(ms!.textContent).toContain('Può usare: posta, calendario')
    expect(google!.textContent).toContain('Non collegato')
    expect(google!.querySelector('[part~="connection-connect"]')!.textContent).toBe('Collega')
  })

  test('disconnect asks first, then deletes the connection', async () => {
    const m = await mounted(CONNS())
    await openView(m)
    m.$('[part="connection-disconnect"]')!.click()
    await until(() => m.$('[part~="connection-confirm"]'))
    expect(m.$('[part~="connection-confirm"]')!.textContent).toContain('Disconnect Microsoft 365?')
    m.$('[part~="connection-confirm-yes"]')!.click()
    await until(() => m.evokai.calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/v1/session/connections/microsoft365')))
    await until(() => m.$('[data-provider="microsoft365"]')?.dataset.status === 'not_connected')
  })

  test('connect opens the provider in a new window and reads the connections again when it closes', async () => {
    const win = { closed: false, close: vi.fn(), location: { href: '' } }
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window)
    const conns = CONNS()
    const m = await mounted(conns)
    await openView(m)
    m.$('[part~="connection-connect"]')!.click()
    await until(() => win.location.href === 'https://login.test/authorize?p=google')
    await until(() => m.$('[part~="connection-waiting"]'))
    conns[1]!.status = 'connected'
    win.closed = true
    await until(() => m.$('[data-provider="google"]')?.dataset.status === 'connected', 3000)
  })

  test('no menu entry when there is nothing to connect', async () => {
    const m = await mounted([])
    m.el.open()
    await tick(50)
    expect(m.$('[part~="connections-menu-item"]')).toBeNull()
  })
})

describe('the connect card', () => {
  test('a tool without a connection shows a card whose button starts it', async () => {
    const win = { closed: false, close: vi.fn(), location: { href: '' } }
    vi.spyOn(window, 'open').mockReturnValue(win as unknown as Window)
    const m = await mounted(CONNS(), [
      [
        { type: 'text', delta: 'I need your Google account.' },
        { type: 'connect', provider: 'google', title: 'Google Workspace', server: 'gws' },
      ],
    ])
    m.el.open()
    await until(() => m.$('textarea'))
    await m.el.send('Find the budget file')
    await until(() => m.$('[part~="connect-card"]'))
    const card = m.$('[part~="connect-card"]')!
    expect(card.textContent).toContain('Connect Google Workspace')
    expect(card.textContent).toContain('Ada needs access to your Google Workspace account')
    m.$('[part~="connect-button"]')!.click()
    await until(() => win.location.href.includes('p=google'))
  })
})
