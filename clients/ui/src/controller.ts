/**
 * The behaviour behind the components, with no drawing: the session (from the headless client), its conversation kept
 * across page loads, the starter questions, the conversation list, dictation, the voice engine and what the voice stage
 * needs (subtitles, the phase, the microphone). The chat panel and the voice stage both read one controller.
 */
import {
  BrowserVoiceEngine,
  Dictation,
  EvokaiSession,
  createVoiceEngine,
  installRecognition,
  localePack,
  recognitionSupport,
  timeline,
  type AttachmentOffer,
  type CreateVoiceOptions,
  unlockAudio,
  type FetchLike,
  type Memory,
  type MemoryState,
  type Connection,
  type ConnectionProvider,
  type PageContext,
  type SessionState,
  type TokenInfo,
  type TokenRequestContext,
  type VoiceEngine,
  type MarkupView,
} from '@evokai/evokai-client/browser'

export interface ConversationItem {
  id: string
  title: string
}

export interface ControllerOptions {
  /** Where Evokai is mounted, base path included (e.g. `https://agents.example.com/agent`). */
  baseUrl?: string
  /** Your backend's endpoint that mints a session token: `POST {conversationId}` → the mint response. */
  tokenUrl?: string
  /** Or a function that returns a token (the client's `getToken`). */
  getToken?: (ctx: TokenRequestContext) => TokenInfo | Promise<TokenInfo>
  /** Or a session you created yourself. */
  session?: EvokaiSession
  /** `en` (default) or `it`: the texts, speech rules and voices. */
  locale?: string
  /** Remembers the open conversation in this browser under this key (default `evokai`; empty: do not remember). */
  storageKey?: string
  /** Your backend's list of the user's conversations: `GET` → `{items: [{id, title}]}`. */
  conversationsUrl?: string
  listConversations?: () => Promise<ConversationItem[]>
  /** What the user is looking at, sent with each message (default: path, title and visible text of the page). */
  getPage?: (() => PageContext | null | undefined) | null
  /** The element whose text is the page (default `main`, else `body`). */
  pageSelector?: string
  /** Voice options for the client's engine (microphone sound, rate, barge-in). */
  voice?: CreateVoiceOptions
  fetch?: FetchLike
}

export type Support = 'checking' | 'ready' | 'downloadable' | 'unsupported'
export type Installing = 'no' | 'downloading' | 'done' | 'failed'

export interface ControllerState {
  session: SessionState
  suggestions: string[]
  /** What the person may attach (null: nothing; the paper clip shows only otherwise). */
  files: AttachmentOffer | null
  /** The delimiters of the answers' inline markup, hidden in what the chat shows (null without). */
  markup: MarkupView | null
  history: ConversationItem[] | null
  support: Support
  installing: Installing
  /** Voice mode is on but this device has no voice to read the answers with (they stay written). */
  noVoice: boolean
  dictating: boolean
  /** Text heard by dictation (goes to the message box). */
  dictated: string
  voiceOn: boolean
  muted: boolean
  talkOver: boolean
  micOpen: boolean
  /** What the user is saying now (voice mode). */
  heard: string
  /** What the agent is saying now and how far it got (subtitles). */
  line: { text: string; upto: number }
  error: string | null
  /** Whether the agents may remember things about the person (null until the session says). */
  memory: MemoryState | null
  /** The memory view is open. */
  memoryView: boolean
  /** What is remembered (null while loading or before the view opens), and a failed load. */
  memories: Memory[] | null
  memoryError: boolean
  /** The person answered the consent question in this browser: never ask again. */
  memoryAnswered: boolean
  /** A short note after the consent answer (`on` or `off`), cleared after a few seconds. */
  memoryNote: 'on' | 'off' | null
  /** The person's connections to providers (null until read); the menu offers the view when there is one. */
  connections: Connection[] | null
  /** The connections view is open, and a failed load. */
  connectionsView: boolean
  connectionsError: boolean
  /** A provider whose window is open (the person is connecting it). */
  connecting: string | null
}

const PAGE_CHARS = 6000

function store(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

const safe = <T>(fn: () => T, fallback: T): T => {
  try {
    return fn()
  } catch {
    return fallback
  }
}

/** The page as the agent sees it: path with its query, title and visible text (the components' own text is never part of it). */
export function pageContext(selector = 'main'): PageContext | null {
  if (typeof document === 'undefined') return null
  const root = document.querySelector(selector) ?? document.body
  let text = ''
  try {
    const clone = root.cloneNode(true) as HTMLElement
    clone.querySelectorAll('evokai-chat, evokai-voice, script, style, [data-evokai-ignore]').forEach((n) => n.remove())
    text = (clone.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, PAGE_CHARS)
  } catch {
    /* no page text */
  }
  // The query too: tabs, filters and selected rows live there, and the agent links to them.
  return { path: location.pathname + location.search, title: document.title, text }
}

export class Controller {
  readonly session: EvokaiSession
  readonly opts: ControllerOptions
  private voice_: VoiceEngine | null = null
  private dictation: Dictation | null = null
  private listeners = new Set<(s: ControllerState) => void>()
  private st: ControllerState
  private opened = false
  private unsub: Array<() => void> = []
  private voiceUnsub: Array<() => void> = []
  private lineTimer = 0
  private noteTimer: ReturnType<typeof setTimeout> | undefined
  private restarted = false

  constructor(opts: ControllerOptions) {
    this.opts = opts
    const key = this.key('talkOver')
    const talkOver = safe(() => store()?.getItem(key), null) !== 'off'
    const answered = this.key('memoryAnswered')
    const memoryAnswered = !!answered && safe(() => store()?.getItem(answered), null) === '1'
    if (opts.session) this.session = opts.session
    else {
      if (!opts.baseUrl) throw new Error('evokai-ui: base-url is required')
      if (!opts.getToken && !opts.tokenUrl) throw new Error('evokai-ui: token-url or getToken is required')
      this.session = new EvokaiSession({
        baseUrl: opts.baseUrl,
        getToken: (ctx) => this.mint(ctx),
        locale: opts.locale ?? 'en',
        getPage: () => (opts.getPage === null ? null : opts.getPage ? opts.getPage() : pageContext(opts.pageSelector)),
        ...(opts.fetch ? { fetch: opts.fetch } : {}),
      })
    }
    this.st = {
      session: this.session.state,
      suggestions: [],
      files: null,
      markup: null,
      history: null,
      support: 'checking',
      installing: 'no',
      noVoice: false,
      dictating: false,
      dictated: '',
      voiceOn: false,
      muted: false,
      talkOver,
      micOpen: false,
      heard: '',
      line: { text: '', upto: 0 },
      error: null,
      memory: null,
      memoryView: false,
      connections: null,
      connectionsView: false,
      connectionsError: false,
      connecting: null,
      memories: null,
      memoryError: false,
      memoryAnswered,
      memoryNote: null,
    }
    this.unsub.push(this.session.subscribe((s) => this.patch({ session: s })))
    this.unsub.push(
      this.session.on('conversation', (e) => {
        if (e.id) this.remember(e.id)
      }),
      // The stored copy of the turn carries the message ids (feedback); a stale or external copy is ignored by the
      // client's store.
      this.session.on('done', () => {
        setTimeout(() => void this.session.loadConversation().catch(() => undefined), 0)
      }),
    )
  }

  get state(): ControllerState {
    return this.st
  }

  get voice(): VoiceEngine | null {
    return this.voice_
  }

  get lang(): string {
    return localePack(this.opts.locale ?? 'en').lang
  }

  subscribe(fn: (s: ControllerState) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private patch(p: Partial<ControllerState>): void {
    this.st = { ...this.st, ...p }
    for (const fn of [...this.listeners]) fn(this.st)
  }

  private key(what = 'conversation'): string {
    const base = this.opts.storageKey ?? 'evokai'
    return base ? `${base}.${what}` : ''
  }

  private remember(conversationId: string | null): void {
    const k = this.key()
    if (!k) return
    safe(() => (conversationId ? store()?.setItem(k, conversationId) : store()?.removeItem(k)), undefined)
  }

  private saved(): string | null {
    const k = this.key()
    return k ? safe(() => store()?.getItem(k) ?? null, null) : null
  }

  /** The application's backend mints a token; its starter questions and a replaced conversation are noted. */
  private async mint(ctx: TokenRequestContext): Promise<TokenInfo> {
    let info: TokenInfo
    if (this.opts.getToken) info = await this.opts.getToken(ctx)
    else {
      const f = this.opts.fetch ?? fetch.bind(globalThis)
      const r = await f(this.opts.tokenUrl as string, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ conversationId: ctx.conversationId }),
      })
      if (!r.ok) throw new Error(`token ${r.status}`)
      info = (await r.json()) as TokenInfo
    }
    if (typeof info !== 'string') {
      const extra = info as { suggestions?: unknown; restarted?: unknown; memory?: unknown }
      if (Array.isArray(extra.suggestions)) this.patch({ suggestions: extra.suggestions.filter((x): x is string => typeof x === 'string').slice(0, 8) })
      if (extra.restarted === true) this.restarted = true
      this.noteMemory(extra.memory)
    }
    return info
  }

  /** The panel opened: resume the remembered conversation (or start one) and read the usage and starter questions. */
  async open(): Promise<void> {
    if (this.opened) return
    this.opened = true
    void this.checkSupport()
    let read = false
    try {
      const saved = this.saved()
      if (saved && !this.session.state.conversationId && (this.opts.getToken || this.opts.tokenUrl)) {
        await this.session.switchConversation(saved)
        if (this.restarted) {
          this.restarted = false
          this.remember(null)
          await this.session.newConversation()
        }
      } else if (!this.opts.session) {
        const info = await this.session.info()
        if (info.suggestions?.length) this.patch({ suggestions: info.suggestions })
        this.patch({ markup: info.markup ?? null })
        this.noteMemory(info.memory)
        this.patch({ files: info.attachments ?? null })
        read = true
      }
      this.patch({ error: null })
      // A resumed conversation (or a session of your own) has not read the session yet: its memory state comes apart.
      if (!read)
        void this.session.info().then(
          (i) => {
            this.noteMemory(i.memory)
            this.patch({ markup: i.markup ?? null, files: i.attachments ?? null })
          },
          () => undefined,
        )
      // Whether there is anything to connect: the menu offers the connections view only then.
      void this.readConnections()
    } catch (e) {
      this.opened = false
      if (this.saved()) this.remember(null)
      this.patch({ error: e instanceof Error ? e.message : String(e) })
    }
  }

  /** Send a message. Resolves false when the service refused it or could not be reached (not when stopped). */
  async send(text: string, answer = false, files: File[] = []): Promise<boolean> {
    const t = text.trim()
    if (!t) return false
    const done = await this.session.chat({ text: t, mode: this.st.voiceOn ? 'voice' : 'text', ...(answer ? { answer } : {}), ...(files.length ? { files } : {}) })
    return done !== null || !this.session.state.error
  }

  /** Edit the last question: it and its answer are replaced by a new turn (refused once it changed data). */
  async rewrite(text: string): Promise<void> {
    const t = text.trim()
    if (!t) return
    await this.session.rewrite(t, { mode: this.st.voiceOn ? 'voice' : 'text' })
  }

  async newConversation(): Promise<void> {
    this.remember(null)
    this.patch({ history: null })
    await this.session.newConversation()
  }

  async switchTo(id: string): Promise<void> {
    this.patch({ history: null })
    this.remember(id)
    await this.session.switchConversation(id)
  }

  /** Show or hide the conversation list. */
  async toggleHistory(): Promise<void> {
    if (this.st.history) return this.patch({ history: null })
    if (this.st.memoryView) this.closeMemory()
    if (this.st.connectionsView) this.closeConnections()
    let items: ConversationItem[] = []
    try {
      if (this.opts.listConversations) items = await this.opts.listConversations()
      else if (this.opts.conversationsUrl) {
        const f = this.opts.fetch ?? fetch.bind(globalThis)
        const r = await f(this.opts.conversationsUrl, { credentials: 'same-origin', headers: { Accept: 'application/json' } })
        items = r.ok ? (((await r.json()) as { items?: ConversationItem[] }).items ?? []) : []
      }
    } catch {
      items = []
    }
    this.patch({ history: items.filter((c) => c.title && c.title !== 'Conversation') })
  }

  get hasHistory(): boolean {
    return !!(this.opts.listConversations || this.opts.conversationsUrl)
  }

  // ── memory ────────────────────────────────────────────────────────────────────────────────────────────────────

  private noteMemory(m: unknown): void {
    if (m && typeof m === 'object' && 'mode' in m) this.patch({ memory: m as MemoryState })
  }

  /** Open the memory view and read what is remembered. */
  async openMemory(): Promise<void> {
    if (this.st.connectionsView) this.closeConnections()
    this.patch({ memoryView: true, history: null })
    await this.loadMemories()
  }

  closeMemory(): void {
    this.patch({ memoryView: false, memories: null, memoryError: false })
  }

  async loadMemories(): Promise<void> {
    this.patch({ memories: null, memoryError: false })
    try {
      const r = await this.session.memories()
      if (this.st.memoryView) this.patch({ memory: r.memory, memories: r.items })
    } catch {
      if (this.st.memoryView) this.patch({ memoryError: true })
    }
  }

  /**
   * The person's answer to memory, from the consent card or the memory view. A no (or turning memory off) deletes what
   * was remembered. The answer is also kept in this browser, so the card never comes back. Rejects when the service
   * refused it.
   */
  async setMemoryConsent(granted: boolean, fromCard = false): Promise<void> {
    const k = this.key('memoryAnswered')
    if (k) safe(() => store()?.setItem(k, '1'), undefined)
    this.patch({ memoryAnswered: true })
    const r = await this.session.memoryConsent(granted)
    this.patch({ memory: r.memory, ...(r.memory.active ? {} : { memories: this.st.memoryView ? [] : null }) })
    if (fromCard) {
      clearTimeout(this.noteTimer)
      this.patch({ memoryNote: granted ? 'on' : 'off' })
      this.noteTimer = setTimeout(() => this.patch({ memoryNote: null }), 6000)
    }
  }

  /** Rewrite one memory; rejects with the service's error (`memory_refused`) for the view to show. */
  async editMemory(id: string, text: string): Promise<void> {
    const m = await this.session.editMemory(id, text)
    this.patch({ memories: (this.st.memories ?? []).map((x) => (x.id === id ? m : x)) })
  }

  /** Delete one memory, or everything (no id). */
  async forgetMemory(id?: string): Promise<void> {
    await this.session.forgetMemory(id)
    this.patch({ memories: id ? (this.st.memories ?? []).filter((x) => x.id !== id) : [] })
  }

  // ── connections ───────────────────────────────────────────────────────────────────────────────────────────────

  /** Read the person's connections (quietly: a failure leaves the menu without the entry). */
  async readConnections(): Promise<void> {
    try {
      this.patch({ connections: await this.session.connections(), connectionsError: false })
    } catch {
      if (this.st.connectionsView) this.patch({ connectionsError: true })
    }
  }

  /** Open the connections view. */
  async openConnections(): Promise<void> {
    if (this.st.memoryView) this.closeMemory()
    this.patch({ connectionsView: true, history: null, connectionsError: false })
    await this.readConnections()
  }

  closeConnections(): void {
    this.patch({ connectionsView: false, connectionsError: false })
  }

  /**
   * Connect a provider: the provider opens in a new window (opened at once, so the browser allows it) and the
   * connections are read again when it closes or the page gets the focus back. Rejects when the service refused.
   */
  async connect(provider: ConnectionProvider): Promise<void> {
    const win = typeof window !== 'undefined' ? window.open('about:blank', '_blank', 'popup,width=520,height=700') : null
    this.patch({ connecting: provider })
    try {
      const started = await this.session.startConnection(provider)
      if (win && !win.closed) win.location.href = started.authorizeUrl
      else if (typeof window !== 'undefined') window.open(started.authorizeUrl, '_blank', 'noopener')
    } catch (e) {
      win?.close()
      this.patch({ connecting: null })
      throw e
    }
    const done = () => {
      clearInterval(timer)
      if (typeof window !== 'undefined') window.removeEventListener('focus', done)
      this.patch({ connecting: null })
      void this.readConnections()
    }
    const timer = setInterval(() => {
      if (!win || win.closed) done()
    }, 800)
    if (typeof window !== 'undefined') window.addEventListener('focus', done, { once: true })
  }

  /** Disconnect a provider (the person's tokens are deleted). */
  async disconnect(provider: ConnectionProvider): Promise<void> {
    await this.session.disconnect(provider)
    await this.readConnections()
  }

  // ── speech ────────────────────────────────────────────────────────────────────────────────────────────────────

  /** The application allows the browser's online recogniser where there is no on-device one (voice options). */
  private get allowOnline(): boolean {
    return !!this.opts.voice?.allowOnlineRecognition
  }

  private async checkSupport(): Promise<void> {
    const s = await recognitionSupport({ lang: this.lang, allowOnline: this.allowOnline }).catch(() => 'unsupported' as const)
    this.patch({ support: s })
  }

  /** Make on-device recognition ready (downloads the language pack the first time; from a user gesture). */
  async ensureRecognition(): Promise<boolean> {
    if (this.st.support === 'ready') return true
    if (this.st.support !== 'downloadable') return false
    this.patch({ support: 'checking', installing: 'downloading' })
    const ok = await installRecognition({ lang: this.lang }).catch(() => false)
    const s = await recognitionSupport({ lang: this.lang, allowOnline: this.allowOnline }).catch(() => 'unsupported' as const)
    const done = ok && s === 'ready'
    this.patch({ support: s, installing: done ? 'done' : 'failed' })
    setTimeout(() => this.st.installing !== 'downloading' && this.patch({ installing: 'no' }), done ? 2500 : 6000)
    return done
  }

  async toggleDictation(base: string): Promise<void> {
    if (this.dictation) {
      this.dictation.stop()
      return
    }
    if (!(await this.ensureRecognition())) return
    const d = new Dictation({ locale: this.opts.locale ?? 'en', micSound: this.opts.voice?.micSound ?? 'goccia', meterMicrophone: false, allowOnlineRecognition: this.allowOnline })
    this.dictation = d
    const prefix = base.trim() ? `${base.trim()} ` : ''
    d.on('text', (t) => this.patch({ dictated: prefix + t }))
    d.on('final', (t) => this.patch({ dictated: prefix + t }))
    d.on('state', (s) => {
      if (s === 'idle' || s === 'error' || s === 'unsupported') {
        this.dictation = null
        this.patch({ dictating: false })
      }
    })
    this.patch({ dictating: true, dictated: base })
    await d.start().catch(() => {
      this.dictation = null
      this.patch({ dictating: false })
    })
  }

  /** Voice mode on or off (from a user gesture). */
  async toggleVoice(): Promise<void> {
    if (this.voice_) return this.stopVoice()
    // Before any await: still inside the gesture, or the assistant hears but is never heard (Safari, iOS, and Chrome
    // when the page had no click yet).
    unlockAudio()
    // On-device recognition is needed unless the token chose a server recognition engine.
    if (this.session.voiceEngine === 'browser' && !this.session.speech?.stt && !(await this.ensureRecognition())) return
    this.dictation?.stop()
    const v = createVoiceEngine(this.session, { locale: this.opts.locale ?? 'en', ...this.opts.voice, talkOver: this.st.talkOver } as CreateVoiceOptions)
    this.voice_ = v
    this.voiceUnsub = [
      v.on('interim', (t) => this.patch({ heard: t })),
      v.on('heard', () => this.patch({ heard: '' })),
      v.on('mic', (open) => this.patch({ micOpen: open })),
      v.on('segmentStart', (seg) => this.followLine(seg.text, seg.spoken)),
      v.on('boundary', ({ segment, charIndex }) => {
        if (this.st.line.text === segment.text && charIndex > this.st.line.upto) this.patch({ line: { text: segment.text, upto: charIndex } })
      }),
      v.on('segmentEnd', (seg) => {
        cancelAnimationFrame(this.lineTimer)
        if (this.st.line.text === seg.text) this.patch({ line: { text: seg.text, upto: seg.text.length } })
      }),
      v.on('state', (s) => {
        if (s === 'error' || s === 'unsupported') void this.stopVoice()
      }),
    ]
    this.patch({ voiceOn: true, muted: false, heard: '', noVoice: false })
    if (v instanceof BrowserVoiceEngine) {
      const silent = (s: { state: string; error?: { code: string } }) => s.state === 'unavailable'
      this.voiceUnsub.push(v.tts.on('status', (s) => this.patch({ noVoice: silent(s) })))
    }
    await v.start()
    if (v.state !== 'active') await this.stopVoice()
    else {
      this.patch({ micOpen: v instanceof BrowserVoiceEngine ? v.micOpen : true })
      if (v instanceof BrowserVoiceEngine) {
        await v.tts.prepare().catch(() => undefined)
        if (this.voice_ === v) this.patch({ noVoice: v.tts.status.state === 'unavailable' })
      }
    }
  }

  async stopVoice(): Promise<void> {
    const v = this.voice_
    this.voice_ = null
    for (const u of this.voiceUnsub) u()
    this.voiceUnsub = []
    cancelAnimationFrame(this.lineTimer)
    this.patch({ voiceOn: false, heard: '', line: { text: '', upto: 0 }, micOpen: false, muted: false, noVoice: false })
    await v?.stop()
  }

  setMuted(muted: boolean): void {
    this.voice_?.setMuted(muted)
    this.patch({ muted, heard: muted ? '' : this.st.heard })
  }

  /** «Interrupt by speaking»: on, the user may talk over the agent; off, strict turns. Remembered in this browser. */
  setTalkOver(on: boolean): void {
    const k = this.key('talkOver')
    if (k) safe(() => store()?.setItem(k, on ? 'on' : 'off'), undefined)
    if (this.voice_ instanceof BrowserVoiceEngine) this.voice_.setTalkOver(on)
    this.patch({ talkOver: on })
  }

  /** Stop the answer: silence, cancel the turn. */
  async stop(): Promise<void> {
    if (this.voice_) await this.voice_.interrupt()
    else await this.session.cancel()
    this.session.abort()
  }

  /**
   * Subtitles: the line advances with estimated syllables (stretched by the voice's rate) and snaps to every word the
   * voice reports. The syllables are those of what the voice says (`spoken`, numbers in words), placed on the shown text.
   */
  private followLine(text: string, spoken = text): void {
    cancelAnimationFrame(this.lineTimer)
    this.patch({ line: { text, upto: 0 } })
    const said = timeline(spoken, { rate: this.opts.voice?.rate ?? 1, locale: this.opts.locale ?? 'en' }).events
    const scale = spoken === text ? 1 : text.length / Math.max(1, spoken.length)
    const events = scale === 1 ? said : said.map((e) => ({ ...e, char: Math.min(text.length, Math.round(e.char * scale)) }))
    if (!events.length || typeof requestAnimationFrame !== 'function') return
    const started = performance.now()
    let idx = 0
    const tick = () => {
      const el = (performance.now() - started) / 1000
      while (idx < events.length && (events[idx]?.at ?? Infinity) <= el) idx++
      const at = idx > 0 ? (events[idx - 1]?.char ?? 0) : 0
      if (this.st.line.text === text && at > this.st.line.upto) this.patch({ line: { text, upto: at } })
      if (idx < events.length && this.st.line.text === text) this.lineTimer = requestAnimationFrame(tick)
    }
    this.lineTimer = requestAnimationFrame(tick)
  }

  dispose(): void {
    clearTimeout(this.noteTimer)
    void this.stopVoice()
    this.dictation?.cancel()
    for (const u of this.unsub) u()
    if (!this.opts.session) this.session.dispose()
  }
}
