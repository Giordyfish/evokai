/** Workflows: the list with their runs, the definition editor with its flow, and a run's step timeline. */
import { useEffect, useRef, useState } from 'preact/hooks'

import type {
  ConsoleApplication,
  WorkflowDetail,
  WorkflowOutlineStep,
  WorkflowProblem,
  WorkflowRun,
  WorkflowRunDetail,
  WorkflowStepState,
} from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, dateTime, ms, sharePct } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { Btn, Hero, KV, Panel, Seg, Tag } from '../ui'
import {
  arrowsOf,
  canCancel,
  canRetry,
  ordered,
  problemLines,
  RETRIED,
  RUN_STATUSES,
  RUN_TONE,
  type RunFilter,
  runHref,
  STEP_TONE,
  stepDetail,
  TEMPLATE,
  toYaml,
  viewOf,
  workflowHref,
} from '../workflows'
import { Loads } from './common'
import { t, type TextKey } from '../i18n'

const runStatus = (s: string): string => t(`wf.status.${s}` as TextKey)
const stepStatus = (s: string): string => t(`wf.stepStatus.${s}` as TextKey)
const kindLabel = (k: string): string => (['agent', 'tool', 'approval', 'condition', 'wait', 'notify', 'parallel'].includes(k) ? t(`wf.kind.${k}` as TextKey) : k)
const triggerLabel = (k: string): string => t(`wf.trigger.${k}` as TextKey)

export function Workflows({ app, id }: { app: ConsoleApplication; id: string | null }) {
  const view = viewOf(id)
  if (view.kind === 'run') return <RunDetail key={view.id} app={app} runId={view.id} />
  if (view.kind === 'new') return <Editor key="new" app={app} name={null} />
  if (view.kind === 'edit') return <Editor key={view.name} app={app} name={view.name} />
  return <List app={app} />
}

function List({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.workflows'), app.name)
  const api = forApp(app.id)
  const list = useLoad(() => api.admin.workflows.list(), [app.id])
  const add = (
    <Btn kind="pri" href={appHref(app.slug, 'workflows', 'new')}>
      {t('wf.add')}
    </Btn>
  )
  return (
    <>
      <Hero title={t('tab.workflows')} sub={t('wf.sub')} actions={add} />
      <Loads load={list} what={t('what.workflows')}>
        {(data) => (
          <>
            {data.pendingApprovals ? (
              <p class="hint" style="margin:0 0 12px">
                <Tag tone="gold" dot href={appHref(app.slug, 'approvals')}>
                  {t('wf.pendingApprovals', { n: data.pendingApprovals })}
                </Tag>
              </p>
            ) : null}
            {data.items.length ? (
              <Panel cls="flat">
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('wf.workflow')}</th>
                        <th>{t('wf.triggers')}</th>
                        <th class="r">{t('wf.steps')}</th>
                        <th>{t('wf.runs30')}</th>
                        <th>{t('wf.lastRun')}</th>
                        <th>{t('wf.nextRun')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((w) => (
                        <tr key={w.name} data-go onClick={() => go(workflowHref(app.slug, w.name))}>
                          <td>
                            <b>{w.title || w.name}</b>
                            <div class="s">
                              <code>{w.name}</code> · {w.version}
                            </div>
                          </td>
                          <td>
                            <Tag>{triggerLabel('manual')}</Tag>{' '}
                            {w.triggers.map((x, i) => (
                              <Tag key={i} tone={x.kind === 'schedule' ? 'teal' : x.kind === 'event' ? 'vi' : 'kserver'} title={x.cron ?? x.agent ?? undefined}>
                                {triggerLabel(x.kind)}
                              </Tag>
                            ))}
                          </td>
                          <td class="num">{w.steps}</td>
                          <td>
                            {Object.entries(w.runs).map(([s, n]) => (
                              <Tag key={s} tone={RUN_TONE[s as keyof typeof RUN_TONE]}>
                                {runStatus(s)} {n}
                              </Tag>
                            ))}
                            {Object.keys(w.runs).length ? null : <span class="s">{t('wf.noRuns')}</span>}
                          </td>
                          <td class="s">{w.lastRunAt ? ago(w.lastRunAt) : t('wf.never')}</td>
                          <td class="s">{w.nextRunAt ? dateTime(w.nextRunAt) : '·'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            ) : (
              <Panel>
                <Empty action={add}>
                  <b>{t('wf.empty')}</b>
                  <br />
                  {t('wf.emptyText')}
                </Empty>
              </Panel>
            )}
            <Runs app={app} workflows={data.items.map((w) => w.name)} />
          </>
        )}
      </Loads>
    </>
  )
}

function Runs({ app, workflows, only }: { app: ConsoleApplication; workflows: string[]; only?: string }) {
  const api = forApp(app.id)
  const [status, setStatus] = useState<RunFilter>('all')
  const [workflow, setWorkflow] = useState(only ?? '')
  const runs = useLoad(
    () => api.admin.workflows.runs({ status: status === 'all' ? undefined : status, workflow: workflow || undefined, limit: 50 }),
    [app.id, status, workflow],
  )
  const labels = Object.fromEntries(RUN_STATUSES.map((s) => [s, s === 'all' ? t('wf.filter.all') : s === 'active' ? t('wf.filter.active') : runStatus(s)]))
  return (
    <Panel
      title={t('wf.runs')}
      style="margin-top:16px"
      actions={
        <span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <Seg options={RUN_STATUSES} value={status} onChange={setStatus} labels={labels} />
          {only ? null : (
            <select class="in" style="height:34px;width:auto" value={workflow} aria-label={t('wf.workflow')} onChange={(e) => setWorkflow(e.currentTarget.value)}>
              <option value="">{t('wf.allWorkflows')}</option>
              {workflows.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          )}
        </span>
      }
    >
      <Loads load={runs} what={t('what.workflowRuns')} panel={false}>
        {(data) =>
          data.items.length ? (
            <div class="wx">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>{t('wf.started')}</th>
                    <th>{t('wf.workflow')}</th>
                    <th>{t('wf.status')}</th>
                    <th>{t('wf.trigger')}</th>
                    <th class="r">{t('wf.duration')}</th>
                    <th class="r">{t('wf.usage')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <RunRow key={r.id} app={app} r={r} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p class="hint">{t('wf.noRunsHere')}</p>
          )
        }
      </Loads>
    </Panel>
  )
}

function RunRow({ app, r }: { app: ConsoleApplication; r: WorkflowRun }) {
  return (
    <tr data-go onClick={() => go(runHref(app.slug, r.id))}>
      <td class="s">{r.startedAt ? dateTime(r.startedAt) : ''}</td>
      <td>
        <code>{r.workflow}</code>
      </td>
      <td>
        <Tag tone={RUN_TONE[r.status]} dot>
          {runStatus(r.status)}
        </Tag>
        {r.reason && r.status !== 'done' ? <span class="s"> {r.reason}</span> : null}
      </td>
      <td class="s">{triggerLabel(r.trigger)}</td>
      <td class="num">{ms(r.durationMs)}</td>
      <td class="num">{sharePct(r.capPct)}</td>
    </tr>
  )
}

// the editor

function Editor({ app, name }: { app: ConsoleApplication; name: string | null }) {
  setTitle(name ?? t('wf.new'), app.name)
  const api = forApp(app.id)
  const load = useLoad<WorkflowDetail | null>(() => (name ? api.admin.workflows.get(name) : Promise.resolve(null)), [app.id, name])
  return (
    <Loads load={load} what={name ?? t('wf.new')}>
      {(w) => <EditorBody app={app} w={w} onSaved={() => void load.reload()} />}
    </Loads>
  )
}

function EditorBody({ app, w, onSaved }: { app: ConsoleApplication; w: WorkflowDetail | null; onSaved: () => void }) {
  const c = useConsole()
  const api = forApp(app.id)
  const [format, setFormat] = useState<'yaml' | 'json'>('yaml')
  const [text, setText] = useState(() => (w ? toYaml(ordered(w.definition)) : TEMPLATE))
  const [problems, setProblems] = useState<WorkflowProblem[]>([])
  const [outline, setOutline] = useState<WorkflowOutlineStep[]>(w?.outline ?? [])
  const [checked, setChecked] = useState<{ ok: boolean; version: string | null } | null>(null)
  const [busy, setBusy] = useState<'save' | 'run' | null>(null)
  const area = useRef<HTMLTextAreaElement>(null)
  const gutter = useRef<HTMLPreElement>(null)

  useEffect(() => {
    const h = setTimeout(() => {
      api.admin.workflows
        .validate({ text, format })
        .then((v) => {
          setProblems(v.problems)
          if (v.outline.length) setOutline(v.outline)
          setChecked({ ok: v.ok, version: v.version })
        })
        .catch((e: unknown) => setProblems([{ path: '', message: describe(e), line: null }]))
    }, 350)
    return () => clearTimeout(h)
  }, [text, format])

  const switchFormat = (f: 'yaml' | 'json') => {
    if (f === format) return
    api.admin.workflows
      .validate({ text, format })
      .then((v) => {
        if (v.definition) setText(f === 'json' ? JSON.stringify(ordered(v.definition), null, 2) : toYaml(ordered(v.definition)))
        setFormat(f)
      })
      .catch(() => setFormat(f))
  }

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setBusy('save')
    try {
      const v = await api.admin.workflows.validate({ text, format })
      if (!v.ok || !v.definition) {
        setProblems(v.problems)
        toast(t('wf.notSaved'), t('wf.fixProblems', { n: v.problems.length }), null)
        return
      }
      const nm = String(v.definition.name ?? '')
      if (w && nm !== w.name) {
        setProblems([{ path: 'name', message: t('wf.nameFixed'), line: 1 }])
        return
      }
      const r = await api.admin.workflows.put(nm, v.definition)
      sparkle(btn)
      done(btn)
      toast(r.created ? t('wf.savedVersion', { version: r.version }) : t('wf.unchanged'), nm)
      await c.reloadApps()
      if (!w) go(workflowHref(app.slug, nm))
      else onSaved()
    } catch (err) {
      toast(t('wf.notSaved'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }

  const runNow = async () => {
    if (!w) return
    const raw = prompt(t('wf.runInput'), '{}')
    if (raw === null) return
    let input: Record<string, unknown>
    try {
      input = JSON.parse(raw || '{}') as Record<string, unknown>
    } catch {
      toast(t('wf.badInput'), '', null)
      return
    }
    setBusy('run')
    try {
      const r = await api.admin.workflows.start(w.name, { input })
      toast(t('wf.runStarted'), w.name)
      go(runHref(app.slug, r.id))
    } catch (err) {
      toast(t('wf.notStarted'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    if (!w || !confirm(t('wf.confirmRemove', { name: w.name }))) return
    try {
      await api.admin.workflows.delete(w.name)
      toast(t('wf.removed'), w.name, null)
      go(appHref(app.slug, 'workflows'))
    } catch (err) {
      toast(t('wf.notRemoved'), describe(err), null)
    }
  }

  const byLine = problemLines(problems)
  const lines = text.split('\n')
  const gutterText = lines.map((_, i) => `${byLine.has(i + 1) ? '●' : ' '}${String(i + 1).padStart(3)}`).join('\n')
  return (
    <>
      <Hero
        title={w ? w.title || w.name : t('wf.new')}
        sub={w ? (w.description || t('wf.editSub')) : t('wf.newSub')}
        actions={
          <>
            {w ? (
              <Btn kind="ghost" busy={busy === 'run' ? t('wf.starting') : false} onClick={() => void runNow()}>
                {t('wf.runNow')}
              </Btn>
            ) : null}
            <Btn kind="pri" check busy={busy === 'save' ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {t('common.save')}
            </Btn>
          </>
        }
      />
      <div class="grid g-21">
        <Panel
          title={t('wf.definition')}
          actions={<Seg options={['yaml', 'json'] as const} value={format} onChange={switchFormat} labels={{ yaml: 'YAML', json: 'JSON' }} />}
        >
          <div class="wfed">
            <pre class="wfed-g" ref={gutter} aria-hidden="true">
              {gutterText}
            </pre>
            <textarea
              ref={area}
              class="in mono wfed-t"
              spellcheck={false}
              aria-label={t('wf.definition')}
              value={text}
              onInput={(e) => setText(e.currentTarget.value)}
              onScroll={(e) => {
                if (gutter.current) gutter.current.scrollTop = e.currentTarget.scrollTop
              }}
            />
          </div>
          {problems.length ? (
            <ul class="wfprob" role="list">
              {problems.map((p, i) => (
                <li key={i}>
                  <button
                    type="button"
                    onClick={() => {
                      const el = area.current
                      if (!el || !p.line) return
                      const start = lines.slice(0, p.line - 1).join('\n').length + (p.line > 1 ? 1 : 0)
                      el.focus()
                      el.setSelectionRange(start, start + (lines[p.line - 1]?.length ?? 0))
                    }}
                  >
                    {p.line ? <b>{t('wf.line', { n: p.line })}</b> : null} {p.path ? <code>{p.path}</code> : null} {p.message}
                  </button>
                </li>
              ))}
            </ul>
          ) : checked?.ok ? (
            <p class="hint">
              <Tag tone="ok" dot>
                {t('wf.valid')}
              </Tag>{' '}
              {checked.version && w && checked.version !== w.version ? t('wf.newVersion', { version: checked.version }) : null}
            </p>
          ) : null}
        </Panel>
        <div>
          <Panel title={t('wf.flow')}>
            {outline.length ? <Flow steps={outline} /> : <p class="hint">{t('wf.noFlow')}</p>}
          </Panel>
          {w ? (
            <Panel title={t('wf.details')} style="margin-top:16px">
              <KV
                rows={[
                  [t('wf.version'), <code key="v">{w.version}</code>],
                  [t('wf.timezone'), w.timezone],
                  [t('wf.retention'), t('wf.days', { n: w.retentionDays })],
                  [t('wf.nextRun'), w.nextRunAt ? dateTime(w.nextRunAt) : t('common.none')],
                  ...(w.hookPath ? [[t('wf.hook'), <code key="h">{w.hookPath}</code>] as [string, preact.JSX.Element]] : []),
                ]}
              />
              <p class="hint" style="margin-top:10px">
                {t('wf.versions', { n: w.versions.length })}
              </p>
              <Btn sm kind="ghost danger" onClick={() => void remove()}>
                {t('wf.remove')}
              </Btn>
            </Panel>
          ) : null}
        </div>
      </div>
      {w ? <Runs app={app} workflows={[w.name]} only={w.name} /> : null}
    </>
  )
}

/** The steps as a vertical flow: a box per step, its arrows when they do not go to the step below, branches side by side. */
export function Flow({ steps }: { steps: WorkflowOutlineStep[] }) {
  return (
    <ol class="flow" role="list">
      {steps.map((s, i) => {
        const below = steps[i + 1]?.id ?? null
        const arrows = arrowsOf(s, below)
        return (
          <li key={s.id} class={`flow-s k-${s.kind}`}>
            <div class="flow-b">
              <Tag tone={s.kind === 'approval' ? 'gold' : s.kind === 'agent' ? 'vi' : s.kind === 'tool' ? 'kserver' : s.kind === 'condition' ? 'teal' : ''}>
                {kindLabel(s.kind)}
              </Tag>
              <code>{s.id}</code>
              {s.title ? <span class="s">{s.title}</span> : null}
              {stepDetail(s) ? <small>{stepDetail(s)}</small> : null}
              {s.approval ? <small>{t('wf.afterApproval', { step: s.approval })}</small> : null}
              {arrows.length ? (
                <span class="flow-a">
                  {arrows.map((a) => (
                    <span key={a.label}>
                      {t(`wf.arrow.${a.label}` as TextKey)} → <code>{a.to === 'end' ? t('wf.end') : a.to}</code>
                    </span>
                  ))}
                </span>
              ) : null}
            </div>
            {s.branches ? (
              <div class="flow-p">
                {s.branches.map((b) => (
                  <div key={b.name}>
                    <small>{b.name}</small>
                    <Flow steps={b.steps} />
                  </div>
                ))}
              </div>
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}

// a run

function RunDetail({ app, runId }: { app: ConsoleApplication; runId: string }) {
  setTitle(t('wf.run'), app.name)
  const api = forApp(app.id)
  const load = useLoad(() => api.admin.workflows.run(runId), [app.id, runId])
  useEffect(() => {
    const live = load.data && canCancel(load.data.status)
    if (!live) return
    const h = setInterval(() => void load.reload(), 4000)
    return () => clearInterval(h)
  }, [load.data?.status])
  return <Loads load={load} what={t('wf.run')}>{(r) => <RunBody app={app} r={r} reload={load.reload} />}</Loads>
}

function RunBody({ app, r, reload }: { app: ConsoleApplication; r: WorkflowRunDetail; reload: () => Promise<void> }) {
  const api = forApp(app.id)
  const act = async (what: 'retry' | 'cancel') => {
    if (what === 'cancel' && !confirm(t('wf.confirmCancel'))) return
    try {
      await (what === 'retry' ? api.admin.workflows.retry(r.id) : api.admin.workflows.cancel(r.id))
      toast(what === 'retry' ? t('wf.retried') : t('wf.cancelled'), r.workflow)
      await reload()
    } catch (err) {
      toast(t('wf.actionFailed'), describe(err), null)
    }
  }
  return (
    <>
      <Hero
        title={r.workflow}
        sub={
          <>
            <Tag tone={RUN_TONE[r.status]} dot>
              {runStatus(r.status)}
            </Tag>{' '}
            {r.startedAt ? dateTime(r.startedAt) : ''}
          </>
        }
        actions={
          <>
            <Btn kind="ghost" href={appHref(app.slug, 'traces', r.traceId)}>
              {t('wf.trace')}
            </Btn>
            {canRetry(r.status, r.reason) ? (
              <Btn kind="pri" onClick={() => void act('retry')}>
                {t('wf.retry')}
              </Btn>
            ) : null}
            {canCancel(r.status) ? (
              <Btn kind="ghost danger" onClick={() => void act('cancel')}>
                {t('wf.cancel')}
              </Btn>
            ) : null}
          </>
        }
      />
      <div class="grid g-21">
        <Panel title={t('wf.timeline')}>
          {r.error ? (
            <p class="hint" style="color:var(--s-bad);margin:0 0 10px">
              {r.error}
            </p>
          ) : null}
          <ol class="stl" role="list">
            {r.steps.map((s) => (
              <StepItem key={s.id + (s.branch ?? '')} app={app} s={s} />
            ))}
          </ol>
          {r.approvals
            .filter((a) => a.status === 'pending')
            .map((a) => (
              <p key={a.id} class="hint">
                <Tag tone="gold" dot href={appHref(app.slug, 'approvals')}>
                  {t('wf.waitingFor', { step: a.step })}
                </Tag>
              </p>
            ))}
        </Panel>
        <div>
          <Panel title={t('wf.details')}>
            <KV
              rows={[
                [t('wf.status'), runStatus(r.status)],
                [t('wf.reason'), r.reason ?? t('common.none')],
                [t('wf.trigger'), triggerLabel(r.trigger)],
                [t('wf.version'), <a key="v" href={workflowHref(app.slug, r.workflow)}><code>{r.version}</code></a>],
                [t('wf.duration'), ms(r.durationMs)],
                [t('wf.usage'), sharePct(r.capPct)],
                [t('wf.subject'), r.subjectKey ? <code key="s">{r.subjectKey}</code> : t('common.none')],
                [t('wf.finished'), r.finishedAt ? dateTime(r.finishedAt) : t('wf.notYet')],
              ]}
            />
            {r.masked ? <p class="hint">{t('wf.masked')}</p> : null}
          </Panel>
          <Panel title={t('wf.input')} style="margin-top:16px">
            <Value v={r.input} />
          </Panel>
          <Panel title={t('wf.output')} style="margin-top:16px">
            <Value v={r.output} />
          </Panel>
          <Panel title={t('wf.flow')} style="margin-top:16px">
            <Flow steps={r.outline} />
          </Panel>
        </div>
      </div>
    </>
  )
}

function Value({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <p class="hint">{t('wf.hidden')}</p>
  return <pre class="wcode">{typeof v === 'string' ? v : JSON.stringify(v, null, 2)}</pre>
}

function StepItem({ app, s }: { app: ConsoleApplication; s: WorkflowStepState }) {
  const took = s.startedAt && s.finishedAt ? new Date(s.finishedAt).getTime() - new Date(s.startedAt).getTime() : null
  const runId = typeof s.meta.runId === 'string' ? s.meta.runId : null
  return (
    <li class={`stl-i st-${s.status}`}>
      <i aria-hidden="true" />
      <div>
        <div class="stl-h">
          <code>{s.id}</code>
          <Tag>{kindLabel(s.kind)}</Tag>
          <Tag tone={STEP_TONE[s.status]} dot>
            {stepStatus(s.status)}
          </Tag>
          {s.attempt > 1 && RETRIED.has(s.kind) ? <span class="s">{t('wf.attempt', { n: s.attempt })}</span> : null}
          {took !== null ? <span class="s">{ms(took)}</span> : null}
          {s.branch ? <span class="s">{t('wf.branch', { branch: s.branch })}</span> : null}
        </div>
        {s.error ? <small class="bad">{s.error}</small> : null}
        {s.dueAt && s.status !== 'done' ? <small>{t('wf.dueAt', { at: dateTime(s.dueAt) })}</small> : null}
        {runId ? (
          <small>
            <a href={appHref(app.slug, 'traces', runId)}>{t('wf.agentTrace')}</a>
            {typeof s.meta.model === 'string' ? ` · ${s.meta.model}` : ''}
          </small>
        ) : null}
        {s.input !== null || s.output !== null ? (
          <details>
            <summary>{t('wf.io')}</summary>
            {s.input !== null ? <pre class="wcode">{JSON.stringify(s.input, null, 2)}</pre> : null}
            {s.output !== null ? <pre class="wcode">{JSON.stringify(s.output, null, 2)}</pre> : null}
          </details>
        ) : null}
      </div>
    </li>
  )
}
