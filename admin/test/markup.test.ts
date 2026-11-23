import { describe, expect, test } from 'vitest'
import { markupForm, markupPatch } from '../src/markup'

describe('markup settings', () => {
  test('nothing stored: both kinds off with the default delimiters', () => {
    const f = markupForm(null)
    expect(f).toMatchObject({ cueOn: false, cueOpen: '{', cueClose: '}', speechOn: false, speechOpen: '[', speechClose: ']' })
    expect(markupPatch(f)).toEqual({ ok: true, markup: null })
  })

  test('cues with names and a module parameter, speech tags with every name', () => {
    const f = { ...markupForm(null), cueOn: true, cueNames: 'wave, nod ,', cueModule: 'gestures', cueParam: 'allowed', speechOn: true }
    expect(markupPatch(f)).toEqual({
      ok: true,
      markup: {
        cue: { open: '{', close: '}', names: ['wave', 'nod'], names_from: { module: 'gestures', param: 'allowed' } },
        speech: { open: '[', close: ']', names: null },
      },
    })
  })

  test('round trip of what is stored', () => {
    const stored = { cue: { open: '<<', close: '>>', names: ['wave'], names_from: null }, speech: null }
    const back = markupPatch(markupForm(stored))
    expect(back).toEqual({ ok: true, markup: { cue: { open: '<<', close: '>>', names: ['wave'], names_from: null } } })
  })

  test.each([
    [{ cueOn: true, cueOpen: 'ab' }, 'limits.markupDelimiter'],
    [{ cueOn: true, cueOpen: '{{{{' }, 'limits.markupDelimiter'],
    [{ cueOn: true, cueNames: '9lives' }, 'limits.markupCueName'],
    [{ cueOn: true, cueModule: 'gestures' }, 'limits.markupModule'],
    [{ cueOn: true, speechOn: true, speechOpen: '{' }, 'limits.markupSame'],
    [{ speechOn: true, speechNames: '[laughs]' }, 'limits.markupTag'],
  ])('refused: %j', (change, error) => {
    expect(markupPatch({ ...markupForm(null), ...change })).toEqual({ ok: false, error })
  })
})
