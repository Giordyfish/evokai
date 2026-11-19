/**
 * First run: an operator with no application creates one, its first agent (a model from the allowed list,
 * instructions, an optional MCP server tried before it is saved, optional skills) and its keys, with no YAML.
 */
import { useEffect, useState } from 'preact/hooks'

import type { McpTestResult, ModelSuggestion, NewApplicationKeys, ParsedSkill } from '@client/core/types'
import { describe, forApp, root } from '../api'
import { copy, pl, slugOf } from '../format'
import { Empty, sparkle, toast } from '../magic'
import { appHref, setTitle, useConsole } from '../state'
import { Btn, Field, Hero, Panel, Seg, Tag } from '../ui'
import { t, type TextKey } from '../i18n'

const STEPS: readonly TextKey[] = ['setup.s.app', 'setup.s.agent', 'setup.s.tools', 'setup.s.skills', 'setup.s.create']
type Auth = 'none' | 'secret' | 'forward_user'
const authLabels = (): Record<Auth, string> => ({ none: t('setup.auth.none'), secret: t('setup.auth.secret'), forward_user: t('setup.auth.user') })

/** An agent name from a title: letters, digits, dashes. */
const agentNameOf = (s: string): string => slugOf(s) || 'assistant'

interface Draft {
  name: string
  slug: string
  slugTouched: boolean
  origins: string
  agent: string
  agentTitle: string
  model: string
  customModel: string
  instructions: string
  mcpUrl: string
  mcpName: string
  auth: Auth
  token: string
}

interface Step {
  label: string
  state: 'todo' | 'run' | 'done' | 'failed'
  error?: string
}

export function Setup() {
  const c = useConsole()
  setTitle(t('page.setup'))
  const [step, setStep] = useState(0)
  const [d, setD] = useState<Draft>({
    name: '',
    slug: '',
    slugTouched: false,
    origins: 'http://localhost:3000',
    agent: 'assistant',
    agentTitle: t('setup.agentTitle'),
    model: '',
    customModel: '',
    // The default instructions in the console's language; {app} and {date} are filled by the service.
    instructions: t('setup.instructions'),
    mcpUrl: '',
    mcpName: 'app',
    auth: 'forward_user',
    token: '',
  })
  const [models, setModels] = useState<{ items: ModelSuggestion[]; allow: string[] } | null>(null)
  const [modelError, setModelError] = useState<string | null>(null)
  const [probe, setProbe] = useState<McpTestResult | null>(null)
  const [probing, setProbing] = useState(false)
  const [skills, setSkills] = useState<ParsedSkill[]>([])
  const [skillErrors, setSkillErrors] = useState<string[]>([])
  const [progress, setProgress] = useState<Step[] | null>(null)
  const [keys, setKeys] = useState<NewApplicationKeys | null>(null)
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }))

  useEffect(() => {
    root
      .models()
      .then((m) => {
        setModels(m)
        const first = m.items.find((x) => x.ready) ?? m.items[0]
        if (first) setD((x) => (x.model ? x : { ...x, model: first.id }))
      })
      .catch((e) => setModelError(describe(e)))
  }, [])

  if (!c.canCreate)
    return (
      <Panel>
        <Empty>
          <b>{t('setup.onlyOperator')}</b>
          <br />
          {t('setup.onlyOperatorText')}
        </Empty>
      </Panel>
    )

  const model = d.model === 'other' ? d.customModel.trim() : d.model
  const appOk = d.name.trim().length > 0 && /^[a-z0-9][a-z0-9-]{1,40}$/.test(d.slug)
  const agentOk = /^[A-Za-z0-9_-]+$/.test(d.agent) && model.length > 0 && d.instructions.trim().length > 0
  const mcpOk = !d.mcpUrl.trim() || (/^https?:\/\//.test(d.mcpUrl.trim()) && /^[a-z0-9][a-z0-9_-]*$/.test(d.mcpName) && (d.auth !== 'secret' || d.token.trim().length > 0))
  const can = [appOk, agentOk, mcpOk, true, appOk && agentOk && mcpOk]

  const test = async () => {
    setProbing(true)
    setProbe(null)
    try {
      setProbe(await root.operator.mcpTest({ url: d.mcpUrl.trim(), token: d.token.trim() || null }))
    } catch (e) {
      setProbe({ ok: false, error: describe(e) })
    } finally {
      setProbing(false)
    }
  }

  const addFiles = async (files: FileList | null) => {
    if (!files) return
    const errors: string[] = []
    const found: ParsedSkill[] = []
    for (const f of Array.from(files)) {
      try {
        found.push(await root.parseSkill(await f.text()))
      } catch (e) {
        errors.push(`${f.name}: ${describe(e)}`)
      }
    }
    setSkills((all) => [...all.filter((s) => !found.some((x) => x.name === s.name)), ...found])
    setSkillErrors(errors)
  }

  const create = async (e: Event) => {
    const el = e.currentTarget as Element
    const plan: Step[] = [
      { label: t('setup.stepApp', { name: d.name.trim() }), state: 'todo' },
      ...(d.mcpUrl.trim() ? [{ label: t('setup.stepMcp', { name: d.mcpName }), state: 'todo' as const }] : []),
      ...skills.map((s) => ({ label: t('setup.stepSkill', { name: s.name }), state: 'todo' as const })),
      { label: t('setup.stepAgent', { name: d.agent }), state: 'todo' },
    ]
    setProgress(plan)
    let i = 0
    const mark = (state: Step['state'], error?: string) => {
      plan[i] = { ...plan[i]!, state, error }
      setProgress([...plan])
    }
    try {
      mark('run')
      const created = await root.operator.applications.create({
        slug: d.slug,
        name: d.name.trim(),
        allowed_origins: d.origins.split(/\s+/).filter(Boolean),
      })
      setKeys(created)
      mark('done')
      const api = forApp(created.id)
      if (d.mcpUrl.trim()) {
        i++
        mark('run')
        await api.admin.mcpServers.put(d.mcpName, {
          url: d.mcpUrl.trim(),
          auth: d.auth,
          ...(d.auth === 'secret' ? { secret: d.token.trim() } : {}),
        })
        mark('done')
      }
      for (const s of skills) {
        i++
        mark('run')
        await api.admin.skills.put(s.name, { description: s.description, body: s.body })
        mark('done')
      }
      i++
      mark('run')
      await api.admin.agents.put({
        name: d.agent,
        title: d.agentTitle.trim() || d.agent,
        model,
        instructions: d.instructions.trim(),
        mcp_servers: d.mcpUrl.trim() ? [d.mcpName] : [],
        skills: skills.length ? skills.map((s) => s.name) : null,
      })
      mark('done')
      sparkle(el)
      toast(t('setup.ready'), t('setup.readySub'))
      await c.reloadApps()
    } catch (err) {
      mark('failed', describe(err))
    }
  }

  const done = progress?.every((p) => p.state === 'done') ?? false
  const failed = progress?.some((p) => p.state === 'failed') ?? false

  return (
    <>
      <Hero
        title={c.apps.length ? t('apps.newApplication') : t('setup.welcome')}
        sub={t('setup.sub')}
      />
      <ol class="wsteps" aria-label={t('setup.steps')}>
        {STEPS.map((s, k) => (
          <li key={s} aria-current={k === step ? 'step' : undefined} class={k < step ? 'past' : ''}>
            <button type="button" disabled={!!progress || k > step || !can.slice(0, k).every(Boolean)} onClick={() => setStep(k)}>
              <i>{k + 1}</i>
              {t(s)}
            </button>
          </li>
        ))}
      </ol>

      {step === 0 ? (
        <Panel title={t('setup.theApp')}>
          <p class="hint" style="margin:0 0 14px">{t('setup.theAppHint')}</p>
          <div class="grid g2" style="gap:0 14px">
            <Field label={t('common.name')} id="w-name">
              <input
                id="w-name"
                class="in"
                value={d.name}
                maxLength={120}
                autoFocus
                onInput={(e) => {
                  const name = e.currentTarget.value
                  set({ name, ...(d.slugTouched ? {} : { slug: slugOf(name) }) })
                }}
              />
            </Field>
            <Field label={t('apps.slug')} id="w-slug" hint={t('setup.slugHint')}>
              <input id="w-slug" class="in mono" value={d.slug} onInput={(e) => set({ slug: e.currentTarget.value, slugTouched: true })} />
            </Field>
          </div>
          <Field label={t('apps.origins')} id="w-origins" hint={t('setup.originsHint')}>
            <input id="w-origins" class="in mono" value={d.origins} onInput={(e) => set({ origins: e.currentTarget.value })} />
          </Field>
        </Panel>
      ) : null}

      {step === 1 ? (
        <Panel title={t('setup.firstAgent')}>
          <div class="grid g2" style="gap:0 14px">
            <Field label={t('setup.title')} id="w-title">
              <input
                id="w-title"
                class="in"
                value={d.agentTitle}
                onInput={(e) => {
                  const title = e.currentTarget.value
                  set({ agentTitle: title, agent: agentNameOf(title) })
                }}
              />
            </Field>
            <Field label={t('common.name')} id="w-agent" hint={t('setup.nameHint')}>
              <input id="w-agent" class="in mono" value={d.agent} onInput={(e) => set({ agent: e.currentTarget.value })} />
            </Field>
          </div>
          <Field label={t('setup.model')} id="w-model" hint={models?.items.find((m) => m.id === d.model)?.note ?? t('setup.anyModel')}>
            {modelError ? <p class="hint" style="color:var(--s-bad)">{modelError}</p> : null}
            <select id="w-model" class="in" value={d.model} onChange={(e) => set({ model: e.currentTarget.value })}>
              {(models?.items ?? []).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                  {m.ready === false ? t('setup.noCreds') : ''}
                </option>
              ))}
              <option value="other">{t('setup.otherModel')}</option>
            </select>
          </Field>
          {d.model === 'other' ? (
            <Field label={t('setup.modelId')} id="w-custom" hint={t('setup.modelIdHint', { allow: (models?.allow ?? []).join(', ') })}>
              <input id="w-custom" class="in mono" value={d.customModel} placeholder="openai:gpt-5-mini" onInput={(e) => set({ customModel: e.currentTarget.value })} />
            </Field>
          ) : null}
          <Field label={t('setup.instructionsLabel')} id="w-ins" hint={t('setup.instructionsHint')}>
            <textarea id="w-ins" class="in" spellcheck={false} style="min-height:150px;padding:10px 12px;height:auto" value={d.instructions} onInput={(e) => set({ instructions: e.currentTarget.value })} />
          </Field>
        </Panel>
      ) : null}

      {step === 2 ? (
        <Panel
          title={
            <>
              {t('setup.yourTools')} <Tag>{t('setup.optional')}</Tag>
            </>
          }
        >
          <p class="hint" style="margin:0 0 14px">{t('setup.toolsHint')}</p>
          <div class="grid g-21" style="gap:0 14px">
            <Field label={t('setup.mcpUrl')} id="w-mcp">
              <input id="w-mcp" class="in mono" value={d.mcpUrl} placeholder="https://app.example.com/api/mcp" onInput={(e) => set({ mcpUrl: e.currentTarget.value })} />
            </Field>
            <Field label={t('setup.serverName')} id="w-mcpname">
              <input id="w-mcpname" class="in mono" value={d.mcpName} onInput={(e) => set({ mcpName: e.currentTarget.value })} />
            </Field>
          </div>
          <Field label={t('setup.howSignIn')}>
            <Seg options={['forward_user', 'secret', 'none'] as const} value={d.auth} labels={authLabels()} onChange={(auth) => set({ auth })} />
          </Field>
          <Field
            label={d.auth === 'secret' ? t('setup.auth.secret') : t('setup.testToken')}
            id="w-token"
            hint={t(d.auth === 'secret' ? 'setup.secretHint' : d.auth === 'forward_user' ? 'setup.userHint' : 'setup.noneHint')}
          >
            <input id="w-token" class="in mono" type="password" autocomplete="off" value={d.token} onInput={(e) => set({ token: e.currentTarget.value })} />
          </Field>
          <Btn sm disabled={!/^https?:\/\//.test(d.mcpUrl.trim())} busy={probing ? t('setup.connecting') : false} onClick={() => void test()}>
            {t('setup.test')}
          </Btn>
          {probe ? (
            probe.ok ? (
              <div style="margin-top:12px" role="status">
                <Tag tone="ok" dot>
                  {t('setup.connected', { ms: probe.latencyMs ?? 0 })}
                </Tag>{' '}
                {pl(probe.tools?.length ?? 0, 'n.tool')}
                <ul class="wtools">
                  {(probe.tools ?? []).map((x) => (
                    <li key={x.name}>
                      <code>{x.name}</code> {x.readOnly ? <Tag>{t('setup.reads')}</Tag> : <Tag tone="warn">{t('setup.asks')}</Tag>}
                      <span>{x.description}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p class="hint" role="alert" style="color:var(--s-bad);margin-top:12px">
                {t('setup.couldNot', { error: probe.error })}
              </p>
            )
          ) : null}
        </Panel>
      ) : null}

      {step === 3 ? (
        <Panel
          title={
            <>
              {t('tab.skills')} <Tag>{t('setup.optional')}</Tag>
            </>
          }
        >
          <p class="hint" style="margin:0 0 14px">
            {t('setup.skillsHint1')}
            <code>name</code>
            {t('setup.skillsHint2')}
            <code>description</code>
            {t('setup.skillsHint3')}
          </p>
          <Field label={t('setup.skillFiles')} id="w-skills">
            <input id="w-skills" type="file" accept=".md,text/markdown,text/plain" multiple onChange={(e) => void addFiles(e.currentTarget.files)} />
          </Field>
          {skillErrors.map((x) => (
            <p key={x} class="hint" role="alert" style="color:var(--s-bad)">
              {x}
            </p>
          ))}
          {skills.length ? (
            <ul class="wtools">
              {skills.map((s) => (
                <li key={s.name}>
                  <code>{s.name}</code>
                  <span>{s.description}</span>
                  <Btn sm kind="ghost" onClick={() => setSkills(skills.filter((x) => x.name !== s.name))}>
                    {t('setup.remove')}
                  </Btn>
                </li>
              ))}
            </ul>
          ) : null}
        </Panel>
      ) : null}

      {step === 4 ? (
        <Panel title={t('setup.create')}>
          {!progress ? (
            <>
              <dl class="kv">
                <dt>{t('setup.s.app')}</dt>
                <dd>
                  {d.name} <code>{d.slug}</code>
                </dd>
                <dt>{t('setup.s.agent')}</dt>
                <dd>
                  {d.agentTitle} <code>{d.agent}</code> {t('setup.on')} <code>{model}</code>
                </dd>
                <dt>{t('setup.s.tools')}</dt>
                <dd>{d.mcpUrl.trim() ? <code>{d.mcpUrl.trim()}</code> : t('setup.none')}</dd>
                <dt>{t('setup.s.skills')}</dt>
                <dd>{skills.length ? skills.map((s) => s.name).join(', ') : t('setup.none')}</dd>
              </dl>
              <Btn kind="pri" fx="key" onClick={(e) => void create(e)}>
                {t('setup.createAll')}
              </Btn>
            </>
          ) : (
            <ul class="wprog" aria-live="polite">
              {progress.map((p) => (
                <li key={p.label} data-s={p.state}>
                  <i aria-hidden="true" />
                  {p.label}
                  {p.error ? <span class="hint" style="color:var(--s-bad);display:block">{p.error}</span> : null}
                </li>
              ))}
            </ul>
          )}
          {failed && keys ? (
            <p class="hint">
              {t('setup.exists')}
              <a href={appHref(d.slug)}>{t('setup.openName', { name: d.name })}</a>).
            </p>
          ) : null}
        </Panel>
      ) : null}

      {keys && (done || failed) ? (
        <div style="margin-top:16px">
          <Keys keys={keys} slug={d.slug} agent={d.agent} done={done} />
        </div>
      ) : null}

      {!progress ? (
        <div style="display:flex;gap:8px;margin-top:16px">
          {step > 0 ? (
            <Btn kind="ghost" onClick={() => setStep(step - 1)}>
              {t('setup.back')}
            </Btn>
          ) : null}
          {step < STEPS.length - 1 ? (
            <Btn kind="pri" disabled={!can[step]} onClick={() => setStep(step + 1)}>
              {t((step === 2 && !d.mcpUrl.trim()) || (step === 3 && !skills.length) ? 'setup.skip' : 'setup.next')}
            </Btn>
          ) : null}
        </div>
      ) : null}
    </>
  )
}

function Keys({ keys, slug, agent, done }: { keys: NewApplicationKeys; slug: string; agent: string; done: boolean }) {
  const base = `${location.origin}${location.pathname.replace(/\/admin\/?.*$/, '')}`
  const curl = `curl -s -X POST ${base}/v1/session-tokens \\
  -H "Authorization: Bearer $EVOKAI_SERVER_KEY" -H "Content-Type: application/json" \\
  -d '{"subject": "user-1", "agent": "${agent}"}'`
  return (
    <Panel
      title={
        <>
          {t('setup.keys')}{' '}
          <Tag tone="gold" dot>
            {t('apps.shownOnce')}
          </Tag>
        </>
      }
      cls="keyc on"
    >
      {(
        [
          [t('apps.serverKey'), keys.serverKey, t('setup.serverKeyHint')],
          [t('apps.adminKey'), keys.adminKey, t('setup.adminKeyHint')],
        ] as const
      ).map(([label, k, hint]) => (
        <div key={label}>
          <span class="lbl">{label}</span>
          <div class="keyrow">
            <div class="k shim">{k}</div>
            <Btn sm onClick={async () => toast((await copy(k)) ? t('apps.keyCopied', { label }) : t('common.copyByHand'), t('apps.notShownAgain'), null)}>
              {t('common.copy')}
            </Btn>
          </div>
          <p class="hint" style="margin:0 0 8px">{hint}</p>
        </div>
      ))}
      <span class="lbl">{t('setup.tryIt')}</span>
      <pre class="wcode">{curl}</pre>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
        {done ? (
          <Btn kind="pri" sm href={`#/playground?app=${encodeURIComponent(slug)}&agent=${encodeURIComponent(agent)}`}>
            {t('setup.talk')}
          </Btn>
        ) : null}
        <Btn sm href={appHref(slug)}>
          {t('setup.openApp')}
        </Btn>
      </div>
    </Panel>
  )
}
