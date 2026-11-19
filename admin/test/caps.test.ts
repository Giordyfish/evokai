import type { Application, ConsoleApplication } from '@client/core/types'

import { capsPatch, kindOf, newRow, rowsOf, usedOf, weekdayName, withPeriod, type CapRow } from '../src/caps'
import { alertText } from '../src/costs'
import { setLocale } from '../src/i18n'
import { en } from '../src/i18n/en'
import { it as italian } from '../src/i18n/it'

afterEach(() => setLocale('en'))

const app = (over: Partial<Application> = {}): Pick<Application, 'effectiveCaps' | 'currency' | 'exchange_rate'> => ({
  currency: 'EUR',
  exchange_rate: 0.9,
  effectiveCaps: [
    { kind: 'app_month', scope: 'application', period: 'month', amount: 9, currency: 'EUR', resets: 1, source: 'caps' },
    { kind: 'subject_day', scope: 'subject', period: 'day', amount: 2, currency: 'USD', resets: null, source: 'legacy' },
  ],
  ...over,
})

test('rows come from the caps in effect, an old USD field converted at the rate', () => {
  const rows = rowsOf(app())
  expect(rows).toEqual([
    { scope: 'application', period: 'month', amount: '9', resets: '1' },
    { scope: 'subject', period: 'day', amount: '1.8', resets: '', fromUsd: true },
  ])
  expect(rowsOf(app({ currency: 'USD', exchange_rate: null }))[1]).toEqual({ scope: 'subject', period: 'day', amount: '2', resets: '' })
})

test('the patch sends every cap in the new form and clears the old fields', () => {
  const rows: CapRow[] = [
    { scope: 'application', period: 'month', amount: '9', resets: '15' },
    { scope: 'subject', period: 'week', amount: '5', resets: 'saturday' },
    { scope: 'subject', period: 'day', amount: '1.5', resets: '' },
  ]
  const r = capsPatch(rows, 'eur', '0.9048')
  expect(r).toEqual({
    ok: true,
    patch: {
      currency: 'EUR',
      exchange_rate: 0.9048,
      caps: {
        application: [{ period: 'month', amount: 9, resets: 15 }],
        subject: [
          { period: 'week', amount: 5, resets: 'saturday' },
          { period: 'day', amount: 1.5 },
        ],
      },
      daily_cap_usd: null,
      subject_monthly_usd: null,
      subject_daily_usd: null,
    },
  })
})

test('the patch refuses what the service would refuse', () => {
  const month: CapRow = { scope: 'application', period: 'month', amount: '9', resets: '1' }
  expect(capsPatch([month], 'EUR', '')).toEqual({ ok: false, error: 'limits.rateNeeded' })
  expect(capsPatch([month], 'EUR', '0')).toEqual({ ok: false, error: 'limits.rateNeeded' })
  expect(capsPatch([month], 'EURO', '1')).toEqual({ ok: false, error: 'limits.currencyCode' })
  expect(capsPatch([month, { ...month }], 'USD', '')).toEqual({ ok: false, error: 'limits.capTwice' })
  expect(capsPatch([{ ...month, amount: '-1' }], 'USD', '')).toEqual({ ok: false, error: 'limits.capsPlain' })
  expect(capsPatch([{ ...month, resets: '32' }], 'USD', '')).toEqual({ ok: false, error: 'limits.capMonthDay' })
  expect(capsPatch([month, { scope: 'subject', period: 'week', amount: '1', resets: 'someday' }], 'USD', '')).toEqual({ ok: false, error: 'limits.capWeekday' })
  expect(capsPatch([{ scope: 'subject', period: 'day', amount: '1', resets: '' }], 'USD', '')).toEqual({ ok: false, error: 'limits.capsMonthNeeded' })
  expect(capsPatch([month], 'USD', '')).toMatchObject({ ok: true, patch: { currency: 'USD', exchange_rate: null } })
})

test('new rows take a free period, and a period change sets its reset', () => {
  const rows: CapRow[] = [{ scope: 'subject', period: 'month', amount: '1', resets: '1' }]
  expect(newRow(rows)).toEqual({ scope: 'subject', period: 'week', amount: '', resets: 'monday' })
  expect(withPeriod(rows[0]!, 'day').resets).toBe('')
  expect(kindOf({ scope: 'application', period: 'week' })).toBe('app_week')
})

test('each cap shows the share used of its window, never an amount', () => {
  const console = {
    usage: {
      monthPct: 10,
      monthLeftPct: 90,
      dayPct: null,
      dayLeftPct: null,
      caps: [{ limit: 'subject_week', scope: 'subject', period: 'week', resets: 'saturday', usedPct: 42.5, leftPct: 57.5, resetsAt: '2026-11-20T23:00:00+00:00' }],
    },
  } as unknown as Pick<ConsoleApplication, 'usage'>
  expect(usedOf(console, { scope: 'subject', period: 'week' })?.usedPct).toBe(42.5)
  expect(usedOf(console, { scope: 'application', period: 'week' })).toBeNull()
})

test('weekdays are named in the console language', () => {
  expect(weekdayName('saturday')).toBe('Saturday')
  setLocale('it')
  expect(weekdayName('saturday')).toBe('sabato')
})

test('alerts name the cap period', () => {
  const base = { id: 'a', scope: 'application' as const, subjectKey: null, threshold: 90, forecastPct: 92.4, usedPct: 51, firedAt: '2026-11-10T08:00:00Z', delivery: {}, acknowledgedAt: null }
  expect(alertText({ ...base, period: '2026-11-14w', cap: 'week' })).toBe('The week-end forecast reaches 92% of the weekly cap (alert at 90%).')
  expect(alertText({ ...base, period: '2026-11', cap: 'month' })).toContain('monthly cap')
  setLocale('it')
  expect(alertText({ ...base, scope: 'subject', period: '2026-11-16d', cap: 'day' })).toBe(
    'La previsione di oggi di una persona arriva al 92% del suo tetto giornaliero (avviso al 90%).',
  )
})

test('the limits texts show no spend, only maxima and shares', () => {
  const texts = [...Object.entries(en), ...Object.entries(italian)]
    .filter(([k]) => k.startsWith('limits.'))
    .flatMap(([, v]) => (typeof v === 'string' ? [v] : [v.one, v.other]))
  expect(texts.filter((s) => /[$€£]|\bspent\b|\bspesa (di|del)/i.test(s))).toEqual([])
})
