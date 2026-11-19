/**
 * The red team of one agent, on its Evals page: the security score of the active version and its trend, the gate,
 * the runs, each run's failures with the masked answers, the categories and the comparison with the previous version,
 * and the versions the gate holds (an owner publishes them). Figures are scores, counts and shares, never amounts.
 */
import { useEffect, useState } from 'preact/hooks'

import type { ConsoleApplication, RedTeamResult, RedTeamRunDetail, RedTeamRuns } from '@client/core/types'
import { describe, EvokaiError, forApp } from '../api'
import { ago, dateTime, sharePct } from '../format'
import { t, tn } from '../i18n'
import { sparkle, toast } from '../magic'
import {
  byOutcome,
  categoryHint,
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
  severityLabel,
  trendGeometry,
} from '../redteam'
import { appHref, go, useLoad } from '../state'
import { Bar, Btn, KV, Panel, Tag, type Tone } from '../ui'
import { Loads } from './common'
import { statusTag } from './run-status'

const sevTone = (s: string): Tone => (s === 'high' ? 'bad' : s === 'medium' ? 'warn' : '')
const modeTone = (m: string): Tone => (m === 'enforce' ? 'ok' : m === 'monitor' ? 'gold' : '')

/** The score as a tag, coloured against the gate's minimum. */
function ScoreTag({ score, min }: { score: number | null | undefined; min: number }) {
  if (score == null) return <span class="s">-</span>
  const tone = scoreTone(score, min)
  return <Tag tone={tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : tone === 'bad' ? 'bad' : ''}>{scoreText(score)}</Tag>
}

export function RedTeamSection(p: {
  app: ConsoleApplication
  agent: string
  runId: string | null
  /** The page's address with this red team run open (null closes it). */
  href: (runId: string | null) => string
  /** Changes when something else saved a version (a model switch): the lists are read again. */
  bump: number
  /** A version was published here: the page's own lists change too. */
  onChanged: () => void
}) {
  const { app, agent, runId } = p
  const api = forApp(app.id)
  const [starting, setStarting] = useState(false)
  const load = useLoad(() => api.admin.redteam.runs(agent), [app.id, agent, p.bump])
  const running = load.data?.items.some((r) => r.status === 'running') ?? false
  useEffect(() => {
    if (runId && load.data && !load.data.items.some((r) => r.id === runId)) void load.reload()
  }, [runId, load.data])
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void load.reload(), 2000)
    return () => clearInterval(timer)
  }, [running])

  const start = async (e: Event) => {
    const el = e.currentTarget as Element
    setStarting(true)
    try {
      const id = await api.admin.redteam.start(agent)
      sparkle(el)
      toast(t('rt.started'), t('rt.startedSub'))
      go(p.href(id))
      await load.reload()
    } catch (err) {
      if (err instanceof EvokaiError && err.code === 'redteam_running') {
        toast(t('rt.already'), t('rt.alreadySub'), null)
        await load.reload()
      } else toast(t('rt.notStarted'), describe(err), null)
    } finally {
      setStarting(false)
    }
  }

  return (
    <Loads load={load} what={t('what.redteam')}>
      {(runs) => (
        <>
          <Panel
            title={
              <>
                {t('rt.title')}
                <Tag tone={modeTone(runs.gate.mode)} dot title={t('rt.gateMode')}>
                  {runs.gate.mode}
                </Tag>
              </>
            }
            actions={
              <Btn sm kind="pri" busy={starting ? t('evals.starting') : running ? t('evals.running') : false} onClick={(e) => void start(e)}>
                {t('rt.run')}
              </Btn>
            }
          >
            <p class="hint" style="margin:0 0 14px">
              {t('rt.sub')}
            </p>
            <Warning runs={runs} />
            <Summary app={app} runs={runs} />
          </Panel>
          <Versions app={app} agent={agent} runs={runs} onChanged={async () => {
            await load.reload()
            p.onChanged()
          }} />
          <Panel title={t('rt.runs')}>
            {!runs.items.length ? (
              <p class="hint">{t('rt.noRuns')}</p>
            ) : (
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('evals.when')}</th>
                      <th>{t('evals.status')}</th>
                      <th>{t('evals.version')}</th>
                      <th class="r">{t('rt.score')}</th>
                      <th class="r">{t('rt.failedCol')}</th>
                      <th class="r">{t('evals.budgetCol')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.items.map((r) => (
                      <tr key={r.id} data-go aria-current={r.id === runId ? 'true' : undefined} onClick={() => go(p.href(r.id))}>
                        <td>
                          {ago(r.startedAt)} <span class="s">{t(r.trigger === 'save' ? 'evals.onSave' : 'evals.onDemand')}</span>
                        </td>
                        <td>{statusTag(r.status)}</td>
                        <td>
                          <code>{r.version.slice(0, 7)}</code>
                        </td>
                        <td class="num">
                          <ScoreTag score={r.score} min={runs.gate.minScore} />
                        </td>
                        <td class="num">
                          {r.summary.total != null ? t('rt.failedOf', { failed: r.summary.failed ?? 0, total: r.summary.total }) : '-'}
                        </td>
                        <td class="num">{sharePct(r.budgetPct)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p class="hint">{t('rt.budgetNote', { used: sharePct(runs.budget.usedPct) })}</p>
          </Panel>
          {runId ? <RunDetail key={runId} app={app} runId={runId} min={runs.gate.minScore} onClose={() => go(p.href(null))} /> : null}
        </>
      )}
    </Loads>
  )
}

/** The gate's warning: the active version scores below the minimum (`monitor` publishes it anyway, `enforce` held the next ones). */
function Warning({ runs }: { runs: RedTeamRuns }) {
  const w = gateWarning(runs)
  if (!w) return null
  return (
    <div class="callout warn" role="status">
      <span>
        <b>{t('rt.warn.title')}</b> {t(w.mode === 'enforce' ? 'rt.warn.enforce' : 'rt.warn.monitor', { score: scoreText(w.score), min: scoreText(w.min), version: w.version.slice(0, 7) })}
      </span>
    </div>
  )
}

function Summary({ app, runs }: { app: ConsoleApplication; runs: RedTeamRuns }) {
  const active = runs.versions.find((v) => v.active) ?? null
  // The latest scored version saved before the active one (a held version saved after it does not count).
  const before = active
    ? runs.versions
        .filter((v) => v.score != null && v.createdAt < active.createdAt)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]
    : undefined
  const delta = active?.score != null && before?.score != null ? active.score - before.score : null
  const { gate } = runs
  return (
    <div class="rtsum">
      <div>
        <span class="lbl">{t('rt.activeScore')}</span>
        <div class="score" data-tone={scoreTone(active?.score, gate.minScore)}>
          {scoreText(active?.score)}
          {active?.score != null ? <small>/100</small> : null}
        </div>
        <p class="hint">
          {active?.score == null
            ? t(active ? 'rt.notScored' : 'rt.noActive')
            : delta != null
              ? t('rt.vsBefore', { pts: pointsText(delta), version: before!.version.slice(0, 7) })
              : t('rt.firstScore')}
        </p>
      </div>
      <div>
        <span class="lbl">{t('rt.trend')}</span>
        <TrendChart trend={runs.trend} min={gate.minScore} showMin={gate.mode !== 'off'} />
      </div>
      <div>
        <span class="lbl">{t('rt.gate')}</span>
        <KV
          rows={[
            [t('rt.gateMode'), <Tag tone={modeTone(gate.mode)}>{gate.mode}</Tag>],
            [t('rt.minScore'), scoreText(gate.minScore)],
            [t('rt.onSave'), t(gate.onSave ? 'rt.yes' : 'rt.no')],
            [t('rt.judge'), <Tag tone={gate.judge ? 'ok' : ''} dot>{t(gate.judge ? 'rt.judgeOn' : 'rt.judgeOff')}</Tag>],
          ]}
        />
        <p class="hint">
          {gate.judge ? t('rt.judgeNote') : t('rt.noJudgeNote')} <a href={appHref(app.slug, 'security')}>{t('rt.changeGate')}</a>
        </p>
      </div>
    </div>
  )
}

/** The score of each finished run, oldest first, with the gate's minimum as a dashed line. */
export function TrendChart({ trend, min, showMin }: { trend: RedTeamRuns['trend']; min: number; showMin: boolean }) {
  if (!trend.length) return <p class="hint">{t('rt.noTrend')}</p>
  const W = 420
  const H = 132
  const g = trendGeometry(trend, min, { w: W, h: H, left: 30, right: 10, top: 10, bottom: 22 })
  const ticks = [g.lo, Math.round((g.lo + 100) / 2), 100]
  return (
    <svg class="chart rtchart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('rt.trendLabel', { n: trend.length })}>
      <defs>
        <linearGradient id="rtg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--c1)" stop-opacity=".35" />
          <stop offset="1" stop-color="var(--c1)" stop-opacity="0" />
        </linearGradient>
      </defs>
      {ticks.map((v) => (
        <g key={v}>
          <line x1={30} x2={W - 10} y1={g.y(v)} y2={g.y(v)} stroke="var(--hair)" />
          <text x={24} y={g.y(v) + 4} text-anchor="end">
            {v}
          </text>
        </g>
      ))}
      {showMin ? (
        <g>
          <line x1={30} x2={W - 10} y1={g.minY} y2={g.minY} stroke="var(--warn)" stroke-dasharray="4 4" />
          <text x={W - 12} y={g.minY - 4} text-anchor="end" style="fill:var(--warn)">
            {t('rt.minLine', { min: scoreText(min) })}
          </text>
        </g>
      ) : null}
      {g.area ? <path d={g.area} fill="url(#rtg)" /> : null}
      <path d={g.line} fill="none" stroke="var(--c1)" stroke-width="2" />
      {g.points.map((pt, i) => (
        <circle key={i} cx={pt.x} cy={pt.y} r="3.5" fill="var(--solid)" stroke={pt.score < min ? 'var(--bad)' : 'var(--c1)'} stroke-width="2">
          <title>{t('rt.point', { version: pt.version.slice(0, 7), score: scoreText(pt.score), when: dateTime(pt.at) })}</title>
        </circle>
      ))}
      {g.points.length > 1 ? (
        <>
          <text x={g.points[0]!.x} y={H - 6} text-anchor="start">
            {g.points[0]!.version.slice(0, 7)}
          </text>
          <text x={g.points[g.points.length - 1]!.x} y={H - 6} text-anchor="end">
            {g.points[g.points.length - 1]!.version.slice(0, 7)}
          </text>
        </>
      ) : (
        <text x={g.points[0]!.x} y={H - 6} text-anchor="middle">
          {g.points[0]!.version.slice(0, 7)}
        </text>
      )}
    </svg>
  )
}

/** The versions with their latest score and publish state; an owner publishes one (overriding the gate after a confirmation). */
function Versions({ app, agent, runs, onChanged }: { app: ConsoleApplication; agent: string; runs: RedTeamRuns; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null)
  const owner = app.role === 'owner'
  const held = runs.versions.filter((v) => v.publishState)
  if (runs.versions.length < 2 && !held.length) return null
  const publish = async (v: RedTeamRuns['versions'][number], forced = needsForce(v.publishState, runs.gate.mode)) => {
    const vars = { version: v.version.slice(0, 7), score: scoreText(v.score), min: scoreText(runs.gate.minScore) }
    if (forced && !confirm(t(v.publishState === 'pending' ? 'rt.confirmPending' : 'rt.confirmBlocked', vars))) return
    setBusy(v.version)
    try {
      await forApp(app.id).admin.redteam.publish(agent, v.version, forced ? { force: true } : {})
      toast(t('rt.published'), t('rt.publishedSub', vars))
      await onChanged()
    } catch (err) {
      if (!forced && err instanceof EvokaiError && err.code === 'redteam_gate') {
        setBusy(null)
        return publish(v, true)
      }
      toast(t('rt.notPublished'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }
  return (
    <Panel title={t('rt.versions')} actions={held.length ? <Tag tone="warn">{tn('rt.heldCount', held.length)}</Tag> : null}>
      <div class="wx">
        <table class="tbl">
          <thead>
            <tr>
              <th>{t('evals.version')}</th>
              <th>{t('rt.saved')}</th>
              <th class="r">{t('rt.score')}</th>
              <th>{t('rt.state')}</th>
              {owner ? <th /> : null}
            </tr>
          </thead>
          <tbody>
            {runs.versions.map((v) => (
              <tr key={v.version}>
                <td>
                  <code>{v.version.slice(0, 7)}</code>
                </td>
                <td>{ago(v.createdAt)}</td>
                <td class="num">
                  <ScoreTag score={v.score} min={runs.gate.minScore} />
                </td>
                <td>
                  {v.active ? (
                    <Tag tone="ok" dot>
                      {t('rt.st.active')}
                    </Tag>
                  ) : v.publishState === 'pending' ? (
                    <Tag tone="vi" dot title={t('rt.st.pendingHint')}>
                      {t('rt.st.pending')}
                    </Tag>
                  ) : v.publishState === 'blocked' ? (
                    <Tag tone="bad" dot title={t('rt.st.blockedHint', { min: scoreText(runs.gate.minScore) })}>
                      {t('rt.st.blocked')}
                    </Tag>
                  ) : (
                    <span class="s">{t('rt.st.older')}</span>
                  )}
                </td>
                {owner ? (
                  <td style="text-align:right">
                    {!v.active ? (
                      <Btn sm kind={v.publishState ? 'pri' : 'ghost'} busy={busy === v.version ? t('rt.publishing') : false} onClick={() => void publish(v)}>
                        {t(v.publishState === 'blocked' ? 'rt.publishAnyway' : 'rt.publish')}
                      </Btn>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p class="hint">{t(owner ? 'rt.versionsNote' : 'rt.versionsOwner')}</p>
    </Panel>
  )
}

function RunDetail({ app, runId, min, onClose }: { app: ConsoleApplication; runId: string; min: number; onClose: () => void }) {
  const load = useLoad(() => forApp(app.id).admin.redteam.run(runId), [app.id, runId])
  const running = load.data?.status === 'running'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void load.reload(), 2000)
    return () => clearInterval(timer)
  }, [running])
  return (
    <Loads load={load} what={t('what.redteamRun')}>
      {(run: RedTeamRunDetail) => {
        const groups = byOutcome(run.results)
        const cmp = comparisonText(run.comparison)
        const newly = new Set(run.comparison?.newlyFailing ?? [])
        const cats = categoryRows(run.summary.byCategory)
        return (
          <Panel
            title={
              <>
                {t('rt.runOf', { version: run.version.slice(0, 7), when: ago(run.startedAt) })} {statusTag(run.status)}
                <ScoreTag score={run.score} min={min} />
              </>
            }
            actions={
              <Btn sm kind="ghost" onClick={onClose}>
                {t('common.close')}
              </Btn>
            }
          >
            {run.status === 'limit' ? <p class="hint">{t('rt.limitNote')}</p> : null}
            {run.belowGate ? (
              <div class="callout bad" role="status">
                <span>{t('rt.belowGate', { score: scoreText(run.score), min: scoreText(min) })}</span>
              </div>
            ) : null}
            {cmp ? (
              <div class={`callout ${cmp.tone || 'vi'}`}>
                <span>
                  <b>{cmp.head}</b>
                  {cmp.fixed ? <> · {cmp.fixed}</> : null}
                  {cmp.failing ? <> · {cmp.failing}</> : null}
                  {run.comparison?.newlyFailing.length ? (
                    <span class="rtids">
                      {run.comparison.newlyFailing.map((id) => (
                        <code key={id}>{id}</code>
                      ))}
                    </span>
                  ) : null}
                </span>
              </div>
            ) : run.previous ? (
              <p class="hint">{t('rt.noPrevRun', { version: run.previous.slice(0, 7) })}</p>
            ) : null}
            {!run.results.length ? <p class="hint">{t(running ? 'rt.runningFirst' : 'rt.noneRan')}</p> : null}
            {cats.length ? (
              <div class="rtcats">
                <span class="lbl">{t('rt.byCategory')}</span>
                <div class="wx">
                  <table class="tbl">
                    <tbody>
                      {cats.map((c) => (
                        <tr key={c.id}>
                          <td title={categoryHint(c.id)}>
                            {categoryLabel(c.id)}
                            {c.tally.failed ? (
                              <>
                                {' '}
                                <Tag tone="bad">{tn('rt.failedN', c.tally.failed)}</Tag>
                              </>
                            ) : null}
                          </td>
                          <td class="num">{t('rt.heldOf', { held: c.tally.passed, total: c.tally.passed + c.tally.failed })}</td>
                          <td style="width:34%">
                            <Bar value={c.heldPct ?? 0} />
                          </td>
                          <td class="num">
                            <ScoreTag score={c.tally.score} min={min} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
            {groups.failed.length ? (
              <>
                <span class="lbl" style="margin-top:16px">
                  {tn('rt.failures', groups.failed.length)}
                </span>
                <div class="grid">
                  {groups.failed.map((r) => (
                    <Attack key={r.id} r={r} newly={newly.has(r.id)} />
                  ))}
                </div>
              </>
            ) : run.results.length && !running ? (
              <p class="hint">{t('rt.allHeld')}</p>
            ) : null}
            {groups.passed.length ? (
              <details class="rtmore">
                <summary>{tn('rt.held', groups.passed.length)}</summary>
                <div class="grid">
                  {groups.passed.map((r) => (
                    <Attack key={r.id} r={r} newly={false} />
                  ))}
                </div>
              </details>
            ) : null}
            {groups.skipped.length ? (
              <details class="rtmore">
                <summary>{tn('rt.skipped', groups.skipped.length)}</summary>
                <div class="grid">
                  {groups.skipped.map((r) => (
                    <Attack key={r.id} r={r} newly={false} />
                  ))}
                </div>
              </details>
            ) : null}
          </Panel>
        )
      }}
    </Loads>
  )
}

/** One attack: what was sent (and injected), how each grader judged it, the masked answer and the tools called. */
function Attack({ r, newly }: { r: RedTeamResult; newly: boolean }) {
  const outcome =
    r.outcome === 'failed' ? (
      <Tag tone="bad" dot>
        {t('rt.o.failed')}
      </Tag>
    ) : r.outcome === 'passed' ? (
      <Tag tone="ok" dot>
        {t('rt.o.passed')}
      </Tag>
    ) : (
      <Tag>{t('rt.o.skipped')}</Tag>
    )
  return (
    <article class={`evres rtatk ${r.outcome === 'failed' ? 'failed' : ''}`}>
      <h4>
        {categoryLabel(r.category)} {outcome}
        <Tag tone={sevTone(r.severity)}>{severityLabel(r.severity)}</Tag>
        <Tag>{languageName(r.language)}</Tag>
        {newly ? <Tag tone="warn">{t('rt.newly')}</Tag> : null}
        <code class="s">{r.id}</code>
      </h4>
      <span class="lbl">{t('rt.attack')}</span>
      <div class="evdiff">{r.text}</div>
      {r.injected ? (
        <>
          <span class="lbl" style="margin-top:10px">
            {t(r.vector === 'tool' ? 'rt.injectedTool' : 'rt.injectedPage')}
          </span>
          <div class="evdiff rtinj">{r.injected}</div>
        </>
      ) : null}
      <span class="lbl" style="margin-top:10px">
        {t('rt.graders')}
      </span>
      <ul class="evchk">
        {r.checks.map((c, i) => (
          <li key={i} data-o={c.outcome}>
            <i aria-hidden="true" />
            <span>
              <b>{graderLabel(c.type)}</b>
              {c.detail ? <span class="s">: {c.detail}</span> : null}
            </span>
          </li>
        ))}
        {r.error ? <li data-o="failed">{r.error}</li> : null}
      </ul>
      <span class="lbl">{t('rt.answer')}</span>
      <div class="evdiff">{r.answer || <span class="s">{t('evals.noAnswer')}</span>}</div>
      <div class="s" style="margin-top:8px">
        {r.tools.length ? (
          <>
            {t('rt.toolsCalled')}{' '}
            {r.tools.map((x, i) => (
              <code key={i}>{x.name}</code>
            ))}
          </>
        ) : (
          t('rt.noTools')
        )}
        {r.reason === 'confirm' ? ` ${t('rt.stoppedConfirm')}` : ''}
      </div>
    </article>
  )
}
