import { defineElements, type EvokaiChatElement } from '../src/elements'
import { ICON_NAMES } from '../src/icons'
import { stringsFor } from '../src/strings'
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

type Ctl = { patch(p: Record<string, unknown>): void; state: { session: Record<string, unknown> } }
/** Voice mode as the controller reports it (jsdom has no recogniser): on, in a phase. */
const voice = (el: EvokaiChatElement, on: boolean, phase = 'listening', extra: Record<string, unknown> = {}) => {
  const c = el.controller as unknown as Ctl
  c.patch({ voiceOn: on, session: { ...c.state.session, phase: on ? phase : 'idle' }, ...extra })
}

async function opened(attrs: Record<string, string> = {}, turns: Parameters<typeof fakeEvokai>[0] = []) {
  const m = await mounted(turns, attrs)
  m.el.open()
  await until(() => m.el.getAttribute('data-view') === 'chat' && m.$('.panel[data-box=open]'))
  return m
}

const orbs = (el: EvokaiChatElement) => el.shadowRoot!.querySelectorAll('canvas.orbgl').length
const here = (el: EvokaiChatElement) => [...el.shadowRoot!.querySelectorAll('[data-rest][data-here]')].map((x) => x.getAttribute('data-rest'))

afterEach(() => {
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('voice in the panel, minimized voice and the one orb', () => {
  test('one orb: the launcher hides while the panel is open, and the orb rests in the header', async () => {
    const { el, $ } = await mounted()
    await until(() => el.getAttribute('data-view') === 'closed')
    expect(orbs(el)).toBe(1)
    expect(here(el)).toEqual(['launcher'])
    expect($('.launcher')!.hasAttribute('data-hidden')).toBe(false)
    el.open()
    await until(() => el.getAttribute('data-view') === 'chat' && $('.panel[data-box=open]'))
    expect(orbs(el)).toBe(1)
    expect(here(el)).toEqual(['head'])
    expect($('.launcher')!.hasAttribute('data-hidden')).toBe(true)
  })

  test('voice mode lives in the panel: the orb in the middle, a minimize button, Escape back to the chat', async () => {
    const { el, $, $$ } = await opened({ 'conversations-url': '/api/agent/conversations' })
    voice(el, true)
    await until(() => el.getAttribute('data-view') === 'voice' && $('.panel[data-layer=voice]'))
    expect(here(el)).toEqual(['center'])
    expect(orbs(el)).toBe(1)
    expect($('.stage')).toBeNull()
    expect($('[part~="stage"]')!.closest('.panel')).not.toBeNull()
    expect($('[part~="tagline"]')!.textContent).toBe('Listening…')
    expect($('[part~="stage-status"]')!.textContent).toBe('Listening')
    expect($('[part~="minimize-button"]')!.getAttribute('aria-label')).toBe('Minimize')
    expect($('[part~="minimize-button"] svg circle')).not.toBeNull()
    // Only minimize and close in the header while voice is on.
    expect($$('[part~="header-button"]').map((b) => b.getAttribute('part'))).toEqual(['header-button minimize-button', 'header-button close-button'])
    expect($('.layer.chat')!.hasAttribute('inert')).toBe(true)
    expect($('.layer.voice')!.hasAttribute('inert')).toBe(false)
    for (const part of ['stage-orb', 'stage-status', 'subtitles', 'stage-controls', 'mute-button', 'end-button', 'talk-over'])
      expect($(`[part~="${part}"]`)).not.toBeNull()
    // No keyboard button in the panel: ending voice already goes back to the chat.
    expect($('[part~="keyboard-button"]')).toBeNull()
    $('[part~="end-button"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, composed: true, cancelable: true }))
    await until(() => el.getAttribute('data-view') === 'chat' && $('.panel[data-layer=chat]'))
    expect(el.isOpen).toBe(true)
    expect(el.controller!.state.voiceOn).toBe(false)
    expect(here(el)).toEqual(['head'])
  })

  test('the closed launcher offers voice mode straight away: it starts minimized, the panel stays closed', async () => {
    const { el, $ } = await mounted()
    await until(() => el.getAttribute('data-view') === 'closed')
    // Hidden until the browser says it can recognise speech.
    expect($('[part~="voice-mini-action"]')).toBeNull()
    ;(el.controller as unknown as Ctl).patch({ support: 'ready' })
    await until(() => $('[part~="voice-mini-action"]'))
    const act = $('[part~="voice-mini-action"]')!
    expect(act.getAttribute('title')).toMatch(/^Voice mode/)
    // The same icon as voice mode in the open chat (the wave), not the microphone of the mute action.
    const other = await opened()
    await until(() => other.$('[part~="voice-button"] svg'))
    expect(act.querySelector('svg')!.innerHTML).toBe(other.$('[part~="voice-button"] svg')!.innerHTML)
    expect($('.dock')!.hasAttribute('data-idle')).toBe(true)
    const c = el.controller as unknown as { toggleVoice: () => Promise<void> }
    c.toggleVoice = async () => voice(el, true)
    $('[part~="voice-mini-action"]')!.click()
    await until(() => el.getAttribute('data-view') === 'minimized' && $('[part~="voice-bubble"]'))
    expect(el.isOpen).toBe(false)
    expect($('[part~="voice-mini-action"]')).toBeNull()
    expect($('.dock')!.hasAttribute('data-idle')).toBe(false)
  })

  test('a refused start leaves the launcher as it was', async () => {
    const { el, $ } = await mounted()
    await until(() => el.getAttribute('data-view') === 'closed')
    ;(el.controller as unknown as Ctl).patch({ support: 'ready' })
    await until(() => $('[part~="voice-mini-action"]'))
    const c = el.controller as unknown as { toggleVoice: () => Promise<void> }
    c.toggleVoice = async () => undefined
    $('[part~="voice-mini-action"]')!.click()
    await new Promise((r) => setTimeout(r, 20))
    expect(el.getAttribute('data-view')).toBe('closed')
    // Voice started later from the panel is not minimized on close by a stale request.
    voice(el, true)
    await new Promise((r) => setTimeout(r, 20))
    expect(el.getAttribute('data-view')).toBe('closed')
  })

  test('minimize: the panel closes, voice goes on in the launcher with a bubble and the buttons over the orb', async () => {
    const { el, $, $$ } = await opened()
    voice(el, true, 'speaking', { line: { text: 'Hardcover has twelve returns pending.', upto: 10 } })
    await until(() => $('[part~="minimize-button"]'))
    const closes: number[] = []
    el.addEventListener('evokai-close', () => closes.push(1))
    $('[part~="minimize-button"]')!.click()
    await until(() => el.getAttribute('data-view') === 'minimized' && $('[part~="voice-bubble"]'))
    expect(el.isOpen).toBe(false)
    expect(closes).toEqual([1])
    expect($('.panel')).toBeNull()
    expect(orbs(el)).toBe(1)
    expect(here(el)).toEqual(['launcher'])
    expect(el.controller!.state.voiceOn).toBe(true)
    const bubble = $('[part~="voice-bubble"]')!
    expect(bubble.querySelector('button')).toBeNull()
    expect(bubble.textContent).toContain('Speaking')
    expect(bubble.querySelector('.said')!.textContent).toBe('Hardcover has')
    expect($$('[part~="voice-action"]').map((b) => b.getAttribute('aria-label'))).toEqual(['Mute the microphone', 'Open the panel again', 'End voice mode'])
    expect($('[part~="restore-action"] svg circle')).not.toBeNull()
    expect($('.launcher')!.getAttribute('aria-label')).toBe('Open the panel again')
    $('[part~="mute-action"]')!.click()
    await until(() => el.controller!.state.muted)
    expect($('[part~="voice-bubble"]')!.textContent).toContain('Microphone off')
    // A click on the orb brings the panel back on voice mode.
    $('.launcher')!.click()
    await until(() => el.getAttribute('data-view') === 'voice' && $('.panel[data-layer=voice]'))
    expect(el.isOpen).toBe(true)
    expect($('[part~="voice-bubble"]')).toBeNull()
  })

  test('ending voice from the minimized orb leaves the panel closed', async () => {
    const { el, $ } = await opened()
    voice(el, true)
    await until(() => $('[part~="minimize-button"]'))
    el.close()
    await until(() => el.getAttribute('data-view') === 'minimized' && $('[part~="end-action"]'))
    $('[part~="end-action"]')!.click()
    await until(() => el.getAttribute('data-view') === 'closed' && !$('[part~="voice-bubble"]'))
    expect(el.controller!.state.voiceOn).toBe(false)
    expect(el.isOpen).toBe(false)
    expect($('.panel')).toBeNull()
    expect(here(el)).toEqual(['launcher'])
  })

  test('closing the panel from voice ends voice: the panel is closed, not minimized', async () => {
    const { el, $ } = await opened()
    voice(el, true)
    await until(() => $('[part~="minimize-button"]'))
    $('[part~="close-button"]')!.click()
    await until(() => el.getAttribute('data-view') === 'closed' && !$('.panel'))
    expect(el.controller!.state.voiceOn).toBe(false)
    expect($('[part~="voice-bubble"]')).toBeNull()
  })

  test('the latest chart sits under the subtitles; the minimized bubble chip opens the panel on it', async () => {
    const { el, $ } = await opened({}, [[{ type: 'text', delta: 'Here.' }, { type: 'chart', chart: CHART as never }]])
    await el.send('chart please')
    await until(() => $('figure'))
    voice(el, true, 'speaking', { line: { text: 'Here is the chart.', upto: 4 } })
    await until(() => $('[part~="stage-cards"] figure'))
    el.close()
    await until(() => $('[part~="voice-bubble-chart"]'))
    expect($('[part~="voice-bubble-chart"]')!.textContent).toContain('Revenue by format')
    $('[part~="voice-bubble-open"]')!.click()
    await until(() => el.getAttribute('data-view') === 'voice' && $('[part~="stage-cards"] figure'))
  })

  test('after the user talks over the agent the voice listens: only the text says it was interrupted', async () => {
    const { el, $ } = await opened({ locale: 'it' })
    voice(el, true, 'interrupted')
    await until(() => $('.panel[data-layer=voice]'))
    expect($('[part~="tagline"]')!.textContent).toBe('Interrotta, ti ascolto…')
    expect($('[part~="stage-status"]')!.textContent).toBe('Interrotta, ti ascolto')
    expect($('[part~="stage"]')!.getAttribute('data-phase')).toBe('listening')
    voice(el, true, 'interrupted', { heard: 'aspetta' })
    await until(() => $('[part~="stage"]')!.getAttribute('data-phase') === 'user')
    expect($('[part~="subtitles"]')!.textContent).toBe('aspetta')
    el.close()
    await until(() => $('[part~="voice-bubble"]'))
    expect($('[part~="voice-bubble-status"]')!.textContent).toBe('Interrotta, ti ascolto')
    expect($('[part~="voice-bubble-text"]')!.textContent).toBe('aspetta')
    expect(stringsFor('en').interrupted).toBe('Interrupted, listening…')
  })

  test('a microphone that went to sleep says so, and any tap on the conversation wakes it', async () => {
    const { el, $ } = await opened({ locale: 'it' })
    voice(el, true, 'listening', { micOpen: false, micAsleep: true })
    await until(() => $('.panel[data-layer=voice]'))
    expect($('[part~="stage-status"]')!.textContent).toBe('Tocca per riattivare l’ascolto')
    expect(stringsFor('en').micAsleep).toBe('Tap to listen again')
    const c = el.controller as unknown as { wakeMic(): void }
    let woken = 0
    c.wakeMic = () => void woken++
    $('[part~="stage-status"]')!.click()
    expect(woken).toBe(1)
    // Muted is what the user chose: it wins over the sleeping microphone.
    voice(el, true, 'listening', { micOpen: false, micAsleep: true, muted: true })
    await until(() => $('[part~="stage-status"]')!.textContent === 'Microfono spento')
  })

  test('minimize and restore are replaceable icons, with texts in both languages', () => {
    expect(ICON_NAMES).toEqual(expect.arrayContaining(['minimize', 'restore', 'chart']))
    expect(stringsFor('it')).toMatchObject({ minimize: 'Riduci', restore: 'Riapri', restoreHint: 'Riapri il pannello', openChart: 'Apri' })
    expect(stringsFor('en')).toMatchObject({ minimize: 'Minimize', restoreHint: 'Open the panel again' })
  })
})
