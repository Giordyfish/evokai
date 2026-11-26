/**
 * The session's state as a pure reducer plus a tiny observable store: what a UI needs to draw a conversation:
 * messages made of parts, the running turn, the open confirmation, the open question and today's usage share.
 * Events are applied once: an event whose `seq` is not newer than the last one seen is ignored, so a replay after a
 * reconnect can overlap what the stream already delivered.
 */
import type {
  AskEvent,
  AttachmentRef,
  CapShare,
  Chart,
  Citation,
  ConfirmReason,
  Diagram,
  DoneEvent,
  EvokaiEvent,
  HistoryMessage,
  PendingConfirmation,
  RewrittenEvent,
  SessionConversation,
  StoredMessage,
  ToolStatus,
  TurnPhase,
  UsageShares,
  VoiceActivity,
  VoiceEngineKind,
} from '../core/types'

export type ConfirmState = 'open' | 'approved' | 'rejected' | 'closed'

export type Part =
  | { kind: 'text'; text: string }
  /** The model's reasoning (`show_reasoning`): show it collapsed. */
  | { kind: 'reasoning'; text: string }
  | {
      kind: 'tool'
      /**
       * `start`, `done`, `error`; for an agent that shows its tools' arguments also `args` (being written), `waiting`
       * (for the person's confirmation) and `skipped` (not run). Only `start`, `done` and `error` mean it ran.
       */
      status: ToolStatus
      name: string
      /** A custom client tool: its call, arguments and (once your handler answered) result or error. */
      client?: true
      /** The call (a client tool's, or any call the service names). */
      callId?: string
      /** A client tool's arguments, or the arguments the agent shows (`show_tool_args`). */
      args?: Record<string, unknown>
      result?: unknown
      error?: string
    }
  | { kind: 'chart'; chart: Chart }
  | { kind: 'diagram'; diagram: Diagram }
  | {
      kind: 'ask'
      askId: string
      question: string
      options: string[]
      multiple: boolean
      other: boolean
      answered: string[] | null
    }
  | { kind: 'navigate'; path: string }
  | { kind: 'connect'; provider: string; title: string }
  | { kind: 'confirm'; actionId: string; items: string[]; state: ConfirmState; why?: ConfirmReason[] }
  | { kind: 'error'; code: string; message: string }

export interface ChatMessage {
  /** Local id (stable for React keys). */
  id: string
  role: 'user' | 'assistant'
  parts: Part[]
  /** The turn this message belongs to, when known. */
  turnId?: string
  /** The stored message id (feedback, rewrite), when the service keeps conversations. */
  messageId?: string
  /** A user message: the last question, which can still be rewritten (`EvokaiSession.rewrite`). */
  rewritable?: boolean
  /** The last assistant message, when the service says it can be regenerated (`EvokaiSession.regenerate`). */
  regenerable?: boolean
  /** Being replaced by a rewrite of the last question (hide it): dropped on `rewritten`, restored on a refusal. */
  replacing?: boolean
  feedback?: number | null
  /** A user message that answered a question (shown as chips). */
  answer?: boolean
  /** Stopped before the end (barge-in, stop, superseded). */
  interrupted?: boolean
  /** Sent by this client and not yet acknowledged by an event of its turn. */
  local?: string
  /** Knowledge passages the answer may cite as `[n]`, by number. */
  citations?: Citation[]
  /** A user message: the files attached to it. */
  attachments?: AttachmentRef[]
}

export interface OpenAsk {
  askId: string
  question: string
  options: string[]
  multiple: boolean
  other: boolean
}

export interface SessionState {
  conversationId: string | null
  messages: ChatMessage[]
  /** The running turn (turnId null until the service has acknowledged it). */
  currentTurn: { turnId: string | null } | null
  busy: boolean
  pendingConfirm: PendingConfirmation | null
  openAsk: OpenAsk | null
  usage: UsageShares | null
  /** The highest event number applied (replay asks for what comes after it). */
  lastSeq: number
  /** Events up to here are in the loaded snapshot. */
  floorSeq: number
  /** Recently applied event numbers: two streams (a superseded turn and its successor) may interleave. */
  recentSeqs: number[]
  /** Server voice: whether the agent's speech is available. */
  speech: { enabled: boolean; message?: string }
  /** Who does speech for this session, as the token chose (known once a token is in hand). */
  voiceEngine: VoiceEngineKind | null
  lastDone: DoneEvent | null
  /** What a voice engine is doing (null when no voice is active). */
  voiceActivity: VoiceActivity | null
  /** The last turn was cut off (barge-in, stop, superseded); cleared by the next activity. */
  interrupted: boolean
  /** The turn phase, derived from the fields above (see `phaseOf`). */
  phase: TurnPhase
  /** A transport error (unreachable service, refused token…). */
  error: { code: string; message: string } | null
}

export type Action =
  | { type: 'toolResult'; callId: string; status: 'done' | 'error'; result?: unknown; error?: string }
  | { type: 'reset'; conversationId?: string | null }
  | { type: 'conversation'; conversationId: string }
  | { type: 'voiceEngine'; voiceEngine: VoiceEngineKind }
  | { type: 'voice'; activity: VoiceActivity | null }
  | { type: 'interrupted' }
  | { type: 'loaded'; conversation: SessionConversation; conversationId?: string | null }
  | { type: 'user'; local: string; text?: string; answer?: boolean; rewrite?: boolean; attachments?: AttachmentRef[] }
  | { type: 'bind'; local: string; turnId: string }
  | { type: 'event'; event: EvokaiEvent }
  | { type: 'decided'; actionId: string; approve: boolean }
  | { type: 'answered'; askId: string; labels: string[] }
  | { type: 'feedback'; messageId: string; value: number | null }
  | { type: 'usage'; usage: UsageShares }
  | { type: 'failed'; local?: string; code: string; message: string }
  | { type: 'settled'; local?: string }

export const initialState = (conversationId: string | null = null): SessionState => ({
  conversationId,
  messages: [],
  currentTurn: null,
  busy: false,
  pendingConfirm: null,
  openAsk: null,
  usage: null,
  lastSeq: 0,
  floorSeq: 0,
  recentSeqs: [],
  speech: { enabled: true },
  voiceEngine: null,
  lastDone: null,
  voiceActivity: null,
  interrupted: false,
  phase: 'idle',
  error: null,
})

let counter = 0
export const localId = (): string => `m${Date.now().toString(36)}${(counter++).toString(36)}`

const INTERRUPTED = new Set(['cancelled', 'barge_in', 'disconnected', 'superseded', 'closed'])

function mapMessages(msgs: ChatMessage[], fn: (m: ChatMessage) => ChatMessage): ChatMessage[] {
  let changed = false
  const out = msgs.map((m) => {
    const n = fn(m)
    if (n !== m) changed = true
    return n
  })
  return changed ? out : msgs
}

function mapParts(msgs: ChatMessage[], fn: (p: Part) => Part): ChatMessage[] {
  return mapMessages(msgs, (m) => {
    let changed = false
    const parts = m.parts.map((p) => {
      const n = fn(p)
      if (n !== p) changed = true
      return n
    })
    return changed ? { ...m, parts } : m
  })
}

/** The assistant message of a turn: bound to it, or the newest local one still waiting, or a new one. */
function withTurnMessage(state: SessionState, turnId: string | null | undefined, fn: (m: ChatMessage) => ChatMessage): SessionState {
  const msgs = state.messages
  let idx = -1
  if (turnId) idx = msgs.findIndex((m) => m.role === 'assistant' && m.turnId === turnId)
  if (idx < 0 && !turnId) {
    for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i]?.role === 'assistant') { idx = i; break }
  }
  if (idx < 0) {
    const created: ChatMessage = { id: localId(), role: 'assistant', parts: [], ...(turnId ? { turnId } : {}) }
    return { ...state, messages: [...msgs, fn(created)] }
  }
  const next = [...msgs]
  next[idx] = fn(msgs[idx] as ChatMessage)
  return { ...state, messages: next }
}

const addPart = (p: Part) => (m: ChatMessage): ChatMessage => ({ ...m, parts: [...m.parts, p] })

/** A citation without its event fields. */
export function citationOf(e: Record<string, unknown>): Citation {
  const loc = (e.location && typeof e.location === 'object' ? e.location : {}) as Citation['location']
  return {
    type: 'citation',
    index: Number(e.index),
    kb: String(e.kb ?? ''),
    documentId: String(e.documentId ?? ''),
    title: String(e.title ?? ''),
    location: { ...loc, charStart: Number(loc.charStart ?? 0), charEnd: Number(loc.charEnd ?? 0) },
    snippet: String(e.snippet ?? ''),
    url: typeof e.url === 'string' ? e.url : null,
  }
}

const withCitation = (list: Citation[], c: Citation): Citation[] =>
  [...list.filter((x) => x.index !== c.index), c].sort((a, b) => a.index - b.index)

const addCitation = (c: Citation) => (m: ChatMessage): ChatMessage => ({ ...m, citations: withCitation(m.citations ?? [], c) })

/**
 * What a shown event does to its message, or null for an event that draws nothing. Live events and the stored events
 * of a reloaded conversation go through it alike, so a reload shows what was shown live.
 */
function partUpdate(e: EvokaiEvent): ((m: ChatMessage) => ChatMessage) | null {
  switch (e.type) {
    case 'text':
    case 'reasoning': {
      const kind = e.type
      return (m) => {
        const last = m.parts[m.parts.length - 1]
        if (last?.kind === kind) return { ...m, parts: [...m.parts.slice(0, -1), { kind, text: last.text + e.delta }] }
        return { ...m, parts: [...m.parts, { kind, text: e.delta }] }
      }
    }
    case 'tool':
      return (m) => {
        const args = e.args !== undefined ? { args: e.args } : {}
        if (e.id) {
          // A call with an id: its arguments, its start and its end update one card.
          const at = m.parts.findIndex((p) => p.kind === 'tool' && !p.client && p.callId === e.id)
          if (at >= 0) {
            const parts = [...m.parts]
            parts[at] = { ...(m.parts[at] as Extract<Part, { kind: 'tool' }>), status: e.status, ...args }
            return { ...m, parts }
          }
          return { ...m, parts: [...m.parts, { kind: 'tool', name: e.name, status: e.status, callId: e.id, ...args }] }
        }
        const idx = m.parts.findIndex((p) => p.kind === 'tool' && p.name === e.name && p.status === 'start')
        if (e.status !== 'start' && idx >= 0) {
          const parts = [...m.parts]
          parts[idx] = { kind: 'tool', name: e.name, status: e.status }
          return { ...m, parts }
        }
        return { ...m, parts: [...m.parts, { kind: 'tool', name: e.name, status: e.status }] }
      }
    case 'client_tool':
      // A stored call says how it ended; a live one that waits runs until the page answers.
      return addPart({ kind: 'tool', name: e.name, status: e.status ?? (e.wait ? 'start' : 'done'), client: true, callId: e.callId, args: e.args })
    case 'chart':
      return addPart({ kind: 'chart', chart: e.chart })
    case 'diagram':
      return addPart({ kind: 'diagram', diagram: e.diagram })
    case 'navigate':
      return addPart({ kind: 'navigate', path: String(e.path) })
    case 'connect':
      return addPart({ kind: 'connect', provider: String(e.provider), title: String(e.title) })
    case 'ask':
      return addPart(askPart(e))
    case 'confirm':
      return addPart({
        kind: 'confirm',
        actionId: String(e.actionId),
        items: Array.isArray(e.items) ? e.items.map(String) : [],
        state: 'open',
        ...(Array.isArray(e.why) ? { why: e.why } : {}),
      })
    case 'citation':
      return addCitation(citationOf(e as unknown as Record<string, unknown>))
    case 'error':
      return addPart({ kind: 'error', code: e.code, message: e.message })
    default:
      return null
  }
}

function applyEvent(state: SessionState, e: EvokaiEvent): SessionState {
  const turnId = e.turnId ?? null
  switch (e.type) {
    case 'conversation': {
      let s: SessionState = { ...state, conversationId: e.id }
      if (e.turnStarted) {
        s = { ...s, busy: true, currentTurn: { turnId }, error: null }
        if (turnId && !s.messages.some((m) => m.role === 'assistant' && m.turnId === turnId)) {
          s = { ...s, messages: [...s.messages, { id: localId(), role: 'assistant', parts: [], turnId }] }
        }
      }
      return s
    }
    case 'ask': {
      const s = withTurnMessage(state, turnId, addPart(askPart(e)))
      return { ...s, openAsk: { askId: e.askId, question: e.question, options: e.options, multiple: e.multiple, other: e.other } }
    }
    case 'confirm': {
      const why = Array.isArray(e.why) ? { why: e.why } : {}
      const s = withTurnMessage(state, turnId, partUpdate(e) as (m: ChatMessage) => ChatMessage)
      return { ...s, pendingConfirm: { actionId: e.actionId, items: e.items, ...why } }
    }
    case 'rewritten':
      return applyRewritten(state, e)
    case 'usage':
      return { ...state, usage: usageOf(e) }
    case 'speech':
      return { ...state, speech: { enabled: e.enabled, ...(e.message ? { message: e.message } : {}) } }
    case 'done':
      return applyDone(state, e)
    default: {
      const update = partUpdate(e)
      return update ? withTurnMessage(state, turnId, update) : state
    }
  }
}

function askPart(e: Pick<AskEvent, 'askId' | 'question' | 'options' | 'multiple' | 'other'>): Part {
  return {
    kind: 'ask',
    askId: String(e.askId),
    question: String(e.question),
    options: Array.isArray(e.options) ? e.options.map(String) : [],
    multiple: e.multiple === true,
    other: e.other === true,
    answered: null,
  }
}

/** The index of the last question shown (a user message that is not part of `skip`). */
function lastQuestion(messages: ChatMessage[], skip: (m: ChatMessage) => boolean): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i] as ChatMessage
    if (m.role === 'user' && !skip(m) && m.parts.some((p) => p.kind === 'text')) return i
  }
  return -1
}

/**
 * The last question was rewritten: drop the replaced turns. By what this client marked (`replacing`), by the replaced
 * turn ids and question id, or (another tab, external storage) the last question shown and what follows it. The new
 * turn's own messages always stay.
 */
function applyRewritten(state: SessionState, e: RewrittenEvent): SessionState {
  const fresh = (m: ChatMessage) => m.local !== undefined || (!!e.turnId && m.turnId === e.turnId)
  const replaced = new Set(e.replaced ?? [])
  let from = state.messages.findIndex((m) => !fresh(m) && (m.replacing || (!!m.turnId && replaced.has(m.turnId)) || (!!e.messageId && m.messageId === e.messageId)))
  if (from < 0) from = lastQuestion(state.messages, fresh)
  if (from < 0) return state
  const messages = state.messages.filter((m, i) => i < from || fresh(m))
  return { ...state, messages, pendingConfirm: null, openAsk: null }
}

/** After a turn: only the last question may be rewritten, and only when the service says so. */
function withRewritable(messages: ChatMessage[], rewritable: boolean | undefined): ChatMessage[] {
  if (rewritable === undefined) return messages
  const last = lastQuestion(messages, () => false)
  return mapMessages(messages, (m) => {
    if (m.role !== 'user') return m
    const value = messages[last] === m && rewritable
    return m.rewritable === value ? m : { ...m, rewritable: value }
  })
}

/** After a turn: only the last answer may be regenerated, and only when the service says so. */
function withRegenerable(messages: ChatMessage[], regenerable: boolean | undefined): ChatMessage[] {
  if (regenerable === undefined) return messages
  let last = -1
  for (let i = messages.length - 1; i >= 0 && last < 0; i--) if (messages[i]?.role === 'assistant') last = i
  return mapMessages(messages, (m) => {
    if (m.role !== 'assistant') return m
    const value = messages[last] === m && regenerable
    return (m.regenerable ?? false) === value ? m : { ...m, regenerable: value }
  })
}

const unfinished = (p: Part): boolean => p.kind === 'tool' && !p.client && p.status === 'args'

function applyDone(state: SessionState, e: DoneEvent): SessionState {
  const turnId = e.turnId ?? null
  let messages = state.messages
  if (turnId) {
    messages = messages
      .map((m) => {
        if (m.turnId !== turnId) return m
        const { local: _local, ...rest } = m
        // A call still being written when the turn ended never ran: the service does not keep it either.
        if (rest.parts.some(unfinished)) rest.parts = rest.parts.filter((p) => !unfinished(p))
        return INTERRUPTED.has(String(e.reason)) && m.role === 'assistant' ? { ...rest, interrupted: true } : rest
      })
      .filter((m) => m.role === 'user' || m.parts.length > 0)
    // A turn that did not start here (server voice): show what the user said, from the returned messages.
    const shown = messages.some((m) => m.role === 'user' && m.turnId === turnId)
    const said = (e.messages ?? []).filter((x) => x.role === 'user' && x.text).map((x) => x.text as string)
    if (!shown && said.length) {
      const at = messages.findIndex((m) => m.turnId === turnId)
      const users: ChatMessage[] = said.map((text) => ({ id: localId(), role: 'user', parts: [{ kind: 'text', text }], turnId }))
      messages = at < 0 ? [...messages, ...users] : [...messages.slice(0, at), ...users, ...messages.slice(at)]
    }
  }
  const running = !!state.currentTurn && state.currentTurn.turnId === turnId
  messages = withRegenerable(withRewritable(messages, e.rewritable), e.regenerable)
  return {
    ...state,
    messages,
    conversationId: e.conversationId || state.conversationId,
    currentTurn: running ? null : state.currentTurn,
    busy: running ? false : state.busy,
    interrupted: running && INTERRUPTED.has(String(e.reason)) ? true : state.interrupted,
    lastDone: e,
  }
}

/**
 * Messages as the service stored them → messages with parts, as they were shown live: a turn's assistant messages make
 * one message, whose parts are its stored events applied in order (the same updates as live events). A message stored
 * before ordered parts shows its text, then its events. Confirmations take their outcome, questions their answer.
 */
export function fromStored(stored: StoredMessage[], pending: PendingConfirmation | null): ChatMessage[] {
  const out: ChatMessage[] = []
  let lastConfirm: Extract<Part, { kind: 'confirm' }> | null = null
  /** The assistant message being built: the next assistant message of the same turn joins it. */
  let open: ChatMessage | null = null
  const close = () => {
    if (open && open.parts.length) out.push(open)
    open = null
  }
  for (const m of stored) {
    const events = Array.isArray(m.events) ? m.events : []
    const turnId = typeof m.turnId === 'string' && m.turnId ? m.turnId : undefined
    if (m.role === 'user') {
      const decided = events.find((e) => e.type === 'confirmed')
      if (decided && decided.type === 'confirmed' && lastConfirm) lastConfirm.state = decided.approve ? 'approved' : 'rejected'
      // A question or a decision starts another turn; a tool's result (no text) is part of the same one.
      if (m.text || m.attachments?.length || decided) close()
      if (!m.text) continue
      out.push({
        id: m.id || localId(),
        role: 'user',
        parts: [{ kind: 'text', text: m.text }],
        ...(turnId ? { turnId } : {}),
        ...(m.id ? { messageId: m.id } : {}),
        ...(m.rewritable !== undefined ? { rewritable: m.rewritable } : {}),
        ...(m.attachments?.length ? { attachments: m.attachments } : {}),
        feedback: m.feedback ?? null,
        ...(m.interrupted ? { interrupted: true } : {}),
      })
      continue
    }
    const current = open as ChatMessage | null
    let msg: ChatMessage
    if (current && !(current.turnId && turnId && current.turnId !== turnId)) msg = current
    else {
      close()
      msg = { id: m.id || localId(), role: 'assistant', parts: [], ...(turnId ? { turnId } : {}), feedback: null }
    }
    // Before ordered parts, the text was not among the events: it goes first.
    if (m.text && !events.some((e) => e.type === 'text')) msg = { ...msg, parts: [...msg.parts, { kind: 'text', text: m.text }] }
    for (const e of events) {
      const update = partUpdate(e)
      if (!update) continue
      msg = update(msg)
      if (e.type === 'confirm') {
        const c = msg.parts[msg.parts.length - 1] as Extract<Part, { kind: 'confirm' }>
        c.state = pending?.actionId === c.actionId ? 'open' : 'closed'
        lastConfirm = c
      }
    }
    // Thumbs go on the turn's last message with text (the one the service would regenerate).
    if (m.id && (m.text || !msg.messageId)) msg = { ...msg, messageId: m.id, feedback: m.feedback ?? msg.feedback ?? null }
    if (m.regenerable) msg = { ...msg, regenerable: true }
    if (m.interrupted) msg = { ...msg, interrupted: true }
    open = msg
  }
  close()
  // A question is answered by the user message that follows it; its text is the chosen labels joined by ", ".
  out.forEach((m, i) =>
    m.parts.forEach((p) => {
      if (p.kind !== 'ask') return
      const reply = out.slice(i + 1).find((x) => x.role === 'user')
      if (!reply) return
      const text = reply.parts.map((x) => (x.kind === 'text' ? x.text : '')).join('').trim()
      const picked = p.options.filter((o) => text.split(', ').includes(o))
      p.answered = picked.length ? picked : [text]
      reply.answer = true
    }),
  )
  return out
}

/** History messages kept by the application (external storage) → messages with parts. */
export function fromHistory(history: HistoryMessage[]): ChatMessage[] {
  return fromStored(
    history.map((h, i) => ({
      id: `h${i}`,
      role: h.role,
      text: h.text ?? '',
      events: (h.events ?? []) as unknown as StoredMessage['events'],
      feedback: null,
      interrupted: h.interrupted ?? false,
      attachments: (h.content ?? []).flatMap((b) =>
        'file' in b ? [{ id: b.file.id, name: b.file.name, mediaType: b.file.mediaType, size: b.file.size ?? 0 }] : [],
      ),
    })),
    null,
  ).map((m) => {
    const { messageId: _drop, ...rest } = m
    return rest
  })
}

function lastOpenAsk(messages: ChatMessage[]): OpenAsk | null {
  const last = messages[messages.length - 1]
  if (last?.role !== 'assistant') return null
  const p = last.parts.find((x) => x.kind === 'ask' && !x.answered)
  return p && p.kind === 'ask' ? { askId: p.askId, question: p.question, options: p.options, multiple: p.multiple, other: p.other } : null
}

const RECENT = 512

/** Whether an event number was already applied (or is part of the loaded snapshot). */
export function seen(state: SessionState, seq: number): boolean {
  return seq <= state.floorSeq || state.recentSeqs.includes(seq) || seq <= state.lastSeq - RECENT
}

function reduceRaw(state: SessionState, action: Action): SessionState {
  switch (action.type) {
    case 'toolResult':
      return {
        ...state,
        messages: mapMessages(state.messages, (m) =>
          m.parts.some((p) => p.kind === 'tool' && p.callId === action.callId)
            ? {
                ...m,
                parts: m.parts.map((p) =>
                  p.kind === 'tool' && p.callId === action.callId
                    ? { ...p, status: action.status, ...(action.status === 'done' ? { result: action.result } : { error: action.error }) }
                    : p,
                ),
              }
            : m,
        ),
      }
    case 'reset':
      return { ...initialState(action.conversationId ?? null), voiceEngine: state.voiceEngine, voiceActivity: state.voiceActivity }
    case 'voice':
      return state.voiceActivity === action.activity
        ? state
        : { ...state, voiceActivity: action.activity, interrupted: action.activity === 'speaking' ? false : state.interrupted }
    case 'interrupted':
      return { ...state, interrupted: true, voiceActivity: state.voiceActivity === 'speaking' ? 'listening' : state.voiceActivity }
    case 'voiceEngine':
      return state.voiceEngine === action.voiceEngine ? state : { ...state, voiceEngine: action.voiceEngine }
    case 'conversation':
      return state.conversationId === action.conversationId ? state : { ...state, conversationId: action.conversationId }
    case 'loaded': {
      const c = action.conversation
      // A snapshot older than what this client already shows (a turn started or streamed meanwhile) is dropped.
      if (state.lastSeq > (c.lastSeq || 0) || state.messages.some((m) => m.local !== undefined)) return state
      const messages = fromStored(c.messages, c.pending)
      return {
        ...state,
        conversationId: action.conversationId ?? state.conversationId,
        messages,
        lastSeq: Math.max(state.lastSeq, c.lastSeq || 0),
        floorSeq: Math.max(state.floorSeq, c.lastSeq || 0),
        recentSeqs: [],
        currentTurn: c.runningTurnId ? { turnId: c.runningTurnId } : null,
        busy: !!c.runningTurnId,
        pendingConfirm: c.pending,
        openAsk: lastOpenAsk(messages),
        error: null,
      }
    }
    case 'user': {
      let messages = state.messages
      // A rewrite: the last question and what follows it are hidden until the service replaces them.
      if (action.rewrite && action.text !== undefined) {
        const from = lastQuestion(messages, (m) => m.local !== undefined)
        if (from >= 0) messages = messages.map((m, i) => (i >= from && m.local === undefined ? { ...m, replacing: true } : m))
      }
      // A typed message supersedes an open confirmation; it answers an open question.
      if (action.text !== undefined && state.pendingConfirm) {
        const id = state.pendingConfirm.actionId
        messages = mapParts(messages, (p) => (p.kind === 'confirm' && p.actionId === id && p.state === 'open' ? { ...p, state: 'closed' } : p))
      }
      if (action.text !== undefined && state.openAsk) {
        const ask = state.openAsk
        const labels = action.text.split(', ').filter((l) => ask.options.includes(l))
        const answered = labels.length ? labels : [action.text]
        messages = mapParts(messages, (p) => (p.kind === 'ask' && p.askId === ask.askId && !p.answered ? { ...p, answered } : p))
      }
      const added: ChatMessage[] = []
      if (action.text !== undefined) {
        added.push({
          id: localId(),
          role: 'user',
          parts: [{ kind: 'text', text: action.text }],
          local: action.local,
          ...(action.answer ? { answer: true } : {}),
          ...(action.attachments?.length ? { attachments: action.attachments } : {}),
        })
      }
      added.push({ id: localId(), role: 'assistant', parts: [], local: action.local })
      return {
        ...state,
        messages: [...messages, ...added],
        busy: true,
        interrupted: false,
        currentTurn: { turnId: null },
        pendingConfirm: action.text !== undefined ? null : state.pendingConfirm,
        openAsk: action.text !== undefined ? null : state.openAsk,
        error: null,
      }
    }
    case 'bind': {
      const messages = mapMessages(state.messages, (m) => (m.local === action.local && !m.turnId ? { ...m, turnId: action.turnId } : m))
      const currentTurn = state.currentTurn && state.currentTurn.turnId === null ? { turnId: action.turnId } : state.currentTurn
      return { ...state, messages, currentTurn }
    }
    case 'event': {
      const e = action.event
      if (typeof e.seq === 'number') {
        if (seen(state, e.seq)) return state
        return applyEvent({ ...state, lastSeq: Math.max(state.lastSeq, e.seq), recentSeqs: [...state.recentSeqs.slice(-(RECENT - 1)), e.seq] }, e)
      }
      return applyEvent(state, e)
    }
    case 'decided': {
      const messages = mapParts(state.messages, (p) =>
        p.kind === 'confirm' && p.actionId === action.actionId ? { ...p, state: action.approve ? 'approved' : 'rejected' } : p,
      )
      return { ...state, messages, pendingConfirm: state.pendingConfirm?.actionId === action.actionId ? null : state.pendingConfirm }
    }
    case 'answered': {
      const messages = mapParts(state.messages, (p) => (p.kind === 'ask' && p.askId === action.askId ? { ...p, answered: action.labels } : p))
      return { ...state, messages, openAsk: state.openAsk?.askId === action.askId ? null : state.openAsk }
    }
    case 'feedback':
      return {
        ...state,
        messages: mapMessages(state.messages, (m) => (m.messageId === action.messageId ? { ...m, feedback: action.value } : m)),
      }
    case 'usage':
      return { ...state, usage: usageOf(action.usage) }
    case 'failed': {
      const target = action.local
      let s: SessionState = { ...state, error: { code: action.code, message: action.message } }
      if (s.messages.some((m) => m.replacing)) {
        // A refused rewrite: what it would have replaced comes back, the edited text goes.
        const messages = s.messages
          .filter((m) => !(target && m.local === target && m.role === 'user'))
          .map((m) => (m.replacing ? (({ replacing: _r, ...rest }) => rest)(m) : m))
        s = { ...s, messages }
      }
      const idx = target ? s.messages.findIndex((m) => m.role === 'assistant' && m.local === target) : -1
      if (idx >= 0) {
        const messages = [...s.messages]
        const m = messages[idx] as ChatMessage
        messages[idx] = { ...m, parts: [...m.parts, { kind: 'error', code: action.code, message: action.message }] }
        s = { ...s, messages }
      }
      return s
    }
    case 'settled': {
      // The request is over: drop its empty placeholder, stop waiting if nothing else is running.
      const target = action.local
      const messages = state.messages
        .filter((m) => !(target && m.local === target && m.role === 'assistant' && m.parts.length === 0))
        .map((m) => {
          if (!target || m.local !== target) return m
          const { local: _local, ...rest } = m
          return rest
        })
      const waiting = state.currentTurn && state.currentTurn.turnId === null
      return { ...state, messages, ...(waiting ? { currentTurn: null, busy: false } : {}) }
    }
  }
}

/**
 * The turn phase: `speaking` while the agent's voice plays, `interrupted` right after a cut-off turn, `thinking` while
 * a turn runs, `listening` while a voice engine waits for the user, `idle` otherwise.
 */
export function phaseOf(s: Pick<SessionState, 'voiceActivity' | 'interrupted' | 'busy'>): TurnPhase {
  if (s.voiceActivity === 'speaking') return 'speaking'
  if (s.interrupted) return 'interrupted'
  if (s.busy) return 'thinking'
  if (s.voiceActivity === 'listening') return 'listening'
  return 'idle'
}

export function reduce(state: SessionState, action: Action): SessionState {
  const next = reduceRaw(state, action)
  if (next === state) return next
  const phase = phaseOf(next)
  return phase === next.phase ? next : { ...next, phase }
}

/** Usage shares from an event or a session info body (absent fields are null). */
export function usageOf(u: Partial<UsageShares> | null | undefined): UsageShares {
  const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const out: UsageShares = { dayPct: n(u?.dayPct), monthPct: n(u?.monthPct), companyPct: n(u?.companyPct), monthOut: u?.monthOut === true }
  // Fields of services that know weekly caps and resets; an older service sends none of them.
  if (u?.weekPct !== undefined) out.weekPct = n(u.weekPct)
  if (u?.out !== undefined) out.out = u.out === true
  if (u?.binding !== undefined) out.binding = capShareOf(u.binding)
  if (u?.resetsAt !== undefined) out.resetsAt = typeof u.resetsAt === 'string' ? u.resetsAt : null
  if (Array.isArray(u?.caps)) out.caps = u.caps.map(capShareOf).filter((c): c is CapShare => c !== null)
  return out
}

/** One cap's share and reset, nothing else (a field that is not a share never reaches the state). */
function capShareOf(c: Partial<CapShare> | null | undefined): CapShare | null {
  if (!c || typeof c !== 'object' || typeof c.limit !== 'string' || typeof c.usedPct !== 'number') return null
  return {
    limit: c.limit,
    scope: c.scope === 'application' ? 'application' : 'subject',
    period: c.period === 'day' || c.period === 'week' ? c.period : 'month',
    usedPct: c.usedPct,
    leftPct: typeof c.leftPct === 'number' ? c.leftPct : Math.max(0, 100 - c.usedPct),
    resetsAt: typeof c.resetsAt === 'string' ? c.resetsAt : '',
  }
}

export type Listener = (state: SessionState) => void

/** A minimal observable store (works with React's useSyncExternalStore). */
export class SessionStore {
  private state: SessionState
  private readonly listeners = new Set<Listener>()

  constructor(state: SessionState = initialState()) {
    this.state = state
  }

  getState = (): SessionState => this.state

  subscribe = (fn: Listener): (() => void) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  dispatch(action: Action): SessionState {
    const next = reduce(this.state, action)
    if (next !== this.state) {
      this.state = next
      for (const fn of [...this.listeners]) fn(next)
    }
    return next
  }
}
