/** An application at a glance: what it can use, its agents, this month, what changed lately. */
import { useState } from 'preact/hooks'

import type { ActivityItem, AgentDef, ConsoleApplication } from '@client/core/types'
import { describe, forApp } from '../api'
import { apiBase } from '../api-base'
import { ago, bar, copy, download, modelName, pl, sharePct } from '../format'
import { toast } from '../magic'
import { appHref, setTitle, useLoad } from '../state'
import { AppAvatar, Bar, Btn, Hero, KV, Panel, Tag } from '../ui'
import { num, t } from '../i18n'
import { Loads } from './common'
import { Summary } from './summary'

export function ChannelTags({ channels }: { channels: string[] }) {
  return (
    <>
      {channels.map((c) => (
        <Tag key={c} tone={c === 'server' ? 'kserver' : 'vi'}>
          {c}
        </Tag>
      ))}{' '}
    </>
  )
}

function activityText(a: ActivityItem, keys: Map<string, string>): [string, string] {
  const by = a.by
    ? a.by === 'config'
      ? t('ov.byConfig')
      : a.by === 'operator'
        ? t('ov.byOperator')
        : keys.get(a.by)
          ? t('ov.byAdminKey', { prefix: keys.get(a.by) })
          : t('ov.byAdminKeyBare')
    : ''
  const when = ago(a.at)
  const withBy = (s: string) => (by ? `${s}, ${by}` : s)
  switch (a.kind) {
    case 'agent':
      return [t('ov.agentSaved', { name: a.name }), withBy(t('ov.agentSavedSub', { when, version: String(a.detail).slice(0, 7) }))]
    case 'key_created':
      return [t(a.detail === 'adm' ? 'ov.adminKeyCreated' : 'ov.serverKeyCreated', { name: a.name }), when]
    case 'key_revoked':
      return [t(a.detail === 'adm' ? 'ov.adminKeyRevoked' : 'ov.serverKeyRevoked', { name: a.name }), when]
    default: {
      const [kind, ...rest] = a.name.split(':')
      const name = rest.join(':')
      const title =
        a.detail === true ? t('ov.granted', { name }) : a.detail === false ? t('ov.revoked', { name }) : t('ov.setTo', { name, value: String(a.detail) })
      return [title, withBy(t('ov.permSub', { when, kind }))]
    }
  }
}

/** The service's public URL with its base path, as this console reaches it. */
function serviceUrl(): string {
  return `${location.origin}${apiBase(location.pathname)}`
}

function Endpoint({ name, url, help }: { name: string; url: string; help: string }) {
  return (
    <li>
      <div style="display:flex;align-items:center;gap:8px;justify-content:space-between">
        <b>{name}</b>
        <Btn sm kind="ghost" onClick={async () => toast((await copy(url)) ? t('ov.std.copied') : t('common.copyByHand'), url, null)}>
          {t('ov.std.copy')}
        </Btn>
      </div>
      <code class="s" style="word-break:break-all">{url}</code>
      <small>{help}</small>
    </li>
  )
}

/** The open standards this application's agents speak: AG-UI, the OpenAI-compatible API and A2A agent cards. */
function Standards({ app, agents }: { app: ConsoleApplication; agents: AgentDef[] }) {
  const base = serviceUrl()
  const a2aOn = app.grants.a2a === true
  const published = agents.filter((g) => g.a2a)
  return (
    <Panel title={t('ov.std.title')}>
      <p class="hint">{t('ov.std.sub')}</p>
      <ul class="vlist">
        <Endpoint name={t('ov.std.agui')} url={`${base}/v1/session/agui`} help={t('ov.std.aguiHelp')} />
        <Endpoint name={t('ov.std.openai')} url={`${base}/v1/openai`} help={t('ov.std.openaiHelp')} />
        {a2aOn
          ? published.map((g) => (
              <Endpoint
                key={g.name}
                name={t('ov.std.a2aCard', { agent: g.name })}
                url={`${base}/v1/a2a/${encodeURIComponent(app.id)}/${encodeURIComponent(g.name)}/.well-known/agent-card.json`}
                help={t('ov.std.a2aHelp')}
              />
            ))
          : null}
      </ul>
      {!a2aOn ? (
        <p class="hint">
          {t('ov.std.a2aOff')} <a href={appHref(app.slug, 'perms')}>{t('ov.editPerms')}</a>
        </p>
      ) : !published.length ? (
        <p class="hint">{t('ov.std.a2aNone')}</p>
      ) : null}
    </Panel>
  )
}

export function Overview({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.overview'), app.name)
  const api = forApp(app.id)
  const load = useLoad(
    async () => {
      const [agents, activity, keys] = await Promise.all([api.admin.agents.list(), api.admin.activity({ limit: 8 }), api.admin.keys.list()])
      return { agents, activity, keys: new Map(keys.map((k) => [k.id, k.prefix])) }
    },
    [app.id],
  )
  const [exporting, setExporting] = useState(false)
  const exportYaml = async () => {
    setExporting(true)
    try {
      const r = await api.admin.export()
      const copied = await copy(r.yaml)
      download(r.filename, r.yaml, 'application/yaml')
      toast(t(copied ? 'ov.yamlCopied' : 'ov.yamlDownloaded'), r.filename)
    } catch (e) {
      toast(t('ov.exportFailed'), describe(e), null)
    } finally {
      setExporting(false)
    }
  }
  const u = app.usage
  return (
    <>
      <Hero
        title={app.name}
        sub={t('ov.sub', {
          slug: app.slug,
          locale: app.locale,
          timezone: app.timezone,
          where: t(app.storageMode === 'evokai' ? 'ov.inEvokai' : 'ov.byApp'),
        })}
        avatar={<AppAvatar slug={app.slug} name={app.name} big />}
        actions={
          <>
            <Btn href={`#/playground?app=${encodeURIComponent(app.slug)}`}>{t('ov.openPlayground')}</Btn>
            <Btn busy={exporting ? t('ov.exporting') : false} onClick={() => void exportYaml()}>
              {t('ov.export')}
            </Btn>
          </>
        }
      />
      <div class="grid g-21">
        <div class="grid">
          <Panel title={t('ov.canUse')} actions={<Btn sm href={appHref(app.slug, 'perms')}>{t('ov.editPerms')}</Btn>}>
            <Summary s={app.grants} overrides={app.counts.overrides} />
          </Panel>
          <Panel title={t('tab.agents')} cls="flat" style="padding:16px 6px 6px">
            <Loads load={load} what={t('what.agents')} panel={false}>
              {(d) =>
                d.agents.length ? (
                  <div class="wx">
                    <table class="tbl">
                      <tbody>
                        {d.agents.map((g: AgentDef & { version: string }) => (
                          <tr key={g.name} data-go onClick={() => (location.hash = appHref(app.slug, 'agents', g.name))}>
                            <td>
                              <b>{g.name}</b>
                              <div class="s">{g.title}</div>
                            </td>
                            <td>
                              <ChannelTags channels={g.channels ?? ['browser']} />
                            </td>
                            <td class="s">{modelName(g.model)}</td>
                            <td class="num">{g.version.slice(0, 7)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p class="hint" style="padding:0 14px 12px">{t('ov.noAgents')}</p>
                )
              }
            </Loads>
          </Panel>
        </div>
        <div class="grid">
          <Panel title={t('ov.thisMonth')}>
            <div style="display:flex;align-items:baseline;gap:8px">
              <b style="font:600 40px var(--disp);letter-spacing:-.03em">{sharePct(u.monthPct, t('apps.noCap'))}</b>
              <span style="color:var(--mu)">{u.monthPct != null ? t('ov.monthUsed') : t('apps.noMonthlyCap')}</span>
            </div>
            <Bar value={bar(u.monthPct)} style="margin:10px 0 14px" />
            <KV
              rows={[
                [t('ov.turns'), num(app.turnsMonth)],
                [t('ov.subjects'), num(app.subjectsMonth)],
                [t('ov.leftMonth'), sharePct(u.monthLeftPct)],
                [t('ov.today'), u.dayPct != null ? t('ov.ofDaily', { share: sharePct(u.dayPct) }) : t('ov.noDaily')],
                [t('ov.ttl'), app.conversationTtlDays ? pl(app.conversationTtlDays, 'n.day') : t('ov.kept')],
              ]}
            />
          </Panel>
          <Loads load={load} what={t('what.agents')} panel={false}>
            {(d) => <Standards app={app} agents={d.agents} />}
          </Loads>
          <Panel title={t('ov.activity')}>
            <Loads load={load} what={t('what.activity')} panel={false}>
              {(d) =>
                d.activity.length ? (
                  <ul class="vlist">
                    {d.activity.map((a, i) => {
                      const [title, s] = activityText(a, d.keys)
                      return (
                        <li key={`${a.at}${a.name}`} class={i === 0 ? 'live' : ''}>
                          <b>{title}</b>
                          <small>{s}</small>
                        </li>
                      )
                    })}
                  </ul>
                ) : (
                  <p class="hint">{t('ov.nothingYet')}</p>
                )
              }
            </Loads>
          </Panel>
        </div>
      </div>
    </>
  )
}
