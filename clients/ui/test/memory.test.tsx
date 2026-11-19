import { defineElements, type EvokaiChatElement } from '../src/elements'
import { memoryDate } from '../src/memory'
import { fakeEvokai, memory, tick, type FakeMemory } from './fake'

defineElements()

const ITEMS = () => [
  memory('m1', 'Prefers short answers with a table', 'preference'),
  memory('m2', 'Handles the returns desk for the children’s section', 'semantic', { source: 'person', updatedAt: '2026-11-10T08:00:00Z' }),
  memory('m3', 'Asked how last week went; the gap was in returns', 'episodic'),
  memory('m4', 'Answers in Italian', 'preference'),
]

async function mounted(mem: Partial<FakeMemory> = {}, attrs: Record<string, string> = {}, turns: Parameters<typeof fakeEvokai>[0] = []) {
  const evokai = fakeEvokai(turns, { mode: 'on', consent: 'unknown', items: ITEMS(), ...mem })
  const el = document.createElement('evokai-chat') as EvokaiChatElement
  el.fetcher = evokai.fetch
  el.setAttribute('base-url', 'https://agents.test/agent')
  el.setAttribute('token-url', '/api/agent/token')
  el.setAttribute('title', 'Ada')
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  document.body.append(el)
  await tick()
  const $ = (sel: string) => el.shadowRoot!.querySelector<HTMLElement>(sel)
  const $$ = (sel: string) => [...el.shadowRoot!.querySelectorAll<HTMLElement>(sel)]
  const active = () => el.shadowRoot!.activeElement
  return { el, evokai, $, $$, active }
}

async function until(fn: () => unknown, ms = 1500) {
  const end = Date.now() + ms
  for (;;) {
    try {
      const v = fn()
      if (v) return v
    } catch {
      /* not yet */
    }
    if (Date.now() > end) throw new Error('timed out')
    await tick(10)
  }
}

const key = (target: Element, k: string, init: KeyboardEventInit = {}) => {
  const e = new KeyboardEvent('keydown', { key: k, bubbles: true, composed: true, cancelable: true, ...init })
  target.dispatchEvent(e)
  return e
}

/** Open the panel, then the memory view from the header menu. */
async function openView(m: Awaited<ReturnType<typeof mounted>>) {
  m.el.open()
  await until(() => m.$('[part~="menu-button"]'))
  m.$('[part~="menu-button"]')!.click()
  await until(() => m.$('[part~="memory-menu-item"]'))
  m.$('[part~="memory-menu-item"]')!.click()
  await until(() => m.$$('[part~="memory-item"]').length || m.$('[part~="memory-empty"]') || m.$('[part~="memory-off"]'))
}

const texts = (els: HTMLElement[]) => els.map((x) => x.textContent)

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('the memory view', () => {
  test('the header menu opens it; memories are grouped by kind with a light meta line', async () => {
    const m = await mounted()
    const { $, $$, el, active } = m
    el.open()
    await until(() => $('[part~="menu-button"]'))
    // The panel puts the focus in the message box as it opens.
    await until(() => active() === $('textarea'))
    const btn = $('[part~="menu-button"]')!
    expect(btn.getAttribute('aria-haspopup')).toBe('menu')
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    // Keyboard: ArrowDown opens the menu on its first entry, Escape closes it (not the panel) and refocuses the button.
    btn.focus()
    key(btn, 'ArrowDown')
    await until(() => $('[role=menu]'))
    expect(btn.getAttribute('aria-expanded')).toBe('true')
    await until(() => active() === $('[part~="memory-menu-item"]'))
    expect($('[part~="memory-menu-item"]')!.textContent).toBe('What Ada remembers')
    key(active()!, 'Escape')
    await until(() => !$('[role=menu]'))
    expect(el.isOpen).toBe(true)
    expect(active()).toBe(btn)

    await openView(m)
    expect($('[part~="memory-view"]')).not.toBeNull()
    expect($('[part~="messages"]')).toBeNull()
    expect($('[part~="memory-title"]')!.textContent).toBe('What Ada remembers')
    expect(active()).toBe($('[part~="memory-title"]'))
    const groups = $$('[part~="memory-group"]')
    expect(groups.map((g) => g.getAttribute('data-kind'))).toEqual(['preference', 'semantic', 'episodic'])
    expect(groups.map((g) => g.querySelector('h3')!.textContent)).toEqual(['How you like answers', 'About your work', 'Past conversations'])
    expect(texts([...groups[0]!.querySelectorAll<HTMLElement>('[part~="memory-text"]')])).toEqual(['Prefers short answers with a table', 'Answers in Italian'])
    const metas = texts($$('[part~="memory-meta"]'))
    expect(metas[0]).toBe('Nov 3')
    expect(metas.filter((x) => x?.includes('edited by you'))).toHaveLength(1)
    expect(el.shadowRoot!.innerHTML).not.toMatch(/[€$]|usd|cost/i)
  })

  test('Escape and the back button return to the conversation without closing the panel', async () => {
    const m = await mounted()
    await openView(m)
    key(m.$('[part~="memory-title"]')!, 'Escape')
    await until(() => m.$('[part~="messages"]'))
    expect(m.el.isOpen).toBe(true)
    expect(m.$('[part~="memory-view"]')).toBeNull()
    await openView(m)
    m.$('[part~="memory-back"]')!.click()
    await until(() => m.$('[part~="messages"]'))
    expect(m.el.isOpen).toBe(true)
  })

  test('inline edit: a refusal is shown in plain words; Enter saves, Escape cancels', async () => {
    const m = await mounted()
    const { $, $$, evokai, el } = m
    await openView(m)
    const first = () => $$('[part~="memory-item"]')[0]!
    first().querySelector<HTMLElement>('[part~="memory-edit"]')!.click()
    await until(() => $('[part~="memory-input"]'))
    const box = () => $('[part~="memory-input"]') as HTMLTextAreaElement
    expect(box().value).toBe('Prefers short answers with a table')
    box().value = 'My password is hunter2'
    box().dispatchEvent(new Event('input', { bubbles: true }))
    await tick()
    key(box(), 'Enter')
    await until(() => $('[part~="memory-error"]'))
    expect($('[part~="memory-error"]')!.textContent).toBe('This cannot be kept: a memory may not contain instructions, passwords or personal data.')
    expect(box().getAttribute('aria-invalid')).toBe('true')
    expect(evokai.calls.find((c) => c.method === 'PATCH')).toMatchObject({ url: 'https://agents.test/agent/v1/session/memories/m1', body: { text: 'My password is hunter2' } })

    // Escape cancels the edit, and only the edit.
    key(box(), 'Escape')
    await until(() => !$('[part~="memory-input"]'))
    expect($('[part~="memory-view"]')).not.toBeNull()
    expect(el.isOpen).toBe(true)
    expect(first().querySelector('[part~="memory-text"]')!.textContent).toBe('Prefers short answers with a table')

    first().querySelector<HTMLElement>('[part~="memory-edit"]')!.click()
    await until(() => $('[part~="memory-input"]'))
    box().value = 'Prefers short answers'
    box().dispatchEvent(new Event('input', { bubbles: true }))
    await tick()
    $('[part~="memory-save"]')!.click()
    await until(() => !$('[part~="memory-input"]'))
    expect(first().querySelector('[part~="memory-text"]')!.textContent).toBe('Prefers short answers')
    expect(first().querySelector('[part~="memory-meta"]')!.textContent).toContain('edited by you')
    expect(evokai.calls.filter((c) => c.method === 'PATCH')).toHaveLength(2)
  })

  test('delete one, then forget everything after a confirmation: the empty state', async () => {
    const m = await mounted()
    const { $, $$, evokai } = m
    await openView(m)
    $$('[part~="memory-item"]')[1]!.querySelector<HTMLElement>('[part~="memory-delete"]')!.click()
    await until(() => $$('[part~="memory-item"]').length === 3)
    expect(evokai.calls.find((c) => c.method === 'DELETE')!.url).toBe('https://agents.test/agent/v1/session/memories/m4')
    expect(texts($$('[part~="memory-text"]'))).not.toContain('Answers in Italian')

    $('[part~="memory-forget-all"]')!.click()
    await until(() => $('[part~="memory-confirm"]'))
    expect($('[part~="memory-confirm"]')!.textContent).toContain('Forget everything that is remembered about you? This cannot be undone.')
    // Cancelling asks nothing of the service.
    $$('[part~="memory-confirm"] button').find((b) => b.textContent === 'Cancel')!.click()
    await until(() => !$('[part~="memory-confirm"]'))
    expect(evokai.calls.filter((c) => c.method === 'DELETE')).toHaveLength(1)

    $('[part~="memory-forget-all"]')!.click()
    await until(() => $('[part~="memory-confirm-yes"]'))
    $('[part~="memory-confirm-yes"]')!.click()
    await until(() => $('[part~="memory-empty"]'))
    expect(evokai.calls.filter((c) => c.method === 'DELETE').at(-1)!.url).toBe('https://agents.test/agent/v1/session/memories')
    expect($$('[part~="memory-item"]')).toHaveLength(0)
    expect($('[part~="memory-forget-all"]')).toBeNull()
  })

  test('turning memory off warns that it deletes everything; a person who said no can turn it on again', async () => {
    const m = await mounted()
    const { $, evokai } = m
    await openView(m)
    $('[part~="memory-consent"]')!.click()
    await until(() => $('[part~="memory-confirm"]'))
    expect($('[part~="memory-confirm"]')!.textContent).toContain('Everything remembered about you is deleted')
    $('[part~="memory-confirm-yes"]')!.click()
    await until(() => $('[part~="memory-off"]'))
    expect(evokai.calls.find((c) => c.url.endsWith('/memory-consent'))!.body).toEqual({ granted: false })
    expect($('[part~="memory-off"]')!.textContent).toContain('Memory is off for you')
    expect(evokai.mem.items).toHaveLength(0)

    $('[part~="memory-consent"]')!.click()
    await until(() => $('[part~="memory-empty"]'))
    expect(evokai.calls.filter((c) => c.url.endsWith('/memory-consent')).at(-1)!.body).toEqual({ granted: true })
  })

  test('declined: the view says memory is off for the person, with «Turn on memory»', async () => {
    const m = await mounted({ consent: 'declined' })
    await openView(m)
    expect(m.$('[part~="memory-off"]')).not.toBeNull()
    expect(m.$('[part~="memory-consent"]')!.textContent).toBe('Turn on memory')
    expect(m.$$('[part~="memory-item"]')).toHaveLength(0)
  })

  test('the menu entry is hidden when the application has memory off', async () => {
    const { el, $, $$ } = await mounted({ mode: 'off' })
    el.open()
    await until(() => $$('.starter').length === 2)
    await tick(20)
    expect($('[part~="menu-button"]')).toBeNull()
    expect($('[part~="new-button"]')).not.toBeNull()
  })

  test('Italian strings', async () => {
    const m = await mounted({}, { locale: 'it' })
    await openView(m)
    expect(m.$('[part~="memory-title"]')!.textContent).toBe('Cosa ricordo di te')
    expect(m.$$('[part~="memory-group"] h3').map((h) => h.textContent)).toEqual(['Come preferisci le risposte', 'Sul tuo lavoro', 'Conversazioni passate'])
    expect(m.$('[part~="memory-forget-all"]')!.textContent).toBe('Dimentica tutto')
    expect(m.$('[part~="memory-consent"]')!.textContent).toBe('Disattiva la memoria')
    expect(m.$$('[part~="memory-meta"]').some((x) => x.textContent?.includes('modificato da te'))).toBe(true)
  })

  test('texts can be overridden like the others', async () => {
    const m = await mounted()
    m.el.strings = { memoryTitle: 'Your notes in {title}' }
    await openView(m)
    expect(m.$('[part~="memory-title"]')!.textContent).toBe('Your notes in Ada')
  })
})

describe('the consent question', () => {
  const ask = { mode: 'ask' as const, consent: 'unknown' as const, items: [] }

  async function answered(locale?: string) {
    const m = await mounted(ask, locale ? { locale } : {})
    m.el.open()
    await until(() => m.$('textarea'))
    expect(m.$('[part~="memory-ask"]')).toBeNull()
    await m.el.send('hello')
    await until(() => m.$('[part~="memory-ask"]'))
    return m
  }

  test('after the first answer, «Yes» records consent and the card never comes back', async () => {
    const m = await answered()
    const card = m.$('[part~="memory-ask"]')!
    expect(card.textContent).toContain('May I remember things about your work to help you better? You can see, change or delete them at any time.')
    expect(m.$('[part~="composer"]')).not.toBeNull()
    m.$('[part~="memory-ask-yes"]')!.click()
    await until(() => !m.$('[part~="memory-ask"]'))
    await until(() => m.evokai.calls.some((c) => c.url.endsWith('/memory-consent')))
    expect(m.evokai.calls.find((c) => c.url.endsWith('/memory-consent'))!.body).toEqual({ granted: true })
    await until(() => m.$('[part~="memory-note"]'))
    expect(m.$('[part~="memory-note"]')!.textContent).toContain('You can see what is remembered from the menu')
    await m.el.send('again')
    await tick(30)
    expect(m.$('[part~="memory-ask"]')).toBeNull()
    expect(localStorage.getItem('evokai.memoryAnswered')).toBe('1')
  })

  test('«No thanks» records a no; a new panel in the same browser does not ask again', async () => {
    const m = await answered()
    m.$('[part~="memory-ask-no"]')!.click()
    await until(() => m.evokai.calls.some((c) => c.url.endsWith('/memory-consent')))
    expect(m.evokai.calls.find((c) => c.url.endsWith('/memory-consent'))!.body).toEqual({ granted: false })
    await until(() => !m.$('[part~="memory-ask"]'))
    m.el.remove()

    // Even if the service still says «ask» (the answer was lost), this browser does not ask twice.
    const again = await mounted(ask, {}, [[{ type: 'text', delta: 'Hi again.' }]])
    again.el.open()
    await until(() => again.$('textarea'))
    await again.el.send('hello')
    await until(() => again.$('.md'))
    await tick(30)
    expect(again.$('[part~="memory-ask"]')).toBeNull()
  })

  test('in Italian', async () => {
    const m = await answered('it')
    expect(m.$('[part~="memory-ask"]')!.textContent).toContain('Posso ricordare alcune cose sul tuo lavoro per aiutarti meglio?')
    expect(m.$('[part~="memory-ask-yes"]')!.textContent).toBe('Sì')
    expect(m.$('[part~="memory-ask-no"]')!.textContent).toBe('No, grazie')
  })

  test('not asked when memory is on or off for the application', async () => {
    for (const mode of ['on', 'off'] as const) {
      const m = await mounted({ mode, consent: 'unknown', items: [] })
      m.el.open()
      await until(() => m.$('textarea'))
      await m.el.send('hello')
      await until(() => m.$('.md'))
      await tick(30)
      expect(m.$('[part~="memory-ask"]')).toBeNull()
      m.el.remove()
    }
  })
})

test('memory dates: day and month, the year only when it is not this year', () => {
  const now = new Date('2026-11-16T12:00:00Z')
  expect(memoryDate('2026-11-03T10:00:00Z', 'en-US', now)).toBe('Nov 3')
  expect(memoryDate('2025-02-01T10:00:00Z', 'it-IT', now)).toBe('1 feb 2025')
  expect(memoryDate(null, 'en-US', now)).toBe('')
  expect(memoryDate('nope', 'en-US', now)).toBe('')
})
