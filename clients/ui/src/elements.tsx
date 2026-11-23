/**
 * The Web Components: `<evokai-chat>` (the launcher and the chat panel, with voice mode) and `<evokai-voice>` (the
 * voice stage on its own). Each renders in its own shadow root; configuration comes from attributes (strings) and
 * properties (functions and objects).
 */
import { render, type VNode } from 'preact'

import { localePack, type ClientToolHandler, type CreateVoiceOptions, type EvokaiSession, type FetchLike, type PageContext, type SessionEvent, type TokenInfo, type TokenRequestContext } from '@evokai/evokai-client/browser'
import { type ChatState, chatState, fromEvent } from './chatstate'
import { Ui, defaultFormat, type ToolRenderer, type UiContext, type ValueFormatter } from './context'
import { Controller, type ConversationItem } from './controller'
import { Panel, useController } from './panel'
import { VoiceStage } from './stage'
import { baseCss } from './styles'
import { stringsFor, type Strings } from './strings'
import { orbColors, orbImages, resolveTheme, themeVars, type Theme } from './theme'
import { ChartView, DiagramView } from './visuals'

const CONFIG = ['base-url', 'token-url', 'conversations-url', 'locale', 'storage-key', 'page-context', 'page-selector'] as const
const LOOK = ['theme', 'title', 'app-name', 'voice', 'scheme', 'position', 'shortcut', 'open', 'unstyled', 'orb-images'] as const

// Outside a browser (server rendering) the module still loads; the elements are only defined in a browser.
const HTMLBase: typeof HTMLElement = typeof HTMLElement === 'undefined' ? (Object as unknown as typeof HTMLElement) : HTMLElement

/** What both elements share: configuration, theme, texts, the controller and the shadow root. */
abstract class EvokaiBase extends HTMLBase {
  static get observedAttributes(): string[] {
    return [...CONFIG, ...LOOK]
  }

  /** Your backend's token function (instead of `token-url`). */
  getToken?: (ctx: TokenRequestContext) => TokenInfo | Promise<TokenInfo>
  /** A session you created (instead of `base-url` + `token-url`). */
  session?: EvokaiSession
  /** The user's conversations (instead of `conversations-url`). */
  listConversations?: () => Promise<ConversationItem[]>
  /** What the user is looking at, sent with each message; null sends nothing. */
  getPage?: (() => PageContext | null | undefined) | null
  /** Voice options for the client's engine (microphone sound, rate, barge-in). */
  voiceOptions?: CreateVoiceOptions
  /** Labels for tool activity, by tool name (or a function). */
  toolLabels?: Record<string, string> | ((name: string) => string | null | undefined)
  /**
   * Your own cards for tools, by tool name: `(part) => HTMLElement | null` draws that tool's activity or result in the
   * chat instead of the default line (server, MCP and client tools alike; `ui_chart` and `ui_diagram` replace the
   * built-in visuals). A part carries only what the events carry: a client tool's arguments and result, a server
   * tool's name and status. Return null to keep the default.
   */
  toolRenderers?: Record<string, ToolRenderer>
  /** How chart values are written (default: plain numbers, `pct` with %). */
  formatValue?: ValueFormatter
  /** The fetch used for Evokai and your token and conversation endpoints (default: the global one). */
  fetcher?: FetchLike
  protected root: ShadowRoot | null = null
  protected mount: HTMLDivElement | null = null
  private base: HTMLStyleElement | null = null
  private skinEl: HTMLStyleElement | null = null
  private skin_: string | string[] | null = null
  private icons_: Partial<Record<string, string>> | null = null
  private watched: Controller | null = null
  private unwatch: (() => void) | null = null
  private state_: ChatState = 'idle'
  private controller_: Controller | null = null
  private configKey = ''
  private theme_: Theme | string | null = null
  private strings_: Partial<Strings> | null = null
  private tools_: Record<string, ClientToolHandler> | null = null
  private unhandle: Array<() => void> = []
  private scheduled = false

  /**
   * Your custom client tools, by name (`docs/client-tools.md`): each function gets the call's arguments, and what it
   * returns is the result of a tool that waits.
   */
  get tools(): Record<string, ClientToolHandler> | null {
    return this.tools_
  }

  set tools(v: Record<string, ClientToolHandler> | null) {
    this.tools_ = v
    const c = this.controller_
    if (c) this.handleTools(c.session)
  }

  private handleTools(session: EvokaiSession): void {
    for (const off of this.unhandle) off()
    this.unhandle = Object.entries(this.tools_ ?? {}).map(([name, fn]) => session.handleTool(name, fn))
  }

  /** The theme: a preset name (`evokai`) or a `Theme` object. */
  get theme(): Theme | string | null {
    return this.theme_ ?? this.getAttribute('theme')
  }

  set theme(v: Theme | string | null) {
    this.theme_ = v
    this.update()
  }

  /**
   * Your own CSS inside the component (a string or several). It is unlayered, so it always wins over the default look
   * (cascade layer `evokai`) whatever its selectors: target the parts (`[part~="user-bubble"]`), the slots and the
   * host's state (`:host([data-phase=speaking])`). With the `unstyled` attribute the default look is dropped and only
   * the tokens and a reset remain.
   */
  get skin(): string | string[] | null {
    return this.skin_
  }

  set skin(v: string | string[] | null) {
    this.skin_ = v
    this.update()
  }

  /**
   * Replaced icons by name (`history`, `plus`, `x`, `mic`, `wave`, `up`, `stop`, `thumbUp`, `thumbDown`, `more`, `back`,
   * `memory`, `edit`, `trash`, `keyboard`): SVG markup.
   */
  get icons(): Partial<Record<string, string>> | null {
    return this.icons_
  }

  set icons(v: Partial<Record<string, string>> | null) {
    this.icons_ = v
    this.update()
  }

  /** Text overrides (any key of `Strings`). */
  get strings(): Partial<Strings> | null {
    return this.strings_
  }

  set strings(v: Partial<Strings> | null) {
    this.strings_ = v
    this.update()
  }

  /** The controller (created from the configuration); share it with an `<evokai-voice for=...>`. */
  get controller(): Controller | null {
    const key = JSON.stringify([...CONFIG.map((a) => this.getAttribute(a)), !!this.getToken, !!this.session])
    if (this.controller_ && key === this.configKey) return this.controller_
    const base = this.getAttribute('base-url') ?? undefined
    if (!this.session && !(base && (this.getToken || this.getAttribute('token-url')))) return null
    this.controller_?.dispose()
    this.configKey = key
    const page = this.getAttribute('page-context') === 'off' ? null : this.getPage
    const c = new Controller({
      ...(base ? { baseUrl: base } : {}),
      ...(this.getAttribute('token-url') ? { tokenUrl: this.getAttribute('token-url') as string } : {}),
      ...(this.getToken ? { getToken: this.getToken } : {}),
      ...(this.session ? { session: this.session } : {}),
      ...(this.getAttribute('conversations-url') ? { conversationsUrl: this.getAttribute('conversations-url') as string } : {}),
      ...(this.listConversations ? { listConversations: this.listConversations } : {}),
      locale: this.getAttribute('locale') ?? 'en',
      ...(this.hasAttribute('storage-key') ? { storageKey: this.getAttribute('storage-key') ?? '' } : {}),
      ...(page !== undefined ? { getPage: page } : {}),
      ...(this.getAttribute('page-selector') ? { pageSelector: this.getAttribute('page-selector') as string } : {}),
      ...(this.voiceOptions ? { voice: this.voiceOptions } : {}),
      ...(this.fetcher ? { fetch: this.fetcher } : {}),
    })
    c.session.on('*', (e: SessionEvent) => {
      this.dispatchEvent(new CustomEvent('evokai-event', { detail: e, bubbles: true, composed: true }))
      if (e.type === 'navigate') this.navigate(e.path)
    })
    this.handleTools(c.session)
    this.controller_ = c
    return c
  }

  set controller(c: Controller | null) {
    this.controller_ = c
    if (c) this.handleTools(c.session)
    this.configKey = JSON.stringify([...CONFIG.map((a) => this.getAttribute(a)), !!this.getToken, !!this.session])
    this.update()
  }

  connectedCallback(): void {
    if (!this.root) {
      this.root = this.attachShadow({ mode: 'open' })
      this.base = document.createElement('style')
      this.skinEl = document.createElement('style')
      this.skinEl.setAttribute('data-evokai-skin', '')
      this.mount = document.createElement('div')
      this.mount.setAttribute('part', 'root')
      this.root.append(this.base, this.skinEl, this.mount)
    }
    this.update()
  }

  disconnectedCallback(): void {
    if (this.mount) render(null, this.mount)
    this.unwatch?.()
    this.unwatch = null
    this.watched = null
  }

  /** The conversation's state in one word (`chatstate.ts`): also `data-state` on the host. */
  get chatState(): ChatState {
    return this.state_
  }

  /** A new state: `data-state` on the host and a `statechange` event (`detail: { state, previous }`). */
  private setState(next: ChatState): void {
    if (next === this.state_) return
    const previous = this.state_
    this.state_ = next
    this.setAttribute('data-state', next)
    // An orb made of images follows the state.
    if (this.hasAttribute('orb-images') || resolveTheme(this.theme).orb?.images) this.update()
    this.dispatchEvent(new CustomEvent('statechange', { detail: { state: next, previous }, bubbles: true, composed: true }))
  }

  /**
   * The conversation's state on the host, for your styles and for slotted content: `data-phase`, `data-busy`,
   * `data-voice`, `data-dictating`, `data-state` (and `data-view` on `<evokai-chat>`).
   */
  private reflect(c: Controller | null): void {
    if (c === this.watched) return
    this.unwatch?.()
    this.watched = c
    this.unwatch = null
    if (!c) return
    const put = (st: Controller['state']) => {
      const phase = st.voiceOn || st.session.busy ? st.session.phase : 'idle'
      if (this.getAttribute('data-phase') !== phase) this.setAttribute('data-phase', phase)
      this.toggleAttribute('data-busy', st.session.busy)
      this.toggleAttribute('data-voice', st.voiceOn)
      this.toggleAttribute('data-dictating', st.dictating)
      this.setState(chatState(this.state_, { phase: st.session.phase, busy: st.session.busy, voiceOn: st.voiceOn, dictating: st.dictating }))
    }
    if (!this.hasAttribute('data-state')) this.setAttribute('data-state', this.state_)
    put(c.state)
    const offState = c.subscribe(put)
    const offEvents = c.session.on('*', (e: SessionEvent) => this.setState(fromEvent(this.state_, e)))
    this.unwatch = () => {
      offState()
      offEvents()
    }
  }

  attributeChangedCallback(): void {
    this.update()
  }

  /** Re-render on the next microtask (several changes in a row render once). */
  update(): void {
    if (this.scheduled) return
    this.scheduled = true
    queueMicrotask(() => {
      this.scheduled = false
      if (this.mount && this.isConnected) this.paint()
    })
  }

  private paint(): void {
    const css = baseCss(this.hasAttribute('unstyled'))
    if (this.base && this.base.textContent !== css) this.base.textContent = css
    const skin = Array.isArray(this.skin_) ? this.skin_.join('\n') : (this.skin_ ?? '')
    if (this.skinEl && this.skinEl.textContent !== skin) this.skinEl.textContent = skin
    this.reflect(this.controller)
    const theme = resolveTheme(this.theme)
    for (const [k, v] of Object.entries(themeVars(theme))) this.style.setProperty(k, v)
    if (theme.scheme && theme.scheme !== 'auto' && !this.hasAttribute('scheme')) this.setAttribute('scheme', theme.scheme)
    if (theme.position && !this.hasAttribute('position')) this.setAttribute('position', theme.position)
    const locale = this.getAttribute('locale') ?? 'en'
    const labels = this.toolLabels
    const ctx: UiContext = {
      s: stringsFor(locale, this.strings_),
      lang: this.controller?.lang ?? localePack(locale).lang,
      fmt: this.formatValue ?? defaultFormat,
      toolLabel: (name) => (typeof labels === 'function' ? labels(name) : labels?.[name]) ?? name,
      names: { title: this.getAttribute('title') ?? 'Assistant', app: this.getAttribute('app-name') ?? '' },
      icons: this.icons_ ?? undefined,
      toolRenderer: (name) => this.toolRenderers?.[name],
    }
    render(<Ui.Provider value={ctx}>{this.view(theme)}</Ui.Provider>, this.mount as HTMLDivElement)
  }

  protected abstract view(theme: Theme): VNode | null

  /** The agent asked to open a page, or an answer's link was clicked: `evokai-navigate` (cancelable), then the page. */
  navigate(path: string): void {
    const ev = new CustomEvent('evokai-navigate', { detail: { path }, bubbles: true, composed: true, cancelable: true })
    if (this.dispatchEvent(ev) && path.startsWith('/') && !path.startsWith('//')) location.assign(path)
  }

  /** Send a message (opens the session if needed). */
  async send(text: string): Promise<void> {
    await this.controller?.open()
    await this.controller?.send(text)
  }
}

/**
 * `<evokai-chat>`: the launcher orb and the chat panel (Ctrl/⌘+J toggles it, Escape closes it), with voice mode inside
 * the panel. Closing the panel while voice is on minimizes it: the conversation goes on in the launcher orb, and
 * `open()` brings it back.
 */
export class EvokaiChatElement extends EvokaiBase {
  private keys = (e: KeyboardEvent) => {
    const key = (this.getAttribute('shortcut') ?? 'j').toLowerCase()
    if (key !== 'none' && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === key) {
      e.preventDefault()
      this.toggle()
    } else if (e.key === 'Escape' && !e.defaultPrevented && this.isOpen && !this.controller?.state.voiceOn) this.close()
  }

  /** What is on screen, on the host for your styles: `data-view` = `closed`, `chat`, `voice` or `minimized`. */
  private onView = (view: string) => {
    if (this.getAttribute('data-view') !== view) this.setAttribute('data-view', view)
  }

  /** Where the orb (or what stands in for it) is: `data-orb` = `launcher`, `head`, `center` or `flying`. */
  private onOrb = (at: string) => {
    if (this.getAttribute('data-orb') !== at) this.setAttribute('data-orb', at)
  }

  get isOpen(): boolean {
    return this.hasAttribute('open')
  }

  open(): void {
    if (this.isOpen) return
    this.setAttribute('open', '')
    this.dispatchEvent(new CustomEvent('evokai-open', { bubbles: true, composed: true }))
  }

  close(): void {
    if (!this.isOpen) return
    this.removeAttribute('open')
    this.dispatchEvent(new CustomEvent('evokai-close', { bubbles: true, composed: true }))
  }

  toggle(): void {
    if (this.isOpen) this.close()
    else this.open()
  }

  /** Start voice mode (call from a user gesture: the microphone needs one). */
  async startVoice(): Promise<void> {
    this.open()
    const c = this.controller
    if (c && !c.state.voiceOn) await c.toggleVoice()
  }

  override connectedCallback(): void {
    super.connectedCallback()
    addEventListener('keydown', this.keys)
  }

  override disconnectedCallback(): void {
    removeEventListener('keydown', this.keys)
    super.disconnectedCallback()
  }

  protected view(theme: Theme): VNode | null {
    const c = this.controller
    if (!c) return null
    return (
      <Panel
        controller={c}
        open={this.isOpen}
        onOpen={(o) => (o ? this.open() : this.close())}
        voice={this.getAttribute('voice') !== 'off'}
        logo={theme.logo}
        orb={orbColors(theme.orb)}
        images={orbImages(this.getAttribute('orb-images'), theme.orb)}
        state={this.chatState}
        keyTarget={typeof window === 'undefined' ? null : window}
        onNavigate={(p) => this.navigate(p)}
        onView={this.onView}
        onOrb={this.onOrb}
        host={this}
      />
    )
  }
}

function Standalone({ c, orb, onEnd }: { c: Controller; orb: Theme['orb']; onEnd: () => void }): VNode | null {
  const st = useController(c)
  if (!st.voiceOn) return null
  const last = [...st.session.messages].reverse().find((m) => m.role === 'assistant')
  const parts = (last?.parts ?? []).filter((p) => p.kind === 'chart' || p.kind === 'diagram').slice(-2)
  const cards = parts.length
    ? parts.map((p, i) => <div key={i} class="vcard">{p.kind === 'chart' ? <ChartView chart={p.chart} /> : p.kind === 'diagram' ? <DiagramView diagram={p.diagram} /> : null}</div>)
    : null
  return <VoiceStage controller={c} state={st} cards={cards} orb={orb} onKeyboard={onEnd} onEnd={onEnd} />
}

/**
 * `<evokai-voice>`: the voice stage alone (a kiosk, a voice button of your own). `start()` from a user gesture opens it;
 * it closes when the user ends it (`evokai-voice-end`). With `for="<id of an evokai-chat>"` it shares that panel's
 * conversation.
 */
export class EvokaiVoiceElement extends EvokaiBase {
  static override get observedAttributes(): string[] {
    return [...super.observedAttributes, 'for']
  }

  override get controller(): Controller | null {
    const id = this.getAttribute('for')
    if (id) {
      const other = document.getElementById(id) as EvokaiChatElement | null
      if (other?.controller) return other.controller
    }
    return super.controller
  }

  override set controller(c: Controller | null) {
    super.controller = c
  }

  /** Open the stage and start listening (from a user gesture). */
  async start(): Promise<void> {
    const c = this.controller
    if (!c) return
    await c.open()
    this.setAttribute('open', '')
    this.update()
    if (!c.state.voiceOn) await c.toggleVoice()
    if (!c.state.voiceOn) this.removeAttribute('open')
    this.update()
  }

  async stop(): Promise<void> {
    await this.controller?.stopVoice()
    this.removeAttribute('open')
    this.dispatchEvent(new CustomEvent('evokai-voice-end', { bubbles: true, composed: true }))
  }

  protected view(theme: Theme): VNode | null {
    const c = this.controller
    if (!c || !this.hasAttribute('open')) return null
    return <Standalone c={c} orb={orbColors(theme.orb)} onEnd={() => void this.stop()} />
  }
}

/** Register `<evokai-chat>` and `<evokai-voice>` (once; safe to call again). */
export function defineElements(registry: CustomElementRegistry | undefined = typeof customElements === 'undefined' ? undefined : customElements): void {
  if (!registry) return
  if (!registry.get('evokai-chat')) registry.define('evokai-chat', EvokaiChatElement)
  if (!registry.get('evokai-voice')) registry.define('evokai-voice', EvokaiVoiceElement)
}

declare global {
  interface HTMLElementTagNameMap {
    'evokai-chat': EvokaiChatElement
    'evokai-voice': EvokaiVoiceElement
  }
}
