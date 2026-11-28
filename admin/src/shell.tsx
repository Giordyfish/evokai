/** The frame around every page: sidebar (brand, application switcher, sections), top bar, breadcrumbs, palette. */
import type { ComponentChildren } from 'preact'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'

import type { ConsoleApplication, ConsoleMe, CostAlert } from '@client/core/types'
import { forApp, root } from './api'
import { useAssistantState } from './assistant/active'
import { assistantActive } from './assistant/choice'
import { AssistantLauncher } from './assistant/Panel'
import { ALERTS_CHANGED, alertText, bannerAlert } from './costs'
import { pl } from './format'
import { BrandMark, Empty } from './magic'
import { Access } from './pages/Access'
import { SystemPolicy } from './pages/SystemPolicy'
import { Gateways } from './pages/Gateways'
import { Agents } from './pages/Agents'
import { AgentEditor } from './pages/AgentEditor'
import { Apps } from './pages/Apps'
import { Assistant } from './pages/Assistant'
import { Audit } from './pages/Audit'
import { Health } from './pages/Health'
import { Keys } from './pages/Keys'
import { Limits } from './pages/Limits'
import { Mcp } from './pages/Mcp'
import { ModuleOff, Modules } from './pages/Modules'
import { Connections } from './pages/Connections'
import { Channels } from './pages/Channels'
import { NotFound } from './pages/NotFound'
import { Overview } from './pages/Overview'
import { Permissions } from './pages/Permissions'
import { Evals } from './pages/Evals'
import { Playground } from './pages/Playground'
import { Preferences } from './pages/Preferences'
import { Privacy } from './pages/Privacy'
import { Providers } from './pages/Providers'
import { Compliance } from './pages/Compliance'
import { Security } from './pages/Security'
import { Traces } from './pages/Traces'
import { Conversations } from './pages/Conversations'
import { Setup } from './pages/Setup'
import { Skills } from './pages/Skills'
import { Prompts } from './pages/Prompts'
import { Knowledge } from './pages/Knowledge'
import { Workflows } from './pages/Workflows'
import { Approvals } from './pages/Approvals'
import { States } from './pages/States'
import { AppUsage, AllUsage } from './pages/Usage'
import { moduleOf, moduleOn } from './moduleSwitch'
import { appHref, go, type Route, type Tab, useConsole } from './state'
import { tabCounts, tabLabel, tabs, voiceLabel } from './nav'
import { locale, t, tn } from './i18n'
import { toggleTheme, useDark } from './theme'
import { AppAvatar, Btn, Icon, type IconName } from './ui'
import { GuidesMenu, TourInvite, TourOverlay } from './tours/Tour'
import { setTourEnv } from './tours/run'

function page(route: Route, app: ConsoleApplication | null, me: ConsoleMe): ComponentChildren {
  // A page of a module that is off: its routes answer module_disabled, the page says why.
  const module = moduleOf(route)
  if (module && !moduleOn(me, module)) return <ModuleOff module={module} />
  if (route.view === 'app') {
    if (!app) return <NotFound />
    const id = route.id
    switch (route.tab) {
      case 'overview':
        return <Overview app={app} />
      case 'agents':
        return id ? <AgentEditor app={app} name={id} /> : <Agents app={app} />
      case 'evals':
        return <Evals app={app} id={id} q={route.q} />
      case 'perms':
        return <Permissions app={app} />
      case 'mcp':
        return <Mcp app={app} id={id} />
      case 'connections':
        return <Connections app={app} />
      case 'channels':
        return <Channels app={app} />
      case 'skills':
        return <Skills app={app} id={id} />
      case 'prompts':
        return <Prompts app={app} id={id} />
      case 'knowledge':
        return <Knowledge app={app} id={id} />
      case 'workflows':
        return <Workflows app={app} id={id} />
      case 'approvals':
        return <Approvals app={app} />
      case 'keys':
        return <Keys app={app} />
      case 'limits':
        return <Limits app={app} />
      case 'usage':
        return <AppUsage app={app} />
      case 'traces':
        return <Traces app={app} id={id} q={route.q} />
      case 'conversations':
        return <Conversations app={app} id={id} q={route.q} />
      case 'security':
        return <Security app={app} />
      case 'providers':
        return <Providers app={app} />
      case 'privacy':
        return <Privacy app={app} />
      case 'compliance':
        return <Compliance app={app} />
      case 'audit':
        return <Audit app={app} />
    }
  }
  if (route.view === 'gateways') return <Gateways gateway={route.gateway} tab={route.tab} />
  switch (route.view) {
    case 'apps':
      return <Apps />
    case 'setup':
      return <Setup />
    case 'playground':
      return <Playground q={route.q} />
    case 'usage':
      return <AllUsage />
    case 'health':
      return <Health />
    case 'access':
      return <Access />
    case 'policy':
      return <SystemPolicy />
    case 'modules':
      return me.deploymentOwner ? <Modules /> : <NotFound />
    case 'assistant':
      return <Assistant />
    case 'preferences':
      return <Preferences q={route.q} />
    case 'states':
      return import.meta.env.DEV ? <States /> : <NotFound />
    default:
      return <NotFound />
  }
}

export function Shell({ route }: { route: Route }) {
  const c = useConsole()
  const appsOn = moduleOn(c.me, 'applications')
  const gatewayOn = moduleOn(c.me, 'gateway')
  const app = route.view === 'app' ? (c.apps.find((a) => a.slug === route.app) ?? null) : null
  const [open, setOpen] = useState(false)
  const [pop, setPop] = useState(false)
  const [pal, setPal] = useState(false)
  const dark = useDark()
  const [lastApp, setLastApp] = useState<string | null>(null)
  const fresh = app && app.slug !== lastApp
  const alerts = useCostAlerts(app)
  const assistant = useAssistantState(route.view === 'assistant')
  const assistantOn = assistantActive(assistant)
  // The guided tours show the applications this person sees, with their rights.
  setTourEnv({ slugs: c.apps.map((a) => a.slug), canCreate: c.canCreate, owner: !!c.me.deploymentOwner })

  useEffect(() => {
    setOpen(false)
    setPop(false)
    scrollTo(0, 0)
  }, [route.view, route.view === 'app' ? `${route.app}/${route.tab}/${route.id}` : ''])
  // With the applications module off, the home page is the gateways.
  useEffect(() => {
    if (route.view === 'apps' && !appsOn && gatewayOn) go('#/gateways')
  }, [route.view, appsOn, gatewayOn])
  useEffect(() => {
    if (app) setTimeout(() => setLastApp(app.slug), 1300)
    else setLastApp(null)
  }, [app?.slug])
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPal(true)
      }
    }
    const away = (e: MouseEvent) => {
      if (!(e.target as Element).closest('.switch')) setPop(false)
    }
    addEventListener('keydown', k)
    addEventListener('click', away)
    return () => {
      removeEventListener('keydown', k)
      removeEventListener('click', away)
    }
  }, [])

  const nav = (href: string, label: string, icon: IconName, current: boolean, badge?: ComponentChildren) => (
    <a href={href} aria-current={current ? 'page' : undefined}>
      <Icon name={icon} />
      {label}
      {badge}
    </a>
  )
  const counts = app ? tabCounts(app) : {}
  const crumbs: Array<[string, string?]> = []
  if (route.view === 'app') {
    crumbs.push([t('page.applications'), '#/apps'])
    if (app) {
      crumbs.push([app.name, appHref(app.slug)])
      if (route.tab !== 'overview') crumbs.push([tabLabel(route.tab), appHref(app.slug, route.tab)])
      if (route.id)
        crumbs.push([
          route.id === 'new'
            ? t('common.new')
            : route.tab === 'workflows' && route.id.startsWith('run:')
              ? t('wf.runCrumb', { id: route.id.slice(4, 12) })
              : route.id,
        ])
    }
  } else if (route.view === 'gateways') {
    crumbs.push([t('page.gateways'), '#/gateways'])
    if (route.gateway) crumbs.push([route.gateway])
  } else if (route.view === 'apps') crumbs.push([t('page.applications')])
  else if (route.view === 'setup') crumbs.push([t('page.applications'), '#/apps'], [t('page.setup')])
  else
    crumbs.push([
      ({ playground: t('page.playground'), usage: t('page.usage'), health: t('page.health'), access: t('page.access'), policy: t('page.systemPolicy'), modules: t('page.modules'), assistant: t('page.assistant'), preferences: t('page.preferences') } as Record<string, string>)[route.view] ??
        t('page.notFound'),
    ])

  const signOut = async () => {
    try {
      await root.logout()
    } finally {
      c.signedOut()
    }
  }
  const who =
    c.me.kind === 'op'
      ? t('shell.operator')
      : c.me.kind === 'sso'
        ? (c.me.email ?? t('shell.signedIn'))
        : t('shell.admin', { prefix: (c.me.keyPrefix ?? '').slice(0, 6) }).trim()
  const readOnly = app?.role === 'viewer'

  return (
    <div class={`shell ${open ? 'open' : ''}`}>
      <aside class="side" aria-label={t('shell.navigation')}>
        <a class="brand" href="#/apps" title={t('shell.home')} aria-label={t('shell.homeLabel')}>
          <BrandMark assistantActive={assistantOn} />
          <b>Evokai</b>
        </a>
        <div style="position:relative" hidden={!appsOn}>
          <button class="switch" aria-haspopup="true" aria-expanded={pop} onClick={() => setPop(!pop)}>
            {app ? <AppAvatar slug={app.slug} name={app.name} /> : <span class="av all">E</span>}
            <span>
              {app ? app.name : t('shell.allApplications')}
              <small>
                {app
                  ? t('shell.appSub', { agents: pl(app.counts.agents, 'n.agent'), voice: voiceLabel(app).toLowerCase() })
                  : pl(c.apps.length, 'n.application')}
              </small>
            </span>
            <span class="chev">
              <Icon name="chev" style="width:12px;height:12px;rotate:90deg" />
            </span>
          </button>
          <div class={`pop ${pop ? 'on' : ''}`}>
            <a href="#/apps">
              <span class="av all">E</span>
              {t('shell.allApplications')}
            </a>
            {c.apps.map((x) => (
              <a key={x.id} href={appHref(x.slug)}>
                <AppAvatar slug={x.slug} name={x.name} />
                {x.name}
              </a>
            ))}
          </div>
        </div>
        {app ? (
          <div class="nl">
            <h6>{app.name}</h6>
            {tabs().map(([k, l]) =>
              nav(
                appHref(app.slug, k),
                l,
                k,
                route.view === 'app' && route.tab === k,
                k === 'perms' ? (
                  counts.perms ? <span class="ov" title={t('shell.overrides')}>{counts.perms}</span> : null
                ) : counts[k] != null ? (
                  <span class="ct">{counts[k]}</span>
                ) : null,
              ),
            )}
          </div>
        ) : null}
        <div class="nl">
          <h6>{t('shell.console')}</h6>
          {appsOn ? nav('#/apps', t('page.applications'), 'apps', route.view === 'apps') : null}
          {gatewayOn ? nav('#/gateways', t('page.gateways'), 'connections', route.view === 'gateways') : null}
          {appsOn ? nav('#/playground', t('page.playground'), 'play', route.view === 'playground') : null}
          {appsOn ? nav('#/usage', t('page.usage'), 'usage', route.view === 'usage') : null}
          {nav('#/health', t('page.health'), 'health', route.view === 'health')}
          {appsOn ? nav('#/assistant', t('page.assistant'), 'agents', route.view === 'assistant') : null}
          {c.me.deploymentOwner ? nav('#/access', t('page.access'), 'access', route.view === 'access') : null}
          {c.me.deploymentOwner ? nav('#/policy', t('page.systemPolicy'), 'security', route.view === 'policy') : null}
          {c.me.deploymentOwner ? nav('#/modules', t('page.modules'), 'perms', route.view === 'modules') : null}
        </div>
        <div class="foot">
          <a
            class="me"
            href="#/preferences"
            aria-current={route.view === 'preferences' ? 'page' : undefined}
            title={t('shell.prefsTitle')}
            aria-label={t('shell.prefsLabel', { who })}
          >
            <i>{c.me.kind === 'op' ? 'OP' : c.me.kind === 'sso' ? (c.me.name || c.me.email || '?').slice(0, 2).toUpperCase() : 'AD'}</i>
            <span>
              {who}
              <small>{t('page.preferences')}</small>
            </span>
          </a>
          <Btn kind="ic sm ghost" label={t('common.toggleTheme')} onClick={() => toggleTheme()}>
            <Icon name={dark ? 'sun' : 'moon'} />
          </Btn>
          <Btn kind="sm ghost" onClick={() => void signOut()}>
            {t('shell.signOut')}
          </Btn>
        </div>
      </aside>
      <div class={`content ${assistantOn ? 'withassistant' : ''}`}>
        <div class="topbar">
          <Btn kind="ic sm mbar" label={t('shell.menu')} onClick={() => setOpen(!open)}>
            <Icon name="menu" />
          </Btn>
          <nav class="crumbs" aria-label={t('shell.breadcrumb')}>
            {crumbs.map(([label, href], i) => [
              i ? <Icon key={`c${i}`} name="chev" /> : null,
              href && i < crumbs.length - 1 ? (
                <a key={label} href={href}>
                  {label}
                </a>
              ) : (
                <b key={label}>{label}</b>
              ),
            ])}
          </nav>
          <button class="cmdk" onClick={() => setPal(true)}>
            <Icon name="search" />
            <span>{t('shell.goTo')}</span>
            <kbd>Ctrl K</kbd>
          </button>
          <GuidesMenu />
        </div>
        <main class={`enter ${app ? 'inapp' : ''} ${readOnly ? 'ro' : ''}`} key={route.view === 'app' ? `${route.app}/${route.tab}/${route.id}` : route.view}>
          <TourInvite />
          {readOnly ? (
            <p class="hint rob" role="status">
              {t('shell.readOnly', { app: app!.name })}
            </p>
          ) : null}
          {app && alerts.length ? <CostBanner app={app} alerts={alerts} /> : null}
          <div class={fresh ? 'freshwrap' : ''}>{page(route, app, c.me)}</div>
        </main>
      </div>
      {pal ? <Palette apps={c.apps} me={c.me} onClose={() => setPal(false)} /> : null}
      {appsOn ? <AssistantLauncher state={assistant} dark={dark} /> : null}
      <TourOverlay />
    </div>
  )
}

/**
 * The open cost alerts of the selected application, read when the application changes (and when a page says they
 * changed), never on every render. A failure only hides the banner.
 */
function useCostAlerts(app: ConsoleApplication | null): CostAlert[] {
  const [state, setState] = useState<{ app: string; items: CostAlert[] } | null>(null)
  const id = app?.id ?? null
  useEffect(() => {
    if (!id) return
    let live = true
    const read = () =>
      forApp(id)
        .admin.costs.alerts({ open: true })
        .then((items) => live && setState({ app: id, items }))
        .catch(() => live && setState({ app: id, items: [] }))
    void read()
    const again = (e: Event) => {
      const d = (e as CustomEvent<{ app?: string }>).detail
      if (!d?.app || d.app === id) void read()
    }
    addEventListener(ALERTS_CHANGED, again)
    return () => {
      live = false
      removeEventListener(ALERTS_CHANGED, again)
    }
  }, [id])
  return state && state.app === id ? state.items : []
}

/** The banner of the open cost alerts: the most important one, a link to the forecast, and acknowledging them all. */
function CostBanner({ app, alerts }: { app: ConsoleApplication; alerts: CostAlert[] }) {
  const [busy, setBusy] = useState(false)
  const top = bannerAlert(alerts)
  if (!top) return null
  const open = alerts.filter((a) => !a.acknowledgedAt)
  const ack = async () => {
    setBusy(true)
    try {
      await Promise.all(open.map((a) => forApp(app.id).admin.costs.acknowledge(a.id)))
    } catch {
      // What was acknowledged stays so; the banner reads the alerts again either way.
    } finally {
      setBusy(false)
      dispatchEvent(new CustomEvent(ALERTS_CHANGED, { detail: { app: app.id, from: 'banner' } }))
    }
  }
  return (
    <div class="banner" role="status">
      <span class="bi" aria-hidden="true">
        !
      </span>
      <span class="bt">
        <b>{alertText(top)}</b>
        {open.length > 1 ? <span class="s"> {tn('costs.banner.more', open.length - 1)}</span> : null}
      </span>
      <span class="x">
        <Btn sm kind="ghost" href={appHref(app.slug, 'usage')}>
          {t('costs.banner.see')}
        </Btn>
        {app.role !== 'viewer' ? (
          <Btn sm busy={busy ? t('costs.acking') : false} onClick={() => void ack()}>
            {t(open.length > 1 ? 'costs.banner.ackAll' : 'costs.ack')}
          </Btn>
        ) : null}
      </span>
    </div>
  )
}

function Palette({ apps, me, onClose }: { apps: ConsoleApplication[]; me: ConsoleMe; onClose: () => void }) {
  const [q, setQ] = useState('')
  const [hl, setHl] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => input.current?.focus(), [])
  const all = useMemo(
    () => [
      ...(moduleOn(me, 'applications')
        ? [
            [t('page.applications'), '#/apps', t('shell.kindPage')],
            [t('page.playground'), '#/playground', t('shell.kindPage')],
            [t('page.usage'), '#/usage', t('shell.kindPage')],
          ]
        : []),
      ...(moduleOn(me, 'gateway') ? [[t('page.gateways'), '#/gateways', t('shell.kindPage')]] : []),
      [t('page.health'), '#/health', t('shell.kindPage')],
      ...(me.deploymentOwner ? [[t('page.modules'), '#/modules', t('shell.kindPage')]] : []),
      [t('page.preferences'), '#/preferences', t('shell.kindPage')],
      ...PREF_SECTIONS().map(([k, l]) => [`${t('page.preferences')}: ${l}`, `#/preferences?s=${k}`, t('shell.kindPage')]),
      ...apps.flatMap((a) => [
        [a.name, appHref(a.slug), t('shell.kindApplication')],
        ...tabs()
          .slice(1)
          .map(([k, l]) => [`${a.name}: ${l}`, appHref(a.slug, k), t('shell.kindApplication')]),
      ]),
    ],
    [apps, me, locale()],
  )
  const items = all.filter((x) => x[0]!.toLowerCase().includes(q.toLowerCase()))
  const pick = (href: string) => {
    onClose()
    go(href)
  }
  return (
    <div class="scrim on" onClick={(e) => (e.target as Element).classList.contains('scrim') && onClose()}>
      <div class="pal" role="dialog" aria-label={t('shell.goTo')}>
        <input
          ref={input}
          value={q}
          placeholder={t('shell.goToPlaceholder')}
          autocomplete="off"
          onInput={(e) => {
            setQ(e.currentTarget.value)
            setHl(0)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose()
            if (e.key === 'ArrowDown') setHl(Math.min(hl + 1, items.length - 1))
            if (e.key === 'ArrowUp') setHl(Math.max(hl - 1, 0))
            if (e.key === 'Enter' && items[hl]) pick(items[hl]![1]!)
          }}
        />
        <ul>
          {items.length ? (
            items.map((x, i) => (
              <li key={x[1]} class={i === hl ? 'hl' : ''} onClick={() => pick(x[1]!)}>
                {x[0]}
                <span>{x[2]}</span>
              </li>
            ))
          ) : (
            <li style="display:block;cursor:default">
              <Empty size={64}>
                <b>{t('shell.nothingMatches')}</b>
                <br />
                {t('shell.tryName')}
              </Empty>
            </li>
          )}
        </ul>
      </div>
    </div>
  )
}

/** The sections of Preferences the palette can open directly. */
const PREF_SECTIONS = (): Array<[string, string]> => [
  ['language', t('prefs.language')],
  ['appearance', t('prefs.appearance')],
  ['a11y', t('prefs.a11y')],
  ['keys', t('prefs.keyboard')],
]
