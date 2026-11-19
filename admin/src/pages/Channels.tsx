/**
 * Channels: the ways to an agent beyond the web panel (an e-mail mailbox, a Microsoft Teams bot, a Slack app). Each
 * shows the endpoints its provider calls, ready to copy, the environment variables its secrets come from (set or
 * missing, never their values), whether the channel is granted and its agent allows it, and a test that validates the
 * configuration without sending anything to anyone. The configuration is edited as JSON.
 */
import { useState } from 'preact/hooks'

import type { ChannelCheck, ChannelKind, ChannelView, ChannelsView, ConsoleApplication } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, copy } from '../format'
import { Empty, sparkle, toast } from '../magic'
import { appHref, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Seg, Switch, Tag } from '../ui'
import { Loads } from './common'
import { t, type TextKey } from '../i18n'

const KINDS = ['email', 'slack', 'teams'] as const satisfies readonly ChannelKind[]
const KIND_LABEL: Record<ChannelKind, TextKey> = { email: 'perms.channelEmail', teams: 'perms.channelTeams', slack: 'perms.channelSlack' }
const ENDPOINT: Record<string, TextKey> = {
  inbound: 'ch.ep.inbound',
  messaging: 'ch.ep.messaging',
  events: 'ch.ep.events',
  interactivity: 'ch.ep.interactivity',
}
const CHECK: Record<string, TextKey> = {
  config: 'ch.check.config',
  env: 'ch.check.env',
  granted: 'ch.check.granted',
  agent: 'ch.check.agent',
  imap: 'ch.check.imap',
  smtp: 'ch.check.smtp',
  graph: 'ch.check.graph',
  webhook: 'ch.check.webhook',
  teams: 'ch.check.teams',
  slack: 'ch.check.slack',
}

/** A starting configuration per kind: names of environment variables, never secrets. */
const TEMPLATES: Record<ChannelKind, Record<string, unknown>> = {
  email: {
    address: 'assistant@example.com',
    inbound: 'webhook',
    webhook_secret_env: 'INBOUND_MAIL_SECRET',
    reply: 'draft',
    transport: 'graph',
    graph: { tenant_id: '', client_id: '', client_secret_env: 'MAIL_APP_SECRET' },
    max_replies_per_sender_per_hour: 10,
  },
  slack: { signing_secret_env: 'SLACK_SIGNING_SECRET', bot_token_env: 'SLACK_BOT_TOKEN', team_id: '' },
  teams: { app_id: '', app_password_env: 'TEAMS_BOT_SECRET', tenant_id: '' },
}

function CopyRow({ label, value }: { label: string; value: string }) {
  return (
    <div class="copyrow">
      <span class="s">{label}</span>
      <code>{value}</code>
      <Btn sm kind="ghost" check onClick={async () => toast((await copy(value)) ? t('conn.copied') : t('common.copyByHand'), value, null)}>
        {t('conn.copy')}
      </Btn>
    </div>
  )
}

function Checks({ checks }: { checks: ChannelCheck[] }) {
  return (
    <ul class="checks">
      {checks.map((c) => (
        <li key={c.check} data-ok={c.ok ? 'true' : 'false'}>
          <Tag tone={c.ok ? 'ok' : 'bad'} dot>
            {CHECK[c.check] ? t(CHECK[c.check]!) : c.check}
          </Tag>
          {c.detail ? <span class="s">{c.detail}</span> : null}
        </li>
      ))}
    </ul>
  )
}

function Editor(p: { app: ConsoleApplication; agents: string[]; initial?: ChannelView; onSaved: () => Promise<void>; onClose: () => void }) {
  const [kind, setKind] = useState<ChannelKind>(p.initial?.kind ?? 'email')
  const [name, setName] = useState(p.initial?.name ?? '')
  const [agent, setAgent] = useState(p.initial?.agent ?? p.agents[0] ?? '')
  const [enabled, setEnabled] = useState(p.initial?.enabled ?? true)
  const [text, setText] = useState(JSON.stringify(p.initial?.config ?? TEMPLATES[p.initial?.kind ?? 'email'], null, 2))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    let config: Record<string, unknown>
    try {
      config = JSON.parse(text) as Record<string, unknown>
    } catch {
      setError(t('ch.badJson'))
      return
    }
    setSaving(true)
    setError('')
    try {
      await forApp(p.app.id).admin.channels.put(kind, name.trim(), { agent, enabled, config })
      sparkle(btn)
      toast(t('ch.saved'), `${t(KIND_LABEL[kind])}: ${name.trim()}`, null)
      await p.onSaved()
      p.onClose()
    } catch (err) {
      setError(describe(err))
    } finally {
      setSaving(false)
    }
  }
  return (
    <Panel title={p.initial ? t('ch.editTitle', { name: p.initial.name }) : t('ch.newTitle')}>
      <div class="grid2">
        <Field label={t('ch.kind')}>
          <Seg
            options={KINDS}
            value={kind}
            disabled={!!p.initial}
            labels={{ email: t('perms.channelEmail'), slack: t('perms.channelSlack'), teams: t('perms.channelTeams') }}
            onChange={(k) => {
              setKind(k)
              setText(JSON.stringify(TEMPLATES[k], null, 2))
            }}
          />
        </Field>
        <Field label={t('ch.name')} hint={t('ch.nameHint')} id="ch-name">
          <input id="ch-name" class="in mono" value={name} disabled={!!p.initial} onInput={(e) => setName(e.currentTarget.value)} />
        </Field>
        <Field label={t('ch.agent')} hint={t('ch.agentHint')} id="ch-agent">
          <select id="ch-agent" class="in" value={agent} onChange={(e) => setAgent(e.currentTarget.value)}>
            {p.agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t('ch.enabled')}>
          <Switch on={enabled} onChange={setEnabled} label={enabled ? t('ch.on') : t('ch.off')} />
        </Field>
      </div>
      <Field label={t('ch.config')} hint={t('ch.configHint')} id="ch-config">
        <textarea id="ch-config" class="in mono" spellcheck={false} style="min-height:240px;height:auto;padding:10px 12px" value={text} onInput={(e) => setText(e.currentTarget.value)} />
      </Field>
      {error ? <p class="hint bad">{error}</p> : null}
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <Btn kind="ghost" onClick={p.onClose}>
          {t('common.cancel')}
        </Btn>
        <Btn kind="pri" check busy={saving ? t('ch.saving') : false} disabled={!name.trim() || !agent} onClick={(e) => void save(e)}>
          {t('ch.save')}
        </Btn>
      </div>
    </Panel>
  )
}

function Channel({ app, ch, onChanged, onEdit }: { app: ConsoleApplication; ch: ChannelView; onChanged: () => Promise<void>; onEdit: () => void }) {
  const [checks, setChecks] = useState<ChannelCheck[] | null>(null)
  const [testing, setTesting] = useState(false)
  const [arming, setArming] = useState(false)
  const test = async () => {
    setTesting(true)
    try {
      const r = await forApp(app.id).admin.channels.test(ch.kind, ch.name)
      setChecks(r.checks)
      toast(r.ok ? t('ch.testOk') : t('ch.testFailed'), t('ch.nothingSent'), null)
      await onChanged()
    } catch (e) {
      toast(t('ch.testFailed'), describe(e), null)
    } finally {
      setTesting(false)
    }
  }
  const remove = async () => {
    if (!arming) {
      setArming(true)
      setTimeout(() => setArming(false), 3000)
      return
    }
    try {
      await forApp(app.id).admin.channels.delete(ch.kind, ch.name)
      toast(t('ch.deleted'), ch.name, null)
      await onChanged()
    } catch (e) {
      toast(t('ch.notDeleted'), describe(e), null)
    }
  }
  const status = !ch.valid ? (
    <Tag tone="bad" dot>
      {t('ch.invalid')}
    </Tag>
  ) : ch.status === 'error' ? (
    <Tag tone="bad" dot title={ch.lastError ?? undefined}>
      {t('ch.error')}
    </Tag>
  ) : ch.status === 'ok' ? (
    <Tag tone="ok" dot>
      {t('ch.ok')}
    </Tag>
  ) : (
    <Tag>{t('ch.untested')}</Tag>
  )
  return (
    <Panel
      title={
        <span style="display:inline-flex;gap:10px;align-items:center;flex-wrap:wrap">
          <Tag tone="vi">{t(KIND_LABEL[ch.kind])}</Tag>
          <code>{ch.name}</code>
          {status}
          {!ch.enabled ? <Tag>{t('ch.off')}</Tag> : null}
        </span>
      }
      actions={
        <>
          <Btn sm kind="ghost" onClick={onEdit}>
            {t('ch.edit')}
          </Btn>
          <Btn sm busy={testing ? t('ch.testing') : false} onClick={() => void test()}>
            {t('ch.test')}
          </Btn>
          <Btn sm kind="ghost danger" onClick={() => void remove()}>
            {arming ? t('ch.confirmDelete') : t('ch.delete')}
          </Btn>
        </>
      }
    >
      <div class="chmeta">
        <span>
          {t('ch.agentIs')} <a href={appHref(app.slug, 'agents', ch.agent)}>{ch.agent}</a>
        </span>
        <span class="s">{t('ch.conversations', { n: String(ch.conversations) })}</span>
        <span class="s">{ch.lastEventAt ? t('ch.lastEvent', { when: ago(ch.lastEventAt) }) : t('ch.noEvent')}</span>
      </div>
      {!ch.granted || !ch.agentAllows ? (
        <p class="hint warn">
          {!ch.granted ? t('ch.notGranted') : ''} {!ch.agentAllows ? t('ch.agentNotAllowing', { agent: ch.agent }) : ''}
        </p>
      ) : null}
      {ch.error ? <p class="hint bad">{ch.error}</p> : null}
      <h4 class="sub4">{t('ch.endpoints')}</h4>
      {Object.entries(ch.endpoints).map(([k, v]) => (
        <CopyRow key={k} label={ENDPOINT[k] ? t(ENDPOINT[k]!) : k} value={v} />
      ))}
      <h4 class="sub4">{t('ch.env')}</h4>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        {ch.env.map((e) => (
          <Tag key={e.name} tone={e.set ? 'ok' : 'warn'} dot title={e.set ? t('conn.envSet') : t('conn.envMissing')}>
            {e.name}
          </Tag>
        ))}
      </div>
      {checks ? (
        <>
          <h4 class="sub4">{t('ch.lastTest')}</h4>
          <Checks checks={checks} />
        </>
      ) : null}
    </Panel>
  )
}

export function Channels({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.channels'), app.name)
  const c = useConsole()
  const api = forApp(app.id)
  const load = useLoad(async () => ({ view: await api.admin.channels.list(), agents: (await api.admin.agents.list()).map((a) => a.name) }), [app.id])
  const [editing, setEditing] = useState<ChannelView | 'new' | null>(null)
  const reload = async () => {
    await load.reload()
    await c.reloadApps()
  }
  return (
    <>
      <Hero
        title={t('tab.channels')}
        sub={t('ch.sub')}
        actions={
          <Btn kind="pri" onClick={() => setEditing('new')}>
            {t('ch.add')}
          </Btn>
        }
      />
      <Loads load={load} what={t('what.channels')}>
        {({ view, agents }: { view: ChannelsView; agents: string[] }) => (
          <div class="grid" style="gap:16px">
            {!view.publicUrlSet ? <p class="hint warn">{t('ch.publicUrlMissing', { base: view.baseUrl })}</p> : null}
            {editing ? (
              <Editor app={app} agents={agents} initial={editing === 'new' ? undefined : editing} onSaved={reload} onClose={() => setEditing(null)} />
            ) : null}
            {view.channels.length === 0 && !editing ? (
              <Panel>
                <Empty k="ember" action={<Btn onClick={() => setEditing('new')}>{t('ch.add')}</Btn>}>
                  <b>{t('ch.empty')}</b>
                  <br />
                  {t('ch.emptyText')}
                </Empty>
              </Panel>
            ) : null}
            {view.channels.map((ch) => (
              <Channel key={`${ch.kind}/${ch.name}`} app={app} ch={ch} onChanged={reload} onEdit={() => setEditing(ch)} />
            ))}
          </div>
        )}
      </Loads>
    </>
  )
}
