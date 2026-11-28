/**
 * The gateway proxy's status (gateway module; the operator key, or an operator or owner of every gateway): whether it
 * runs (its instances' heartbeats), the last day's PCs, people and requests, format alerts, refused organisations and
 * unidentified requests, and the `proxy` console settings (allowed organisations per provider, unidentified requests,
 * default gateway), which deployment owners change here. The proxy itself is never switched from the console.
 */
import { useState } from 'preact/hooks'

import type { ProxySettings, ProxyStatus } from '@client/core/types'
import { describe } from '../api'
import { ago } from '../format'
import { proxy, PROXY_APPS } from '../gateway'
import { sparkle, toast } from '../magic'
import { setTitle, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Seg, Tag } from '../ui'
import { has, num, rich, t, type TextKey } from '../i18n'
import { Loads } from './common'

export function Proxy() {
  setTitle(t('page.proxy'))
  const load = useLoad(() => proxy.status(), [])
  return (
    <>
      <Hero title={t('page.proxy')} sub={t('px.sub')} />
      <Loads load={load} what={t('what.proxy')}>
        {(s) => <StatusView s={s} reload={load.reload} />}
      </Loads>
    </>
  )
}

const code = (s: string) => <code>{s}</code>

function Stat({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div class="panel flat" style="padding:14px 16px;min-width:150px;flex:1">
      <div class="s">{label}</div>
      <div style="font:600 26px var(--disp);margin-top:4px">{num(value)}</div>
      {hint ? <div class="hint" style="margin:4px 0 0">{hint}</div> : null}
    </div>
  )
}

const surfaceLabel = (k: string): string => (has(`px.surface.${k}`) ? t(`px.surface.${k}` as TextKey) : k)

function StatusView({ s, reload }: { s: ProxyStatus; reload: () => Promise<void> }) {
  const d = s.lastDay
  return (
    <div data-tour="px-status">
      <Panel
        title={t('px.state')}
        actions={
          <Btn sm kind="ghost" onClick={() => void reload()}>
            {t('px.refresh')}
          </Btn>
        }
      >
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          {s.running ? (
            <Tag tone="ok" dot>
              {t('px.running')}
            </Tag>
          ) : (
            <Tag tone="bad" dot>
              {t('px.notRunning')}
            </Tag>
          )}
          {s.configured ? <Tag tone="ok">{t('px.configured')}</Tag> : <Tag tone="warn">{t('px.notConfigured')}</Tag>}
          {s.providers.length ? s.providers.map((p) => <Tag key={p}>{PROXY_APPS[p] ?? p}</Tag>) : <Tag tone="warn">{t('px.noProviders')}</Tag>}
          <Tag tone={s.pacFallback === 'direct' ? 'warn' : ''}>{t('px.fallback', { v: s.pacFallback })}</Tag>
        </div>
        <p class="hint">{s.running ? t('px.runningHint') : rich('px.notRunningHint', { cmd: code('evokai proxy'), on: code('EVOKAI_PROXY=on') })}</p>
        {s.publicAddress ? (
          <p class="s" style="margin:0">
            {t('px.address')} <code>{s.publicAddress}</code>
          </p>
        ) : null}
        {s.instances.length ? (
          <div class="wx" style="margin-top:12px">
            <table class="tbl">
              <thead>
                <tr>
                  <th>{t('px.instance')}</th>
                  <th>{t('px.lastBeat')}</th>
                  <th>{t('px.providers')}</th>
                  <th class="r">{t('px.pcs')}</th>
                </tr>
              </thead>
              <tbody>
                {s.instances.map((i) => (
                  <tr key={i.instance}>
                    <td>
                      <code>{i.instance}</code>{' '}
                      <Tag tone={i.running ? 'ok' : 'bad'} dot>
                        {i.running ? t('px.running') : t('px.stale')}
                      </Tag>
                    </td>
                    <td class="s">{ago(i.at)}</td>
                    <td class="s">{i.providers.join(', ') || '-'}</td>
                    <td class="r">{num(i.pcs24h)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </Panel>

      <h3 style="font:600 17px var(--disp);margin:24px 0 10px">{t('px.lastDay')}</h3>
      <div style="display:flex;gap:12px;flex-wrap:wrap" data-tour="px-day">
        <Stat label={t('px.pcs')} value={d.pcs} hint={t('px.pcsHint')} />
        <Stat label={t('px.people')} value={d.people} hint={t('px.peopleHint')} />
        <Stat label={t('px.accounts')} value={d.accounts} hint={t('px.accountsHint')} />
        <Stat label={t('px.requests')} value={d.requests} />
        <Stat label={t('px.unidentified')} value={d.unidentifiedRefused + d.unidentifiedRecorded} hint={t('px.unidentifiedHint', { refused: d.unidentifiedRefused, recorded: d.unidentifiedRecorded })} />
      </div>
      {Object.keys(d.surfaces).length ? (
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px">
          {Object.entries(d.surfaces).map(([k, n]) => (
            <Tag key={k}>
              {surfaceLabel(k)}: {num(n)}
            </Tag>
          ))}
        </div>
      ) : null}

      <Panel title={t('px.alerts')} style="margin-top:16px" tour="px-alerts">
        <p class="hint">{rich('px.alertsHint', { event: code('proxy.format_unknown') })}</p>
        {d.formatAlerts.length ? (
          <div class="wx">
            <table class="tbl">
              <thead>
                <tr>
                  <th>{t('audit.when')}</th>
                  <th>{t('px.adapter')}</th>
                  <th>{t('px.host')}</th>
                  <th>{t('px.why')}</th>
                  <th>{t('px.gateway')}</th>
                </tr>
              </thead>
              <tbody>
                {d.formatAlerts.map((a, i) => (
                  <tr key={`${a.at}${i}`}>
                    <td class="s">{ago(a.at)}</td>
                    <td>
                      <code>{a.adapter}</code>
                    </td>
                    <td>
                      <code>{a.host || '-'}</code>
                    </td>
                    <td class="s">{a.why || '-'}</td>
                    <td class="s">{a.gateway || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p class="s" style="margin:0">
            <Tag tone="ok" dot>
              {t('px.noAlerts')}
            </Tag>
          </p>
        )}
      </Panel>

      <Panel title={t('px.orgs')} style="margin-top:16px">
        <p class="hint">{t('px.orgsHint')}</p>
        {d.refusedOrgs.length ? (
          <div class="wx">
            <table class="tbl">
              <thead>
                <tr>
                  <th>{t('px.provider')}</th>
                  <th>{t('px.org')}</th>
                  <th class="r">{t('px.refusals')}</th>
                  <th class="r">{t('px.people')}</th>
                  <th>{t('px.last')}</th>
                </tr>
              </thead>
              <tbody>
                {d.refusedOrgs.map((o) => (
                  <tr key={`${o.provider}:${o.org}`}>
                    <td>{PROXY_APPS[o.provider] ?? o.provider}</td>
                    <td>
                      <code>{o.org || '-'}</code>
                    </td>
                    <td class="r">{num(o.events)}</td>
                    <td class="r">{num(o.people)}</td>
                    <td class="s">{ago(o.lastAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p class="s" style="margin:0">
            <Tag tone="ok" dot>
              {t('px.noOrgs')}
            </Tag>
          </p>
        )}
      </Panel>

      <SettingsPanel key={JSON.stringify(s.settings)} s={s} reload={reload} />

      <p class="hint" style="margin-top:16px">
        {t('px.setupHint')}
      </p>
    </div>
  )
}

const UNIDENTIFIED = ['block', 'record'] as const

const orgLines = (v: Record<string, string[]>, p: string): string => (v[p] ?? []).join('\n')

function SettingsPanel({ s, reload }: { s: ProxyStatus; reload: () => Promise<void> }) {
  const value = s.settings.value
  const [orgs, setOrgs] = useState<Record<string, string>>(() => Object.fromEntries(s.catalogue.map((p) => [p, orgLines(value.allowed_orgs, p)])))
  const [mode, setMode] = useState<ProxySettings['unidentified']>(value.unidentified)
  const [gateway, setGateway] = useState<string>(() => {
    const d = value.default_gateway
    return s.gateways.find((g) => g.slug === d || g.id === d)?.slug ?? ''
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const edit = s.canEdit
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setBusy(true)
    setError(null)
    try {
      const allowed: Record<string, string[]> = {}
      for (const [p, text] of Object.entries(orgs)) {
        const list = text
          .split(/[\s,]+/)
          .map((x) => x.trim())
          .filter(Boolean)
        if (list.length) allowed[p] = list
      }
      await proxy.save({ allowed_orgs: allowed, unidentified: mode, default_gateway: gateway || null })
      sparkle(btn)
      toast(t('px.saved'), t('px.savedSub'), null)
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  const reset = async () => {
    setBusy(true)
    setError(null)
    try {
      await proxy.reset()
      toast(t('px.resetDone'), t('px.savedSub'), null)
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel
      title={t('px.settings')}
      style="margin-top:16px"
      tour="px-settings"
      actions={<Tag tone={s.settings.stored ? 'vi' : ''}>{s.settings.stored ? t('px.fromConsole') : t('px.fromEnv')}</Tag>}
    >
      {!edit ? <p class="hint">{t('px.ownerOnly')}</p> : <p class="hint">{t('px.settingsHint')}</p>}
      <Field label={t('px.allowedOrgs')} hint={t('px.allowedOrgsHint')}>
        <div class="grid g2">
          {s.catalogue.map((p) => (
            <Field key={p} label={PROXY_APPS[p] ?? p} id={`px-orgs-${p}`}>
              <textarea
                id={`px-orgs-${p}`}
                class="in mono"
                rows={3}
                style="min-height:0;height:76px"
                spellcheck={false}
                disabled={!edit}
                value={orgs[p] ?? ''}
                placeholder={t('px.orgsPlaceholder')}
                onInput={(e) => {
                  const text = e.currentTarget.value
                  setOrgs((o) => ({ ...o, [p]: text }))
                }}
              />
            </Field>
          ))}
        </div>
      </Field>
      <div class="grid g2">
        <Field label={t('px.unidentifiedMode')} hint={mode === 'block' ? t('px.blockHint') : t('px.recordHint')}>
          <Seg options={UNIDENTIFIED} value={mode} onChange={setMode} disabled={!edit} labels={{ block: t('px.block'), record: t('px.record') }} />
        </Field>
        <Field label={t('px.defaultGateway')} hint={t('px.defaultGatewayHint')} id="px-default">
          <select id="px-default" class="in" disabled={!edit} value={gateway} onChange={(e) => setGateway(e.currentTarget.value)}>
            <option value="">{t('px.noDefault')}</option>
            {s.gateways.map((g) => (
              <option key={g.id} value={g.slug}>
                {g.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {mode === 'record' && !gateway ? (
        <p class="hint" style="color:var(--s-warn)">
          {t('px.recordNeedsGateway')}
        </p>
      ) : null}
      {error ? (
        <p class="hint" role="alert" style="color:var(--s-bad)">
          {error}
        </p>
      ) : null}
      {edit ? (
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <Btn kind="pri" check busy={busy ? t('common.saving') : false} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
          {s.settings.stored ? (
            <Btn kind="ghost" disabled={busy} onClick={() => void reset()}>
              {t('px.reset')}
            </Btn>
          ) : null}
        </div>
      ) : null}
    </Panel>
  )
}
