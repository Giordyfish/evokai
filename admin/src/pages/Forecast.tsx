/**
 * The month-end forecast on an application's Usage page: where usage will end against the monthly cap (with its
 * band), the people most likely to reach their cap, this month's alerts, and the agents whose prompt is long enough to
 * cache. Shares of the caps only, never an amount.
 */
import { useEffect, useState } from 'preact/hooks'

import type { CacheSuggestion, CapPeriod, ConsoleApplication, CostAlert, CostForecast } from '@client/core/types'
import { describe, forApp } from '../api'
import { ALERTS_CHANGED, alertText, chartTop, forecastBand, forecastSeries, trendLabel, wholePct } from '../costs'
import { ago, bar, dateTime, day, modelName, monthShort, sharePct } from '../format'
import { num, t, tn } from '../i18n'
import { toast } from '../magic'
import { appHref, useLoad } from '../state'
import { Bar, Btn, Panel, Tag, type Tone } from '../ui'
import { Loads } from './common'

const trendTone = (v: string): Tone => (v === 'rising' ? 'warn' : v === 'falling' ? 'ok' : '')
const trendArrow = (v: string): string => (v === 'rising' ? '↗' : v === 'falling' ? '↘' : '→')

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div class="fstat">
      <span class="lbl">{label}</span>
      <b>{value}</b>
      {sub ? <span class="s">{sub}</span> : null}
    </div>
  )
}

export function Forecast({ app }: { app: ConsoleApplication }) {
  const api = forApp(app.id)
  const readOnly = app.role === 'viewer'
  const [acking, setAcking] = useState<string | null>(null)
  const load = useLoad(() => api.admin.costs.forecast(), [app.id])
  // Reading the forecast may fire new alerts, and the banner may acknowledge some: both sides read again.
  useEffect(() => {
    if (load.data) dispatchEvent(new CustomEvent(ALERTS_CHANGED, { detail: { app: app.id, from: 'usage' } }))
  }, [load.data])
  useEffect(() => {
    const again = (e: Event) => {
      if ((e as CustomEvent<{ from?: string }>).detail?.from !== 'usage') void load.reload()
    }
    addEventListener(ALERTS_CHANGED, again)
    return () => removeEventListener(ALERTS_CHANGED, again)
  }, [])

  const ack = async (a: CostAlert) => {
    setAcking(a.id)
    try {
      await api.admin.costs.acknowledge(a.id)
      toast(t('costs.acked'), alertText(a), null)
      await load.reload()
    } catch (err) {
      toast(t('costs.notAcked'), describe(err), null)
    } finally {
      setAcking(null)
    }
  }

  return (
    <Loads load={load} what={t('what.forecast')}>
      {(f) => {
        const a = f.application
        const names = new Map(f.subjects.map((s) => [s.subjectKey, s.subject]))
        return (
          <div class="grid" style="margin-bottom:16px">
            <Panel
              title={t('costs.fc.title')}
              actions={
                a.capSet ? (
                  <Tag tone={trendTone(a.trend)}>
                    {trendArrow(a.trend)} {trendLabel(a.trend)}
                  </Tag>
                ) : null
              }
            >
              {!a.capSet ? (
                <p class="hint">{t('costs.fc.noCap')}</p>
              ) : (
                <>
                  <div class="fstats">
                    <Stat label={t('costs.fc.used')} value={sharePct(a.usedPct)} />
                    <Stat
                      label={t('costs.fc.end')}
                      value={sharePct(a.forecastPct)}
                      sub={a.lowPct != null && a.highPct != null ? t('costs.fc.band', { low: sharePct(a.lowPct), high: sharePct(a.highPct) }) : undefined}
                    />
                    <Stat label={t('costs.fc.trend')} value={`${trendArrow(a.trend)} ${trendLabel(a.trend)}`} />
                    <Stat label={t('costs.fc.daysLeft')} value={num(a.daysLeft)} />
                  </div>
                  <ForecastChart f={f} />
                  <div class="flegend">
                    <span>
                      <i style="background:var(--c1)" />
                      {t('costs.fc.lgUsed')}
                    </span>
                    <span>
                      <i class="dash" />
                      {t('costs.fc.lgForecast')}
                    </span>
                    <span>
                      <i style="background:color-mix(in srgb,var(--c2) 22%,transparent)" />
                      {t('costs.fc.lgBand')}
                    </span>
                    <span>
                      <i class="thr" />
                      {t('costs.fc.lgThresholds', { list: f.thresholds.map((x) => wholePct(x)).join(', ') })}
                    </span>
                  </div>
                </>
              )}
              <p class="hint">
                {t('costs.fc.note')} {f.webhook.set ? t(f.webhook.signed ? 'costs.fc.hookSigned' : 'costs.fc.hookUnsigned') : t('costs.fc.noHook')}{' '}
                <a href={appHref(app.slug, 'limits')}>{t('costs.fc.settings')}</a>
              </p>
            </Panel>
            <OtherCaps caps={(f.caps ?? []).filter((c) => c.period !== 'month')} />
            <div class="grid g2">
              <Panel title={t('costs.fc.people')}>
                {!f.subjects.length ? (
                  <p class="hint">{t('costs.fc.noPeople')}</p>
                ) : (
                  <div class="wx">
                    <table class="tbl">
                      <thead>
                        <tr>
                          <th>{t('usage.subject')}</th>
                          <th class="r">{t('costs.fc.usedCol')}</th>
                          <th>{t('costs.fc.endCol')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {f.subjects.map((s) => (
                          <tr key={s.subjectKey}>
                            <td>
                              <code>{s.subject}</code>
                              <div class="s">{t(s.cap === 'subject' ? capOwnKey(s.period) : 'costs.fc.capApp')}</div>
                            </td>
                            <td class="num">{sharePct(s.usedPct)}</td>
                            <td style="min-width:150px">
                              <div style="display:flex;align-items:center;gap:8px">
                                <span class="num">{sharePct(s.forecastPct)}</span>
                                <Bar value={bar(s.forecastPct)} style="flex:1" />
                              </div>
                              {s.lowPct != null && s.highPct != null ? (
                                <div class="s">{t('costs.fc.band', { low: sharePct(s.lowPct), high: sharePct(s.highPct) })}</div>
                              ) : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
              <Panel title={t('costs.fc.alerts')} actions={f.alerts.length ? <Tag>{tn('costs.fc.alertCount', f.alerts.length)}</Tag> : null}>
                {!f.alerts.length ? (
                  <p class="hint">{t('costs.fc.noAlerts')}</p>
                ) : (
                  <ul class="falerts">
                    {f.alerts.map((x) => (
                      <li key={x.id} data-open={x.acknowledgedAt ? undefined : 'true'}>
                        <div>
                          <b>{alertText(x)}</b>
                          <div class="s">
                            {x.scope === 'subject' ? `${t('costs.fc.person', { subject: names.get(x.subjectKey ?? '') ?? x.subjectKey ?? '' })} · ` : ''}
                            {t('costs.fc.fired', { when: ago(x.firedAt), used: sharePct(x.usedPct) })}
                            {deliveryText(x) ? ` · ${deliveryText(x)}` : ''}
                          </div>
                        </div>
                        {x.acknowledgedAt ? (
                          <span class="s">{t('costs.fc.ackedAt', { when: ago(x.acknowledgedAt) })}</span>
                        ) : !readOnly ? (
                          <Btn sm busy={acking === x.id ? t('costs.acking') : false} onClick={() => void ack(x)}>
                            {t('costs.ack')}
                          </Btn>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
          </div>
        )
      }}
    </Loads>
  )
}

/** What the webhook did with an alert. */
function deliveryText(a: CostAlert): string {
  const d = a.delivery ?? {}
  if (d.reason) return t('costs.fc.hookFailed', { reason: d.reason })
  if (d.status == null) return ''
  const code = Number(d.status)
  return code >= 200 && code < 300 ? t('costs.fc.hookDelivered') : t('costs.fc.hookAnswered', { status: String(d.status) })
}

/**
 * The month as a burn-up: the running total of the days so far (solid), the forecast to the month's end (dashed) in
 * its band, the alert thresholds as lines; below, each day's own share (the days left drawn hollow).
 */
export function ForecastChart({ f }: { f: CostForecast }) {
  const a = f.application
  const series = forecastSeries(a.daily)
  if (!series.length) return null
  const W = 960
  const l = 58
  const r = 46
  const top = 22
  const plotB = 196
  const stripT = 214
  const stripB = 250
  const H = 274
  const n = series.length
  const max = chartTop(series, a.highPct, f.thresholds)
  const x = (i: number) => l + ((i + 0.5) * (W - l - r)) / n
  const y = (v: number) => plotB - (Math.min(v, max) / max) * (plotB - top)
  const dayMax = Math.max(0.01, ...series.map((d) => d.daily))
  const yd = (v: number) => stripB - (v / dayMax) * (stripB - stripT)
  const lastUsed = series.map((d) => d.kind).lastIndexOf('used')
  const used = series.slice(0, lastUsed + 1)
  const pt = (i: number) => `${x(i).toFixed(1)} ${y(series[i]!.total).toFixed(1)}`
  const usedLine = used.map((_, i) => `${i ? 'L' : 'M'}${pt(i)}`).join(' ')
  const usedArea = used.length > 1 ? `${usedLine} L${x(lastUsed).toFixed(1)} ${plotB} L${x(0).toFixed(1)} ${plotB}Z` : ''
  const fcLine = lastUsed < n - 1 ? series.map((_, i) => i).filter((i) => i >= Math.max(0, lastUsed)).map((i, k) => `${k ? 'L' : 'M'}${pt(i)}`).join(' ') : ''
  const band = forecastBand(series, a.lowPct, a.highPct)
  const bandPath = band.length
    ? `M${band.map((b) => `${x(Math.max(0, b.i)).toFixed(1)} ${y(b.hi).toFixed(1)}`).join(' L')} L${[...band]
        .reverse()
        .map((b) => `${x(Math.max(0, b.i)).toFixed(1)} ${y(b.lo).toFixed(1)}`)
        .join(' L')}Z`
    : ''
  const tickStep = max <= 60 ? 10 : max <= 150 ? 25 : 50
  const ticks = Array.from({ length: Math.floor(max / tickStep) + 1 }, (_, k) => k * tickStep)
  const bw = Math.max(2, ((W - l - r) / n) * 0.62)
  const end = series[n - 1]!
  return (
    <svg class="chart fchart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('costs.fc.chartLabel', { used: sharePct(a.usedPct), end: sharePct(a.forecastPct) })}>
      <defs>
        <linearGradient id="fcg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--c1)" stop-opacity=".4" />
          <stop offset="1" stop-color="var(--c1)" stop-opacity="0" />
        </linearGradient>
      </defs>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={l} x2={W - r} y1={y(v)} y2={y(v)} stroke="var(--hair)" />
          <text x={l - 8} y={y(v) + 4} text-anchor="end">
            {wholePct(v)}
          </text>
        </g>
      ))}
      {bandPath ? <path d={bandPath} fill="color-mix(in srgb,var(--c2) 18%,transparent)" /> : null}
      {usedArea ? <path d={usedArea} fill="url(#fcg)" /> : null}
      {f.thresholds
        .filter((v) => v <= max)
        .map((v) => (
          <g key={`t${v}`}>
            <line x1={l} x2={W - r} y1={y(v)} y2={y(v)} stroke={v >= 100 ? 'var(--bad)' : 'var(--warn)'} stroke-dasharray="5 4" stroke-width="1.2" />
            <text x={W - r + 4} y={y(v) + 4} style={`fill:${v >= 100 ? 'var(--bad)' : 'var(--warn)'}`}>
              {wholePct(v)}
            </text>
          </g>
        ))}
      {fcLine ? <path d={fcLine} fill="none" stroke="var(--c2)" stroke-width="2" stroke-dasharray="6 5" /> : null}
      {usedLine ? <path d={usedLine} fill="none" stroke="var(--c1)" stroke-width="2.2" /> : null}
      {lastUsed >= 0 && lastUsed < n - 1 ? (
        <g>
          <line x1={x(lastUsed)} x2={x(lastUsed)} y1={top} y2={stripB} stroke="var(--mu)" stroke-opacity=".35" />
          <text x={x(lastUsed)} y={top - 8} text-anchor="middle">
            {t('costs.fc.today')}
          </text>
          <circle cx={x(lastUsed)} cy={y(series[lastUsed]!.total)} r="3.5" fill="var(--solid)" stroke="var(--c1)" stroke-width="2" />
        </g>
      ) : null}
      <circle cx={x(n - 1)} cy={y(end.total)} r="4" fill="var(--c2)">
        <title>{t('costs.fc.endTitle', { end: sharePct(a.forecastPct) })}</title>
      </circle>
      {series.map((d, i) => (
        <rect
          key={d.day}
          class={d.kind === 'used' ? 'u' : 'f'}
          x={x(i) - bw / 2}
          y={yd(d.daily)}
          width={bw}
          height={Math.max(0.5, stripB - yd(d.daily))}
          rx="1.5"
        >
          <title>{t(d.kind === 'used' ? 'costs.fc.dayUsed' : 'costs.fc.dayForecast', { day: day(d.day), share: sharePct(d.daily), total: sharePct(d.total) })}</title>
        </rect>
      ))}
      <text x={l - 8} y={stripB} text-anchor="end">
        {t('costs.fc.perDay')}
      </text>
      {series.map((d, i) =>
        (i % 7 === 0 && n - 1 - i >= 4) || i === n - 1 ? (
          <text key={`x${d.day}`} x={x(i)} y={H - 6} text-anchor="middle">
            {Number(d.day.slice(8))} {monthShort(new Date(`${d.day}T12:00:00Z`))}
          </text>
        ) : null,
      )}
    </svg>
  )
}

/** Agents whose stable prompt is long enough for the prompt cache while it is off: the share of input it would serve. */
export function CacheHints({ app }: { app: ConsoleApplication }) {
  const load = useLoad(() => forApp(app.id).admin.costs.cacheSuggestions(), [app.id])
  return (
    <Panel title={t('costs.cache.title')} style="margin-top:16px">
      <p class="hint" style="margin:0 0 12px">
        {t('costs.cache.sub')}
      </p>
      <Loads load={load} what={t('what.cacheHints')} panel={false}>
        {(items: CacheSuggestion[]) =>
          !items.length ? (
            <p class="hint">{t('costs.cache.none')}</p>
          ) : (
            <div class="wx">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>{t('evals.agent')}</th>
                    <th>{t('costs.cmp.model')}</th>
                    <th class="r">{t('costs.cache.stable')}</th>
                    <th class="r">{t('costs.cache.share')}</th>
                    <th>{t('costs.cache.suggested')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((s) => (
                    <tr key={s.agent}>
                      <td>
                        <a href={appHref(app.slug, 'agents', s.agent)}>
                          <b>{s.agent}</b>
                        </a>
                      </td>
                      <td title={s.model}>{modelName(s.model)}</td>
                      <td class="num">
                        {t('costs.cache.tokens', { stable: num(s.stableTokens), min: num(s.minTokens) })}
                      </td>
                      <td class="num">{s.inputSharePct == null ? t('costs.cache.noUsage') : sharePct(s.inputSharePct)}</td>
                      <td>
                        <code>prompt_cache: {s.suggested}</code>
                      </td>
                      <td style="text-align:right">
                        <Btn sm kind="ghost" href={appHref(app.slug, 'agents', s.agent)}>
                          {t('evals.openAgent')}
                        </Btn>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Loads>
    </Panel>
  )
}

const capOwnKey = (p: CapPeriod | undefined) => (p === 'week' ? 'costs.fc.capOwnWeek' : p === 'day' ? 'costs.fc.capOwnDay' : 'costs.fc.capOwn')

/** The application's weekly and daily caps: the share used, the forecast at the end of the window, the reset. */
function OtherCaps({ caps }: { caps: NonNullable<CostForecast['caps']> }) {
  if (!caps.length) return null
  return (
    <Panel title={t('costs.fc.others')}>
      <div class="wx">
        <table class="tbl">
          <thead>
            <tr>
              <th>{t('costs.fc.capCol')}</th>
              <th class="r">{t('costs.fc.usedCol')}</th>
              <th>{t('costs.fc.endCol')}</th>
              <th>{t('costs.fc.resetsCol')}</th>
            </tr>
          </thead>
          <tbody>
            {caps.map((c) => (
              <tr key={c.limit}>
                <td>{t(c.period === 'week' ? 'costs.fc.appWeek' : 'costs.fc.appDay')}</td>
                <td class="num">{sharePct(c.usedPct)}</td>
                <td style="min-width:150px">
                  <div style="display:flex;align-items:center;gap:8px">
                    <span class="num">{sharePct(c.forecastPct)}</span>
                    <Bar value={bar(c.forecastPct)} style="flex:1" />
                  </div>
                </td>
                <td>{dateTime(c.resetsAt)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
