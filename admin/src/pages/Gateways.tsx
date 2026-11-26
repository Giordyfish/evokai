/**
 * Gateways: Claude Code and Codex pass through Evokai with people's own seats, under the organisation's policy.
 * A gateway is apart from applications: its own list, its people (one personal key each), its policy (the security
 * page's controls that act on a gateway, with versions, tests and copy), its usage in tokens (never an amount), its
 * security events, the snippets to connect a tool, and a template of the notice to the employees.
 */
import { useMemo, useState } from 'preact/hooks'

import type { Gateway, GatewayProvider, GatewayUsage } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, copy, day } from '../format'
import { claudeManaged, claudeShell, claudeUser, codexConfig, codexShell, gateways, GW_TABS, gwHref, type GwTab, PROVIDERS, TOOL } from '../gateway'
import { Empty, sparkle, toast } from '../magic'
import { appPolicy } from '../policy'
import { go, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Seg, Switch, Tabs, Tag } from '../ui'
import { num, t, type TextKey } from '../i18n'
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
        {({ items, canCreate }) => (
          <>
            {items.length ? (
              <Panel cls="flat">
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
                              <Tag key={p}>{TOOL[p]}</Tag>
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
              <Panel>
                <Empty k="ember">
                  <b>{t('gw.empty')}</b>
                  <br />
                  {t('gw.emptyText')}
                </Empty>
              </Panel>
            )}
            {canCreate ? <CreateGateway /> : null}
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

function CreateGateway() {
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
    <Panel title={t('gw.create')} style="margin-top:16px">
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
        <ProviderSwitches value={providers} onChange={setProviders} />
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

function ProviderSwitches({ value, onChange, disabled }: { value: GatewayProvider[]; onChange: (v: GatewayProvider[]) => void; disabled?: boolean }) {
  return (
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      {PROVIDERS.map((p) => (
        <Switch
          key={p}
          disabled={disabled}
          on={value.includes(p)}
          label={t(`gw.provider.${p}` as TextKey)}
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
              <Tag key={p}>{TOOL[p]}</Tag>
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
  const [shown, setShown] = useState<{ key: string; label: string } | null>(null)
  const [arming, setArming] = useState<string | null>(null)
  const canEdit = g.role === 'owner' || g.role === 'operator'
  const add = async (e: Event) => {
    const btn = e.currentTarget as Element
    try {
      const r = await gateways.addPerson(g.id, label.trim())
      setShown({ key: r.key, label: r.label })
      setLabel('')
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
    <>
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
            <Btn kind="pri" fx="key" disabled={!label.trim()} onClick={(e) => void add(e)}>
              {t('gw.makeKey')}
            </Btn>
          </div>
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
                      <th>{t('keys.key')}</th>
                      <th>{t('keys.created')}</th>
                      <th>{t('keys.lastUsed')}</th>
                      <th>{t('keys.state')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((k) => (
                      <tr key={k.id}>
                        <td>
                          <b>{k.label}</b>
                        </td>
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
                            <Btn sm kind="ghost danger" onClick={() => void revoke(k.id, k.label)}>
                              {arming === k.id ? t('keys.confirmRevoke') : t('keys.revoke')}
                            </Btn>
                          ) : null}
                        </td>
                      </tr>
                    ))}
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
    </>
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
      <GatewaySettings g={g} reload={reload} />
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

function GatewaySettings({ g, reload }: { g: Gateway; reload: () => Promise<void> }) {
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
          <ProviderSwitches value={providers} onChange={setProviders} disabled={!isOwner} />
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
    <>
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
    </>
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
                    <code>{r.model || '-'}</code> <Tag>{TOOL[r.provider]}</Tag>
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

function Snippet({ title, hint, text, prose }: { title: string; hint: string; text: string; prose?: boolean }) {
  return (
    <Panel
      title={title}
      actions={
        <Btn sm check onClick={async () => toast((await copy(text)) ? t('gw.copied') : t('common.copyByHand'), title, null)}>
          {t('common.copy')}
        </Btn>
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

function Connect({ g }: { g: Gateway }) {
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

// Notice

/** The template's paragraphs, in the console's language, filled with this gateway. */
function noticeText(g: Gateway, days: number | null): string {
  const tools = g.providers.map((p) => TOOL[p]).join(', ')
  const keep = days ? t('gw.n.keepDays', { n: days }) : t('gw.n.keepForever')
  return [
    t('gw.n.title', { gateway: g.name }),
    t('gw.n.what', { tools }),
    t('gw.n.recorded'),
    t('gw.n.notRecorded'),
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
  return (
    <Loads load={load} what={t('what.securityPolicy')}>
      {(view) => {
        const text = noticeText(g, view.eventTtlDays ?? view.defaultEventTtlDays ?? null)
        return (
          <>
            <p class="hint">{t('gw.noticeSub')}</p>
            <Snippet prose title={t('gw.tab.notice')} hint={t('gw.noticeHint')} text={text} />
          </>
        )
      }}
    </Loads>
  )
}
