/**
 * Sign in: an admin key (or the operator key) exchanged for a short HttpOnly cookie; the key is never stored. A sign-in
 * link (`#/login?code=...`, made by a local script) is traded at once for the same cookie, without typing.
 */
import { useEffect, useState } from 'preact/hooks'

import { describe, EvokaiError, root } from '../api'
import { codeFromHash, exchangeCode, urlWithoutCode } from '../login-code'
import { t, type TextKey } from '../i18n'
import { LangSwitch } from '../lang'
import { masSrc, PageLoader, RM, Rune } from '../magic'
import { setTitle } from '../state'
import { toggleTheme, useDark } from '../theme'
import { Btn, Field, Icon } from '../ui'

const SSO_ERRORS = new Set(['mfa_required', 'not_allowed', 'no_role', 'email_unverified', 'disabled', 'state', 'token', 'provider', 'not_configured'])

/** The reason a single sign-on was refused, from `#/login?sso_error=...`. */
function ssoErrorFromHash(hash: string): string | null {
  const q = new URLSearchParams(hash.split('?')[1] ?? '')
  const code = q.get('sso_error')
  if (!code) return null
  return t(SSO_ERRORS.has(code) ? (`login.sso.${code}` as TextKey) : 'login.sso.refused')
}

/** Codes already tried in this page: each works once, so a second mount must not spend an attempt on it. */
const spent = new Set<string>()

export function Login({ onIn, reason }: { onIn: () => Promise<void>; reason: string | null }) {
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [cast, setCast] = useState(false)
  const [leave, setLeave] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dark = useDark()
  const [mage, setMage] = useState(masSrc('combo', 200))
  const [link] = useState(() => codeFromHash(location.hash))
  const [opening, setOpening] = useState(() => link !== null && !spent.has(link))
  const [sso, setSso] = useState<{ enabled: boolean; label: string | null } | null>(null)
  const [ssoError] = useState(() => ssoErrorFromHash(location.hash))
  setTitle(t('page.signIn'))

  useEffect(() => {
    root
      .sso()
      .then(setSso)
      .catch(() => setSso(null))
  }, [])

  useEffect(() => {
    if (!opening || link === null) return
    spent.add(link)
    void exchangeCode(
      link,
      (code) => root.loginWithCode(code),
      () => history.replaceState(history.state, '', urlWithoutCode(location)),
    ).then(async (r) => {
      if (r.ok) return onIn()
      setError(r.error instanceof EvokaiError && r.error.status === 401 ? t('login.expiredLink') : describe(r.error))
      setOpening(false)
    })
  }, [])

  if (opening)
    return (
      <main class="login">
        <PageLoader what={t('what.console')} />
      </main>
    )

  const submit = async (e: Event) => {
    e.preventDefault()
    if (busy || !key.trim()) return
    setBusy(true)
    setError(null)
    try {
      await root.login(key.trim())
      setKey('')
      setCast(true)
      if (!RM()) setMage(masSrc('rune', 200))
      setTimeout(() => setLeave(true), RM() ? 0 : 2700)
      setTimeout(() => void onIn(), RM() ? 60 : 3150)
    } catch (err) {
      setBusy(false)
      setError(
        err instanceof EvokaiError && err.status === 401
          ? t('login.badKey')
          : describe(err),
      )
    }
  }

  return (
    <main class="login">
      <LangSwitch />
      <div class={`lbox ${cast ? 'cast' : ''} ${leave ? 'leave' : ''}`}>
        <div class="stage">
          <Rune size={250} cls="turn lrune" />
          <img class="lmas" src={mage} width={200} height={200} alt={t('login.mageAlt')} />
        </div>
        <h1>Evokai Console</h1>
        <p>
          {reason ??
            (sso?.enabled ? t('login.withSso') : t('login.withKey'))}
        </p>
        {ssoError ? (
          <p class="ssoerr" role="alert">
            {ssoError}
          </p>
        ) : null}
        {sso?.enabled ? (
          <Btn kind="pri" style="width:100%;margin-bottom:12px" onClick={() => location.assign(root.ssoStartUrl())}>
            {sso.label ?? t('login.sso')}
          </Btn>
        ) : null}
        <form class="panel card" onSubmit={submit}>
          <Field label={t('login.key')} id="key" hint={error ? <span style="color:var(--s-bad)">{error}</span> : undefined}>
            <input
              id="key"
              class="in mono"
              type="password"
              value={key}
              onInput={(e) => setKey(e.currentTarget.value)}
              placeholder={t('login.keyPlaceholder')}
              autocomplete="off"
              spellcheck={false}
              autoFocus
            />
          </Field>
          <div style="display:flex;gap:8px">
            <Btn kind="pri" type="submit" busy={busy ? t('login.entering') : false} style="flex:1">
              {t('login.enter')}
            </Btn>
            <Btn kind="ic" label={t('common.toggleTheme')} onClick={() => toggleTheme()}>
              <Icon name={dark ? 'sun' : 'moon'} />
            </Btn>
          </div>
        </form>
        <p class="fine">{t('login.fine')}</p>
      </div>
    </main>
  )
}
