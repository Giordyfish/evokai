import { profileFrom } from '../src/protection'

const empty = {
  geos: [] as string[],
  countries: '',
  eu: false,
  jurisdictions: '',
  retention: 'any' as const,
  voices: false,
  recognition: false,
  vendors: '',
}

test('an empty form is no profile', () => {
  expect(profileFrom(empty)).toBeNull()
})

test('the profile keeps only the rules that are set', () => {
  expect(profileFrom({ ...empty, geos: ['eu'], eu: true })).toEqual({
    residency: { geographies: ['eu'] },
    jurisdiction: ['eu'],
  })
  expect(profileFrom({ ...empty, countries: 'de, fr  it', jurisdictions: 'ch', retention: 'zero_retention' })).toEqual({
    residency: { countries: ['DE', 'FR', 'IT'] },
    jurisdiction: ['CH'],
    retention: 'zero_retention',
  })
})

test('what the browser may send is declared with its vendors', () => {
  expect(profileFrom({ ...empty, voices: true, vendors: 'Google, Apple' })).toEqual({
    browser: { online_voices: true, online_recognition: false, vendors: ['Google', 'Apple'] },
  })
})
