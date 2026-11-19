/** Locale packs. English is the default; Italian is opt-in data (`locale: 'it'`). */
import { en } from './en'
import { it } from './it'
import type { LocaleCode, LocalePack } from './types'

export type { ChatText, LocaleCode, LocalePack, SpeechRules, VoiceText } from './types'

/** The built-in packs. */
export const locales: Record<LocaleCode, LocalePack> = { en, it }

/** A pack from a code (unknown codes fall back to English) or a pack of your own. */
export function localePack(locale: LocaleCode | LocalePack | (string & {}) | undefined): LocalePack {
  if (locale && typeof locale === 'object') return locale
  const code = (locale ?? 'en').toLowerCase().split('-')[0] as LocaleCode
  return locales[code] ?? en
}
