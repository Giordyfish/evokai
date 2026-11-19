/** A language's syllable rules, used to estimate how speech moves when there is no audio to measure. */
export interface SyllableRules {
  /** Split one word into syllables (approximate but stable). */
  syllabify(word: string): string[]
  /** Index of the stressed syllable. */
  stressIndex(syllables: string[]): number
  /** Function words (articles, prepositions…): spoken softer. */
  isFunctionWord(word: string): boolean
}
