/**
 * The console's languages: English (the source) and Italian. Every text a person reads comes from the dictionaries in
 * this folder, by key. English defines the keys; the Italian dictionary has the same type, so a missing or extra
 * Italian string fails the typecheck (and `test/i18n.test.ts` checks the placeholders match).
 *
 * The language follows the browser (`it*` is Italian, anything else English) until the person picks one in their
 * preferences (`src/prefs.ts`, which keeps the choice and calls `setLocale`). Dates and numbers use the same language.
 */
import type { ComponentChildren } from 'preact'

import { en, type Messages, type Plural } from './en'
import { it } from './it'

export type Locale = 'en' | 'it'
export const LOCALES: readonly Locale[] = ['en', 'it'] as const
export type Key = keyof Messages
/** The keys whose message has a singular and a plural. */
export type PluralKey = { [K in Key]: Messages[K] extends Plural ? K : never }[Key]
/** The keys of plain messages. */
export type TextKey = Exclude<Key, PluralKey>

const DICTS: Record<Locale, Messages> = { en, it }

/** The language a browser asks for: Italian for any `it*` tag, else English. */
export function fromBrowser(languages: readonly string[] | undefined): Locale {
  const first = languages?.find(Boolean)
  return first && /^it\b/i.test(first) ? 'it' : 'en'
}

function initial(): Locale {
  if (typeof navigator === 'undefined') return 'en'
  return fromBrowser(navigator.languages?.length ? navigator.languages : [navigator.language])
}

let current: Locale = initial()
const listeners = new Set<(l: Locale) => void>()

const mark = (l: Locale) => {
  if (typeof document !== 'undefined') document.documentElement.lang = l
}
mark(current)

export const locale = (): Locale => current

/** Switch the language (the person's choice is kept by their preferences, `src/prefs.ts`). */
export function setLocale(l: Locale): void {
  if (l === current) return
  current = l
  mark(l)
  listeners.forEach((f) => f(l))
}

export function onLocale(f: (l: Locale) => void): () => void {
  listeners.add(f)
  return () => listeners.delete(f)
}

/** The BCP 47 tag for Intl. */
export const intlTag = (l: Locale = current): string => (l === 'it' ? 'it-IT' : 'en-GB')

/** A number as the language writes it (`1,234.5` or `1.234,5`). */
export const num = (n: number, opts?: Intl.NumberFormatOptions): string => n.toLocaleString(intlTag(), opts)

export type Vars = Record<string, string | number | null | undefined>

const fill = (s: string, vars?: Vars): string =>
  vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? (typeof vars[k] === 'number' ? num(vars[k] as number) : String(vars[k] ?? '')) : m)) : s

/** A message in the current language; `{name}` placeholders are filled from `vars` (numbers formatted). */
export function t(key: TextKey, vars?: Vars): string {
  return fill(DICTS[current][key] as string, vars)
}

/** Whether a key exists (for texts keyed by ids that come from the service). */
export function has(key: string): key is TextKey {
  return Object.prototype.hasOwnProperty.call(en, key) && typeof (en as Record<string, unknown>)[key] === 'string'
}

/** A counted message: the singular or plural form, with `{n}` the count. */
export function tn(key: PluralKey, n: number, vars?: Vars): string {
  const m = DICTS[current][key] as Plural
  return fill(n === 1 ? m.one : m.other, { ...vars, n })
}

/**
 * A message with parts that are elements (a name in bold, a link): `{name}` placeholders are replaced by the nodes in
 * `parts`, strings and numbers as for `t`.
 */
export function rich(key: TextKey, parts: Record<string, ComponentChildren>): ComponentChildren[] {
  const s = DICTS[current][key] as string
  const out: ComponentChildren[] = []
  let last = 0
  for (const m of s.matchAll(/\{(\w+)\}/g)) {
    out.push(s.slice(last, m.index))
    const v = parts[m[1]!]
    out.push(typeof v === 'number' ? num(v) : v)
    last = m.index! + m[0].length
  }
  out.push(s.slice(last))
  return out.filter((x) => x !== '')
}

/** For tests: the dictionaries themselves. */
export const dictionaries = DICTS
