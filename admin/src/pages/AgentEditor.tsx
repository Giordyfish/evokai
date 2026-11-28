/**
 * The agent editor: instructions, model chain, tools, skills, speech, output and versions, with the live summary
 * card. Choices come from what the application is granted; what is not granted is shown greyed with a link to
 * Permissions, and the server refuses a definition that references it.
 */
import type { ComponentChildren } from 'preact'
import { useEffect, useState } from 'preact/hooks'

import type { AgentDef, AgentVersion, Application, ConsoleApplication, GrantView, KnowledgeBase, McpServerInfo, ModuleRef, PromptVersion, SkillInfo } from '@client/core/types'
import { adminJson, describe, forApp } from '../api'
import { type Fit, fitBadge, fitReasons } from '../fit'
import { ago, day, modelName } from '../format'
import { capabilityItems, capabilityUsable, channelGranted, builtinGranted, clientGranted, codeGranted, grantedModels, attachmentsGranted, openaiConfirmGranted, knowledgeGranted, modelLevel, normalizeModel, serverGranted, skillGranted, toolGranted } from '../grants'
import { done, Fx, HAT, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { rulesOf, toRow, type RuleRow } from '../askRules'
import { blankTool, type ClientTool, type ClientToolRow, fromRow, MAX_TOOLS, toolProblems, toRow as toolRow } from '../clientTools'
import { Btn, Field, Hero, KV, Panel, StatusTag, Switch, Tag } from '../ui'
import { Loads } from './common'
import { RulesEditor } from './AskRules'
import { ParamsForm } from './ModuleParams'
import { capDescription } from '../capabilityText'
import { hasLegacyLabels, isLegacy, withSyntax } from '../templates'
import { t, tn, type TextKey } from '../i18n'

const BUILTIN_KINDS = ['web_search', 'web_fetch', 'code_execution', 'image_generation', 'file_search', 'mcp_server', 'memory']
const STORING_KINDS = ['file_search', 'memory']

type Sec = 'ins' | 'mod' | 'model' | 'tools' | 'skills' | 'kb' | 'voice' | 'out' | 'ver'
const SECTION_FIELDS: Record<Sec, Array<keyof AgentDef>> = {
  ins: ['name', 'title', 'instructions', 'channels', 'a2a'],
  mod: ['modules'],
  model: ['model', 'fallbacks', 'models', 'max_tool_calls_per_turn', 'max_output_tokens', 'model_settings', 'temperature', 'prompt_cache', 'show_reasoning'],
  tools: ['mcp_servers', 'client_tools', 'ask_rules', 'attachments', 'show_tool_args'],
  skills: ['skills', 'preload_skills'],
  kb: ['knowledge', 'code', 'capabilities', 'subagents'],
  voice: ['voice'],
  out: ['output_schema'],
  ver: [],
}
const ATTACHMENT_KINDS = ['png', 'jpeg', 'webp', 'gif', 'pdf', 'docx', 'pptx', 'txt', 'md', 'csv'] as const
type AttachmentKind = (typeof ATTACHMENT_KINDS)[number]
const CLIENT_TOOLS: Array<[string, TextKey]> = [
  ['navigate', 'ag.ct.navigate'],
  ['chart', 'ag.ct.chart'],
  ['diagram', 'ag.ct.diagram'],
  ['ask', 'ag.ct.ask'],
  ['memory', 'ag.ct.memory'],
]

const blank = (): AgentDef => ({
  name: '',
  title: '',
  instructions: '',
  model: '',
  fallbacks: [],
  models: [],
  mcp_servers: [],
  skills: null,
  channels: ['browser'],
  max_tool_calls_per_turn: 6,
})

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/** What changed between two definitions, in words. */
function changes(prev: AgentDef | undefined, next: AgentDef): string {
  if (!prev) return t('ag.firstVersion')
  const keys = Object.keys({ ...prev, ...next }) as Array<keyof AgentDef>
  const changed = keys.filter((k) => !same(prev[k], next[k])).map((k) => String(k).replace(/_/g, ' '))
  return changed.length ? t('ag.changed', { fields: changed.join(', ') }) : t('ag.same')
}

interface Data {
  versions: AgentVersion[]
  grants: GrantView
  servers: McpServerInfo[]
  skills: SkillInfo[]
  bases: KnowledgeBase[]
  keys: Map<string, string>
  prompts: PromptVersion[]
  /** The application, for what an agent inherits (its output cap). */
  application: Application
}

export function AgentEditor({ app, name }: { app: ConsoleApplication; name: string }) {
  const isNew = name === 'new'
  setTitle(isNew ? t('ag.new') : name, t('tab.agents'), app.name)
  const api = forApp(app.id)
  const load = useLoad<Data>(async () => {
    const [versions, grants, servers, skills, keys, bases, prompts, application] = await Promise.all([
      isNew ? Promise.resolve([] as AgentVersion[]) : api.admin.agents.versions(name),
      api.admin.grants.get(),
      api.admin.mcpServers.list(),
      api.admin.skills.list(),
      api.admin.keys.list(),
      api.admin.knowledge.list().then((l) => l.items),
      api.admin.prompts.list(),
      api.admin.application.get(),
    ])
    return { versions, grants, servers, skills, bases, prompts, application, keys: new Map(keys.map((k) => [k.id, k.prefix])) }
  }, [app.id, name])
  return (
    <Loads load={load} what={isNew ? t('what.newAgent') : name}>
      {(d) => <Editor key={`${name}${d.versions[0]?.version ?? ''}`} app={app} data={d} isNew={isNew} reload={load.reload} />}
    </Loads>
  )
}

interface Suitability {
  complexity: { tier: 'low' | 'medium' | 'high'; factors: Record<string, number> }
  estimate: Fit
}

/** The primary model's suitability for this agent (as saved): an estimate, or the measurement, with its reasons. */
function ModelFit({ app, agent, model }: { app: ConsoleApplication; agent: string; model: string }) {
  const [fit, setFit] = useState<Suitability | null>(null)
  useEffect(() => {
    let live = true
    setFit(null)
    const path = `/v1/admin/agents/${encodeURIComponent(agent)}/suitability?model=${encodeURIComponent(model)}`
    adminJson<Suitability>(app.id, 'GET', path)
      .then((r) => live && setFit(r))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [app.id, agent, model])
  if (!fit) return null
  const b = fitBadge(fit.estimate)
  const tier = { low: t('fit.tier.low'), medium: t('fit.tier.medium'), high: t('fit.tier.high') }[fit.complexity.tier]
  return (
    <div class="s fit" style="margin-top:4px">
      <Tag tone={b.tone} dot>
        {b.text}
      </Tag>{' '}
      {fitReasons(fit.estimate).join(' · ')}
      {fit.estimate.basis === 'estimate' ? <div class="s">{t('fit.forTier', { tier })}</div> : null}
    </div>
  )
}

function Editor({ app, data, isNew, reload }: { app: ConsoleApplication; data: Data; isNew: boolean; reload: () => Promise<void> }) {
  const c = useConsole()
  const api = forApp(app.id)
  const live = data.versions.find((v) => v.active) ?? data.versions[0]
  const base: AgentDef = live ? { ...blank(), ...live.definition } : blank()
  const [draft, setDraft] = useState<AgentDef>(base)
  const [sec, setSec] = useState<Sec>('ins')
  const [saving, setSaving] = useState(false)
  const [problems, setProblems] = useState<string | null>(null)
  const [schemaText, setSchemaText] = useState(draft.output_schema ? JSON.stringify(draft.output_schema, null, 2) : '')
  const [settingsText, setSettingsText] = useState(JSON.stringify(draft.model_settings ?? {}, null, 2))
  const [askRows, setAskRows] = useState<RuleRow[]>((draft.ask_rules ?? []).map(toRow))
  const [toolRows, setToolRows] = useState<ClientToolRow[]>(((draft.client_tools?.custom ?? []) as ClientTool[]).map(toolRow))
  const toolIssues = toolProblems(toolRows)
  /** The tools rules can name (as last listed), and whether each changes data. */
  const ruleTools = data.servers.flatMap((s) =>
    (s.tools ?? []).map((x) => ({ name: `${s.tool_prefix ?? ''}${x.name}`, mutating: !!x.destructive || x.readOnly === false })),
  )
  const g = data.grants
  const perm = appHref(app.slug, 'perms')
  const set = (patch: Partial<AgentDef>) => setDraft({ ...draft, ...patch })
  const server = (draft.channels ?? ['browser']).includes('server')
  const browser = (draft.channels ?? ['browser']).includes('browser')
  type ChannelName = NonNullable<typeof draft.channels>[number]
  const MESSAGING: ChannelName[] = ['email', 'teams', 'slack']
  /** Turn one channel on or off, keeping the others (an agent keeps at least one channel: the browser). */
  const toggleChannel = (name: ChannelName, v: boolean) => {
    const now = new Set<ChannelName>(draft.channels ?? ['browser'])
    if (v) now.add(name)
    else now.delete(name)
    const order: ChannelName[] = ['browser', 'server', ...MESSAGING]
    const next = order.filter((x) => now.has(x))
    set({ channels: next.length ? next : ['browser'] })
  }
  const vnum = data.versions.length
  const liveIndex = live ? data.versions.length - data.versions.indexOf(live) : 0
  const dirty = (s: Sec) => SECTION_FIELDS[s].some((k) => !same(draft[k], base[k]))
  const secs: Array<[Sec, string]> = (
    ['ins', 'mod', 'model', 'tools', 'skills', 'kb', ...(browser ? ['voice'] : []), ...(server ? ['out'] : []), ...(isNew ? [] : ['ver'])] as Sec[]
  ).map((k) => [k, t(`ag.sec.${k}`)])
  const unsaved = secs.filter(([k]) => dirty(k)).length
  const locked = (
    <a class="tag" href={perm}>
      {t('ag.notGranted')}
    </a>
  )

  /** What the agent gets without a cap of its own: the application's, else the deployment's. */
  const inheritedCap = data.application.max_output_tokens ?? data.application.maxOutputTokensDefault ?? ''
  const chain = [draft.model, ...(draft.fallbacks ?? [])].filter(Boolean)
  const granted = grantedModels(g)
  const toolsGranted = (draft.mcp_servers ?? []).reduce((n, s) => {
    const info = data.servers.find((x) => x.name === s)
    return n + (info?.tools ?? []).filter((x) => toolGranted(g, s, x.name) && (!server || browser || !x.mutating)).length
  }, 0)
  const skillCount =
    draft.skills == null ? data.skills.filter((s) => skillGranted(g, s.name)).length : draft.skills.filter((s) => skillGranted(g, s)).length

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setProblems(null)
    let body: AgentDef = draft
    try {
      body = withSyntax(
        {
          ...body,
          output_schema: server && schemaText.trim() ? JSON.parse(schemaText) : null,
          model_settings: settingsText.trim() ? JSON.parse(settingsText) : {},
        },
        base,
        isNew,
      )
    } catch {
      setProblems(t('ag.badJson'))
      return
    }
    if (toolIssues.some((x) => x !== null) || toolRows.length > MAX_TOOLS) {
      setProblems(t('ag.customFix'))
      setSec('tools')
      return
    }
    body = { ...body, client_tools: { ...(body.client_tools ?? {}), custom: toolRows.map(fromRow) } }
    if (!body.name || !/^[A-Za-z0-9_-]+$/.test(body.name)) {
      setProblems(t('ag.badName'))
      setSec('ins')
      return
    }
    setSaving(true)
    try {
      const r = await api.admin.agents.put(body)
      sparkle(btn)
      done(btn)
      toast(r.created ? t('ag.savedAs', { n: vnum + 1 }) : t('ag.nothingNew'), t(r.created ? 'ag.newConvos' : 'ag.liveSame'))
      await c.reloadApps()
      if (isNew) go(appHref(app.slug, 'agents', body.name))
      else await reload()
    } catch (err) {
      setProblems(describe(err))
    } finally {
      setSaving(false)
    }
  }

  const restore = async (v: AgentVersion) => {
    try {
      await api.admin.agents.put(v.definition)
      toast(t('ag.restored', { version: v.version.slice(0, 7) }), t('ag.newConvos'))
      await reload()
    } catch (err) {
      toast(t('ag.notRestored'), describe(err), null)
    }
  }

  const retire = async () => {
    if (!confirm(t('ag.confirmRetire', { name: base.name }))) return
    try {
      await api.admin.agents.delete(base.name)
      toast(t('ag.retired'), base.name, null)
      await c.reloadApps()
      go(appHref(app.slug, 'agents'))
    } catch (err) {
      toast(t('ag.notRetired'), describe(err), null)
    }
  }

  const row = (title: ComponentChildren, sub: ComponentChildren, control: ComponentChildren, blocked = false) => (
    <div class={`opt ${blocked ? 'blocked' : ''}`}>
      <div class="t">
        <b>{title}</b>
        <small>{sub}</small>
      </div>
      {control}
    </div>
  )

  const move = (i: number, d: number) => {
    const list = [...chain]
    const j = i + d
    if (j < 0 || j >= list.length) return
    ;[list[i], list[j]] = [list[j]!, list[i]!]
    set({ model: list[0]!, fallbacks: list.slice(1) })
  }
  const remove = (i: number) => {
    const list = chain.filter((_, k) => k !== i)
    set({ model: list[0] ?? '', fallbacks: list.slice(1) })
  }

  // Prompt modules: the latest version of each prompt (or the version a module pins) and its parameters.
  const promptAt = (name: string, version?: number | null): PromptVersion | undefined => {
    const all = data.prompts.filter((x) => x.name === name)
    return version ? all.find((x) => x.version === version) : all.reduce<PromptVersion | undefined>((m, x) => (!m || x.version > m.version ? x : m), undefined)
  }
  const modules: ModuleRef[] = draft.modules ?? []
  const promptNames = [...new Set(data.prompts.map((x) => x.name))].filter((n) => !modules.some((m) => m.name === n)).sort()
  const [adding, setAdding] = useState('')
  const setModule = (i: number, patch: Partial<ModuleRef>) => set({ modules: modules.map((m, k) => (k === i ? { ...m, ...patch } : m)) })
  const moveModule = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= modules.length) return
    const next = [...modules]
    ;[next[i], next[j]] = [next[j]!, next[i]!]
    set({ modules: next })
  }

  const panes: Record<Sec, ComponentChildren> = {
    mod: (
      <Panel title={t('ag.sec.mod')}>
        <p class="hint">{t('ag.modHint')}</p>
        {modules.length ? null : <p class="hint">{t('ag.modNone')}</p>}
        {modules.map((m, i) => {
          const prompt = promptAt(m.name, m.version)
          return (
            <div key={m.name} class="opt" style="display:block">
              <div class="flags" style="justify-content:space-between">
                <span>
                  <b>
                    <code>{m.name}</code>
                  </b>{' '}
                  {prompt ? <small class="hint">v{prompt.version}{prompt.description ? `, ${prompt.description}` : ''}</small> : <Tag tone="bad">{t('ag.modMissing')}</Tag>}
                </span>
                <span class="flags">
                  <Switch label={t('ag.modOn')} on={m.on !== false} onChange={(v) => setModule(i, { on: v })} />
                  <Switch label={t('ag.modSession')} on={m.session === true} onChange={(v) => setModule(i, { session: v })} />
                  <select
                    class="in"
                    style="width:auto"
                    value={m.version ? String(m.version) : ''}
                    aria-label={t('ag.modVersion')}
                    onChange={(e) => setModule(i, { version: e.currentTarget.value ? Number(e.currentTarget.value) : null })}
                  >
                    <option value="">{t('ag.modLatest')}</option>
                    {data.prompts
                      .filter((x) => x.name === m.name)
                      .map((x) => (
                        <option key={x.version} value={x.version}>
                          v{x.version}
                        </option>
                      ))}
                  </select>
                  <Btn sm kind="ghost" label={t('prm.up')} onClick={() => moveModule(i, -1)}>
                    ↑
                  </Btn>
                  <Btn sm kind="ghost" label={t('prm.down')} onClick={() => moveModule(i, 1)}>
                    ↓
                  </Btn>
                  <Btn sm kind="ghost danger" onClick={() => set({ modules: modules.filter((_, k) => k !== i) })}>
                    {t('ag.modRemove')}
                  </Btn>
                </span>
              </div>
              {prompt?.parameters ? (
                <ParamsForm schema={prompt.parameters} values={(m.params ?? {}) as Record<string, unknown>} onChange={(v) => setModule(i, { params: v })} idPrefix={`ag-mod-${i}`} />
              ) : null}
            </div>
          )
        })}
        <div class="flags" style="margin-top:12px">
          <select class="in" style="width:auto" value={adding} onChange={(e) => setAdding(e.currentTarget.value)} aria-label={t('ag.modPick')}>
            <option value="">{t('ag.modPick')}</option>
            {promptNames.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
          <Btn
            sm
            disabled={!adding}
            onClick={() => {
              set({ modules: [...modules, { name: adding, on: true, session: false, params: {} }] })
              setAdding('')
            }}
          >
            {t('ag.modAdd')}
          </Btn>
          <a class="hint" href={appHref(app.slug, 'prompts')}>
            {t('ag.modManage')}
          </a>
        </div>
      </Panel>
    ),
    ins: (
      <Panel title={t('ag.sec.ins')}>
        <div class="grid g2" style="gap:0 14px">
          <Field label={t('common.name')} id="ag-name" hint={isNew ? t('ag.nameHint') : undefined}>
            <input id="ag-name" class="in mono" value={draft.name} disabled={!isNew} onInput={(e) => set({ name: e.currentTarget.value.trim() })} />
          </Field>
          <Field label={t('ag.title')} id="ag-title">
            <input id="ag-title" class="in" value={draft.title ?? ''} onInput={(e) => set({ title: e.currentTarget.value })} />
          </Field>
        </div>
        <Field label={t('ag.channels')}>
          <div class="flags">
            <Switch
              label={t('ag.browserSessions')}
              on={browser}
              disabled={!channelGranted(g, 'browser') && !browser}
              onChange={(v) => toggleChannel('browser', v)}
            />
            <Switch
              label={t('ag.serverRuns')}
              on={server}
              disabled={!channelGranted(g, 'server') && !server}
              onChange={(v) => toggleChannel('server', v)}
            />
            {!channelGranted(g, 'server') ? locked : null}
            {MESSAGING.map((ch) => (
              <Switch
                key={ch}
                label={t(`ag.channel.${ch}` as TextKey)}
                on={(draft.channels ?? []).includes(ch)}
                disabled={!channelGranted(g, ch) && !(draft.channels ?? []).includes(ch)}
                onChange={(v) => toggleChannel(ch, v)}
              />
            ))}
          </div>
        </Field>
        <Field label={t('ag.a2a')} hint={t('ag.a2aHint')}>
          <div class="flags">
            <Switch
              label={t('ag.a2aPublish')}
              on={draft.a2a === true}
              disabled={!channelGranted(g, 'a2a') && draft.a2a !== true}
              onChange={(v) => set({ a2a: v })}
            />
            {!channelGranted(g, 'a2a') ? locked : null}
          </div>
        </Field>
        {draft.prompt ? (
          <p class="hint">
            {t('ag.usesPrompt')}
            <code>{draft.prompt.name}</code>
            {draft.prompt.version ? ` v${draft.prompt.version}` : ''}
            {t('ag.usesPromptRest')}
          </p>
        ) : null}
        <textarea
          class="in"
          spellcheck={false}
          style="min-height:300px"
          value={draft.instructions ?? ''}
          onInput={(e) => set({ instructions: e.currentTarget.value })}
        />
        <div class="hint">{t('ag.insHint')}</div>
        {!isNew && isLegacy(base) ? (
          <p class="hint">
            <Tag tone="warn">{t('tpl.legacy')}</Tag> {t('tpl.legacyHint')}
            {hasLegacyLabels(base) ? <> {t('tpl.legacyLabels')}</> : null}
          </p>
        ) : null}
      </Panel>
    ),
    model: (
      <Panel title={t('ag.modelTitle')}>
        <ol class="chain">
          {chain.map((m, i) => {
            const level = modelLevel(g, m)
            const bad = level === 'off' || (i === 0 && level === 'fallback')
            return (
              <li key={m}>
                <span class="ord">{i + 1}</span>
                <div style="flex:1;min-width:0">
                  <b>{modelName(m)}</b> {i ? <Tag>{t('ag.fallback')}</Tag> : <Tag tone="vi">{t('ag.primary')}</Tag>}
                  {bad ? (
                    <a class="tag bad" href={perm}>
                      {t(level === 'off' ? 'ag.notGranted' : 'ag.fallbackOnly')}
                    </a>
                  ) : null}
                  <div class="s" style="font:12px var(--mono);color:var(--mu);overflow:hidden;text-overflow:ellipsis">{m}</div>
                  {i === 0 && !isNew ? <ModelFit app={app} agent={draft.name} model={m} /> : null}
                </div>
                <span class="grip">
                  <button type="button" aria-label={t('ag.moveUp')} onClick={() => move(i, -1)}>
                    &#8593;
                  </button>
                  <button type="button" aria-label={t('ag.moveDown')} onClick={() => move(i, 1)}>
                    &#8595;
                  </button>
                  <button type="button" aria-label={t('ag.remove')} onClick={() => remove(i)}>
                    &#215;
                  </button>
                </span>
              </li>
            )
          })}
        </ol>
        <div style="display:flex;gap:10px;align-items:center;margin:12px 0 18px;flex-wrap:wrap">
          <select
            class="in"
            style="width:auto;height:34px"
            value=""
            onChange={(e) => {
              const v = e.currentTarget.value
              if (!v) return
              if (!draft.model) set({ model: v })
              else set({ fallbacks: [...(draft.fallbacks ?? []), v] })
            }}
          >
            <option value="">{t(draft.model ? 'ag.addFallback' : 'ag.choosePrimary')}</option>
            {granted
              .filter((x) => !chain.some((m) => normalizeModel(m) === x.name) && (draft.model || x.level === 'any'))
              .map((x) => (
                <option key={x.name} value={x.name.startsWith('bedrock:') ? x.name.slice(8) : x.name}>
                  {modelName(x.name)}
                  {x.level === 'fallback' ? t('ag.fallbackOnlyParen') : ''}
                </option>
              ))}
          </select>
          <span class="hint" style="margin:0">
            {t('ag.granted', { models: granted.map((x) => modelName(x.name) + (x.level === 'fallback' ? t('ag.fallbackOnlyParen') : '')).join(', ') || t('fmt.none') })}{' '}
            <a href={perm}>{t('ag.permissions')}</a>
          </span>
        </div>
        <Field label={t('ag.sessionModels')} hint={t('ag.sessionModelsHint')}>
          <div class="flags">
            {granted
              .filter((x) => x.level === 'any')
              .map((x) => {
                const id = x.name.startsWith('bedrock:') ? x.name.slice(8) : x.name
                const on = (draft.models ?? []).some((m) => normalizeModel(m) === x.name)
                return (
                  <Switch
                    key={x.name}
                    label={modelName(x.name)}
                    on={on}
                    onChange={(v) => set({ models: v ? [...(draft.models ?? []), id] : (draft.models ?? []).filter((m) => normalizeModel(m) !== x.name) })}
                  />
                )
              })}
          </div>
        </Field>
        <div class="grid g3">
          <Field label={t('ag.toolCalls')} id="ag-max">
            <input
              id="ag-max"
              class="in num"
              type="number"
              min={0}
              value={draft.max_tool_calls_per_turn ?? 6}
              onInput={(e) => set({ max_tool_calls_per_turn: Number(e.currentTarget.value) })}
            />
          </Field>
          <Field
            label={t('ag.outputCap')}
            id="ag-out"
            hint={t('ag.outputCapHint', { n: String(inheritedCap) })}
          >
            <input
              id="ag-out"
              class="in num"
              type="number"
              min={64}
              max={128000}
              value={draft.max_output_tokens ?? ''}
              placeholder={String(inheritedCap)}
              onInput={(e) => set({ max_output_tokens: e.currentTarget.value === '' ? null : Number(e.currentTarget.value) })}
            />
          </Field>
          {server ? (
            <Field label={t('ag.temperature')} id="ag-temp">
              <input
                id="ag-temp"
                class="in num"
                type="number"
                step="0.1"
                min={0}
                max={2}
                value={draft.temperature ?? ''}
                placeholder={t('ag.default')}
                onInput={(e) => set({ temperature: e.currentTarget.value === '' ? null : Number(e.currentTarget.value) })}
              />
            </Field>
          ) : null}
          <Field label={t('ag.promptCache')} id="ag-cache" hint={t('ag.promptCacheHint')}>
            <select
              id="ag-cache"
              class="in"
              value={draft.prompt_cache ?? 'off'}
              onChange={(e) => set({ prompt_cache: e.currentTarget.value as AgentDef['prompt_cache'] })}
            >
              <option value="off">{t('ag.off')}</option>
              <option value="5m">{t('ag.5m')}</option>
              <option value="1h">{t('ag.1h')}</option>
            </select>
          </Field>
        </div>
        <Field label={t('ag.modelSettings')} hint={t('ag.modelSettingsHint')}>
          <textarea class="in" spellcheck={false} style="min-height:90px" value={settingsText} onInput={(e) => setSettingsText(e.currentTarget.value)} />
        </Field>
        {row(
          t('ag.showReasoning'),
          t('ag.showReasoningSub'),
          <Switch on={!!draft.show_reasoning} aria={t('ag.showReasoning')} onChange={(v) => set({ show_reasoning: v })} />,
        )}
      </Panel>
    ),
    tools: (
      <>
        <Panel title={t('ag.mcpServers')}>
          {data.servers.length ? (
            data.servers.map((s) => {
              const ok = serverGranted(g, s.name)
              const tools = s.tools ?? []
              const on = tools.filter((x) => toolGranted(g, s.name, x.name)).length
              return row(
                <>
                  {s.name} <StatusTag status={s.status} />
                </>,
                `${s.auth}, ${s.tools ? t('ag.toolsOf', { on, total: tools.length, app: app.name }) : t('ag.toolsNotYet')}`,
                ok ? (
                  <Switch
                    on={(draft.mcp_servers ?? []).includes(s.name)}
                    aria={s.name}
                    onChange={(v) =>
                      set({ mcp_servers: v ? [...(draft.mcp_servers ?? []), s.name] : (draft.mcp_servers ?? []).filter((x) => x !== s.name) })
                    }
                  />
                ) : (
                  locked
                ),
                !ok,
              )
            })
          ) : (
            <p class="hint">
              {t('ag.noMcp')}
              <a href={appHref(app.slug, 'mcp')}>{t('ag.addOne')}</a>.
            </p>
          )}
          {row(
            t('ag.showToolArgs'),
            t('ag.showToolArgsSub'),
            <Switch on={!!draft.show_tool_args} aria={t('ag.showToolArgs')} onChange={(v) => set({ show_tool_args: v })} />,
          )}
        </Panel>
        <div style="height:16px" />
        <Panel title={t('ask.title')}>
          <p class="hint">{t('ask.agentNote')}</p>
          <RulesEditor
            rows={askRows}
            tools={ruleTools}
            idPrefix="ag-ask"
            onChange={(rows) => {
              setAskRows(rows)
              const rules = rulesOf(rows)
              set({ ask_rules: rules.length || base.ask_rules?.length ? rules : base.ask_rules })
            }}
          />
        </Panel>
        <div style="height:16px" />
        {browser ? (
          <Panel title={t('ag.clientTools')}>
            {CLIENT_TOOLS.map(([k, dk]) => {
              const d = t(dk)
              const ok = clientGranted(g, k)
              const ct = draft.client_tools ?? {}
              const on = k === 'navigate' ? (ct.navigate?.enabled ?? true) : ((ct as Record<string, unknown>)[k] ?? true) !== false
              return (
                <div key={k}>
                  {row(
                    k,
                    k === 'memory' && app.storageMode === 'external' ? t('ag.onlyEvokai', { text: d }) : d,
                    ok ? (
                      <Switch
                        on={on}
                        aria={k}
                        onChange={(v) =>
                          set({
                            client_tools: k === 'navigate' ? { ...ct, navigate: { ...ct.navigate, enabled: v } } : { ...ct, [k]: v },
                          })
                        }
                      />
                    ) : (
                      locked
                    ),
                    !ok,
                  )}
                  {k === 'navigate' && ok && on ? (
                    <Field label={t('ag.allowedPaths')} hint={t('ag.allowedPathsHint')}>
                      <input
                        class="in mono"
                        value={ct.navigate?.path_pattern ?? '^/[A-Za-z0-9/_?=&.-]*$'}
                        onInput={(e) => set({ client_tools: { ...ct, navigate: { ...ct.navigate, path_pattern: e.currentTarget.value } } })}
                      />
                    </Field>
                  ) : null}
                </div>
              )
            })}
            {row(
              'confirm',
              t('ag.confirmCard'),
              clientGranted(g, 'confirm') ? <Tag tone="ok">{t('ag.grantedTag')}</Tag> : locked,
              !clientGranted(g, 'confirm'),
            )}
            <p class="hint">{t('ag.confirmCardHint')}</p>
            <h4>{t('ag.customTools')}</h4>
            <p class="hint">{t('ag.customToolsHint')}</p>
            {toolRows.map((r, i) => {
              const edit = (patch: Partial<ClientToolRow>) => setToolRows(toolRows.map((x, j) => (j === i ? { ...x, ...patch } : x)))
              const issue = toolIssues[i]
              return (
                <div key={i} class="opt" style="display:block">
                  <Field label={t('ag.customNameLabel')}>
                    <input class="in mono" value={r.name} onInput={(e) => edit({ name: e.currentTarget.value })} />
                  </Field>
                  <Field label={t('ag.customDescriptionLabel')}>
                    <textarea class="in" value={r.description} onInput={(e) => edit({ description: e.currentTarget.value })} />
                  </Field>
                  <Field label={t('ag.customParameters')}>
                    <textarea class="in mono" spellcheck={false} style="min-height:120px" value={r.parameters} onInput={(e) => edit({ parameters: e.currentTarget.value })} />
                  </Field>
                  {row(t('ag.customWait'), t('ag.customWaitHint'), <Switch on={r.wait} aria={t('ag.customWait')} onChange={(v) => edit({ wait: v })} />)}
                  {issue ? <p class="hint bad">{t(issue)}</p> : null}
                  <Btn onClick={() => setToolRows(toolRows.filter((_, j) => j !== i))}>{t('ag.customRemove')}</Btn>
                </div>
              )
            })}
            {toolRows.length >= MAX_TOOLS ? (
              <p class="hint">{t('ag.customTooMany')}</p>
            ) : (
              <Btn onClick={() => setToolRows([...toolRows, blankTool()])}>{t('ag.customAdd')}</Btn>
            )}
          </Panel>
        ) : (
          <Panel title={t('ag.clientTools')}>
            <p class="hint" style="margin:0">
              {t('ag.noClient')}
            </p>
          </Panel>
        )}
        {browser
          ? (() => {
              const ok = attachmentsGranted(g)
              const a = draft.attachments ?? { enabled: false }
              const kinds: AttachmentKind[] = a.types ?? [...ATTACHMENT_KINDS]
              const put = (p: Partial<NonNullable<AgentDef['attachments']>>) => set({ attachments: { ...a, ...p } })
              const num = (key: 'max_file_mb' | 'max_files' | 'max_conversation_mb', label: string, max: number, dflt: number) => (
                <Field label={label}>
                  <input class="in num" type="number" min={1} max={max} value={a[key] ?? dflt} onInput={(e) => put({ [key]: Number(e.currentTarget.value) })} />
                </Field>
              )
              return (
                <>
                  <div style="height:16px" />
                  <Panel title={t('ag.attachments')}>
                    {row(
                      t('ag.attachmentsOn'),
                      ok ? t('ag.attachmentsSub') : t('ag.attachmentsNotGranted'),
                      ok ? <Switch on={!!a.enabled} aria={t('ag.attachmentsOn')} onChange={(v) => put({ enabled: v })} /> : locked,
                      !ok,
                    )}
                    {ok && a.enabled ? (
                      <>
                        <Field label={t('ag.attachmentsTypes')} hint={t('ag.attachmentsTypesHint')}>
                          <div class="flags">
                            {ATTACHMENT_KINDS.map((k) => (
                              <Switch
                                key={k}
                                label={k}
                                on={kinds.includes(k)}
                                disabled={kinds.length === 1 && kinds.includes(k)}
                                onChange={(v) => put({ types: v ? [...kinds, k] : kinds.filter((x) => x !== k) })}
                              />
                            ))}
                          </div>
                        </Field>
                        <div class="grid g3">
                          {num('max_file_mb', t('ag.attachmentsFileMb'), 50, 10)}
                          {num('max_files', t('ag.attachmentsFiles'), 10, 5)}
                          {num('max_conversation_mb', t('ag.attachmentsConversationMb'), 1000, 50)}
                        </div>
                      </>
                    ) : null}
                  </Panel>
                </>
              )
            })()
          : null}
      </>
    ),
    skills: (
      <Panel title={t('ag.sec.skills')}>
        {row(
          t('ag.everySkill'),
          t('ag.everySkillSub'),
          <Switch on={draft.skills == null} onChange={(v) => set({ skills: v ? null : data.skills.filter((s) => skillGranted(g, s.name)).map((s) => s.name) })} />,
        )}
        {data.skills.map((s) => {
          const ok = skillGranted(g, s.name)
          const on = draft.skills == null || draft.skills.includes(s.name)
          return (
            <div key={s.name}>
              {row(
                s.name,
                s.description,
                ok ? (
                  <Switch
                    on={on}
                    aria={s.name}
                    disabled={draft.skills == null}
                    onChange={(v) =>
                      set(
                        v
                          ? { skills: [...(draft.skills ?? []), s.name] }
                          : {
                              skills: (draft.skills ?? []).filter((x) => x !== s.name),
                              preload_skills: (draft.preload_skills ?? []).filter((x) => x !== s.name),
                            },
                      )
                    }
                  />
                ) : (
                  locked
                ),
                !ok,
              )}
            </div>
          )
        })}
        <Field
          label={t('ag.preloaded')}
          hint={t('ag.preloadedHint')}
        >
          <div class="flags">
            {data.skills
              .filter((s) => skillGranted(g, s.name) && (draft.skills == null || draft.skills.includes(s.name)))
              .map((s) => {
                const pre = draft.preload_skills ?? []
                return (
                  <Switch
                    key={s.name}
                    label={s.name}
                    on={pre.includes(s.name)}
                    onChange={(v) => set({ preload_skills: v ? [...pre, s.name] : pre.filter((x) => x !== s.name) })}
                  />
                )
              })}
          </div>
        </Field>
        <div class="hint">{t('ag.skillsNote')}</div>
      </Panel>
    ),
    kb: (
      <>
      <Panel title={t('ag.sec.kb')}>
        <p class="hint" style="margin:0 0 14px">
          {t('ag.kbNote')}
        </p>
        {data.bases.length ? (
          data.bases.map((b) => {
            const ok = knowledgeGranted(g, b.name)
            const on = (draft.knowledge?.bases ?? []).includes(b.name)
            const bases = draft.knowledge?.bases ?? []
            return (
              <div key={b.name}>
                {row(
                  b.title || b.name,
                  b.description || b.name,
                  ok ? (
                    <Switch
                      on={on}
                      aria={b.name}
                      onChange={(v) =>
                        set({ knowledge: { ...(draft.knowledge ?? {}), bases: v ? [...bases, b.name] : bases.filter((x) => x !== b.name) } })
                      }
                    />
                  ) : (
                    locked
                  ),
                  !ok,
                )}
              </div>
            )
          })
        ) : (
          <p class="hint">
            {t('ag.kbNone')} <a href={appHref(app.slug, 'knowledge', 'new')}>{t('ag.kbCreate')}</a>
          </p>
        )}
        {(draft.knowledge?.bases ?? []).length ? (
          <>
            {row(
              t('ag.kbAuto'),
              t('ag.kbAutoSub'),
              <Switch on={!!draft.knowledge?.auto} onChange={(v) => set({ knowledge: { ...(draft.knowledge ?? {}), auto: v } })} />,
            )}
            <Field label={t('ag.kbTopK')} hint={t('ag.kbTopKHint')}>
              <input
                class="in num"
                type="number"
                min={1}
                max={20}
                value={draft.knowledge?.top_k ?? ''}
                onInput={(e) => {
                  const v = e.currentTarget.value
                  set({ knowledge: { ...(draft.knowledge ?? {}), top_k: v ? Number(v) : null } })
                }}
              />
            </Field>
          </>
        ) : null}
        {(() => {
          const code = codeGranted(g)
          const ok = code.granted && code.enabled
          return row(
            t('ag.code'),
            ok ? t('ag.codeSub') : code.granted ? t('ag.codeNoSandbox') : t('ag.codeNotGranted'),
            ok ? <Switch on={!!draft.code?.enabled} aria={t('ag.code')} onChange={(v) => set({ code: { enabled: v } })} /> : locked,
            !ok,
          )
        })()}
        <h4>{t('ag.builtinTools')}</h4>
        <p class="hint">{t('ag.builtinToolsHint')}</p>
        {BUILTIN_KINDS.map((kind) => {
          const ok = builtinGranted(g, kind)
          const list = (draft.builtin_tools ?? []) as Array<{ kind: string; config?: Record<string, unknown> }>
          const cur = list.find((x) => x.kind === kind)
          const others = list.filter((x) => x.kind !== kind)
          return (
            <div key={kind}>
              {row(
                t(`ag.builtin.${kind}` as TextKey),
                ok ? (STORING_KINDS.includes(kind) ? t('ag.builtinKeeps') : kind) : t('ag.builtinNotGranted'),
                ok ? (
                  <Switch
                    on={!!cur}
                    aria={kind}
                    onChange={(v) => set({ builtin_tools: (v ? [...others, { kind, config: {} }] : others) as AgentDef['builtin_tools'] })}
                  />
                ) : (
                  locked
                ),
                !ok,
              )}
              {cur && ok ? (
                <Field label={t('ag.builtinConfig')} hint={t('ag.builtinConfigHint')}>
                  <textarea
                    class="in mono"
                    spellcheck={false}
                    value={JSON.stringify(cur.config ?? {}, null, 2)}
                    onChange={(e) => {
                      try {
                        const config = JSON.parse(e.currentTarget.value) as Record<string, unknown>
                        set({ builtin_tools: [...others, { kind, config }] as AgentDef['builtin_tools'] })
                      } catch {
                        toast(t('ag.builtinBadJson'), '')
                      }
                    }}
                  />
                </Field>
              ) : null}
            </div>
          )
        })}
        {(() => {
          const ok = openaiConfirmGranted(g)
          return row(
            t('ag.openaiConfirm'),
            ok ? t('ag.openaiConfirmSub') : t('ag.openaiConfirmNotGranted'),
            ok ? <Switch on={!!draft.openai_confirm} aria={t('ag.openaiConfirm')} onChange={(v) => set({ openai_confirm: v })} /> : locked,
            !ok,
          )
        })()}
      </Panel>
      <Panel title={t('ag.caps')}>
        <p class="hint" style="margin:0 0 14px">
          {t('ag.capsNote')}
        </p>
        {(() => {
          const items = capabilityItems(g).filter((x) => x.value === true)
          if (!items.length) return <p class="hint">{t('ag.capsNone')}</p>
          const uses = draft.capabilities ?? {}
          return items.map((x) => {
            const ok = capabilityUsable(x)
            const use = uses[x.name]
            const on = !!use?.enabled
            return (
              <div key={x.name}>
                {row(
                  <code>{x.name}</code>,
                  capDescription(x),
                  ok ? (
                    <Switch
                      on={on}
                      aria={x.name}
                      onChange={(v) => set({ capabilities: { ...uses, [x.name]: { enabled: v, config: use?.config ?? {} } } })}
                    />
                  ) : (
                    locked
                  ),
                  !ok,
                )}
                {on && use?.config?.acts_as === 'application' ? (
                  <p style="margin:4px 0 8px">
                    <Tag tone="warn" dot>
                      {t('ag.capActsAsApplication')}
                    </Tag>
                  </p>
                ) : null}
                {on ? (
                  <Field label={t('ag.capSettings', { name: x.name })} hint={t('ag.capSettingsHint')}>
                    <textarea
                      class="in mono"
                      rows={3}
                      value={JSON.stringify(use?.config ?? {}, null, 2)}
                      onChange={(e) => {
                        try {
                          const config = JSON.parse(e.currentTarget.value || '{}') as Record<string, unknown>
                          set({ capabilities: { ...uses, [x.name]: { enabled: true, config } } })
                        } catch {
                          toast(t('ag.capSettingsBad'), x.name, null)
                        }
                      }}
                    />
                  </Field>
                ) : null}
                {on && x.name === 'subagents' ? (
                  <Field label={t('ag.subagents')} hint={t('ag.subagentsHint')}>
                    <input
                      class="in mono"
                      value={(draft.subagents ?? []).join(', ')}
                      onChange={(e) =>
                        set({
                          subagents: e.currentTarget.value
                            .split(',')
                            .map((v) => v.trim())
                            .filter(Boolean),
                        })
                      }
                    />
                  </Field>
                ) : null}
              </div>
            )
          })
        })()}
      </Panel>
      </>
    ),
    voice: (
      <Panel title={t('ag.sec.voice')}>
        <p class="hint" style="margin:0 0 14px">
          {t('ag.voiceNote')}
        </p>
        <div class="grid g2">
          <Field label={t('ag.firstClause')} hint={t('ag.firstClauseHint')}>
            <div class="affix">
              <input
                class="in num"
                type="number"
                min={0}
                value={draft.voice?.first_clause_min_chars ?? 48}
                onInput={(e) => set({ voice: { ...draft.voice, first_clause_min_chars: Number(e.currentTarget.value) } })}
              />
              <span>{t('ag.characters')}</span>
            </div>
          </Field>
          <Field label={t('ag.serverVoice')} hint={t('ag.serverVoiceHint')}>
            <input class="in mono" value={draft.voice?.voice_id ?? ''} onInput={(e) => set({ voice: { ...draft.voice, voice_id: e.currentTarget.value } })} />
          </Field>
        </div>
        <Field label={t('ag.filler')} hint={t('ag.fillerHint')}>
          <input
            class="in"
            value={draft.voice?.fillers?.['*'] ?? ''}
            onInput={(e) => set({ voice: { ...draft.voice, fillers: { ...draft.voice?.fillers, '*': e.currentTarget.value } } })}
          />
        </Field>
        <Field label={t('ag.spoken')} hint={t('ag.spokenHint')}>
          <textarea
            class="in"
            style="min-height:110px;font:14px/1.55 var(--ui)"
            value={draft.voice?.prompt_rule ?? ''}
            onInput={(e) => set({ voice: { ...draft.voice, prompt_rule: e.currentTarget.value } })}
          />
        </Field>
      </Panel>
    ),
    out: (
      <Panel title={t('ag.outTitle')}>
        <p class="hint" style="margin:0 0 10px">
          {t('ag.outNote')}
        </p>
        <textarea class="in" spellcheck={false} style="min-height:340px" value={schemaText} onInput={(e) => setSchemaText(e.currentTarget.value)} />
      </Panel>
    ),
    ver: (
      <Panel title={t('ag.versions')}>
        <ul class="vlist">
          {data.versions.map((v, i) => {
            const n = data.versions.length - i
            const by =
              v.createdBy === 'config'
                ? t('ov.byConfig')
                : v.createdBy === 'operator'
                  ? t('ov.byOperator')
                  : data.keys.get(v.createdBy ?? '')
                    ? t('ov.byAdminKey', { prefix: data.keys.get(v.createdBy ?? '') })
                    : t('ov.byAdminKeyBare')
            return (
              <li key={v.version} class={v.active ? 'live' : ''}>
                <div style="display:flex;align-items:center;gap:10px">
                  <b>v{n}</b>
                  <code style="color:var(--mu)">{v.version.slice(0, 7)}</code>
                  {v.active ? (
                    <Tag tone="ok" dot>
                      {t('ag.live')}
                    </Tag>
                  ) : null}
                  {isLegacy(v.definition) ? <Tag tone="warn">{t('tpl.legacy')}</Tag> : null}
                  <span style="margin-left:auto">
                    {v.active ? null : (
                      <Btn sm kind="ghost" onClick={() => void restore(v)}>
                        {t('ag.restore')}
                      </Btn>
                    )}
                  </span>
                </div>
                <small>
                  {changes(data.versions[i + 1]?.definition, v.definition)}. {day(v.createdAt)}, {by}
                </small>
              </li>
            )
          })}
        </ul>
        <div style="margin-top:12px">
          <Btn sm kind="ghost danger" onClick={() => void retire()}>
            {t('ag.retire')}
          </Btn>
        </div>
      </Panel>
    ),
  }
  if (!secs.find((s) => s[0] === sec)) setSec('ins')

  const flame = (
    <span class="w" title={t('ag.unsavedTitle')}>
      {Fx({ k: 'flameLoop', size: 26 }) ?? <i style="width:6px;height:6px;border-radius:50%;background:var(--gold);margin:auto" />}
    </span>
  )
  return (
    <>
      <Hero
        title={isNew ? draft.name || t('ag.new') : base.name}
        sub={`${draft.title || t('ag.noTitle')}. ${t(server && !browser ? 'ag.serverSub' : 'ag.browserSub')}`}
        actions={
          <>
            {!isNew ? (
              <Btn href={`#/playground?app=${encodeURIComponent(app.slug)}&agent=${encodeURIComponent(base.name)}`}>{t('ag.test')}</Btn>
            ) : null}
            <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {isNew ? t('ag.create') : t('ag.saveAs', { n: vnum + 1 })}
            </Btn>
          </>
        }
      />
      {problems ? (
        <Panel style="margin-bottom:16px;box-shadow:0 0 0 1px color-mix(in srgb,var(--s-bad) 45%,transparent)">
          <b style="color:var(--s-bad)">{t('ag.notSaved')}</b> {problems} {problems.includes('not granted') ? <a href={perm}>{t('ag.openPerms')}</a> : null}
        </Panel>
      ) : null}
      <div class="ed">
        <nav class="secnav" aria-label={t('ag.sections')} data-tour="agent-sections">
          {secs.map(([k, l]) => (
            <a
              key={k}
              role="button"
              tabIndex={0}
              aria-current={k === sec ? 'true' : undefined}
              onClick={() => setSec(k)}
              onKeyDown={(e) => e.key === 'Enter' && setSec(k)}
            >
              {l}
              {dirty(k) ? flame : null}
            </a>
          ))}
        </nav>
        <div>
          <div class="pane on" key={sec}>
            {panes[sec]}
          </div>
        </div>
        <aside class="panel preview" data-tour="agent-preview">
          <div class="head">
            <img src={HAT} alt="" />
            <div>
              <b style="font:600 17px var(--disp)">{draft.name || t('ag.newAgent')}</b>
              <div class="hint" style="margin:0">
                {isNew ? t('ag.draft') : `${t('ag.liveV', { n: liveIndex })}${unsaved ? t('ag.draftV', { n: vnum + 1 }) : ''}`}
              </div>
            </div>
          </div>
          <KV
            rows={[
              [t('ag.primaryRow'), draft.model ? modelName(draft.model) : t('fmt.none')],
              [t('ag.fallbacksRow'), String(Math.max(0, chain.length - 1))],
              [t('ag.toolsRow'), t('ag.nGranted', { n: toolsGranted })],
              [t('ag.skillsRow'), String(skillCount)],
              [t('ag.kbRow'), (draft.knowledge?.bases ?? []).join(', ') || t('fmt.none')],
              [t('ag.channelsRow'), [...(draft.channels ?? ['browser']), ...(draft.a2a ? ['a2a'] : [])].join(', ')],
              [
                t('ag.unsavedRow'),
                <span style={unsaved ? 'color:var(--gold)' : ''}>{unsaved ? tn('ag.nSections', unsaved) : t('fmt.none')}</span>,
              ],
            ]}
          />
          {live ? <p class="hint">{t('ag.liveSince', { when: ago(live.createdAt) })}</p> : null}
          <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--hair2)" class="hint">
            {t('ag.choicesFrom', { app: app.name })}
            <a class="tag vi" href={perm}>
              {t('ag.permissions')}
            </a>
          </div>
        </aside>
      </div>
    </>
  )
}
