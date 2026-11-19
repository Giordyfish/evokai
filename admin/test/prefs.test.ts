/** The person's preferences: the allow-list, what they set on <html>, the formats, and the sync with the service. */
import type { ConsolePreferencesView } from '@client/core/types'

import { clock, dateTime, day } from '../src/format'
import { locale, setLocale } from '../src/i18n'
import {
  applyTo,
  attributes,
  DEFAULTS,
  detachPrefs,
  mascotMoves,
  type Prefs,
  prefs,
  reducedMotion,
  resetPrefs,
  SAVE_DELAY,
  sanitize,
  saveState,
  setPrefs,
  type Saver,
  syncPrefs,
  type System,
} from '../src/prefs'

const SYS: System = { dark: false, reducedMotion: false, moreContrast: false, languages: ['en-GB'] }

function fakeSaver(stored: Prefs | null) {
  const puts: Prefs[] = []
  let saved = stored
  const view = (): ConsolePreferencesView => ({ preferences: saved ?? DEFAULTS, updatedAt: saved ? '2026-11-15T10:00:00+00:00' : null })
  const saver: Saver = {
    get: async () => view(),
    put: async (p) => {
      puts.push(p)
      saved = p
      return view()
    },
    reset: async () => {
      saved = null
      return view()
    },
  }
  return { saver, puts }
}

beforeEach(() => {
  detachPrefs()
  setPrefs(DEFAULTS)
})
afterEach(() => {
  vi.useRealTimers()
  detachPrefs()
  setPrefs(DEFAULTS)
  setLocale('en')
})

describe('the allow-list', () => {
  it('keeps every valid choice and drops anything else to its default', () => {
    const p = sanitize({ ...DEFAULTS, theme: 'dark', textSize: 125, focusAlways: true, language: 'it' })
    expect([p.theme, p.textSize, p.focusAlways, p.language]).toEqual(['dark', 125, true, 'it'])
    const bad = sanitize({ theme: 'purple', textSize: '125', focusAlways: 'yes', language: 'de', fontFamily: 'Comic Sans', version: 9 })
    expect(bad).toEqual(DEFAULTS)
    expect(sanitize(null)).toEqual(DEFAULTS)
    expect(sanitize('nonsense')).toEqual(DEFAULTS)
  })
})

describe('on <html>', () => {
  it('resolves the choices that follow the system', () => {
    expect(attributes(DEFAULTS, SYS)).toEqual({
      theme: 'light',
      motion: 'full',
      contrast: 'normal',
      mascot: 'on',
      density: 'comfortable',
      text: '100',
      focus: 'auto',
      links: 'auto',
    })
    const a = attributes(DEFAULTS, { ...SYS, dark: true, reducedMotion: true, moreContrast: true })
    expect([a.theme, a.motion, a.contrast]).toEqual(['dark', 'reduce', 'more'])
    // An explicit choice wins over the system.
    const b = attributes({ ...DEFAULTS, theme: 'light', motion: 'full', contrast: 'normal' }, { ...SYS, dark: true, reducedMotion: true, moreContrast: true })
    expect([b.theme, b.motion, b.contrast]).toEqual(['light', 'full', 'normal'])
  })

  it('writes the attributes on the element and switches the language', () => {
    const el = { dataset: {} as DOMStringMap }
    applyTo(el, { ...DEFAULTS, textSize: 125, contrast: 'more', focusAlways: true, underlineLinks: true, mascot: 'hidden', language: 'it' }, SYS)
    expect(el.dataset).toMatchObject({ text: '125', contrast: 'more', focus: 'always', links: 'underline', mascot: 'hidden' })
    expect(locale()).toBe('it')
    applyTo(el, DEFAULTS, { ...SYS, languages: ['it-IT'] })
    expect(locale()).toBe('it')
    applyTo(el, DEFAULTS, { ...SYS, languages: ['de-DE', 'it'] })
    expect(locale()).toBe('en')
  })

  it('reduces motion and stills the mascot as asked', () => {
    setPrefs({ motion: 'reduce' })
    expect(reducedMotion()).toBe(true)
    expect(mascotMoves()).toBe(false)
    setPrefs({ motion: 'full', mascot: 'on' })
    expect(reducedMotion()).toBe(false)
    expect(mascotMoves()).toBe(true)
    setPrefs({ mascot: 'still' })
    expect(mascotMoves()).toBe(false)
  })
})

describe('dates and times', () => {
  const d = new Date(2025, 10, 5, 14, 30)
  it('follow the language unless the person picks an order or a clock', () => {
    expect(day(d)).toBe('05 Nov 2025')
    expect(clock(d)).toBe('14:30')
    setPrefs({ dateFormat: 'mdy' })
    expect(day(d)).toBe('Nov 5, 2025')
    setPrefs({ dateFormat: 'ymd', timeFormat: '12h' })
    expect(dateTime(d)).toBe('2025-11-05, 2:30 pm')
    setPrefs({ dateFormat: 'dmy', timeFormat: '24h' })
    setLocale('it')
    expect(dateTime(d)).toBe('05 nov 2025, 14:30')
  })
})

describe('with the service', () => {
  it('takes the saved copy when the person has one', async () => {
    setPrefs({ theme: 'light' })
    const { saver, puts } = fakeSaver({ ...DEFAULTS, theme: 'dark', textSize: 112 })
    await syncPrefs(saver)
    expect(prefs().theme).toBe('dark')
    expect(prefs().textSize).toBe(112)
    expect(puts).toEqual([])
    expect(saveState().s).toBe('saved')
  })

  it("brings this browser's copy along when the person never saved one", async () => {
    setPrefs({ contrast: 'more' })
    const { saver, puts } = fakeSaver(null)
    await syncPrefs(saver)
    expect(puts).toHaveLength(1)
    expect(puts[0]!.contrast).toBe('more')
  })

  it('saves a run of changes once, a moment after the last', async () => {
    const { saver, puts } = fakeSaver(null)
    await syncPrefs(saver)
    vi.useFakeTimers()
    setPrefs({ textSize: 112 })
    setPrefs({ textSize: 125 })
    setPrefs({ underlineLinks: true })
    expect(saveState().s).toBe('saving')
    expect(puts).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(SAVE_DELAY + 10)
    expect(puts).toHaveLength(1)
    expect(puts[0]).toMatchObject({ textSize: 125, underlineLinks: true })
    expect(saveState().s).toBe('saved')
  })

  it('keeps changes in this browser only while signed out, and resets everywhere', async () => {
    const { saver, puts } = fakeSaver({ ...DEFAULTS, theme: 'dark' })
    detachPrefs()
    setPrefs({ theme: 'light' })
    expect(saveState().s).toBe('local')
    await syncPrefs(saver)
    expect(puts).toEqual([])
    await resetPrefs()
    expect(prefs()).toEqual(DEFAULTS)
    expect((await saver.get()).updatedAt).toBeNull()
  })

  it('says when a save failed', async () => {
    const { saver } = fakeSaver(null)
    await syncPrefs({ ...saver, put: async () => Promise.reject(new Error('down')) })
    vi.useFakeTimers()
    setPrefs({ mascot: 'hidden' })
    await vi.advanceTimersByTimeAsync(SAVE_DELAY + 10)
    expect(saveState().s).toBe('error')
  })
})
