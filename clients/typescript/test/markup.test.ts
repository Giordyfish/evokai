import { stripMarkup } from '../src/core/markup'
import { ingestAll, setup } from './helpers/voice'

const BOTH = { cue: { open: '{', close: '}' }, speech: { open: '[', close: ']' } }

describe('stripMarkup', () => {
  test('cues and speech tags leave the text, spaces tidied', () => {
    expect(stripMarkup('Hi {wave}! [laughs] It is {point:left}there.', BOTH)).toBe('Hi! It is there.')
  })

  test('task lists and accented cue names stay, as on the server', () => {
    expect(stripMarkup('- [x] done {saluté}', BOTH)).toBe('- [x] done {saluté}')
  })

  test('links, citations and JSON stay', () => {
    expect(stripMarkup('See [the guide](https://x.example) [1] and {"a": 1}', BOTH)).toBe('See [the guide](https://x.example) [1] and {"a": 1}')
  })

  test('custom delimiters, and no markup at all', () => {
    expect(stripMarkup('<<wave>>Hi ((whispers)) there', { cue: { open: '<<', close: '>>' }, speech: { open: '((', close: '))' } })).toBe('Hi there')
    expect(stripMarkup('Hi {wave}', null)).toBe('Hi {wave}')
  })
})

describe('a say with only cues', () => {
  test('is not spoken', async () => {
    const s = setup()
    await s.engine.start()
    ingestAll(s.session, [
      { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
      { type: 'say', index: 0, text: 'Done.', turnId: 't1', seq: 2 },
      { type: 'say', index: 1, text: '', marks: [{ name: 'smile', at: 0 }], turnId: 't1', seq: 3 },
    ])
    await vi.waitFor(() => expect(s.tts.spoken).toEqual(['Done.']))
    s.tts.synth.finish()
    await new Promise((r) => setTimeout(r, 20))
    expect(s.tts.spoken).toEqual(['Done.'])
  })
})
