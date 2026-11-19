import { wordDiff } from '../src/diff'

describe('wordDiff', () => {
  it('marks what the new answer added and removed', () => {
    const parts = wordDiff('October has 24 differences.', 'October has 26 open differences.')
    expect(parts[0]).toEqual({ text: 'October has ', kind: 'same' })
    expect(parts.filter((p) => p.kind === 'del').map((p) => p.text.trim())).toEqual(['24'])
    expect(parts.filter((p) => p.kind === 'add').map((p) => p.text.trim()).join(' ')).toBe('26 open')
    const joined = (k: 'same' | 'add' | 'del') =>
      parts
        .filter((p) => p.kind !== k)
        .map((p) => p.text)
        .join('')
    expect(joined('add')).toBe('October has 24 differences.')
    expect(joined('del')).toBe('October has 26 open differences.')
  })

  it('handles equal, empty and very long texts', () => {
    expect(wordDiff('same', 'same')).toEqual([{ text: 'same', kind: 'same' }])
    expect(wordDiff('', '')).toEqual([])
    expect(wordDiff('', 'new')).toEqual([{ text: 'new', kind: 'add' }])
    const long = 'w '.repeat(2000)
    expect(wordDiff(long, `${long}x`)).toHaveLength(2)
  })
})
