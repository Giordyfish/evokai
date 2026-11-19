/** Why a confirmation is asked, as a sentence the interface can show next to the item. */
import type { ConfirmReason } from '../core/types'
import { localePack, type LocalePack } from '../locales'

/**
 * The text of a confirmation's reason: the ask rule's own label when it has one, else the pack's sentence for its key
 * (English when the pack has none). Empty for an unknown key or no reason.
 */
export function confirmReasonText(why: ConfirmReason | null | undefined, pack: LocalePack = localePack('en')): string {
  if (!why) return ''
  if (why.label) return why.label
  const texts = (pack.chatText ?? localePack('en').chatText)?.confirmReasons as Record<string, string> | undefined
  return texts?.[why.key] ?? ''
}
