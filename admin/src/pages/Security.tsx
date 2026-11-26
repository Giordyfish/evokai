/**
 * Security: the application's policy (each control off, in monitor or enforced, with its OWASP ids, and the minimum the
 * system policy sets), what each agent overrides, the policy's versions and tests, a copy from another policy, the
 * events with filters and their counts by rule. Snippets are masked by the service: no secret or raw personal data
 * ever reaches this page.
 */
import { useMemo, useState } from 'preact/hooks'

import type {
  Application,
  ConsoleApplication,
  SecurityEvent,
  SecurityEventQuery,
  SecurityMode,
  SecurityPolicy,
  SecurityView,
} from '@client/core/types'
import { adminJson, describe, forApp } from '../api'
import { GATEWAY_CONTROLS } from '../gateway'
import { appPolicy, type PolicyTestResult, testFailures } from '../policy'
import { CopyPolicy, PolicyHistory, PolicyTests, TestFailures } from './PolicyTools'
import {
  CLASSIFIER_CHOICES,
  type Choice,
  type ClassifierChoice,
  classifierIn,
  INHERIT,
  MODES,
  policyFrom,
  settingsOf,
  UNSET,
  withClassifier,
} from '../security'
import { ago, csv, dateTime, download } from '../format'
import { DEFAULT_MIN_SCORE, GATE_MODES, type GateMode } from '../redteam'
import { done, Empty, sparkle, toast } from '../magic'
import { appHref, setTitle, useConsole, useLoad } from '../state'
import { Bar, Btn, Field, Hero, Panel, Seg, Switch, Tag, type Tone } from '../ui'
import { Loads } from './common'
import { has, num, t, tn, type TextKey } from '../i18n'

/** A control's label or enforce text in the console's language; the service's English for a control it does not know yet. */
const controlText = (kind: 'ctl' | 'ctlx', id: string, fallback: string): string => {
  const key = `${kind}.${id}`
  return has(key) ? t(key) : fallback
}

/** A classifier choice as people read it (terms of art such as Bedrock Guardrails stay as they are). */
const classifierName = (c: string): string => (has(`sec.clf.n.${c}`) ? t(`sec.clf.n.${c}` as TextKey) : c)

const modeTone = (m: SecurityMode): Tone => (m === 'enforce' ? 'ok' : m === 'monitor' ? 'gold' : '')
const actionTone = (a: string): Tone => (a === 'blocked' ? 'bad' : a === 'logged' ? '' : 'vi')
const sevTone = (s: string): Tone => (s === 'high' ? 'bad' : s === 'medium' ? 'warn' : '')
const ACTIONS = ['blocked', 'redacted', 'removed', 'tokenized', 'logged'] as const
const SEVERITIES = ['high', 'medium', 'low'] as const
/** An event's action or severity in the console's language (unknown values as the service sent them). */
const actionLabel = (a: string): string => ((ACTIONS as readonly string[]).includes(a) ? t(`sec.a.${a}` as TextKey) : a)
const sevLabel = (s: string): string => ((SEVERITIES as readonly string[]).includes(s) ? t(`sec.s.${s}` as TextKey) : s)

export function Security({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.security'), app.name)
  const api = forApp(app.id)
  const c = useConsole()
  const load = useLoad(() => api.admin.security.get(), [app.id])
  const policy = useMemo(() => appPolicy(app.id), [app.id])
  const owner = app.role === 'owner'
  // What this person may copy from: the system policy (deployment owners) and the other applications they see.
  // A slug never starts with '*': the system policy's entry cannot be taken for an application.
  const sources = [
    ...(c.me.deploymentOwner ? [{ value: '*system', label: t('pol.systemSource'), system: true }] : []),
    ...c.apps.filter((a) => a.id !== app.id).map((a) => ({ value: a.slug, label: a.name })),
  ]
  return (
    <>
      <Loads load={load} what={t('what.securityPolicy')}>
        {(view) => <Policy key={JSON.stringify(view.policy)} app={app} view={view} onSaved={(v) => load.set(v)} onReload={load.reload} />}
      </Loads>
      <div class="grid g2" style="margin-top:16px">
        <PolicyHistory api={policy} canEdit={owner} onChanged={load.reload} stamp={load.data?.version} />
        <PolicyTests api={policy} canEdit={owner} stamp={load.data?.version} />
      </div>
      {owner ? <CopyPolicy api={policy} sources={sources} onChanged={load.reload} /> : null}
      <RedTeamGate app={app} />
      <Events app={app} />
    </>
  )
}

/**
 * The red team gate (owner): what happens when a saved version scores below the minimum (`off` nothing, `monitor` a
 * warning, `enforce` the version waits and is held back), the minimum score, and whether every saved version is attacked.
 */
function RedTeamGate({ app }: { app: ConsoleApplication }) {
  const load = useLoad(() => forApp(app.id).admin.application.get(), [app.id])
  return (
    <Loads load={load} what={t('what.application')}>
      {(a) => <GateForm key={`${a.redteam_gate}/${a.redteam_min_score}/${a.redteam_on_save}`} app={app} a={a} reload={load.reload} />}
    </Loads>
  )
}

function GateForm({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const owner = app.role === 'owner'
  const [mode, setMode] = useState<GateMode>(a.redteam_gate ?? 'off')
  const [min, setMin] = useState(String(a.redteam_min_score ?? DEFAULT_MIN_SCORE))
  const [onSave, setOnSave] = useState(a.redteam_on_save !== false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    const score = Number(min.replace(',', '.'))
    if (min.trim() === '' || Number.isNaN(score) || score < 0 || score > 100) return setError(t('sec.rt.minRange'))
    setBusy(true)
    setError(null)
    try {
      await forApp(app.id).admin.application.patch({ redteam_gate: mode, redteam_min_score: score, redteam_on_save: onSave })
      sparkle(btn)
      done(btn)
      toast(t('sec.rt.saved'), t(`sec.rt.m.${mode}`))
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel
      style="margin-top:16px"
      title={
        <>
          {t('sec.rt.title')} <Tag tone={modeTone(a.redteam_gate ?? 'off')} dot>{a.redteam_gate ?? 'off'}</Tag>
        </>
      }
      actions={
        owner ? (
          <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
        ) : null
      }
    >
      <p class="hint" style="margin:0 0 12px">
        {t('sec.rt.sub')}
      </p>
      {!owner ? <p class="hint">{t('sec.rt.ownerOnly')}</p> : null}
      {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('sec.rt.mode')}>
          <Seg options={GATE_MODES} value={mode} onChange={setMode} disabled={!owner} />
          <ul class="opts">
            {GATE_MODES.map((m) => (
              <li key={m} aria-current={m === mode ? 'true' : undefined}>
                <code>{m}</code> {t(`sec.rt.m.${m}`)}
              </li>
            ))}
          </ul>
        </Field>
        <div>
          <Field label={t('sec.rt.min')} id="rt-min" hint={t('sec.rt.minHint', { n: DEFAULT_MIN_SCORE })}>
            <div class="affix">
              <input id="rt-min" class="in num" inputMode="decimal" value={min} disabled={!owner} onInput={(e) => setMin(e.currentTarget.value)} />
              <span>/100</span>
            </div>
          </Field>
          <Field label={t('sec.rt.onSave')} hint={t('sec.rt.onSaveHint')}>
            <Switch on={onSave} disabled={!owner} onChange={setOnSave} label={t(onSave ? 'sec.rt.onSaveOn' : 'sec.rt.onSaveOff')} />
          </Field>
        </div>
      </div>
    </Panel>
  )
}

/** An agent's own policy, edited as JSON; saving stores a new version of the agent. */
/** Whose policy a page edits: an application, or a gateway (stored as an application row, `src/gateway.ts`). */
export type PolicyOwner = Pick<ConsoleApplication, 'id' | 'slug' | 'name' | 'role'>

function AgentPolicy(p: { app: PolicyOwner; name: string; policy: SecurityPolicy | null; onSaved: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState(JSON.stringify(p.policy ?? {}, null, 2))
  const [busy, setBusy] = useState(false)
  if (!open)
    return (
      <Btn sm kind="ghost" onClick={() => setOpen(true)}>
        {t('sec.editPolicy')}
      </Btn>
    )
  const save = async () => {
    let policy: SecurityPolicy | null
    try {
      const parsed = text.trim() ? (JSON.parse(text) as SecurityPolicy) : {}
      policy = Object.keys(parsed).length ? parsed : null
    } catch {
      toast(t('sec.notSaved'), t('sec.policyInvalid'), null)
      return
    }
    setBusy(true)
    try {
      const api = forApp(p.app.id)
      const def = (await api.admin.agents.list()).find((a) => a.name === p.name)
      if (!def) throw new Error(t('sec.agentGone', { name: p.name }))
      const { version: _v, ...rest } = def
      const r = await api.admin.agents.put({ ...rest, security: policy })
      toast(t(r.created ? 'sec.agentSaved' : 'sec.nothingNew'), r.created ? t('sec.newConvos') : p.name)
      setOpen(false)
      await p.onSaved()
    } catch (err) {
      toast(t('sec.notSaved'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div style="margin-top:8px">
      <textarea class="in" style="min-height:120px" value={text} spellcheck={false} onInput={(e) => setText(e.currentTarget.value)} />
      <div style="display:flex;gap:8px;margin-top:6px">
        <Btn sm kind="pri" busy={busy ? t('common.saving') : false} onClick={() => void save()}>
          {t('sec.saveName', { name: p.name })}
        </Btn>
        <Btn sm kind="ghost" onClick={() => setOpen(false)}>
          {t('common.cancel')}
        </Btn>
      </div>
    </div>
  )
}

/**
 * The policy's controls and settings, saved as a new version. On a gateway only the controls a gateway runs are listed
 * (`GATEWAY_CONTROLS`), and there is no classifier and no agent.
 */
export function Policy({
  app,
  view,
  onSaved,
  onReload,
  gateway = false,
}: {
  app: PolicyOwner
  view: SecurityView
  onSaved: (v: SecurityView) => void
  onReload: () => Promise<void>
  gateway?: boolean
}) {
  const controls = gateway ? view.controls.filter((c) => (GATEWAY_CONTROLS as readonly string[]).includes(c.id)) : view.controls
  const [base, setBase] = useState<Choice>(view.policy.mode ?? INHERIT)
  const [modes, setModes] = useState<Record<string, Choice>>({ ...(view.policy.modes ?? {}) })
  const [json, setJson] = useState(JSON.stringify(settingsOf(view.policy), null, 2))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [failures, setFailures] = useState<PolicyTestResult[] | null>(null)
  const floors = view.effective.floors ?? {}

  const save = async (e: Event | null, force = false) => {
    const btn = e ? (e.currentTarget as Element) : null
    let settings: Record<string, unknown>
    try {
      settings = json.trim() ? (JSON.parse(json) as Record<string, unknown>) : {}
    } catch {
      setError(t('sec.settingsInvalid'))
      return
    }
    setSaving(true)
    setError(null)
    try {
      // A save is a new version of the policy: the note goes with it; force saves although policy tests fail.
      const q = new URLSearchParams()
      if (note.trim()) q.set('note', note.trim())
      if (force) q.set('force', 'true')
      const query = q.toString()
      const v = await adminJson<SecurityView>(app.id, 'PUT', `/v1/admin/security${query ? `?${query}` : ''}`, policyFrom(base, modes, settings))
      sparkle(btn)
      done(btn)
      setFailures(null)
      toast(t('sec.saved'), t('sec.savedSub'))
      onSaved(v)
    } catch (err) {
      const f = testFailures(err)
      if (f) setFailures(f)
      else setError(describe(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Hero
        title={gateway ? t('gw.tab.policy') : t('tab.security')}
        sub={gateway ? t('gw.policySub') : t('sec.sub')}
        actions={
          <span style="display:flex;gap:8px;align-items:center">
            <input class="in" style="max-width:240px" maxLength={500} value={note} placeholder={t('pol.note')} title={t('pol.noteHint')} onInput={(e) => setNote(e.currentTarget.value)} />
            <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {t('common.save')}
            </Btn>
          </span>
        }
      />
      {failures ? <TestFailures failures={failures} busy={saving} onForce={() => void save(null, true)} /> : null}
      {!view.enabled ? (
        <p class="hint" style="color:var(--s-bad);margin-bottom:12px">
          {t('sec.guardOff')}
        </p>
      ) : null}
      {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:12px">{error}</p> : null}
      <Panel
        tour="sec-controls"
        title={t('sec.controls')}
        actions={
          <span style="display:flex;gap:8px;align-items:center">
            <span class="hint" style="margin:0">{t('sec.everyControl')}</span>
            <Seg options={[INHERIT, ...MODES] as const} value={base} onChange={setBase} labels={{ [INHERIT]: t('sec.defaults') }} />
          </span>
        }
        cls="flat"
      >
        <div class="wx">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('sec.control')}</th>
                <th>OWASP</th>
                <th>{t('sec.now')}</th>
                <th>{gateway ? t('gw.thisGateway') : t('sec.application')}</th>
              </tr>
            </thead>
            <tbody>
              {controls.map((c) => {
                const now = view.effective.modes[c.id] ?? c.default
                return (
                  <tr key={c.id}>
                    <td>
                      <b>{controlText('ctl', c.id, c.label)}</b>
                      <div class="s">
                        <code>{c.id}</code> · {t('sec.enforceWith', { how: controlText('ctlx', c.id, c.enforce) })}
                      </div>
                    </td>
                    <td>
                      {c.owasp.map((o) => (
                        <Tag key={o}>{o}</Tag>
                      ))}
                    </td>
                    <td>
                      <Tag tone={modeTone(now)} dot>
                        {now}
                      </Tag>
                      {now !== c.default ? <div class="s">{t('sec.defaultMode', { mode: c.default })}</div> : null}
                      {floors[c.id] ? <div class="s">{t('pol.min', { mode: floors[c.id]! })}</div> : null}
                    </td>
                    <td style="white-space:nowrap">
                      <Seg
                        options={[INHERIT, ...MODES] as const}
                        value={modes[c.id] ?? INHERIT}
                        labels={{ [INHERIT]: t('sec.inherit') }}
                        onChange={(v) => setModes({ ...modes, [c.id]: v })}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
      {gateway ? null : <ClassifierPanel view={view} json={json} setJson={setJson} />}
      <div class="grid g2">
        <Panel title={t('sec.settings')}>
          <Field label={t('sec.policySettings')} hint={t('sec.policySettingsHint')}>
            <textarea class="in" value={json} spellcheck={false} onInput={(e) => setJson(e.currentTarget.value)} />
          </Field>
          <details>
            <summary class="hint">{t('sec.appliesNow')}</summary>
            <pre class="mono" style="white-space:pre-wrap;font-size:12px">{JSON.stringify(view.effective.settings, null, 2)}</pre>
          </details>
        </Panel>
        {gateway ? (
          <Panel title={t('gw.checksTitle')}>
            <p class="hint">{t('gw.checksText')}</p>
            <p class="hint">{t('gw.neverMask')}</p>
          </Panel>
        ) : (
        <Panel title={t('sec.agents')}>
          {view.agents.length ? (
            <ul class="plain">
              {view.agents.map((a) => {
                const own = Object.entries(a.security?.modes ?? {})
                return (
                  <li key={a.name} style="margin-bottom:10px">
                    <a href={appHref(app.slug, 'agents', a.name)}>
                      <b>{a.name}</b>
                    </a>{' '}
                    {a.security?.mode ? <Tag tone={modeTone(a.security.mode)}>{t('sec.allMode', { mode: a.security.mode })}</Tag> : null}
                    {a.security ? <AgentClassifier policy={a.security} /> : null}
                    {own.length ? (
                      own.map(([k, v]) => (
                        <Tag key={k} tone={modeTone(v)}>
                          {k}: {v}
                        </Tag>
                      ))
                    ) : !a.security?.mode ? (
                      <span class="hint" style="margin:0">
                        {t('sec.followsApp')}
                      </span>
                    ) : null}{' '}
                    <AgentPolicy app={app} name={a.name} policy={a.security} onSaved={onReload} />
                  </li>
                )
              })}
            </ul>
          ) : (
            <p class="hint">{t('sec.noAgents')}</p>
          )}
          <p class="hint">{t('sec.agentOverride')}</p>
          <p class="hint">
            {(view.eventTtlDays ?? view.defaultEventTtlDays)
              ? t('sec.eventsKeptDays', { n: (view.eventTtlDays ?? view.defaultEventTtlDays)! })
              : t('sec.eventsKeptForever')}
          </p>
        </Panel>
        )}
      </div>
    </>
  )
}

/** An agent's own classifier choice, when it has one. */
function AgentClassifier({ policy }: { policy: SecurityPolicy }) {
  const own = classifierIn(JSON.stringify(settingsOf(policy)))
  if (!own || own.choice === UNSET) return null
  return <Tag tone="vi">{t('sec.clf.agent', { name: classifierName(own.choice) })}</Tag>
}

/**
 * The prompt-injection classifier of the application: the choice (kept in the settings JSON, which stays the one
 * source of what is saved), what this deployment can actually use and why not, and the characters a turn may send.
 */
function ClassifierPanel({ view, json, setJson }: { view: SecurityView; json: string; setJson: (v: string) => void }) {
  const current = classifierIn(json)
  const set = (patch: Parameters<typeof withClassifier>[1]) => {
    const next = withClassifier(json, patch)
    if (next !== null) setJson(next)
  }
  const effective = (view.effective.settings.injection ?? {}) as { classifier?: string | boolean; classifier_max_chars?: number }
  const now = effective.classifier === true ? 'default' : effective.classifier === false ? 'none' : (effective.classifier ?? 'none')
  const nowName = now === 'default' ? (view.defaultClassifier ? classifierName(view.defaultClassifier) : classifierName('none')) : classifierName(now)
  const available = new Map(view.classifiers.map((c) => [c.id, c.available]))
  const labels = Object.fromEntries([[UNSET, t('sec.inherit')], ...CLASSIFIER_CHOICES.map((c) => [c, classifierName(c)])]) as Record<
    ClassifierChoice | typeof UNSET,
    string
  >
  return (
    <Panel title={t('sec.clf.title')} actions={<Tag tone={now === 'none' ? '' : 'ok'}>{t('sec.clf.now', { name: nowName })}</Tag>}>
      <p class="hint">{t('sec.clf.sub')}</p>
      {current ? (
        <Field label={t('sec.clf.choice')}>
          <Seg
            options={[UNSET, ...CLASSIFIER_CHOICES] as const}
            value={current.choice}
            labels={labels}
            onChange={(v) => set({ choice: v })}
          />
        </Field>
      ) : (
        <p class="hint" style="color:var(--s-bad)">
          {t('sec.clf.invalidJson')}
        </p>
      )}
      {current?.choice === 'default' || current?.choice === UNSET ? (
        <p class="hint">
          {view.defaultClassifier ? t('sec.clf.defaultIs', { name: classifierName(view.defaultClassifier) }) : t('sec.clf.defaultNone')}
        </p>
      ) : null}
      {current && current.choice !== UNSET && current.choice !== 'none' && current.choice !== 'default' && !available.get(current.choice) ? (
        <p class="hint" style="color:var(--s-bad)">
          {t('sec.clf.chosenUnavailable')}
        </p>
      ) : null}
      <div class="wx">
        <table class="tbl">
          <thead>
            <tr>
              <th>{t('sec.clf.choice')}</th>
              <th>{t('sec.clf.here')}</th>
            </tr>
          </thead>
          <tbody>
            {view.classifiers.map((c) => (
              <tr key={c.id}>
                <td>
                  <b>{classifierName(c.id)}</b>
                  <div class="s">
                    <code>{c.id}</code> · {t(`sec.clf.d.${c.id}` as TextKey)}
                  </div>
                </td>
                <td>
                  <Tag tone={c.available ? 'ok' : ''} dot>
                    {c.available ? t('sec.clf.available') : t('sec.clf.unavailable')}
                  </Tag>
                  {c.reason ? <div class="s mono" style="overflow-wrap:anywhere">{c.reason}</div> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {current ? (
        <Field label={t('sec.clf.maxChars')} hint={t('sec.clf.maxCharsHint', { n: num(effective.classifier_max_chars ?? 24000) })}>
          <input
            class="in mono"
            type="number"
            min={0}
            step={1000}
            value={current.maxChars ?? ''}
            placeholder={String(effective.classifier_max_chars ?? 24000)}
            onChange={(e) => {
              const v = e.currentTarget.value.trim()
              set({ maxChars: v === '' ? null : Math.max(0, Math.floor(Number(v))) })
            }}
          />
        </Field>
      ) : null}
    </Panel>
  )
}

const CONTROL_FILTERS: Array<[string, TextKey]> = [
  ['', 'sec.everyControl'],
  ['input.', 'sec.f.input'],
  ['untrusted.', 'sec.f.untrusted'],
  ['pii.', 'sec.f.pii'],
  ['output.', 'sec.f.output'],
  ['tools.', 'sec.f.tools'],
  ['abuse.', 'sec.f.abuse'],
  ['scope', 'sec.f.scope'],
  ['memory.write', 'sec.f.memory'],
  ['turn.budget', 'sec.f.budget'],
]

export function Events({ app }: { app: PolicyOwner }) {
  const api = forApp(app.id)
  const [q, setQ] = useState<SecurityEventQuery>({})
  const [more, setMore] = useState<SecurityEvent[]>([])
  const [next, setNext] = useState<string | null>(null)
  const load = useLoad(async () => {
    const [page, summary] = await Promise.all([api.admin.security.events({ ...q, limit: 50 }), api.admin.security.summary()])
    setMore([])
    setNext(page.next)
    return { page, summary }
  }, [app.id, JSON.stringify(q)])

  const loadMore = async () => {
    if (!next) return
    try {
      const page = await api.admin.security.events({ ...q, before: next, limit: 50 })
      setMore([...more, ...page.items])
      setNext(page.next)
    } catch (err) {
      toast(t('sec.notLoaded'), describe(err), null)
    }
  }
  const exportCsv = (items: SecurityEvent[]) =>
    download(
      `${app.slug}-security-events.csv`,
      csv([
        ['at', 'control', 'rule', 'action', 'mode', 'severity', 'agent', 'tool', 'subject', 'snippet'],
        ...items.map((e) => [e.at, e.control, e.rule, e.action, e.mode, e.severity, e.agent, e.tool, e.subject, e.snippet]),
      ]),
      'text/csv',
    )

  return (
    <Loads load={load} what={t('what.securityEvents')}>
      {({ page, summary }) => {
        const items = [...page.items, ...more]
        const top = summary.byRule.slice(0, 8)
        const max = Math.max(1, ...top.map((r) => r.count))
        return (
          <div class="grid g2" style="margin-top:16px" data-tour="sec-events">
            <Panel title={t('sec.byRule')} actions={<Tag>{tn('sec.nEvents', summary.total)}</Tag>}>
              {top.length ? (
                <table class="tbl">
                  <tbody>
                    {top.map((r) => (
                      <tr key={`${r.control}/${r.rule}`} data-go onClick={() => setQ({ ...q, control: r.control, rule: r.rule })}>
                        <td>
                          <b>{r.rule}</b>
                          <div class="s">
                            <code>{r.control}</code>
                          </div>
                        </td>
                        <td style="width:40%">
                          <Bar value={(r.count / max) * 100} />
                        </td>
                        <td class="num">
                          {r.count}
                          {r.acted ? <div class="s">{t('sec.actedOn', { n: r.acted })}</div> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <Empty k="ember">
                  <b>{t('sec.nothing')}</b>
                  <br />
                  {t('sec.nothingText')}
                </Empty>
              )}
            </Panel>
            <Panel title={t('sec.filters')}>
              <div class="grid g2" style="gap:0 14px">
                <Field label={t('sec.control')}>
                  <select class="in" value={q.control ?? ''} onChange={(e) => setQ({ ...q, control: e.currentTarget.value || undefined, rule: undefined })}>
                    {CONTROL_FILTERS.map(([v, l]) => (
                      <option key={v} value={v}>
                        {t(l)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t('sec.action')}>
                  <select class="in" value={q.action ?? ''} onChange={(e) => setQ({ ...q, action: (e.currentTarget.value || undefined) as SecurityEventQuery['action'] })}>
                    <option value="">{t('sec.any')}</option>
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>
                        {actionLabel(a)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t('sec.severity')}>
                  <select class="in" value={q.severity ?? ''} onChange={(e) => setQ({ ...q, severity: (e.currentTarget.value || undefined) as SecurityEventQuery['severity'] })}>
                    <option value="">{t('sec.any')}</option>
                    {SEVERITIES.map((s) => (
                      <option key={s} value={s}>
                        {sevLabel(s)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t('sec.subject')}>
                  <input class="in mono" value={q.subject ?? ''} placeholder={t('sec.any')} onChange={(e) => setQ({ ...q, subject: e.currentTarget.value.trim() || undefined })} />
                </Field>
              </div>
              {q.rule ? (
                <p class="hint">
                  {t('sec.rule')} <code>{q.rule}</code>{' '}
                  <Btn sm kind="ghost" onClick={() => setQ({ ...q, rule: undefined })}>
                    {t('sec.clear')}
                  </Btn>
                </p>
              ) : null}
            </Panel>
            <Panel
              title={t('sec.events')}
              cls="flat"
              style="grid-column:1/-1"
              actions={
                items.length ? (
                  <Btn sm kind="ghost" onClick={() => exportCsv(items)}>
                    {t('usage.export')}
                  </Btn>
                ) : undefined
              }
            >
              {items.length ? (
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('audit.when')}</th>
                        <th>{t('sec.rule')}</th>
                        <th>{t('sec.action')}</th>
                        <th>{t('sec.where')}</th>
                        <th>{t('sec.excerpt')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((e) => (
                        <tr key={e.id}>
                          <td class="s" title={dateTime(e.at)}>
                            {ago(e.at)}
                          </td>
                          <td>
                            <b>{e.rule}</b>
                            <div class="s">
                              <code>{e.control}</code> <Tag tone={sevTone(e.severity)}>{sevLabel(e.severity)}</Tag>
                            </div>
                          </td>
                          <td>
                            <Tag tone={actionTone(e.action)} dot>
                              {actionLabel(e.action)}
                            </Tag>
                          </td>
                          <td class="s">
                            {e.agent || '-'}
                            {e.tool ? ` · ${e.tool}` : ''}
                            <div>{e.subject ?? ''}</div>
                          </td>
                          <td class="s mono" style="max-width:420px;overflow-wrap:anywhere">
                            {e.snippet || JSON.stringify(e.details)}
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
                  <b>{t('sec.noMatch')}</b>
                </Empty>
              )}
            </Panel>
          </div>
        )
      }}
    </Loads>
  )
}
