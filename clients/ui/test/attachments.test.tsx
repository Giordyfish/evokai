import { defineElements, type EvokaiChatElement } from '../src/elements'
import { fileError } from '../src/attach'
import { stringsFor } from '../src/strings'
import { fakeEvokai, tick } from './fake'

defineElements()

const OFFER = { kinds: ['png', 'pdf'], mediaTypes: ['image/png', 'application/pdf'], maxFileBytes: 1000, maxFiles: 2 }

async function mounted(offer: typeof OFFER | null, lang?: string) {
  const evokai = fakeEvokai([], undefined, [], offer)
  const el = document.createElement('evokai-chat') as EvokaiChatElement
  el.fetcher = evokai.fetch
  el.setAttribute('base-url', 'https://agents.test/agent')
  el.setAttribute('token-url', '/api/agent/token')
  if (lang) el.setAttribute('locale', lang)
  document.body.append(el)
  await tick()
  const $ = (sel: string) => el.shadowRoot!.querySelector<HTMLElement>(sel)
  const $$ = (sel: string) => [...el.shadowRoot!.querySelectorAll<HTMLElement>(sel)]
  el.shadowRoot!.querySelector<HTMLElement>('.launcher')!.click()
  return { evokai, $, $$ }
}

async function until(fn: () => unknown, ms = 1500) {
  const end = Date.now() + ms
  while (!fn()) {
    if (Date.now() > end) throw new Error('timed out')
    await tick(10)
  }
}

function choose(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, 'files', { value: files, configurable: true })
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('<evokai-chat>: attachments', () => {
  test('no paper clip when the session takes no files', async () => {
    const { $ } = await mounted(null)
    await until(() => $('textarea'))
    await tick(20)
    expect($('[part~="attach-button"]')).toBeNull()
  })

  test('chosen files show as previews, can be removed, and go with the message', async () => {
    const { $, $$, evokai } = await mounted(OFFER)
    await until(() => $('[part~="attach-button"]'))
    const input = $('input[type="file"]') as HTMLInputElement
    expect(input.accept).toBe('image/png,application/pdf,.png,.pdf')
    choose(input, [new File(['%PDF-1'], 'a.pdf', { type: 'application/pdf' }), new File(['x'], 'b.png', { type: 'image/png' })])
    await until(() => $$('[part~="attachment-preview"]').length === 2)
    $$('[part~="attachment-remove"]')[1]!.click()
    await until(() => $$('[part~="attachment-preview"]').length === 1)
    const box = $('textarea') as HTMLTextAreaElement
    box.value = 'Read it'
    box.dispatchEvent(new Event('input', { bubbles: true }))
    await tick()
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await until(() => evokai.calls.some((c) => c.url.endsWith('/v1/session/chat')))
    const call = evokai.calls.find((c) => c.url.endsWith('/v1/session/chat'))!
    expect((call.body as { attachments: unknown[] }).attachments).toEqual([{ name: 'a.pdf', mediaType: 'application/pdf', data: 'JVBERi0x' }])
    await until(() => $$('[part~="attachment-preview"]').length === 0)
    await until(() => $('[part~="user-attachments"]'))
    expect($('[part~="user-attachments"]')!.textContent).toContain('a.pdf')
  })

  test('a file too large, of a kind not taken, or one too many is refused in the language of the panel', async () => {
    const { $, $$ } = await mounted(OFFER, 'it')
    await until(() => $('[part~="attach-button"]'))
    const input = $('input[type="file"]') as HTMLInputElement
    choose(input, [new File([new Uint8Array(2000)], 'big.png', { type: 'image/png' })])
    await until(() => $('[part~="attachment-error"]'))
    expect($('[part~="attachment-error"]')!.textContent).toContain('big.png')
    expect($('[part~="attachment-error"]')!.textContent).toContain('troppo grande')
    choose(input, [new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' })])
    await until(() => $('[part~="attachment-error"]')!.textContent!.includes('logo.svg'))
    const ok = (n: string) => new File(['x'], n, { type: 'image/png' })
    choose(input, [ok('1.png'), ok('2.png'), ok('3.png')])
    await until(() => $$('[part~="attachment-preview"]').length === 2)
    expect($('[part~="attachment-error"]')!.textContent).toContain('2')
  })

  test('the service refusals have their own words', () => {
    const it = stringsFor('it')
    expect(fileError(it, 'attachment_unsupported')).toBe(it.fileUnsupported)
    expect(fileError(stringsFor('en'), 'attachments_full')).toBe(stringsFor('en').filesFull)
    expect(fileError(it, 'rewrite_running')).toBeNull()
  })
})
