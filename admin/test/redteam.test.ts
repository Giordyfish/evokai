import type { RedTeamResult, RedTeamRuns } from '@client/core/types'

import { setLocale } from '../src/i18n'
import {
  byOutcome,
  CATEGORIES,
  categoryLabel,
  categoryRows,
  comparisonText,
  gateWarning,
  graderLabel,
  languageName,
  needsForce,
  pointsText,
  scoreText,
  scoreTone,
  trendGeometry,
} from '../src/redteam'

afterEach(() => setLocale('en'))

const result = (id: string, outcome: RedTeamResult['outcome'], severity: RedTeamResult['severity'], category: RedTeamResult['category']): RedTeamResult => ({
  id,
  outcome,
  severity,
  category,
  language: 'en',
  text: id,
  vector: 'message',
  injected: null,
  graders: [],
  checks: [],
  answer: '',
  tools: [],
  reason: '',
  error: null,
  latencyMs: 0,
})

const gate = (mode: RedTeamRuns['gate']['mode'], minScore = 80): RedTeamRuns['gate'] => ({ mode, minScore, onSave: true, judge: false })
const version = (v: string, active: boolean, score: number | null) => ({ version: v, active, publishState: null, createdAt: '2026-11-01T10:00:00Z', score })

test('every category and grader has a label in both languages, unknown ids stay as sent', () => {
  for (const c of CATEGORIES) expect(categoryLabel(c)).not.toBe(c)
  expect(categoryLabel('prompt_extraction')).toBe('System prompt extraction')
  expect(categoryLabel('time_travel')).toBe('time_travel')
  expect(graderLabel('no_mutation')).toBe('No change without confirmation')
  expect(graderLabel('new_rule')).toBe('new_rule')
  setLocale('it')
  expect(categoryLabel('exfiltration')).toBe('Esfiltrazione di dati')
  expect(graderLabel('judge')).toBe('Modello giudice')
})

test('scores and their changes read as points', () => {
  expect(scoreText(86.54)).toBe('86.5')
  expect(scoreText(90)).toBe('90')
  expect(scoreText(null)).toBe('-')
  expect(pointsText(4.5)).toBe('+4.5 pts')
  expect(pointsText(-2)).toBe('-2 pts')
  expect(pointsText(0)).toBe('0 pts')
  setLocale('it')
  expect(scoreText(86.54)).toBe('86,5')
  expect(pointsText(-1.26)).toBe('-1,3 pt')
})

test('a score reads against the minimum: at it, close below, well below', () => {
  expect(scoreTone(80, 80)).toBe('ok')
  expect(scoreTone(72, 80)).toBe('warn')
  expect(scoreTone(69.9, 80)).toBe('bad')
  expect(scoreTone(null, 80)).toBe('')
})

test('failures come first, the most severe first, then by category', () => {
  const g = byOutcome([
    result('a', 'passed', 'high', 'jailbreak'),
    result('b', 'failed', 'low', 'direct_injection'),
    result('c', 'failed', 'high', 'jailbreak'),
    result('d', 'skipped', 'medium', 'obfuscation'),
    result('e', 'failed', 'high', 'direct_injection'),
  ])
  expect(g.failed.map((r) => r.id)).toEqual(['e', 'c', 'b'])
  expect(g.passed.map((r) => r.id)).toEqual(['a'])
  expect(g.skipped.map((r) => r.id)).toEqual(['d'])
})

test('categories are listed in the suite order with the share held off', () => {
  const rows = categoryRows({
    zz_new: { score: null, passed: 0, failed: 0, total: 0 },
    jailbreak: { score: 50, passed: 1, failed: 1, total: 2 },
    direct_injection: { score: 100, passed: 3, failed: 0, total: 3 },
  })
  expect(rows.map((r) => r.id)).toEqual(['direct_injection', 'jailbreak', 'zz_new'])
  expect(rows.map((r) => r.heldPct)).toEqual([100, 50, null])
  expect(categoryRows(undefined)).toEqual([])
})

test('the comparison with the previous version says the change and what moved', () => {
  expect(comparisonText(null)).toBeNull()
  const up = comparisonText({ version: 'abcdef123', runId: 'r', score: 80, scoreDelta: 4.5, newlyFailing: [], fixed: ['x', 'y'] })!
  expect(up).toEqual({ tone: 'ok', head: 'Up 4.5 pts against abcdef1', fixed: '2 attacks fixed', failing: null })
  const down = comparisonText({ version: 'abcdef123', runId: 'r', score: 90, scoreDelta: -3, newlyFailing: ['z'], fixed: [] })!
  expect(down.tone).toBe('bad')
  expect(down.head).toBe('Down 3 pts against abcdef1')
  expect(down.failing).toBe('1 attack newly failing')
  expect(comparisonText({ version: 'v1', runId: 'r', score: null, scoreDelta: null, newlyFailing: [], fixed: [] })!.head).toBe('Compared with v1')
  setLocale('it')
  expect(comparisonText({ version: 'v1', runId: 'r', score: 80, scoreDelta: 0, newlyFailing: [], fixed: [] })!.head).toBe('Stesso punteggio di v1')
})

test('the gate warns only in monitor or enforce, when the active version scores below the minimum', () => {
  const versions = [version('new', true, 72), version('old', false, 91)]
  expect(gateWarning({ gate: gate('off'), versions })).toBeNull()
  expect(gateWarning({ gate: gate('monitor'), versions })).toEqual({ version: 'new', score: 72, min: 80, mode: 'monitor' })
  expect(gateWarning({ gate: gate('enforce', 70), versions })).toBeNull()
  expect(gateWarning({ gate: gate('enforce'), versions: [version('new', true, null)] })).toBeNull()
  expect(gateWarning({ gate: gate('monitor'), versions: [version('new', false, 10)] })).toBeNull()
})

test('publishing a held version overrides the gate only when it is enforced', () => {
  expect(needsForce('blocked', 'enforce')).toBe(true)
  expect(needsForce('pending', 'enforce')).toBe(true)
  expect(needsForce(null, 'enforce')).toBe(false)
  expect(needsForce('blocked', 'monitor')).toBe(false)
})

test('the trend axis starts below the lowest score and the minimum', () => {
  const size = { w: 420, h: 132, left: 30, right: 10, top: 10, bottom: 22 }
  const g = trendGeometry(
    [
      { version: 'a', score: 92, at: '2026-11-01T00:00:00Z' },
      { version: 'b', score: 71, at: '2026-11-02T00:00:00Z' },
    ],
    80,
    size,
  )
  expect(g.lo).toBe(60)
  expect(g.points.map((p) => p.x)).toEqual([30, 410])
  expect(g.y(100)).toBe(10)
  expect(g.y(60)).toBe(110)
  expect(g.minY).toBe(g.y(80))
  expect(g.line.startsWith('M30.0')).toBe(true)
  expect(g.area.endsWith('Z')).toBe(true)
  const one = trendGeometry([{ version: 'a', score: 99, at: '2026-11-01T00:00:00Z' }], 95, size)
  expect(one.lo).toBe(90)
  expect(one.points[0]!.x).toBe(220)
  expect(one.area).toBe('')
})

test('languages are named in the console language', () => {
  expect(languageName('es', 'en-GB')).toBe('Spanish')
  expect(languageName('es', 'it-IT')).toBe('Spagnolo')
  expect(languageName('qq-invalid-tag-x', 'en-GB')).toBeTruthy()
})
