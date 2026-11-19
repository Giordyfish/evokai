/**
 * Evals: test cases per agent, run on demand and when a new version is saved, compared with a baseline version.
 * Figures are counts and shares: pass rate, latency, tokens against the baseline, and the run's share of the
 * application's eval budget. Never an amount.
 */
import { useEffect, useState } from 'preact/hooks'

import type { ConsoleApplication, EvalCase, EvalCaseResult, EvalCheck, EvalRunDetail } from '@client/core/types'
import { describe, forApp } from '../api'
import { wordDiff } from '../diff'
import { ago, ms, pl } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Switch, Tag } from '../ui'
import { Loads } from './common'
import { ModelChoice } from './ModelChoice'
import { RedTeamSection } from './RedTeam'
import { statusTag } from './run-status'
import { num, t, type TextKey } from '../i18n'

const CHECK_TYPES: Array<EvalCheck['type']> = ['contains', 'not_contains', 'regex', 'json_schema', 'tool_called', 'tool_not_called', 'rubric']

const blank = (type: EvalCheck['type']): EvalCheck => {
  switch (type) {
    case 'regex':
      return { type, pattern: '' }
    case 'json_schema':
      return { type, schema: { type: 'object' } }
    case 'tool_called':
    case 'tool_not_called':
      return { type, tool: '' }
    case 'rubric':
      return { type, rubric: '' }
    default:
      return { type, value: '' }
  }
}

export function checkText(c: EvalCheck): string {
  switch (c.type) {
    case 'contains':
      return t('evals.chk.contains', { value: c.value })
    case 'not_contains':
      return t('evals.chk.notContains', { value: c.value })
    case 'regex':
      return t('evals.chk.regex', { pattern: c.pattern })
    case 'json_schema':
      return t('evals.chk.schema')
    case 'tool_called':
      return t('evals.chk.called', { tool: c.tool })
    case 'tool_not_called':
      return t('evals.chk.notCalled', { tool: c.tool })
    case 'rubric':
      return t('evals.chk.rubric', { rubric: c.rubric })
  }
}

const one = (v: number) => num(Math.round(v * 10) / 10, { maximumFractionDigits: 1 })
const pctText = (v: number | null | undefined) => (v == null ? '-' : `${one(v)}%`)
const signed = (v: number | null | undefined, unit = '') => (v == null ? '-' : `${v > 0 ? '+' : ''}${one(v)}${unit}`)
const pts = (v: number | null | undefined) => (v == null ? '-' : t('evals.pts', { v: signed(v) }))

export function Evals({ app, id, q }: { app: ConsoleApplication; id: string | null; q: URLSearchParams }) {
  setTitle(id ? t('evals.of', { agent: id }) : t('tab.evals'), app.name)
  return id ? <AgentEvals app={app} agent={id} q={q} /> : <EvalsHome app={app} />
}

function EvalsHome({ app }: { app: ConsoleApplication }) {
  const api = forApp(app.id)
  const load = useLoad(async () => {
    const agents = await api.admin.agents.list()
    const runs = await Promise.all(agents.map((g) => api.admin.evals.runs(g.name, { limit: 1 }).catch(() => null)))
    const cases = await Promise.all(agents.map((g) => api.admin.evals.cases(g.name).catch(() => [] as EvalCase[])))
    return agents.map((g, i) => ({ agent: g, last: runs[i]?.items[0] ?? null, cases: cases[i] ?? [] }))
  }, [app.id])
  return (
    <>
      <Hero title={t('tab.evals')} sub={t('evals.sub')} />
      <Loads load={load} what={t('what.evals')}>
        {(rows) =>
          !rows.length ? (
            <Panel>
              <Empty>
                <b>{t('evals.noAgents')}</b>
                <br />
                {t('evals.noAgentsText')}
              </Empty>
            </Panel>
          ) : (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('evals.agent')}</th>
                      <th class="r">{t('evals.cases')}</th>
                      <th>{t('evals.lastRun')}</th>
                      <th class="r">{t('evals.passRate')}</th>
                      <th class="r">{t('evals.vsBaseline')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ agent, last, cases }) => {
                      const s = last ? last.summary[last.version] : undefined
                      return (
                        <tr key={agent.name} data-go onClick={() => go(appHref(app.slug, 'evals', agent.name))}>
                          <td>
                            <b>{agent.name}</b>
                            <div class="s">{agent.title}</div>
                          </td>
                          <td class="num">{cases.filter((c) => c.enabled).length}</td>
                          <td>{last ? <>{statusTag(last.status)} <span class="s">{ago(last.startedAt)}</span></> : <span class="s">{t('evals.neverRun')}</span>}</td>
                          <td class="num">{pctText(s?.passRate)}</td>
                          <td class="num">{last?.comparison ? pts(last.comparison.passRateDelta) : '-'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}

function AgentEvals({ app, agent, q }: { app: ConsoleApplication; agent: string; q: URLSearchParams }) {
  const api = forApp(app.id)
  const runId = q.get('run')
  /** This page's address with some of its query changed (an eval run and a red team run stay open side by side). */
  const hrefWith = (change: Record<string, string | null>): string => {
    const next = new URLSearchParams(q)
    for (const [k, v] of Object.entries(change)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    const s = next.toString()
    return `${appHref(app.slug, 'evals', agent)}${s ? `?${s}` : ''}`
  }
  const [bump, setBump] = useState(0)
  const [editing, setEditing] = useState<EvalCase | 'new' | null>(null)
  const [baseline, setBaseline] = useState('')
  const [starting, setStarting] = useState(false)
  const load = useLoad(async () => {
    const [cases, runs, versions] = await Promise.all([api.admin.evals.cases(agent), api.admin.evals.runs(agent), api.admin.agents.versions(agent)])
    return { cases, runs, versions }
  }, [app.id, agent])
  const running = load.data?.runs.items.some((r) => r.status === 'running') ?? false
  // A run opened by its link (a save started it) that the list does not show yet: read the list again.
  useEffect(() => {
    if (runId && load.data && !load.data.runs.items.some((r) => r.id === runId)) void load.reload()
  }, [runId, load.data])
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void load.reload(), 1500)
    return () => clearInterval(timer)
  }, [running])

  const start = async (e: Event) => {
    const el = e.currentTarget as Element
    setStarting(true)
    try {
      const id = await api.admin.evals.start(agent, baseline ? { baseline } : {})
      sparkle(el)
      toast(t('evals.started'), t('evals.startedSub'))
      go(hrefWith({ run: id }))
      await load.reload()
    } catch (err) {
      toast(t('evals.notStarted'), describe(err), null)
    } finally {
      setStarting(false)
    }
  }

  return (
    <>
      <Hero
        title={t('evals.of', { agent })}
        sub={t('evals.agentSub')}
        actions={
          <>
            <Btn href={appHref(app.slug, 'agents', agent)}>{t('evals.openAgent')}</Btn>
            <Btn
              kind="pri"
              busy={starting ? t('evals.starting') : running ? t('evals.running') : false}
              disabled={!load.data?.cases.some((c) => c.enabled)}
              onClick={(e) => void start(e)}
            >
              {t('evals.runNow')}
            </Btn>
          </>
        }
      />
      <Loads load={load} what={t('what.theEvals')}>
        {({ cases, runs, versions }) => (
          <div class="grid">
            <div class="grid g3">
              <Panel title={t('evals.compare')}>
                <select class="in" value={baseline} onChange={(e) => setBaseline(e.currentTarget.value)} aria-label={t('evals.baselineVersion')}>
                  <option value="">{t('evals.noBaseline')}</option>
                  {versions.slice(1).map((v) => (
                    <option key={v.version} value={v.version}>
                      {v.version.slice(0, 7)}, {ago(v.createdAt)}
                    </option>
                  ))}
                </select>
                <p class="hint">
                  {t('evals.onSaveNote')}
                  {runs.budget.onSave ? '' : t('evals.onSaveOff')}.
                </p>
              </Panel>
              <Panel title={t('evals.budget')}>
                <div class="meter">
                  <b>{pctText(runs.budget.usedPct)}</b> <span style="color:var(--mu)">{t('evals.usedMonth')}</span>
                </div>
                <p class="hint">{t('evals.budgetNote')}</p>
              </Panel>
              <Panel title={t('evals.judge')}>
                <Tag tone={runs.budget.judge ? 'ok' : ''} dot>
                  {t(runs.budget.judge ? 'evals.on' : 'evals.off')}
                </Tag>
                <p class="hint">{t('evals.judgeNote')}</p>
              </Panel>
            </div>

            <Panel
              title={
                <>
                  {t('evals.cases')} <Tag>{pl(cases.filter((c) => c.enabled).length, 'evals.enabledCase')}</Tag>
                </>
              }
              actions={
                <Btn sm onClick={() => setEditing('new')}>
                  {t('evals.newCase')}
                </Btn>
              }
            >
              {editing ? (
                <CaseEditor
                  app={app}
                  agent={agent}
                  initial={editing === 'new' ? null : editing}
                  onClose={async (changed) => {
                    setEditing(null)
                    if (changed) await load.reload()
                  }}
                />
              ) : null}
              {!cases.length && !editing ? (
                <Empty>
                  <b>{t('evals.noCases')}</b>
                  <br />
                  {t('evals.noCasesText')}
                </Empty>
              ) : (
                <ul class="evcases">
                  {cases.map((c) => (
                    <li key={c.name}>
                      <button type="button" onClick={() => setEditing(c)}>
                        <b>{c.name}</b>
                        {!c.enabled ? <Tag>{t('evals.caseOff')}</Tag> : null}
                        <span class="s">{c.input}</span>
                        <span class="s">{c.checks.map(checkText).join(', ') || t('evals.noChecks')}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            <Panel title={t('evals.runs')}>
              {!runs.items.length ? (
                <p class="hint">{t('evals.noRuns')}</p>
              ) : (
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('evals.when')}</th>
                        <th>{t('evals.status')}</th>
                        <th>{t('evals.version')}</th>
                        <th class="r">{t('evals.passRate')}</th>
                        <th class="r">{t('evals.passChange')}</th>
                        <th class="r">{t('evals.latencyChange')}</th>
                        <th class="r">{t('evals.tokensVs')}</th>
                        <th class="r">{t('evals.budgetCol')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.items.map((r) => {
                        const s = r.summary[r.version]
                        const b = r.baseline ? r.summary[r.baseline] : undefined
                        return (
                          <tr key={r.id} data-go aria-current={r.id === runId ? 'true' : undefined} onClick={() => go(hrefWith({ run: r.id }))}>
                            <td>
                              {ago(r.startedAt)} <span class="s">{t(r.trigger === 'save' ? 'evals.onSave' : 'evals.onDemand')}</span>
                            </td>
                            <td>{statusTag(r.status)}</td>
                            <td>
                              <code>{r.version.slice(0, 7)}</code>
                              {r.baseline ? <span class="s">{t('evals.vs', { version: r.baseline.slice(0, 7) })}</span> : null}
                            </td>
                            <td class="num">{pctText(s?.passRate)}</td>
                            <td class="num">{r.comparison ? pts(r.comparison.passRateDelta) : '-'}</td>
                            <td class="num">{r.comparison ? signed(r.comparison.latencyDeltaMs, ' ms') : '-'}</td>
                            <td class="num">{r.comparison ? pctText(r.comparison.tokensVsBaselinePct) : '-'}</td>
                            <td class="num">{pctText((s?.capPct ?? 0) + (b?.capPct ?? 0))}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
            {runId ? <RunDetail app={app} runId={runId} key={runId} /> : null}
            <ModelChoice
              app={app}
              agent={agent}
              hasCases={cases.some((c) => c.enabled)}
              onSwitched={() => {
                setBump((b) => b + 1)
                void load.reload()
              }}
            />
            <RedTeamSection
              app={app}
              agent={agent}
              runId={q.get('rt')}
              href={(id) => hrefWith({ rt: id })}
              bump={bump}
              onChanged={() => void load.reload()}
            />
          </div>
        )}
      </Loads>
    </>
  )
}

function CaseEditor({ app, agent, initial, onClose }: { app: ConsoleApplication; agent: string; initial: EvalCase | null; onClose: (changed: boolean) => Promise<void> }) {
  const api = forApp(app.id)
  const [name, setName] = useState(initial?.name ?? '')
  const [input, setInput] = useState(initial?.input ?? '')
  const [path, setPath] = useState(initial?.page?.path ?? '')
  const [pageText, setPageText] = useState(initial?.page?.text ?? '')
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [checks, setChecks] = useState<EvalCheck[]>(initial?.checks ?? [{ type: 'contains', value: '' }])
  const [schemaText, setSchemaText] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState(false)
  const set = (i: number, c: EvalCheck) => setChecks(checks.map((x, k) => (k === i ? c : x)))

  const save = async (e: Event) => {
    const el = e.currentTarget as Element
    setBusy(true)
    try {
      const parsed = checks.map((c, i) => (c.type === 'json_schema' && schemaText[i] !== undefined ? { ...c, schema: JSON.parse(schemaText[i]!) as Record<string, unknown> } : c))
      await api.admin.evals.putCase(agent, name.trim(), {
        input,
        page: path || pageText ? { path: path || null, title: null, text: pageText || null } : null,
        checks: parsed,
        enabled,
      })
      done(el)
      toast(t('evals.caseSaved'), name)
      await onClose(true)
    } catch (err) {
      toast(t('evals.notSaved'), err instanceof SyntaxError ? t('evals.schemaInvalid') : describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  const remove = async () => {
    if (!initial || !confirm(t('evals.confirmRemove', { name: initial.name }))) return
    try {
      await api.admin.evals.deleteCase(agent, initial.name)
      await onClose(true)
    } catch (err) {
      toast(t('evals.notRemoved'), describe(err), null)
    }
  }
  return (
    <div class="evedit">
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('common.name')} id="ev-name">
          <input id="ev-name" class="in" value={name} disabled={!!initial} onInput={(e) => setName(e.currentTarget.value)} maxLength={120} />
        </Field>
        <Field label={t('evals.enabled')}>
          <Switch on={enabled} onChange={setEnabled} label={t(enabled ? 'evals.runsWithOthers' : 'evals.skipped')} />
        </Field>
      </div>
      <Field label={t('evals.input')} id="ev-input" hint={t('evals.inputHint')}>
        <textarea id="ev-input" class="in" style="min-height:70px;height:auto;padding:10px 12px" value={input} onInput={(e) => setInput(e.currentTarget.value)} />
      </Field>
      <div class="grid g-21" style="gap:0 14px">
        <Field label={t('evals.pageText')} id="ev-page" hint={t('evals.pageTextHint')}>
          <textarea id="ev-page" class="in" style="min-height:44px;height:auto;padding:10px 12px" value={pageText} onInput={(e) => setPageText(e.currentTarget.value)} />
        </Field>
        <Field label={t('evals.pagePath')} id="ev-path">
          <input id="ev-path" class="in mono" value={path} placeholder="/orders/42" onInput={(e) => setPath(e.currentTarget.value)} />
        </Field>
      </div>
      <span class="lbl">{t('evals.checks')}</span>
      <ul class="evchecks">
        {checks.map((c, i) => (
          <li key={i}>
            <select class="in" aria-label={t('evals.check')} value={c.type} onChange={(e) => set(i, blank(e.currentTarget.value as EvalCheck['type']))}>
              {CHECK_TYPES.map((k) => (
                <option key={k} value={k}>
                  {t(`evals.ct.${k}` as TextKey)}
                </option>
              ))}
            </select>
            {c.type === 'contains' || c.type === 'not_contains' ? (
              <input class="in" aria-label={t('evals.text')} value={c.value} onInput={(e) => set(i, { ...c, value: e.currentTarget.value })} />
            ) : c.type === 'regex' ? (
              <input class="in mono" aria-label={t('evals.pattern')} value={c.pattern} onInput={(e) => set(i, { ...c, pattern: e.currentTarget.value })} />
            ) : c.type === 'tool_called' || c.type === 'tool_not_called' ? (
              <input class="in mono" aria-label={t('evals.tool')} value={c.tool} placeholder={t('evals.toolPh')} onInput={(e) => set(i, { ...c, tool: e.currentTarget.value })} />
            ) : c.type === 'rubric' ? (
              <input class="in" aria-label={t('evals.rubric')} value={c.rubric} placeholder={t('evals.rubricPh')} onInput={(e) => set(i, { ...c, rubric: e.currentTarget.value })} />
            ) : (
              <textarea
                class="in mono"
                aria-label="JSON Schema"
                style="min-height:60px;height:auto;padding:8px 10px"
                value={schemaText[i] ?? JSON.stringify(c.type === "json_schema" ? c.schema : {}, null, 2)}
                onInput={(e) => setSchemaText({ ...schemaText, [i]: e.currentTarget.value })}
              />
            )}
            <Btn sm kind="ghost" label={t('evals.removeCheck')} onClick={() => setChecks(checks.filter((_, k) => k !== i))}>
              {t('evals.remove')}
            </Btn>
          </li>
        ))}
      </ul>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <Btn sm onClick={() => setChecks([...checks, { type: 'contains', value: '' }])}>
          {t('evals.addCheck')}
        </Btn>
        <span style="flex:1" />
        {initial ? (
          <Btn sm kind="ghost danger" onClick={() => void remove()}>
            {t('evals.removeCase')}
          </Btn>
        ) : null}
        <Btn sm kind="ghost" onClick={() => void onClose(false)}>
          {t('common.cancel')}
        </Btn>
        <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} disabled={!name.trim() || !input.trim()} onClick={(e) => void save(e)}>
          {t('evals.saveCase')}
        </Btn>
      </div>
    </div>
  )
}

function RunDetail({ app, runId }: { app: ConsoleApplication; runId: string }) {
  const api = forApp(app.id)
  const load = useLoad(() => api.admin.evals.run(runId), [app.id, runId])
  const running = load.data?.status === 'running'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void load.reload(), 1500)
    return () => clearInterval(timer)
  }, [running])
  return (
    <Loads load={load} what={t('what.run')}>
      {(run: EvalRunDetail) => (
        <Panel
          title={
            <>
              {t('evals.run', { when: ago(run.startedAt) })} {statusTag(run.status)}
            </>
          }
        >
          {run.status === 'limit' ? <p class="hint">{t('evals.limitNote')}</p> : null}
          {!run.cases.length ? <p class="hint">{t(running ? 'evals.runningFirst' : 'evals.noCaseRan')}</p> : null}
          <div class="grid">
            {run.cases.map((c) => (
              <CaseResult key={c.name} name={c.name} version={run.version} baseline={run.baseline} results={c.results} changed={c.changed} />
            ))}
          </div>
        </Panel>
      )}
    </Loads>
  )
}

function CaseResult(p: { name: string; version: string; baseline: string | null; results: Record<string, EvalCaseResult>; changed: boolean }) {
  const now = p.results[p.version]
  const before = p.baseline ? p.results[p.baseline] : undefined
  const verdict = (r: EvalCaseResult | undefined) =>
    !r ? (
      <Tag>{t('evals.notRun')}</Tag>
    ) : r.passed ? (
      <Tag tone="ok" dot>
        {t('evals.passed')}
      </Tag>
    ) : (
      <Tag tone="bad" dot>
        {t('evals.failed')}
      </Tag>
    )
  return (
    <article class={`evres ${p.changed ? 'changed' : ''}`}>
      <h4>
        {p.name} {verdict(now)}
        {before ? <span class="s">{t(before.passed ? 'evals.wasPassing' : 'evals.wasFailing')}</span> : null}
        {p.changed ? <Tag tone="warn">{t('evals.changed')}</Tag> : null}
      </h4>
      {now ? (
        <>
          <ul class="evchk">
            {now.checks.map((x, i) => (
              <li key={i} data-o={x.outcome}>
                <i aria-hidden="true" />
                {checkText(x.check)}
                {x.detail ? <span class="s">: {x.detail}</span> : null}
              </li>
            ))}
            {now.error ? <li data-o="failed">{now.error}</li> : null}
          </ul>
          <div class="s">
            {now.tools.length ? t('evals.tools', { tools: now.tools.map((x) => x.name).join(', ') }) : t('evals.noTools')}
            {ms(now.latencyMs)}, {pl(now.inputTokens + now.outputTokens, 'n.token')}
            {before ? t('evals.before', { time: ms(before.latencyMs), tokens: pl(before.inputTokens + before.outputTokens, 'n.token') }) : ''}.
            {now.reason === 'confirm' ? t('evals.stoppedConfirm') : ''}
          </div>
          {before ? (
            <div class="evdiff" aria-label={t('evals.diffLabel')}>
              {wordDiff(before.answer, now.answer).map((d, i) =>
                d.kind === 'same' ? <span key={i}>{d.text}</span> : d.kind === 'add' ? <ins key={i}>{d.text}</ins> : <del key={i}>{d.text}</del>,
              )}
            </div>
          ) : (
            <div class="evdiff">{now.answer || <span class="s">{t('evals.noAnswer')}</span>}</div>
          )}
        </>
      ) : null}
    </article>
  )
}
