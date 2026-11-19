/**
 * Today's use of the assistant as a thin ring: it fills with the day's share and turns from the accent to red as the
 * allowance runs out. Hover or focus shows the shares of the day, the month and the application's month. Only shares
 * reach the browser; there is no amount to show.
 */
import type { VNode } from 'preact'
import { useState } from 'preact/hooks'

import type { UsageShares } from '@evokai/evokai-client/browser'
import { useUi } from './context'
import { fill } from './strings'

let n = 0

/** 0–100, rounded, as text. */
export const pctText = (v: number | null | undefined): string => `${Math.max(0, Math.min(100, Math.round(v ?? 0)))}%`

/**
 * The share the ring shows: the cap that limits most when the service names it, else the subject's day, week or month,
 * else the application's month; full when a cap is reached.
 */
export function ringShare(u: UsageShares): number {
  if (u.monthOut || u.out) return 100
  return Math.max(0, Math.min(100, u.binding?.usedPct ?? u.dayPct ?? u.weekPct ?? u.monthPct ?? u.companyPct ?? 0))
}

/** When a reached cap is available again, in the person's language and time zone (null without a reset). */
export function backText(u: UsageShares, lang: string, template: string): string | null {
  const when = whenText(u.resetsAt, lang)
  return when ? fill(template, { when }) : null
}

/** A reset time as the person reads it (weekday, day, month, hour), or null without a valid time. */
function whenText(iso: string | null | undefined, lang: string): string | null {
  const at = new Date(iso ?? '')
  return iso && !Number.isNaN(at.getTime())
    ? new Intl.DateTimeFormat(lang, { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(at)
    : null
}

/**
 * When the share the ring shows renews: every night for a daily cap, else the day and hour of the weekly or monthly
 * cap that limits most, in the person's language and time zone.
 */
export function renewsText(u: UsageShares, lang: string, s: { usageRenews: string; usageRenewsAt: string }): string {
  const when = u.binding && u.binding.period !== 'day' ? whenText(u.binding.resetsAt, lang) : null
  return when ? fill(s.usageRenewsAt, { when }) : s.usageRenews
}

export function UsageRing({ usage }: { usage: UsageShares }): VNode {
  const { s, names, lang } = useUi()
  const [open, setOpen] = useState(false)
  const [id] = useState(() => `ek-usage-${++n}`)
  const shown = ringShare(usage)
  const danger = shown > 50 ? (shown - 50) / 50 : 0
  const color = `color-mix(in oklab, var(--ek-accent) ${Math.round((1 - danger) * 100)}%, var(--ek-danger))`
  const r = 9
  const c = 2 * Math.PI * r
  const rows: Array<[string, number | null]> = [
    [s.usageToday, usage.dayPct],
    [s.usageWeek, usage.weekPct ?? null],
    [s.usageMonth, usage.monthPct],
    [fill(s.usageApp, { app: names.app || names.title }), usage.companyPct],
  ]
  return (
    <span class="usage" part="usage" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        class="ib"
        aria-describedby={id}
        aria-label={usage.monthOut ? s.usageOutAria : usage.out ? s.usageLimitOut : fill(s.usageAria, { pct: pctText(shown) })}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" class="ring">
          <circle cx="12" cy="12" r={r} fill="none" stroke="var(--ek-border)" stroke-width="2.4" />
          <circle cx="12" cy="12" r={r} fill="none" stroke={color} stroke-width="2.4" stroke-linecap="round" stroke-dasharray={`${(shown / 100) * c} ${c}`} />
        </svg>
      </button>
      <span id={id} role="tooltip" class={`tip ${open ? 'on' : ''}`} part="usage-tip">
        <b>{usage.monthOut ? s.usageOut : usage.out ? s.usageLimitOut : s.usageTitle}</b>
        <span class="grid">
          {rows
            .filter(([, v]) => v !== null && v !== undefined)
            .map(([label, v]) => [
              <span key={`${label}l`} class="sub">
                {label}
              </span>,
              <span key={`${label}v`} class="num strong">
                {pctText(v)}
              </span>,
            ])}
        </span>
        <span class="sub">{usage.monthOut || usage.out ? (backText(usage, lang, s.usageBackAt) ?? s.usageBack) : renewsText(usage, lang, s)}</span>
      </span>
    </span>
  )
}
