import { createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react'

import { defineElements, EvokaiChatElement, type EvokaiVoiceElement } from '../src/elements'
import { EvokaiChat } from '../src/react'
import { trapFocus } from '../src/stage'
import { fakeEvokai, tick } from './fake'

defineElements()

const CHART = {
  type: 'column',
  title: 'Revenue by format',
  subtitle: 'This week',
  unit: 'eur',
  points: [
    { label: 'Paperback', value: 1200 },
    { label: 'Hardback', value: 800 },
  ],
  highlight: 'Paperback',
  name: '',
  compare: null,
}

async function mounted(turns: Parameters<typeof fakeEvokai>[0] = [], attrs: Record<string, string> = {}) {
  const evokai = fakeEvokai(turns)
  const el = document.createElement('evokai-chat') as EvokaiChatElement
  el.fetcher = evokai.fetch
  el.setAttribute('base-url', 'https://agents.test/agent')
  el.setAttribute('token-url', '/api/agent/token')
  el.setAttribute('title', 'Ada')
  el.setAttribute('app-name', 'Acme')
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v)
  document.body.append(el)
  await tick()
  const $ = (sel: string) => el.shadowRoot!.querySelector<HTMLElement>(sel)
  const $$ = (sel: string) => [...el.shadowRoot!.querySelectorAll<HTMLElement>(sel)]
  return { el, evokai, $, $$ }
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

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('<evokai-chat>', () => {
  test('the launcher opens the panel, which mints a token and shows the starter questions and usage shares', async () => {
    const { el, evokai, $, $$ } = await mounted()
    const launcher = $('.launcher')!
    expect(launcher.getAttribute('aria-label')).toBe('Open Ada')
    expect(launcher.getAttribute('aria-expanded')).toBe('false')
    launcher.click()
    await until(() => $$('.starter').length === 2)
    expect(el.hasAttribute('open')).toBe(true)
    expect($('.panel')!.getAttribute('role')).toBe('dialog')
    expect($('#ek-title')!.textContent).toBe('Ada')
    expect(evokai.calls[0]).toMatchObject({ method: 'POST', url: '/api/agent/token', body: { conversationId: null } })
    expect($('.usage button')!.getAttribute('aria-label')).toBe('Use today: 13%')
    expect($('.tip')!.textContent).toContain('This month, Acme')
    expect(el.shadowRoot!.innerHTML).not.toMatch(/[€$]|usd|cost/i)
  })

  test('a turn renders Markdown, tool activity, a chart and a confirmation; deciding sends it', async () => {
    const { $, $$, evokai } = await mounted([
      [
        { type: 'tool', name: 'orders_search', status: 'start' },
        { type: 'tool', name: 'orders_search', status: 'done' },
        { type: 'text', delta: 'This week: **24** orders.\n\n| A | B |\n|---|---|\n| 1 | 2 |' },
        { type: 'chart', chart: CHART as never },
        { type: 'confirm', actionId: 'a1', items: ['Refund order 1042'] },
      ],
      [{ type: 'text', delta: 'Done.' }],
    ])
    $('.launcher')!.click()
    await until(() => $('textarea'))
    const box = $('textarea') as HTMLTextAreaElement
    box.value = 'How did this week go?'
    box.dispatchEvent(new Event('input', { bubbles: true }))
    await tick()
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await until(() => $('.confirm'))
    expect($('.bubble')!.textContent).toBe('How did this week go?')
    expect($('.md strong')!.textContent).toBe('24')
    expect($$('.md table td').map((x) => x.textContent)).toEqual(['1', '2'])
    expect($('.tool')!.textContent).toBe('orders_search')
    expect($('figure .vt')!.textContent).toBe('Revenue by format')
    expect($('figure [role=img]')!.getAttribute('aria-label')).toBe('Paperback 1,200, Hardback 800')
    expect($('.confirm')!.getAttribute('role')).toBe('alertdialog')
    const confirm = $$('.confirm button').find((b) => b.textContent === 'Confirm')!
    confirm.click()
    await until(() => evokai.calls.filter((c) => c.url.endsWith('/chat')).length === 2)
    expect(evokai.calls.filter((c) => c.url.endsWith('/chat'))[1]!.body).toMatchObject({ confirm: { actionId: 'a1', approve: true } })
  })

  test('citations: superscript markers link to the sources listed under the answer, in Italian too', async () => {
    const cite = (index: number, url: string | null) => ({
      type: 'citation',
      index,
      kb: 'policies',
      documentId: `d${index}`,
      title: `Condizioni di vendita ${index}`,
      location: { page: 3, section: 'Art. 2', charStart: 0, charEnd: 10 },
      snippet: 'Un libro si può restituire entro trenta giorni.',
      url,
    })
    const { el, $, $$ } = await mounted(
      [
        [
          { type: 'tool', name: 'knowledge_search', status: 'start' },
          cite(1, 'https://docs.test/d1#page=3') as never,
          cite(2, null) as never,
          cite(3, 'https://docs.test/d3') as never,
          { type: 'tool', name: 'knowledge_search', status: 'done' },
          { type: 'text', delta: 'Si può restituire [2] entro trenta giorni [1]. Vedi [9] e [link](/x).' },
        ],
      ],
      { locale: 'it' },
    )
    el.open()
    await until(() => $('textarea'))
    await el.send('resi?')
    await until(() => $('[part=sources]'))
    expect($('.tool')!.textContent).toBe('Ricerca nei documenti')
    const sups = $$('[part=citation]')
    expect(sups.map((x) => x.textContent)).toEqual(['2', '1'])
    expect(sups[1]!.querySelector('a')!.getAttribute('href')).toBe('https://docs.test/d1#page=3')
    expect(sups[1]!.querySelector('a')!.getAttribute('target')).toBe('_blank')
    expect(sups[0]!.querySelector('a')).toBeNull()
    expect($('.md')!.textContent).toContain('[9]')
    expect($('.sources-title')!.textContent).toBe('Fonti')
    const items = $$('[part=source]')
    expect(items.map((x) => x.getAttribute('data-index'))).toEqual(['2', '1'])
    expect(items[1]!.querySelector('.src-where')!.textContent).toBe('p. 3 · Art. 2')
    expect(items[1]!.querySelector('.src-snippet')!.textContent).toContain('restituire')
  })

  test('tool labels and texts can be replaced; Italian is built in', async () => {
    const { el, $ } = await mounted([[{ type: 'tool', name: 'orders_search', status: 'error' }]], { locale: 'it' })
    el.toolLabels = { orders_search: 'Cerco gli ordini' }
    el.strings = { tagline: 'Pronta' }
    el.open()
    await until(() => $('.panel'))
    expect($('.ht .sub')!.textContent).toBe('Pronta')
    expect($('textarea')!.getAttribute('placeholder')).toBe('Chiedi a Ada…')
    await el.send('ciao')
    await until(() => $('.tool'))
    expect($('.tool')!.textContent).toBe('Cerco gli ordini, non riuscito')
  })

  test('navigate events and relative links go through a cancelable evokai-navigate event', async () => {
    const { el, $ } = await mounted([[{ type: 'text', delta: 'See [the order](/orders/1042).' }, { type: 'navigate', path: '/orders' }]])
    const paths: string[] = []
    el.addEventListener('evokai-navigate', (e) => {
      e.preventDefault()
      paths.push((e as CustomEvent<{ path: string }>).detail.path)
    })
    el.open()
    await until(() => $('.panel'))
    await el.send('open it')
    await until(() => $('.md a'))
    expect(paths).toEqual(['/orders'])
    $('.md a')!.click()
    expect(paths).toEqual(['/orders', '/orders/1042'])
  })

  test('toolRenderers draw a tool\'s card instead of the default line', async () => {
    const { el, $, $$ } = await mounted([
      [
        { type: 'tool', name: 'orders_month', status: 'start' },
        { type: 'tool', name: 'orders_month', status: 'done' },
        { type: 'tool', name: 'stock_report', status: 'done' },
        { type: 'text', delta: 'Done.' },
      ],
    ])
    el.toolRenderers = {
      orders_month: (part) => {
        const d = document.createElement('div')
        d.className = 'mine'
        d.textContent = `${part.name}:${part.status}`
        return d
      },
      stock_report: () => null,
    }
    el.open()
    await until(() => $('.panel'))
    await el.send('orders')
    await until(() => $('.mine'))
    expect($('.mine')!.textContent).toBe('orders_month:done')
    expect($$('[part~="tool"]').map((x) => x.textContent)).toEqual(['stock_report'])
  })

  test('the tools property answers custom client tools', async () => {
    const { el, evokai, $ } = await mounted([[{ type: 'client_tool', callId: 'c1', name: 'kiosk_light', args: { on: true }, wait: true }]])
    const got: unknown[] = []
    el.tools = {
      kiosk_light: (args) => {
        got.push(args)
        return { lit: true }
      },
    }
    el.open()
    await until(() => $('.panel'))
    await el.send('light')
    await until(() => evokai.calls.some((c) => c.url.endsWith('/v1/session/tool-results')))
    expect(got).toEqual([{ on: true }])
    expect(evokai.calls.find((c) => c.url.endsWith('/v1/session/tool-results'))?.body).toEqual({ callId: 'c1', result: { lit: true } })
  })

  test('Ctrl+J toggles, Escape closes; the conversation is remembered', async () => {
    const { el, $ } = await mounted()
    dispatchEvent(new KeyboardEvent('keydown', { key: 'j', ctrlKey: true }))
    await until(() => el.hasAttribute('open'))
    await until(() => $('.panel'))
    dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(el.hasAttribute('open')).toBe(false)
    await el.send('hi')
    expect(localStorage.getItem('evokai.conversation')).toBe('c1')
  })

  test('the conversation list comes from your endpoint and hides unused conversations', async () => {
    const { $, $$ } = await mounted([], { 'conversations-url': '/api/agent/conversations' })
    $('.launcher')!.click()
    await until(() => $('[aria-label=Conversations]'))
    $('[aria-label=Conversations]')!.click()
    await until(() => $$('.history button').length)
    expect($$('.history button').map((b) => b.textContent)).toEqual(['Order 1042'])
  })

  test('a theme object sets custom properties on the host', async () => {
    const { el } = await mounted()
    el.theme = { light: { brand: '#123456' }, scheme: 'dark', orb: { main: '#00ff00' } }
    await tick()
    expect(el.style.getPropertyValue('--evokai-brand-light')).toBe('#123456')
    expect(el.style.getPropertyValue('--evokai-orb-main')).toBe('#00ff00')
    expect(el.getAttribute('scheme')).toBe('dark')
  })
})

describe('<evokai-voice> and focus', () => {
  test('the voice element needs a start from a gesture and renders nothing before', async () => {
    const v = document.createElement('evokai-voice') as EvokaiVoiceElement
    v.setAttribute('base-url', 'https://agents.test/agent')
    v.setAttribute('token-url', '/t')
    document.body.append(v)
    await tick()
    expect(v.shadowRoot!.querySelector('.stage')).toBeNull()
    expect(v.controller).not.toBeNull()
  })

  test('Tab cycles inside the stage', () => {
    const root = document.createElement('div')
    root.innerHTML = '<button id="a">a</button><button id="b">b</button><button id="c">c</button>'
    document.body.append(root)
    const c = root.querySelector<HTMLButtonElement>('#c')!
    c.focus()
    const tab = new KeyboardEvent('keydown', { key: 'Tab', cancelable: true })
    trapFocus(root, tab)
    expect(document.activeElement?.id).toBe('a')
    expect(tab.defaultPrevented).toBe(true)
    const back = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, cancelable: true })
    trapFocus(root, back)
    expect(document.activeElement?.id).toBe('c')
  })
})

describe('React wrapper', () => {
  test('renders the element with attributes, properties and callbacks', async () => {
    const evokai = fakeEvokai([])
    const host = document.createElement('div')
    document.body.append(host)
    const opened: boolean[] = []
    const ref = { current: null as EvokaiChatElement | null }
    await act(async () => {
      createRoot(host).render(
        createElement(EvokaiChat, {
          ref,
          baseUrl: 'https://agents.test/agent',
          tokenUrl: '/api/agent/token',
          title: 'Ada',
          theme: { light: { brand: '#102030' } },
          toolLabels: { x: 'X' },
          skin: '[part~="user-bubble"]{color:red}',
          icons: { x: '<path d="M1 1"/>' },
          children: createElement('span', { slot: 'empty', id: 'mine' }, 'Hi'),
          voice: false,
          onOpenChange: (o: boolean) => opened.push(o),
        }),
      )
    })
    const el = host.querySelector('evokai-chat') as EvokaiChatElement
    expect(el).toBeInstanceOf(EvokaiChatElement)
    expect(ref.current).toBe(el)
    expect(el.getAttribute('title')).toBe('Ada')
    expect(el.getAttribute('voice')).toBe('off')
    expect(el.toolLabels).toEqual({ x: 'X' })
    el.fetcher = evokai.fetch
    el.open()
    expect(opened).toEqual([true])
    await tick()
    expect(el.style.getPropertyValue('--evokai-brand-light')).toBe('#102030')
    expect(el.skin).toBe('[part~="user-bubble"]{color:red}')
    expect(el.querySelector('#mine')!.getAttribute('slot')).toBe('empty')
  })
})

describe('skins', () => {
  test('a skin goes into the shadow root after the default look, which sits in a cascade layer', async () => {
    const { el } = await mounted()
    el.skin = ['[part~="panel"]{border-radius:0}', ':host([data-phase=speaking]) [part~="title"]{color:red}']
    await tick()
    const styles = [...el.shadowRoot!.querySelectorAll('style')]
    expect(styles[0]!.textContent!.startsWith('@layer evokai{')).toBe(true)
    expect(styles[1]!.textContent).toBe('[part~="panel"]{border-radius:0}\n:host([data-phase=speaking]) [part~="title"]{color:red}')
  })

  test('unstyled keeps the tokens and drops the look; the parts are there to style', async () => {
    const { el, $, $$ } = await mounted([], { unstyled: '' })
    const base = el.shadowRoot!.querySelector('style')!.textContent!
    expect(base).toContain('--ek-brand')
    expect(base).not.toContain('.launcher{')
    $('[part~="launcher"]')!.click()
    await until(() => $$('[part~="starter"]').length === 2)
    for (const part of ['panel', 'header', 'title', 'tagline', 'messages', 'composer', 'input', 'send-button', 'aurora'])
      expect($(`[part~="${part}"]`)).not.toBeNull()
    el.removeAttribute('unstyled')
    await tick()
    expect(el.shadowRoot!.querySelector('style')!.textContent).toContain('.launcher{')
  })

  test('the conversation state is on the host and icons can be replaced', async () => {
    const { el, $, $$ } = await mounted([[{ type: 'text', delta: 'Done.' }, { type: 'done' }]])
    el.icons = { up: '<svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>', plus: '<path d="M2 2h20"/>' }
    $('.launcher')!.click()
    await until(() => $$('.starter').length === 2)
    expect(el.getAttribute('data-phase')).toBe('idle')
    expect(el.hasAttribute('data-busy')).toBe(false)
    expect($('[part~="send-button"] [data-icon="up"] circle')).not.toBeNull()
    expect($('[part~="new-button"] svg[data-icon="plus"] path')!.getAttribute('d')).toBe('M2 2h20')
    $$('.starter')[0]!.click()
    await until(() => el.hasAttribute('data-busy'))
    await until(() => !el.hasAttribute('data-busy'))
    expect(el.getAttribute('data-phase')).toBe('idle')
  })
})
