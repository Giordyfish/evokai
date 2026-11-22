import { setLocale } from '../src/i18n'
import { SKIN, targetApp } from '../src/assistant/launcher'
import { ORB_MAX } from '@ui/travel'
import { activateBody, assistantActive, badge, canActivate, type Candidate, needsSecondDisclaimer, requirementLines, trialRunning } from '../src/assistant/choice'

afterEach(() => setLocale('en'))

const measured = { toolCallsPct: 97, confirmPct: 100, redteamPct: 93, contextTokens: 200000, measuredAt: '2026-11-22', evokaiVersion: '0.1.3', source: 'release' as const }
const good: Candidate = { model: 'bedrock:eu.good', label: 'Good', status: 'recommended', recommended: true, measured }
const weak: Candidate = { model: 'bedrock:eu.weak', label: 'Weak', status: 'not_recommended', recommended: false, measured: { ...measured, confirmPct: 80 } }
const unknown: Candidate = { model: 'bedrock:eu.new', label: 'New', status: 'not_measured', recommended: false, measured: null }
const requirements = { toolCallsPct: 95, confirmPct: 100, redteamPct: 90, contextTokens: 128000, streaming: true, tools: true }

describe('the first disclaimer', () => {
  it('lists every requirement with its figure', () => {
    const lines = requirementLines(requirements)
    expect(lines).toHaveLength(5)
    expect(lines.join(' ')).toContain('95%')
    expect(lines.join(' ')).toContain('100%')
    expect(lines.join(' ')).toContain('90%')
    expect(lines.join(' ')).toContain('128k')
  })
  it('speaks Italian', () => {
    setLocale('it')
    expect(requirementLines(requirements)[1]).toContain('100%')
    expect(badge('recommended').text).toBe('Consigliato')
    expect(badge('not_recommended').text).toBe('Non consigliato')
    expect(badge('not_measured').text).toBe('Non misurato')
  })
})

describe('the second disclaimer', () => {
  it('opens only for a model that does not meet every requirement', () => {
    expect(needsSecondDisclaimer(good)).toBe(false)
    expect(needsSecondDisclaimer(weak)).toBe(true)
    expect(needsSecondDisclaimer(unknown)).toBe(true)
  })
  it('must be ticked before a not recommended model is activated', () => {
    expect(canActivate(good, false)).toBe(true)
    expect(canActivate(weak, false)).toBe(false)
    expect(canActivate(unknown, false)).toBe(false)
    expect(canActivate(weak, true)).toBe(true)
    expect(activateBody(weak, true)).toEqual({ model: 'bedrock:eu.weak', acceptNotRecommended: true })
    expect(activateBody(good, false)).toEqual({ model: 'bedrock:eu.good' })
  })
})

describe('a try', () => {
  it('is running until its run ends', () => {
    const base = { available: true, active: false, model: null, requirements, candidates: [] }
    expect(trialRunning(null)).toBe(false)
    expect(trialRunning({ ...base, trial: null })).toBe(false)
    const trial = { runId: 'r', model: 'm', status: 'running', progressPct: 40, budgetPct: 3, measured: null, startedAt: '' }
    expect(trialRunning({ ...base, trial })).toBe(true)
    expect(trialRunning({ ...base, trial: { ...trial, status: 'done' } })).toBe(false)
  })
})

describe('one mascot on screen', () => {
  const base = { requirements, candidates: [], trial: null, model: null }
  it('counts the assistant as there only while it is available and activated', () => {
    expect(assistantActive(null)).toBe(false)
    expect(assistantActive({ ...base, available: false, active: false })).toBe(false)
    expect(assistantActive({ ...base, available: true, active: false })).toBe(false)
    expect(assistantActive({ ...base, available: true, active: true, model: 'bedrock:eu.x' })).toBe(true)
  })
})

describe('the mascot is the launcher', () => {
  const apps = [{ id: 'sys' }, { id: 'shop' }, { id: 'blog' }]
  it('acts on the application in view, else the last one opened, else the first that is not its own', () => {
    expect(targetApp(apps[2]!, 'shop', apps, 'sys')?.id).toBe('blog')
    expect(targetApp(null, 'shop', apps, 'sys')?.id).toBe('shop')
    expect(targetApp(null, 'gone', apps, 'sys')?.id).toBe('shop')
    expect(targetApp(null, null, [{ id: 'sys' }], 'sys')?.id).toBe('sys')
    expect(targetApp(null, null, [], 'sys')).toBeNull()
  })
  it('keeps the launcher and the travelling orb: the skin only sizes the places the mascot rests in', () => {
    expect(SKIN).not.toMatch(/\.dock\{display:none\}|\.orbfly\{display:none\}/)
    expect(SKIN).toContain('.oslot.launch{')
    expect(SKIN).toContain(`.oslot.mid{width:${ORB_MAX}px;height:${ORB_MAX}px}`)
  })
})
