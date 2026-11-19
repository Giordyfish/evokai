/**
 * The credentials of the capabilities that need a provider's account: web search, third-party integrations, remote
 * sandboxes. One form per provider, generated from its fields; secrets are write-only (the page says whether one is
 * set, by the application or by the deployment's environment, and takes a new one or removes it).
 */
import { useState } from 'preact/hooks'

import type { ConsoleApplication } from '@client/core/types'
import { adminJson, describe } from '../api'
import { done, sparkle, toast } from '../magic'
import { useLoad } from '../state'
import { Btn, Field, Panel, Tag } from '../ui'
import { Loads } from './common'
import { t } from '../i18n'

/** One value of a provider's credentials, as the service lists it (never a secret). */
export interface CredentialField {
  name: string
  secret: boolean
  required: boolean
  env: string
  set: boolean
  fromEnvironment: boolean
  value?: string | null
}

/** A provider and the capabilities that use its credentials. */
export interface CredentialProvider {
  provider: string
  capabilities: string[]
  fields: CredentialField[]
  updatedAt: string | null
}

const PATH = '/v1/admin/capability-credentials'

export function CapabilityCredentials({ app }: { app: ConsoleApplication }) {
  const load = useLoad(() => adminJson<{ items: CredentialProvider[] }>(app.id, 'GET', PATH), [app.id])
  return (
    <Loads load={load} what={t('creds.what')}>
      {(view) => (
        <Panel title={t('creds.title')} cls="flat">
          <p class="hint" style="padding:0 18px 8px">
            {t('creds.note')}
          </p>
          {view.items.map((p) => (
            <ProviderForm key={p.provider} app={app} p={p} onSaved={(next) => load.set({ items: view.items.map((x) => (x.provider === next.provider ? next : x)) })} />
          ))}
        </Panel>
      )}
    </Loads>
  )
}

function state(f: CredentialField): string {
  if (f.set) return t('creds.set')
  if (f.fromEnvironment) return t('creds.fromEnv', { env: f.env })
  return t('creds.notSet')
}

function ProviderForm({ app, p, onSaved }: { app: ConsoleApplication; p: CredentialProvider; onSaved: (p: CredentialProvider) => void }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const ready = p.fields.every((f) => !f.required || f.set || f.fromEnvironment)
  const changed = Object.values(values).some((v) => v.trim())

  const send = async (e: Event, body: Record<string, string | null>) => {
    const btn = e.currentTarget as Element
    setBusy(true)
    try {
      const next = await adminJson<CredentialProvider>(app.id, 'PUT', `${PATH}/${encodeURIComponent(p.provider)}`, { values: body })
      setValues({})
      onSaved(next)
      sparkle(btn)
      done(btn)
      toast(t('creds.saved'), p.provider)
    } catch (err) {
      toast(t('creds.notSaved'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  const clear = (e: Event) => void send(e, Object.fromEntries(p.fields.filter((f) => f.set).map((f) => [f.name, null])))

  return (
    <details class="acc">
      <summary class="item">
        <div class="t">
          <b>
            <code>{p.provider}</code>
          </b>
          <small>{t('creds.usedBy', { capabilities: p.capabilities.join(', ') })}</small>
        </div>
        <div class="prov">{ready ? <Tag tone="ok">{t('creds.ready')}</Tag> : <Tag tone="gold">{t('creds.missing')}</Tag>}</div>
      </summary>
      <div style="padding:4px 16px 14px 16px">
        <div class="grid g2" style="gap:0 14px">
          {p.fields.map((f) => {
            const id = `cr-${p.provider}-${f.name}`
            return (
              <Field key={f.name} id={id} label={<code>{f.name}</code>} hint={state(f)}>
                <input
                  id={id}
                  class="in mono"
                  type={f.secret ? 'password' : 'text'}
                  autocomplete={f.secret ? 'new-password' : 'off'}
                  placeholder={!f.secret && f.value ? f.value : ''}
                  value={values[f.name] ?? ''}
                  onInput={(e) => setValues({ ...values, [f.name]: e.currentTarget.value })}
                />
              </Field>
            )
          })}
        </div>
        <div style="display:flex;gap:8px;margin-top:8px">
          <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} disabled={!changed} onClick={(e) => void send(e, values)}>
            {t('common.save')}
          </Btn>
          {p.fields.some((f) => f.set) ? (
            <Btn sm kind="ghost danger" disabled={busy} onClick={(e) => clear(e)}>
              {t('creds.remove')}
            </Btn>
          ) : null}
        </div>
      </div>
    </details>
  )
}
