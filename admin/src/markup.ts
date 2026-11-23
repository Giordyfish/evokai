/**
 * The application's inline markup (cues and speech tags) as the Limits page edits it: a flat form, and the patch it
 * becomes. Names are typed as a comma-separated list; an empty list means every name.
 */
import type { TextKey } from './i18n'

export interface MarkupKind {
  open: string
  close: string
  names?: string[] | null
  enabled?: boolean
}
export interface CueKind extends MarkupKind {
  names_from?: { module: string; param: string } | null
}
export interface MarkupSettings {
  cue?: CueKind | null
  speech?: MarkupKind | null
}

export interface MarkupForm {
  cueOn: boolean
  cueOpen: string
  cueClose: string
  cueNames: string
  cueModule: string
  cueParam: string
  speechOn: boolean
  speechOpen: string
  speechClose: string
  speechNames: string
}

/** The form for what is stored (defaults where nothing is). */
export function markupForm(m: MarkupSettings | null | undefined): MarkupForm {
  const cue = m?.cue
  const speech = m?.speech
  return {
    cueOn: !!cue && cue.enabled !== false,
    cueOpen: cue?.open ?? '{',
    cueClose: cue?.close ?? '}',
    cueNames: (cue?.names ?? []).join(', '),
    cueModule: cue?.names_from?.module ?? '',
    cueParam: cue?.names_from?.param ?? '',
    speechOn: !!speech && speech.enabled !== false,
    speechOpen: speech?.open ?? '[',
    speechClose: speech?.close ?? ']',
    speechNames: (speech?.names ?? []).join(', '),
  }
}

const DELIMITER = /^[!-/:-@[-`{-~]{1,3}$/
const CUE_NAME = /^[A-Za-z][\w-]{0,39}$/
const TAG = /^[\p{L}][\p{L}\p{N}' -]{0,39}$/u

const list = (s: string): string[] =>
  s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)

export type MarkupPatch = { ok: true; markup: MarkupSettings | null } | { ok: false; error: TextKey }

/** The `markup` of an application patch, or the reason the form is refused. */
export function markupPatch(f: MarkupForm): MarkupPatch {
  const out: MarkupSettings = {}
  if (f.cueOn) {
    if (!DELIMITER.test(f.cueOpen) || !DELIMITER.test(f.cueClose)) return { ok: false, error: 'limits.markupDelimiter' }
    const names = list(f.cueNames)
    if (names.some((n) => !CUE_NAME.test(n))) return { ok: false, error: 'limits.markupCueName' }
    const module = f.cueModule.trim()
    const param = f.cueParam.trim()
    if (!!module !== !!param) return { ok: false, error: 'limits.markupModule' }
    out.cue = { open: f.cueOpen, close: f.cueClose, names: names.length ? names : null, names_from: module ? { module, param } : null }
  }
  if (f.speechOn) {
    if (!DELIMITER.test(f.speechOpen) || !DELIMITER.test(f.speechClose)) return { ok: false, error: 'limits.markupDelimiter' }
    if (f.cueOn && f.speechOpen === f.cueOpen) return { ok: false, error: 'limits.markupSame' }
    const names = list(f.speechNames)
    if (names.some((n) => !TAG.test(n))) return { ok: false, error: 'limits.markupTag' }
    out.speech = { open: f.speechOpen, close: f.speechClose, names: names.length ? names : null }
  }
  return { ok: true, markup: out.cue || out.speech ? out : null }
}
