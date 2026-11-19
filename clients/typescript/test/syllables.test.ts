import type { AudioSignal } from '../src/browser/audio/signals'
import { DRIVER_DECAY, SYLLABLE_SECONDS, stressIndex, syllabify, syllableDriver, syllableRules, timeline } from '../src/browser/audio/syllables'

const flat = () => 0.5 // no jitter: 0.85 + 0.5 × 0.3 = 1

describe('Italian syllables', () => {
  test.each([
    ['settembre', ['set', 'tem', 'bre']],
    ['mostra', ['mo', 'stra']],
    ['passa', ['pas', 'sa']],
    ['rosso', ['ros', 'so']],
    ['agente', ['a', 'gen', 'te']],
    ['quattro', ['quat', 'tro']],
    ['ragno', ['ra', 'gno']],
    ['figlio', ['fi', 'glio']],
    ['albero', ['al', 'be', 'ro']],
    ['città', ['cit', 'tà']],
    ['gli', ['gli']],
    ['per', ['per']],
    ['Aprile,', ['a', 'pri', 'le']],
  ])('%s → %j', (word, expected) => {
    expect(syllabify(word, 'it')).toEqual(expected)
  })

  test('stress: the accented vowel, else the second-to-last syllable', () => {
    expect(stressIndex(['cit', 'tà'], 'it')).toBe(1)
    expect(stressIndex(['set', 'tem', 'bre'], 'it')).toBe(1)
    expect(stressIndex(['per'], 'it')).toBe(0)
    expect(stressIndex(['per', 'ché'], 'it')).toBe(1)
  })
})

describe('English fallback and other languages', () => {
  test('rough English syllables, stress on the first', () => {
    expect(syllabify('september')).toHaveLength(3)
    expect(syllabify('time')).toEqual(['time'])
    expect(syllabify('table')).toHaveLength(2)
    expect(syllabify('agency').join('')).toBe('agency')
    expect(stressIndex(['sep', 'tem', 'ber'])).toBe(0)
  })

  test('a language without rules gives no estimate', () => {
    expect(syllableRules('fr')).toBeNull()
    expect(timeline('Bonjour à tous.', { locale: 'fr' })).toEqual({ events: [], duration: 0 })
    const emitted: AudioSignal[] = []
    const d = syllableDriver('Bonjour.', { locale: 'fr', emit: (s) => emitted.push(s), raf: () => undefined })
    d.stop()
    expect(emitted).toEqual([])
  })
})

describe('timeline', () => {
  test('timing follows the rate; full stops and commas pause; long syllables last longer', () => {
    const t = timeline('Ciao, mondo.', { locale: 'it', random: flat })
    // cia-o | mon-do, with a comma pause (0.22 s) between the words.
    const s = SYLLABLE_SECONDS
    expect(t.events.map((e) => +e.at.toFixed(4))).toEqual([0, s, 2 * s + 0.22, 3 * s + 0.22].map((x) => +x.toFixed(4)))
    const fast = timeline('Ciao, mondo.', { locale: 'it', random: flat, rate: 2 })
    expect(fast.duration).toBeCloseTo(t.duration / 2, 5)
  })

  test('the stressed syllable is strongest, function words softer, sentence ends fade', () => {
    const t = timeline('la settimana passa', { locale: 'it', random: flat })
    const [la, set, ti, ma, na, pas, sa] = t.events.map((e) => +e.intensity.toFixed(3))
    expect(la).toBeCloseTo(0.6, 3) // a one-syllable function word: stressed (1) × 0.6
    void [set, ti, na, pas, sa]
    expect(ma).toBe(Math.max(...[set, ti, ma, na] as number[]))
    // «passa» ends the text: its later syllables fade.
    expect(sa).toBeLessThan(0.55)
  })
})

describe('syllable driver', () => {
  function rig(text: string) {
    let ms = 0
    const frames: Array<() => void> = []
    const out: AudioSignal[] = []
    const d = syllableDriver(text, { locale: 'it', random: flat, emit: (s) => out.push(s), now: () => ms, raf: (cb) => frames.push(cb) })
    const step = (to: number) => {
      ms = to
      frames.shift()?.()
    }
    return { d, out, step }
  }

  test('syllables fire on time as onsets; the level jumps then decays', () => {
    const { out, step } = rig('settembre')
    step(0)
    expect(out[0]).toMatchObject({ source: 'agent', onset: true, estimated: true, spectrum: null })
    const first = out[0]!.level
    step(50)
    expect(out[1]!.onset).toBe(false)
    expect(out[1]!.level).toBeCloseTo(first * DRIVER_DECAY, 6)
    step(SYLLABLE_SECONDS * 1000 + 1)
    expect(out[2]!.onset).toBe(true)
  })

  test('a word boundary re-syncs the estimate to that word', () => {
    const { d, out, step } = rig('Uno due tre quattro')
    step(0)
    // The browser reports word «quattro» (char 12) early: its syllable fires on the next frame.
    d.resync(12)
    step(1)
    expect(out.at(-1)!.onset).toBe(true)
    d.stop()
    expect(out.at(-1)).toMatchObject({ level: 0, onset: false })
  })
})
