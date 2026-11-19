/**
 * Confirmation rules (ask rules): the editor shared by the agent editor and the Permissions page, and the application's
 * panel on Permissions. A rule only adds a confirmation; the service checks every rule again when it is saved.
 */
import { useState } from 'preact/hooks'

import type { AskRule, AskRulesView, ConsoleApplication } from '@client/core/types'
import { describe, forApp } from '../api'
import { blankRow, moveRow, ruleProblems, rulesOf, toRow, type RuleRow } from '../askRules'
import { done, sparkle, toast } from '../magic'
import { useLoad } from '../state'
import { Btn, Field, Panel, Seg, Tag } from '../ui'
import { Loads } from './common'
import { t } from '../i18n'

/** The editor of an ordered list of rules. `tools`: names offered as suggestions, with whether each changes data. */
export function RulesEditor(p: {
  rows: RuleRow[]
  onChange: (rows: RuleRow[]) => void
  tools: Array<{ name: string; mutating: boolean }>
  idPrefix: string
}) {
  const mutating = new Set(p.tools.filter((x) => x.mutating).map((x) => x.name))
  const problems = ruleProblems(p.rows, mutating)
  const listId = `${p.idPrefix}-tools`
  const put = (i: number, patch: Partial<RuleRow>) => p.onChange(p.rows.map((r, k) => (k === i ? { ...r, ...patch } : r)))
  return (
    <div class="askrules">
      <datalist id={listId}>
        {p.tools.map((x) => (
          <option key={x.name} value={x.name} />
        ))}
      </datalist>
      {p.rows.length ? null : <p class="hint">{t('ask.empty')}</p>}
      {p.rows.map((r, i) => {
        const id = `${p.idPrefix}-${i}`
        return (
          <div key={i} class="askrule" style="border-top:1px solid var(--hair);padding:12px 0">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
              <b>{t('ask.rule', { n: i + 1 })}</b>
              {mutating.has(r.action.trim()) ? <Tag tone="gold">{t('ask.writes')}</Tag> : null}
              <span style="flex:1" />
              <Btn sm disabled={i === 0} onClick={() => p.onChange(moveRow(p.rows, i, -1))}>
                {t('ask.up')}
              </Btn>
              <Btn sm disabled={i === p.rows.length - 1} onClick={() => p.onChange(moveRow(p.rows, i, 1))}>
                {t('ask.down')}
              </Btn>
              <Btn sm onClick={() => p.onChange(p.rows.filter((_, k) => k !== i))}>
                {t('ask.remove')}
              </Btn>
            </div>
            <div class="grid g2" style="gap:0 14px">
              <Field label={t('ask.action')} id={`${id}-action`} hint={t('ask.actionHint')}>
                <input id={`${id}-action`} class="in" list={listId} spellcheck={false} value={r.action} onInput={(e) => put(i, { action: e.currentTarget.value })} />
              </Field>
              <Field label={t('ask.effect')} hint={t('ask.effectHint')}>
                <Seg
                  options={['ask', 'default'] as const}
                  labels={{ ask: t('ask.effect.ask'), default: t('ask.effect.default') }}
                  value={r.effect}
                  onChange={(v) => put(i, { effect: v })}
                />
              </Field>
              <Field label={t('ask.argument')} id={`${id}-arg`} hint={t('ask.argumentHint')}>
                <input id={`${id}-arg`} class="in" spellcheck={false} value={r.argument} onInput={(e) => put(i, { argument: e.currentTarget.value })} />
              </Field>
              <Field label={t('ask.resource')} id={`${id}-res`} hint={t('ask.resourceHint')}>
                <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
                  <input id={`${id}-res`} class="in" style="flex:1 1 140px;width:auto" spellcheck={false} value={r.resource} onInput={(e) => put(i, { resource: e.currentTarget.value })} />
                  <Seg
                    options={['value', 'host'] as const}
                    labels={{ value: t('ask.part.value'), host: t('ask.part.host') }}
                    value={r.part}
                    disabled={!r.argument.trim()}
                    onChange={(v) => put(i, { part: v })}
                  />
                </div>
              </Field>
              <Field label={t('ask.labelEn')} id={`${id}-en`} hint={t('ask.labelHint')}>
                <input id={`${id}-en`} class="in" value={r.labelEn} onInput={(e) => put(i, { labelEn: e.currentTarget.value })} />
              </Field>
              <Field label={t('ask.labelIt')} id={`${id}-it`}>
                <input id={`${id}-it`} class="in" value={r.labelIt} onInput={(e) => put(i, { labelIt: e.currentTarget.value })} />
              </Field>
              <Field label={t('ask.tags')} id={`${id}-tags`} hint={t('ask.tagsHint')}>
                <input id={`${id}-tags`} class="in" spellcheck={false} value={r.tags} onInput={(e) => put(i, { tags: e.currentTarget.value })} />
              </Field>
              <Field label={t('ask.id')} id={`${id}-id`} hint={t('ask.idHint')}>
                <input id={`${id}-id`} class="in" spellcheck={false} value={r.id} onInput={(e) => put(i, { id: e.currentTarget.value })} />
              </Field>
            </div>
          </div>
        )
      })}
      {problems.map((x) => (
        <p key={`${x.n}${x.kind}`} class="hint" role="alert" style="color:var(--s-bad)">
          {x.kind === 'lifts'
            ? t('ask.liftsWrite', { n: x.n, tool: x.tool })
            : x.kind === 'orphan'
              ? t('ask.orphanDefault', { n: x.n })
              : t('ask.needsArgument', { n: x.n })}
        </p>
      ))}
      <Btn sm onClick={() => p.onChange([...p.rows, blankRow()])}>
        {t('ask.add')}
      </Btn>
    </div>
  )
}

/** A read-only list of rules (the deployment's, an agent's). */
function RuleList({ rules }: { rules: AskRule[] }) {
  return (
    <ul class="hint" style="margin:4px 0 0 18px">
      {rules.map((r, i) => (
        <li key={i}>
          <code>{r.action}</code>
          {r.argument ? (
            <>
              {' '}
              <code>{`${r.argument}${r.part === 'host' ? ` (${t('ask.part.host')})` : ''} = ${r.resource ?? '*'}`}</code>
            </>
          ) : null}{' '}
          <Tag tone={r.effect === 'default' ? '' : 'gold'}>{r.effect === 'default' ? t('ask.effect.default') : t('ask.effect.ask')}</Tag>
          {r.tags?.length ? <> {r.tags.join(', ')}</> : null}
        </li>
      ))}
    </ul>
  )
}

/** The application's confirmation rules, on the Permissions page. */
export function AskRulesPanel({ app }: { app: ConsoleApplication }) {
  const api = forApp(app.id)
  const load = useLoad(() => api.admin.askRules.get(), [app.id])
  return (
    <Loads load={load} what={t('ask.title')}>
      {(view) => <AppRules key={JSON.stringify(view.rules)} app={app} view={view} onSaved={(v) => load.set(v)} />}
    </Loads>
  )
}

function AppRules({ app, view, onSaved }: { app: ConsoleApplication; view: AskRulesView; onSaved: (v: AskRulesView) => void }) {
  const api = forApp(app.id)
  const [rows, setRows] = useState<RuleRow[]>(view.rules.map(toRow))
  const [saving, setSaving] = useState(false)
  const dirty = JSON.stringify(rulesOf(rows)) !== JSON.stringify(rulesOf(view.rules.map(toRow)))
  const save = async (e: Event) => {
    const btn = e.currentTarget as HTMLElement
    setSaving(true)
    try {
      const next = await api.admin.askRules.put(rulesOf(rows))
      sparkle(btn)
      done(btn)
      toast(t('ask.saved'), t('ask.savedSub'))
      onSaved(next)
    } catch (err) {
      toast(t('ask.notSaved'), describe(err), null)
    } finally {
      setSaving(false)
    }
  }
  return (
    <Panel
      title={t('ask.title')}
      actions={
        <Btn kind="pri" check busy={saving ? t('common.saving') : false} disabled={!dirty} onClick={(e) => void save(e)}>
          {t('ask.save')}
        </Btn>
      }
    >
      <p class="hint">{t('ask.note')}</p>
      {view.deployment.length ? (
        <>
          <p class="hint" style="margin-top:8px">
            <b>{t('ask.deployment')}</b>
          </p>
          <RuleList rules={view.deployment} />
        </>
      ) : null}
      <RulesEditor rows={rows} onChange={setRows} tools={view.tools} idPrefix="app-ask" />
      {view.agents.length ? (
        <>
          <p class="hint" style="margin-top:12px">
            <b>{t('ask.agents')}</b>
          </p>
          {view.agents.map((a) => (
            <div key={a.name} style="margin-top:6px">
              <code>{a.name}</code>
              <RuleList rules={a.rules} />
            </div>
          ))}
        </>
      ) : null}
    </Panel>
  )
}
