/**
 * A browser session: one subject, one agent, one conversation, authorised by a short-lived session token that the
 * application's backend mints. Chats stream over fetch + SSE; a dropped stream is completed by replaying the events
 * after the last sequence number (`GET /v1/session/events?after=seq`).
 */
import {
  defaultFetch,
  errorFromResponse,
  EvokaiError,
  isAbort,
  joinUrl,
  requestJson,
  type FetchLike,
  type RequestOptions,
} from '../core/http'
import type {
  AttachmentRef,
  ChatMode,
  ClientToolEvent,
  ToolPart,
  Connection,
  ConnectionProvider,
  ConnectionStarted,
  DoneEvent,
  EvokaiEvent,
  SessionEvent,
  SessionEventType,
  TurnPhase,
  UsageShares,
  VoiceActivity,
  HistoryMessage,
  LimitErrorEvent,
  PageContext,
  SessionConversation,
  Memory,
  MemoryList,
  MemoryState,
  SessionInfo,
  SpeechChoice,
  VoiceEngineKind,
} from '../core/types'
import { readSse } from './sse'
import { localePack, type LocaleCode, type LocalePack } from '../locales'
import {
  speak as speakWith,
  speechEvents as speechEventsWith,
  transcribe as transcribeWith,
  type SpeakInput,
  type SpeechResult,
  type SpeechStreamEvent,
} from './speech-server'
import { localId, seen, SessionStore, type SessionState } from './store'

/** Runs a custom client tool: gets the call's arguments (and the event); its return value is the result. */
export type ClientToolHandler = (args: Record<string, unknown>, event: ClientToolEvent) => unknown

/** A token, or the session-token response of your backend (its other fields are used when present). */
export type TokenInfo =
  | string
  | {
      token: string
      conversationId?: string | null
      voiceEngine?: VoiceEngineKind
      /** The server speech engines the token chose (`speech` at mint), null for the defaults. */
      speech?: SpeechChoice | null
      expiresAt?: number
      usage?: UsageShares
    }

export interface TokenRequestContext {
  /** The conversation the token must open (null = a new one). */
  conversationId: string | null
  /** Why a token is needed: first use, the previous one was refused or is expiring, or a conversation switch. */
  reason: 'initial' | 'refresh' | 'switch'
}

export interface EvokaiSessionOptions {
  /** Where the service is mounted, base path included (e.g. `https://agents.example.com/agent`). */
  baseUrl: string
  /** A session token (when you have one already). */
  token?: TokenInfo
  /** Fetch a session token from your backend; called at first use, on 401, near expiry and on conversation switch. */
  getToken?: (ctx: TokenRequestContext) => TokenInfo | Promise<TokenInfo>
  fetch?: FetchLike
  /** The page the user is looking at, sent with each message. */
  getPage?: () => PageContext | null | undefined
  /** External storage: the conversation so far, sent with each message. */
  getHistory?: () => HistoryMessage[] | null | undefined | Promise<HistoryMessage[] | null | undefined>
  /** External storage: the turn's new messages, to append to your copy. */
  onMessages?: (messages: HistoryMessage[], done: DoneEvent) => void
  /** Replay after a dropped stream: how many polls and how far apart (ms, grows linearly). */
  replay?: { attempts?: number; delayMs?: number }
  /** Which voice engine the token chose, when `token`/`getToken` return a bare string. */
  voiceEngine?: VoiceEngineKind
  /** Language of the client's own rules and texts (speech rules, voice, syllables); default `en`. */
  locale?: LocaleCode | LocalePack | (string & {})
}

export interface ChatInput {
  text?: string
  confirm?: { actionId: string; approve: boolean }
  page?: PageContext | null
  mode?: ChatMode
  history?: HistoryMessage[] | null
  /** Seconds since epoch when the user stopped speaking (voice latency telemetry). */
  endOfSpeechAt?: number
  /** Show the text as an answer to a question (chips). */
  answer?: boolean
  /**
   * Rewrite the last question with `text`: it and everything after it are replaced (see `EvokaiSession.rewrite`). With
   * external storage, `onMessages` gets `done.replacedFrom`: drop your history from that index before appending.
   */
  rewrite?: boolean
  /** Regenerate the last answer instead of sending `text` (use `EvokaiSession.regenerate`). */
  regenerate?: boolean
  /**
   * Files for the model (`docs/attachments.md`): images and documents, as the session's `attachments` allows. The
   * service reads their kind from the bytes and refuses what the agent does not take (`attachment_*` codes).
   */
  files?: Array<File | Blob>
  signal?: AbortSignal
}

type EventListener = (event: SessionEvent) => void

const REFRESH_MARGIN_S = 30

export class EvokaiSession {
  readonly store = new SessionStore()
  private readonly baseUrl: string
  private readonly fetchFn: FetchLike
  private readonly opts: EvokaiSessionOptions
  private tok: { token: string; expiresAt?: number } | null = null
  private engine: VoiceEngineKind
  private speech_: SpeechChoice | null = null
  private readonly listeners = new Map<string, Set<EventListener>>()
  private readonly tools = new Map<string, ClientToolHandler>()
  private readonly renderers = new Map<string, (part: ToolPart) => void>()
  private readonly controllers = new Set<AbortController>()
  private replaying: Promise<void> | null = null
  /** Local preview URLs of images just sent, revoked once no message shows them (`fileRef`). */
  private readonly previews = new Set<string>()

  constructor(opts: EvokaiSessionOptions) {
    if (!opts.baseUrl) throw new Error('EvokaiSession: baseUrl is required')
    if (!opts.token && !opts.getToken) throw new Error('EvokaiSession: token or getToken is required')
    this.opts = opts
    this.baseUrl = opts.baseUrl
    this.fetchFn = opts.fetch ?? defaultFetch()
    this.engine = opts.voiceEngine ?? 'browser'
    if (opts.voiceEngine) this.store.dispatch({ type: 'voiceEngine', voiceEngine: opts.voiceEngine })
    // Client-side events derived from the state: the turn phase and the usage shares.
    let phase: TurnPhase = this.state.phase
    let usage = this.state.usage
    let messages = this.state.messages
    this.store.subscribe((st) => {
      if (st.messages !== messages) {
        messages = st.messages
        if (this.previews.size) this.revokePreviews(st.messages)
      }
      if (st.phase !== phase) {
        const previous = phase
        phase = st.phase
        this.emit({ type: 'phase', phase, previous, turnId: st.currentTurn?.turnId ?? st.lastDone?.turnId ?? null })
      }
      if (st.usage !== usage) {
        usage = st.usage
        if (usage) for (const fn of [...this.usageListeners]) fn(usage)
      }
    })
    if (opts.token) this.accept(opts.token)
  }

  // ── state ────────────────────────────────────────────────────────────────────────────────────────────────────

  get state(): SessionState {
    return this.store.getState()
  }

  subscribe(fn: (state: SessionState) => void): () => void {
    return this.store.subscribe(fn)
  }

  /** The session's locale pack (engines default to it). */
  get locale(): LocalePack {
    return localePack(this.opts.locale)
  }

  /** Which voice engine this session's token chose. */
  get voiceEngine(): VoiceEngineKind {
    return this.engine
  }

  /** Listen to raw events (`'*'` for all). Returns an unsubscribe function. */
  on<T extends SessionEventType | '*'>(
    type: T,
    fn: (event: T extends SessionEventType ? Extract<SessionEvent, { type: T }> : SessionEvent) => void,
  ): () => void {
    const set = this.listeners.get(type) ?? new Set<EventListener>()
    set.add(fn as EventListener)
    this.listeners.set(type, set)
    return () => set.delete(fn as EventListener)
  }

  /**
   * Apply an event from any channel (the SSE stream, a replay, or the server voice connection). Duplicates (an
   * event whose seq was already applied) are dropped, so listeners see each event once.
   */
  ingest(event: EvokaiEvent): void {
    if (typeof event.seq === 'number' && seen(this.state, event.seq)) return
    this.store.dispatch({ type: 'event', event })
    this.emit(event)
    this.renderEvent(event)
    if (event.type === 'client_tool') void this.runTool(event)
    if (event.type === 'done' && event.messages?.length) this.opts.onMessages?.(event.messages, event)
  }

  /** Revoke the preview URLs no message shows any more (a reload, a new conversation, a refused rewrite). */
  private revokePreviews(messages: SessionState['messages']): void {
    const shown = new Set(messages.flatMap((m) => m.attachments?.flatMap((a) => (a.url ? [a.url] : [])) ?? []))
    for (const url of this.previews) {
      if (shown.has(url)) continue
      this.previews.delete(url)
      URL.revokeObjectURL(url)
    }
  }

  private emit(event: SessionEvent): void {
    for (const key of [event.type, '*']) for (const fn of [...(this.listeners.get(key) ?? [])]) fn(event)
  }

  // ── phase and usage ──────────────────────────────────────────────────────────────────────────────────────────

  /** The turn phase now (`phase` events report every change). */
  get phase(): TurnPhase {
    return this.state.phase
  }

  /** The usage shares now (percentages only), or null until known. */
  get usage(): UsageShares | null {
    return this.state.usage
  }

  private readonly usageListeners = new Set<(u: UsageShares) => void>()

  /** Called whenever the usage shares change (token, session info, `usage` events). */
  subscribeUsage(fn: (u: UsageShares) => void): () => void {
    this.usageListeners.add(fn)
    return () => this.usageListeners.delete(fn)
  }

  /** For voice engines: what the voice is doing (null when voice stops). Drives `listening`/`speaking`. */
  setVoiceActivity(activity: VoiceActivity | null): void {
    this.store.dispatch({ type: 'voice', activity })
  }

  /** For voice engines and stop buttons: the turn was cut off (phase `interrupted` until the next activity). */
  markInterrupted(): void {
    this.store.dispatch({ type: 'interrupted' })
  }

  /** External storage: the history `getHistory` provides (null without one). */
  async history(): Promise<HistoryMessage[] | null> {
    return (await this.opts.getHistory?.()) ?? null
  }

  // ── token ────────────────────────────────────────────────────────────────────────────────────────────────────

  private accept(info: TokenInfo): string {
    if (typeof info === 'string') {
      this.tok = { token: info }
      return info
    }
    this.tok = { token: info.token, ...(info.expiresAt ? { expiresAt: info.expiresAt } : {}) }
    if (info.usage) this.store.dispatch({ type: 'usage', usage: info.usage })
    if (info.voiceEngine) {
      this.engine = info.voiceEngine
      this.store.dispatch({ type: 'voiceEngine', voiceEngine: info.voiceEngine })
    }
    if (info.speech !== undefined) this.speech_ = info.speech
    if (info.conversationId && !this.state.conversationId) this.store.dispatch({ type: 'conversation', conversationId: info.conversationId })
    return info.token
  }

  /** The current session token, fetched or refreshed when needed (for transports that authenticate themselves). */
  async getAccessToken(force = false): Promise<string> {
    const near = this.tok?.expiresAt !== undefined && this.tok.expiresAt - REFRESH_MARGIN_S < Date.now() / 1000
    if (this.tok && !force && !(near && this.opts.getToken)) return this.tok.token
    if (!this.opts.getToken) {
      if (this.tok) return this.tok.token
      throw new EvokaiError(401, 'unauthorized', 'No session token')
    }
    const info = await this.opts.getToken({
      conversationId: this.state.conversationId,
      reason: this.tok ? 'refresh' : 'initial',
    })
    return this.accept(info)
  }

  /** One authorised request; a refused token is refreshed once. */
  private async call<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const token = await this.getAccessToken()
    try {
      return await requestJson<T>(this.fetchFn, this.baseUrl, path, token, opts)
    } catch (e) {
      if (e instanceof EvokaiError && e.status === 401 && this.opts.getToken) {
        return requestJson<T>(this.fetchFn, this.baseUrl, path, await this.getAccessToken(true), opts)
      }
      throw e
    }
  }

  // ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────

  /** The session: conversation, agent, the subject's memories and usage shares (also updates the usage). */
  async info(): Promise<SessionInfo> {
    const info = await this.call<SessionInfo>('/v1/session')
    if (info.speech !== undefined) this.speech_ = info.speech
    this.store.dispatch({ type: 'usage', usage: info.usage })
    if (!this.state.conversationId) this.store.dispatch({ type: 'conversation', conversationId: info.conversationId })
    return info
  }

  /** Load the conversation (empty until the first message); resumes a turn that is still running. */
  async loadConversation(): Promise<SessionConversation> {
    const c = await this.call<SessionConversation>('/v1/session/conversation')
    this.store.dispatch({ type: 'loaded', conversation: c })
    if (c.runningTurnId) void this.resume(c.runningTurnId)
    return c
  }

  /** Events with seq > after, in order (replay). */
  async events(after = this.state.lastSeq): Promise<EvokaiEvent[]> {
    const r = await this.call<{ events: EvokaiEvent[] }>('/v1/session/events', { query: { after } })
    return r.events
  }

  // ── chatting ─────────────────────────────────────────────────────────────────────────────────────────────────

  /**
   * Send a message (or a decision) and stream the turn. Resolves with the turn's `done` event, or null when it was
   * aborted or failed (the failure is in `state.error` and on the assistant message).
   */
  async chat(input: ChatInput): Promise<DoneEvent | null> {
    const local = localId()
    const text = input.text?.trim()
    if (input.confirm) this.store.dispatch({ type: 'decided', actionId: input.confirm.actionId, approve: input.confirm.approve })
    const rewrite = input.rewrite && text !== undefined && !input.confirm
    const files = !input.confirm && !input.regenerate ? (input.files ?? []) : []
    const attachments = files.map(fileRef)
    for (const a of attachments) if (a.url) this.previews.add(a.url)
    // The question being rewritten (Evokai storage: the service refuses it if it is no longer the last one).
    const editing = rewrite ? [...this.state.messages].reverse().find((m) => m.role === 'user' && m.local === undefined) : undefined
    this.store.dispatch({
      type: 'user',
      local,
      ...(text !== undefined && !input.confirm ? { text } : {}),
      ...(input.answer ? { answer: true } : {}),
      ...(rewrite ? { rewrite: true } : {}),
      ...(attachments.length ? { attachments } : {}),
    })
    const history = input.history !== undefined ? input.history : await this.opts.getHistory?.()
    const page = input.page !== undefined ? input.page : this.opts.getPage?.()
    const body: Record<string, unknown> = { mode: input.mode ?? 'text' }
    if (input.regenerate) {
      // The service runs its own copy of the question again.
    } else if (input.confirm) body.confirm = input.confirm
    else if (text !== undefined) body.text = text
    if (page) body.page = page
    if (history) body.history = history
    if (input.endOfSpeechAt) body.endOfSpeechAt = input.endOfSpeechAt
    if (files.length) {
      body.attachments = await Promise.all(
        files.map(async (f, i) => ({ name: attachments[i]?.name ?? 'file', mediaType: f.type, data: await base64(f) })),
      )
    }
    if (rewrite && !input.regenerate) body.rewrite = editing?.messageId ? { messageId: editing.messageId } : {}
    const path = input.regenerate ? '/v1/session/regenerate' : '/v1/session/chat'

    const ctrl = new AbortController()
    this.controllers.add(ctrl)
    const onAbort = () => ctrl.abort()
    input.signal?.addEventListener('abort', onAbort)
    let turnId: string | null = null
    let done: DoneEvent | null = null
    try {
      let res = await this.post(body, await this.getAccessToken(), ctrl.signal, path)
      if (res.status === 401 && this.opts.getToken) res = await this.post(body, await this.getAccessToken(true), ctrl.signal, path)
      if (!res.ok || !res.body) throw await errorFromResponse(res)
      const header = res.headers.get('X-Turn-Id')
      if (header) {
        turnId = header
        this.store.dispatch({ type: 'bind', local, turnId })
      }
      try {
        for await (const msg of readSse(res.body, ctrl.signal)) {
          if (!msg.data) continue
          let event: EvokaiEvent
          try {
            event = JSON.parse(msg.data) as EvokaiEvent
          } catch {
            continue
          }
          if (!turnId && event.turnId) {
            turnId = event.turnId
            this.store.dispatch({ type: 'bind', local, turnId })
          }
          this.ingest(event)
          if (event.type === 'done' && (!turnId || event.turnId === turnId)) {
            done = event
            break
          }
        }
      } catch (e) {
        if (ctrl.signal.aborted) throw e
        // The stream broke mid-turn: fall through to replay.
      }
      if (!done && !ctrl.signal.aborted && turnId) done = await this.resume(turnId)
      return done
    } catch (e) {
      if (isAbort(e) || ctrl.signal.aborted) return null
      const err = e instanceof EvokaiError ? e : new EvokaiError(0, 'network', e instanceof Error ? e.message : String(e))
      this.store.dispatch({ type: 'failed', local, code: err.code, message: err.message })
      return null
    } finally {
      input.signal?.removeEventListener('abort', onAbort)
      this.controllers.delete(ctrl)
      this.store.dispatch({ type: 'settled', local })
    }
  }

  /**
   * An image attached to this conversation (`AttachmentRef.id`), fetched with the session token: show it with
   * `URL.createObjectURL`. Documents are never served back (404 `unknown_attachment`).
   */
  async attachment(id: string): Promise<Blob> {
    const path = `/v1/session/attachments/${encodeURIComponent(id)}`
    const get = async (token: string) =>
      this.fetchFn(joinUrl(this.baseUrl, path), { headers: { Authorization: `Bearer ${token}` } })
    let res = await get(await this.getAccessToken())
    if (res.status === 401 && this.opts.getToken) res = await get(await this.getAccessToken(true))
    if (!res.ok) throw await errorFromResponse(res)
    return res.blob()
  }

  private async post(body: Record<string, unknown>, token: string, signal: AbortSignal, path = '/v1/session/chat'): Promise<Response> {
    try {
      return await this.fetchFn(joinUrl(this.baseUrl, path), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
        signal,
      })
    } catch (e) {
      if (isAbort(e)) throw e
      throw new EvokaiError(0, 'network', e instanceof Error ? e.message : 'Network error')
    }
  }

  /**
   * Complete a turn from the replay endpoint: polls `events?after=lastSeq` until the turn's `done` arrives (or the
   * attempts run out). Resolves with that `done`, or null.
   */
  async resume(turnId?: string | null): Promise<DoneEvent | null> {
    const target = turnId ?? this.state.currentTurn?.turnId ?? null
    const attempts = this.opts.replay?.attempts ?? 8
    const delay = this.opts.replay?.delayMs ?? 500
    let found: DoneEvent | null = null
    const run = async () => {
      for (let i = 0; i < attempts && !found; i++) {
        if (i > 0) await sleep(delay * i)
        let events: EvokaiEvent[]
        try {
          events = await this.events(this.state.lastSeq)
        } catch {
          continue
        }
        for (const e of events) {
          this.ingest(e)
          if (e.type === 'done' && (!target || e.turnId === target)) found = e
        }
        if (!target && !this.state.currentTurn) break
      }
    }
    // One replay at a time: a second caller waits for the first and then checks again.
    while (this.replaying) await this.replaying
    this.replaying = run().finally(() => {
      this.replaying = null
    })
    await this.replaying
    if (!found && target && this.state.lastDone?.turnId === target) found = this.state.lastDone
    return found
  }

  /** Stop a turn (barge-in or a stop button): only that turn; the stream still ends with its `done`. */
  async cancel(turnId?: string | null): Promise<boolean> {
    const id = turnId ?? this.state.currentTurn?.turnId
    if (!id) return false
    const r = await this.call<{ ok: boolean; cancelled: boolean }>(`/v1/session/turns/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    })
    return r.cancelled
  }

  /** Close the open streams (the service cancels their turns). */
  abort(): void {
    for (const c of [...this.controllers]) c.abort()
  }

  /**
   * Edit the last question and send it again: the last turn (the question, the answer, its tool calls and results) is
   * replaced and a new turn runs. Refused with an `EvokaiConflictError` (in `state.error`, the result is null) when
   * that turn changed data (`rewrite_after_write`), when it is not the last question (`rewrite_not_last`), when there
   * is none (`rewrite_nothing`) or while a turn runs (`rewrite_running`). `ChatMessage.rewritable` says when it is
   * allowed.
   */
  rewrite(text: string, opts: Omit<ChatInput, 'confirm' | 'text' | 'rewrite'> = {}): Promise<DoneEvent | null> {
    return this.chat({ ...opts, text, rewrite: true })
  }

  /**
   * Regenerate the last answer: the last question runs again and its new answer replaces the last one (a `rewritten`
   * event, then the new turn). Allowed while `ChatMessage.regenerable` (the service refuses with `not_regenerable`
   * after a turn that changed data or ran a client tool); resolves with the new turn's `done`, or null.
   */
  regenerate(opts: Omit<ChatInput, 'confirm' | 'text' | 'rewrite' | 'regenerate'> = {}): Promise<DoneEvent | null> {
    const question = [...this.state.messages].reverse().find((m) => m.role === 'user' && m.parts.some((p) => p.kind === 'text'))
    const text = question?.parts.map((p) => (p.kind === 'text' ? p.text : '')).join('') ?? ''
    return this.chat({ ...opts, text, rewrite: true, regenerate: true })
  }

  /** Approve or reject an open confirmation. */
  confirm(actionId: string, approve: boolean, opts: Omit<ChatInput, 'confirm' | 'text'> = {}): Promise<DoneEvent | null> {
    return this.chat({ ...opts, confirm: { actionId, approve } })
  }

  /** Answer an open question with one or more of its options (or free text). */
  answer(askId: string, labels: string[], opts: Omit<ChatInput, 'confirm' | 'text'> = {}): Promise<DoneEvent | null> {
    this.store.dispatch({ type: 'answered', askId, labels })
    return this.chat({ ...opts, text: labels.join(', '), answer: true })
  }

  // ── client tools ─────────────────────────────────────────────────────────────────────────────────────────────

  /**
   * Run a custom client tool when the agent calls it (`docs/client-tools.md`). For a tool that waits, what `fn`
   * returns (or throws) is posted as its result; for one that does not, `fn` just runs. Returns an unregister
   * function. A tool without a handler that waits gets an error result, so the turn does not wait for nothing.
   */
  handleTool(name: string, fn: ClientToolHandler): () => void {
    this.tools.set(name, fn)
    return () => {
      if (this.tools.get(name) === fn) this.tools.delete(name)
    }
  }

  /**
   * Draw a tool's activity with your own UI code: `render` gets a `ToolPart` each time the tool starts and ends (and
   * a client tool's result once your handler answered). Server and MCP tools give their name and status only; use
   * `ui_chart` and `ui_diagram` for the visuals. Returns an unregister function.
   */
  renderTool(name: string, render: (part: ToolPart) => void): () => void {
    this.renderers.set(name, render)
    return () => {
      if (this.renderers.get(name) === render) this.renderers.delete(name)
    }
  }

  private render(part: ToolPart): void {
    const fn = this.renderers.get(part.name)
    if (!fn) return
    try {
      fn(part)
    } catch {
      // The page's own drawing code: its failure never stops the session.
    }
  }

  private renderEvent(e: EvokaiEvent): void {
    if (e.type === 'tool') this.render({ name: e.name, status: e.status })
    else if (e.type === 'client_tool') this.render({ name: e.name, status: e.wait ? 'start' : 'done', callId: e.callId, args: e.args })
    else if (e.type === 'chart') this.render({ name: 'ui_chart', status: 'done', result: e.chart })
    else if (e.type === 'diagram') this.render({ name: 'ui_diagram', status: 'done', result: e.diagram })
  }

  /** Answer a waiting client tool call yourself (when you listen to `client_tool` events instead of `handleTool`). */
  async toolResult(callId: string, answer: { result: unknown } | { error: string }): Promise<void> {
    await this.call('/v1/session/tool-results', { method: 'POST', body: { callId, ...answer } })
  }

  private async runTool(event: ClientToolEvent): Promise<void> {
    const fn = this.tools.get(event.name)
    if (!fn) {
      if (event.wait && this.listenerCount('client_tool') === 0) {
        await this.toolResult(event.callId, { error: `No handler for ${event.name} in this client.` }).catch(() => undefined)
      }
      return
    }
    let answer: { result: unknown } | { error: string }
    try {
      answer = { result: (await fn(event.args, event)) ?? null }
    } catch (e) {
      answer = { error: e instanceof Error ? e.message : String(e) }
    }
    if (event.wait) {
      const status = 'result' in answer ? 'done' : 'error'
      this.store.dispatch({ type: 'toolResult', callId: event.callId, status, ...answer })
      this.render({ name: event.name, status, callId: event.callId, args: event.args, ...answer })
      await this.toolResult(event.callId, answer).catch(() => undefined)
    }
  }

  private listenerCount(type: string): number {
    return this.listeners.get(type)?.size ?? 0
  }

  /** Thumbs up (1), down (-1) or none (null) on a stored assistant message. */
  async feedback(messageId: string, value: 1 | -1 | null): Promise<void> {
    this.store.dispatch({ type: 'feedback', messageId, value })
    await this.call('/v1/session/messages/' + encodeURIComponent(messageId) + '/feedback', { method: 'POST', body: { value } })
  }

  // ── memory ───────────────────────────────────────────────────────────────────────────────────────────────────

  /** What the agents remember about the person, and whether memory is on for them (and whether to ask). */
  memories(): Promise<MemoryList> {
    return this.call<MemoryList>('/v1/session/memories')
  }

  /** Record the person's answer to «may the assistant remember things about you?». A no deletes what was remembered. */
  memoryConsent(granted: boolean): Promise<{ memory: MemoryState; deleted: number }> {
    return this.call('/v1/session/memory-consent', { method: 'POST', body: { granted } })
  }

  /** Rewrite one memory (400 `memory_refused` when it holds instructions, secrets or personal data). */
  editMemory(id: string, text: string): Promise<Memory> {
    return this.call<Memory>(`/v1/session/memories/${encodeURIComponent(id)}`, { method: 'PATCH', body: { text } })
  }

  /** Delete one memory, or everything remembered about the person (no id). Resolves with how many were deleted. */
  async forgetMemory(id?: string): Promise<number> {
    const path = id ? `/v1/session/memories/${encodeURIComponent(id)}` : '/v1/session/memories'
    const r = await this.call<{ deleted: number }>(path, { method: 'DELETE' })
    return r.deleted
  }

  /** Kiosk sessions: close the session with its exit code. */
  async exit(code: string): Promise<void> {
    await this.call('/v1/session/exit', { method: 'POST', body: { code } })
  }

  // ── connections ────────────────────────────────────────────────────────────────────────────────────────────

  /** The person's connections: one per provider the application configured and granted. */
  async connections(): Promise<Connection[]> {
    const r = await this.call<{ connections: Connection[] }>('/v1/session/connections')
    return r.connections
  }

  /**
   * Start connecting a provider: open `authorizeUrl` in a new window (the link is single use, valid ten minutes).
   * `returnTo` (one of the application's origins) is where the browser goes afterwards; without it, a page says the
   * window can be closed. Read `connections()` again when the window closes.
   */
  async startConnection(provider: ConnectionProvider, returnTo?: string): Promise<ConnectionStarted> {
    return this.call<ConnectionStarted>('/v1/session/connections/' + encodeURIComponent(provider) + '/start', {
      method: 'POST',
      body: returnTo ? { returnTo } : {},
    })
  }

  /** Disconnect a provider: the person's tokens are deleted (and revoked at the provider when it can be). */
  async disconnect(provider: ConnectionProvider): Promise<void> {
    await this.call('/v1/session/connections/' + encodeURIComponent(provider), { method: 'DELETE' })
  }

  /** Report a problem with this session. */
  async reportBug(description: string, context: Record<string, unknown> = {}): Promise<string> {
    const r = await this.call<{ id: string }>('/v1/session/bug-reports', { method: 'POST', body: { description, context } })
    return r.id
  }

  /**
   * Open another conversation (or a new one with null): asks `getToken` for a token bound to it, then loads it.
   */
  async switchConversation(conversationId: string | null): Promise<void> {
    if (!this.opts.getToken) throw new Error('EvokaiSession: switching conversation needs getToken')
    this.abort()
    this.store.dispatch({ type: 'reset', conversationId })
    const info = await this.opts.getToken({ conversationId, reason: 'switch' })
    this.tok = null
    this.accept(info)
    if (typeof info !== 'string' && info.conversationId) this.store.dispatch({ type: 'conversation', conversationId: info.conversationId })
    if (conversationId) await this.loadConversation()
  }

  /** Start a new conversation (a fresh token from `getToken`). */
  newConversation(): Promise<void> {
    return this.switchConversation(null)
  }

  /** Abort streams and drop listeners. */
  dispose(): void {
    this.abort()
    this.listeners.clear()
  }

  /** Absolute URL of a service path (used by transports). */
  url(path: string): string {
    return joinUrl(this.baseUrl, path)
  }

  /** The fetch this session uses (for transports that make their own requests). */
  get fetchLike(): FetchLike {
    return this.fetchFn
  }

  /**
   * The server speech engines the token chose (`speech` at mint), or null: the browser voice engine then speaks and
   * listens on the device. Read from the token or from `info()`.
   */
  get speech(): SpeechChoice | null {
    return this.speech_
  }

  /** Server speech: a text or a `say` segment as audio and timings (`POST /v1/session/speech`). */
  speak(input: SpeakInput): Promise<SpeechResult> {
    return speakWith(this, input)
  }

  /** Server speech as a stream: audio first, timings as the engine reports them (for `TimedSpeechPlayer`). */
  speechEvents(input: SpeakInput): AsyncGenerator<SpeechStreamEvent> {
    return speechEventsWith(this, input)
  }

  /** Transcribe a recording with the session's recognition engine (`POST /v1/session/transcribe`). */
  transcribe(audio: Blob | Uint8Array, opts?: { language?: string; contentType?: string; signal?: AbortSignal }) {
    return transcribeWith(this, audio, opts)
  }
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** Whether an `error` event is a spending cap that blocked the turn (`limit` says which). */
export function isLimitError(e: SessionEvent): e is LimitErrorEvent {
  return e.type === 'error' && e.code === 'limit'
}

/** What a question shows of a file before the service keeps it: an image gets a local preview. */
function fileRef(f: File | Blob): AttachmentRef {
  const name = 'name' in f && f.name ? f.name : 'file'
  const url = f.type.startsWith('image/') && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(f) : undefined
  return { name, mediaType: f.type, size: f.size, ...(url ? { url } : {}) }
}

/** A file's bytes as base64 (in slices: a large file would overflow the call stack in one go). */
async function base64(f: Blob): Promise<string> {
  const bytes = new Uint8Array(await f.arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(bin)
}
