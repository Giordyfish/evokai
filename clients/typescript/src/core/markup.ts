/**
 * The answers' inline markup (`docs/markup.md`): cues (`{wave}`) and speech tags (`[laughs]`) stay in the `text`
 * events and the stored messages, so a chat that shows them hides them with `stripMarkup` and the session's
 * delimiters (`SessionInfo.markup`). The grammar is the server's: a cue is `open name close` or `open name:arg
 * close`, a speech tag `open words close` not followed by `(` (a Markdown link).
 */
import type { MarkupView } from './types'

const esc = (s: string): string => s.replace(/[.*+?^${}()|[\]\\/-]/g, '\\$&')

const cache = new Map<string, RegExp | null>()

/** The pattern of a session's markup (null without any). */
export function markupPattern(view: MarkupView | null | undefined): RegExp | null {
  if (!view || (!view.cue && !view.speech)) return null
  const key = JSON.stringify(view)
  if (cache.has(key)) return cache.get(key) ?? null
  const parts: string[] = []
  if (view.cue) {
    const o = esc(view.cue.open)
    const c = esc(view.cue.close)
    parts.push(`${o}[A-Za-z][\\w-]{0,39}(?::[^${esc(view.cue.close[0] ?? '}')}\\n]{0,80})?${c}`)
  }
  if (view.speech) parts.push(`${esc(view.speech.open)}\\p{L}[\\p{L}\\p{N}_' -]{0,39}${esc(view.speech.close)}(?!\\()`)
  const re = new RegExp(parts.join('|'), 'gu')
  cache.set(key, re)
  return re
}

/** A text without its inline markup, spaces tidied where a token stood. */
export function stripMarkup(text: string, view: MarkupView | null | undefined): string {
  const re = markupPattern(view)
  if (!re) return text
  return text
    .replace(re, '\u0000')
    .replace(/ ?\u0000(?= |[.,;:!?…)\]»]|$)/g, '')
    .replace(/\u0000 ?/g, '')
}
