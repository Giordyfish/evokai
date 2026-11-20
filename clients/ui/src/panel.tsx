/**
 * The chat panel and its launcher: answers in Markdown, tool activity, charts and schemas, questions and
 * confirmations, the usage ring (shares only), the conversation list, starter questions, feedback, dictation and voice
 * mode (inside the panel, and minimized into the launcher orb with a speech bubble), the header menu with what the
 * agents remember, and the one-time question about memory. One orb travels between the launcher, the header and the
 * middle of the voice layer (`flow.ts`, `travel.tsx`).
 */
import type { TargetedKeyboardEvent, TargetedMouseEvent, VNode } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'

import type { ChatMessage, Citation, Part } from '@evokai/evokai-client/browser'
import type { Controller, ControllerState } from './controller'
import { useUi } from './context'
import { citedIndexes, Markdown } from './markdown'
import { Flow, settled, viewOf, type Look, type Rest, type View } from './flow'
import { reducedMotion } from './orb'
import { lineCut, stagePhase, Subtitles, VoiceControls, voiceStatus } from './stage'
import { spotOf, TravelOrb, type TravelHandle } from './travel'
import { Icon, type IconName } from './icons'
import { ConnectCard, ConnectionsView } from './connections'
import { MemoryAsk, MemoryView } from './memory'
import { fill } from './strings'
import type { OrbColors } from './theme'
import { UsageRing } from './usage'
import { AskCard, ChartView, ConfirmCard, DiagramView } from './visuals'

export function useController(c: Controller): ControllerState {
  const [s, set] = useState(c.state)
  useEffect(() => c.subscribe(set), [c])
  return s
}

export interface PanelProps {
  controller: Controller
  open: boolean
  onOpen: (open: boolean) => void
  /** The voice button and the stage (default true). */
  voice: boolean
  logo?: string | undefined
  orb?: Partial<OrbColors> | undefined
  /** The agent asked to open a page (or a link in an answer was clicked). */
  onNavigate: (path: string) => void
  /** Where keys are listened to (the shadow root's host document). */
  keyTarget: EventTarget | null
  /** The view changed (closed, chat, voice, minimized). */
  onView?: ((view: View) => void) | undefined
}

const KNOWLEDGE_TOOL = 'knowledge_search'

/** Where a cited passage is, in words: «p. 3 · Art. 2». */
export function whereLabel(c: Citation, pageLabel: string): string {
  const out: string[] = []
  if (c.location.page != null) out.push(fill(pageLabel, { page: c.location.pageEnd ? `${c.location.page}-${c.location.pageEnd}` : String(c.location.page) }))
  if (c.location.section) out.push(c.location.section)
  return out.join(' · ')
}

/** The citations a message's answer actually uses, in order of first use. */
export function usedCitations(m: ChatMessage): Citation[] {
  const all = new Map((m.citations ?? []).map((c) => [c.index, c]))
  const text = m.parts.map((p) => (p.kind === 'text' ? p.text : '')).join('\n')
  return citedIndexes(text)
    .map((n) => all.get(n))
    .filter((c): c is Citation => !!c)
}

/** A citation marker: the number in superscript, linking to the source when it has an address. */
function Cite({ c }: { c: Citation }) {
  const { s } = useUi()
  const label = fill(s.openSource, { title: c.title })
  return (
    <sup class="cite" part="citation" data-index={c.index}>
      {c.url ? (
        <a href={c.url} target="_blank" rel="noopener noreferrer" title={c.title} aria-label={label}>
          {c.index}
        </a>
      ) : (
        <span title={c.title}>{c.index}</span>
      )}
    </sup>
  )
}

/** The sources under an answer: each cited passage's document, place and the start of its text. */
function Sources({ cites }: { cites: Citation[] }) {
  const { s } = useUi()
  if (!cites.length) return null
  return (
    <div class="sources" part="sources">
      <div class="sources-title">{s.sources}</div>
      <ol>
        {cites.map((c) => {
          const where = whereLabel(c, s.sourcePage)
          return (
            <li key={c.index} part="source" data-index={c.index} value={c.index}>
              <span class="src-n" aria-hidden="true">
                {c.index}
              </span>
              <span class="src-body">
                {c.url ? (
                  <a class="src-title" href={c.url} target="_blank" rel="noopener noreferrer">
                    {c.title}
                  </a>
                ) : (
                  <span class="src-title">{c.title}</span>
                )}
                {where ? <span class="src-where">{where}</span> : null}
                {c.snippet ? <span class="src-snippet">{c.snippet}</span> : null}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function Parts({ m, p }: { m: ChatMessage; p: { busy: boolean; openAsk: string | null; c: Controller; st: ControllerState; keyTarget: EventTarget | null } }) {
  const { s, toolLabel } = useUi()
  const cites = new Map((m.citations ?? []).map((c) => [c.index, c]))
  const label = (name: string) => {
    const l = toolLabel(name)
    return name === KNOWLEDGE_TOOL && l === name ? s.searching : l
  }
  return (
    <>
      {m.parts.map((part: Part, i) => {
        switch (part.kind) {
          case 'text':
            return <Markdown key={i} text={part.text} cites={cites} cite={(c, k) => <Cite key={k} c={c} />} />
          case 'tool':
            return (
              <div key={i} class={`tool ${part.status}`} part="tool" data-status={part.status}>
                <i aria-hidden="true" />
                {label(part.name)}
                {part.status === 'start' ? s.toolRunning : part.status === 'error' ? `, ${s.toolFailed}` : ''}
              </div>
            )
          case 'chart':
            return <ChartView key={i} chart={part.chart} />
          case 'diagram':
            return <DiagramView key={i} diagram={part.diagram} />
          case 'ask':
            return (
              <AskCard
                key={i}
                question={part.question}
                options={part.options}
                multiple={part.multiple}
                other={part.other}
                answered={part.answered}
                active={part.askId === p.openAsk}
                disabled={p.busy}
                keyTarget={p.keyTarget}
                onAnswer={(labels) => void p.c.session.answer(part.askId, labels)}
              />
            )
          case 'navigate':
            return (
              <div key={i} class="nav" part="navigation">
                → {part.path}
              </div>
            )
          case 'connect':
            return <ConnectCard key={i} provider={part.provider} title={part.title} c={p.c} state={p.st} />
          case 'confirm':
            return <ConfirmCard key={i} items={part.items} why={part.why} state={part.state} disabled={p.busy} onDecide={(ok) => void p.c.session.confirm(part.actionId, ok)} />
          case 'error':
            return (
              <div key={i} class="err" part="error" role="alert">
                {part.message}
              </div>
            )
        }
      })}
      <Sources cites={usedCitations(m)} />
    </>
  )
}

export interface MenuItem {
  id: string
  label: string
  icon: IconName
  part: string
  onSelect: () => void
}

let menus = 0

/**
 * The header menu: a button that opens a short list of actions. Arrow keys, Home and End move between them, Escape
 * closes it and gives the focus back to the button, Tab or a click outside closes it.
 */
export function HeaderMenu({ items, buttonRef }: { items: MenuItem[]; buttonRef: { current: HTMLButtonElement | null } }): VNode {
  const { s } = useUi()
  const [open, setOpen] = useState(false)
  const [id] = useState(() => `ek-menu-${++menus}`)
  const list = useRef<HTMLDivElement>(null)
  const entries = () => [...(list.current?.querySelectorAll<HTMLButtonElement>('[role=menuitem]') ?? [])]
  const close = (focus = true) => {
    setOpen(false)
    if (focus) buttonRef.current?.focus()
  }
  useEffect(() => {
    if (open) entries()[0]?.focus()
  }, [open])
  const onKey = (e: TargetedKeyboardEvent<HTMLDivElement>) => {
    const all = entries()
    const at = all.indexOf((list.current?.getRootNode() as ShadowRoot | Document | undefined)?.activeElement as HTMLButtonElement)
    const go = (i: number) => {
      e.preventDefault()
      all[(i + all.length) % all.length]?.focus()
    }
    if (e.key === 'ArrowDown') go(at + 1)
    else if (e.key === 'ArrowUp') go(at - 1)
    else if (e.key === 'Home') go(0)
    else if (e.key === 'End') go(all.length - 1)
    else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      close()
    } else if (e.key === 'Tab') close(false)
  }
  return (
    <span
      class="menuwrap"
      onFocusOut={(e) => {
        const to = e.relatedTarget as Node | null
        if (open && to && !e.currentTarget.contains(to)) setOpen(false)
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        class="ib"
        part="header-button menu-button"
        aria-label={s.menu}
        title={s.menu}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && !open) {
            e.preventDefault()
            setOpen(true)
          } else if (e.key === 'Escape' && open) {
            e.preventDefault()
            e.stopPropagation()
            close()
          }
        }}
      >
        <Icon d="more" />
      </button>
      {open ? (
        <>
          <div class="scrim" aria-hidden="true" onClick={() => close(false)} />
          <div ref={list} id={id} class="menu" part="menu" role="menu" aria-label={s.menu} onKeyDown={onKey}>
            {items.map((it) => (
              <button
                key={it.id}
                type="button"
                role="menuitem"
                tabIndex={-1}
                part={`menu-item ${it.part}`}
                onClick={() => {
                  setOpen(false)
                  it.onSelect()
                }}
              >
                <Icon d={it.icon} />
                <span>{it.label}</span>
              </button>
            ))}
          </div>
        </>
      ) : null}
    </span>
  )
}

/** The minimized bubble's line: the end of what was said and the start of what follows, at word boundaries. */
export function lineWindow(text: string, cut: number, before = 46, after = 18): { said: string; tosay: string } {
  let said = text.slice(0, cut)
  let tosay = text.slice(cut)
  if (said.length > before) {
    const sp = said.indexOf(' ', said.length - before)
    said = sp < 0 ? said.slice(-before) : said.slice(sp + 1)
  }
  if (tosay.length > after) {
    const sp = tosay.indexOf(' ', after)
    tosay = sp < 0 ? tosay : tosay.slice(0, sp)
  }
  return { said, tosay }
}

/** Beside the launcher while voice goes on with the panel minimized: the status, the line, the latest chart. */
function VoiceBubble({ st, chart, onOpen }: { st: ControllerState; chart: string | null; onOpen: () => void }): VNode {
  const { s } = useUi()
  const phase = stagePhase(st)
  const small = phase === 'listening' || phase === 'thinking'
  const line = st.line
  const w = phase === 'speaking' && line.text ? lineWindow(line.text, lineCut(line)) : null
  return (
    <div class={`vbubble ${small ? 'small' : ''}`} part="voice-bubble" data-phase={phase}>
      <div class="vst" part="voice-bubble-status">
        <span aria-live="polite">{voiceStatus(s, st, true)}</span>
        {small && !st.muted ? (
          <i class="hop" aria-hidden="true">
            <b />
            <b />
            <b />
          </i>
        ) : null}
      </div>
      {phase === 'user' && st.heard ? (
        <p class="vtxt" part="voice-bubble-text">
          <span class="sub heard">{st.heard}</span>
        </p>
      ) : w ? (
        <p class="vtxt" part="voice-bubble-text">
          <span class="said">{w.said}</span>
          <span class="tosay">{w.tosay}</span>
        </p>
      ) : null}
      {chart && phase === 'speaking' ? (
        <div class="vchip" part="voice-bubble-chart">
          <Icon d="chart" size={14} />
          <span class="vct">{chart}</span>
          <button type="button" part="voice-bubble-open" onClick={onOpen}>
            {s.openChart}
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** Animation support: without it (or with reduced motion) every change of view is instant. */
const canMove = () => typeof requestAnimationFrame === 'function' && typeof Element !== 'undefined' && typeof Element.prototype.animate === 'function' && !reducedMotion()

/** Where the focus goes once a view is on screen (`opened`: the composer, unless the person already moved inside). */
type FocusAim = 'opened' | 'voiceButton' | 'voice' | 'launcher'

export function Panel(props: PanelProps): VNode {
  const { controller: c, open, onOpen } = props
  const st = useController(c)
  const { s, names } = useUi()
  const t = (x: string) => fill(x, { title: names.title, app: names.app })
  const [input, setInput] = useState('')
  /** Editing the last question: the next send rewrites it. */
  const [editing, setEditing] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const voiceBtn = useRef<HTMLButtonElement>(null)
  const launcher = useRef<HTMLButtonElement>(null)
  const menuBtn = useRef<HTMLButtonElement | null>(null)
  const launchSlot = useRef<HTMLSpanElement>(null)
  const headSlot = useRef<HTMLSpanElement>(null)
  const centerSlot = useRef<HTMLSpanElement>(null)
  const travel = useRef<TravelHandle | null>(null)
  const aim = useRef<FocusAim | null>(null)
  const [, slotsChanged] = useState(0)
  const ss = st.session
  const busy = ss.busy
  // What a rewrite is replacing is hidden until the service has replaced it (or refused).
  const msgs = ss.messages.filter((m) => !m.replacing)

  // The view: voice started or shown in this panel goes on minimized when the panel closes.
  const own = useRef(false)
  // Voice started from the launcher's own action: it goes on minimized, as if started in the panel.
  const startMini = useRef(false)
  if (!st.voiceOn) own.current = startMini.current
  else if (open || startMini.current) {
    own.current = true
    startMini.current = false
  }
  const target = viewOf(open, st.voiceOn, own.current)

  const slot = (r: Rest) => (r === 'launcher' ? launchSlot.current : r === 'head' ? headSlot.current : centerSlot.current)
  const [look, setLook] = useState<Look>(() => settled(target))
  const flow = useRef<Flow | null>(null)
  if (!flow.current)
    flow.current = new Flow(target, {
      show: (l) =>
        new Promise<void>((r) => {
          setLook(l)
          setTimeout(r, 0)
        }),
      spot: (r) => spotOf(slot(r)),
      fly: (a, b, ms) => travel.current?.fly(a, b, ms) ?? Promise.resolve(),
      wait: (ms) => new Promise<void>((r) => setTimeout(r, ms)),
      motion: canMove,
    })
  // Changes that arrive together (voice stops and the panel closes, in two renders) make one transition: the timer
  // reads the latest view when it fires.
  const latest = useRef(target)
  latest.current = target
  useEffect(() => {
    const id = setTimeout(() => flow.current?.go(latest.current), 0)
    return () => clearTimeout(id)
  }, [target])
  const onView = props.onView
  useEffect(() => onView?.(target), [target, onView])

  // Where the focus goes after a change of view, once the place is on screen.
  const prev = useRef(target)
  useEffect(() => {
    const was = prev.current
    prev.current = target
    if (was === target) return
    const inside = !!(rootRef.current?.getRootNode() as ShadowRoot | Document | undefined)?.activeElement
    if (target === 'chat') aim.current = was === 'voice' ? 'voiceButton' : 'opened'
    else if (target === 'voice') aim.current = 'voice'
    else aim.current = inside || was === 'minimized' ? 'launcher' : null
  }, [target])
  useEffect(() => {
    const a = aim.current
    if (!a) return
    let el: HTMLElement | null | undefined = null
    if (a === 'launcher') el = look.box === 'none' ? launcher.current : null
    else if ((look.box === 'open' || look.box === 'in') && look.layer === (a === 'voice' ? 'voice' : 'chat'))
      el = a === 'opened' ? box.current : a === 'voiceButton' ? voiceBtn.current : panelRef.current?.querySelector<HTMLElement>('.layer.voice [part~="stage-controls"] button')
    if (!el) return
    aim.current = null
    const now = (rootRef.current?.getRootNode() as ShadowRoot | Document | undefined)?.activeElement
    if (a === 'opened' && now && now !== launcher.current && panelRef.current?.contains(now)) return
    el.focus()
  }, [look])

  // The box opens and closes around the orb: its transform origin is the orb's place, measured before it moves.
  useLayoutEffect(() => {
    const p = panelRef.current
    if (!p || (look.box !== 'pre' && look.box !== 'out')) return
    const sp = spotOf(slot(look.layer === 'voice' ? 'center' : 'head'))
    const pr = p.getBoundingClientRect()
    if (sp) p.style.setProperty('--ek-origin', `${(sp.x - pr.left).toFixed(1)}px ${(sp.y - pr.top).toFixed(1)}px`)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [look.box])

  // A slotted launcher, avatar or stage orb stands in for the orb where it rests.
  useEffect(() => {
    const r = rootRef.current
    const on = () => slotsChanged((n) => n + 1)
    r?.addEventListener('slotchange', on)
    return () => r?.removeEventListener('slotchange', on)
  }, [])
  const slotted = (name: string) => {
    const host = (rootRef.current?.getRootNode() as ShadowRoot | undefined)?.host
    return !!host && [...host.children].some((e) => e.getAttribute('slot') === name)
  }
  const custom = (r: Look['orb']) => (r === 'launcher' ? slotted('launcher') : r === 'head' ? !!props.logo || slotted('avatar') : r === 'center' ? slotted('stage-orb') : false)

  useEffect(() => {
    if (open) void c.open()
  }, [open, c])
  useEffect(() => {
    if (st.dictating) setInput(st.dictated)
  }, [st.dictated, st.dictating])
  const shown = look.box !== 'none'
  useLayoutEffect(() => {
    const el = list.current
    if (el && typeof el.scrollTo === 'function') el.scrollTo({ top: el.scrollHeight })
  }, [msgs, shown, st.memoryView])

  const submit = (e?: Event) => {
    e?.preventDefault()
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    if (editing) {
      setEditing(false)
      void c.rewrite(text)
    } else void c.send(text)
  }
  const onLink = (e: TargetedMouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement).closest?.('a')
    if (!a) return
    const href = a.getAttribute('href') ?? ''
    if (href.startsWith('/') && !href.startsWith('//')) {
      e.preventDefault()
      props.onNavigate(href)
    }
  }
  const leaveVoice = () => void c.stopVoice()
  const closePanel = () => {
    if (st.voiceOn) void c.stopVoice()
    onOpen(false)
  }
  const phaseText = st.voiceOn ? voiceStatus(s, st) : busy ? s.working : s.tagline
  const voiceTitle = st.support === 'unsupported' ? s.recognitionUnsupported : st.support === 'downloadable' ? s.recognitionDownloadable : ''
  const noSpeech = st.support === 'unsupported' || st.support === 'checking'
  const openAsk = ss.openAsk?.askId ?? null
  const last = msgs.at(-1)
  const waiting = busy && (!last || last.role === 'user' || last.parts.length === 0)
  const visuals = (() => {
    const a = [...msgs].reverse().find((m) => m.role === 'assistant')
    return (a?.parts ?? []).filter((p) => p.kind === 'chart' || p.kind === 'diagram').slice(-2)
  })()
  const cards = visuals.length
    ? visuals.map((p, i) => (
        <div key={i} class="vcard" part="stage-card">
          {p.kind === 'chart' ? <ChartView chart={p.chart} /> : p.kind === 'diagram' ? <DiagramView diagram={p.diagram} /> : null}
        </div>
      ))
    : null
  const lastVisual = visuals.at(-1)
  const chartTitle = lastVisual ? (lastVisual.kind === 'chart' ? lastVisual.chart.title : lastVisual.kind === 'diagram' ? lastVisual.diagram.title : '') || s.details : null

  const mem = st.memory
  const menu: MenuItem[] = [
    ...(mem && mem.mode !== 'off'
      ? [{ id: 'memory', label: t(s.memoryMenu), icon: 'memory' as const, part: 'memory-menu-item', onSelect: () => void c.openMemory() }]
      : []),
    ...(st.connections?.length
      ? [{ id: 'connections', label: s.connectionsMenu, icon: 'link' as const, part: 'connections-menu-item', onSelect: () => void c.openConnections() }]
      : []),
  ]
  const closeMemory = () => {
    c.closeMemory()
    setTimeout(() => (menuBtn.current ?? box.current)?.focus(), 0)
  }
  const closeConnections = () => {
    c.closeConnections()
    setTimeout(() => (menuBtn.current ?? box.current)?.focus(), 0)
  }
  const answered = msgs.some((m) => m.role === 'assistant' && m.parts.some((p) => p.kind === 'text'))
  const askMemory = !!mem?.ask && mem.mode === 'ask' && !st.memoryAnswered && answered && !busy && !st.voiceOn
  const voiceLayer = look.layer === 'voice'
  const phase = stagePhase(st)
  const here = (r: Rest) => (look.orb === r ? '' : undefined)

  return (
    <div ref={rootRef} class="root" data-view={target}>
      <div class="dock" part="dock" data-mini={look.bubble ? '' : undefined} data-idle={target === 'closed' && !noSpeech ? '' : undefined}>
        {look.bubble ? <VoiceBubble st={st} chart={chartTitle} onOpen={() => onOpen(true)} /> : null}
        <button
          ref={launcher}
          type="button"
          class={`launcher ${open ? 'is-open' : ''}`}
          part="launcher"
          data-hidden={shown ? '' : undefined}
          aria-label={open ? s.close : target === 'minimized' ? s.restoreHint : t(s.open)}
          aria-expanded={open}
          title={target === 'minimized' ? s.restoreHint : names.title}
          onClick={() => onOpen(!open)}
        >
          <span ref={launchSlot} class="oslot launch" part="orb-slot" data-rest="launcher" data-here={here('launcher')}>
            <slot name="launcher" />
          </span>
        </button>
        {look.bubble ? (
          <div class="acts" part="voice-actions" role="group" aria-label={s.voiceActions}>
            <button
              type="button"
              class={`ab ${st.muted ? 'muted' : ''}`}
              part="voice-action mute-action"
              aria-pressed={st.muted}
              aria-label={st.muted ? s.unmute : s.mute}
              title={st.muted ? s.unmute : s.mute}
              onClick={() => c.setMuted(!st.muted)}
            >
              <Icon d="mic" />
            </button>
            <button type="button" class="ab" part="voice-action restore-action" aria-label={s.restoreHint} title={s.restoreHint} onClick={() => onOpen(true)}>
              <Icon d="restore" />
            </button>
            <button type="button" class="ab fin" part="voice-action end-action" aria-label={s.endVoice} title={s.endVoice} onClick={() => void c.stopVoice()}>
              <Icon d="x" />
            </button>
          </div>
        ) : target === 'closed' && !noSpeech ? (
          <div class="acts" part="launcher-actions">
            <button
              type="button"
              class="ab"
              part="voice-mini-action"
              title={t(s.voiceModeHint)}
              onClick={() => {
                // From the click itself (audio needs the gesture); a refusal leaves the launcher as it was.
                startMini.current = true
                void c.toggleVoice().finally(() => {
                  if (!c.state.voiceOn) startMini.current = false
                })
              }}
            >
              <Icon d="wave" />
            </button>
          </div>
        ) : null}
      </div>
      {shown ? (
        <section
          ref={panelRef}
          class="panel"
          part="panel"
          role="dialog"
          aria-modal="false"
          aria-labelledby="ek-title"
          data-box={look.box}
          data-layer={look.layer}
          inert={look.box === 'pre' || look.box === 'out'}
        >
          <div class="aurora" part="aurora" aria-hidden="true">
            <i />
            <i />
            <i />
          </div>
          <header part="header">
            <span ref={headSlot} class="oslot head" part="orb-slot" data-rest="head" data-here={here('head')}>
              <slot name="avatar">{props.logo ? <img class="logo" part="avatar" src={props.logo} alt="" /> : null}</slot>
            </span>
            <div class="ht">
              <div id="ek-title" class="title" part="title">
                {names.title}
              </div>
              <div class="sub tag" part="tagline" aria-live="polite">
                {phaseText}
              </div>
            </div>
            {ss.usage && !voiceLayer ? <UsageRing usage={ss.usage} /> : null}
            <div class="hb" part="header-buttons">
              {voiceLayer ? (
                <button type="button" class="ib min" part="header-button minimize-button" aria-label={s.minimize} title={s.minimizeHint} onClick={() => onOpen(false)}>
                  <Icon d="minimize" />
                </button>
              ) : (
                <>
                  {menu.length ? <HeaderMenu items={menu} buttonRef={menuBtn} /> : null}
                  {c.hasHistory ? (
                    <button type="button" class="ib" part="header-button history-button" aria-label={s.history} aria-expanded={!!st.history} onClick={() => void c.toggleHistory()}>
                      <Icon d="history" />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    class="ib"
                    part="header-button new-button"
                    aria-label={s.newChat}
                    onClick={() => {
                      if (st.memoryView) c.closeMemory()
                      if (st.connectionsView) c.closeConnections()
                      void c.newConversation()
                      box.current?.focus()
                    }}
                  >
                    <Icon d="plus" />
                  </button>
                </>
              )}
              <button type="button" class="ib" part="header-button close-button" aria-label={s.close} onClick={closePanel}>
                <Icon d="x" />
              </button>
            </div>
          </header>

          <div class="body">
            <div class="layer chat" inert={voiceLayer}>
              {st.history ? (
                <nav class="history" part="history" aria-label={s.history}>
                  {st.history.length === 0 ? (
                    <p class="sub">{s.noHistory}</p>
                  ) : (
                    st.history.map((h) => (
                      <button
                        key={h.id}
                        type="button"
                        part="history-item"
                        aria-current={h.id === ss.conversationId ? 'true' : undefined}
                        onClick={() => {
                          if (st.memoryView) c.closeMemory()
                          if (st.connectionsView) c.closeConnections()
                          void c.switchTo(h.id)
                        }}
                      >
                        {h.title || s.untitled}
                      </button>
                    ))
                  )}
                </nav>
              ) : null}

              {st.memoryView ? (
                <MemoryView controller={c} state={st} onClose={closeMemory} />
              ) : st.connectionsView ? (
                <ConnectionsView controller={c} state={st} onClose={closeConnections} />
              ) : (
                <>
                  <div ref={list} class="list" part="messages" role="log" aria-live="polite" aria-relevant="additions" onClick={onLink}>
                    {msgs.length === 0 ? (
                      <div class="starters" part="starters">
                        <slot name="empty" />
                        {st.error ? (
                          <div class="err" role="alert">
                            {s.unreachable}
                          </div>
                        ) : null}
                        {st.suggestions.map((q) => (
                          <button key={q} type="button" class="starter" part="starter" onClick={() => void c.send(q)}>
                            {q}
                          </button>
                        ))}
                      </div>
                    ) : (
                      msgs.map((m) => (
                        <div key={m.id} class={`msg ${m.role}`} part={`message ${m.role}-message`}>
                          {m.role === 'user' ? (
                            m.answer ? (
                              <div class="answers" part="answers">
                                {m.parts
                                  .flatMap((p) => (p.kind === 'text' ? p.text.split(', ') : []))
                                  .map((l, i) => (
                                    <span key={i} class="chip on" part="answer-chip">
                                      {l}
                                    </span>
                                  ))}
                              </div>
                            ) : (
                              <>
                                <div class="bubble" part="user-bubble">
                                  {m.parts.map((p, i) => (p.kind === 'text' ? <span key={i}>{p.text}</span> : null))}
                                </div>
                                {m.rewritable && !busy && !editing ? (
                                  <div class="fb" part="edit-message">
                                    <button
                                      type="button"
                                      aria-label={s.edit}
                                      onClick={() => {
                                        setEditing(true)
                                        setInput(m.parts.map((p) => (p.kind === 'text' ? p.text : '')).join(''))
                                        box.current?.focus()
                                      }}
                                    >
                                      <Icon d="edit" size={14} />
                                    </button>
                                  </div>
                                ) : null}
                              </>
                            )
                          ) : (
                            <>
                              <Parts m={m} p={{ busy, openAsk, c, st, keyTarget: props.keyTarget }} />
                              {m.messageId && !busy && m.parts.some((p) => p.kind === 'text') ? (
                                <div class="fb" part="feedback">
                                  <button type="button" aria-label={s.helpful} aria-pressed={m.feedback === 1} onClick={() => void c.session.feedback(m.messageId as string, m.feedback === 1 ? null : 1)}>
                                    <Icon d="thumbUp" size={14} />
                                  </button>
                                  <button type="button" aria-label={s.notHelpful} aria-pressed={m.feedback === -1} onClick={() => void c.session.feedback(m.messageId as string, m.feedback === -1 ? null : -1)}>
                                    <Icon d="thumbDown" size={14} />
                                  </button>
                                </div>
                              ) : null}
                            </>
                          )}
                        </div>
                      ))
                    )}
                    {waiting ? (
                      <div class="dots" part="thinking" role="status" aria-label={s.thinking}>
                        <i />
                        <i />
                        <i />
                      </div>
                    ) : null}
                  </div>

                  {st.installing !== 'no' ? (
                    <div class="notice" part="notice" role="status" aria-live="polite">
                      {st.installing === 'downloading' ? <span class="spin" aria-hidden="true" /> : null}
                      <span>{st.installing === 'downloading' ? s.recognitionDownloading : st.installing === 'done' ? s.recognitionReady : s.recognitionFailed}</span>
                    </div>
                  ) : null}
                  {askMemory ? <MemoryAsk c={c} /> : null}
                  {st.memoryNote ? (
                    <div class="notice" part="notice memory-note" role="status" aria-live="polite">
                      <span>{st.memoryNote === 'on' ? s.memoryOnNote : s.memoryOffNote}</span>
                    </div>
                  ) : null}
                  {editing ? (
                    <div class="notice" part="notice editing" role="status">
                      <span>{s.editing}</span>
                      <button
                        type="button"
                        class="chip"
                        onClick={() => {
                          setEditing(false)
                          setInput('')
                        }}
                      >
                        {s.cancelEdit}
                      </button>
                    </div>
                  ) : null}
                  <form class="composer" part="composer" onSubmit={submit}>
                    <div class="field" part="field">
                      <textarea
                        ref={box}
                        data-evokai="composer"
                        part="input"
                        value={input}
                        rows={1}
                        aria-label={t(s.message)}
                        placeholder={st.dictating ? s.placeholderDictating : t(s.placeholder)}
                        onInput={(e) => setInput(e.currentTarget.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                            e.preventDefault()
                            submit()
                          }
                        }}
                      />
                      <button
                        type="button"
                        class={`cb ${st.dictating ? 'on' : ''}`}
                        part="composer-button dictate-button"
                        disabled={noSpeech}
                        aria-pressed={st.dictating}
                        aria-label={st.dictating ? s.stopDictation : s.dictate}
                        title={voiceTitle || (st.dictating ? s.stopDictation : s.dictate)}
                        onClick={() => void c.toggleDictation(input)}
                      >
                        <Icon d="mic" />
                      </button>
                      {props.voice ? (
                        <button
                          ref={voiceBtn}
                          type="button"
                          class={`cb ${st.voiceOn ? 'on' : ''}`}
                          part="composer-button voice-button"
                          disabled={noSpeech && c.session.voiceEngine === 'browser'}
                          aria-pressed={st.voiceOn}
                          aria-label={st.voiceOn ? s.leaveVoice : s.voiceMode}
                          title={voiceTitle || (st.voiceOn ? s.leaveVoice : t(s.voiceModeHint))}
                          onClick={() => void c.toggleVoice()}
                        >
                          <Icon d="wave" />
                        </button>
                      ) : null}
                      {busy ? (
                        <button type="button" class="cb send" part="composer-button send-button stop-button" aria-label={s.stop} onClick={() => void c.stop()}>
                          <Icon d="stop" size={14} />
                        </button>
                      ) : (
                        <button type="submit" class="cb send" part="composer-button send-button" disabled={!input.trim()} aria-label={s.send}>
                          <Icon d="up" />
                        </button>
                      )}
                    </div>
                  </form>
                </>
              )}
            </div>

            <div
              class="layer voice"
              part="stage"
              data-phase={phase}
              role="group"
              aria-label={t(s.voiceDialog)}
              inert={!voiceLayer}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault()
                  leaveVoice()
                }
              }}
            >
              <div class="stagebox">
                <span ref={centerSlot} class="oslot mid" part="stage-orb orb-slot" data-rest="center" data-here={here('center')}>
                  <slot name="stage-orb" />
                </span>
                <div class="status" part="stage-status" aria-live="polite">
                  {voiceLayer ? voiceStatus(s, st, true) : ''}
                </div>
                {st.voiceOn && st.noVoice ? (
                  <p class="sub vnote" part="stage-notice" role="status">
                    {s.speechUnavailable}
                  </p>
                ) : null}
                {voiceLayer ? <Subtitles st={st} /> : <p class="subtitles" part="subtitles" />}
                {voiceLayer && cards ? (
                  <div class="vcards" part="stage-cards" role="group" aria-label={s.details}>
                    {cards}
                  </div>
                ) : null}
              </div>
              <VoiceControls controller={c} state={st} size={18} onEnd={leaveVoice} />
            </div>
          </div>
        </section>
      ) : null}
      <TravelOrb
        handle={travel}
        controller={c}
        at={look.orb}
        find={slot}
        frozen={look.box === 'pre' || look.box === 'in' || look.box === 'out'}
        hidden={custom(look.orb)}
        phase={st.voiceOn ? phase : 'idle'}
        busy={busy || ss.phase === 'speaking'}
        muted={st.muted || !st.micOpen}
        colors={props.orb}
      />
    </div>
  )
}
