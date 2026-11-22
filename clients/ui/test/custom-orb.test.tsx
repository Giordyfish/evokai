import * as orbModule from '../src/orb'
import { defineElements, type EvokaiChatElement } from '../src/elements'
import { CSS } from '../src/styles'
import { orbImageFor, orbImages, orbTransition, themeVars, type OrbImages } from '../src/theme'
import { fakeEvokai, tick } from './fake'

// Every WebGL orb the component creates, counted.
vi.mock('../src/orb', async (original) => {
  const mod = await original<typeof orbModule>()
  return { ...mod, createOrb: vi.fn(mod.createOrb) }
})

defineElements()

const IMAGES: OrbImages = { idle: 'idle.webp', listening: 'listening.webp', speaking: 'speaking.webp' }

async function mounted(setup: (el: EvokaiChatElement) => void = () => {}) {
  const evokai = fakeEvokai([])
  const el = document.createElement('evokai-chat') as EvokaiChatElement
  el.fetcher = evokai.fetch
  el.setAttribute('base-url', 'https://agents.test/agent')
  el.setAttribute('token-url', '/api/agent/token')
  setup(el)
  document.body.append(el)
  await tick()
  const $ = (sel: string) => el.shadowRoot!.querySelector<HTMLElement>(sel)
  return { el, $ }
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
const voice = (el: EvokaiChatElement, on: boolean, phase = 'listening') => {
  const c = el.controller as unknown as Ctl
  c.patch({ voiceOn: on, session: { ...c.state.session, phase: on ? phase : 'idle', busy: phase === 'thinking' } })
}

async function open(el: EvokaiChatElement) {
  el.open()
  await until(() => el.getAttribute('data-view') === 'chat' && el.shadowRoot!.querySelector('.panel[data-box=open]'))
  await tick(50)
}

const img = (el: EvokaiChatElement) => el.shadowRoot!.querySelector<HTMLImageElement>('.orbfly img.orbimg')
const webglOrbs = (el: EvokaiChatElement) => el.shadowRoot!.querySelectorAll('canvas.orbgl').length

afterEach(() => {
  vi.mocked(orbModule.createOrb).mockClear()
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('an element of the page as the orb', () => {
  test('it travels between the rests in place of the orb, the same node all along, and the host says where it is', async () => {
    const figure = document.createElement('div')
    figure.setAttribute('slot', 'orb')
    const { el, $ } = await mounted((e) => e.append(figure))
    await until(() => el.getAttribute('data-orb') === 'launcher')
    expect(webglOrbs(el)).toBe(0)
    // Not even for the first frame: the element is known from the first render.
    expect(orbModule.createOrb).not.toHaveBeenCalled()
    const slot = $('.orbfly slot[name=orb]') as HTMLSlotElement
    expect(slot).toBeTruthy()
    expect($('.orbfly')!.getAttribute('data-kind')).toBe('element')

    await open(el)
    expect(el.getAttribute('data-orb')).toBe('head')
    voice(el, true)
    await until(() => el.getAttribute('data-orb') === 'center' && el.getAttribute('data-view') === 'voice')
    el.close()
    await until(() => el.getAttribute('data-orb') === 'launcher' && el.getAttribute('data-view') === 'minimized')
    expect($('[part~="voice-bubble"]')).toBeTruthy()
    expect(el.querySelector('[slot=orb]')).toBe(figure)
    expect(webglOrbs(el)).toBe(0)
  })

  test('added after the component is on the page, it replaces the orb there and then', async () => {
    const { el } = await mounted()
    await until(() => webglOrbs(el) === 1)
    const figure = document.createElement('div')
    figure.setAttribute('slot', 'orb')
    el.append(figure)
    await until(() => webglOrbs(el) === 0 && el.shadowRoot!.querySelector('.orbfly slot[name=orb]'))
    figure.remove()
    await until(() => webglOrbs(el) === 1)
  })

  test('a child that becomes the orb later (its slot set afterwards) replaces it too', async () => {
    const figure = document.createElement('div')
    const { el } = await mounted((e) => e.append(figure))
    await until(() => webglOrbs(el) === 1)
    figure.setAttribute('slot', 'orb')
    await until(() => webglOrbs(el) === 0 && el.shadowRoot!.querySelector('.orbfly slot[name=orb]'))
  })

  test('it wins over images', async () => {
    const figure = document.createElement('div')
    figure.setAttribute('slot', 'orb')
    const { el } = await mounted((e) => {
      e.setAttribute('orb-images', JSON.stringify(IMAGES))
      e.append(figure)
    })
    await until(() => el.shadowRoot!.querySelector('.orbfly slot[name=orb]'))
    expect(img(el)).toBeNull()
  })
})

describe('an orb made of images', () => {
  test('one image per chat state, idle for a state without one', async () => {
    const { el } = await mounted((e) => e.setAttribute('orb-images', JSON.stringify(IMAGES)))
    await until(() => img(el)?.getAttribute('src') === 'idle.webp')
    expect(webglOrbs(el)).toBe(0)
    expect(orbModule.createOrb).not.toHaveBeenCalled()
    await open(el)
    voice(el, true, 'listening')
    await until(() => el.getAttribute('data-state') === 'listening' && img(el)?.getAttribute('src') === 'listening.webp')
    voice(el, true, 'speaking')
    await until(() => img(el)?.getAttribute('src') === 'speaking.webp')
    voice(el, true, 'thinking')
    await until(() => el.getAttribute('data-state') === 'thinking' && img(el)?.getAttribute('src') === 'idle.webp')
  })

  test('from the theme too; the attribute wins over the theme', async () => {
    const { el } = await mounted((e) => {
      e.theme = { orb: { main: '#0b3d91', images: { idle: 'theme-idle.png' } } }
    })
    await until(() => img(el)?.getAttribute('src') === 'theme-idle.png')
    // The images are not a colour: no custom property for them.
    expect(el.style.getPropertyValue('--evokai-orb-images')).toBe('')
    expect(el.style.getPropertyValue('--evokai-orb-main')).toBe('#0b3d91')
    el.setAttribute('orb-images', JSON.stringify({ idle: 'attr-idle.gif' }))
    await until(() => img(el)?.getAttribute('src') === 'attr-idle.gif')
  })

  test('a one-shot image plays on a change of state, then the state image', async () => {
    const images = { ...IMAGES, transitions: { '*>speaking': { src: 'hello.webp', ms: 60 }, '*>done': null } } as unknown as OrbImages
    const { el } = await mounted((e) => e.setAttribute('orb-images', JSON.stringify(images)))
    await open(el)
    voice(el, true, 'speaking')
    await until(() => img(el)?.getAttribute('src') === 'hello.webp')
    await until(() => img(el)?.getAttribute('src') === 'speaking.webp')
  })

  test('with reduced motion the still image stands in for every state', async () => {
    const before = window.matchMedia
    window.matchMedia = ((q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia
    try {
      const { el } = await mounted((e) => e.setAttribute('orb-images', JSON.stringify({ ...IMAGES, still: 'still.png' })))
      await until(() => img(el)?.getAttribute('src') === 'still.png')
      await open(el)
      voice(el, true, 'speaking')
      await until(() => el.getAttribute('data-state') === 'speaking')
      await tick(20)
      expect(img(el)?.getAttribute('src')).toBe('still.png')
    } finally {
      window.matchMedia = before
    }
  })

  test('the helpers: idle as fallback, transitions by pair or from any state, a broken attribute is ignored', () => {
    expect(orbImageFor(IMAGES, 'tool')).toBe('idle.webp')
    expect(orbImageFor(IMAGES, 'listening')).toBe('listening.webp')
    const t = { ...IMAGES, transitions: { 'confirm>tool': { src: 'yes.webp', ms: 900 }, '*>done': { src: 'end.webp', ms: 500 } } }
    expect(orbTransition(t, 'confirm', 'tool')?.src).toBe('yes.webp')
    expect(orbTransition(t, 'thinking', 'tool')).toBeNull()
    expect(orbTransition(t, 'speaking', 'done')?.src).toBe('end.webp')
    expect(orbImages('{not json', { images: IMAGES })).toBe(IMAGES)
    expect(orbImages('{not json', undefined)).toBeNull()
    expect(orbImages(null, { images: IMAGES })).toBe(IMAGES)
    expect(orbImages('{"listening":"x.png"}', undefined)).toBeNull()
    expect(themeVars({ orb: { ink: '#000000', images: IMAGES } })).toEqual({ '--evokai-orb-ink': '#000000' })
  })
})

describe('the launcher on the left', () => {
  test('the bubble sits beside the launcher on its other side, whatever the launcher size', () => {
    expect(CSS).toContain('.vbubble{position:absolute;right:calc(100% + 16px)')
    expect(CSS).toContain(':host([position=bottom-left]) .vbubble{right:auto;left:calc(100% + 16px)')
    expect(CSS).toContain(':host([position=bottom-left]) .dock{right:auto;left:24px}')
    expect(CSS).toContain(':host([position=bottom-left]) .panel{right:auto;left:24px}')
  })
})
