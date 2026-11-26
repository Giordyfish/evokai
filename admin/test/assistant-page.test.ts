import { cleanText, pickTarget } from '../src/assistant/page'

describe('the assistant reads and moves the page', () => {
  it('reads the visible text one block per line, cut at a maximum', () => {
    expect(cleanText('Usage\n\n  Turns \n  11\n\nFirst word (median)\n4.3 s\n')).toBe('Usage\nTurns\n11\nFirst word (median)\n4.3 s')
    expect(cleanText('abcdef', 3)).toBe('abc\n[...]')
  })

  it('moves to the innermost element that shows the text, whatever its case and spaces', () => {
    const texts = ['Usage Turns 11 First word (median) 4.3 s', 'First word (median) 4.3 s', 'First  word (median)', '4.3 s']
    expect(pickTarget(texts, 'first word')).toBe(2)
    expect(pickTarget(texts, '4.3 s')).toBe(3)
    expect(pickTarget(texts, 'p95')).toBe(-1)
    expect(pickTarget(texts, '  ')).toBe(-1)
  })
})
