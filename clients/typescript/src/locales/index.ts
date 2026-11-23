/** Locale packs. English is the default; the other languages are opt-in data (`locale: 'it'`). */
import { de } from './de'
import { en } from './en'
import { es } from './es'
import { fr } from './fr'
import { it } from './it'
import { ro } from './ro'
import { sv } from './sv'
import type { LocaleCode, LocalePack } from './types'

export type { ChatText, LocaleCode, LocalePack, SpeechRules, VoiceText } from './types'

/** The built-in packs. */
export const locales: Record<LocaleCode, LocalePack> = { en, it, es, fr, de, ro, sv }

/** A pack from a code (unknown codes fall back to English) or a pack of your own. */
export function localePack(locale: LocaleCode | LocalePack | (string & {}) | undefined): LocalePack {
  if (locale && typeof locale === 'object') return locale
  const code = (locale ?? 'en').toLowerCase().split('-')[0] as LocaleCode
  return locales[code] ?? en
}
