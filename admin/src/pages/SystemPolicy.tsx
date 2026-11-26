/**
 * The system policy (deployment owners): one policy for the whole deployment that can only make things stricter. A
 * minimum mode per control, a data protection profile every application must meet as well as its own, and default
 * settings used where an application sets nothing. With its versions, its tests and a copy from an application.
 */
import { useMemo, useState } from 'preact/hooks'

import type { SecurityControl, SecurityMode } from '@client/core/types'
import { consoleJson, describe } from '../api'
import { done, sparkle, toast } from '../magic'
import { type PolicyContent, type PolicyTestResult, systemPolicy, testFailures } from '../policy'
import { setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Seg, Tag, type Tone } from '../ui'
import { Loads } from './common'
import { CopyPolicy, PolicyHistory, PolicyTests, TestFailures } from './PolicyTools'
import { has, t, type TextKey } from '../i18n'

interface SystemView {
  version: number
  content: PolicyContent
  effective: { modes: Record<string, SecurityMode>; settings: Record<string, unknown>; floors: Record<string, SecurityMode> }
  enabled: boolean
  controls: SecurityControl[]
}

const NONE = 'none'
type Floor = typeof NONE | 'monitor' | 'enforce'
const FLOORS: readonly Floor[] = [NONE, 'monitor', 'enforce']
const modeTone = (m: SecurityMode): Tone => (m === 'enforce' ? 'ok' : m === 'monitor' ? 'gold' : '')
const controlText = (kind: 'ctl' | 'ctlx', id: string, fallback: string): string => (has(`${kind}.${id}`) ? t(`${kind}.${id}` as TextKey) : fallback)
const json = (v: unknown): string => JSON.stringify(v ?? {}, null, 2)

export function SystemPolicy() {
  setTitle(t('page.systemPolicy'))
  const load = useLoad(() => consoleJson<SystemView>('GET', '/v1/console/system-policy'), [])
  const api = useMemo(() => systemPolicy(), [])
  const c = useConsole()
  const sources = c.apps.map((a) => ({ value: a.slug, label: a.name }))
  return (
    <>
      <Loads load={load} what={t('page.systemPolicy')}>
        {(view) => <Editor key={view.version} view={view} onSaved={(v) => load.set(v)} />}
      </Loads>
      <div class="grid g2" style="margin-top:16px">
        <PolicyHistory api={api} canEdit onChanged={load.reload} stamp={load.data?.version} />
        <PolicyTests api={api} canEdit stamp={load.data?.version} />
      </div>
      <CopyPolicy api={api} sources={sources} onChanged={load.reload} />
    </>
  )
}

function Editor({ view, onSaved }: { view: SystemView; onSaved: (v: SystemView) => void }) {
  const security = (view.content.security ?? {}) as { mode?: SecurityMode; modes?: Record<string, SecurityMode> } & Record<string, unknown>
  const { mode, modes: own, ...rest } = security
  const [all, setAll] = useState<Floor>(mode && mode !== 'off' ? mode : NONE)
  const [modes, setModes] = useState<Record<string, Floor>>(Object.fromEntries(Object.entries(own ?? {}).map(([k, v]) => [k, v === 'off' ? NONE : v])))
  const [settings, setSettings] = useState(json(rest))
  const [profile, setProfile] = useState(json(view.content.data_protection))
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failures, setFailures] = useState<PolicyTestResult[] | null>(null)

  const save = async (e: Event | null, force = false) => {
    const btn = e ? (e.currentTarget as Element) : null
    let s: Record<string, unknown>
    let p: Record<string, unknown>
    try {
      s = settings.trim() ? (JSON.parse(settings) as Record<string, unknown>) : {}
      p = profile.trim() ? (JSON.parse(profile) as Record<string, unknown>) : {}
    } catch {
      setError(t('sys.invalid'))
      return
    }
    const picked = Object.fromEntries(Object.entries(modes).filter(([, v]) => v !== NONE))
    const sec = { ...s, ...(all !== NONE ? { mode: all } : {}), ...(Object.keys(picked).length ? { modes: picked } : {}) }
    setSaving(true)
    setError(null)
    try {
      const v = await consoleJson<SystemView>('PUT', '/v1/console/system-policy', {
        security: Object.keys(sec).length ? sec : null,
        data_protection: Object.keys(p).length ? p : null,
        note: note.trim() || null,
        force,
      })
      sparkle(btn)
      done(btn)
      setFailures(null)
      if (v.version !== view.version) toast(t('sys.saved'), t('sys.savedSub', { n: v.version }))
      else toast(t('sys.nothingNew'), t('pol.version', { n: v.version }), null)
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
        title={t('page.systemPolicy')}
        sub={t('sys.sub')}
        actions={
          <span style="display:flex;gap:8px;align-items:center">
            {view.version ? <Tag>{t('pol.version', { n: view.version })}</Tag> : null}
            <input class="in" style="max-width:240px" maxLength={500} value={note} placeholder={t('pol.note')} title={t('pol.noteHint')} onInput={(e) => setNote(e.currentTarget.value)} />
            <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {t('common.save')}
            </Btn>
          </span>
        }
      />
      {!view.enabled ? (
        <p class="hint" style="color:var(--s-bad);margin-bottom:12px">
          {t('sec.guardOff')}
        </p>
      ) : null}
      {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:12px">{error}</p> : null}
      {failures ? <TestFailures failures={failures} busy={saving} onForce={() => void save(null, true)} /> : null}
      <Panel
        tour="sys-controls"
        title={t('sec.controls')}
        cls="flat"
        actions={
          <span style="display:flex;gap:8px;align-items:center">
            <span class="hint" style="margin:0">{t('sys.everyControl')}</span>
            <Seg options={FLOORS} value={all} onChange={setAll} labels={{ [NONE]: t('sys.none') }} />
          </span>
        }
      >
        <div class="wx">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('sec.control')}</th>
                <th>{t('sys.now')}</th>
                <th>{t('sys.minimum')}</th>
              </tr>
            </thead>
            <tbody>
              {view.controls.map((c) => {
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
                      <Tag tone={modeTone(now)} dot>
                        {now}
                      </Tag>
                      {now !== c.default ? <div class="s">{t('sec.defaultMode', { mode: c.default })}</div> : null}
                    </td>
                    <td style="white-space:nowrap">
                      <Seg options={FLOORS} value={modes[c.id] ?? NONE} labels={{ [NONE]: t('sys.none') }} onChange={(v) => setModes({ ...modes, [c.id]: v })} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </Panel>
      <div class="grid g2">
        <Panel title={t('sys.defaults')}>
          <Field label={t('sec.policySettings')} hint={t('sys.defaultsHint')}>
            <textarea class="in" value={settings} spellcheck={false} onInput={(e) => setSettings(e.currentTarget.value)} />
          </Field>
          <details>
            <summary class="hint">{t('sec.appliesNow')}</summary>
            <pre class="mono" style="white-space:pre-wrap;font-size:12px">{JSON.stringify(view.effective.settings, null, 2)}</pre>
          </details>
        </Panel>
        <Panel title={t('sys.protection')}>
          <Field label={t('sys.protection')} hint={t('sys.protectionHint')}>
            <textarea class="in" value={profile} spellcheck={false} onInput={(e) => setProfile(e.currentTarget.value)} />
          </Field>
        </Panel>
      </div>
    </>
  )
}
