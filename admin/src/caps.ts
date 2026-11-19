/**
 * The caps as the Limits page edits them: one row per cap (who it binds, its period, its maximum in the application's
 * currency, its reset), the currency and its exchange rate, and the share of each cap used so far. The page shows the
 * maxima an admin sets, never what was spent: spend is only ever a share of a cap.
 */
import type { AppPatch, Application, CapConfig, CapPeriod, ConsoleApplication, ConsoleCapShare, LimitKind, Weekday } from '@client/core/types'

import { WEEKDAYS } from './format'
import type { TextKey } from './i18n'

export { WEEKDAYS, weekdayName } from './format'
export const PERIODS: readonly CapPeriod[] = ['month', 'week', 'day']
/** Currencies offered in the form; any ISO 4217 code is accepted. */
export const CURRENCIES = ['USD', 'EUR', 'GBP', 'CHF'] as const

export interface CapRow {
  scope: 'application' | 'subject'
  period: CapPeriod
  /** As typed, in the application's currency. */
  amount: string
  /** A weekday for a week, a day of the month (as typed) for a month, '' for a day. */
  resets: string
  /** Set by an old `*_usd` field (in USD): saving moves it to the application's currency at the rate. */
  fromUsd?: boolean
}

/** The kind of cap a row is (what a blocked turn reports). */
export const kindOf = (r: Pick<CapRow, 'scope' | 'period'>): LimitKind => `${r.scope === 'application' ? 'app' : 'subject'}_${r.period}` as LimitKind

const round = (v: number): string => String(Math.round(v * 10000) / 10000)

/**
 * The rows of the caps in effect: those of `caps` as they are, an old field's converted from USD to the application's
 * currency at its rate (shown as such, and saved in the currency).
 */
export function rowsOf(a: Pick<Application, 'effectiveCaps' | 'currency' | 'exchange_rate'>): CapRow[] {
  const currency = a.currency ?? 'USD'
  const rate = currency === 'USD' ? 1 : (a.exchange_rate ?? null)
  return (a.effectiveCaps ?? [])
    .filter((c) => c.source !== 'token')
    .map((c) => {
      const usd = c.currency === 'USD' && currency !== 'USD'
      const amount = usd ? (rate ? c.amount * rate : c.amount) : c.amount
      const row: CapRow = { scope: c.scope, period: c.period, amount: round(amount), resets: c.resets == null ? '' : String(c.resets) }
      if (usd) row.fromUsd = true
      return row
    })
}

/** A new row: the first period this scope does not have yet (a month by default). */
export function newRow(rows: CapRow[], scope: CapRow['scope'] = 'subject'): CapRow {
  const taken = new Set(rows.filter((r) => r.scope === scope).map((r) => r.period))
  const period = PERIODS.find((p) => !taken.has(p)) ?? 'month'
  return { scope, period, amount: '', resets: period === 'week' ? 'monday' : period === 'month' ? '1' : '' }
}

/** A row with another period: its reset follows (Monday for a week, the 1st for a month, none for a day). */
export function withPeriod(r: CapRow, period: CapPeriod): CapRow {
  if (period === r.period) return r
  return { ...r, period, resets: period === 'week' ? 'monday' : period === 'month' ? '1' : '' }
}

export type CapsResult = { ok: true; patch: AppPatch } | { ok: false; error: TextKey }

/**
 * The change to save: the currency, its rate and every cap in the new form. The old fields are cleared (their caps are
 * in the list now); the application keeps a monthly cap, so the list needs one.
 */
export function capsPatch(rows: CapRow[], currency: string, rate: string): CapsResult {
  const code = currency.trim().toUpperCase()
  if (!/^[A-Z]{3}$/.test(code)) return { ok: false, error: 'limits.currencyCode' }
  const r = rate.trim() === '' ? null : Number(rate)
  if (code !== 'USD' && (r == null || !Number.isFinite(r) || r <= 0)) return { ok: false, error: 'limits.rateNeeded' }
  if (r != null && (!Number.isFinite(r) || r <= 0)) return { ok: false, error: 'limits.rateNeeded' }
  const seen = new Set<string>()
  const caps: { application: CapConfig[]; subject: CapConfig[] } = { application: [], subject: [] }
  for (const row of rows) {
    const amount = row.amount.trim() === '' ? NaN : Number(row.amount)
    if (!Number.isFinite(amount) || amount < 0) return { ok: false, error: 'limits.capsPlain' }
    const key = `${row.scope}:${row.period}`
    if (seen.has(key)) return { ok: false, error: 'limits.capTwice' }
    seen.add(key)
    const cap: CapConfig = { period: row.period, amount }
    if (row.period === 'week') {
      if (!WEEKDAYS.includes(row.resets as Weekday)) return { ok: false, error: 'limits.capWeekday' }
      cap.resets = row.resets as Weekday
    } else if (row.period === 'month') {
      const day = Number(row.resets || '1')
      if (!Number.isInteger(day) || day < 1 || day > 31) return { ok: false, error: 'limits.capMonthDay' }
      cap.resets = day
    }
    caps[row.scope].push(cap)
  }
  if (!seen.has('application:month')) return { ok: false, error: 'limits.capsMonthNeeded' }
  return {
    ok: true,
    patch: {
      currency: code,
      exchange_rate: r,
      caps,
      daily_cap_usd: null,
      subject_monthly_usd: null,
      subject_daily_usd: null,
    },
  }
}

/** The share used of a row's cap in its current window (null for a cap not saved yet or not reported). */
export function usedOf(app: Pick<ConsoleApplication, 'usage'>, row: Pick<CapRow, 'scope' | 'period'>): ConsoleCapShare | null {
  return app.usage.caps?.find((c) => c.limit === kindOf(row)) ?? null
}
