import { setLocale } from '../src/i18n'
import { type Fit, fitBadge, fitReasons } from '../src/fit'

afterEach(() => setLocale('en'))

const facts = { contextTokens: 200000, needContext: 128000, tau2Pass1: 85.3, tau2AsOf: '2026-05-05', needTau2: 75 }
const est = (verdict: Fit['verdict'], reasons: string[], basis: Fit['basis'] = 'estimate'): Fit => ({ verdict, reasons, basis, facts })

describe('the agent editor shows the estimate', () => {
  it('never shows an estimate as a measurement', () => {
    expect(fitBadge(est('recommended', [])).text).toBe('Recommended (estimate)')
    expect(fitBadge(est('recommended', [], 'measured')).text).toBe('Recommended (measured)')
    expect(fitBadge(est('not_recommended', [])).text).toBe('Not recommended (estimate)')
    expect(fitBadge(est('to_verify', [])).text).toBe('To verify')
    expect(fitBadge(est('to_verify', [])).tone).toBe('')
  })
  it('speaks Italian', () => {
    setLocale('it')
    expect(fitBadge(est('recommended', [])).text).toBe('Consigliato (stima)')
    expect(fitBadge(est('not_recommended', [])).text).toBe('Non consigliato (stima)')
    expect(fitBadge(est('to_verify', [])).text).toBe('Da verificare')
    expect(fitBadge(est('recommended', [], 'measured')).text).toBe('Consigliato (misurato)')
  })
  it('gives its reasons with their figures and no amount', () => {
    const lines = fitReasons(est('recommended', ['context_ok', 'tau2_ok', 'tools_unknown']))
    expect(lines[0]).toBe('context window 200k tokens (needs 128k)')
    expect(lines[1]).toContain('85.3%')
    expect(lines[1]).toContain('75%')
    expect(lines[2]).toBe('tool calling not confirmed by a source')
    expect(lines.join(' ')).not.toMatch(/[$€]|USD|cost/i)
    expect(fitReasons(est('to_verify', ['a_new_code']))).toEqual(['a_new_code'])
  })
})
