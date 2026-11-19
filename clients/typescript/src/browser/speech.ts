/**
 * What gets spoken and how spoken replies are read: a port of the server's rules
 * (src/evokai/audio/speech/speakable.py and replies.py), with the word lists from a locale pack, so speech and spoken
 * answers mean the same whether the browser or the server does the work. The server's Python is the reference.
 * `redact` knows the built-in formats only: an application's own identifiers (`application.identifiers`) are said
 * the way they ask by the server, in the `say` events every voice engine speaks.
 */
import { localePack, type LocaleCode, type LocalePack } from '../locales'

export type LocaleInput = LocaleCode | LocalePack | (string & {}) | undefined

/** Identifier patterns, replaced by the locale's words (the identifiers themselves are language-independent). */
const IDENTIFIERS: Array<[RegExp, keyof LocalePack['speech']['redactions']]> = [
  [/\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b/gi, 'taxCode'],
  [/\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b/gi, 'iban'],
  [/\b\d{11}\b/g, 'vat'],
  [/[\w.+-]+@[\w-]+\.[\w.-]+/g, 'email'],
  [/(?:\+39\s?)?\b3\d{2}[\s.]?\d{6,7}\b/g, 'phone'],
]

const HAS_WORD = /[\p{L}\p{N}]/u
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

interface Compiled {
  pack: LocalePack
  sources: RegExp
  abbr: RegExp
  yes: RegExp
  no: RegExp
  ordinals: Array<[RegExp, number]>
}
const cache = new WeakMap<LocalePack, Compiled>()

function compiled(locale: LocaleInput): Compiled {
  const pack = localePack(locale)
  let c = cache.get(pack)
  if (!c) {
    const s = pack.speech
    const words = (list: string[]) => list.map((w) => escape(norm(w))).join('|')
    c = {
      pack,
      sources: new RegExp(`^\\s*(?:#+\\s*)?(?:\\*\\*)?(${s.sources.map(escape).join('|')})(?:\\*\\*)?\\s*:?`, 'i'),
      abbr: new RegExp(`(?:\\b(?:${s.abbreviations.map(escape).join('|')})\\.)$`, 'i'),
      yes: new RegExp(`^(${words(s.yes)})\\b`),
      no: new RegExp(`^(${words(s.no)})\\b`),
      ordinals: Object.entries(s.ordinals).map(([w, n]) => [new RegExp(`\\b${escape(norm(w))}\\b`), n]),
    }
    cache.set(pack, c)
  }
  return c
}

/** Replace personal identifiers (tax codes, IBANs, VAT numbers, e-mails, phones) with neutral words. */
export function redact(text: string, locale?: LocaleInput): string {
  const r = localePack(locale).speech.redactions
  let s = text
  for (const [re, key] of IDENTIFIERS) s = s.replace(re, r[key])
  return s
}

/** Markdown to plain speakable text: no code, tables, images, link targets, URLs, heading/list marks or emphasis. */
export function speakable(markdown: string, locale?: LocaleInput): string {
  const s = markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .split('\n')
    .filter((l) => !/^\s*\|/.test(l))
    .join('\n')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*[-*•]\s+/gm, '')
    .replace(/^\s*\d+[.)]\s+/gm, '')
    .replace(/(\*\*|__|\*|_)(.+?)\1/g, '$2')
    .replace(/[→←↑↓]/g, ' ')
    .replace(/https?:\/\/\S+/g, ' ')
  return redact(s, locale).replace(/[ \t]+/g, ' ').trim()
}

/**
 * Incremental sentence splitter over streamed Markdown. `push` returns the segments completed so far; `flush` the
 * rest. A sentence ends at . ! ? … followed by white space, or at a blank line / list break, never inside numbers
 * (4.572, 1,2) nor after one of the locale's abbreviations. With `firstClauseMinChars`, the very first segment is
 * released at the first , ; or : once it is that long. A sources section is never read aloud.
 */
export class SentenceSplitter {
  private buf = ''
  private emitted = 0
  private readonly rules: Compiled
  /** Set at a sources section: sources are shown, never read. */
  muted = false

  constructor(
    private readonly firstClauseMinChars = 0,
    locale?: LocaleInput,
  ) {
    this.rules = compiled(locale)
  }

  push(delta: string): string[] {
    this.buf += delta
    return this.take(false)
  }

  flush(): string[] {
    return this.take(true)
  }

  reset(): void {
    this.buf = ''
    this.emitted = 0
    this.muted = false
  }

  private take(final: boolean): string[] {
    const { pack, sources, abbr } = this.rules
    const out: string[] = []
    const buf = this.buf
    let start = 0
    for (let i = 0; i < buf.length; i++) {
      const ch = buf[i] as string
      const nxt = buf[i + 1]
      const blank = ch === '\n' && nxt !== undefined && ('\n-*#|'.includes(nxt) || /\d/.test(nxt))
      const end = '.!?…'.includes(ch) && (nxt === undefined ? final : /\s/.test(nxt))
      const clause =
        !blank &&
        !end &&
        this.firstClauseMinChars > 0 &&
        this.emitted + out.length === 0 &&
        ',;:'.includes(ch) &&
        nxt !== undefined &&
        /\s/.test(nxt) &&
        buf.slice(start, i + 1).trim().length >= this.firstClauseMinChars
      if (!(blank || end || clause)) continue
      const piece = buf.slice(start, i + 1)
      if (end && ch === '.' && abbr.test(piece)) continue
      const text = speakable(piece, pack)
      if (sources.test(piece.trim())) this.muted = true
      if (HAS_WORD.test(text) && !this.muted) out.push(text)
      start = i + 1
    }
    this.buf = buf.slice(start)
    if (final) {
      const text = speakable(this.buf, pack)
      if (sources.test(this.buf.trim())) this.muted = true
      if (HAS_WORD.test(text) && !this.muted) out.push(text)
      this.buf = ''
    }
    this.emitted += out.length
    return out
  }
}

// ── spoken replies ──────────────────────────────────────────────────────────────────────────────────────────────

/** Lower case, no accents, letters and digits only, single spaces. */
export function norm(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/\p{Mn}/gu, '')
    .replace(/[^\p{L}\p{N} ]|_/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** A spoken yes (true) or no (false) to a confirmation; null when it is neither. */
export function spokenDecision(said: string, locale?: LocaleInput): boolean | null {
  const { yes, no } = compiled(locale)
  const s = norm(said)
  if (yes.test(s)) return true
  if (no.test(s)) return false
  return null
}

/** Map a spoken reply to an option: by number or ordinal ("the second", "2") or by its words. */
export function spokenOption(said: string, options: string[], locale?: LocaleInput): string | null {
  const { ordinals } = compiled(locale)
  const s = norm(said)
  const digit = /\b([1-5])\b/.exec(s)
  // The ordinal said first wins: "the second one" is 2 (as on the server).
  let num: number | null = digit ? Number(digit[1]) : null
  if (num === null) {
    let at = Infinity
    for (const [re, n] of ordinals) {
      const m = re.exec(s)
      if (m && (m.index < at || (m.index === at && num !== null && n < num))) {
        at = m.index
        num = n
      }
    }
  }
  if (num && num > 0 && num <= options.length) return options[num - 1] ?? null
  let best: string | null = null
  let bestScore = 0
  for (const o of options) {
    const words = norm(o).split(' ').filter((w) => w.length > 2)
    const score = words.length ? words.filter((w) => s.includes(w)).length / words.length : 0
    if (score > bestScore) {
      best = o
      bestScore = score
    }
  }
  return bestScore >= 0.5 ? best : null
}

/** Whether what the microphone heard is mostly the agent's own voice (language-independent). */
export function isEcho(heard: string, spoken: string): boolean {
  const h = norm(heard).split(' ').filter(Boolean)
  if (!h.length) return true
  const said = new Set(norm(spoken).split(' '))
  return h.filter((w) => said.has(w)).length / h.length > 0.6
}

/**
 * After the agent has finished speaking, whether a late transcript is still its voice: only a run of at least three
 * of its words, in the same order. A short reply that reuses its words («ordini aperti», «sì, i resi») is the user's.
 */
export function isTrailingEcho(heard: string, spoken: string): boolean {
  const h = norm(heard).split(' ').filter(Boolean)
  if (h.length < 3) return false
  return ` ${norm(spoken)} `.includes(` ${h.join(' ')} `)
}

/** Hesitations that are not words a person means (per language). */
export const FILLER_WORDS: Record<string, string[]> = {
  en: ['uh', 'um', 'er', 'erm', 'hm', 'hmm', 'mm', 'ah', 'oh', 'eh', 'ok', 'okay', 'so', 'well', 'like', 'yes', 'no'],
  it: ['eh', 'ehm', 'em', 'mm', 'mmm', 'uhm', 'ah', 'oh', 'beh', 'mah', 'ok', 'okay', 'si', 'no', 'allora', 'cioe', 'tipo'],
}

/** The words that carry meaning: no hesitations, no single letters. */
export function meaningfulWords(heard: string, locale?: LocaleInput): string[] {
  const code = localePack(locale).code
  const fillers = new Set((FILLER_WORDS[code] ?? FILLER_WORDS.en ?? []).map(norm))
  return norm(heard)
    .split(' ')
    .filter((w) => w.length > 1 && !fillers.has(w))
}

/**
 * What the user said on top of the agent's voice: recognition often hears the agent first and the user after, in one
 * transcript. Leading words that are the agent's (exact, or the same first five letters) are dropped; the rest is
 * the user's. Numbers are skipped too: they are spoken in words and heard in digits.
 */
export function stripEcho(heard: string, spoken: string): string {
  const said = norm(spoken).split(' ').filter(Boolean)
  const exact = new Set(said)
  const stems = new Set(said.filter((w) => w.length >= 5).map((w) => w.slice(0, 5)))
  const words = heard.trim().split(/\s+/).filter(Boolean)
  let i = 0
  for (; i < words.length; i++) {
    const w = norm(words[i] as string)
    const agents = !w || /^\d+$/.test(w) || exact.has(w) || (w.length >= 5 && stems.has(w.slice(0, 5)))
    if (!agents) break
  }
  return words.slice(i).join(' ')
}

/** The last few words of a transcript: barge-in looks at what was just heard, not the whole phrase. */
export const tailWords = (text: string, n = 4): string => text.trim().split(/\s+/).slice(-n).join(' ')
