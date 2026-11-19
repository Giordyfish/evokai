import { slugOf } from '../src/format'

describe('slugOf', () => {
  it('makes a slug the service accepts from any name', () => {
    expect(slugOf('Acme Support')).toBe('acme-support')
    expect(slugOf('  Città & Più!  ')).toBe('citta-piu')
    expect(slugOf('x'.repeat(60))).toHaveLength(40)
    expect(slugOf('---')).toBe('')
  })
})
