/**
 * Estimating how speech moves from its text, for when the browser speaks with `speechSynthesis` and gives no audio
 * to measure. Syllables are found by rule, stressed syllables are stronger, function words softer, sentence ends
 * fade, commas and full stops pause; the estimate re-aligns on every word boundary the browser reports.
 *
 * Languages: Italian (`it`, full rules) and English (`en`, a rough fallback). Any other language gives no estimate:
 * an empty timeline, and the driver publishes nothing.
 */
import type { LocaleCode, LocalePack } from '../../../locales'
import { now as clock, type AudioSignal } from '../signals'
import { english } from './en'
import { italian } from './it'
import type { SyllableRules } from './types'

export type { SyllableRules } from './types'
export { syllabify as syllabifyItalian, stressIndex as stressIndexItalian } from './it'
export { syllabify as syllabifyEnglish } from './en'

const RULES: Record<string, SyllableRules> = { it: italian, en: english }

/** The syllable rules for a locale, or null when the language has none (no estimate). */
export function syllableRules(locale: LocaleCode | LocalePack | (string & {}) = 'en'): SyllableRules | null {
  const code = (typeof locale === 'object' ? locale.code : locale).toLowerCase().split('-')[0] ?? ''
  return RULES[code] ?? null
}

/** Syllables of one word with the locale's rules: `syllabify("settembre", "it")` → ["set", "tem", "bre"]. */
export function syllabify(word: string, locale: LocaleCode | LocalePack | (string & {}) = 'en'): string[] {
  return syllableRules(locale)?.syllabify(word) ?? []
}

/** The stressed syllable of a word already split. */
export function stressIndex(syllables: string[], locale: LocaleCode | LocalePack | (string & {}) = 'en'): number {
  return syllableRules(locale)?.stressIndex(syllables) ?? 0
}

export interface SyllableEvent {
  /** Seconds from the start of the text. */
  at: number
  /** 0–1: the stressed syllable is strongest, function words softer, sentence ends fade. */
  intensity: number
  /** Index of the word in the text. */
  word: number
  /** Character index of the word in the text (to re-sync on `boundary`). */
  char: number
}

export interface SyllableTimeline {
  events: SyllableEvent[]
  /** Seconds, pauses at commas and full stops included. */
  duration: number
}

export interface TimelineOptions {
  /** Start at this character. */
  from?: number
  /** Speech rate (1 = normal). */
  rate?: number
  locale?: LocaleCode | LocalePack | (string & {})
  /** Random source for the small natural jitter (default Math.random; pass `() => 0.5` for none). */
  random?: () => number
}

/** One syllable lasts this long at rate 1 (long syllables 15% more). */
export const SYLLABLE_SECONDS = 0.165

/** The syllable timeline of `text`. */
export function timeline(text: string, opts: TimelineOptions = {}): SyllableTimeline {
  const rules = syllableRules(opts.locale)
  if (!rules) return { events: [], duration: 0 }
  const rate = opts.rate ?? 1
  const random = opts.random ?? Math.random
  const syl = SYLLABLE_SECONDS / rate
  const events: SyllableEvent[] = []
  let t = 0
  const re = /([^\s.,;:!?]+)([.,;:!?]*)/g
  re.lastIndex = opts.from ?? 0
  const words: Array<{ w: string; p: string; at: number }> = []
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) words.push({ w: m[1] as string, p: m[2] as string, at: m.index })
  words.forEach((wd, wi) => {
    const sy = rules.syllabify(wd.w)
    if (!sy.length) return
    const st = rules.stressIndex(sy)
    const func = rules.isFunctionWord(wd.w)
    const endOfSentence = /[.!?]/.test(wd.p) || wi === words.length - 1
    sy.forEach((s, k) => {
      let i = k === st ? 1 : 0.55
      if (func) i *= 0.6
      if (endOfSentence) i *= 0.75 + 0.25 * (1 - k / sy.length)
      events.push({ at: t, intensity: Math.min(1, i * (0.85 + random() * 0.3)), word: wi, char: wd.at })
      t += syl * (s.length > 3 ? 1.15 : 1)
    })
    t += /[.!?]/.test(wd.p) ? 0.42 / rate : /[,;:]/.test(wd.p) ? 0.22 / rate : 0.03
  })
  return { events, duration: t }
}

export interface SyllableDriver {
  /** Re-align on a word boundary reported by the speech engine (`charIndex` into the text). */
  resync(charIndex: number): void
  stop(): void
}

export interface DriverOptions extends Omit<TimelineOptions, 'from'> {
  /** Where the estimated `agent` signals go. */
  emit: (signal: AudioSignal) => void
  /** Clock in milliseconds (default `performance.now`). */
  now?: () => number
  /** Frame scheduler (default `requestAnimationFrame`). */
  raf?: (cb: () => void) => unknown
}

/** Per-frame decay of the estimated level between syllables. */
export const DRIVER_DECAY = 0.93

/**
 * Publish estimated `agent` signals for `text` while it is spoken: each syllable is an onset with its intensity, and
 * the level jumps to 0.12 + 0.7 × intensity then decays. Starts now; call `resync` on boundary events.
 */
export function syllableDriver(text: string, opts: DriverOptions): SyllableDriver {
  const tl = timeline(text, opts).events
  const now = opts.now ?? clock
  const raf: (cb: () => void) => unknown =
    opts.raf ?? (typeof requestAnimationFrame === 'function' ? requestAnimationFrame : (cb) => setTimeout(cb, 16))
  let base = now()
  let idx = 0
  let level = 0
  let alive = tl.length > 0
  const tick = () => {
    if (!alive) return
    const elapsed = (now() - base) / 1000
    let onset = false
    let intensity = 0
    while (idx < tl.length && (tl[idx] as SyllableEvent).at <= elapsed) {
      intensity = (tl[idx++] as SyllableEvent).intensity
      onset = true
      level = 0.12 + 0.7 * intensity
    }
    opts.emit({ source: 'agent', rms: level, level, onset, intensity: onset ? intensity : 0, spectrum: null, estimated: true, time: now() })
    level *= DRIVER_DECAY
    raf(tick)
  }
  if (alive) raf(tick)
  return {
    resync(charIndex: number) {
      const k = tl.findIndex((e) => e.char >= charIndex)
      if (k >= 0) {
        base = now() - (tl[k] as SyllableEvent).at * 1000
        idx = k
      }
    },
    stop() {
      if (!alive && tl.length === 0) return
      alive = false
      opts.emit({ source: 'agent', rms: 0, level: 0, onset: false, intensity: 0, spectrum: null, estimated: true, time: now() })
    },
  }
}
