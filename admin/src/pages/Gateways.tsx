/**
 * Gateways: Claude Code and Codex pass through Evokai with people's own seats, under the organisation's policy.
 * A gateway is apart from applications: its own list, its people (one personal key each), its policy (the security
 * page's controls that act on a gateway, with versions, tests and copy), its usage in tokens (never an amount), its
 * security events, the snippets to connect a tool, and a template of the notice to the employees.
 */
import type { ComponentChildren } from 'preact'
import { useEffect, useMemo, useState } from 'preact/hooks'

import type { Gateway, GatewayPerson, GatewayProvider, GatewayUsage, ProxySetup } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, copy, day } from '../format'
import {
  claudeCodeNetwork,
  claudeManaged,
  claudeShell,
  claudeUser,
  codexConfig,
  codexNetwork,
  codexShell,
  desktopLinux,
  desktopMac,
  desktopWindows,
  firefoxPolicy,
  gateways,
  GW_TABS,
  gwHref,
  type GwTab,
  isEmail,
  linuxShell,
  macProfile,
  netValues,
  PROVIDERS,
  proxy,
  PROXY_APPS,
  ROOT_FILE,
  rootDer,
  toolOf,
  vscodeNetwork,
  windowsShell,
} from '../gateway'
import { Empty, sparkle, toast } from '../magic'
import { appPolicy } from '../policy'
import { go, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Seg, Switch, Tabs, Tag } from '../ui'
import { has, num, rich, t, type TextKey } from '../i18n'
import { Loads } from './common'
import { CopyPolicy, PolicyHistory, PolicyTests } from './PolicyTools'
import { Events, Policy, type PolicyOwner } from './Security'

export function Gateways({ gateway, tab }: { gateway: string | null; tab: GwTab }) {
  return gateway ? <GatewayPage slug={gateway} tab={tab} /> : <GatewayList />
}

const slugOf = (name: string): string =>
  name
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)

function GatewayList() {
  setTitle(t('page.gateways'))
  const load = useLoad(() => gateways.list(), [])
  return (
    <>
      <Hero title={t('page.gateways')} sub={t('gw.sub')} />
      <Loads load={load} what={t('what.gateways')}>
        {({ items, canCreate, providers }) => (
          <>
            {items.length ? (
              <Panel cls="flat" tour="gw-list">
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('gw.name')}</th>
                        <th>{t('gw.providers')}</th>
                        <th>{t('gw.people')}</th>
                        <th>{t('keys.state')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((g) => (
                        <tr key={g.id}>
                          <td>
                            <a href={gwHref(g.slug)}>
                              <b>{g.name}</b>
                            </a>
                            <div class="s">
                              <code>{g.slug}</code>
                            </div>
                          </td>
                          <td>
                            {g.providers.map((p) => (
                              <Tag key={p}>{toolOf(p)}</Tag>
                            ))}
                          </td>
                          <td>{num(g.people ?? 0)}</td>
                          <td>
                            <StatusOf g={g} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            ) : (
              <Panel tour="gw-list">
                <Empty k="ember">
                  <b>{t('gw.empty')}</b>
                  <br />
                  {t('gw.emptyText')}
                </Empty>
              </Panel>
            )}
            {canCreate ? <CreateGateway options={providers ?? PROVIDERS} /> : null}
          </>
        )}
      </Loads>
    </>
  )
}

function StatusOf({ g }: { g: Gateway }) {
  return g.status === 'active' ? (
    <Tag tone="ok" dot>
      {t('keys.active')}
    </Tag>
  ) : (
    <Tag tone="bad" dot>
      {t('gw.disabled')}
    </Tag>
  )
}

function CreateGateway({ options }: { options: readonly GatewayProvider[] }) {
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [providers, setProviders] = useState<GatewayProvider[]>(['anthropic'])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const create = async (e: Event) => {
    const btn = e.currentTarget as Element
    setBusy(true)
    setError(null)
    try {
      const g = await gateways.create({
        name: name.trim(),
        slug: slug || slugOf(name),
        providers,
      })
      sparkle(btn)
      toast(t('gw.created'), g.name)
      go(gwHref(g.slug))
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title={t('gw.create')} style="margin-top:16px" tour="gw-create">
      <p class="hint">{t('gw.createSub')}</p>
      <div class="grid g2">
        <Field label={t('gw.name')} id="gw-name">
          <input id="gw-name" class="in" maxLength={120} value={name} onInput={(e) => setName(e.currentTarget.value)} />
        </Field>
        <Field label={t('gw.slug')} id="gw-slug" hint={t('gw.slugHint')}>
          <input id="gw-slug" class="in mono" maxLength={41} value={slug || slugOf(name)} onInput={(e) => setSlug(e.currentTarget.value)} />
        </Field>
      </div>
      <Field label={t('gw.providers')}>
        <ProviderSwitches options={options} value={providers} onChange={setProviders} />
      </Field>
      {error ? (
        <p class="hint" style="color:var(--s-bad)">
          {error}
        </p>
      ) : null}
      <Btn kind="pri" busy={busy ? t('common.saving') : false} disabled={!name.trim() || !providers.length} onClick={(e) => void create(e)}>
        {t('gw.createBtn')}
      </Btn>
    </Panel>
  )
}

/** A switch per provider the service offers (`github` only in builds whose gateway list returns it). */
function ProviderSwitches({
  options,
  value,
  onChange,
  disabled,
}: {
  options: readonly GatewayProvider[]
  value: GatewayProvider[]
  onChange: (v: GatewayProvider[]) => void
  disabled?: boolean
}) {
  return (
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      {options.map((p) => (
        <Switch
          key={p}
          disabled={disabled}
          on={value.includes(p)}
          label={has(`gw.provider.${p}`) ? t(`gw.provider.${p}` as TextKey) : toolOf(p)}
          onChange={(on) => onChange(on ? [...value, p] : value.filter((x) => x !== p))}
        />
      ))}
    </div>
  )
}

function GatewayPage({ slug, tab }: { slug: string; tab: GwTab }) {
  const load = useLoad(() => gateways.get(slug), [slug])
  return (
    <Loads load={load} what={t('what.gateway')}>
      {(g) => <GatewayView g={g} tab={tab} reload={load.reload} />}
    </Loads>
  )
}

function GatewayView({ g, tab, reload }: { g: Gateway; tab: GwTab; reload: () => Promise<void> }) {
  setTitle(t(`gw.tab.${tab}` as TextKey), g.name)
  const owner: PolicyOwner = {
    id: g.id,
    slug: g.slug,
    name: g.name,
    role: g.role ?? null,
  }
  return (
    <>
      <Hero
        title={g.name}
        sub={
          <>
            <StatusOf g={g} />{' '}
            {g.providers.map((p) => (
              <Tag key={p}>{toolOf(p)}</Tag>
            ))}{' '}
            <code>{g.slug}</code>
          </>
        }
      />
      <Tabs
        label={t('gw.sections')}
        current={tab}
        items={GW_TABS.map((k) => ({
          key: k,
          label: t(`gw.tab.${k}` as TextKey),
          href: gwHref(g.slug, k),
        }))}
      />
      <div style="margin-top:16px">
        {tab === 'people' ? <People g={g} /> : null}
        {tab === 'policy' ? <GatewayPolicy g={g} owner={owner} reload={reload} /> : null}
        {tab === 'usage' ? <Usage g={g} /> : null}
        {tab === 'events' ? <Events app={owner} /> : null}
        {tab === 'connect' ? <Connect g={g} /> : null}
        {tab === 'notice' ? <Notice g={g} /> : null}
      </div>
    </>
  )
}

// People

function People({ g }: { g: Gateway }) {
  const load = useLoad(() => gateways.people(g.id), [g.id])
  const [label, setLabel] = useState('')
  const [email, setEmail] = useState('')
  const [shown, setShown] = useState<{ key: string; label: string } | null>(null)
  const [arming, setArming] = useState<string | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const canEdit = g.role === 'owner' || g.role === 'operator'
  const add = async (e: Event) => {
    const btn = e.currentTarget as Element
    try {
      const r = await gateways.addPerson(g.id, label.trim(), email.trim() || undefined)
      setShown({ key: r.key, label: r.label })
      setLabel('')
      setEmail('')
      sparkle(btn)
      toast(t('gw.keyMade'), t('keys.copyNow'), null)
      await load.reload()
    } catch (err) {
      toast(t('gw.keyNotMade'), describe(err), null)
    }
  }
  const revoke = async (id: string, who: string) => {
    if (arming !== id) {
      setArming(id)
      setTimeout(() => setArming((x) => (x === id ? null : x)), 3000)
      return
    }
    setArming(null)
    try {
      await gateways.revoke(g.id, id)
      toast(t('keys.revoked'), who, null)
      await load.reload()
    } catch (err) {
      toast(t('keys.notRevoked'), describe(err), null)
    }
  }
  return (
    <div data-tour="gw-people">
      <p class="hint">{t('gw.peopleSub')}</p>
      {canEdit ? (
        <Panel title={t('gw.addPerson')}>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <input
              class="in"
              style="max-width:320px"
              maxLength={120}
              value={label}
              placeholder={t('gw.personLabel')}
              aria-label={t('gw.personLabel')}
              onInput={(e) => setLabel(e.currentTarget.value)}
            />
            <input
              class="in"
              type="email"
              style="max-width:320px"
              maxLength={254}
              value={email}
              placeholder={t('gw.personEmail')}
              aria-label={t('gw.personEmail')}
              aria-invalid={email.trim() && !isEmail(email) ? 'true' : undefined}
              onInput={(e) => setEmail(e.currentTarget.value)}
            />
            <Btn kind="pri" fx="key" disabled={!label.trim() || (!!email.trim() && !isEmail(email))} onClick={(e) => void add(e)}>
              {t('gw.makeKey')}
            </Btn>
          </div>
          <p class="hint" style="margin:8px 0 0">
            {t('gw.personEmailHint')}
          </p>
        </Panel>
      ) : null}
      {shown ? (
        <Panel cls="keyc on">
          <h3 style="font:600 15.5px var(--disp)">
            {t('gw.keyFor', { who: shown.label })}{' '}
            <Tag tone="gold" dot>
              {t('apps.shownOnce')}
            </Tag>
          </h3>
          <div class="keyrow">
            <div class="k shim">{shown.key}</div>
          </div>
          <p class="hint">{t('gw.keySnippet')}</p>
          <pre class="mono" style="white-space:pre-wrap;font-size:12px">
            {claudeUser(shown.key)}
          </pre>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <Btn
              sm
              kind="pri"
              check
              onClick={async () => toast((await copy(shown.key)) ? t('keys.copied') : t('common.copyByHand'), t('apps.notShownAgain'), null)}
            >
              {t('keys.copyKey')}
            </Btn>
            <Btn
              sm
              onClick={async () =>
                toast((await copy(claudeUser(shown.key))) ? t('gw.copied') : t('common.copyByHand'), t('apps.notShownAgain'), null)
              }
            >
              {t('gw.copySettings')}
            </Btn>
          </div>
        </Panel>
      ) : null}
      <Loads load={load} what={t('what.gatewayPeople')}>
        {({ items }) =>
          items.length ? (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('gw.person')}</th>
                      <th>{t('gw.email')}</th>
                      <th>{t('keys.key')}</th>
                      <th>{t('keys.created')}</th>
                      <th>{t('keys.lastUsed')}</th>
                      <th>{t('keys.state')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((k) =>
                      editing === k.id ? (
                        <PersonEdit key={k.id} g={g} k={k} onDone={() => setEditing(null)} onSaved={load.reload} />
                      ) : (
                      <tr key={k.id}>
                        <td>
                          <b>{k.label}</b>
                        </td>
                        <td class="s">{k.email ? <code>{k.email}</code> : <span class="s">{t('gw.noEmail')}</span>}</td>
                        <td>
                          <code>{k.prefix}_••••</code>
                        </td>
                        <td class="s">{day(k.createdAt)}</td>
                        <td class="s">{ago(k.lastUsedAt)}</td>
                        <td>
                          {k.revoked ? (
                            <Tag tone="bad" dot>
                              {t('keys.revokedTag')}
                            </Tag>
                          ) : (
                            <Tag tone="ok" dot>
                              {t('keys.active')}
                            </Tag>
                          )}
                        </td>
                        <td class="r">
                          {!k.revoked && canEdit ? (
                            <span style="display:inline-flex;gap:6px">
                              <Btn sm kind="ghost" onClick={() => setEditing(k.id)}>
                                {t('gw.editPerson')}
                              </Btn>
                              <Btn sm kind="ghost danger" onClick={() => void revoke(k.id, k.label)}>
                                {arming === k.id ? t('keys.confirmRevoke') : t('keys.revoke')}
                              </Btn>
                            </span>
                          ) : null}
                        </td>
                      </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          ) : (
            <Panel>
              <Empty k="ember">
                <b>{t('gw.noPeople')}</b>
                <br />
                {t('gw.noPeopleText')}
              </Empty>
            </Panel>
          )
        }
      </Loads>
    </div>
  )
}

/** A person's row while their name and e-mail are edited (their key stays the same). */
function PersonEdit({ g, k, onDone, onSaved }: { g: Gateway; k: GatewayPerson; onDone: () => void; onSaved: () => Promise<void> }) {
  const [label, setLabel] = useState(k.label)
  const [email, setEmail] = useState(k.email ?? '')
  const [busy, setBusy] = useState(false)
  const bad = !!email.trim() && !isEmail(email)
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setBusy(true)
    try {
      await gateways.changePerson(g.id, k.id, { label: label.trim(), email: email.trim() })
      sparkle(btn)
      toast(t('gw.personSaved'), label.trim(), null)
      onDone()
      await onSaved()
    } catch (err) {
      toast(t('gw.personNotSaved'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <tr>
      <td>
        <input class="in" maxLength={120} value={label} aria-label={t('gw.personLabel')} onInput={(e) => setLabel(e.currentTarget.value)} />
      </td>
      <td colSpan={4}>
        <input
          class="in"
          type="email"
          maxLength={254}
          value={email}
          placeholder={t('gw.personEmail')}
          aria-label={t('gw.personEmail')}
          aria-invalid={bad ? 'true' : undefined}
          onInput={(e) => setEmail(e.currentTarget.value)}
        />
      </td>
      <td />
      <td class="r">
        <span style="display:inline-flex;gap:6px">
          <Btn sm kind="ghost" onClick={onDone}>
            {t('common.cancel')}
          </Btn>
          <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} disabled={!label.trim() || bad} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
        </span>
      </td>
    </tr>
  )
}

// Policy

function GatewayPolicy({ g, owner, reload }: { g: Gateway; owner: PolicyOwner; reload: () => Promise<void> }) {
  const c = useConsole()
  const api = forApp(g.id)
  const load = useLoad(() => api.admin.security.get(), [g.id])
  const policy = useMemo(() => appPolicy(g.id), [g.id])
  const others = useLoad(() => gateways.list(), [])
  const isOwner = g.role === 'owner'
  const sources = [
    ...(c.me.deploymentOwner ? [{ value: '*system', label: t('pol.systemSource'), system: true }] : []),
    ...(others.data?.items ?? []).filter((x) => x.id !== g.id).map((x) => ({ value: x.slug, label: x.name })),
    ...c.apps.map((a) => ({ value: a.slug, label: a.name })),
  ]
  return (
    <>
      <GatewaySettings g={g} reload={reload} options={others.data?.providers ?? PROVIDERS} />
      <Loads load={load} what={t('what.securityPolicy')}>
        {(view) => <Policy key={JSON.stringify(view.policy)} gateway app={owner} view={view} onSaved={(v) => load.set(v)} onReload={load.reload} />}
      </Loads>
      <div class="grid g2" style="margin-top:16px">
        <PolicyHistory api={policy} canEdit={isOwner} onChanged={load.reload} stamp={load.data?.version} />
        <PolicyTests api={policy} canEdit={isOwner} stamp={load.data?.version} />
      </div>
      {isOwner ? <CopyPolicy api={policy} sources={sources} onChanged={load.reload} /> : null}
    </>
  )
}

const list = (s: string): string[] =>
  s
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean)

function GatewaySettings({ g, reload, options }: { g: Gateway; reload: () => Promise<void>; options: readonly GatewayProvider[] }) {
  const isOwner = g.role === 'owner'
  const [providers, setProviders] = useState<GatewayProvider[]>(g.providers)
  const [models, setModels] = useState(g.allowedModels.join(', '))
  const [perDay, setPerDay] = useState(g.tokensPerDay ? String(g.tokensPerDay) : '')
  const [perMonth, setPerMonth] = useState(g.tokensPerMonth ? String(g.tokensPerMonth) : '')
  const [active, setActive] = useState(g.status === 'active')
  const [dp, setDp] = useState(JSON.stringify(g.dataProtection ?? {}, null, 2))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    const cap = (s: string): number | null => {
      const n = Number(s.replace(/[\s.,]/g, ''))
      return s.trim() === '' ? 0 : Number.isFinite(n) && n >= 0 ? n : null
    }
    const day = cap(perDay)
    const month = cap(perMonth)
    if (day === null || month === null) return setError(t('gw.capInvalid'))
    let protection: Record<string, unknown>
    try {
      protection = dp.trim() ? (JSON.parse(dp) as Record<string, unknown>) : {}
    } catch {
      return setError(t('gw.dpInvalid'))
    }
    setBusy(true)
    setError(null)
    try {
      await gateways.patch(g.id, {
        providers,
        allowed_models: list(models),
        tokens_per_day: day,
        tokens_per_month: month,
        status: active ? 'active' : 'disabled',
        data_protection: protection,
      })
      sparkle(btn)
      toast(t('gw.saved'), g.name)
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel
      title={t('gw.settings')}
      actions={
        isOwner ? (
          <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
        ) : null
      }
      style="margin-bottom:16px"
    >
      {!isOwner ? <p class="hint">{t('gw.ownerOnly')}</p> : null}
      <div class="grid g2">
        <Field label={t('gw.providers')}>
          <ProviderSwitches options={[...new Set([...options, ...g.providers])]} value={providers} onChange={setProviders} disabled={!isOwner} />
        </Field>
        <Field label={t('gw.status')}>
          <Switch on={active} onChange={setActive} disabled={!isOwner} label={active ? t('keys.active') : t('gw.disabled')} />
        </Field>
        <Field label={t('gw.models')} hint={t('gw.modelsHint')} id="gw-models">
          <input
            id="gw-models"
            class="in mono"
            value={models}
            disabled={!isOwner}
            placeholder="claude-*"
            onInput={(e) => setModels(e.currentTarget.value)}
          />
        </Field>
        <Field label={t('gw.caps')} hint={t('gw.capsHint')}>
          <div style="display:flex;gap:8px">
            <input
              class="in"
              inputMode="numeric"
              value={perDay}
              disabled={!isOwner}
              placeholder={t('gw.perDay')}
              aria-label={t('gw.perDay')}
              onInput={(e) => setPerDay(e.currentTarget.value)}
            />
            <input
              class="in"
              inputMode="numeric"
              value={perMonth}
              disabled={!isOwner}
              placeholder={t('gw.perMonth')}
              aria-label={t('gw.perMonth')}
              onInput={(e) => setPerMonth(e.currentTarget.value)}
            />
          </div>
        </Field>
      </div>
      <Field label={t('gw.dp')} hint={t('gw.dpHint')}>
        <textarea class="in mono" value={dp} disabled={!isOwner} spellcheck={false} onInput={(e) => setDp(e.currentTarget.value)} />
      </Field>
      {error ? (
        <p class="hint" style="color:var(--s-bad)">
          {error}
        </p>
      ) : null}
    </Panel>
  )
}

// Usage

const DAYS = ['7', '30', '90'] as const

function Usage({ g }: { g: Gateway }) {
  const [days, setDays] = useState<(typeof DAYS)[number]>('30')
  const load = useLoad(() => gateways.usage(g.id, Number(days)), [g.id, days])
  return (
    <div data-tour="gw-usage">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <p class="hint" style="margin:0">
          {t('gw.usageSub')}
        </p>
        <Seg
          options={DAYS}
          value={days}
          onChange={setDays}
          labels={{
            '7': t('gw.days', { n: 7 }),
            '30': t('gw.days', { n: 30 }),
            '90': t('gw.days', { n: 90 }),
          }}
        />
      </div>
      <Loads load={load} what={t('what.gatewayUsage')}>
        {(u) => <UsageTables u={u} />}
      </Loads>
    </div>
  )
}

function TokenCells({ r }: { r: GatewayUsage['people'][number] | GatewayUsage['models'][number] }) {
  return (
    <>
      <td class="r">{num(r.requests)}</td>
      <td class="r">{num(r.inputTokens)}</td>
      <td class="r">{num(r.outputTokens)}</td>
      <td class="r">{num(r.cacheReadTokens)}</td>
      <td class="r">{r.blocked ? <Tag tone="bad">{num(r.blocked)}</Tag> : num(0)}</td>
    </>
  )
}

function TokenHead({ first }: { first: string }) {
  return (
    <thead>
      <tr>
        <th>{first}</th>
        <th class="r">{t('gw.requests')}</th>
        <th class="r">{t('gw.inputTokens')}</th>
        <th class="r">{t('gw.outputTokens')}</th>
        <th class="r">{t('gw.cacheTokens')}</th>
        <th class="r">{t('gw.blocked')}</th>
      </tr>
    </thead>
  )
}

function UsageTables({ u }: { u: GatewayUsage }) {
  if (!u.people.length)
    return (
      <Panel>
        <Empty k="ember">
          <b>{t('gw.noUsage')}</b>
          <br />
          {t('gw.noUsageText')}
        </Empty>
      </Panel>
    )
  return (
    <>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
        {Object.entries(u.outcomes).map(([o, n]) => (
          <Tag key={o} tone={o === 'forwarded' ? 'ok' : o === 'blocked' ? 'bad' : 'warn'}>
            {t(`gw.o.${o}` as TextKey)}: {num(n)}
          </Tag>
        ))}
      </div>
      <Panel title={t('gw.byPerson')} cls="flat">
        <div class="wx">
          <table class="tbl">
            <TokenHead first={t('gw.person')} />
            <tbody>
              {u.people.map((r) => (
                <tr key={r.keyId ?? r.person}>
                  <td>
                    <b>{r.person}</b>
                  </td>
                  <TokenCells r={r} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title={t('gw.byModel')} cls="flat" style="margin-top:16px">
        <div class="wx">
          <table class="tbl">
            <TokenHead first={t('gw.model')} />
            <tbody>
              {u.models.map((r) => (
                <tr key={`${r.provider}:${r.model}`}>
                  <td>
                    <code>{r.model || '-'}</code> <Tag>{toolOf(r.provider)}</Tag>
                  </td>
                  <TokenCells r={r} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title={t('gw.recent')} cls="flat" style="margin-top:16px">
        <p class="hint">{t('gw.recentSub')}</p>
        <div class="wx">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('audit.when')}</th>
                <th>{t('gw.person')}</th>
                <th>{t('gw.model')}</th>
                <th>{t('gw.outcome')}</th>
                <th class="r">{t('gw.outputTokens')}</th>
                <th class="r">{t('gw.firstByte')}</th>
                <th class="r">{t('gw.duration')}</th>
              </tr>
            </thead>
            <tbody>
              {u.recent.map((r, i) => (
                <tr key={`${r.at}${i}`}>
                  <td class="s">{ago(r.at)}</td>
                  <td>{r.person}</td>
                  <td>
                    <code>{r.model || '-'}</code>
                  </td>
                  <td>
                    <Tag tone={r.outcome === 'forwarded' ? 'ok' : r.outcome === 'blocked' ? 'bad' : 'warn'}>{t(`gw.o.${r.outcome}` as TextKey)}</Tag>
                    {r.control ? <code> {r.control}</code> : null}
                  </td>
                  <td class="r">{num(r.outputTokens)}</td>
                  <td class="r">{r.firstByteMs == null ? '-' : t('gw.ms', { n: num(r.firstByteMs) })}</td>
                  <td class="r">{t('gw.ms', { n: num(r.durationMs) })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}

// Connect

/** Save a text as a file on the PC (a configuration profile, a policy file). */
function saveFile(name: string, text: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function Snippet({
  title,
  hint,
  text,
  prose,
  file,
}: {
  title: string
  hint: ComponentChildren
  text: string
  prose?: boolean
  /** Also offer the text as a file: its name and media type. */
  file?: [string, string]
}) {
  return (
    <Panel
      title={title}
      actions={
        <span style="display:inline-flex;gap:6px">
          {file ? (
            <Btn sm onClick={() => saveFile(file[0], text, file[1])}>
              {t('gw.net.download')}
            </Btn>
          ) : null}
          <Btn sm check onClick={async () => toast((await copy(text)) ? t('gw.copied') : t('common.copyByHand'), title, null)}>
            {t('common.copy')}
          </Btn>
        </span>
      }
    >
      <p class="hint">{hint}</p>
      {prose ? (
        <div style="white-space:pre-wrap;line-height:1.55">{text}</div>
      ) : (
        <pre class="mono" style="white-space:pre-wrap;font-size:12px">
          {text}
        </pre>
      )}
    </Panel>
  )
}

const CONNECT_VIEWS = ['direct', 'network'] as const
type ConnectView = (typeof CONNECT_VIEWS)[number]

function Connect({ g }: { g: Gateway }) {
  const [view, setView] = useState<ConnectView>(() => (location.hash.includes('view=network') ? 'network' : 'direct'))
  return (
    <div data-tour="gw-connect">
      <div style="margin-bottom:12px" data-tour="gw-connect-views">
        <Seg options={CONNECT_VIEWS} value={view} onChange={setView} labels={{ direct: t('gw.connect.direct'), network: t('gw.connect.network') }} />
      </div>
      {view === 'direct' ? <DirectAddress g={g} /> : <CompanyNetwork g={g} />}
    </div>
  )
}

function DirectAddress({ g }: { g: Gateway }) {
  return (
    <>
      <p class="hint">{t('gw.connectSub')}</p>
      <div class="grid g2">
        <Snippet title={t('gw.snip.managed')} hint={t('gw.snip.managedHint')} text={claudeManaged()} />
        <Snippet title={t('gw.snip.user')} hint={t('gw.snip.userHint')} text={claudeUser()} />
      </div>
      <Snippet title={t('gw.snip.shell')} hint={t('gw.snip.shellHint')} text={claudeShell()} />
      {g.providers.includes('openai') ? (
        <div class="grid g2" style="margin-top:16px">
          <Snippet title={t('gw.snip.codex')} hint={t('gw.snip.codexHint')} text={codexConfig()} />
          <Snippet title={t('gw.snip.codexKey')} hint={t('gw.snip.codexKeyHint')} text={codexShell()} />
        </div>
      ) : null}
    </>
  )
}

// Connect: the company network (the gateway proxy)

function CompanyNetwork({ g }: { g: Gateway }) {
  const load = useLoad(() => proxy.setup(), [])
  return (
    <Loads load={load} what={t('what.proxySetup')}>
      {(s) => <Network g={g} s={s} />}
    </Loads>
  )
}

const uuid = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID().toUpperCase()
    : 'xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx'.replace(/x/g, () => Math.floor(Math.random() * 16).toString(16)).toUpperCase()

/** A value with its copy button (the PAC's address, the proxy's, an egress address). */
function Value({ label, value, hint }: { label: string; value: string | null; hint?: ComponentChildren }) {
  return (
    <div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;padding:6px 0">
      <span style="min-width:200px">
        <b>{label}</b>
      </span>
      {value ? (
        <>
          <code style="word-break:break-all">{value}</code>
          <Btn sm kind="ghost" check onClick={async () => toast((await copy(value)) ? t('gw.copied') : t('common.copyByHand'), label, null)}>
            {t('common.copy')}
          </Btn>
        </>
      ) : (
        <Tag tone="warn">{t('gw.net.notSet')}</Tag>
      )}
      {hint ? (
        <div class="hint" style="flex-basis:100%;margin:0">
          {hint}
        </div>
      ) : null}
    </div>
  )
}

/** Numbered steps, each a message of the dictionaries (with code parts where it names a value). */
function Steps({ items }: { items: ComponentChildren[] }) {
  return (
    <ol style="margin:0 0 8px;padding-left:20px;line-height:1.6">
      {items.map((x, i) => (
        <li key={i}>{x}</li>
      ))}
    </ol>
  )
}

const code = (s: string): ComponentChildren => <code>{s}</code>

function Network({ g, s }: { g: Gateway; s: ProxySetup }) {
  const v = netValues(s)
  const [der, setDer] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    void rootDer(s.caDerUrl).then((x) => live && setDer(x))
    return () => {
      live = false
    }
  }, [s.caDerUrl])
  const ids = useMemo(() => [uuid(), uuid(), uuid()] as const, [])
  const uses = (p: string): boolean => g.providers.includes(p as GatewayProvider) || s.providers.includes(p)
  const notServed = (p: string): ComponentChildren =>
    s.providers.includes(p) ? null : (
      <Tag tone="warn" title={t('gw.net.notServedHint')}>
        {t('gw.net.notServed')}
      </Tag>
    )
  return (
    <div data-tour="gw-network" style="display:flex;flex-direction:column;gap:16px">
      <p class="hint" style="margin:0">{t('gw.net.sub')}</p>
      {!s.configured ? (
        <Panel cls="flat">
          <Tag tone="warn" dot>
            {t('gw.net.notConfigured')}
          </Tag>{' '}
          <span class="s">{rich('gw.net.notConfiguredText', { address: code('EVOKAI_PROXY_PUBLIC_ADDRESS'), cert: code('EVOKAI_PROXY_CA_CERT') })}</span>
        </Panel>
      ) : null}
      <Panel title={t('gw.net.basics')}>
        <Value label={t('gw.net.pac')} value={s.pacUrl} hint={t('gw.net.pacHint')} />
        <Value label={t('gw.net.address')} value={s.publicAddress} hint={t('gw.net.addressHint')} />
        <div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;padding:6px 0">
          <span style="min-width:200px">
            <b>{t('gw.net.root')}</b>
          </span>
          {s.caUrl ? (
            <>
              <Btn sm href={s.caUrl}>
                {t('gw.net.rootPem')}
              </Btn>
              <Btn sm href={s.caDerUrl ?? s.caUrl}>
                {t('gw.net.rootDer')}
              </Btn>
            </>
          ) : (
            <Tag tone="warn">{t('gw.net.notSet')}</Tag>
          )}
          <div class="hint" style="flex-basis:100%;margin:0">
            {t('gw.net.rootHint')}
          </div>
        </div>
        <div style="display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;padding:6px 0">
          <span style="min-width:200px">
            <b>{t('gw.net.served')}</b>
          </span>
          {s.providers.length ? s.providers.map((p) => <Tag key={p}>{PROXY_APPS[p] ?? p}</Tag>) : <Tag tone="warn">{t('gw.net.servedNone')}</Tag>}
        </div>
      </Panel>

      <Panel title={t('gw.net.order')}>
        <Steps items={[t('gw.net.order1'), t('gw.net.order2'), t('gw.net.order3'), t('gw.net.order4'), t('gw.net.order5')]} />
      </Panel>

      <h3 style="font:600 17px var(--disp);margin:12px 0 0">{t('gw.net.pcs')}</h3>
      <Panel title={t('gw.net.win')}>
        <Steps
          items={[
            rich('gw.net.win1', { store: code('Computer certificate store - Root') }),
            rich('gw.net.win2', { setting: code('Setup Script Url'), pac: code(v.pac) }),
            t('gw.net.win3'),
          ]}
        />
      </Panel>
      <Snippet title={t('gw.net.winShell')} hint={t('gw.net.winShellHint')} text={windowsShell(v)} />
      <Panel title={t('gw.net.mac')}>
        <Steps items={[t('gw.net.mac1'), rich('gw.net.mac2', { payload: code('com.apple.proxy.http.global') }), t('gw.net.mac3')]} />
      </Panel>
      <Snippet
        title={t('gw.net.macProfile')}
        hint={der ? t('gw.net.macProfileHint') : t('gw.net.macProfileNoRoot')}
        text={macProfile(v, der, ids)}
        file={['evokai-proxy.mobileconfig', 'application/x-apple-aspen-config']}
      />
      <Snippet title={t('gw.net.linux')} hint={t('gw.net.linuxHint')} text={linuxShell(v)} />

      <h3 style="font:600 17px var(--disp);margin:12px 0 0">{t('gw.net.tools')}</h3>
      {uses('anthropic') ? (
        <>
          <Panel title={t('gw.net.desktop')}>
            <p class="hint">{rich('gw.net.desktopHint', { key: code('egressProxyPacUrl'), fixed: code('egressProxyUrl') })}</p>
          </Panel>
          <div class="grid g2">
            <Snippet title={t('gw.net.desktopMac')} hint={rich('gw.net.desktopMacHint', { domain: code('com.anthropic.claudefordesktop') })} text={desktopMac(v)} />
            <Snippet title={t('gw.net.desktopWin')} hint={rich('gw.net.desktopWinHint', { key: code('HKLM\\SOFTWARE\\Policies\\Claude') })} text={desktopWindows(v)} />
          </div>
          <Snippet title={t('gw.net.desktopLinux')} hint={rich('gw.net.desktopLinuxHint', { file: code('/etc/claude-desktop/managed-settings.json') })} text={desktopLinux(v)} />
          <Snippet
            title={t('gw.net.claudeCode')}
            hint={
              <>
                {rich('gw.net.claudeCodeHint', { mac: code(ROOT_FILE.mac), win: code(ROOT_FILE.windows), linux: code(ROOT_FILE.linux) })}
                <br />
                <br />
                {rich('gw.net.claudeCodeLimit', { no: code('NO_PROXY'), direct: <b>{t('gw.connect.direct')}</b> })}
              </>
            }
            text={claudeCodeNetwork(v)}
          />
        </>
      ) : null}
      {uses('openai') ? (
        <Snippet
          title={t('gw.net.codex')}
          hint={
            <>
              {notServed('openai')}{' '}
              {rich('gw.net.codexHint', { proxy: code('HTTPS_PROXY'), ca: code('CODEX_CA_CERTIFICATE'), fallback: code('SSL_CERT_FILE'), linux: code(ROOT_FILE.linux) })}
            </>
          }
          text={codexNetwork(v)}
        />
      ) : null}
      {uses('github') ? (
        <Snippet
          title={t('gw.net.vscode')}
          hint={
            <>
              {notServed('github')} {t('gw.net.vscodeHint')}
            </>
          }
          text={vscodeNetwork()}
        />
      ) : null}
      <Snippet
        title={t('gw.net.firefox')}
        hint={rich('gw.net.firefoxHint', { policy: code('Certificates > ImportEnterpriseRoots') })}
        text={firefoxPolicy()}
        file={['policies.json', 'application/json']}
      />

      <h3 style="font:600 17px var(--disp);margin:12px 0 0">{t('gw.net.enforce')}</h3>
      <Panel title={t('gw.net.allow')}>
        <p class="hint">{t('gw.net.allowSub')}</p>
        {s.egressIps.length ? (
          s.egressIps.map((ip) => <Value key={ip} label={t('gw.net.egress')} value={ip} />)
        ) : (
          <Value label={t('gw.net.egress')} value={null} hint={rich('gw.net.egressHint', { name: code('EVOKAI_PROXY_EGRESS_IPS') })} />
        )}
        <Steps items={[t('gw.net.allowClaude'), t('gw.net.allowChatgpt'), t('gw.net.allowGithub')]} />
        <p class="hint">{t('gw.net.allowNote')}</p>
      </Panel>
      <Panel title={t('gw.net.fallback')}>
        <p>
          {t('gw.net.fallbackNow')}{' '}
          <Tag tone={v.direct ? 'warn' : 'ok'}>{s.pacFallback}</Tag>
        </p>
        <Steps items={[rich('gw.net.fallbackBlock', { v: code('block') }), rich('gw.net.fallbackDirect', { v: code('direct') })]} />
        <p class="hint">{rich('gw.net.fallbackHow', { name: code('EVOKAI_PROXY_PAC_FALLBACK') })}</p>
      </Panel>
      <Panel title={t('gw.net.check')} tour="gw-check">
        <p class="hint">{t('gw.net.checkHint')}</p>
        <a class="btn sm" href={s.checkUrl} target="_blank" rel="noopener noreferrer">
          <span class="lb">{s.checkUrl}</span>
        </a>
      </Panel>
    </div>
  )
}

// Notice

/** The template's paragraphs, in the console's language, filled with this gateway. */
function noticeText(g: Gateway, days: number | null, viaProxy: boolean): string {
  const tools = g.providers.map((p) => toolOf(p)).join(', ')
  const keep = days ? t('gw.n.keepDays', { n: days }) : t('gw.n.keepForever')
  return [
    t('gw.n.title', { gateway: g.name }),
    t('gw.n.what', { tools }),
    t('gw.n.recorded'),
    t('gw.n.notRecorded'),
    ...(viaProxy ? [t('gw.n.proxy')] : []),
    t('gw.n.checks'),
    keep,
    t('gw.n.purpose'),
    t('gw.n.basis'),
    t('gw.n.statuto'),
    t('gw.n.rights'),
    t('gw.n.contact'),
  ].join('\n\n')
}

function Notice({ g }: { g: Gateway }) {
  const load = useLoad(() => forApp(g.id).admin.security.get(), [g.id])
  const net = useLoad(() => proxy.setup(), [])
  return (
    <Loads load={load} what={t('what.securityPolicy')}>
      {(view) => {
        const text = noticeText(g, view.eventTtlDays ?? view.defaultEventTtlDays ?? null, !!net.data?.configured)
        return (
          <div data-tour="gw-notice">
            <p class="hint">{t('gw.noticeSub')}</p>
            <Snippet prose title={t('gw.tab.notice')} hint={t('gw.noticeHint')} text={text} />
          </div>
        )
      }}
    </Loads>
  )
}
