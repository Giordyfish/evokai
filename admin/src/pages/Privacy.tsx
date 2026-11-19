/**
 * Privacy: a person's data (export as JSON, sign out everywhere, erase), the application's data residency and how
 * long each kind of data is kept. An export made here carries no amounts.
 */
import { useState } from 'preact/hooks'

import type { Application, ConsoleApplication, Residency } from '@client/core/types'
import { describe, forApp } from '../api'
import { download } from '../format'
import { done, sparkle, toast } from '../magic'
import { appHref, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Seg, Switch, Tag } from '../ui'
import { Loads } from './common'
import { Protection } from './Protection'
import { t } from '../i18n'

const GEOGRAPHIES = ['eu', 'us', 'apac', 'global'] as const

export function Privacy({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.privacy'), app.name)
  const load = useLoad(() => forApp(app.id).admin.application.get(), [app.id])
  return (
    <>
      <Hero title={t('tab.privacy')} sub={t('privacy.sub')} />
      <div class="grid g2">
        <Subject app={app} />
        <Loads load={load} what={t('what.application')}>
          {(a) => <Residence key={JSON.stringify(a.residency ?? null)} app={app} a={a} reload={load.reload} />}
        </Loads>
      </div>
      <Loads load={load} what={t('what.application')}>
        {(a) => <Protection app={app} a={a} reload={load.reload} />}
      </Loads>
      <Loads load={load} what={t('what.application')}>
        {(a) => <Memory key={`${a.memory_mode}/${a.memory_ttl_days}`} app={app} a={a} reload={load.reload} />}
      </Loads>
    </>
  )
}

function Subject({ app }: { app: ConsoleApplication }) {
  const api = forApp(app.id)
  const [subject, setSubject] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const s = subject.trim()

  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what)
    try {
      await fn()
    } catch (err) {
      toast(t('privacy.notDone'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }
  const exportData = () =>
    run('export', async () => {
      const data = await api.privacy.export(s)
      download(`${app.slug}-subject-export.json`, JSON.stringify(data, null, 2), 'application/json')
      toast(t('privacy.exportReady'), t('privacy.exportSub', { conversations: data.conversations.length, memories: data.memories.length }), null)
    })
  const signOut = () =>
    run('signout', async () => {
      await api.privacy.revokeSessions(s)
      toast(t('privacy.signedOut'), t('privacy.signedOutSub'), null)
    })
  const erase = () =>
    run('erase', async () => {
      const r = await api.privacy.erase(s)
      setConfirm('')
      toast(t('privacy.erased'), t('privacy.erasedSub', { conversations: r.conversations, memories: r.memories }), null)
    })

  return (
    <Panel title={t('privacy.personData')}>
      <Field label={t('privacy.subject')} hint={t('privacy.subjectHint')}>
        <input class="in mono" value={subject} onInput={(e) => setSubject(e.currentTarget.value)} autocomplete="off" spellcheck={false} />
      </Field>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
        <Btn disabled={!s} busy={busy === 'export' ? t('privacy.exporting') : false} onClick={() => void exportData()}>
          {t('privacy.exportJson')}
        </Btn>
        <Btn kind="ghost" disabled={!s} busy={busy === 'signout' ? t('privacy.signingOut') : false} onClick={() => void signOut()}>
          {t('privacy.signOutAll')}
        </Btn>
      </div>
      <Field
        label={t('privacy.erase')}
        hint={t('privacy.eraseHint')}
      >
        <input class="in mono" value={confirm} placeholder={t('privacy.sameSubject')} onInput={(e) => setConfirm(e.currentTarget.value)} autocomplete="off" />
      </Field>
      <Btn kind="danger" disabled={!s || confirm.trim() !== s} busy={busy === 'erase' ? t('privacy.erasing') : false} onClick={() => void erase()}>
        {t('privacy.eraseBtn')}
      </Btn>
    </Panel>
  )
}

const MEMORY_MODES = ['off', 'ask', 'on'] as const
type MemoryMode = (typeof MEMORY_MODES)[number]

/**
 * Long-term memory: off, ask (only people who said yes, asked once in the chat panel or by the application when it
 * mints their token) or on (everyone, unless a person said no), and how long a memory is kept.
 */
function Memory({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const [mode, setMode] = useState<MemoryMode>(a.memory_mode ?? 'on')
  const [ttl, setTtl] = useState(a.memory_ttl_days == null ? '' : String(a.memory_ttl_days))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    const days = ttl.trim() === '' ? null : Number(ttl)
    if (days != null && (!Number.isInteger(days) || days < 1 || days > 3650)) return setError(t('privacy.mem.ttlWhole'))
    setSaving(true)
    setError(null)
    try {
      await forApp(app.id).admin.application.patch({ memory_mode: mode, memory_ttl_days: days })
      sparkle(btn)
      done(btn)
      toast(t('privacy.mem.saved'), t(`privacy.mem.m.${mode}`))
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setSaving(false)
    }
  }
  return (
    <Panel
      title={
        <>
          {t('privacy.mem.title')} <Tag tone={a.memory_mode === 'off' ? '' : 'ok'} dot>{a.memory_mode ?? 'on'}</Tag>
        </>
      }
      style="margin-top:16px"
      actions={
        <Btn sm kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
          {t('common.save')}
        </Btn>
      }
    >
      <p class="hint" style="margin:0 0 12px">
        {t('privacy.mem.sub')}
      </p>
      {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('privacy.mem.mode')}>
          <Seg options={MEMORY_MODES} value={mode} onChange={setMode} />
          <ul class="opts">
            {MEMORY_MODES.map((m) => (
              <li key={m} aria-current={m === mode ? 'true' : undefined}>
                <code>{m}</code> {t(`privacy.mem.m.${m}`)}
              </li>
            ))}
          </ul>
        </Field>
        <Field label={t('privacy.mem.ttl')} hint={t('privacy.mem.ttlHint')}>
          <div class="affix">
            <input class="in num" inputMode="numeric" value={ttl} placeholder={t('limits.keptPh')} onInput={(e) => setTtl(e.currentTarget.value)} />
            <span>{t('privacy.days')}</span>
          </div>
        </Field>
      </div>
    </Panel>
  )
}

/** The residency as the form edits it. */
export function residencyFrom(region: string, regions: string[], models: string): Residency | null {
  const patterns = models
    .split('\n')
    .map((x) => x.trim())
    .filter(Boolean)
  const out: Residency = {}
  if (region.trim()) out.region = region.trim()
  if (regions.length) out.model_regions = regions
  if (patterns.length) out.models = patterns
  return Object.keys(out).length ? out : null
}

function Residence({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const c = useConsole()
  const r = a.residency ?? {}
  const [region, setRegion] = useState(r.region ?? '')
  const [regions, setRegions] = useState<string[]>(r.model_regions ?? [])
  const [models, setModels] = useState((r.models ?? []).join('\n'))
  const [ttl, setTtl] = useState(a.security_event_ttl_days == null ? '' : String(a.security_event_ttl_days))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    const days = ttl.trim() === '' ? null : Number(ttl)
    if (days != null && (!Number.isInteger(days) || days < 1)) {
      setError(t('privacy.retentionWhole'))
      return
    }
    setSaving(true)
    setError(null)
    try {
      await forApp(app.id).admin.application.patch({ residency: residencyFrom(region, regions, models), security_event_ttl_days: days })
      sparkle(btn)
      done(btn)
      toast(t('privacy.saved'), t('privacy.savedSub'))
      await c.reloadApps()
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Panel
      title={t('privacy.residency')}
      actions={
        <Btn sm kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
          {t('common.save')}
        </Btn>
      }
    >
      {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
      <Field label={t('privacy.where')} hint={t('privacy.whereHint')}>
        <input class="in mono" value={region} placeholder={t('privacy.notDeclared')} onInput={(e) => setRegion(e.currentTarget.value)} />
      </Field>
      <Field label={t('privacy.modelsRun')} hint={t('privacy.modelsRunHint')}>
        <div style="display:grid;gap:8px">
          {GEOGRAPHIES.map((k) => (
            <Switch
              key={k}
              on={regions.includes(k)}
              label={t(`privacy.geo.${k}`)}
              onChange={(v) => setRegions(v ? [...regions, k] : regions.filter((x) => x !== k))}
            />
          ))}
        </div>
      </Field>
      <Field label={t('privacy.otherModels')} hint={t('privacy.otherModelsHint')}>
        <textarea class="in" style="min-height:70px" value={models} onInput={(e) => setModels(e.currentTarget.value)} />
      </Field>
      <Field label={t('privacy.eventsKept')} hint={t('privacy.eventsKeptHint')}>
        <div class="affix">
          <input class="in num" inputMode="numeric" value={ttl} placeholder={t('privacy.defaultPh')} onInput={(e) => setTtl(e.currentTarget.value)} />
          <span>{t('privacy.days')}</span>
        </div>
      </Field>
      <p class="hint">
        {t('privacy.conversations', { ttl: a.conversation_ttl_days ? t('privacy.daysAfterLast', { n: a.conversation_ttl_days }) : t('ov.kept') })}
        <a href={appHref(app.slug, 'limits')}>{t('tab.limits')}</a>
        {t('privacy.storage')}
        <Tag>{a.storage_mode}</Tag>
      </p>
    </Panel>
  )
}
