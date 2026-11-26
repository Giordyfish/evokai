import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/onest'
import '@fontsource/fragment-mono'
import './styles.css'

import { render } from 'preact'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'

import type { ConsoleApplication, ConsoleMe } from '@client/core/types'
import { describe, loggedOut, root, setMe } from './api'
import { fxSrc, installPointerFx, Mage, onToast, PageLoader, preload, takeFx } from './magic'
import { Login } from './pages/Login'
import { Shell } from './shell'
import { type Console, ConsoleCtx, go, parseRoute, type Route } from './state'
import { Btn } from './ui'
import { detachPrefs, initPrefs, syncPrefs } from './prefs'
import { onLocale, t } from './i18n'

// The person's preferences (this browser's copy) before the first paint; their saved copy follows sign in.
initPrefs()
installPointerFx()

function Toast() {
  const [s, setS] = useState({ title: '', sub: '', fx: null as string | null, on: false, n: 0, attn: false })
  const slot = useRef<HTMLSpanElement>(null)
  useEffect(() => onToast((v) => setS({ ...v })), [])
  useLayoutEffect(() => {
    const el = slot.current
    if (!el) return
    el.replaceChildren()
    if (!s.fx || !s.on) return
    const im = takeFx(s.fx as 'flame', 40)
    if (im) el.append(im)
    else {
      const still = new Image(40, 40)
      still.src = fxSrc('flame') ?? ''
      still.alt = ''
      el.append(still)
    }
  }, [s.n])
  return (
    <div class={`toast ${s.on ? 'on' : ''} ${s.attn ? 'attn' : ''}`} role="status" aria-live="polite">
      <span class="kfx" style="width:40px;height:40px" ref={slot} />
      <div>
        <b>{s.title}</b>
        <small>{s.sub}</small>
      </div>
    </div>
  )
}

function App() {
  const [route, setRoute] = useState<Route>(parseRoute())
  const [me, setMeState] = useState<ConsoleMe | null | undefined>(undefined)
  const [apps, setApps] = useState<{ items: ConsoleApplication[]; canCreate: boolean } | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [why, setWhy] = useState<string | null>(null)

  useEffect(() => {
    const on = () => setRoute(parseRoute())
    addEventListener('hashchange', on)
    return () => removeEventListener('hashchange', on)
  }, [])

  const reloadApps = useCallback(async () => {
    setApps(await root.applications())
  }, [])

  const start = useCallback(async () => {
    setError(null)
    try {
      const who = await root.me()
      setMe(who)
      setMeState(who)
      void syncPrefs(root.preferences)
      await reloadApps()
    } catch (e) {
      if (loggedOut(e)) {
        detachPrefs()
        setMe(null)
        setMeState(null)
      } else setError(e)
    }
  }, [reloadApps])

  useEffect(() => {
    void start()
    preload()
  }, [start])

  const signedOut = useCallback((reason?: string) => {
    detachPrefs()
    setMe(null)
    setMeState(null)
    setApps(null)
    setWhy(reason ?? null)
    if (parseRoute().view !== 'login') go('#/login')
  }, [])

  if (route.view === 'login' || me === null) {
    return (
      <Login
        reason={why}
        onIn={async () => {
          setWhy(null)
          await start()
          go(route.view === 'login' ? '#/apps' : location.hash || '#/apps')
        }}
      />
    )
  }
  if (error)
    return (
      <main class="login">
        <div class="empty">
          <Mage k="blink" size={96} cls="sad" />
          <p>
            <b>{t('shell.couldNotReach')}</b>
            <br />
            {describe(error)}
          </p>
          <Btn kind="pri" onClick={() => void start()}>
            {t('common.tryAgain')}
          </Btn>
        </div>
      </main>
    )
  if (!me || !apps)
    return (
      <main class="login">
        <PageLoader what={t('what.console')} />
      </main>
    )
  const ctx: Console = { me, apps: apps.items.filter((a) => !a.hidden), allApps: apps.items, canCreate: apps.canCreate, reloadApps, signedOut }
  return (
    <ConsoleCtx.Provider value={ctx}>
      <Shell route={route} />
    </ConsoleCtx.Provider>
  )
}

function Root() {
  // A new language re-renders everything: every text is read with t() while rendering.
  const [, setN] = useState(0)
  useEffect(() => onLocale(() => setN((n) => n + 1)), [])
  return (
    <>
      <div class="aurora" aria-hidden="true" />
      <App />
      <div class="progress" id="prog">
        <i />
      </div>
      <Toast />
    </>
  )
}

render(<Root />, document.getElementById('root')!)
