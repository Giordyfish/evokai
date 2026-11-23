import { defineElements, type EvokaiChatElement } from '../src/elements'
import { fakeEvokai, tick } from './fake'

defineElements()

async function mounted(turns: Parameters<typeof fakeEvokai>[0]) {
  const evokai = fakeEvokai(turns)
  const el = document.createElement('evokai-chat') as EvokaiChatElement
  el.fetcher = evokai.fetch
  el.setAttribute('base-url', 'https://agents.test/agent')
  el.setAttribute('token-url', '/api/agent/token')
  document.body.append(el)
  await tick()
  const $ = (sel: string) => el.shadowRoot!.querySelector<HTMLElement>(sel)
  const $$ = (sel: string) => [...el.shadowRoot!.querySelectorAll<HTMLElement>(sel)]
  return { evokai, $, $$ }
}

async function until(fn: () => unknown, ms = 1500) {
  const end = Date.now() + ms
  while (!fn()) {
    if (Date.now() > end) throw new Error('timed out')
    await tick(10)
  }
}

async function type($: (sel: string) => HTMLElement | null, text: string) {
  const box = $('textarea') as HTMLTextAreaElement
  box.value = text
  box.dispatchEvent(new Event('input', { bubbles: true }))
  await tick()
  box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
}

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('<evokai-chat>: regenerating the last answer', () => {
  test('the last answer offers regenerate while the service allows it', async () => {
    const { $, evokai } = await mounted([
      [
        { type: 'text', delta: 'Order 1042 is shipped.' },
        { type: 'done', conversationId: 'c1', reason: 'end_turn', rewritable: true, regenerable: true } as never,
      ],
      [
        { type: 'rewritten', replaced: ['tt'], turn: 't2' } as never,
        { type: 'text', delta: 'Order 1042 shipped on Monday.' },
        { type: 'done', conversationId: 'c1', reason: 'end_turn', rewritable: true, regenerable: false } as never,
      ],
    ])
    $('.launcher')!.click()
    await until(() => $('textarea'))
    await type($, 'Where is order 1042?')
    await until(() => $('[part~="regenerate"] button'))
    $('[part~="regenerate"] button')!.click()
    await until(() => evokai.calls.some((c) => c.url.endsWith('/v1/session/regenerate')))
    await until(() => $('.panel')!.textContent!.includes('shipped on Monday'))
    expect($('.panel')!.textContent).not.toContain('Order 1042 is shipped.')
    expect($('[part~="regenerate"] button')).toBeNull()
  })
})

describe('<evokai-chat>: the last question and confirmations', () => {
  test('the last question can be edited while the service allows it; sending rewrites it', async () => {
    const { $, evokai } = await mounted([
      [
        { type: 'text', delta: 'Order 1042 is shipped.' },
        { type: 'done', conversationId: 'c1', reason: 'end_turn', rewritable: true } as never,
      ],
      [
        { type: 'rewritten', replaced: ['tt'] } as never,
        { type: 'text', delta: 'Order 1043 too.' },
      ],
    ])
    $('.launcher')!.click()
    await until(() => $('textarea'))
    await type($, 'Where is order 1042?')
    await until(() => $('[part~="edit-message"] button'))
    $('[part~="edit-message"] button')!.click()
    await until(() => $('[part~="editing"]'))
    expect(($('textarea') as HTMLTextAreaElement).value).toBe('Where is order 1042?')
    await type($, 'And order 1043?')
    const chats = () => evokai.calls.filter((c) => c.url.endsWith('/chat'))
    await until(() => chats().length === 2)
    expect(chats()[1]!.body).toMatchObject({ text: 'And order 1043?', rewrite: {} })
    await until(() => $('.bubble')?.textContent === 'And order 1043?')
    expect($('[part~="editing"]')).toBeNull()
  })

  test('a confirmation asked by a rule says why', async () => {
    const { $ } = await mounted([
      [
        {
          type: 'confirm',
          actionId: 'a1',
          items: ['Read the account VIP-1'],
          why: [{ key: 'confirm.why.rule', rule: 'vip', label: 'Key accounts' }],
        },
      ],
    ])
    $('.launcher')!.click()
    await until(() => $('textarea'))
    await type($, 'Show VIP-1')
    await until(() => $('.confirm'))
    expect($('.confirm small')!.textContent).toBe('Key accounts')
    expect($('.confirm .ct')!.textContent).toBe('Go ahead?')
  })
})
