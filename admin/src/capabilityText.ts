/**
 * What the console says about a capability, in the console's language: the service sends the catalog's English texts,
 * the dictionaries hold them in English and Italian keyed by the capability's id (cap.<id>, capw.<id>). A capability the
 * dictionaries do not know yet keeps the service's text.
 */
import { has, t } from './i18n'

/** The capability's description. */
export function capDescription(x: { name: string; description?: string | null }): string {
  const key = `cap.${x.name}`
  return has(key) ? t(key) : (x.description ?? '')
}

/** What the capability still needs before it works here (empty when nothing). */
export function capWiring(x: { name: string; wiring?: string | null }): string {
  if (!x.wiring) return ''
  const key = `capw.${x.name}`
  return has(key) ? t(key) : x.wiring
}
