import { ago, appColor, bar, csv, modelName, pct, sharePct } from '../src/format'
import { apiBase } from '../src/api-base'

test('model names read like products', () => {
  expect(modelName('bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0')).toBe('Claude Haiku 4.5')
  expect(modelName('eu.anthropic.claude-sonnet-4-6')).toBe('Claude Sonnet 4.6')
  expect(modelName('eu.amazon.nova-2-lite-v1:0')).toBe('Nova 2 Lite')
  expect(modelName('function:primary')).toBe('Primary')
  expect(modelName('bedrock:eu.anthropic.*')).toBe('*')
})

test('shares read as percentages, never amounts, and bars are bounded', () => {
  expect(sharePct(0.134)).toBe('0.13%')
  expect(sharePct(42.04)).toBe('42.0%')
  expect(sharePct(null)).toBe('no cap')
  expect(bar(null)).toBe(0)
  expect(pct(3, 9)).toBe(33)
  expect(pct(1, 0)).toBe(0)
})

test('dates read as people say them', () => {
  expect(ago(Date.now() - 5_000)).toBe('now')
  expect(ago(Date.now() - 120_000)).toBe('2 min ago')
  expect(ago(null)).toBe('never')
})

test('csv quotes what needs quoting, colours are stable', () => {
  expect(csv([['a', 'b,c'], [1, null]])).toBe('a,"b,c"\n1,\n')
  expect(appColor('bookshop')).toBe(appColor('bookshop'))
})

test('the API base is where the console is mounted', () => {
  expect(apiBase('/evokai/admin/')).toBe('/evokai')
  expect(apiBase('/agent/admin')).toBe('/agent')
  expect(apiBase('/')).toBe('/agent')
})

test('times without an offset are UTC', async () => {
  const { toDate } = await import('../src/format')
  expect(toDate('2026-11-14T10:00:00').toISOString()).toBe('2026-11-14T10:00:00.000Z')
  expect(toDate('2026-11-14T10:00:00+00:00').toISOString()).toBe('2026-11-14T10:00:00.000Z')
})
