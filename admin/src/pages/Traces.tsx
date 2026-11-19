/**
 * Traces: every turn and server run with its spans (model calls, tools, guard decisions, speech), filtered by agent,
 * version, person, outcome, guard, thumbs, speed and errors; one trace as a waterfall; and the versions of an agent
 * side by side. Usage is a share of the cap, never an amount.
 */
import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'

import type { ConsoleApplication, TraceDetail, TraceQuery, TraceSpan, TraceSummary, VersionStats } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, dateTime, modelName, ms, sharePct } from '../format'
import { Empty, toast } from '../magic'
import { appHref, go, setTitle, useLoad } from '../state'
import { contentText, delta, labelText, layout, METRICS, outcomeLabel, outcomeTone, paramsOf, queryOf, SLOW, spanColor } from '../traces'
import { Btn, Field, Hero, KV, Panel, Seg, Tag } from '../ui'
import { Loads } from './common'
import { num, t, tn, type TextKey } from '../i18n'

const OUTCOMES = ['end_turn', 'confirm', 'ask', 'limit', 'guard', 'max_steps', 'cancelled', 'model', 'internal', 'failed'] as const

export function OutcomeTag({ x }: { x: Pick<TraceSummary, 'status' | 'reason' | 'error'> }) {
  return (
    <Tag tone={outcomeTone(x)} dot>
      {outcomeLabel(x)}
    </Tag>
  )
}

const href = (app: ConsoleApplication, q: TraceQuery & { view?: string; a?: string; b?: string }) => {
  const p = paramsOf(q).toString()
  return `${appHref(app.slug, 'traces')}${p ? `?${p}` : ''}`
}

export function Traces({ app, id, q }: { app: ConsoleApplication; id: string | null; q: URLSearchParams }) {
  if (id) return <TraceView app={app} id={id} />
  const view = q.get('view') === 'versions' ? 'versions' : 'turns'
  setTitle(t('tab.traces'), app.name)
  return (
    <>
      <Hero
        title={t('tab.traces')}
        sub={t('tr.sub')}
        actions={
          <Seg
            options={['turns', 'versions'] as const}
            value={view}
            labels={{ turns: t('tr.viewTurns'), versions: t('tr.viewVersions') }}
            onChange={(v) => go(v === 'versions' ? href(app, { view: 'versions', agent: q.get('agent') ?? undefined } as TraceQuery) : href(app, {}))}
          />
        }
      />
      {view === 'versions' ? <Versions app={app} q={q} /> : <TraceList app={app} q={q} />}
    </>
  )
}

function useAgents(app: ConsoleApplication) {
  return useLoad(async () => (await forApp(app.id).admin.agents.list()).map((a) => a.name), [app.id])
}

function TraceList({ app, q }: { app: ConsoleApplication; q: URLSearchParams }) {
  const api = forApp(app.id)
  const query = queryOf(q)
  const agents = useAgents(app)
  const [more, setMore] = useState<TraceSummary[]>([])
  const [next, setNext] = useState<string | null>(null)
  const set = (patch: Partial<TraceQuery>) => go(href(app, { ...query, ...patch }))
  const load = useLoad(async () => {
    const page = await api.admin.traces.list({ ...query, limit: 50 })
    setMore([])
    setNext(page.next)
    return page
  }, [app.id, q.toString()])
  const loadMore = async () => {
    if (!next) return
    try {
      const page = await api.admin.traces.list({ ...query, before: next, limit: 50 })
      setMore([...more, ...page.items])
      setNext(page.next)
    } catch (err) {
      toast(t('tr.notLoaded'), describe(err), null)
    }
  }
  const dateIn = (v: string | Date | undefined) => (typeof v === 'string' ? v.slice(0, 10) : '')
  return (
    <div class="grid" style="gap:16px">
      <Panel title={t('tr.filters')} actions={Object.keys(query).length ? <Btn sm kind="ghost" onClick={() => go(href(app, {}))}>{t('tr.clear')}</Btn> : undefined}>
        <div class="grid g3" style="gap:0 14px">
          <Field label={t('tr.agent')}>
            <select class="in" value={query.agent ?? ''} onChange={(e) => set({ agent: e.currentTarget.value || undefined, version: undefined })}>
              <option value="">{t('tr.any')}</option>
              {(agents.data ?? []).map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t('tr.version')}>
            <input class="in mono" value={query.version ?? ''} placeholder={t('tr.any')} onChange={(e) => set({ version: e.currentTarget.value.trim() || undefined })} />
          </Field>
          <Field label={t('tr.subject')} hint={t('tr.subjectHint')}>
            <input class="in mono" value={query.subject ?? ''} placeholder={t('tr.any')} onChange={(e) => set({ subject: e.currentTarget.value.trim() || undefined })} />
          </Field>
          <Field label={t('tr.outcome')}>
            <select class="in" value={query.outcome ?? ''} onChange={(e) => set({ outcome: e.currentTarget.value || undefined })}>
              <option value="">{t('tr.any')}</option>
              {OUTCOMES.map((o) => (
                <option key={o} value={o}>
                  {outcomeLabel({ status: o, reason: o === 'failed' ? null : o })}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t('tr.kind')}>
            <select class="in" value={query.kind ?? ''} onChange={(e) => set({ kind: (e.currentTarget.value || undefined) as TraceQuery['kind'] })}>
              <option value="">{t('tr.any')}</option>
              <option value="turn">{t('tr.kind.turn')}</option>
              <option value="run">{t('tr.kind.run')}</option>
              <option value="eval">{t('tr.kind.eval')}</option>
              <option value="workflow">{t('tr.kind.workflow')}</option>
            </select>
          </Field>
          <Field label={t('tr.slow')}>
            <select class="in" value={String(query.minMs ?? 0)} onChange={(e) => set({ minMs: Number(e.currentTarget.value) || undefined })}>
              {SLOW.map((v) => (
                <option key={v} value={v}>
                  {v ? t('tr.slowOver', { s: num(v / 1000) }) : t('tr.any')}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t('tr.from')}>
            <input class="in" type="date" value={dateIn(query.from)} onChange={(e) => set({ from: e.currentTarget.value || undefined })} />
          </Field>
          <Field label={t('tr.to')}>
            <input class="in" type="date" value={dateIn(query.to)} onChange={(e) => set({ to: e.currentTarget.value || undefined })} />
          </Field>
          <Field label={t('tr.feedback')}>
            <select class="in" value={query.feedback ?? ''} onChange={(e) => set({ feedback: (e.currentTarget.value || undefined) as TraceQuery['feedback'] })}>
              <option value="">{t('tr.any')}</option>
              <option value="down">{t('tr.fb.down')}</option>
              <option value="up">{t('tr.fb.up')}</option>
              <option value="any">{t('tr.fb.any')}</option>
            </select>
          </Field>
        </div>
        <div class="flags">
          <label class="affix">
            <input type="checkbox" checked={!!query.guard} onChange={(e) => set({ guard: e.currentTarget.checked || undefined })} />
            <span>{t('tr.onlyGuard')}</span>
          </label>
          <label class="affix">
            <input type="checkbox" checked={!!query.errors} onChange={(e) => set({ errors: e.currentTarget.checked || undefined })} />
            <span>{t('tr.onlyErrors')}</span>
          </label>
        </div>
      </Panel>
      <Loads load={load} what={t('what.traces')}>
        {(page) => {
          const items = [...page.items, ...more]
          return (
            <Panel cls="flat">
              {items.length ? (
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('tr.when')}</th>
                        <th>{t('tr.agent')}</th>
                        <th>{t('tr.outcome')}</th>
                        <th class="r">{t('tr.duration')}</th>
                        <th class="r">{t('tr.firstToken')}</th>
                        <th>{t('tr.model')}</th>
                        <th class="r">{t('tr.tools')}</th>
                        <th>{t('tr.signals')}</th>
                        <th>{t('tr.person')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((x) => (
                        <tr key={x.id} data-go onClick={() => go(appHref(app.slug, 'traces', x.id))}>
                          <td class="s" title={dateTime(x.startedAt)}>
                            {ago(x.startedAt)}
                          </td>
                          <td>
                            <b>{x.agent}</b>
                            <div class="s">
                              <code>{x.version}</code> · {t(`tr.kind.${x.kind}` as TextKey)}
                              {x.mode === 'voice' ? ` · ${t('tr.voice')}` : ''}
                            </div>
                          </td>
                          <td>
                            <OutcomeTag x={x} />
                          </td>
                          <td class="num">{ms(x.durationMs)}</td>
                          <td class="num">{x.firstTokenMs == null ? '-' : ms(x.firstTokenMs)}</td>
                          <td class="s" title={x.model ?? undefined}>
                            {x.model ? modelName(x.model) : '-'}
                            {x.fallback ? (
                              <>
                                {' '}
                                <Tag tone="warn">{t('tr.fallback')}</Tag>
                              </>
                            ) : null}
                          </td>
                          <td class="num">
                            {x.toolCalls}
                            {x.toolErrors ? <div class="s">{t('tr.nErrors', { n: x.toolErrors })}</div> : null}
                          </td>
                          <td>
                            <span style="display:flex;gap:4px;flex-wrap:wrap">
                              {x.guardEvents ? <Tag tone="gold">{tn('tr.nGuard', x.guardEvents)}</Tag> : null}
                              {x.feedback != null ? <Tag tone={x.feedback > 0 ? 'ok' : 'bad'}>{x.feedback > 0 ? t('tr.fb.upShort') : t('tr.fb.downShort')}</Tag> : null}
                              {x.error ? <Tag tone="bad">{t('tr.error')}</Tag> : null}
                            </span>
                          </td>
                          <td class="s mono" title={t('tr.subjectKeyTitle')}>
                            {x.subjectKey}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {next ? (
                    <div style="padding:12px;text-align:center">
                      <Btn sm onClick={() => void loadMore()}>
                        {t('audit.loadMore')}
                      </Btn>
                    </div>
                  ) : null}
                </div>
              ) : (
                <Empty k="ember">
                  <b>{t('tr.none')}</b>
                  <br />
                  {page.storage === 'external' ? t('tr.noneExternal') : t('tr.noneText')}
                </Empty>
              )}
            </Panel>
          )
        }}
      </Loads>
    </div>
  )
}

// one trace

const AXIS = 4

function Waterfall({ spans, selected, onSelect }: { spans: TraceSpan[]; selected: string | null; onSelect: (id: string) => void }) {
  const placed = layout(spans)
  const total = Math.max(1, ...spans.map((s) => s.start + s.ms))
  return (
    <>
      <div class="wf" style="display:grid;grid-template-columns:200px minmax(0,1fr);gap:8px;padding:0 0 6px">
        <span />
        <span class="s mono" style="display:flex;justify-content:space-between;margin-right:58px;font-size:11px;color:var(--mu)">
          {Array.from({ length: AXIS + 1 }, (_, i) => (
            <span key={i}>{ms((total * i) / AXIS)}</span>
          ))}
        </span>
      </div>
      <ol class="wf" role="list">
        {placed.map(({ span: s, depth, left, width }) => (
          <li
            key={s.id}
            style={{ gridTemplateColumns: '200px minmax(0,1fr)', cursor: 'pointer', background: selected === s.id ? 'var(--vis)' : undefined, borderRadius: 'var(--r-xs)' }}
            onClick={() => onSelect(s.id)}
            aria-current={selected === s.id ? 'true' : undefined}
          >
            <span class="ty" style={{ paddingLeft: `${depth * 14}px`, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: spanColor(s) }} title={s.name}>
              {s.name}
            </span>
            <span class="lane">
              <i style={{ left: `${left}%`, width: `${width}%`, background: spanColor(s), opacity: s.status === 'cancelled' ? 0.5 : 1 }} />
              <span>{ms(s.ms)}</span>
            </span>
          </li>
        ))}
      </ol>
    </>
  )
}

function attrText(v: unknown): string {
  if (typeof v === 'boolean') return v ? t('tr.yes') : t('tr.no')
  if (typeof v === 'number') return num(v)
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

function SpanPanel({ span }: { span: TraceSpan }) {
  const attrs = Object.entries(span.attrs ?? {})
  const content = Object.entries(span.content ?? {})
  return (
    <Panel title={span.name} actions={<Tag tone={span.status === 'error' ? 'bad' : span.status === 'cancelled' ? '' : 'ok'}>{t(`tr.st.${span.status}` as TextKey)}</Tag>}>
      <KV
        rows={[
          [t('tr.spanKind'), <code key="k">{span.kind}</code>],
          [t('tr.startsAt'), ms(span.start)],
          [t('tr.duration'), ms(span.ms)],
          ...attrs.map(([k, v]) => [<code key={k}>{k}</code>, <span key={`${k}v`} class="mono" style="overflow-wrap:anywhere">{attrText(v)}</span>] as [ComponentChildren, ComponentChildren]),
        ]}
      />
      {content.length ? (
        content.map(([k, v]) => (
          <div key={k} style="margin-top:12px">
            <div class="hint">
              <code>{k}</code>
            </div>
            <pre class="raw" style="max-height:280px">{Array.isArray(v) ? v.map(contentText).join('\n\n') : contentText(v)}</pre>
          </div>
        ))
      ) : (
        <p class="hint">{t('tr.noContent')}</p>
      )}
    </Panel>
  )
}

function TraceView({ app, id }: { app: ConsoleApplication; id: string }) {
  const load = useLoad(() => forApp(app.id).admin.traces.get(id), [app.id, id])
  const [selected, setSelected] = useState<string | null>(null)
  setTitle(t('tr.trace'), app.name)
  return (
    <Loads load={load} what={t('what.trace')}>
      {(x: TraceDetail) => {
        const span = x.spans.find((s) => s.id === selected) ?? x.spans[0]
        return (
          <>
            <Hero
              title={
                <>
                  {x.agent} <small class="mono" style="font-size:18px;color:var(--mu)">{x.version}</small>
                </>
              }
              sub={`${t(`tr.kind.${x.kind}` as TextKey)} · ${dateTime(x.startedAt)}`}
              actions={
                <>
                  <Btn kind="ghost" href={appHref(app.slug, 'traces')}>
                    {t('tr.back')}
                  </Btn>
                  {x.conversation?.readable ? (
                    <Btn kind="pri" href={`${appHref(app.slug, 'conversations', x.conversation.id)}?turn=${encodeURIComponent(x.id)}`}>
                      {t('tr.openConversation')}
                    </Btn>
                  ) : null}
                </>
              }
            />
            <div class="sum" style="margin-bottom:16px">
              <div>
                <dt>{t('tr.outcome')}</dt>
                <dd>
                  <OutcomeTag x={x} />
                </dd>
              </div>
              <div>
                <dt>{t('tr.duration')}</dt>
                <dd>
                  {ms(x.durationMs)}
                  <small>{t('tr.firstTokenIn', { t: x.firstTokenMs == null ? '-' : ms(x.firstTokenMs) })}</small>
                </dd>
              </div>
              <div>
                <dt>{t('tr.tokens')}</dt>
                <dd>
                  {num(x.inputTokens + x.outputTokens)}
                  <small>{t('tr.tokensSplit', { input: num(x.inputTokens), output: num(x.outputTokens), cached: num(x.cacheReadTokens) })}</small>
                </dd>
              </div>
              <div>
                <dt>{t('tr.capShare')}</dt>
                <dd>
                  {sharePct(x.capPct)}
                  <small>{t('tr.modelCalls', { n: x.modelCalls, tools: x.toolCalls })}</small>
                </dd>
              </div>
            </div>
            <div class="grid g-21">
              <Panel title={t('tr.waterfall')} actions={<Tag>{tn('tr.nSpans', x.spans.length)}</Tag>}>
                <Waterfall spans={x.spans} selected={span?.id ?? null} onSelect={setSelected} />
                <p class="hint">{x.content === 'none' ? t('tr.contentNone') : x.content === 'full' ? t('tr.contentFull') : t('tr.contentMasked')}</p>
              </Panel>
              <div class="grid" style="gap:16px">
                {span ? <SpanPanel span={span} /> : null}
                {x.notes.length ? (
                  <Panel title={t('cv.notes')}>
                    {x.notes.map((n) => (
                      <p key={n.id} class="hint">
                        {n.label ? <Tag tone="vi">{labelText(n.label)}</Tag> : null} {n.note} <span class="s">· {n.author}</span>
                      </p>
                    ))}
                  </Panel>
                ) : null}
              </div>
            </div>
          </>
        )
      }}
    </Loads>
  )
}

// versions

function metricText(v: VersionStats, kind: 'count' | 'ms' | 'pct', key: keyof VersionStats): string {
  const value = v[key] as number | null
  if (value == null) return '-'
  return kind === 'ms' ? ms(value) : kind === 'pct' ? `${num(value, { maximumFractionDigits: 1 })}%` : num(value)
}

function Versions({ app, q }: { app: ConsoleApplication; q: URLSearchParams }) {
  const agents = useAgents(app)
  const agent = q.get('agent') ?? agents.data?.[0] ?? ''
  const load = useLoad(async () => (agent ? forApp(app.id).admin.traces.versions(agent) : null), [app.id, agent])
  const nav = (patch: Record<string, string | undefined>) => {
    const p = new URLSearchParams(q)
    p.set('view', 'versions')
    for (const [k, v] of Object.entries(patch)) (v ? p.set(k, v) : p.delete(k))
    go(`${appHref(app.slug, 'traces')}?${p.toString()}`)
  }
  return (
    <div class="grid" style="gap:16px">
      <Panel>
        <div class="grid g3" style="gap:0 14px">
          <Field label={t('tr.agent')}>
            <select class="in" value={agent} onChange={(e) => nav({ agent: e.currentTarget.value, a: undefined, b: undefined })}>
              {(agents.data ?? []).map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <p class="hint">{t('ver.hint')}</p>
      </Panel>
      <Loads load={load} what={t('what.versions')}>
        {(data) => {
          if (!data || !data.items.length)
            return (
              <Empty k="ember">
                <b>{t('ver.none')}</b>
              </Empty>
            )
          const withTurns = data.items.filter((v) => v.turns)
          const a = data.items.find((v) => v.version === q.get('a')) ?? withTurns[1] ?? data.items[1] ?? data.items[0]!
          const b = data.items.find((v) => v.version === q.get('b')) ?? withTurns[0] ?? data.items[0]!
          return (
            <>
              <Panel cls="flat" title={undefined}>
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('tr.version')}</th>
                        {METRICS.map((m) => (
                          <th key={m.key} class="r">
                            {t(`ver.m.${m.key}` as TextKey)}
                          </th>
                        ))}
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((v) => (
                        <tr key={v.version}>
                          <td>
                            <code>{v.version}</code> {v.active ? <Tag tone="ok">{t('ver.active')}</Tag> : null}
                            {v.createdAt ? <div class="s">{ago(v.createdAt)}</div> : null}
                          </td>
                          {METRICS.map((m) => (
                            <td key={m.key} class="num">
                              {metricText(v, m.kind, m.key)}
                            </td>
                          ))}
                          <td>
                            <span style="display:flex;gap:4px">
                              <Btn sm kind={v.version === a.version ? 'pri' : 'ghost'} onClick={() => nav({ a: v.version })} label={t('ver.pickA', { v: v.version })}>
                                A
                              </Btn>
                              <Btn sm kind={v.version === b.version ? 'pri' : 'ghost'} onClick={() => nav({ b: v.version })} label={t('ver.pickB', { v: v.version })}>
                                B
                              </Btn>
                              <Btn sm kind="ghost" href={href(app, { agent: data.agent, version: v.version })}>
                                {t('ver.turns')}
                              </Btn>
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
              <Panel title={t('ver.sideBySide', { a: a.version, b: b.version })}>
                <table class="tbl">
                  <thead>
                    <tr>
                      <th />
                      <th class="r">A · {a.version}</th>
                      <th class="r">B · {b.version}</th>
                      <th class="r">{t('ver.change')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {METRICS.map((m) => {
                      const d = delta(a[m.key] as number | null, b[m.key] as number | null, m.higher)
                      const sign = d && d.diff > 0 ? '+' : ''
                      const diffText = !d ? '-' : m.kind === 'ms' ? `${sign}${ms(d.diff)}` : m.kind === 'pct' ? `${sign}${num(d.diff, { maximumFractionDigits: 1 })}` : `${sign}${num(d.diff)}`
                      return (
                        <tr key={m.key}>
                          <td>{t(`ver.m.${m.key}` as TextKey)}</td>
                          <td class="num">{metricText(a, m.kind, m.key)}</td>
                          <td class="num">{metricText(b, m.kind, m.key)}</td>
                          <td class="num">{d && d.diff !== 0 && m.key !== 'turns' && m.key !== 'usageShare' ? <Tag tone={d.better ? 'ok' : 'bad'}>{diffText}</Tag> : diffText}</td>
                        </tr>
                      )
                    })}
                    <tr>
                      <td>{t('ver.outcomes')}</td>
                      <td class="s r">{outcomesText(a)}</td>
                      <td class="s r">{outcomesText(b)}</td>
                      <td />
                    </tr>
                  </tbody>
                </table>
                <p class="hint">{t('ver.deltaHint')}</p>
              </Panel>
            </>
          )
        }}
      </Loads>
    </div>
  )
}

function outcomesText(v: VersionStats): string {
  const parts = Object.entries(v.outcomes)
    .sort((x, y) => y[1] - x[1])
    .map(([k, n]) => `${outcomeLabel({ status: k, reason: k })} ${num(n)}`)
  return parts.join(', ') || '-'
}
