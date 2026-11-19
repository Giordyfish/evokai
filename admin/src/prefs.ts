/**
 * The signed-in person's preferences: language, date and time format, theme, density and accessibility.
 *
 * They apply at once as attributes on <html> (styles.css reads them) and as the console's language. A copy is kept in
 * this browser so the first paint already uses them; the service keeps the person's copy per console identity
 * (`/v1/console/me/preferences`), which wins when it arrives. A person who never saved any brings this browser's copy
 * along on their first sign-in.
 */
import type { ConsolePreferences, ConsolePreferencesView } from '@client/core/types'

import { fromBrowser, type Locale, setLocale } from './i18n'

export type Prefs = ConsolePreferences

export const DEFAULTS: Prefs = {
  version: 1,
  language: 'auto',
  dateFormat: 'auto',
  timeFormat: 'auto',
  theme: 'system',
  density: 'comfortable',
  motion: 'system',
  mascot: 'on',
  textSize: 100,
  contrast: 'system',
  focusAlways: false,
  underlineLinks: false,
}

/** The choices of each field: the same allow-list as the service. */
export const CHOICES = {
  language: ['auto', 'en', 'it'],
  dateFormat: ['auto', 'dmy', 'mdy', 'ymd'],
  timeFormat: ['auto', '24h', '12h'],
  theme: ['system', 'light', 'dark'],
  density: ['comfortable', 'compact'],
  motion: ['system', 'reduce', 'full'],
  mascot: ['on', 'still', 'hidden'],
  textSize: [100, 112, 125],
  contrast: ['system', 'more', 'normal'],
} as const satisfies { [K in keyof Prefs]?: readonly Prefs[K][] }

const FLAGS = ['focusAlways', 'underlineLinks'] as const

/** Preferences from anything (a cached copy, an older version): every field that is not one of its choices is the default. */
export function sanitize(x: unknown): Prefs {
  const o = x && typeof x === 'object' ? (x as Record<string, unknown>) : {}
  const out: Record<string, unknown> = { ...DEFAULTS }
  for (const [k, allowed] of Object.entries(CHOICES)) if ((allowed as readonly unknown[]).includes(o[k])) out[k] = o[k]
  for (const k of FLAGS) if (typeof o[k] === 'boolean') out[k] = o[k]
  return out as unknown as Prefs
}

export const same = (a: Prefs, b: Prefs): boolean => JSON.stringify(sanitize(a)) === JSON.stringify(sanitize(b))

/** What the system asks for, for the choices that follow it. */
export interface System {
  dark: boolean
  reducedMotion: boolean
  moreContrast: boolean
  languages: readonly string[]
}

const media = (q: string): boolean => typeof matchMedia === 'function' && matchMedia(q).matches

export function system(): System {
  const nav = typeof navigator === 'undefined' ? undefined : navigator
  return {
    dark: media('(prefers-color-scheme: dark)'),
    reducedMotion: media('(prefers-reduced-motion: reduce)'),
    moreContrast: media('(prefers-contrast: more)'),
    languages: nav ? (nav.languages?.length ? nav.languages : [nav.language]) : [],
  }
}

export const languageOf = (p: Prefs, s: System): Locale => (p.language === 'auto' ? fromBrowser(s.languages) : p.language)

/**
 * The attributes on <html> that carry the preferences (styles.css reads them): `data-theme` light or dark,
 * `data-motion` reduce or full, `data-contrast` more or normal, `data-mascot`, `data-density`, `data-text` (the text
 * size in percent), `data-focus` always or auto, `data-links` underline or auto.
 */
export function attributes(p: Prefs, s: System): Record<string, string> {
  return {
    theme: p.theme === 'system' ? (s.dark ? 'dark' : 'light') : p.theme,
    motion: p.motion === 'system' ? (s.reducedMotion ? 'reduce' : 'full') : p.motion,
    contrast: p.contrast === 'system' ? (s.moreContrast ? 'more' : 'normal') : p.contrast,
    mascot: p.mascot,
    density: p.density,
    text: String(p.textSize),
    focus: p.focusAlways ? 'always' : 'auto',
    links: p.underlineLinks ? 'underline' : 'auto',
  }
}

/** Write the preferences on an element (the document's root) and switch the language. */
export function applyTo(el: { dataset: DOMStringMap }, p: Prefs, s: System): void {
  for (const [k, v] of Object.entries(attributes(p, s))) el.dataset[k] = v
  setLocale(languageOf(p, s))
}

// The current preferences, this browser's copy and the service's.

const STORE = 'evk-prefs'
/** Before preferences, the language and the theme were kept on their own. */
const LEGACY_LANG = 'evk-lang'
const LEGACY_THEME = 'evk-theme'

function cached(): Prefs {
  try {
    const raw = localStorage.getItem(STORE)
    if (raw) return sanitize(JSON.parse(raw))
    const lang = localStorage.getItem(LEGACY_LANG)
    const theme = localStorage.getItem(LEGACY_THEME)
    return sanitize({ ...DEFAULTS, language: lang ?? 'auto', theme: theme ?? 'system' })
  } catch {
    return { ...DEFAULTS }
  }
}

function keep(p: Prefs): void {
  try {
    localStorage.setItem(STORE, JSON.stringify(p))
    localStorage.removeItem(LEGACY_LANG)
    localStorage.removeItem(LEGACY_THEME)
  } catch {
    /* storage blocked: the service's copy still applies after sign in */
  }
}

let current: Prefs = { ...DEFAULTS }
const listeners = new Set<(p: Prefs) => void>()

export const prefs = (): Prefs => current

export function onPrefs(f: (p: Prefs) => void): () => void {
  listeners.add(f)
  return () => listeners.delete(f)
}

function apply(p: Prefs): void {
  current = p
  if (typeof document !== 'undefined') applyTo(document.documentElement, p, system())
  listeners.forEach((f) => f(p))
}

/** Whether animations are reduced now (the preference, or the system's when it follows the system). */
export const reducedMotion = (): boolean => (current.motion === 'system' ? system().reducedMotion : current.motion === 'reduce')

/** Whether the mascot moves: not when it is still or hidden, nor under reduced motion. */
export const mascotMoves = (): boolean => current.mascot === 'on' && !reducedMotion()

// Saving: the whole document, a moment after the last change (a run of clicks is one save and one audit entry).

export type SaveState = 'local' | 'saving' | 'saved' | 'error'
export interface Saver {
  get: () => Promise<ConsolePreferencesView>
  put: (p: Prefs) => Promise<ConsolePreferencesView>
  reset: () => Promise<ConsolePreferencesView>
}

let saver: Saver | null = null
let timer: ReturnType<typeof setTimeout> | undefined
let state: { s: SaveState; at: string | null; error: unknown } = { s: 'local', at: null, error: null }
const stateListeners = new Set<(s: typeof state) => void>()

const setState = (v: Partial<typeof state>) => {
  state = { ...state, ...v }
  stateListeners.forEach((f) => f(state))
}
export const saveState = () => state
export function onSaveState(f: (s: typeof state) => void): () => void {
  stateListeners.add(f)
  return () => stateListeners.delete(f)
}

export const SAVE_DELAY = 600

async function save(): Promise<void> {
  if (!saver) return
  const s = saver
  setState({ s: 'saving', error: null })
  try {
    const v = await s.put(current)
    if (s === saver) setState({ s: 'saved', at: v.updatedAt, error: null })
  } catch (e) {
    if (s === saver) setState({ s: 'error', error: e })
  }
}

/** Change some preferences: applied and kept in this browser at once, saved for the person shortly after. */
export function setPrefs(patch: Partial<Prefs>): void {
  const next = sanitize({ ...current, ...patch })
  apply(next)
  keep(next)
  if (!saver) return
  clearTimeout(timer)
  setState({ s: 'saving' })
  timer = setTimeout(() => void save(), SAVE_DELAY)
}

/** Save now what is pending (after an error, or before leaving). */
export function saveNow(): Promise<void> {
  clearTimeout(timer)
  return save()
}

/** Back to the defaults, for this person everywhere. */
export async function resetPrefs(): Promise<void> {
  clearTimeout(timer)
  apply({ ...DEFAULTS })
  keep(current)
  if (!saver) return
  setState({ s: 'saving', error: null })
  try {
    const v = await saver.reset()
    setState({ s: 'saved', at: v.updatedAt, error: null })
  } catch (e) {
    setState({ s: 'error', error: e })
  }
}

/**
 * After sign in: the service's copy wins when this person saved one; when they never did, this browser's copy (if it
 * is not the defaults) becomes theirs.
 */
export async function syncPrefs(s: Saver): Promise<void> {
  saver = s
  clearTimeout(timer)
  try {
    const v = await s.get()
    if (saver !== s) return
    if (v.updatedAt) {
      const theirs = sanitize(v.preferences)
      if (!same(theirs, current)) apply(theirs)
      keep(theirs)
      setState({ s: 'saved', at: v.updatedAt, error: null })
    } else if (!same(current, DEFAULTS)) await save()
    else setState({ s: 'saved', at: null, error: null })
  } catch (e) {
    if (saver === s) setState({ s: 'error', error: e })
  }
}

/** Signed out: changes stay in this browser until the next sign in. */
export function detachPrefs(): void {
  clearTimeout(timer)
  saver = null
  setState({ s: 'local', at: null, error: null })
}

/** Read this browser's copy, apply it, and follow the system for the choices that follow it. */
export function initPrefs(): void {
  apply(cached())
  if (typeof matchMedia !== 'function') return
  for (const q of ['(prefers-color-scheme: dark)', '(prefers-reduced-motion: reduce)', '(prefers-contrast: more)'])
    matchMedia(q).addEventListener?.('change', () => apply(current))
  addEventListener('languagechange', () => apply(current))
}
