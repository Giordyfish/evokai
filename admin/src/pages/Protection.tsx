/**
 * The data protection profile of an application (residency, jurisdiction, retention, what the browser may send) and every external processor under it: allowed, or excluded and why.
 */
import { useState } from 'preact/hooks'

import type { Application, ConsoleApplication, DataProtectionReport, ProcessorVerdict } from '@client/core/types'
import { describe, forApp } from '../api'
import { done, sparkle, toast } from '../magic'
import { useConsole, useLoad } from '../state'
import { Btn, Field, Panel, Seg, Switch, Tag } from '../ui'
import { Loads } from './common'
import { where } from './Providers'
import { t, tn, type TextKey } from '../i18n'
import { GEOS, profileFrom, RETENTION } from '../protection'

export function Protection({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const report = useLoad(() => forApp(app.id).admin.dataProtection.get(), [app.id, JSON.stringify(a.data_protection ?? null)])
  return (
    <>
      <ProfileForm key={JSON.stringify(a.data_protection ?? null)} app={app} a={a} reload={reload} />
      <Loads load={report} what={t('what.processors')}>
        {(r) => (
          <>
            <Processors r={r} />
            <div style="height:16px" />
            <BuiltinTools r={r} />
          </>
        )}
      </Loads>
    </>
  )
}

function ProfileForm({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const c = useConsole()
  const p = a.data_protection ?? {}
  const juris = p.jurisdiction ?? []
  const [geos, setGeos] = useState<string[]>(p.residency?.geographies ?? [])
  const [countries, setCountries] = useState((p.residency?.countries ?? []).join(', '))
  const [eu, setEu] = useState(juris.includes('eu'))
  const [jurisdictions, setJurisdictions] = useState(juris.filter((x) => x !== 'eu').join(', '))
  const [retention, setRetention] = useState<(typeof RETENTION)[number]>((p.retention as (typeof RETENTION)[number]) ?? 'any')
  const [voices, setVoices] = useState(Boolean(p.browser?.online_voices))
  const [recognition, setRecognition] = useState(Boolean(p.browser?.online_recognition))
  const [vendors, setVendors] = useState((p.browser?.vendors ?? []).join(', '))
  const [storage, setStorage] = useState(Boolean(p.provider_tool_storage))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setSaving(true)
    setError(null)
    try {
      const profile = profileFrom({ geos, countries, eu, jurisdictions, retention, voices, recognition, vendors, storage })
      await forApp(app.id).admin.application.patch({ data_protection: profile })
      sparkle(btn)
      done(btn)
      toast(t('dp.saved'), t('dp.savedSub'))
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
      title={t('dp.profileTitle')}
      actions={
        <Btn sm kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
          {t('common.save')}
        </Btn>
      }
    >
      <p class="hint">{t('dp.profileSub')}</p>
      {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
      <Field label={t('dp.residencyLabel')} hint={t('dp.residencyHint')}>
        <div style="display:grid;gap:8px">
          {GEOS.map((k) => (
            <Switch key={k} on={geos.includes(k)} label={t(`privacy.geo.${k}` as TextKey)} onChange={(v) => setGeos(v ? [...geos, k] : geos.filter((x) => x !== k))} />
          ))}
          <input class="in mono" value={countries} placeholder="DE,FR,IT" onInput={(e) => setCountries(e.currentTarget.value)} aria-label={t('dp.countries')} />
        </div>
      </Field>
      <Field label={t('dp.jurisdictionLabel')} hint={t('dp.jurisdictionHint')}>
        <div style="display:grid;gap:8px">
          <Switch on={eu} label={t('dp.jurisdictionEu')} onChange={setEu} />
          <input class="in mono" value={jurisdictions} placeholder="CH" onInput={(e) => setJurisdictions(e.currentTarget.value)} aria-label={t('dp.countries')} />
        </div>
      </Field>
      <Field label={t('dp.retentionLabel')} hint={t('dp.retentionHint')}>
        <Seg options={RETENTION} value={retention} onChange={setRetention} labels={{ any: t('dp.ret.any'), no_training: t('dp.ret.noTraining'), zero_retention: t('dp.ret.zero') }} />
      </Field>
      <Field label={t('dp.storageLabel')} hint={t('dp.storageHint')}>
        <Switch on={storage && retention !== 'zero_retention'} label={t('dp.storageSwitch')} onChange={setStorage} />
      </Field>
      <Field label={t('dp.browserLabel')} hint={t('dp.browserHint')}>
        <div style="display:grid;gap:8px">
          <Switch on={voices} label={t('dp.onlineVoices')} onChange={setVoices} />
          <Switch on={recognition} label={t('dp.onlineRecognition')} onChange={setRecognition} />
          <input class="in" value={vendors} placeholder={t('dp.vendorsPh')} onInput={(e) => setVendors(e.currentTarget.value)} aria-label={t('dp.vendors')} />
        </div>
      </Field>
    </Panel>
  )
}

function Processors({ r }: { r: DataProtectionReport }) {
  const excluded = r.processors.filter((x) => !x.allowed).length
  return (
    <Panel
      title={t('dp.processorsTitle')}
      actions={<Tag tone={excluded ? 'warn' : 'ok'}>{excluded ? tn('dp.excludedN', excluded) : t('dp.allAllowed')}</Tag>}
    >
      <div class="wx">
        <table class="tbl">
          <thead>
            <tr>
              <th>{t('dp.processor')}</th>
              <th>{t('prov.where')}</th>
              <th>{t('prov.controlledFrom')}</th>
              <th>{t('dp.verdict')}</th>
            </tr>
          </thead>
          <tbody>
            {r.processors.map((x) => (
              <Row key={`${x.kind}/${x.name}`} x={x} />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function BuiltinTools({ r }: { r: DataProtectionReport }) {
  const rows = r.builtinTools ?? []
  if (!rows.length) return null
  return (
    <Panel title={t('dp.builtinTitle')}>
      <p class="hint">{t('dp.builtinSub')}</p>
      <div class="wx">
        <table class="tbl">
          <thead>
            <tr>
              <th>{t('dp.builtinTool')}</th>
              <th>{t('dp.builtinAgent')}</th>
              <th>{t('dp.verdict')}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x) => (
              <tr key={`${x.agent}/${x.kind}`}>
                <td>
                  <code>{x.kind}</code>
                  {x.keepsData ? <div class="s">{t('dp.builtinKeeps')}</div> : null}
                </td>
                <td>{x.agent}</td>
                <td>
                  <Tag tone={x.allowed ? 'ok' : 'warn'} dot>
                    {x.allowed ? t('dp.allowed') : t('dp.excluded')}
                  </Tag>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function Row({ x }: { x: ProcessorVerdict }) {
  const f = x.facts
  return (
    <tr>
      <td>
        <code>{x.name}</code>
        <div class="s">
          {t(`dp.kind.${x.kind}` as TextKey)}
          {f?.title ? <> · {f.title}</> : null}
        </div>
      </td>
      <td>{where(f)}</td>
      <td>{f?.controlledFrom ?? (f?.inProcess ? t('dp.inProcess') : t('dp.unknown'))}</td>
      <td>
        <Tag tone={x.allowed ? 'ok' : 'warn'} dot>
          {x.allowed ? t('dp.allowed') : t('dp.excluded')}
        </Tag>
        {x.reasons.map((r) => (
          <div class="s" key={`${r.dimension}/${r.code}`}>
            {t(`dp.why.${r.dimension}.${r.code}` as TextKey)}
          </div>
        ))}
      </td>
    </tr>
  )
}
