/**
 * The playground: talk to an agent as a test subject and inspect every event of the turn. It mints a real session
 * token with the console login and runs real turns (they are priced and recorded under the test subject). The console
 * assistant can drive it (`assistant/drive.ts`): it opens it on an agent, types its message here and reads the answer.
 */
import type { ComponentChildren } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import type { AgentDef, Chart, Citation, ConsoleApplication, Diagram, HistoryMessage, ModuleChoice, PromptVersion, SessionToken } from '@client/core/types'
import { readSse } from '@client/browser/sse'
import { baseUrl, describe, forApp } from '../api'
import { CitedText, Sources } from '../cite'
import { Empty, Fx, HAT, Mage, Motes, oneshot, toast } from '../magic'
import { setTitle, useConsole, useLoad } from '../state'
import { Btn, Hero, Panel, Seg, Switch, Tag } from '../ui'
import { ParamsForm } from './ModuleParams'
import { type PlaygroundReply, registerPlayground } from '../assistant/drive'
import { num, t } from '../i18n'

type Item =
  | { kind: 'user'; text: string }
  | { kind: 'think' }
  | { kind: 'ai'; text: string; visuals: Array<{ chart?: Chart; diagram?: Diagram }>; interrupted?: boolean; cites?: Citation[] }
  | { kind: 'tool'; name: string; status: 'start' | 'done' | 'error' | 'waiting'; ms?: number; t0: number }
  | { kind: 'confirm'; actionId: string; items: string[]; state: 'open' | 'approved' | 'rejected' }
  | { kind: 'ask'; question: string; options: string[]; answered?: boolean }
  | { kind: 'note'; text: string; tone?: 'bad' | 'mu' }

interface Ev {
  seq: number
  type: string
  t: number
  end?: number
  label: string
  raw: Record<string, unknown>
}

/** Event types by kind: a colour of the semantic scheme and a shape, so the kind reads without colour too. */
const KIND: Record<string, 'meta' | 'tool' | 'text' | 'chart' | 'confirm' | 'usage' | 'bad'> = {
  tool: 'tool',
  text: 'text',
  say: 'text',
  chart: 'chart',
  diagram: 'chart',
  navigate: 'chart',
  citation: 'chart',
  confirm: 'confirm',
  ask: 'confirm',
  usage: 'usage',
  error: 'bad',
}
const COL = { meta: 'var(--e-meta)', tool: 'var(--e-tool)', text: 'var(--e-text)', chart: 'var(--e-chart)', confirm: 'var(--e-confirm)', usage: 'var(--e-usage)', bad: 'var(--s-bad)' }
const GL = { meta: '○', tool: '◆', text: '●', chart: '▮', confirm: '▲', usage: '■', bad: '⊘' }

function label(e: Record<string, unknown>): string {
  switch (e.type) {
    case 'conversation':
      return String(e.id ?? '').slice(0, 8)
    case 'tool':
      return `${e.status} ${e.name}`
    case 'text':
      return JSON.stringify(e.delta).slice(0, 60)
    case 'say':
      return `${e.filler ? t('pg.ev.filler') : ''}${String(e.text).slice(0, 60)}`
    case 'chart':
      return `${(e.chart as Chart).type}, ${(e.chart as { title?: string }).title ?? ''}`
    case 'diagram':
      return `${(e.diagram as Diagram).kind}, ${(e.diagram as { title?: string }).title ?? ''}`
    case 'confirm':
      return `${String(e.actionId).slice(0, 10)}: ${(e.items as string[]).join('; ')}`
    case 'ask':
      return String(e.question)
    case 'navigate':
      return String(e.path)
    case 'citation':
      return `[${e.index}] ${String(e.title)}`
    case 'usage':
      return t('pg.ev.usage', { day: (e.dayPct as number | null) ?? t('fmt.none'), month: (e.monthPct as number | null) ?? t('fmt.none') })
    case 'error':
      return `${e.code}: ${e.message}`
    case 'done':
      return t('pg.ev.reason', { reason: String(e.reason) })
    default:
      return ''
  }
}

/** A chart or a diagram as the console draws it (the playground, a conversation under review). */
export function Visual({ v }: { v: { chart?: Chart; diagram?: Diagram } }) {
  if (v.chart?.type === 'kpi')
    return (
      <div class="kpis">
        {v.chart.items.map((k) => (
          <div key={k.label}>
            <span>{k.label}</span>
            <b>{num(k.value)}</b>
            {k.delta != null ? <em class={k.delta < 0 ? 'dn' : ''}>{`${k.delta > 0 ? '+' : ''}${num(k.delta)}%`}</em> : null}
          </div>
        ))}
      </div>
    )
  if (v.chart?.type === 'image')
    return (
      <div class="pgchart">
        <b>{v.chart.title}</b>
        <img class="pgimg" src={`data:${v.chart.mediaType === 'image/jpeg' ? 'image/jpeg' : 'image/png'};base64,${v.chart.data}`} alt={v.chart.title} />
      </div>
    )
  if (v.chart) {
    const pts = v.chart.points
    const max = Math.max(1e-9, ...pts.map((p) => Math.abs(p.value)))
    return (
      <div class="pgchart">
        <b>{v.chart.title}</b> <small>{v.chart.subtitle}</small>
        {pts.map((p) => (
          <div key={p.label} class="pgrow">
            <span>{p.label}</span>
            <div class="bar1">
              <i style={{ width: `${(Math.abs(p.value) / max) * 100}%` }} />
            </div>
            <code>{num(p.value)}</code>
          </div>
        ))}
      </div>
    )
  }
  if (v.diagram) {
    const d = v.diagram
    const rows =
      d.kind === 'steps'
        ? d.steps.map((s) => `${t(s.state === 'done' ? 'pg.step.done' : s.state === 'current' ? 'pg.step.now' : 'pg.step.next')}: ${s.label}`)
        : d.kind === 'tree'
          ? [d.root.label, ...d.children.map((c) => `  ${c.label}`)]
          : d.rows.map((r) => `${r.label}: ${r.left} / ${r.right}`)
    return (
      <div class="pgchart">
        <b>{d.title}</b> <small>{d.kind}</small>
        <pre>{rows.join('\n')}</pre>
      </div>
    )
  }
  return null
}

/** How long the assistant waits for an agent's answer before it reads what came so far (its tool waits up to 120 s). */
const DRIVE_MS = 100_000

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export function Playground({ q }: { q: URLSearchParams }) {
  setTitle(t('page.playground'))
  const c = useConsole()
  const [appSlug, setAppSlug] = useState(q.get('app') ?? c.apps[0]?.slug ?? '')
  // The address can change while the page is open (the assistant opens another application here).
  const wanted = q.get('app')
  useEffect(() => {
    if (wanted) setAppSlug(wanted)
  }, [wanted])
  const app = c.apps.find((a) => a.slug === appSlug) ?? c.apps[0]
  if (!app)
    return (
      <>
        <Hero title={t('page.playground')} sub={t('pg.sub')} />
        <Panel>
          <Empty>
            <b>{t('apps.empty')}</b>
          </Empty>
        </Panel>
      </>
    )
  return <Pg key={app.id} app={app} agentName={q.get('agent')} onApp={setAppSlug} />
}

function Pg({ app, agentName, onApp }: { app: ConsoleApplication; agentName: string | null; onApp: (s: string) => void }) {
  const c = useConsole()
  const api = forApp(app.id)
  const agents = useLoad(() => api.admin.agents.list(), [app.id])
  const prompts = useLoad(() => api.admin.prompts.list().catch(() => [] as PromptVersion[]), [app.id])
  const browserAgents = (agents.data ?? []).filter((a: AgentDef) => (a.channels ?? ['browser']).includes('browser'))
  const [agent, setAgent] = useState(agentName ?? '')
  useEffect(() => {
    if (agentName) setAgent(agentName)
  }, [agentName])
  const current = browserAgents.find((a) => a.name === agent) ?? browserAgents[0]
  const [subject, setSubject] = useState('playground:console')
  const [tags, setTags] = useState('playground')
  const [mode, setMode] = useState<'text' | 'voice'>('text')
  const [view, setView] = useState<'waterfall' | 'raw'>('waterfall')
  const [items, setItems] = useState<Item[]>([])
  const [events, setEvents] = useState<Ev[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [usage, setUsage] = useState<string | null>(null)
  const token = useRef<SessionToken | null>(null)
  /** The test session's prompt modules and locale; a change applies from the next message (a new token). */
  const [choices, setChoices] = useState<Record<string, ModuleChoice>>({})
  const [locale, setLocale] = useState('')
  const optionsChanged = useRef(false)
  const history = useRef<HistoryMessage[]>([])
  const abort = useRef<AbortController | null>(null)
  const thread = useRef<HTMLDivElement>(null)
  /** The assistant is typing here (its `playground` tool). */
  const [driven, setDriven] = useState(false)
  /** What the turn in flight has said so far, for the assistant when its time runs out. */
  const live = useRef<PlaygroundReply | null>(null)

  useEffect(() => () => abort.current?.abort(), [])
  useEffect(() => {
    thread.current?.lastElementChild?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [items])

  const reset = () => {
    abort.current?.abort()
    token.current = null
    history.current = []
    setItems([])
    setEvents([])
    setUsage(null)
  }
  useEffect(reset, [current?.name, subject])
  useEffect(() => setChoices({}), [current?.name])
  useEffect(() => {
    optionsChanged.current = true
  }, [JSON.stringify(choices), locale])
  const sessionModules = (current?.modules ?? []).filter((m) => m.session)
  const promptOf = (name: string, version?: number | null): PromptVersion | undefined => {
    const all = (prompts.data ?? []).filter((x) => x.name === name)
    return version ? all.find((x) => x.version === version) : all.reduce<PromptVersion | undefined>((m, x) => (!m || x.version > m.version ? x : m), undefined)
  }

  const ensureToken = async (): Promise<SessionToken> => {
    const prev = token.current
    if (prev && !optionsChanged.current && prev.expiresAt * 1000 - Date.now() > 20_000) return prev
    const next = await api.mintSessionToken({
      subject: subject.trim() || 'playground:console',
      agent: current!.name,
      tags: tags.split(/[\s,]+/).filter(Boolean),
      ...(prev ? { conversationId: prev.conversationId } : {}),
      ...(Object.keys(choices).length ? { modules: choices } : {}),
      ...(locale ? { locale } : {}),
    })
    optionsChanged.current = false
    token.current = next
    return next
  }

  const run = async (body: Record<string, unknown>, shown: Item | null): Promise<PlaygroundReply | null> => {
    if (!current || busy) return null
    setBusy(true)
    const reply: PlaygroundReply = { application: app.slug, agent: current.name, answer: '', tools: [] }
    live.current = reply
    const t0 = performance.now()
    setEvents([])
    let list: Item[] = [...items.filter((x) => x.kind !== 'think'), ...(shown ? [shown] : []), { kind: 'think' }]
    setItems(list)
    const push = (f: (l: Item[]) => Item[]) => {
      list = f(list)
      setItems(list)
    }
    const evs: Ev[] = []
    const openTools = new Map<string, number>()
    let cites: Citation[] = []
    try {
      const tok = await ensureToken()
      const ctl = new AbortController()
      abort.current = ctl
      const res = await fetch(`${baseUrl}/v1/session/chat`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${tok.token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ mode, ...body, ...(tok.storageMode === 'external' ? { history: history.current } : {}) }),
        signal: ctl.signal,
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.message ?? `HTTP ${res.status}`)
      }
      for await (const m of readSse(res.body!, ctl.signal)) {
        let e: Record<string, unknown>
        try {
          e = JSON.parse(m.data)
        } catch {
          continue
        }
        const at = Math.round(performance.now() - t0)
        const ev: Ev = { seq: Number(e.seq ?? evs.length + 1), type: String(e.type), t: at, label: label(e), raw: e }
        if (e.type === 'tool' && e.status === 'start') openTools.set(String(e.name), evs.length)
        if (e.type === 'tool' && e.status !== 'start') {
          const i = openTools.get(String(e.name))
          if (i != null) evs[i]!.end = at
        }
        evs.push(ev)
        setEvents([...evs])
        const unthink = (l: Item[]) => l.filter((x) => x.kind !== 'think')
        switch (e.type) {
          case 'text': {
            reply.answer += String(e.delta)
            push((l) => {
              const last = l[l.length - 1]
              if (last?.kind === 'ai') return [...l.slice(0, -1), { ...last, text: last.text + String(e.delta), cites }]
              return [...unthink(l), { kind: 'ai', text: String(e.delta), visuals: [], cites }]
            })
            break
          }
          case 'citation':
            cites = [...cites.filter((x) => x.index !== Number(e.index)), e as unknown as Citation]
            break
          case 'chart':
          case 'diagram': {
            const v = e.type === 'chart' ? { chart: e.chart as Chart } : { diagram: e.diagram as Diagram }
            push((l) => {
              const last = l[l.length - 1]
              if (last?.kind === 'ai') return [...l.slice(0, -1), { ...last, visuals: [...last.visuals, v] }]
              return [...unthink(l), { kind: 'ai', text: '', visuals: [v] }]
            })
            break
          }
          case 'tool': {
            const name = String(e.name)
            if (e.status === 'start') reply.tools.push(name)
            if (e.status === 'start') push((l) => [...unthink(l), { kind: 'tool', name, status: 'start', t0: at }, { kind: 'think' }])
            else
              push((l) =>
                l.map((x) => (x.kind === 'tool' && x.name === name && x.status === 'start' ? { ...x, status: e.status as 'done', ms: at - x.t0 } : x)),
              )
            break
          }
          case 'confirm':
            reply.waiting = 'confirm'
            push((l) => [
              ...unthink(l),
              { kind: 'confirm', actionId: String(e.actionId), items: (e.items as string[]) ?? [], state: 'open' },
            ])
            break
          case 'ask':
            reply.waiting = 'ask'
            push((l) => [...unthink(l), { kind: 'ask', question: String(e.question), options: (e.options as string[]) ?? [] }])
            break
          case 'navigate':
            push((l) => [...unthink(l), { kind: 'note', text: t('pg.navigate', { path: String(e.path) }), tone: 'mu' }])
            break
          case 'usage':
            setUsage(t('pg.usage', { day: (e.dayPct as number | null) ?? t('fmt.none'), month: (e.monthPct as number | null) ?? t('fmt.none') }))
            break
          case 'error':
            reply.error = String(e.message)
            push((l) => [...unthink(l), { kind: 'note', text: String(e.message), tone: 'bad' }])
            break
          case 'done':
            if (Array.isArray(e.messages)) history.current = [...history.current, ...(e.messages as HistoryMessage[])]
            break
        }
      }
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        reply.error = describe(err)
        push((l) => [...l.filter((x) => x.kind !== 'think'), { kind: 'note', text: describe(err), tone: 'bad' }])
      }
    } finally {
      push((l) => l.filter((x) => x.kind !== 'think'))
      setBusy(false)
      abort.current = null
      void c.reloadApps()
    }
    return reply
  }

  // The assistant drives the playground while it shows this agent: it types its message in the composer, sends it,
  // and gets the agent's answer back (or what came so far, when its time runs out: the turn goes on here).
  const latest = useRef({ run, busy, reset })
  latest.current = { run, busy, reset }
  const offered = browserAgents.map((a) => a.name).join(',')
  useEffect(() => {
    if (!current) return
    return registerPlayground({
      application: app.slug,
      agent: current.name,
      agents: offered ? offered.split(',') : [],
      send: async (message, fresh) => {
        if (latest.current.busy) throw new Error('The playground is still answering the last message: wait for it, then send again.')
        if (fresh) {
          latest.current.reset()
          await pause(80)
        }
        setDriven(true)
        const steps = Math.min(message.length, 40)
        for (let i = 1; i <= steps; i++) {
          setText(message.slice(0, Math.ceil((message.length * i) / steps)))
          await pause(18)
        }
        await pause(150)
        setText('')
        const done = latest.current.run({ text: message }, { kind: 'user', text: message })
        const late = pause(DRIVE_MS).then(() => (live.current ? { ...live.current, stillAnswering: true } : null))
        const out = await Promise.race([done, late])
        if (!out) throw new Error('The playground could not send the message.')
        return out
      },
    })
  }, [app.slug, current?.name, offered])

  const send = (e?: Event) => {
    e?.preventDefault()
    const said = text.trim()
    if (!said) return
    setText('')
    void run({ text: said }, { kind: 'user', text: said })
  }
  const decide = (actionId: string, approve: boolean, el: Element) => {
    setItems((l) => l.map((x) => (x.kind === 'confirm' && x.actionId === actionId ? { ...x, state: approve ? 'approved' : 'rejected' } : x)))
    if (approve) {
      oneshot(el, 'sparkles', 110, 2600) // already playing when it started on pointerdown
      toast(t('pg.confirmed'), t('pg.resumes'), null)
    }
    void run({ confirm: { actionId, approve } }, null)
  }

  const total = Math.max(1, ...events.map((e) => e.end ?? e.t + 40))
  const thinking = (
    <div class="think">
      <Mage k="ember" size={56} />
      <span>{t('pg.thinking', { name: current?.name ?? '' })}</span>
    </div>
  )
  const rendered: ComponentChildren[] = items.map((x, i) => {
    switch (x.kind) {
      case 'user':
        return (
          <div key={i} class="msg me">
            {x.text}
          </div>
        )
      case 'think':
        return <div key={i}>{thinking}</div>
      case 'ai':
        return (
          <div key={i} style="display:flex;flex-direction:column;gap:6px;align-self:stretch">
            <div class="who">
              <img src={HAT} alt="" />
              {current?.name}
            </div>
            <div class="msg ai" style="white-space:pre-wrap">
              <CitedText text={x.text} cites={x.cites ?? []} />
              <Sources text={x.text} cites={x.cites ?? []} />
              {x.visuals.map((v, k) => (
                <Visual key={k} v={v} />
              ))}
            </div>
          </div>
        )
      case 'tool':
        return (
          <span key={i} class="toolchip">
            {x.status === 'start' ? (
              <Motes />
            ) : (
              <span class="ok" style={x.status === 'error' ? 'background:var(--s-bad)' : ''} />
            )}
            {x.name}
            {x.ms != null ? <span>{x.ms} ms</span> : null}
          </span>
        )
      case 'confirm':
        return (
          <div key={i} class="confirm">
            <span style="display:flex;align-items:center;gap:6px">
              <Fx k="flameLoop" size={30} cls="btnflame" />
              <Tag tone="gold">{t('pg.confirmChange')}</Tag>
            </span>
            <div class="q">{x.items.join('; ')}</div>
            {x.state === 'open' ? (
              <div class="a">
                <Btn kind="pri" sm fx="confirm" disabled={busy} onClick={(e) => decide(x.actionId, true, e.currentTarget)}>
                  {t('pg.confirm')}
                </Btn>
                <Btn sm disabled={busy} onClick={(e) => decide(x.actionId, false, e.currentTarget)}>
                  {t('common.cancel')}
                </Btn>
              </div>
            ) : (
              <Tag tone={x.state === 'approved' ? 'ok' : ''}>{t(x.state === 'approved' ? 'pg.confirmed' : 'pg.cancelled')}</Tag>
            )}
          </div>
        )
      case 'ask':
        return (
          <div key={i} class="confirm" style="box-shadow:0 0 0 1px var(--hair)">
            <div class="q" style="margin-top:0">
              {x.question}
            </div>
            <div class="a" style="flex-wrap:wrap">
              {x.options.map((o) => (
                <Btn key={o} sm disabled={busy || x.answered} onClick={() => {
                  setItems((l) => l.map((y, k) => (k === i ? { ...x, answered: true } : y)))
                  void run({ text: o }, { kind: 'user', text: o })
                }}>
                  {o}
                </Btn>
              ))}
            </div>
          </div>
        )
      case 'note':
        return (
          <div key={i} class="hint" style={`align-self:flex-start;${x.tone === 'bad' ? 'color:var(--s-bad)' : ''}`}>
            {x.text}
          </div>
        )
    }
  })

  return (
    <>
      <Hero
        title={t('page.playground')}
        sub={t('pg.subFull')}
        actions={
          <Btn
            onClick={() => {
              reset()
              toast(t('pg.newConvo'), current ? t('pg.with', { name: current.name }) : t('common.justNow'), null)
            }}
          >
            {t('pg.newConvo')}
          </Btn>
        }
      />
      <div class="pgbar">
        <select class="in" value={app.slug} onChange={(e) => onApp(e.currentTarget.value)} aria-label={t('pg.application')}>
          {c.apps.map((a) => (
            <option key={a.id} value={a.slug}>
              {a.name}
            </option>
          ))}
        </select>
        <select class="in" value={current?.name ?? ''} onChange={(e) => setAgent(e.currentTarget.value)} aria-label={t('pg.agent')}>
          {browserAgents.map((a) => (
            <option key={a.name} value={a.name}>
              {a.name}
            </option>
          ))}
        </select>
        {driven ? <Tag tone="gold">{t('pg.byAssistant')}</Tag> : null}
        {current ? <Tag tone="ok">{t('pg.live', { version: (current as AgentDef & { version: string }).version.slice(0, 7) })}</Tag> : null}
        <input class="in mono" style="width:190px" value={subject} onChange={(e) => setSubject(e.currentTarget.value)} aria-label={t('pg.subject')} />
        <input class="in mono" style="width:120px" value={tags} onInput={(e) => setTags(e.currentTarget.value)} aria-label={t('pg.tags')} />
        <Seg options={['text', 'voice'] as const} labels={{ text: t('pg.text'), voice: t('pg.voice') }} value={mode} onChange={setMode} />
        <select class="in" style="width:auto" value={locale} onChange={(e) => setLocale(e.currentTarget.value)} aria-label={t('pg.locale')}>
          <option value="">{t('pg.localeApp')}</option>
          <option value="en">{t('pg.localeEn')}</option>
          <option value="it">{t('pg.localeIt')}</option>
          <option value="es">{t('pg.localeEs')}</option>
          <option value="fr">{t('pg.localeFr')}</option>
          <option value="de">{t('pg.localeDe')}</option>
          <option value="ro">{t('pg.localeRo')}</option>
          <option value="sv">{t('pg.localeSv')}</option>
        </select>
      </div>
      {sessionModules.length ? (
        <Panel title={t('pg.modules')}>
          <p class="hint">{t('pg.modulesHint')}</p>
          {sessionModules.map((m) => {
            const choice = choices[m.name] ?? {}
            const on = choice.on ?? m.on !== false
            const prompt = promptOf(m.name, m.version)
            const update = (patch: ModuleChoice) => setChoices({ ...choices, [m.name]: { ...choice, ...patch } })
            return (
              <div key={m.name} class="opt" style="display:block">
                <div class="flags">
                  <Switch label={m.name} on={on} onChange={(v) => update({ on: v })} />
                  {prompt?.description ? <small class="hint">{prompt.description}</small> : null}
                </div>
                {on && prompt?.parameters ? (
                  <ParamsForm
                    schema={prompt.parameters}
                    values={(choice.params ?? m.params ?? {}) as Record<string, unknown>}
                    onChange={(v) => update({ params: v })}
                    idPrefix={`pg-${m.name}`}
                  />
                ) : null}
              </div>
            )
          })}
        </Panel>
      ) : null}
      <div class="pg">
        <Panel>
          <div class="thread" ref={thread}>
            {agents.loading && !agents.data ? (
              <div class="who">{t('pg.loadingAgents')}</div>
            ) : !current ? (
              <Empty>
                <b>{t('pg.noBrowserAgent')}</b>
                <br />
                {t('pg.serverOnly')}
              </Empty>
            ) : (
              <div class="who">
                <img src={HAT} alt="" />
                {t('pg.ready', { name: current.name })}
                {usage ? ` ${usage}` : ''}
                {mode === 'voice' ? t('pg.voiceNote') : ''}
              </div>
            )}
            {rendered}
          </div>
          <form class="composer" onSubmit={send}>
            <input value={text} onInput={(e) => setText(e.currentTarget.value)} placeholder={t('pg.ask')} aria-label={t('pg.message')} disabled={!current} />
            <Btn kind="pri" type="submit" busy={busy ? t('pg.running') : false} flame disabled={!current}>
              {t('pg.send')}
            </Btn>
          </form>
        </Panel>
        <Panel
          title={t('pg.events')}
          actions={<Seg options={['waterfall', 'raw'] as const} labels={{ waterfall: t('pg.waterfall'), raw: t('pg.raw') }} value={view} onChange={setView} />}
        >
          {!events.length ? (
            <ol class="wf">
              <li style="grid-template-columns:1fr;color:var(--mu)">{t('pg.sendToWatch')}</li>
            </ol>
          ) : view === 'raw' ? (
            <pre class="raw">{events.map((e) => JSON.stringify(e.raw)).join('\n')}</pre>
          ) : (
            <ol class="wf">
              {events.map((e) => {
                const kind = KIND[e.type] ?? 'meta'
                const col = COL[kind]
                const left = (e.t / total) * 100
                const width = Math.max(1.5, (((e.end ?? e.t + 40) - e.t) / total) * 100)
                return (
                  <li key={`${e.seq}${e.type}`}>
                    <span class="q">{e.seq}</span>
                    <span class="ty" style={{ color: col }}>
                      <span class="gl" aria-hidden="true">
                        {GL[kind]}
                      </span>
                      {e.type}
                    </span>
                    <span class="lane">
                      <i style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%`, background: col }} />
                      <span>{e.t} ms</span>
                    </span>
                    <small>{e.label}</small>
                  </li>
                )
              })}
            </ol>
          )}
        </Panel>
      </div>
    </>
  )
}
