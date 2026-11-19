/**
 * A small English fallback: one syllable per vowel group (a silent final "e" dropped, "-le" kept), stress on the first
 * syllable. Rough by design: it only needs the rhythm of speech, not correct hyphenation.
 */
import type { SyllableRules } from './types'

const FUNCTION_WORDS = new Set([
  'a', 'an', 'the', 'of', 'to', 'in', 'on', 'at', 'by', 'for', 'with', 'and', 'or', 'but', 'is', 'are', 'was', 'it', 'its',
  'that', 'this', 'as', 'from', 'be', 'not', 'you', 'we', 'i',
])

export function syllabify(word: string): string[] {
  const w = word.toLowerCase().replace(/[^a-z']/g, '')
  if (!w) return []
  const groups = w.match(/[^aeiouy]*[aeiouy]+(?:[^aeiouy]*$|[^aeiouy](?=[^aeiouy]))?/g) ?? [w]
  // A silent final "e" (make, time) belongs to the syllable before; "-le" (table) is its own.
  if (groups.length > 1) {
    const last = groups[groups.length - 1] as string
    if (/^[^aeiouy]*e$/.test(last) && !/[^aeiouy]le$/.test(w)) {
      groups.pop()
      groups[groups.length - 1] += last
    }
  }
  const joined = groups.join('')
  if (joined.length < w.length) groups[groups.length - 1] += w.slice(joined.length)
  return groups
}

export const english = {
  syllabify,
  stressIndex: (_s: string[]) => 0,
  isFunctionWord: (w: string) => FUNCTION_WORDS.has(w.toLowerCase()),
} satisfies SyllableRules
