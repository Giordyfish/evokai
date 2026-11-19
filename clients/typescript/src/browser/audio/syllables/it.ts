/**
 * Italian syllables by rule (no model): a vowel nucleus (diphthongs with unstressed i/u stay together), one consonant
 * goes with the next syllable, doubled consonants split, "s" + consonant and the clusters below stay together, other
 * clusters split.
 * Stress: the syllable with an accented vowel, else the second-to-last (the common "parola piana").
 */
import type { SyllableRules } from './types'

const VOWELS = 'aeiouàèéìíòóùú'
const ACCENTED = 'àèéìíòóùú'
const isVowel = (c: string | undefined) => c !== undefined && VOWELS.includes(c)
/** Consonant pairs that open a syllable together. */
const KEEP = ['bl', 'br', 'cl', 'cr', 'dr', 'fl', 'fr', 'gl', 'gr', 'pl', 'pr', 'tr', 'vr', 'ch', 'gh', 'gn', 'sc', 'qu', 'gu']
const FUNCTION_WORDS = new Set([
  'il', 'lo', 'la', 'i', 'gli', 'le', 'un', 'uno', 'una', 'di', 'a', 'da', 'in', 'con', 'su', 'per', 'tra', 'fra', 'e', 'o',
  'ma', 'che', 'non', 'si', 'ci', 'ne', 'del', 'della', 'dei', 'delle', 'al', 'alla', 'ai', 'nel', 'nella', 'è',
])

export function syllabify(word: string): string[] {
  const w = word.toLowerCase().replace(/[^a-zàèéìíòóùú']/g, '')
  if (!w) return []
  const out: string[] = []
  let cur = ''
  let i = 0
  while (i < w.length) {
    cur += w[i]
    if (isVowel(w[i])) {
      // Diphthongs: an unstressed i/u next to another vowel stays in the nucleus.
      while (i + 1 < w.length && isVowel(w[i + 1]) && ('iu'.includes(w[i] as string) || 'iu'.includes(w[i + 1] as string))) cur += w[++i]
      let j = i + 1
      let cons = ''
      while (j < w.length && !isVowel(w[j])) cons += w[j++]
      if (j >= w.length) {
        out.push(cur + cons)
        cur = ''
        break
      }
      // A doubled consonant always splits (pas-sa); "s" + consonant and the pairs in KEEP open the next syllable.
      const doubled = cons.length >= 2 && cons[0] === cons[1]
      const together = !doubled && (cons.length <= 1 || cons[0] === 's' || (cons.length === 2 && KEEP.includes(cons)))
      if (together) {
        out.push(cur)
        cur = ''
      } else {
        // The first consonant closes this syllable.
        out.push(cur + cons[0])
        cur = ''
        i++
      }
    }
    i++
  }
  if (cur) {
    if (out.length) out[out.length - 1] += cur
    else out.push(cur)
  }
  return out
}

export function stressIndex(syllables: string[]): number {
  const k = syllables.findIndex((s) => [...s].some((c) => ACCENTED.includes(c)))
  return k >= 0 ? k : Math.max(0, syllables.length - 2)
}

export const italian: SyllableRules = {
  syllabify,
  stressIndex,
  isFunctionWord: (w) => FUNCTION_WORDS.has(w.toLowerCase()),
}
