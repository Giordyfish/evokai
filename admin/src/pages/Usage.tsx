/**
 * Usage: tokens, calls and shares of the caps. Spend is priced by the model that answered, but no amount ever reaches
 * the console: the service sends shares of the application's caps (used and left) and this page shows only those.
 */
import { useState } from 'preact/hooks'

import type { ConsoleApplication, ConsoleUsageRow } from '@client/core/types'
import { forApp } from '../api'
import { bar, csv, day, download, modelName, monthShort, monthSoFar, ms, pct1, sharePct } from '../format'
import { num, t } from '../i18n'
import { Empty } from '../magic'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { AppAvatar, Bar, Btn, Hero, Panel, Seg, Tag } from '../ui'
import { Loads } from './common'
import { CacheHints, Forecast } from './Forecast'

const VIEWS = ['day', 'user', 'agent', 'model'] as const
type View = (typeof VIEWS)[number]
const viewLabels = (): Record<View, string> => ({ day: t('usage.v.day'), user: t('usage.v.user'), agent: t('usage.v.agent'), model: t('usage.v.model') })

function monthDays(now = new Date()): string[] {
  const out: string[] = []
  for (let d = 1; d <= now.getDate(); d++) {
    const x = new Date(Date.UTC(now.getFullYear(), now.getMonth(), d))
    out.push(x.toISOString().slice(0, 10))
  }
  return out
}

/** Shares of a cap by day (percent): one area per series, stacked unless the series are shares of different caps. */
export function AreaChart({ series, stack = true }: { series: Array<{ name: string; color: string; values: number[] }>; stack?: boolean }) {
  const days = monthDays()
  const n = Math.max(2, days.length)
  const W = 640
  const H = 220
  const l = 40
  const bt = 26
  const tops = days.map((_, i) =>
    stack ? series.reduce((s, x) => s + (x.values[i] ?? 0), 0) : Math.max(0, ...series.map((x) => x.values[i] ?? 0)),
  )
  const maxRaw = Math.max(0.01, ...tops)
  const step = Math.pow(10, Math.floor(Math.log10(maxRaw)))
  const max = Math.ceil(maxRaw / step) * step
  const x = (i: number) => l + (i * (W - l - 12)) / (n - 1)
  const y = (t: number) => H - bt - (t / max) * (H - bt - 12)
  let base = days.map(() => 0)
  const layers = series.map((s, k) => {
    if (!stack) base = days.map(() => 0)
    const top = base.map((b, i) => b + (s.values[i] ?? 0))
    const line = top.map((t, i) => `${x(i).toFixed(1)} ${y(t).toFixed(1)}`).join(' L')
    const back = base
      .map((t, i) => [i, t] as const)
      .reverse()
      .map(([i, t]) => `${x(i).toFixed(1)} ${y(t).toFixed(1)}`)
      .join(' L')
    base = top
    return { k, s, area: `M${line} L${back}Z`, line: `M${line}` }
  })
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max)
  return (
    <svg class="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('usage.chartLabel')}>
      <defs>
        {series.map((s, k) => (
          <linearGradient key={k} id={`ga${k}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stop-color={s.color} stop-opacity=".5" />
            <stop offset="1" stop-color={s.color} stop-opacity="0" />
          </linearGradient>
        ))}
      </defs>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={l} x2={W - 12} y1={y(v)} y2={y(v)} stroke="var(--hair)" />
          <text x={l - 8} y={y(v) + 4} text-anchor="end">
            {num(v, { maximumFractionDigits: v < 1 ? 2 : v < 10 ? 1 : 0, minimumFractionDigits: v < 1 ? 2 : v < 10 ? 1 : 0 })}%
          </text>
        </g>
      ))}
      {layers.map((L) => (
        <g key={L.k}>
          <path d={L.area} fill={`url(#ga${L.k})`} />
          <path d={L.line} fill="none" stroke={L.s.color} stroke-width={L.k ? 1.6 : 2} />
        </g>
      ))}
      {days.map((d, i) =>
        i % Math.ceil(days.length / 8) ? null : (
          <text key={d} x={x(i)} y={H - 6} text-anchor="middle">
            {Number(d.slice(8))} {monthShort(new Date(d))}
          </text>
        ),
      )}
    </svg>
  )
}

const seriesColors = ['var(--c1)', 'var(--c2)', 'var(--e-chart)', 'var(--e-confirm)', 'var(--e-usage)']

/** Each day of the month so far, as its share of the monthly cap. */
function byDay(rows: ConsoleUsageRow[]): number[] {
  const m = new Map(rows.map((r) => [r.day, r.capPct ?? 0]))
  return monthDays().map((d) => m.get(d) ?? 0)
}

const sum = (rows: ConsoleUsageRow[], key: 'capPct' | 'subjectCapPct'): number | null =>
  rows.some((r) => r[key] != null) ? Math.round(rows.reduce((s, r) => s + (r[key] ?? 0), 0) * 100) / 100 : null

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <Panel>
      <div class="hint" style="margin:0">
        {label}
      </div>
      <b style="font:600 28px var(--disp);letter-spacing:-.03em">{value}</b>
    </Panel>
  )
}

export function AppUsage({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.usage'), app.name)
  const api = forApp(app.id)
  const [view, setView] = useState<View>('day')
  const [tag, setTag] = useState('')
  const load = useLoad(async () => {
    const only = tag || undefined
    const [days, users, agents, models, summary] = await Promise.all([
      api.consoleUsage({ groupBy: 'day', tag: only }),
      api.consoleUsage({ groupBy: ['subject', 'tags'], tag: only }),
      api.consoleUsage({ groupBy: 'agent', tag: only }),
      api.consoleUsage({ groupBy: 'model', tag: only }),
      api.admin.usageSummary({ tag: only }),
    ])
    return { days, users, agents, models, summary }
  }, [app.id, tag])

  const exportCsv = () => {
    const d = load.data
    if (!d) return
    const rows: Array<Array<string | number | null | undefined>> =
      view === 'day'
        ? [
            ['day', 'calls', 'input tokens', 'cache read tokens', 'cache write tokens', 'output tokens', '% of monthly cap', '% of daily cap'],
            ...d.days.rows.map((r) => [r.day, r.calls, r.inputTokens, r.cacheReadTokens, r.cacheWriteTokens, r.outputTokens, r.capPct, r.dayCapPct]),
          ]
        : view === 'user'
          ? [
              ['subject', 'tags', 'calls', '% of monthly cap', '% of user month', '% of user day (today)'],
              ...d.users.rows.map((r) => [r.subject, (r.tags ?? []).join(' '), r.calls, r.capPct, r.subjectCapPct, r.subjectTodayPct]),
            ]
          : view === 'agent'
            ? [['agent', 'calls', '% of monthly cap'], ...d.agents.rows.map((r) => [r.agent, r.calls, r.capPct])]
            : [['model', 'calls', '% of monthly cap'], ...d.models.rows.map((r) => [r.model, r.calls, r.capPct])]
    download(`${app.slug}-usage-${view}.csv`, csv(rows), 'text/csv')
  }

  return (
    <>
      <Hero title={t('tab.usage')} sub={t('usage.sub')} actions={<Btn onClick={exportCsv}>{t('usage.export')}</Btn>} />
      <Forecast app={app} />
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:16px" data-tour="usage-views">
        <Seg options={VIEWS} labels={viewLabels()} value={view} onChange={setView} />
        <select class="in" style="width:auto;height:36px" value={tag} onChange={(e) => setTag(e.currentTarget.value)} aria-label={t('usage.tag')}>
          <option value="">{t('usage.allTags')}</option>
          {(load.data?.summary.tags ?? (tag ? [tag] : [])).map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
        <span style="margin-left:auto" class="hint">
          {monthSoFar()}
        </span>
      </div>
      <Loads load={load} what={t('what.usage')}>
        {(d) => {
          const calls = d.days.rows.reduce((s, r) => s + r.calls, 0)
          if (!d.summary.turns && !calls)
            return (
              <Panel>
                <Empty k="ember">
                  <b>{t('usage.empty')}</b>
                  <br />
                  {t('usage.emptyText')}
                </Empty>
              </Panel>
            )
          const caps = d.users.caps
          const used = tag ? sum(d.days.rows, 'capPct') : d.days.application.monthPct
          const merged = new Map<string, { tags: Set<string>; rows: ConsoleUsageRow[]; calls: number; today: number | null }>()
          for (const r of d.users.rows) {
            const m = merged.get(r.subject ?? '') ?? { tags: new Set<string>(), rows: [], calls: 0, today: null }
            for (const x of r.tags ?? []) m.tags.add(x)
            m.rows.push(r)
            m.calls += r.calls
            m.today = r.subjectTodayPct ?? m.today
            merged.set(r.subject ?? '', m)
          }
          const users = [...merged.entries()]
            .map(([s, m]) => ({ subject: s, ...m, cap: sum(m.rows, 'capPct'), month: sum(m.rows, 'subjectCapPct') }))
            .sort((a, b) => (b.cap ?? 0) - (a.cap ?? 0))
          const simple = (rows: ConsoleUsageRow[], keyOf: (r: ConsoleUsageRow) => string, fmt: (k: string) => string = (k) => k) => (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th />
                      <th class="r">{t('usage.calls')}</th>
                      <th class="r">{t('usage.ofMonthly')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {[...rows]
                      .sort((a, b) => (b.capPct ?? 0) - (a.capPct ?? 0))
                      .map((r) => (
                        <tr key={keyOf(r)}>
                          <td>
                            <code>{fmt(keyOf(r))}</code>
                          </td>
                          <td class="num">{num(r.calls)}</td>
                          <td class="num">{sharePct(r.capPct)}</td>
                          <td style="width:30%">
                            <Bar value={bar(r.capPct)} />
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )
          return (
            <>
              <div class="grid g4" style="margin-bottom:16px">
                <Kpi label={tag ? t('usage.usedByTag', { tag }) : t('usage.usedMonthly')} value={sharePct(used)} />
                <Kpi label={t('usage.leftMonth')} value={sharePct(d.days.application.monthLeftPct)} />
                <Kpi label={t('usage.turns')} value={num(d.summary.turns)} />
                <Kpi label={t('usage.fallback')} value={d.summary.turns ? pct1((d.summary.fallbackTurns / d.summary.turns) * 100) : t('fmt.none')} />
              </div>
              <div class="grid g4" style="margin-bottom:16px">
                <Kpi label={t('usage.usedToday')} value={sharePct(d.days.application.dayPct, t('usage.noDaily'))} />
                <Kpi label={t('usage.leftToday')} value={sharePct(d.days.application.dayLeftPct, t('usage.noDaily'))} />
                <Kpi label={t('usage.firstWord')} value={ms(d.summary.medianFirstTokenMs)} />
                <Kpi label={t('usage.subjects')} value={num(d.summary.subjects)} />
              </div>
              <Panel title={t('usage.shareByDay')}>
                <AreaChart series={[{ name: app.name, color: 'var(--c1)', values: byDay(d.days.rows) }]} />
              </Panel>
              <div style="margin-top:16px">
                {view === 'day' ? (
                  <Panel cls="flat">
                    <div class="wx">
                      <table class="tbl">
                        <thead>
                          <tr>
                            <th>{t('usage.day')}</th>
                            <th class="r">{t('usage.calls')}</th>
                            <th class="r">{t('usage.tokensInOut')}</th>
                            <th class="r" title={t('usage.cacheTitle')}>
                              {t('usage.cache')}
                            </th>
                            <th class="r">{t('usage.ofMonthly')}</th>
                            {caps.daily ? <th class="r">{t('usage.ofDaily')}</th> : null}
                          </tr>
                        </thead>
                        <tbody>
                          {[...d.days.rows].reverse().map((r) => (
                            <tr key={r.day}>
                              <td>{r.day ? day(r.day) : ''}</td>
                              <td class="num">{num(r.calls)}</td>
                              <td class="num">
                                {num(r.inputTokens)} / {num(r.outputTokens)}
                              </td>
                              <td class="num">
                                {num(r.cacheReadTokens)} / {num(r.cacheWriteTokens)}
                              </td>
                              <td class="num">{sharePct(r.capPct)}</td>
                              {caps.daily ? <td class="num">{sharePct(r.dayCapPct)}</td> : null}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Panel>
                ) : view === 'user' ? (
                  <Panel cls="flat">
                    <div class="wx">
                      <table class="tbl">
                        <thead>
                          <tr>
                            <th>{t('usage.subject')}</th>
                            <th>{t('usage.tags')}</th>
                            <th class="r">{t('usage.calls')}</th>
                            <th class="r">{t('usage.ofMonthly')}</th>
                            {caps.subjectMonthly ? <th>{t('usage.ofUserMonth')}</th> : null}
                            {caps.subjectDaily ? <th class="r">{t('usage.todayUserDay')}</th> : null}
                          </tr>
                        </thead>
                        <tbody>
                          {users.map((u) => (
                            <tr key={u.subject}>
                              <td>
                                <code>{u.subject}</code>
                              </td>
                              <td>
                                {[...u.tags].map((x) => (
                                  <Tag key={x}>{x}</Tag>
                                ))}
                              </td>
                              <td class="num">{num(u.calls)}</td>
                              <td class="num">{sharePct(u.cap)}</td>
                              {caps.subjectMonthly ? (
                                <td>
                                  <div style="display:flex;align-items:center;gap:8px">
                                    <span class="num">{sharePct(u.month)}</span>
                                    <Bar value={bar(u.month)} style="flex:1" />
                                  </div>
                                </td>
                              ) : null}
                              {caps.subjectDaily ? <td class="num">{sharePct(u.today)}</td> : null}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </Panel>
                ) : view === 'agent' ? (
                  simple(d.agents.rows, (r) => r.agent ?? '')
                ) : (
                  simple(d.models.rows, (r) => r.model ?? '', modelName)
                )}
              </div>
            </>
          )
        }}
      </Loads>
      <CacheHints app={app} />
    </>
  )
}

export function AllUsage() {
  setTitle(t('page.usage'))
  const c = useConsole()
  const load = useLoad(
    () =>
      Promise.all(
        c.apps.map(async (a) => {
          const api = forApp(a.id)
          const [days, summary] = await Promise.all([api.consoleUsage({ groupBy: 'day' }), api.admin.usageSummary()])
          return { app: a, days, summary }
        }),
      ),
    [c.apps.map((a) => a.id).join()],
  )
  const exportCsv = () => {
    if (!load.data) return
    const rows: Array<Array<string | number | null>> = [['application', 'day', 'calls', '% of monthly cap']]
    for (const x of load.data) for (const r of x.days.rows) rows.push([x.app.slug, r.day ?? '', r.calls, r.capPct])
    download('evokai-usage.csv', csv(rows), 'text/csv')
  }
  return (
    <>
      <Hero title={t('page.usage')} sub={t('usage.allSub')} actions={<Btn onClick={exportCsv}>{t('usage.export')}</Btn>} />
      {!c.apps.length ? (
        <Panel tour="usage-all">
          <Empty k="ember">
            <b>{t('apps.empty')}</b>
          </Empty>
        </Panel>
      ) : (
        <Loads load={load} what={t('what.usage')}>
          {(d) => {
            const turns = d.reduce((s, x) => s + x.summary.turns, 0)
            const fb = d.reduce((s, x) => s + x.summary.fallbackTurns, 0)
            const medians = d.map((x) => x.summary.medianFirstTokenMs).filter((v): v is number => v != null)
            const top = [...d].sort((a, b) => (b.days.application.monthPct ?? 0) - (a.days.application.monthPct ?? 0))[0]
            return (
              <>
                <div class="grid g2" style="margin-bottom:16px" data-tour="usage-all">
                  {d.map(({ app, days }) => (
                    <Panel key={app.id}>
                      <div style="display:flex;align-items:center;gap:12px;cursor:pointer" onClick={() => go(appHref(app.slug, 'usage'))}>
                        <AppAvatar slug={app.slug} name={app.name} />
                        <div style="flex:1">
                          <b>{app.name}</b>
                          <Bar value={bar(days.application.monthPct)} style="margin-top:8px" />
                        </div>
                        <span class="mono">
                          {days.application.monthPct != null
                            ? t('usage.usedLeft', { used: sharePct(days.application.monthPct), left: sharePct(days.application.monthLeftPct) })
                            : t('fmt.noCap')}
                        </span>
                      </div>
                    </Panel>
                  ))}
                </div>
                <div class="grid g4" style="margin-bottom:16px">
                  <Kpi label={top ? t('usage.mostUsedApp', { app: top.app.name }) : t('usage.mostUsed')} value={sharePct(top?.days.application.monthPct)} />
                  <Kpi label={t('usage.turns')} value={num(turns)} />
                  <Kpi label={t('usage.fallback')} value={turns ? pct1((fb / turns) * 100) : t('fmt.none')} />
                  <Kpi label={t('usage.firstWord')} value={ms(medians.length ? medians.sort((a, b) => a - b)[Math.floor(medians.length / 2)] : null)} />
                </div>
                <Panel
                  title={t('usage.shareEach')}
                  actions={d.map((x, i) => (
                    <Tag key={x.app.id} tone={i % 2 ? 'teal' : 'c1'} dot>
                      {x.app.name}
                    </Tag>
                  ))}
                >
                  <AreaChart
                    stack={false}
                    series={d.map((x, i) => ({ name: x.app.name, color: seriesColors[i % seriesColors.length]!, values: byDay(x.days.rows) }))}
                  />
                </Panel>
              </>
            )
          }}
        </Loads>
      )}
    </>
  )
}
