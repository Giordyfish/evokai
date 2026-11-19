/**
 * Access (deployment owners): the console's single sign-on provider with its role rules, the people who signed in
 * (disable one and their login ends at once), and the audit log of the whole deployment. Admin keys and the operator
 * key keep working whatever is set here, so a wrong provider never locks the console.
 */
import { useState } from 'preact/hooks'

import type { ConsoleUser, SsoConfig, SsoConfigView } from '@client/core/types'
import { describe, root } from '../api'
import { ago, copy, dateTime } from '../format'
import { done, sparkle, toast } from '../magic'
import { setTitle, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Switch, Tag } from '../ui'
import { AuditLog } from './Audit'
import { Loads } from './common'
import { t } from '../i18n'

const EXAMPLE_ROLES = [
  { claim: 'evokai-owners', role: 'owner', applications: ['*'] },
  { claim: 'bookshop-operators', role: 'operator', applications: ['bookshop'] },
  { email: '*@example.com', role: 'viewer', applications: ['bookshop'] },
]

const lines = (s: string): string[] =>
  s
    .split(/[\n,]/)
    .map((x) => x.trim())
    .filter(Boolean)

export function Access() {
  setTitle(t('page.access'))
  const load = useLoad(() => root.deployment.ssoConfig(), [])
  const users = useLoad(() => root.deployment.users(), [])
  return (
    <>
      <Hero title={t('page.access')} sub={t('access.sub')} />
      <div class="grid g2">
        <Loads load={load} what={t('what.signInProvider')}>
          {(v) => <Provider key={JSON.stringify(v)} view={v} onSaved={(x) => load.set(x)} />}
        </Loads>
        <Loads load={users} what={t('what.users')}>
          {(items) => <Users items={items} reload={users.reload} />}
        </Loads>
      </div>
      <div style="margin-top:16px">
        <AuditLog app={null} filename="evokai-audit.csv" />
      </div>
    </>
  )
}

function Provider({ view, onSaved }: { view: SsoConfigView; onSaved: (v: SsoConfigView) => void }) {
  const c = view.config
  const [issuer, setIssuer] = useState(c?.issuer ?? '')
  const [clientId, setClientId] = useState(c?.client_id ?? '')
  const [secret, setSecret] = useState('')
  const [label, setLabel] = useState(c?.label ?? t('login.sso'))
  const [domains, setDomains] = useState((c?.allowed_domains ?? []).join('\n'))
  const [emails, setEmails] = useState((c?.allowed_emails ?? []).join('\n'))
  const [claim, setClaim] = useState(c?.role_claim ?? 'groups')
  const [mfa, setMfa] = useState(c?.require_mfa ?? true)
  const [rules, setRules] = useState(JSON.stringify(c?.roles ?? EXAMPLE_ROLES, null, 2))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    let roles: SsoConfig['roles']
    try {
      roles = JSON.parse(rules) as SsoConfig['roles']
    } catch {
      setError(t('access.rulesInvalid'))
      return
    }
    const body: SsoConfig = {
      issuer: issuer.trim(),
      client_id: clientId.trim(),
      label: label.trim() || t('login.sso'),
      allowed_domains: lines(domains),
      allowed_emails: lines(emails),
      role_claim: claim.trim() || 'groups',
      require_mfa: mfa,
      roles,
      ...(secret ? { client_secret: secret } : {}),
      ...(c?.scopes ? { scopes: c.scopes } : {}),
      ...(c?.mfa_values ? { mfa_values: c.mfa_values } : {}),
      ...(c?.redirect_uri ? { redirect_uri: c.redirect_uri } : {}),
    }
    setBusy(true)
    setError(null)
    try {
      const v = await root.deployment.putSsoConfig(body)
      sparkle(btn)
      done(btn)
      toast(t('access.saved'), t('access.savedSub'))
      setSecret('')
      onSaved(v)
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  const reset = async () => {
    try {
      onSaved(await root.deployment.resetSsoConfig())
      toast(t('access.backToEnv'), t('access.backToEnvSub'), null)
    } catch (err) {
      toast(t('access.notReset'), describe(err), null)
    }
  }

  return (
    <Panel
      title={t('login.sso')}
      actions={
        <span style="display:flex;gap:8px;align-items:center">
          <Tag tone={view.source ? 'ok' : ''} dot>
            {t(view.source === 'console' ? 'access.setHere' : view.source === 'environment' ? 'access.fromEnv' : 'access.off')}
          </Tag>
          {view.source === 'console' ? (
            <Btn sm kind="ghost" onClick={() => void reset()}>
              {t('access.reset')}
            </Btn>
          ) : null}
          <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
        </span>
      }
    >
      {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
      <Field label={t('access.issuer')} hint={t('access.issuerHint')}>
        <input class="in mono" value={issuer} placeholder="https://login.example.com" onInput={(e) => setIssuer(e.currentTarget.value)} />
      </Field>
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('access.clientId')}>
          <input class="in mono" value={clientId} onInput={(e) => setClientId(e.currentTarget.value)} />
        </Field>
        <Field label={t('access.clientSecret')} hint={c?.hasClientSecret ? t('access.secretKept') : t('access.secretPublic')}>
          <input class="in mono" type="password" value={secret} autocomplete="off" onInput={(e) => setSecret(e.currentTarget.value)} />
        </Field>
      </div>
      {view.redirectUri ? (
        <Field label={t('access.redirect')}>
          <div style="display:flex;gap:8px">
            <code style="overflow-wrap:anywhere">{view.redirectUri}</code>
            <Btn sm kind="ghost" onClick={async () => toast((await copy(view.redirectUri!)) ? t('access.copied') : t('common.copyByHand'), view.redirectUri!, null)}>
              {t('common.copy')}
            </Btn>
          </div>
        </Field>
      ) : null}
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('access.domains')} hint={t('access.domainsHint')}>
          <textarea class="in" style="min-height:64px" value={domains} onInput={(e) => setDomains(e.currentTarget.value)} />
        </Field>
        <Field label={t('access.emails')} hint={t('access.onePerLine')}>
          <textarea class="in" style="min-height:64px" value={emails} onInput={(e) => setEmails(e.currentTarget.value)} />
        </Field>
      </div>
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('access.claim')} hint={t('access.claimHint')}>
          <input class="in mono" value={claim} onInput={(e) => setClaim(e.currentTarget.value)} />
        </Field>
        <Field label={t('access.buttonLabel')}>
          <input class="in" value={label} onInput={(e) => setLabel(e.currentTarget.value)} />
        </Field>
      </div>
      <Field label={t('access.mfa')} hint={t('access.mfaHint')}>
        <Switch on={mfa} label={t('access.requireMfa')} onChange={setMfa} />
      </Field>
      <Field
        label={t('access.rules')}
        hint={t('access.rulesHint')}
      >
        <textarea class="in" value={rules} spellcheck={false} onInput={(e) => setRules(e.currentTarget.value)} />
      </Field>
    </Panel>
  )
}

function Users({ items, reload }: { items: ConsoleUser[]; reload: () => Promise<void> }) {
  const flip = async (u: ConsoleUser) => {
    try {
      await root.deployment.setUserDisabled(u.id, !u.disabled)
      toast(t(u.disabled ? 'access.enabled' : 'access.disabled'), u.email, null)
      await reload()
    } catch (err) {
      toast(t('access.notChanged'), describe(err), null)
    }
  }
  return (
    <Panel title={t('access.users')} cls="flat">
      {items.length ? (
        <div class="wx">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('audit.who')}</th>
                <th>{t('access.roles')}</th>
                <th>{t('access.lastSignIn')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((u) => (
                <tr key={u.id}>
                  <td>
                    {u.name}
                    <div class="s">{u.email}</div>
                  </td>
                  <td>
                    {Object.entries(u.roles).map(([app, role]) => (
                      <Tag key={app}>
                        {app === '*' ? t('access.everyApp') : app}: {role}
                      </Tag>
                    ))}
                  </td>
                  <td class="s" title={u.lastLoginAt ? dateTime(u.lastLoginAt) : undefined}>
                    {ago(u.lastLoginAt)}
                  </td>
                  <td class="r">
                    <Btn sm kind={u.disabled ? 'ghost' : 'ghost danger'} onClick={() => void flip(u)}>
                      {t(u.disabled ? 'access.enable' : 'access.disable')}
                    </Btn>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p class="hint" style="padding:14px">{t('access.nobody')}</p>
      )}
    </Panel>
  )
}
