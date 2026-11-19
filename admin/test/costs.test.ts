import type { CostAlert, ModelComparisonRow } from '@client/core/types'

import {
  alertPatch,
  alertText,
  bannerAlert,
  chartTop,
  comparisonRows,
  forecastBand,
  forecastSeries,
  parseThresholds,
  relativeCostText,
  thresholdsText,
  trendLabel,
  wholePct,
} from '../src/costs'
import { en } from '../src/i18n/en'
import { it as italian } from '../src/i18n/it'
import { setLocale } from '../src/i18n'

afterEach(() => setLocale('en'))

const alert = (id: string, over: Partial<CostAlert> = {}): CostAlert => ({
  id,
  scope: 'application',
  subjectKey: null,
  period: '2026-11',
  threshold: 90,
  forecastPct: 92.4,
  usedPct: 51,
  firedAt: '2026-11-10T08:00:00Z',
  delivery: {},
  acknowledgedAt: null,
  ...over,
})

const row = (model: string, over: Partial<ModelComparisonRow> = {}): ModelComparisonRow => ({
  model,
  label: model,
  passRate: 90,
  passRateDelta: 0,
  relativeCostPct: 100,
  latencyMs: 800,
  cases: 10,
  errors: 0,
  passes: true,
  current: false,
  recommended: false,
  ...over,
})

test('thresholds are whole shares from 1 to 200, at most six, sorted without duplicates', () => {
  expect(parseThresholds('90, 75,100')).toEqual({ ok: true, values: [75, 90, 100] })
  expect(parseThresholds('80% 80 120')).toEqual({ ok: true, values: [80, 120] })
  expect(parseThresholds('  ')).toEqual({ ok: true, values: null })
  expect(parseThresholds('75, 9.5')).toEqual({ ok: false, error: 'costs.thr.whole' })
  expect(parseThresholds('75, abc')).toEqual({ ok: false, error: 'costs.thr.whole' })
  expect(parseThresholds('0, 50')).toEqual({ ok: false, error: 'costs.thr.range' })
  expect(parseThresholds('201')).toEqual({ ok: false, error: 'costs.thr.range' })
  expect(parseThresholds('10 20 30 40 50 60 70')).toEqual({ ok: false, error: 'costs.thr.tooMany' })
  expect(thresholdsText([75, 90, 100])).toBe('75, 90, 100')
  expect(thresholdsText(null)).toBe('')
})

test('the alert settings keep the stored secret unless a new one is typed', () => {
  expect(alertPatch({ thresholds: '75, 90', webhook: ' https://hooks.example.com/x ', secret: '' })).toEqual({
    ok: true,
    patch: { cost_alert_thresholds: [75, 90], cost_alert_webhook: 'https://hooks.example.com/x' },
  })
  expect(alertPatch({ thresholds: '', webhook: '', secret: 's3cret' })).toEqual({
    ok: true,
    patch: { cost_alert_thresholds: null, cost_alert_webhook: '', cost_alert_secret: 's3cret' },
  })
  expect(alertPatch({ thresholds: '75', webhook: 'http://hooks.example.com', secret: '' })).toEqual({ ok: false, error: 'costs.hook.https' })
  expect(alertPatch({ thresholds: 'x', webhook: '', secret: '' })).toEqual({ ok: false, error: 'costs.thr.whole' })
})

test('the forecast series runs the totals across the days so far and the days left', () => {
  const s = forecastSeries([
    { day: '2026-11-02', usedPct: 4 },
    { day: '2026-11-01', usedPct: 3 },
    { day: '2026-11-03', forecastPct: 5 },
    { day: '2026-11-04', forecastPct: 5.5 },
  ])
  expect(s.map((d) => [d.day.slice(8), d.kind, d.daily, d.total])).toEqual([
    ['01', 'used', 3, 3],
    ['02', 'used', 4, 7],
    ['03', 'forecast', 5, 12],
    ['04', 'forecast', 5.5, 17.5],
  ])
  expect(forecastSeries([{ day: '2026-11-01', usedPct: null }])[0]!.daily).toBe(0)
})

test('the band widens from today to the low and high ends', () => {
  const s = forecastSeries([
    { day: '2026-11-01', usedPct: 10 },
    { day: '2026-11-02', forecastPct: 10 },
    { day: '2026-11-03', forecastPct: 10 },
  ])
  expect(forecastBand(s, 20, 40)).toEqual([
    { i: 0, lo: 10, hi: 10 },
    { i: 1, lo: 15, hi: 25 },
    { i: 2, lo: 20, hi: 40 },
  ])
  expect(forecastBand(s, null, 40)).toEqual([])
  expect(forecastBand(forecastSeries([{ day: '2026-11-01', usedPct: 10 }]), 10, 10)).toEqual([])
  const fresh = forecastBand(forecastSeries([{ day: '2026-11-01', forecastPct: 5 }, { day: '2026-11-02', forecastPct: 5 }]), 8, 12)
  expect(fresh).toEqual([
    { i: 0, lo: 4, hi: 6 },
    { i: 1, lo: 8, hi: 12 },
  ])
})

test('the chart has room for the totals, the band and every threshold', () => {
  const s = forecastSeries([{ day: '2026-11-01', usedPct: 30 }])
  expect(chartTop(s, 60, [75, 90, 100])).toBe(110)
  expect(chartTop(s, 230, [100])).toBe(250)
  expect(chartTop(forecastSeries([]), null, [])).toBe(20)
})

test('the banner shows the application before a person, then the highest threshold, then the latest', () => {
  expect(bannerAlert([])).toBeNull()
  expect(bannerAlert([alert('a', { acknowledgedAt: '2026-11-11T00:00:00Z' })])).toBeNull()
  const pick = bannerAlert([
    alert('p', { scope: 'subject', subjectKey: 'k', threshold: 100 }),
    alert('a75', { threshold: 75 }),
    alert('a90old', { firedAt: '2026-11-05T00:00:00Z' }),
    alert('a90new', { firedAt: '2026-11-12T00:00:00Z' }),
  ])
  expect(pick?.id).toBe('a90new')
  expect(bannerAlert([alert('p', { scope: 'subject', subjectKey: 'k' })])?.id).toBe('p')
})

test('alerts say the forecast and the threshold, for the application or a person', () => {
  expect(alertText(alert('a'))).toBe('The month-end forecast reaches 92% of the monthly cap (alert at 90%).')
  expect(alertText(alert('p', { scope: 'subject', forecastPct: 104, threshold: 100 }))).toBe(
    "A person's month-end forecast reaches 104% of their monthly cap (alert at 100%).",
  )
  setLocale('it')
  expect(alertText(alert('a'))).toBe('La previsione di fine mese arriva al 92% del tetto mensile (avviso al 90%).')
})

test('a model cost is a share of the current one, never an amount', () => {
  expect(relativeCostText({ current: false, relativeCostPct: 38.2 })).toBe("38% of the current model's cost")
  expect(relativeCostText({ current: true, relativeCostPct: 100 })).toBe("The current model's cost")
  expect(relativeCostText({ current: false, relativeCostPct: null })).toBe('-')
  expect(wholePct(1234.4)).toBe('1,234%')
  const rows = comparisonRows([row('b', { relativeCostPct: 60 }), row('cur', { current: true }), row('a', { relativeCostPct: 20 }), row('n', { relativeCostPct: null })])
  expect(rows.map((r) => r.model)).toEqual(['cur', 'a', 'b', 'n'])
  expect(trendLabel('rising')).toBe('Rising')
})

test('no cost text names a currency', () => {
  const texts = [...Object.entries(en), ...Object.entries(italian)]
    .filter(([k]) => k.startsWith('costs.') || k.startsWith('rt.'))
    .flatMap(([, v]) => (typeof v === 'string' ? [v] : [v.one, v.other]))
  expect(texts.filter((s) => /[$€£]|\b(USD|EUR|dollar|euro)/i.test(s))).toEqual([])
})
