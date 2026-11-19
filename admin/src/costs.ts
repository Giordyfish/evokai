/**
 * Cost governance as the console reads it: the month-end forecast chart's series, which alert the banner shows and
 * how it says it, the alert settings (thresholds, webhook, secret) as the form edits them, and a cheaper model's cost
 * as a share of the current one. Shares only: the console never shows an amount.
 */
import type { AppPatch, CostAlert, CostForecast, ModelComparisonRow } from '@client/core/types'

import { num, t, type TextKey } from './i18n'

/** The event a page sends when the open alerts changed (acknowledged, or fired by a forecast): the banner reloads. */
export const ALERTS_CHANGED = 'evk:cost-alerts'
/** The thresholds the service uses when the application sets none. */
export const DEFAULT_THRESHOLDS = [75, 90, 100] as const
/** The most thresholds an application may set, and their bounds (shares of a cap). */
export const MAX_THRESHOLDS = 6
export const THRESHOLD_MIN = 1
export const THRESHOLD_MAX = 200

/** A share as a whole percentage: `92%`, `104%`, or a dash. */
export const wholePct = (v: number | null | undefined): string => (v == null ? '-' : `${num(Math.round(v))}%`)

/** The thresholds as the form writes them: `75, 90, 100`. */
export const thresholdsText = (v: readonly number[] | null | undefined): string => (v ?? []).join(', ')

/**
 * The thresholds typed in the form: whole numbers between 1 and 200, separated by commas or spaces, at most six.
 * Empty means the service's defaults (null). Duplicates are dropped and the rest sorted.
 */
export function parseThresholds(text: string): { ok: true; values: number[] | null } | { ok: false; error: TextKey } {
  const parts = text.split(/[\s,;]+/).filter(Boolean)
  if (!parts.length) return { ok: true, values: null }
  const values: number[] = []
  for (const p of parts) {
    const s = p.replace(/%$/, '')
    if (!/^\d+$/.test(s)) return { ok: false, error: 'costs.thr.whole' }
    const n = Number(s)
    if (n < THRESHOLD_MIN || n > THRESHOLD_MAX) return { ok: false, error: 'costs.thr.range' }
    if (!values.includes(n)) values.push(n)
  }
  if (values.length > MAX_THRESHOLDS) return { ok: false, error: 'costs.thr.tooMany' }
  return { ok: true, values: values.sort((a, b) => a - b) }
}

/**
 * The alert settings to save. The webhook is an https address ("" removes it); the secret is write-only: a typed value
 * replaces it, otherwise it is left out so the stored one stays (removing it is its own action: `cost_alert_secret: ""`).
 */
export function alertPatch(form: { thresholds: string; webhook: string; secret: string }): { ok: true; patch: AppPatch } | { ok: false; error: TextKey } {
  const thr = parseThresholds(form.thresholds)
  if (!thr.ok) return thr
  const hook = form.webhook.trim()
  if (hook && !/^https:\/\/[^\s/]+/i.test(hook)) return { ok: false, error: 'costs.hook.https' }
  const patch: AppPatch = { cost_alert_thresholds: thr.values, cost_alert_webhook: hook }
  if (form.secret) patch.cost_alert_secret = form.secret
  return { ok: true, patch }
}

/** One day of the forecast chart: its own share of the monthly cap and the month's running total up to it. */
export interface ChartDay {
  day: string
  daily: number
  total: number
  kind: 'used' | 'forecast'
}

/** The forecast's days as the chart draws them: the days so far (used) then the days left (forecast), with running totals. */
export function forecastSeries(daily: CostForecast['application']['daily']): ChartDay[] {
  let total = 0
  return [...daily]
    .sort((a, b) => a.day.localeCompare(b.day))
    .map((d) => {
      const kind = d.usedPct !== undefined ? 'used' : 'forecast'
      const v = (kind === 'used' ? d.usedPct : d.forecastPct) ?? 0
      total += v
      return { day: d.day, daily: v, total: Math.round(total * 100) / 100, kind }
    })
}

/**
 * The uncertainty band over the days left: from the running total of the last day so far, widening evenly to the
 * forecast's low and high ends at the end of the month. Empty when no day is left or the ends are unknown.
 */
export function forecastBand(series: ChartDay[], low: number | null, high: number | null): Array<{ i: number; lo: number; hi: number }> {
  const first = series.findIndex((d) => d.kind === 'forecast')
  if (first < 0 || low == null || high == null) return []
  const start = first > 0 ? series[first - 1]!.total : 0
  const steps = series.length - first
  const out = [{ i: first - 1, lo: start, hi: start }]
  for (let k = 1; k <= steps; k++) out.push({ i: first - 1 + k, lo: start + ((low - start) * k) / steps, hi: start + ((high - start) * k) / steps })
  return first > 0 ? out : out.slice(1)
}

/** The chart's top: room for the totals, the band and every threshold, rounded up to a step of 10 (25 above 200). */
export function chartTop(series: ChartDay[], high: number | null, thresholds: readonly number[]): number {
  const raw = Math.max(10, high ?? 0, ...series.map((d) => d.total), ...thresholds) * 1.06
  const step = raw > 200 ? 25 : 10
  return Math.ceil(raw / step) * step
}

/**
 * The alert the banner shows among the open ones: the application's before a person's, then the highest threshold,
 * then the latest. Null when every alert was acknowledged.
 */
export function bannerAlert(alerts: CostAlert[]): CostAlert | null {
  const open = alerts.filter((a) => !a.acknowledgedAt)
  open.sort(
    (a, b) =>
      Number(b.scope === 'application') - Number(a.scope === 'application') || b.threshold - a.threshold || b.firedAt.localeCompare(a.firedAt),
  )
  return open[0] ?? null
}

/** What the banner (and the alerts list) says about an alert. */
export function alertText(a: CostAlert): string {
  const vars = { forecast: wholePct(a.forecastPct), threshold: wholePct(a.threshold) }
  const period = a.cap === 'week' ? 'Week' : a.cap === 'day' ? 'Day' : ''
  return t(`${a.scope === 'application' ? 'costs.alert.app' : 'costs.alert.subject'}${period}` as TextKey, vars)
}

/** A model's cost against the current model's: `38% of the current model's cost`. */
export function relativeCostText(row: Pick<ModelComparisonRow, 'current' | 'relativeCostPct'>): string {
  if (row.current) return t('costs.cmp.isCurrent')
  return row.relativeCostPct == null ? '-' : t('costs.cmp.relCost', { pct: wholePct(row.relativeCostPct) })
}

/** The models of a comparison for the table: the current one first, then the cheapest first. */
export function comparisonRows(models: ModelComparisonRow[]): ModelComparisonRow[] {
  return [...models].sort(
    (a, b) => Number(b.current) - Number(a.current) || (a.relativeCostPct ?? Infinity) - (b.relativeCostPct ?? Infinity) || a.model.localeCompare(b.model),
  )
}

/** The trend of the daily usage in words. */
export const trendLabel = (v: 'rising' | 'flat' | 'falling'): string => t(`costs.trend.${v}`)
