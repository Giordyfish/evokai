/** MCP servers: the application's own tools. A server that is down loses only its own tools. */
import { useEffect, useRef, useState } from 'preact/hooks'

import type { ConnectorKind, ConsoleApplication, GrantView, McpServerInfo } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, ms, pl } from '../format'
import { toolGranted } from '../grants'
import { done, Empty, Motes, oneshot, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Icon, Panel, Seg, StatusTag, Switch, Tag } from '../ui'
import { Loads } from './common'
import { t } from '../i18n'

export function Mcp({ app, id }: { app: ConsoleApplication; id: string | null }) {
  setTitle(id ? (id === 'new' ? t('mcp.new') : id) : t('tab.mcp'), app.name)
  const api = forApp(app.id)
  const load = useLoad(async () => {
    const [servers, grants] = await Promise.all([api.admin.mcpServers.list(), api.admin.grants.get()])
    return { servers, grants }
  }, [app.id])
  if (id)
    return (
      <Loads load={load} what={id === 'new' ? t('what.newServer') : id}>
        {(d) => {
          const s = d.servers.find((x) => x.name === id)
          if (!s && id !== 'new')
            return (
              <Panel>
                <Empty>
                  <b>{t('mcp.noServer', { name: id })}</b>
                  <br />
                  <a href={appHref(app.slug, 'mcp')}>{t('mcp.back')}</a>
                </Empty>
              </Panel>
            )
          return <Detail key={id} app={app} server={s ?? null} grants={d.grants} reload={load.reload} />
        }}
      </Loads>
    )
  const add = (
    <Btn kind="pri" href={appHref(app.slug, 'mcp', 'new')}>
      {t('mcp.add')}
    </Btn>
  )
  return (
    <>
      <Hero title={t('tab.mcp')} sub={t('mcp.sub')} actions={add} />
      <Loads load={load} what={t('what.mcpServers')}>
        {({ servers }) =>
          servers.length ? (
            <div class="grid g2">
              {servers.map((s) => (
                <article class="panel appc" key={s.name} tabIndex={0} onClick={() => go(appHref(app.slug, 'mcp', s.name))}>
                  <div class="row">
                    <span class="av all" style="border-radius:50%">
                      <Icon name="mcp" style="width:16px;height:16px" />
                    </span>
                    <div style="min-width:0">
                      <h2 style="font-size:19px">{s.name}</h2>
                      <code style="color:var(--mu);font-size:12px">{s.connector ? t('mcp.readyConnector', { name: s.connector }) : s.url.replace(/^https?:\/\//, '')}</code>
                    </div>
                    <span style="margin-left:auto">
                      <StatusTag status={s.status} />
                    </span>
                  </div>
                  <div class="caps">
                    <Tag tone="vi">{s.auth}</Tag>
                    {s.tools ? <Tag>{pl(s.tools.length, 'n.tool')}</Tag> : <Tag>{t('mcp.toolsNotListed')}</Tag>}
                    {s.tools ? <Tag tone="gold">{t('mcp.askFirst', { n: s.tools.filter((x) => x.mutating).length })}</Tag> : null}
                    {s.latencyMs != null ? <Tag>{ms(s.latencyMs)}</Tag> : null}
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <Panel>
              <Empty action={add}>
                <b>{t('mcp.empty')}</b>
                <br />
                {t('mcp.emptyText')}
              </Empty>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}

function Detail({ app, server, grants, reload }: { app: ConsoleApplication; server: McpServerInfo | null; grants: GrantView; reload: () => Promise<void> }) {
  const c = useConsole()
  const api = forApp(app.id)
  const isNew = !server
  const [name, setName] = useState(server?.name ?? '')
  const [url, setUrl] = useState(server?.url ?? 'https://')
  const [auth, setAuth] = useState<McpServerInfo['auth']>(server?.auth ?? 'forward_user')
  const [timeout, setTimeoutS] = useState(server?.timeout_seconds ?? 60)
  const [prefix, setPrefix] = useState(server?.tool_prefix ?? '')
  const [secret, setSecret] = useState('')
  const [worker, setWorker] = useState(server?.worker ?? false)
  const [kind, setKind] = useState<'endpoint' | 'connector'>(server?.connector ? 'connector' : 'endpoint')
  const [connector, setConnector] = useState(server?.connector ?? 'sql')
  const [configText, setConfigText] = useState<string>(server?.connector ? JSON.stringify(server.config ?? {}, null, 2) : (CONNECTOR_EXAMPLES.sql ?? '{}'))
  const [kinds, setKinds] = useState<ConnectorKind[]>([])
  useEffect(() => {
    api.admin.mcpServers
      .connectors()
      .then(setKinds)
      .catch(() => setKinds([]))
  }, [api])
  const [credential, setCredential] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const res = useRef<HTMLDivElement>(null)

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setSaving(true)
    try {
      if (kind === 'connector') {
        let config: Record<string, unknown>
        try {
          config = JSON.parse(configText) as Record<string, unknown>
        } catch {
          throw new Error(t('mcp.configInvalid'))
        }
        await api.admin.mcpServers.put(name.trim(), { connector, config, timeout_seconds: Number(timeout), tool_prefix: prefix })
      } else
        await api.admin.mcpServers.put(name.trim(), {
          url: url.trim(),
          auth,
          timeout_seconds: Number(timeout),
          tool_prefix: prefix,
          worker: auth === 'secret' && worker,
          ...(secret ? { secret } : {}),
        })
      setSecret('')
      sparkle(btn)
      done(btn)
      toast(t('mcp.saved'), name)
      await c.reloadApps()
      if (isNew) go(appHref(app.slug, 'mcp', name.trim()))
      else await reload()
    } catch (err) {
      toast(t('mcp.notSaved'), describe(err), null)
    } finally {
      setSaving(false)
    }
  }
  const test = async () => {
    if (!server) return
    setTesting(true)
    setResult(null)
    try {
      const r = await api.admin.mcpServers.test(server.name, credential || undefined)
      if (r.ok) {
        const text = t('mcp.answered', { tools: pl(r.tools?.length ?? 0, 'n.tool'), time: ms(r.latencyMs) })
        setResult({ ok: true, text })
        setTimeout(() => oneshot(res.current?.firstElementChild, 'sparkles', 100, 2600), 30)
        toast(t('mcp.works'), text, null)
        await reload()
      } else setResult({ ok: false, text: r.error ?? t('mcp.noAnswer') })
    } catch (err) {
      setResult({ ok: false, text: describe(err) })
    } finally {
      setTesting(false)
    }
  }
  const remove = async () => {
    if (!server || !confirm(t('mcp.confirmRemove', { name: server.name }))) return
    try {
      await api.admin.mcpServers.delete(server.name)
      toast(t('mcp.removed'), server.name, null)
      await c.reloadApps()
      go(appHref(app.slug, 'mcp'))
    } catch (err) {
      toast(t('mcp.notRemoved'), describe(err), null)
    }
  }
  const tools = server?.tools ?? []
  return (
    <>
      <Hero
        title={isNew ? t('mcp.new') : server.name}
        sub={
          isNew
            ? t(kind === 'connector' ? 'mcp.subConnector' : 'mcp.subEndpoint')
            : `${server.tools ? pl(tools.length, 'n.tool') : t('mcp.toolsNotYet')}, ${
                server.connector
                  ? t('mcp.theConnector', { name: server.connector })
                  : t(server.auth === 'forward_user' ? 'mcp.asUser' : server.auth === 'secret' ? 'mcp.withSecret' : 'mcp.noCreds')
              }.`
        }
        actions={
          <>
            {!isNew ? (
              <Btn busy={testing ? t('mcp.testing') : false} onClick={() => void test()}>
                {t('mcp.test')}
              </Btn>
            ) : null}
            <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {isNew ? t('mcp.add') : t('common.save')}
            </Btn>
          </>
        }
      />
      <div class="grid g-21">
        <Panel title={t('mcp.connection')}>
          {isNew ? (
            <Field label={t('common.name')} id="m-name" hint={t('mcp.nameHint')}>
              <input id="m-name" class="in mono" value={name} onInput={(e) => setName(e.currentTarget.value)} />
            </Field>
          ) : null}
          {isNew ? (
            <Field label={t('mcp.kind')} hint={t('mcp.kindHint')}>
              <Seg
                options={['endpoint', 'connector'] as const}
                labels={{ endpoint: t('mcp.kindEndpoint'), connector: t('mcp.kindConnector') }}
                value={kind}
                onChange={setKind}
              />
            </Field>
          ) : null}
          {kind === 'connector' ? (
            <>
              <Field label={t('mcp.connector')} id="m-kind">
                <select
                  id="m-kind"
                  class="in"
                  value={connector}
                  disabled={!isNew}
                  onChange={(e) => {
                    const k = e.currentTarget.value
                    setConnector(k)
                    setConfigText(CONNECTOR_EXAMPLES[k] ?? '{}')
                  }}
                >
                  {(kinds.length ? kinds : Object.keys(CONNECTOR_EXAMPLES).map((k) => ({ kind: k, title: k, schema: {} }))).map((k) => (
                    <option key={k.kind} value={k.kind}>
                      {k.title}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label={t('mcp.config')}
                id="m-config"
                hint={
                  <>
                    {t('mcp.configHint1')}
                    <code>_env</code>
                    {t('mcp.configHint2')}
                    <code>writes</code>
                    {t('mcp.configHint3')}
                    {Object.keys(((kinds.find((k) => k.kind === connector)?.schema ?? {}) as { properties?: object }).properties ?? {}).join(', ') || t('mcp.seeDocs')}
                  </>
                }
              >
                <textarea id="m-config" class="in mono" spellcheck={false} style="min-height:220px;height:auto;padding:10px 12px" value={configText} onInput={(e) => setConfigText(e.currentTarget.value)} />
              </Field>
            </>
          ) : (
            <>
              <Field label={t('mcp.endpoint')} id="m-url" hint="Streamable HTTP">
                <input id="m-url" class="in mono" value={url} onInput={(e) => setUrl(e.currentTarget.value)} />
              </Field>
              <Field label={t('mcp.auth')}>
                <Seg options={['forward_user', 'secret', 'none'] as const} value={auth} onChange={setAuth} />
              </Field>
            </>
          )}
          <div class="grid g2">
            <Field label={t('mcp.timeout')}>
              <div class="affix">
                <input class="in num" type="number" min={1} max={300} value={timeout} onInput={(e) => setTimeoutS(Number(e.currentTarget.value))} />
                <span>{t('mcp.seconds')}</span>
              </div>
            </Field>
            <Field label={t('mcp.prefix')} hint={t('mcp.prefixHint')}>
              <input class="in mono" value={prefix} onInput={(e) => setPrefix(e.currentTarget.value)} />
            </Field>
          </div>
          {auth === 'secret' && kind === 'endpoint' ? (
            <Field label={t('mcp.secret')} hint={server?.hasSecret ? t('mcp.secretKept') : t('mcp.secretNew')}>
              <input class="in mono" type="password" autocomplete="off" value={secret} onInput={(e) => setSecret(e.currentTarget.value)} />
            </Field>
          ) : null}
          {auth === 'secret' && kind === 'endpoint' ? (
            <Field label={t('mcp.worker')} hint={t('mcp.workerHint')}>
              <Switch on={worker} aria={t('mcp.worker')} onChange={setWorker} />
            </Field>
          ) : null}
          {!isNew ? (
            <Btn sm kind="ghost danger" onClick={() => void remove()}>
              {t('mcp.remove')}
            </Btn>
          ) : null}
        </Panel>
        {!isNew ? (
          <Panel title={t('mcp.health')}>
            <div ref={res} style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              {testing ? (
                <span class="toolchip">
                  <Motes />
                  tools/list
                </span>
              ) : result ? (
                <>
                  <StatusTag status={result.ok ? 'ok' : 'down'} />
                  <span class="hint" style="margin:0">
                    {result.text}
                  </span>
                </>
              ) : (
                <>
                  <StatusTag status={server.status} />
                  <span class="hint" style="margin:0">
                    {server.latencyMs != null ? `${ms(server.latencyMs)}, ` : ''}
                    {server.checkedAt ? t('mcp.checked', { when: ago(server.checkedAt) }) : t('mcp.neverChecked')}
                    {server.lastError ? `, ${server.lastError}` : ''}
                  </span>
                </>
              )}
            </div>
            {server.auth === 'forward_user' ? (
              <Field label={t('mcp.userToken')} hint={t('mcp.userTokenHint')}>
                <input class="in mono" type="password" autocomplete="off" value={credential} onInput={(e) => setCredential(e.currentTarget.value)} />
              </Field>
            ) : null}
            <p class="hint">{t('mcp.downNote')}</p>
          </Panel>
        ) : null}
      </div>
      {!isNew ? (
        <div style="margin-top:16px">
          <Panel title={t('mcp.tools')} actions={<Btn sm href={appHref(app.slug, 'perms')}>{t('mcp.editGrants')}</Btn>}>
            {tools.length ? (
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('mcp.tool')}</th>
                      <th>{t('mcp.kind')}</th>
                      <th>{t('mcp.for', { app: app.name })}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tools.map((x) => (
                      <tr key={x.name}>
                        <td>
                          <code>{x.name}</code>
                          {x.description ? <div class="s">{x.description}</div> : null}
                        </td>
                        <td>{x.mutating ? <Tag tone="gold">{t('mcp.changesData')}</Tag> : <Tag>{t('mcp.reads')}</Tag>}</td>
                        <td>
                          {toolGranted(grants, server.name, x.name) ? (
                            <Tag tone="ok" dot>
                              {t('mcp.granted')}
                            </Tag>
                          ) : (
                            <Tag tone="bad" dot>
                              {t('mcp.revoked')}
                            </Tag>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p class="hint">{t('mcp.notListed')}</p>
            )}
          </Panel>
        </div>
      ) : null}
    </>
  )
}

/** A starting configuration per connector (docs/connectors has every key). */
const CONNECTOR_EXAMPLES: Record<string, string> = {
  sql: JSON.stringify({ dsn_env: 'SHOP_DATABASE_URL', tables: ['public.orders', 'public.books'], hidden_columns: { 'public.orders': ['email'] }, row_limit: 100 }, null, 2),
  openapi: JSON.stringify({ spec_url: 'https://api.example.com/openapi.json', operations: ['getOrder', 'listOrders'], auth: { header: 'Authorization', prefix: 'Bearer ', secret_env: 'SHOP_API_KEY' } }, null, 2),
  google_drive: JSON.stringify({ folders: ['<folder id>'], client_id_env: 'GOOGLE_CLIENT_ID', client_secret_env: 'GOOGLE_CLIENT_SECRET', refresh_token_env: 'GOOGLE_REFRESH_TOKEN' }, null, 2),
  microsoft_files: JSON.stringify({ tenant_id: '<tenant id>', client_id: '<app id>', client_secret_env: 'GRAPH_CLIENT_SECRET', site_id: '<site id>', folders: ['/Policies'] }, null, 2),
  imap: JSON.stringify({ host: 'imap.example.com', username_env: 'MAILBOX_USER', password_env: 'MAILBOX_PASSWORD', folders: ['INBOX'] }, null, 2),
}
