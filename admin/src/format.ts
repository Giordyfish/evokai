/** Small formatting helpers: model names, plain numbers, dates, plurals, application colours, in the console's language. */
import { intlTag, num, type PluralKey, t, tn } from './i18n'
import { type Prefs, prefs } from './prefs'
import type { Weekday } from '@client/core/types'

/** `bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0` → `Claude Haiku 4.5`; `eu.amazon.nova-2-lite-v1:0` → `Nova 2 Lite`. */
export function modelName(id: string): string {
  let s = id
  const colon = s.indexOf(':')
  if (colon > 0 && /^[a-z][a-z0-9-]*$/.test(s.slice(0, colon))) s = s.slice(colon + 1)
  s = s.replace(/^(eu|us|apac|global|ca|jp|au)\./, '')
  s = s.replace(/^(anthropic|amazon|meta|mistral|cohere|ai21|deepseek|openai|qwen|google|writer)\./, '')
  s = s.replace(/-v\d+(:\d+)?$/, '').replace(/:\d+$/, '').replace(/-\d{8}$/, '')
  const words = s.split(/[-_]/).filter(Boolean)
  const out: string[] = []
  for (const w of words) {
    const prev = out[out.length - 1]
    if (/^\d+$/.test(w) && prev && /^\d+(\.\d+)*$/.test(prev)) out[out.length - 1] = `${prev}.${w}`
    else out.push(/^\d/.test(w) ? w : w[0]!.toUpperCase() + w.slice(1))
  }
  return out.join(' ') || id
}

/** The model id without its provider (what the agent definitions use for Bedrock). */
export const bareModel = (id: string): string => (id.startsWith('bedrock:') ? id.slice(8) : id)

/** A share of a cap as text: `0.42%`, `12.5%` (`0,42%` in Italian), or `no cap`. The console never shows an amount, only shares. */
export const sharePct = (v: number | null | undefined, none?: string): string => {
  if (v == null) return none ?? t('fmt.noCap')
  const d = v < 10 ? 2 : 1
  return `${num(Number(v), { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: false })}%`
}

/** A ratio as a percentage with one decimal: `12.5%` (`12,5%`). */
export const pct1 = (v: number): string => `${num(v, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`

/** A plain number as written in a field: no trailing zeros. */
export const plain = (v: number | null | undefined): string => (v == null ? '' : String(Math.round(v * 10000) / 10000))

export const pct = (a: number, b: number | null | undefined): number => (!b ? 0 : Math.min(999, Math.round((a / b) * 100)))

/** A share for a bar: 0 without a cap. */
export const bar = (v: number | null | undefined): number => (v == null ? 0 : Math.min(999, v))

/** A count with its noun: `1 agent`, `3 agents` (`3 agenti`). */
export const pl = (n: number, noun: PluralKey): string => tn(noun, n)

/** A time from the API: stored times are UTC, and SQLite returns them without an offset. */
export function toDate(v: string | number | Date): Date {
  if (typeof v === 'string' && /T\d{2}:\d{2}/.test(v) && !/(Z|[+-]\d{2}:?\d{2})$/.test(v)) return new Date(`${v}Z`)
  return new Date(v)
}

/**
 * `12 Nov`, or `12 Nov 2025` outside this year (`12 nov` in Italian); `withYear` always adds the year. The person's
 * date format can put the month first (`Nov 5, 2025`) or write the date as ISO (`2025-11-12`).
 */
export function day(iso: string | number | Date, withYear = false, order: Prefs['dateFormat'] = prefs().dateFormat): string {
  const d = toDate(iso)
  const showYear = withYear || d.getFullYear() !== new Date().getFullYear()
  if (order === 'ymd') return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  if (order === 'auto') return d.toLocaleDateString(intlTag(), { day: '2-digit', month: 'short', year: showYear ? 'numeric' : undefined }).replace(/\.$/, '')
  const parts = new Intl.DateTimeFormat(intlTag(), { day: order === 'mdy' ? 'numeric' : '2-digit', month: 'short' }).formatToParts(d)
  const dd = parts.find((p) => p.type === 'day')?.value ?? ''
  const mm = (parts.find((p) => p.type === 'month')?.value ?? '').replace(/\.$/, '')
  if (order === 'mdy') return `${mm} ${dd}${showYear ? `, ${d.getFullYear()}` : ''}`
  return `${dd} ${mm}${showYear ? ` ${d.getFullYear()}` : ''}`
}

/** A time of day in the person's clock: `14:30`, or `2:30 pm` with the 12-hour clock. */
export function clock(iso: string | number | Date, f: Prefs['timeFormat'] = prefs().timeFormat): string {
  const h12 = f === 'auto' ? undefined : f === '12h'
  return toDate(iso).toLocaleTimeString(intlTag(), { hour: h12 ? 'numeric' : '2-digit', minute: '2-digit', hour12: h12 })
}

/** A full date and time: `15 Nov 2026, 14:30`. */
export const dateTime = (iso: string | number | Date): string => `${day(iso, true)}, ${clock(iso)}`

/** A short month name for a chart axis: `Nov` (`nov`). */
export const monthShort = (d: Date): string => d.toLocaleString(intlTag(), { month: 'short' }).replace(/\.$/, '')

/** `now`, `2 min ago`, `3 h ago`, `yesterday`, else the day. */
export function ago(iso: string | number | Date | null | undefined): string {
  if (iso == null) return t('ago.never')
  const at = toDate(iso).getTime()
  const s = Math.round((Date.now() - at) / 1000)
  if (s < 0) {
    const f = -s
    return f < 60 ? t('ago.soon') : f < 3600 ? t('ago.inMin', { n: Math.round(f / 60) }) : t('ago.inH', { n: Math.round(f / 3600) })
  }
  if (s < 45) return t('ago.now')
  if (s < 3600) return t('ago.min', { n: Math.max(1, Math.round(s / 60)) })
  if (s < 86400) return t('ago.h', { n: Math.round(s / 3600) })
  if (s < 172800) return t('ago.yesterday')
  return day(iso)
}

const one = (v: number) => num(v, { minimumFractionDigits: 1, maximumFractionDigits: 1 })

export const ms = (v: number | null | undefined): string =>
  v == null ? t('fmt.none') : v >= 1000 ? `${one(v / 1000)} s` : `${num(Math.round(v))} ms`

export function bytes(n: number): string {
  return n < 1000 ? `${n} B` : `${one(n / 1000)} kB`
}

const PAIRS: Array<[string, string]> = [
  ['#7c5cff', '#2fb7a6'],
  ['#f472b6', '#f59e0b'],
  ['#22d3ee', '#6366f1'],
  ['#a78bfa', '#ec4899'],
  ['#34d399', '#0ea5e9'],
  ['#f97316', '#e11d48'],
]

/** A stable gradient per application. */
export function appColor(slug: string): string {
  let h = 0
  for (const c of slug) h = (h * 31 + c.charCodeAt(0)) >>> 0
  const [a, b] = PAIRS[h % PAIRS.length]!
  return `linear-gradient(135deg,${a},${b})`
}

export const titleCase = (s: string): string => (s ? s[0]!.toUpperCase() + s.slice(1) : s)

/** The month so far, as the usage pages say it: `1 to 14 November` (`1-14 novembre`). */
export function monthSoFar(now = new Date()): string {
  return t('fmt.monthSoFar', { day: now.getDate(), month: now.toLocaleString(intlTag(), { month: 'long' }) })
}

export function download(filename: string, text: string, type = 'text/plain'): void {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function csv(rows: Array<Array<string | number | null | undefined>>): string {
  const cell = (v: string | number | null | undefined) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return rows.map((r) => r.map(cell).join(',')).join('\n') + '\n'
}

export async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** A slug from a name: lower case, digits and dashes. */
export function slugOf(name: string): string {
  return name
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
}

/** The weekdays a weekly cap may reset on, Monday first. */
export const WEEKDAYS: readonly Weekday[] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

/** A weekday's name in the console's language: `Saturday`, `sabato`. */
export function weekdayName(d: Weekday): string {
  // 1 January 2024 was a Monday.
  return new Date(Date.UTC(2024, 0, 1 + WEEKDAYS.indexOf(d), 12)).toLocaleString(intlTag(), { weekday: 'long', timeZone: 'UTC' })
}
