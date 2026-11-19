/**
 * Connections: the providers people of the application can connect (Microsoft 365, Google Workspace, GitHub, Linear,
 * Notion, Slack) so agents act as them, each with its configuration, whether it is granted, how many people connected
 * (counts only, never tokens) and «revoke all». Microsoft 365 and Google Workspace are configured by their delegated
 * connector; the others by the application's OAuth app, set here (the secret is write-only). The redirect URI to
 * register at the providers is shown ready to copy.
 */
import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'

import type { ConnectionsAdminView, ConsoleApplication, ProviderSetupView } from '@client/core/types'
import { describe, forApp } from '../api'
import { copy } from '../format'
import { Empty, toast } from '../magic'
import { appHref, setTitle, useLoad } from '../state'
import { Btn, Field, Hero, KV, Panel, Switch, Tag } from '../ui'
import { Loads } from './common'
import { t, tn, type TextKey } from '../i18n'

/** How to register each provider's OAuth app, before it is configured. */
const HOW: Record<string, TextKey> = {
  microsoft365: 'conn.howMicrosoft',
  google: 'conn.howGoogle',
  github: 'conn.howGithub',
  linear: 'conn.howLinear',
  notion: 'conn.howNotion',
  slack: 'conn.howSlack',
}

const FAMILY: Record<string, TextKey> = {
  mail: 'perms.familyMail',
  calendar: 'perms.familyCalendar',
  files: 'perms.familyFiles',
}

function CopyRow({ value }: { value: string }) {
  return (
    <div class="copyrow">
      <code>{value}</code>
      <Btn sm kind="ghost" check onClick={async () => toast((await copy(value)) ? t('conn.copied') : t('common.copyByHand'), value, null)}>
        {t('conn.copy')}
      </Btn>
    </div>
  )
}

/** The application's OAuth app at a provider no connector configures: client id, write-only secret, writes. */
function AppForm({ app, p, onSaved }: { app: ConsoleApplication; p: ProviderSetupView; onSaved: () => Promise<void> }) {
  const [clientId, setClientId] = useState(p.clientId ?? '')
  const [secret, setSecret] = useState('')
  const [writes, setWrites] = useState(p.writes)
  const [busy, setBusy] = useState<'save' | 'remove' | null>(null)
  const [arming, setArming] = useState(false)
  const id = `oa-${p.provider}`
  const save = async (e: Event) => {
    e.preventDefault()
    setBusy('save')
    try {
      await forApp(app.id).admin.connections.setApp(p.provider, { clientId: clientId.trim(), clientSecret: secret || undefined, writes })
      setSecret('')
      toast(t('conn.app.saved'), p.title, null)
      await onSaved()
    } catch (err) {
      toast(t('conn.app.notSaved'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }
  const remove = async () => {
    if (!arming) {
      setArming(true)
      setTimeout(() => setArming(false), 3000)
      return
    }
    setArming(false)
    setBusy('remove')
    try {
      await forApp(app.id).admin.connections.deleteApp(p.provider)
      toast(t('conn.app.removed'), p.title, null)
      await onSaved()
    } catch (err) {
      toast(t('conn.app.notRemoved'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }
  return (
    <form class="grid" style="gap:10px;margin-top:12px" onSubmit={(e) => void save(e)}>
      <Field label={t('conn.clientId')} id={`${id}-client`}>
        <input id={`${id}-client`} class="in mono" value={clientId} autocomplete="off" spellcheck={false} onInput={(e) => setClientId(e.currentTarget.value)} />
      </Field>
      <Field
        label={
          <>
            {t('conn.secret')}{' '}
            <Tag tone={p.secretSet ? 'ok' : ''} dot>
              {t(p.secretSet ? 'conn.app.secretSet' : 'conn.app.secretNotSet')}
            </Tag>
          </>
        }
        id={`${id}-secret`}
        hint={t(p.secretSet ? 'conn.app.secretKept' : 'conn.app.secretNew')}
      >
        <input id={`${id}-secret`} class="in mono" type="password" autocomplete="new-password" value={secret} onInput={(e) => setSecret(e.currentTarget.value)} />
      </Field>
      <Field label={t('conn.app.writes')} hint={t('conn.app.writesHint')}>
        <Switch on={writes} onChange={setWrites} label={writes ? t('conn.app.writesOn') : t('conn.app.writesOff')} />
      </Field>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <Btn sm kind="pri" type="submit" check busy={busy === 'save' ? t('conn.app.saving') : false} disabled={!clientId.trim() || (!p.secretSet && !secret)}>
          {t('conn.app.save')}
        </Btn>
        {p.configured ? (
          <Btn sm kind="ghost danger" busy={busy === 'remove' ? t('conn.app.removing') : false} onClick={() => void remove()}>
            {arming ? t('conn.app.confirmRemove') : t('conn.app.remove')}
          </Btn>
        ) : null}
      </div>
    </form>
  )
}

function Provider({ app, p, onRevoked }: { app: ConsoleApplication; p: ProviderSetupView; onRevoked: () => Promise<void> }) {
  const [arming, setArming] = useState(false)
  const [busy, setBusy] = useState(false)
  const revoke = async () => {
    if (!arming) {
      setArming(true)
      setTimeout(() => setArming(false), 3000)
      return
    }
    setArming(false)
    setBusy(true)
    try {
      const n = await forApp(app.id).admin.connections.revokeAll(p.provider)
      toast(t('conn.revoked'), tn('n.personConnection', n), null)
      await onRevoked()
    } catch (e) {
      toast(t('conn.notRevoked'), describe(e), null)
    } finally {
      setBusy(false)
    }
  }
  const tags = (
    <span style="display:inline-flex;gap:6px;flex-wrap:wrap">
      {!p.configured ? (
        <Tag>{t('conn.notConfigured')}</Tag>
      ) : !p.valid ? (
        <Tag tone="bad" dot>
          {t('conn.invalid')}
        </Tag>
      ) : (
        <Tag tone={p.secretSet ? 'ok' : 'warn'} dot>
          {p.secretSet ? t('conn.ready') : t('conn.secretMissing')}
        </Tag>
      )}
      {p.configured ? (
        <Tag tone={p.granted ? 'vi' : 'warn'} href={appHref(app.slug, 'perms')} title={t('conn.toPermissions')}>
          {p.granted ? t('conn.granted') : t('conn.notGranted')}
        </Tag>
      ) : null}
    </span>
  )
  return (
    <Panel
      title={
        <span style="display:inline-flex;gap:10px;align-items:center;flex-wrap:wrap">
          {p.title}
          {tags}
        </span>
      }
      actions={
        p.configured ? (
          <Btn sm kind="ghost danger" busy={busy ? t('conn.revoking') : false} disabled={!p.connected && !p.expired} onClick={() => void revoke()}>
            {arming ? t('conn.confirmRevokeAll') : t('conn.revokeAll')}
          </Btn>
        ) : null
      }
    >
      {p.configured && p.configuredBy === 'application' ? (
        <>
          <div class="stats3">
            <div>
              <b>{p.connected}</b>
              <span class="s">{t('conn.connectedPeople')}</span>
            </div>
            <div>
              <b>{p.expired}</b>
              <span class="s">{t('conn.expiredPeople')}</span>
            </div>
          </div>
          {p.error ? <p class="hint bad">{p.error}</p> : null}
          <KV
            rows={[
              [t('conn.integration'), <code>{p.integration}</code>],
              [t('conn.scopes'), p.scopes.length ? <code style="white-space:normal;word-break:break-word">{p.scopes.join(' ')}</code> : t('conn.app.noScopes')],
            ]}
          />
          <AppForm app={app} p={p} onSaved={onRevoked} />
        </>
      ) : p.configured ? (
        <>
          <div class="stats3">
            <div>
              <b>{p.connected}</b>
              <span class="s">{t('conn.connectedPeople')}</span>
            </div>
            <div>
              <b>{p.expired}</b>
              <span class="s">{t('conn.expiredPeople')}</span>
            </div>
            <div>
              <b>{p.families.filter((f) => f.granted).length}</b>
              <span class="s">{t('conn.familiesGranted')}</span>
            </div>
          </div>
          {p.error ? <p class="hint bad">{p.error}</p> : null}
          <KV
            rows={[
              [t('conn.server'), <a href={appHref(app.slug, 'mcp', p.server ?? undefined)}>{p.server}</a>],
              [t('conn.clientId'), <code>{p.clientId}</code>],
              ...(p.provider === 'microsoft365' ? ([[t('conn.tenant'), <code>{p.tenant}</code>]] as Array<[string, ComponentChildren]>) : []),
              [
                t('conn.secret'),
                <span>
                  <code>{p.secretEnv}</code>{' '}
                  <Tag tone={p.secretSet ? 'ok' : 'warn'}>{p.secretSet ? t('conn.envSet') : t('conn.envMissing')}</Tag>
                </span>,
              ],
              [t('conn.writes'), p.writes ? t('conn.writesOn') : t('conn.writesOff')],
              [
                t('conn.families'),
                <span style="display:inline-flex;gap:6px;flex-wrap:wrap">
                  {p.families.map((f) => (
                    <Tag key={f.family} tone={f.granted && p.granted ? 'vi' : ''}>
                      {FAMILY[f.family] ? t(FAMILY[f.family]!) : f.family}
                    </Tag>
                  ))}
                </span>,
              ],
              [t('conn.scopes'), <code style="white-space:normal;word-break:break-word">{p.scopes.join(' ')}</code>],
            ]}
          />
        </>
      ) : (
        <>
          <p class="hint" style="margin:0">
            {t(HOW[p.provider] ?? 'conn.howGoogle')}
          </p>
          {p.configuredBy === 'application' ? <AppForm app={app} p={p} onSaved={onRevoked} /> : null}
        </>
      )}
    </Panel>
  )
}

export function Connections({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.connections'), app.name)
  const load = useLoad(() => forApp(app.id).admin.connections.get(), [app.id])
  return (
    <>
      <Hero title={t('tab.connections')} sub={t('conn.sub')} />
      <Loads load={load} what={t('what.connections')}>
        {(view: ConnectionsAdminView) => (
          <div class="grid" style="gap:16px">
            <Panel title={t('conn.redirect')}>
              <p class="hint" style="margin-top:0">
                {t('conn.redirectHint')}
              </p>
              {view.redirectUri ? <CopyRow value={view.redirectUri} /> : null}
              {!view.publicUrlSet ? <p class="hint warn">{t('conn.publicUrlMissing')}</p> : null}
            </Panel>
            {view.providers.some((p) => p.configured) ? null : (
              <Panel>
                <Empty k="ember">
                  <b>{t('conn.empty')}</b>
                  <br />
                  {t('conn.emptyText')}
                </Empty>
              </Panel>
            )}
            {view.providers.map((p) => (
              <Provider key={p.provider} app={app} p={p} onRevoked={load.reload} />
            ))}
          </div>
        )}
      </Loads>
    </>
  )
}
