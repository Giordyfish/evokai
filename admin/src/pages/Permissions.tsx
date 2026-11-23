/**
 * Permissions: what an application may use. The application decides at runtime among these, and its agents pick from
 * the same set. Each item follows the default or is overridden; changes are staged, then saved together.
 */
import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'

import type { ConsoleApplication, GrantChange, GrantItem, GrantValue, GrantView, ModelGrant } from '@client/core/types'
import { describe, forApp } from '../api'
import { modelName, pl } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import { appHref, setTitle, useConsole, useLoad } from '../state'
import { Btn, Hero, Icon, Panel, Seg, Switch, Tag } from '../ui'
import { Loads } from './common'
import { AskRulesPanel } from './AskRules'
import { Summary } from './summary'
import { CapabilityCredentials } from './CapabilityCredentials'
import { capDescription, capWiring } from '../capabilityText'
import { t, type TextKey } from '../i18n'

const GROUPS: Record<string, { title: TextKey; note: TextKey }> = {
  models: { title: 'perms.g.models', note: 'perms.g.modelsNote' },
  mcp: { title: 'perms.g.mcp', note: 'perms.g.mcpNote' },
  skills: { title: 'perms.g.skills', note: 'perms.g.skillsNote' },
  client: { title: 'perms.g.client', note: 'perms.g.clientNote' },
  clientCustom: { title: 'perms.g.clientCustom', note: 'perms.g.clientCustomNote' },
  voice: { title: 'perms.g.voice', note: 'perms.g.voiceNote' },
  storage: { title: 'perms.g.storage', note: 'perms.g.storageNote' },
  channels: { title: 'perms.g.channels', note: 'perms.g.channelsNote' },
  knowledge: { title: 'perms.g.knowledge', note: 'perms.g.knowledgeNote' },
  workflows: { title: 'perms.g.workflows', note: 'perms.g.workflowsNote' },
  code: { title: 'perms.g.code', note: 'perms.g.codeNote' },
  openai: { title: 'perms.g.openai', note: 'perms.g.openaiNote' },
  attachments: { title: 'perms.g.attachments', note: 'perms.g.attachmentsNote' },
  builtin: { title: 'perms.g.builtin', note: 'perms.g.builtinNote' },
  connections: { title: 'perms.g.connections', note: 'perms.g.connectionsNote' },
  capabilities: { title: 'perms.g.capabilities', note: 'perms.g.capabilitiesNote' },
  modules: { title: 'perms.g.modules', note: 'perms.g.modulesNote' },
}
const CHANNEL_LABEL: Record<string, TextKey> = {
  browser: 'perms.browserSessions',
  server: 'perms.serverRuns',
  a2a: 'perms.a2a',
  email: 'perms.channelEmail',
  teams: 'perms.channelTeams',
  slack: 'perms.channelSlack',
}
const FAMILY_LABEL: Record<string, TextKey> = {
  mail: 'perms.familyMail',
  calendar: 'perms.familyCalendar',
  files: 'perms.familyFiles',
}
const CLIENT: Record<string, TextKey> = {
  navigate: 'perms.c.navigate',
  chart: 'perms.c.chart',
  diagram: 'perms.c.diagram',
  ask: 'perms.c.ask',
  confirm: 'perms.c.confirm',
  memory: 'perms.c.memory',
}
const LEVELS = ['any', 'fallback', 'off'] as const
const levelLabels = (): Record<ModelGrant, string> => ({ any: t('perms.lv.any'), fallback: t('perms.lv.fallback'), off: t('perms.lv.off') })

const key = (x: { kind: string; name: string }) => `${x.kind}\u0000${x.name}`

/** Product names: the same in every language. */
const PROVIDER_LABEL: Record<string, string> = {
  microsoft365: 'Microsoft 365',
  google: 'Google Workspace',
  github: 'GitHub',
  linear: 'Linear',
  notion: 'Notion',
  slack: 'Slack',
}

function label(x: GrantItem): string {
  if (x.kind === 'model') return x.pattern ? x.name : modelName(x.name)
  if (x.kind === 'voice') return t(x.name === 'browser' ? 'perms.browserSpeech' : 'perms.serverVoice')
  if (x.kind === 'storage') return t(x.name === 'evokai' ? 'perms.inEvokai' : 'perms.external')
  if (x.kind === 'channel') return t(CHANNEL_LABEL[x.name] ?? 'perms.serverRuns')
  if (x.kind === 'connection') {
    const [provider, family] = x.name.split('/')
    if (family) return FAMILY_LABEL[family] ? t(FAMILY_LABEL[family]) : family
    return PROVIDER_LABEL[provider ?? ''] ?? provider ?? x.name
  }
  if (x.kind === 'knowledge' && x.name === '*') return t('perms.knowledgeAll')
  if (x.kind === 'workflow') return t(x.name === '*' ? 'perms.workflowsAll' : 'perms.workflowsAutonomous')
  if (x.kind === 'code') return t('perms.code')
  if (x.kind === 'openai') return t('perms.openaiConfirm')
  if (x.kind === 'attachments') return t('perms.attachments')
  if (x.kind === 'builtin') return t(`ag.builtin.${x.name}` as TextKey)
  return x.label ?? x.name
}

function sub(x: GrantItem, appName: string): string {
  switch (x.kind) {
    case 'model':
      return x.pattern
        ? t('perms.pattern')
        : `${x.name}${x.allowed === false ? `, ${t('perms.outsideAllow')}` : ''}${x.usedBy?.length ? `, ${t('perms.usedBy', { agents: x.usedBy.join(', ') })}` : ''}${x.rateSource === 'fallback' ? `, ${t('perm.rateFallback')}` : ''}`
    case 'mcp':
      return x.missing ? t('perms.missing') : `${x.auth}, ${x.listed ? pl(x.children?.length ?? 0, 'n.tool') : t('perms.toolsNotYet')}`
    case 'skill':
      return x.missing ? t('perms.missing') : (x.description ?? '')
    case 'client':
      if (CLIENT[x.name]) return t(CLIENT[x.name]!)
      if (x.name === 'session') return t('perms.c.session')
      if (x.pattern) return t('perms.pattern')
      return x.usedBy?.length ? t('perms.usedBy', { agents: x.usedBy.join(', ') }) : ''
    case 'voice':
      return x.name === 'browser' ? t('perms.browserVoiceSub') : `${t('perms.serverVoiceSub')}${x.enabled === false ? t('perms.voiceNotEnabled') : ''}`
    case 'stt':
      return `${t('perms.stt')}${t(x.configured ? 'perms.sttConfigured' : 'perms.sttNotConfigured')}`
    case 'tts':
      return [x.language, x.provider, x.region].filter(Boolean).join(', ') || t('perms.voiceWord')
    case 'storage':
      return t(x.name === 'evokai' ? 'perms.storageEvokai' : 'perms.storageExternal')
    case 'channel':
      return x.name === 'browser'
        ? t('perms.channelBrowser')
        : x.name === 'a2a'
          ? t('perms.channelA2a')
          : x.name === 'email' || x.name === 'teams' || x.name === 'slack'
            ? t('perms.channelMessagingSub')
            : t('perms.channelServer', { app: appName })
    case 'connection':
      return x.name.includes('/') ? t('perms.connectionFamilySub') : t('perms.connectionProviderSub')
    case 'knowledge':
      if (x.name === '*') return t('perms.knowledgeAllSub')
      return x.missing
        ? t('perms.missing')
        : [x.description, x.usedBy?.length ? t('perms.usedBy', { agents: x.usedBy.join(', ') }) : ''].filter(Boolean).join(', ')
    case 'workflow':
      return t(x.name === '*' ? 'perms.workflowsAllSub' : 'perms.workflowsAutonomousSub')
    case 'capability':
      return [
        capDescription(x),
        x.offered === false ? t('perms.capNotOffered') : '',
        x.installed === false
          ? t((x as GrantItem & { notInstalled?: string | null }).notInstalled === 'lite' ? 'perms.capNotInLite' : 'perms.capNotInstalled')
          : '',
        x.missingCredentials?.length ? t('perms.capMissingCredentials', { names: x.missingCredentials.join(', ') }) : '',
        (x as GrantItem & { actsAs?: string | null }).actsAs === 'person' ? t('perms.capActsAsPerson') : '',
        x.host === 'local' ? t('perms.capLocal') : x.host === 'workspace' ? t('perms.capWorkspace') : '',
        x.wiring ? t('perms.capWiring', { what: capWiring(x) }) : '',
        x.usedBy?.length ? t('perms.usedBy', { agents: x.usedBy.join(', ') }) : '',
      ]
        .filter(Boolean)
        .join(', ')
    case 'attachments':
      return x.usedBy?.length ? t('perms.usedBy', { agents: x.usedBy.join(', ') }) : ''
    case 'module':
      return [t('perms.moduleSub'), x.usedBy?.length ? t('perms.usedBy', { agents: x.usedBy.join(', ') }) : ''].filter(Boolean).join(', ')
    case 'code':
      return `${t('perms.codeSub', { region: x.region ?? '' })}${x.enabled === false ? t('perms.codeNotEnabled') : ''}${x.usedBy?.length ? `, ${t('perms.usedBy', { agents: x.usedBy.join(', ') })}` : ''}`
    default:
      return ''
  }
}

export function Permissions({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.perms'), app.name)
  const load = useLoad(() => forApp(app.id).admin.grants.get(), [app.id])
  return (
    <Loads load={load} what={t('what.permissions')}>
      {(view) => <Editor app={app} view={view} onSaved={(v) => load.set(v)} />}
    </Loads>
  )
}

function Editor({ app, view, onSaved }: { app: ConsoleApplication; view: GrantView; onSaved: (v: GrantView) => void }) {
  const c = useConsole()
  const [staged, setStaged] = useState(new Map<string, GrantChange>())
  const [groupId, setGroupId] = useState('models')
  const [filter, setFilter] = useState<'all' | 'overrides'>('all')
  const [saving, setSaving] = useState(false)
  const cur = view.groups.find((g) => g.id === groupId) ?? view.groups[0]!

  const value = (x: GrantItem): GrantValue | null => {
    const s = staged.get(key(x))
    if (!s) return x.value
    return s.value === null ? (x.kind === 'model' ? (x.default as GrantValue) : x.default) : s.value
  }
  const isSet = (x: GrantItem): boolean => {
    const s = staged.get(key(x))
    return s ? s.value !== null : x.override !== null
  }
  const on = (x: GrantItem) => value(x) !== false && value(x) !== 'off'
  const stage = (x: GrantItem, v: GrantValue | null) => {
    const next = new Map(staged)
    const unchanged = v === null ? x.override === null : v === x.override || (x.override === null && v === x.default)
    if (unchanged) next.delete(key(x))
    else next.set(key(x), { kind: x.kind, name: x.name, value: v })
    setStaged(next)
  }
  const all = (g: { items: GrantItem[] }): Array<[GrantItem | null, GrantItem]> =>
    g.items.flatMap((x): Array<[GrantItem | null, GrantItem]> => (x.children?.length ? x.children.map((k) => [x, k]) : [[null, x]]))
  const count = (g: { items: GrantItem[] }) => {
    const items = all(g)
    return `${items.filter(([p, x]) => on(x) && (!p || on(p))).length}/${items.length}`
  }
  const hasOverrides = (g: { items: GrantItem[] }) => g.items.some((x) => isSet(x) || (x.children ?? []).some(isSet))

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    if (!staged.size) return
    setSaving(true)
    try {
      const v = await forApp(app.id).admin.grants.change([...staged.values()])
      setStaged(new Map())
      onSaved(v)
      sparkle(btn)
      done(btn)
      toast(t('perms.saved'), t('perms.savedSub'))
      await c.reloadApps()
    } catch (err) {
      toast(t('perms.notSaved'), describe(err), null)
    } finally {
      setSaving(false)
    }
  }

  const prov = (x: GrantItem) =>
    isSet(x) ? (
      <>
        <Tag tone="vi">{t('perms.override')}</Tag>
        <button class="rv" title={t('perms.backToDefault')} aria-label={t('perms.backToDefault')} onClick={() => stage(x, null)}>
          <Icon name="undo" />
        </button>
      </>
    ) : (
      <Tag>{t('perms.default')}</Tag>
    )

  const control = (x: GrantItem, blocked = false): ComponentChildren =>
    x.kind === 'model' ? (
      <Seg
        options={LEVELS}
        labels={levelLabels()}
        value={(value(x) as ModelGrant | null) ?? 'any'}
        onChange={(v) => stage(x, v)}
      />
    ) : (
      <Switch on={on(x)} aria={label(x)} disabled={blocked} onChange={(v) => stage(x, v)} />
    )

  const visible = (x: GrantItem) => filter === 'all' || isSet(x) || (x.children ?? []).some(isSet)
  const items = cur.items.filter(visible)
  const body = items.length ? (
    items.map((x) =>
      x.children ? (
        <details class="acc" open={cur.id !== 'mcp' || cur.items.length < 3} key={key(x)}>
          <summary class={`item ${isSet(x) ? 'set' : ''}`}>
            <div class="t">
              <span class="cv">
                <Icon name="chev" />
              </span>
              <div>
                <b>{label(x)}</b>
                <small>
                  {sub(x, app.name)}
                  {x.children.length ? `, ${t('perms.ofOn', { on: x.children.filter(on).length, total: x.children.length })}` : ''}
                </small>
              </div>
            </div>
            <div class="prov">{prov(x)}</div>
            <div class="ctl" onClick={(e) => e.preventDefault()}>
              {control(x)}
            </div>
          </summary>
          {x.children.length ? (
            <div class="tools">
              {x.children.map((k) => (
                <div key={key(k)} class={`tl ${isSet(k) ? 'set' : ''} ${on(x) ? '' : 'blocked'}`}>
                  {k.kind === 'tool' ? (
                    <>
                      <code title={k.description || k.label}>{k.label}</code>
                      {k.mutating ? <Tag tone="gold">{t('perms.asks')}</Tag> : null}
                      {k.listed === false ? <Tag>{t('perms.notListed')}</Tag> : null}
                    </>
                  ) : (
                    <span style="flex:1;min-width:0">
                      <b style="font-weight:600">{k.label}</b>
                      <small style="display:block;color:var(--mu);font-size:12px">{sub(k, app.name)}</small>
                    </span>
                  )}
                  {isSet(k) ? (
                    <button class="rv" style="border:0;background:none;color:var(--vi);cursor:pointer;width:22px;height:22px" title={t('perms.backToDefault')} onClick={() => stage(k, null)}>
                      <Icon name="undo" />
                    </button>
                  ) : null}
                  <Switch on={on(k)} aria={k.label ?? k.name} disabled={!on(x)} onChange={(v) => stage(k, v)} />
                </div>
              ))}
            </div>
          ) : (
            <p class="hint" style="padding:4px 16px 12px 48px">
              {x.kind === 'mcp' ? (
                <>
                  {t('perms.toolsNotListed')}
                  <a href={appHref(app.slug, 'mcp', x.name)}>{t('perms.testServer')}</a>
                  {t('perms.toSeeThem')}
                </>
              ) : x.name === 'browser' ? (
                t('perms.noBrowserVoices')
              ) : (
                t('perms.noServerVoices')
              )}
            </p>
          )}
        </details>
      ) : (
        <div key={key(x)} class={`item ${isSet(x) ? 'set' : ''} ${x.kind === 'model' && x.allowed === false ? 'blocked' : ''}`}>
          <div class="t">
            <b>{label(x)}</b>
            <small>{sub(x, app.name)}</small>
          </div>
          <div class="prov">{prov(x)}</div>
          <div class="ctl">{control(x)}</div>
        </div>
      ),
    )
  ) : (
    <Empty>
      {filter === 'all' ? (
        cur.id === 'mcp' ? (
          <>
            <b>{t('perms.noMcp')}</b>
            <br />
            <a href={appHref(app.slug, 'mcp')}>{t('perms.addTools')}</a>
            {t('perms.toGrant')}
          </>
        ) : (
          <>
            <b>{t('perms.nothing')}</b>
          </>
        )
      ) : (
        <>
          <b>{t('perms.noOverrides')}</b>
          <br />
          {t('perms.followsDefault')}
        </>
      )}
    </Empty>
  )

  return (
    <>
      <Hero
        title={t('tab.perms')}
        sub={t('perms.sub', { app: app.name })}
        actions={
          <>
            <Seg
              options={['all', 'overrides'] as const}
              labels={{ all: t('perms.all'), overrides: t('perms.overridesOnly') }}
              value={filter}
              onChange={setFilter}
            />
            <Btn kind="pri" check busy={saving ? t('common.saving') : false} disabled={!staged.size} onClick={(e) => void save(e)}>
              {staged.size ? t('perms.saveChanges', { changes: pl(staged.size, 'n.change') }) : t('common.save')}
            </Btn>
          </>
        }
      />
      <div style="margin-bottom:16px">
        <Summary s={view.summary} overrides={view.overrides} />
      </div>
      <div class="perm">
        <nav class="panel glist" aria-label={t('perms.groups')}>
          {view.groups.map((g) => (
            <a
              key={g.id}
              role="button"
              tabIndex={0}
              aria-current={g.id === cur.id ? 'true' : undefined}
              onClick={() => setGroupId(g.id)}
              onKeyDown={(e) => e.key === 'Enter' && setGroupId(g.id)}
            >
              <span class={`d ${hasOverrides(g) ? '' : 'no'}`} title={hasOverrides(g) ? t('perms.hasOverrides') : ''} />
              {GROUPS[g.id] ? t(GROUPS[g.id]!.title) : g.id}
              <span class="c">{count(g)}</span>
            </a>
          ))}
        </nav>
        <Panel cls="flat">
          <div style="padding:18px 18px 10px">
            <h3 style="font:600 18px var(--disp)">{GROUPS[cur.id] ? t(GROUPS[cur.id]!.title) : cur.id}</h3>
            <p class="hint" style="margin-top:4px">
              {GROUPS[cur.id] ? t(GROUPS[cur.id]!.note) : ''}
              {cur.id === 'models'
                ? `${t('perms.deployment', { models: view.modelAllow.join(', ') })}${view.applicationModels.length ? t('perms.application', { models: view.applicationModels.join(', ') }) : ''}.`
                : ''}
            </p>
          </div>
          {body}
        </Panel>
      </div>
      <div style="margin-top:16px">
        <AskRulesPanel app={app} />
      </div>
      {cur.id === 'capabilities' ? (
        <div style="margin-top:16px">
          <CapabilityCredentials app={app} />
        </div>
      ) : null}
    </>
  )
}
