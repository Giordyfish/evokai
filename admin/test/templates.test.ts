import { isLegacy, isLegacyPrompt, withSyntax } from '../src/templates'

describe('template syntax', () => {
  it('marks the versions saved before Handlebars as legacy (the badge)', () => {
    expect(isLegacy({})).toBe(true)
    expect(isLegacy({ template_syntax: 'legacy' })).toBe(true)
    expect(isLegacy({ template_syntax: 'handlebars' })).toBe(false)
    expect(isLegacy(null)).toBe(false)
    expect(isLegacyPrompt({ templateSyntax: 'legacy' })).toBe(true)
    expect(isLegacyPrompt({ templateSyntax: 'handlebars' })).toBe(false)
    expect(isLegacyPrompt(undefined)).toBe(false)
  })

  it('keeps an old agent legacy only while its texts are unchanged', () => {
    type Def = { instructions?: string; prompt?: unknown; confirm_labels?: unknown; template_syntax?: string; model?: string }
    const base: Def = { instructions: 'Today is {date}.', prompt: null, confirm_labels: { a: 'Run {x}' } }
    expect(withSyntax({ ...base, model: 'other' }, base, false).template_syntax).toBe('legacy')
    expect(withSyntax({ ...base, instructions: 'Today is {{date}}.' }, base, false).template_syntax).toBe('handlebars')
    expect(withSyntax({ ...base, confirm_labels: { a: 'Run {{x}}' } }, base, false).template_syntax).toBe('handlebars')
    const current: Def = { ...base, template_syntax: 'handlebars' }
    expect(withSyntax(current, current, false).template_syntax).toBe('handlebars')
    expect(withSyntax<Def>({ instructions: 'x' }, { instructions: 'x' }, true).template_syntax).toBe('handlebars')
  })
})
