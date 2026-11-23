/**
 * Shapes shared by every entry point: the request bodies come from the generated OpenAPI types; the events and the
 * visuals are the server's stream contract (src/evokai/sessions/turns.py, src/evokai/tools/visuals.py), which the
 * OpenAPI schema describes only as free-form objects.
 */
import type { components } from '../generated/openapi'

type Schemas = components['schemas']

export type TokenRequest = Omit<Schemas['TokenRequest'], 'actor' | 'tags'> & { actor?: 'agent'; tags?: string[] }
export type SubjectLimits = Schemas['SubjectLimits']
export type Consent = Schemas['Consent']
export type ChatBody = Schemas['ChatBody']
export type ChatResult = Omit<Schemas['ChatResult'], 'events'> & { events: EvokaiEvent[] }
export type HistoryMessage = Schemas['HistoryMessage']
export type PageContext = Schemas['PageContext']
export type AgentDef = Schemas['AgentDef']
/**
 * An ask rule (`evokai.guard.ask`): calls of `action` (and, with `argument`, those whose value, or URL host with
 * `part: 'host'`, matches `resource`) wait for the person's confirmation. Patterns are whole values with `*` and `?`;
 * in each list (deployment, application, agent) the last rule that matches wins, and a call asks when any list says
 * so. `effect` is `ask` or `default` (cancels an `ask` above it in the same list) only: a rule never removes a
 * confirmation of another list nor of a tool that changes data.
 */
export type AskRule = Schemas['AskRule']
export type AppPatch = Omit<Schemas['AppPatch'], 'messages'> & { messages?: Partial<Record<LimitMessageKey, string>> | null }
export type KeyRequest = Schemas['KeyRequest']
export type McpServerBody = Schemas['McpServerBody']
export type SkillBody = Schemas['SkillBody']
export type VoiceBody = Schemas['VoiceBody']
export type Offer = Schemas['Offer']
export type OfferData = Schemas['OfferData']
export type Answer = Schemas['Answer']

export type VoiceEngineKind = 'browser' | 'server'

/**
 * Where a turn stands, for an interface to show: `idle` (nothing happening), `listening` (voice is waiting for the
 * user), `thinking` (a turn runs), `speaking` (the agent's voice plays), `interrupted` (a turn was just cut off).
 */
export type TurnPhase = 'idle' | 'listening' | 'thinking' | 'speaking' | 'interrupted'

/** What a voice engine reports to the session to derive the phase. */
export type VoiceActivity = 'listening' | 'speaking'
export type ChatMode = 'text' | 'voice'

/** What `POST /v1/session-tokens` returns. */
export interface SessionToken {
  token: string
  /** Unix seconds. */
  expiresAt: number
  conversationId: string
  voiceEngine: VoiceEngineKind
  /** Where the conversation lives: in Evokai, or kept by the application. */
  storageMode: StorageMode
  /** The subject's shares of the caps at minting time. */
  usage: UsageShares
  /** The agent's starter questions, to show before the first message. */
  suggestions?: string[]
  /** Whether memory is on for the person, and whether the chat panel should ask them. */
  memory?: MemoryState
}

export type StorageMode = 'evokai' | 'external'

// ── visuals ─────────────────────────────────────────────────────────────────────────────────────────────────────

export type Unit = 'eur' | 'count' | 'pct'
export type ChartType = 'bar' | 'column' | 'line' | 'area' | 'donut' | 'stacked' | 'kpi'
export interface Point { label: string; value: number }
export interface Series { name: string; points: Point[] }
export interface KpiItem { label: string; value: number; unit: Unit; delta: number | null; note: string }
export interface XYChart {
  type: 'bar' | 'column' | 'line' | 'area'
  title: string
  subtitle: string
  unit: Unit
  points: Point[]
  highlight: string | null
  name: string
  compare: Series | null
}
export interface ShareChart { type: 'donut' | 'stacked'; title: string; subtitle: string; unit: Unit; points: Point[] }
export interface KpiChart { type: 'kpi'; title: string; subtitle: string; items: KpiItem[] }
/** A picture the agent's code drew in the sandbox (`code_run`): PNG or JPEG, base64, at most 512 kB. */
export interface ImageChart { type: 'image'; title: string; subtitle: string; mediaType: 'image/png' | 'image/jpeg'; data: string }
export type Chart = XYChart | ShareChart | KpiChart | ImageChart

export type StepState = 'done' | 'current' | 'todo'
export interface StepsDiagram { kind: 'steps'; title: string; steps: Array<{ label: string; note: string; state: StepState }> }
export interface TreeDiagram {
  kind: 'tree'
  title: string
  root: { label: string; note: string }
  children: Array<{ label: string; note: string }>
}
export interface CompareDiagram {
  kind: 'compare'
  title: string
  columns: [string, string]
  rows: Array<{ label: string; left: string; right: string; differs: boolean }>
}
export type Diagram = StepsDiagram | TreeDiagram | CompareDiagram

// ── events ──────────────────────────────────────────────────────────────────────────────────────────────────────

/** Every streamed event carries the turn it belongs to and a per-session sequence number (for replay). */
export interface EventMeta {
  turnId?: string | null
  seq?: number
}

export interface ConversationEvent extends EventMeta { type: 'conversation'; id: string; turnStarted?: boolean }
export interface TextEvent extends EventMeta { type: 'text'; delta: string }
/**
 * Voice mode only: a speakable segment, numbered from 0 within the turn. `text` is what is shown; `spoken`, present only
 * when it differs, is what the speech engine says (numbers in words: «1.000» is shown, «mille» is said).
 */
/** A cue of the answer (`{wave}`, `{point:left}`) on a spoken segment: `at` is the offset in `text`, `spokenAt` in `spoken`. */
export interface SayMark {
  name: string
  arg?: string
  at: number
  spokenAt?: number
}
/**
 * A spoken segment (voice mode). `text` is the subtitle; `spoken` what the voice says, when it differs (numbers in
 * words); `tagged` the spoken form with its speech tags (`[laughs]`), for engines that perform them; `marks` the
 * cues. An empty `text` carries only cues (the answer ended on them): nothing to speak.
 */
export interface SayEvent extends EventMeta {
  type: 'say'
  index: number
  text: string
  spoken?: string
  tagged?: string
  marks?: SayMark[]
  filler?: boolean
}
/** The delimiters of the answers' inline markup (`docs/markup.md`), to hide it in what a chat shows. */
export interface MarkupView {
  cue?: { open: string; close: string }
  speech?: { open: string; close: string }
}
export interface ToolEvent extends EventMeta { type: 'tool'; name: string; status: 'start' | 'done' | 'error' }
export interface ChartEvent extends EventMeta { type: 'chart'; chart: Chart }
export interface DiagramEvent extends EventMeta { type: 'diagram'; diagram: Diagram }
export interface AskEvent extends EventMeta {
  type: 'ask'
  askId: string
  question: string
  options: string[]
  multiple: boolean
  other: boolean
}
export interface NavigateEvent extends EventMeta { type: 'navigate'; path: string }
/**
 * A custom client tool the agent called (`docs/client-tools.md`). With `wait`, the turn waits until the client posts
 * the result (`EvokaiSession.handleTool` does it for you); without, the model was already told `ok`.
 */
/**
 * One tool's activity as a page draws it (`EvokaiSession.renderTool`, `<evokai-chat>` `toolRenderers`): only what the
 * events already carry. A server or MCP tool has its name and status (never its arguments or output); a client tool
 * also has its arguments and the result your own handler returned; `ui_chart` and `ui_diagram` have the visual.
 */
export interface ToolPart {
  name: string
  status: 'start' | 'done' | 'error'
  callId?: string
  args?: Record<string, unknown>
  result?: unknown
  error?: string
}
export interface ClientToolEvent extends EventMeta {
  type: 'client_tool'
  callId: string
  name: string
  args: Record<string, unknown>
  wait: boolean
}
/** The i18n key of why a call waits for the person (`confirmReasonText` turns it into a sentence). */
export type ConfirmReasonKey = 'confirm.why.writes' | 'confirm.why.rule' | 'confirm.why.policy'
/**
 * Why one call of a confirmation waits for the person: the tool changes data (`confirm.why.writes`), the security
 * policy forces it (`confirm.why.policy`), or an ask rule asked (`confirm.why.rule`, with the rule's id, its level and
 * its label in the application's language when it has one).
 */
export interface ConfirmReason {
  key: ConfirmReasonKey | (string & {})
  rule?: string
  level?: 'deployment' | 'application' | 'agent'
  label?: string
}
/** `why[i]` says why `items[i]` waits (absent from services before ask rules). */
export interface ConfirmEvent extends EventMeta { type: 'confirm'; actionId: string; items: string[]; why?: ConfirmReason[] }
/**
 * Stored on the user message that answered a confirmation (history only, never streamed). `writes`: a tool that changes
 * data ran (the question can no longer be rewritten).
 */
export interface ConfirmedEvent extends EventMeta { type: 'confirmed'; approve: boolean; writes?: boolean }
/**
 * The last question was rewritten: the first event of the new turn after `conversation`. Drop what is shown of the
 * replaced turns (`replaced`, and the question `messageId` with everything after it). With external storage
 * `replacedFrom` is the index of the sent history the new messages replace from.
 */
export interface RewrittenEvent extends EventMeta {
  type: 'rewritten'
  replaced: string[]
  messageId?: string
  replacedFrom?: number
}
/** Shares of the caps after a model call: percentages only, never amounts. */
export interface UsageEvent extends EventMeta, UsageShares {
  type: 'usage'
}
/** Which cap blocked a turn, in the order the service checks them (the application's first, then month, week, day). */
export type LimitKind = 'app_month' | 'app_week' | 'app_day' | 'subject_month' | 'subject_week' | 'subject_day'
/** A cap's calendar window. */
export type CapPeriod = 'day' | 'week' | 'month'
/**
 * A turn blocked by a spending cap (the turn then ends with `done` reason `limit`). `message` says which cap and when it
 * resets; `resetsAt` (ISO time) is that reset. `limit: 'eval'` is an eval run's own budget (no `resetsAt`).
 */
export interface LimitErrorEvent extends EventMeta { type: 'error'; code: 'limit'; limit: LimitKind | 'eval'; message: string; resetsAt?: string }
/** Any other failure: `internal`, `model`, `empty`, `expired`… The message is safe to show. */
export interface OtherErrorEvent extends EventMeta { type: 'error'; code: Exclude<string, 'limit'>; message: string; limit?: undefined }
export type ErrorEvent = LimitErrorEvent | OtherErrorEvent
export type DoneReason =
  | 'end_turn' | 'confirm' | 'ask' | 'limit' | 'max_steps' | 'cancelled' | 'empty' | 'expired' | 'model' | 'internal'
  | 'disconnected' | 'barge_in' | 'superseded' | 'closed'
export interface DoneEvent extends EventMeta {
  type: 'done'
  conversationId: string
  reason: DoneReason | (string & {})
  /** External storage: this turn's new messages, to append to the application's copy. */
  messages?: HistoryMessage[]
  /** External storage, a rewrite: drop the sent history from this index before appending `messages`. */
  replacedFrom?: number
  /** Whether the conversation's last question can still be rewritten (absent from older services). */
  rewritable?: boolean
  /** Whether the conversation's last answer can still be regenerated (`EvokaiSession.regenerate`). */
  regenerable?: boolean
}
/** Server voice: speech went off (provider failure); the session goes on in text. */
export interface SpeechEvent extends EventMeta { type: 'speech'; enabled: boolean; reason?: string; message?: string }

/** Where a cited passage is in its document. */
export interface CitationLocation {
  page?: number
  /** The last page, when the passage spans pages. */
  pageEnd?: number
  /** The heading path, e.g. `Titolo I > Art. 3`. */
  section?: string
  charStart: number
  charEnd: number
}
/**
 * A knowledge passage the answer may cite as `[index]`: sent when the agent's search finds it, before the text that
 * cites it. `url` is the application's address of the document (the base's link template) or a signed, short-lived
 * link to the file Evokai keeps (null when there is neither).
 */
export interface CitationEvent extends EventMeta {
  type: 'citation'
  index: number
  kb: string
  documentId: string
  title: string
  location: CitationLocation
  snippet: string
  url: string | null
}
/** A citation without its turn and sequence (server runs, the console's test search). */
export type Citation = Omit<CitationEvent, keyof EventMeta>

/**
 * The agent handed the conversation to another agent of the application (its definition lists it in `handoff_to`):
 * from the next turn `to` answers, with the conversation so far. `title` is the new agent's title, for the interface.
 */
export interface HandoffEvent extends EventMeta { type: 'handoff'; from: string; to: string; title: string; reason: string }

/** A provider the person can connect (Microsoft 365, Google Workspace) for the agent to act as them. */
export type ConnectionProvider = 'microsoft365' | 'google' | 'github' | 'linear' | 'notion' | 'slack'

/**
 * A tool needs the person to connect a provider first: show a card whose button starts the connection
 * (`EvokaiSession.startConnection`), then let them ask again.
 */
export interface ConnectEvent extends EventMeta { type: 'connect'; provider: ConnectionProvider; title: string; server: string }

export type EvokaiEvent =
  | ConversationEvent | TextEvent | SayEvent | ToolEvent | ChartEvent | DiagramEvent | AskEvent | NavigateEvent
  | ConfirmEvent | ConfirmedEvent | UsageEvent | ErrorEvent | DoneEvent | SpeechEvent | CitationEvent | HandoffEvent
  | ConnectEvent | RewrittenEvent | ClientToolEvent

export type EventType = EvokaiEvent['type']

/** Client-side: the turn phase changed (never sent by the service; emitted by `EvokaiSession`). */
export interface PhaseEvent {
  type: 'phase'
  phase: TurnPhase
  previous: TurnPhase
  /** The running (or last) turn, when known. */
  turnId: string | null
}

/** Everything `EvokaiSession.on` can deliver: the service's events plus the client's `phase`. */
export type SessionEvent = EvokaiEvent | PhaseEvent
export type SessionEventType = SessionEvent['type']

// ── session (browser) responses ────────────────────────────────────────────────────────────────────────────────

/** One cap as a browser sees it: its share and when it resets, never an amount. */
export interface CapShare {
  limit: LimitKind
  scope: 'application' | 'subject'
  period: CapPeriod
  usedPct: number
  leftPct: number
  /** The next reset (ISO time): 00:00 in the application's time zone. */
  resetsAt: string
}
/**
 * What a browser may know about spending: shares of the caps, never amounts.
 * `dayPct`/`weekPct`/`monthPct` are the subject's (null when there is no such cap); `companyPct` is the application's
 * month; `monthOut` is true when a monthly cap is reached; `out` when any cap is (nothing more until `resetsAt`).
 * `binding` is the cap that limits most (a reached one that resets last, else the highest share).
 */
export interface UsageShares {
  dayPct: number | null
  weekPct?: number | null
  monthPct: number | null
  companyPct: number | null
  monthOut: boolean
  out?: boolean
  binding?: CapShare | null
  resetsAt?: string | null
  caps?: CapShare[]
}
export interface MemoryFact { id: string; fact: string; kind?: MemoryKind }

/** What an agent remembers: how the person likes answers, a fact about their work, or a finished conversation. */
export type MemoryKind = 'preference' | 'semantic' | 'episodic'

/** Whether agents may remember things about the person: the application's mode and the person's answer. */
export interface MemoryState {
  mode: 'off' | 'ask' | 'on'
  consent: 'granted' | 'declined' | 'unknown'
  /** Memory is on for this person now. */
  active: boolean
  /** Ask the person once (mode `ask`, no answer yet). */
  ask: boolean
}

/** One memory, as the person sees and edits it. */
export interface Memory {
  id: string
  text: string
  kind: MemoryKind
  agent: string | null
  /** `agent` (the agent remembered it), `summary` (a finished conversation) or `person` (edited by the person). */
  source: 'agent' | 'summary' | 'person'
  createdAt: string | null
  updatedAt: string | null
  expiresAt: string | null
  lastUsedAt: string | null
  uses: number
}

export interface MemoryList {
  memory: MemoryState
  items: Memory[]
}

export interface SessionInfo {
  conversationId: string
  agent: string
  expiresAt: number
  usage: UsageShares
  memories: MemoryFact[]
  /** Whether memory is on for the person, and whether to ask them (null when the application is gone). */
  memory?: MemoryState | null
  /** The agent's starter questions, to show before the first message. */
  suggestions?: string[]
  /** The inline markup the agent may write (null without): `stripMarkup` hides it. */
  markup?: MarkupView | null
  /** The server speech engines the token chose, null for the deployment's defaults. */
  speech?: SpeechChoice | null
}

/** The server speech engines of a session (`speech` at token mint). */
export interface SpeechChoice {
  tts: string | null
  stt: string | null
}

export interface StoredMessage {
  id: string
  role: 'user' | 'assistant'
  text: string
  events: EvokaiEvent[]
  feedback: number | null
  interrupted?: boolean
  /** User messages: whether this is the last question and can still be rewritten (`EvokaiSession.rewrite`). */
  rewritable?: boolean
  /** The last assistant message, when it can still be regenerated (`EvokaiSession.regenerate`). */
  regenerable?: boolean
}

export interface PendingConfirmation { actionId: string; items: string[]; why?: ConfirmReason[] }

export interface SessionConversation {
  messages: StoredMessage[]
  pending: PendingConfirmation | null
  lastSeq: number
  runningTurnId: string | null
}

// ── server responses ───────────────────────────────────────────────────────────────────────────────────────────

export interface ConversationSummary { id: string; title: string; agent: string; updatedAt: string }
export interface ConversationDetail {
  id: string
  title: string
  agent: string
  messages: StoredMessage[]
  pending: PendingConfirmation | null
}
export type UsageGroup = 'subject' | 'agent' | 'kind' | 'model' | 'day' | 'tags'
export interface UsageRow {
  subject?: string
  agent?: string
  kind?: string
  model?: string
  day?: string
  /** With `groupBy` tags: the tags the sessions carried. */
  tags?: string[]
  costUsd: number
  /** Every input token, the cached ones included. */
  inputTokens: number
  outputTokens: number
  /** Input tokens read from the prompt cache (billed at a tenth of the input price). */
  cacheReadTokens: number
  /** Input tokens written to the prompt cache (billed at 1.25x for 5 minutes, 2x for 1 hour). */
  cacheWriteTokens: number
  characters: number
  audioMs: number
  /** Text units billed by the injection classifier (Bedrock Guardrails, usage kind `guard`), 1,000 characters each at most. */
  textUnits: number
  calls: number
}
/** Which caps an application has; the amounts stay on the server. */
export interface CapsSet {
  monthly: boolean
  weekly?: boolean
  daily: boolean
  subjectMonthly: boolean
  subjectWeekly?: boolean
  subjectDaily: boolean
}
/** A cap of an application for the console: its share of the current window, never an amount. */
export interface ConsoleCapShare {
  limit: LimitKind
  scope: 'application' | 'subject'
  period: CapPeriod
  /** The weekday of a weekly cap (`saturday`), the day of the month of a monthly one. */
  resets: Weekday | number | null
  /** Of the application's cap; for a subject's cap, the person who used most of it. */
  usedPct: number | null
  leftPct: number | null
  resetsAt: string
}
/** An application's month, week and day as used and left percentages of its caps (null without that cap), and every cap. */
export interface AppShares {
  monthPct: number | null
  monthLeftPct: number | null
  weekPct?: number | null
  weekLeftPct?: number | null
  dayPct: number | null
  dayLeftPct: number | null
  caps?: ConsoleCapShare[]
}
export type Weekday = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday' | 'sunday'
/** A cap as an application configures it, in its currency. `resets`: a weekday for a week, a day of the month (1-31) for a month. */
export interface CapConfig { period: CapPeriod; amount: number; resets?: Weekday | number | null }
/** The application's caps per calendar window (`caps`): its own and each subject's. */
export interface CapsConfig { application: CapConfig[]; subject: CapConfig[] }
/** A cap in effect, whatever set it: `caps` (in the application's currency), `legacy` (an old `*_usd` field, in USD) or `token`. */
export interface EffectiveCap {
  kind: LimitKind
  scope: 'application' | 'subject'
  period: CapPeriod
  amount: number
  currency: string
  resets: Weekday | number | null
  source: 'caps' | 'legacy' | 'token'
}
/** A usage row for the console: tokens, calls and shares of the caps, never an amount. */
export interface ConsoleUsageRow extends Omit<UsageRow, 'costUsd'> {
  /** Of the application's monthly cap. */
  capPct: number | null
  /** Rows of a day: of the application's daily cap. */
  dayCapPct?: number | null
  /** Rows of a subject: of the subject's monthly cap. */
  subjectCapPct?: number | null
  /** Rows of a subject: what it spent today, of the subject's daily cap. */
  subjectTodayPct?: number | null
}
/** `GET /v1/usage` for a console login: shares of the caps instead of amounts. */
export interface ConsoleUsageReport { rows: ConsoleUsageRow[]; application: AppShares; caps: CapsSet }
/** A cap in the server-to-server usage report: its maximum, its window and, for the application's own caps, what it spent. */
export interface UsageReportCap extends EffectiveCap {
  capUsd: number
  windowStart: string
  resetsAt: string
  spentUsd?: number
  /** `spentUsd` in the application's currency, at `exchangeRate`. */
  spent?: number | null
}
export interface UsageReport {
  /** `cost` is `costUsd` in the application's currency, at `application.exchangeRate`. */
  rows: Array<UsageRow & { cost?: number | null }>
  application: {
    /** The application's currency and the rate (units per 1 USD) the amounts in it were converted with. */
    currency?: string
    exchangeRate?: number | null
    monthlyCapUsd: number | null
    dailyCapUsd: number | null
    monthUsd: number
    dayUsd: number
    caps?: UsageReportCap[]
  }
}
export interface AgentSummary { name: string; version: string; title: string }
export interface VoiceInfo {
  provider: string
  voiceId: string
  language: string
  name: string
  gender: string | null
  region: string | null
}
export interface Application {
  id: string
  slug: string
  name: string
  /** The old monthly cap in USD: it applies while `caps` sets no application month. */
  monthly_cap_usd: number
  daily_cap_usd: number | null
  /** The currency of `caps` (ISO code, `USD` by default); spend is recorded in USD. */
  currency?: string
  /** Units of `currency` per 1 USD (required when it is not USD). */
  exchange_rate?: number | null
  /** Caps per calendar window, in `currency` (null: the old fields only). */
  caps?: CapsConfig | null
  /** Every cap in effect, new form and old fields together. */
  effectiveCaps?: EffectiveCap[]
  allowed_origins: string[]
  allowed_models: string[]
  storage_mode: 'evokai' | 'external'
  voice_engine: VoiceEngineKind
  /** Default caps of each subject (a token may override them). */
  subject_monthly_usd: number | null
  subject_daily_usd: number | null
  /** Language of Evokai's own messages. */
  /** Language of Evokai's messages and speech rules (`en` default; `it`, `en-GB`…). */
  locale: string
  /** IANA time zone for dates in prompts and for the caps' windows (default UTC). */
  timezone: string
  /** Conversations disappear this many days after their last message; null = kept. */
  conversation_ttl_days: number | null
  /** Security events are deleted this many days after they happened; null = the deployment's default. */
  security_event_ttl_days?: number | null
  /** Data residency (null: no declaration). */
  residency?: Residency | null
  /** The data protection profile: residency, jurisdiction, retention (null: none). */
  data_protection?: DataProtection | null
  /** Audit entries are deleted this many days after the change; null = the deployment's default. */
  audit_ttl_days?: number | null
  /** Overrides of Evokai's messages (`limit.app`, `limit.appWeek`, `limit.appDay`, `limit.subjectMonth`, `limit.subjectWeek`, `limit.subjectDay`; `{resets}` says when the cap resets). */
  messages: Partial<Record<LimitMessageKey, string>>
  /** The limit messages of the application's locale (what applies without an override). */
  messageDefaults: Record<LimitMessageKey, string>
  /** Inline markup the agents may write (`docs/markup.md`); null without. */
  markup?: {
    cue?: { open: string; close: string; names?: string[] | null; names_from?: { module: string; param: string } | null; enabled?: boolean } | null
    speech?: { open: string; close: string; names?: string[] | null; enabled?: boolean } | null
  } | null
  /** What content traces keep (default `masked`; external storage keeps none). */
  tracing_content?: TraceContent
  /** The monthly budget of its evals (null: the default, 2), apart from its caps. */
  eval_monthly_usd?: number | null
  /** The model that judges rubric checks (null: rubrics are skipped). */
  eval_judge_model?: string | null
  /** Run an agent's eval cases when a new version is saved (default true). */
  eval_on_save?: boolean
  /** A test user's token is stored for forward_user MCP servers during evals. */
  hasEvalCredential?: boolean
  /** Long-term memory: off, ask (only people who said yes) or on (default, unless a person said no). */
  memory_mode?: 'off' | 'ask' | 'on'
  /** Memories expire this many days after they were written or confirmed (null: kept). */
  memory_ttl_days?: number | null
  /** Publishing a version below `redteam_min_score`: off, monitor (a warning) or enforce (held back). */
  redteam_gate?: 'off' | 'monitor' | 'enforce'
  redteam_min_score?: number
  /** Run the attack suite on every saved version (default true). */
  redteam_on_save?: boolean
  /** Shares of a cap a month-end forecast fires an alert at (75, 90, 100 by default). */
  cost_alert_thresholds?: number[]
  /** The https address that receives cost alerts (signed with the alert secret). */
  cost_alert_webhook?: string | null
  /** A webhook secret is stored (it is never shown). */
  hasCostAlertSecret?: boolean
}

export type RunRequest = Omit<Schemas['RunRequest'], 'tags' | 'attachments' | 'store' | 'groups'> & {
  tags?: string[]
  /** The subject's groups or roles: knowledge documents with access groups are found only when one matches. */
  groups?: string[]
  attachments?: Array<{ name: string; mediaType?: string; text: string }>
  store?: boolean
}
/** A server run's answer (amounts allowed: server to server). */
export interface RunResult {
  runId: string
  /** The schema-valid JSON when the agent has an output_schema, else the answer text. */
  output: unknown
  model: string
  /** `inputTokens` includes the cache reads and writes. */
  usage: { inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheWriteTokens: number; costUsd: number; toolCalls: number }
  conversationId: string | null
  /** Knowledge passages the run found, numbered as the answer cites them (`[n]`). */
  citations: Citation[]
}
export interface AgentVersion {
  version: string
  active: boolean
  createdAt: string
  /** `config` (the config file), `operator`, or the id of the admin key that saved it. */
  createdBy: string | null
  definition: AgentDef
}
export interface McpTestResult {
  ok: boolean
  latencyMs?: number
  tools?: Array<{ name: string; description: string; readOnly: boolean; destructive: boolean }>
  error?: string
}
/** A model the console offers (setup wizard, agent editor), within the deployment's allow-list. */
export interface ModelSuggestion {
  id: string
  label: string
  provider: string
  /** Credentials found (true), missing (false) or unknown (null, e.g. an AWS role). */
  ready: boolean | null
  note: string
}
/** A SKILL.md as the service reads it. */
export interface ParsedSkill {
  name: string
  description: string
  body: string
  size: number
}
export interface AdminConversation {
  id: string
  subject: string
  /** Null for another person's conversation with the console assistant (an owner's list shows no content). */
  title: string | null
  /** On the assistant's system application: whether it is the caller's own conversation. */
  own?: boolean
  agent: string
  agentVersion: string
  status: string
  voiceEngine: VoiceEngineKind
  /** Null for conversations older than per-conversation storage modes (the application's mode). */
  storageMode: StorageMode | null
  updatedAt: string
  /** Turns, thumbs and reviewers' notes of the conversation. */
  turns?: number
  thumbsUp?: number
  thumbsDown?: number
  notes?: number
}
export interface AdminConversationQuery {
  subject?: string
  agent?: string
  /** Only conversations with a thumbs up, down, or any. */
  feedback?: 'up' | 'down' | 'any'
  /** Only conversations with a reviewer's note. */
  noted?: boolean
  /** The `next` of the previous page. */
  before?: string
  limit?: number
}
export interface AdminTurn { id: string; mode: ChatMode; status: string; reason: string | null; timings: Record<string, unknown> | null }
export interface AdminConversationDetail {
  id: string
  subject: string
  title: string
  agent: string
  agentVersion: string
  voiceEngine: VoiceEngineKind
  messages: Array<StoredMessage & { interrupted: boolean; turnId?: string | null }>
  turns: AdminTurn[]
  /** The trace of each turn (summaries; open one with `admin.traces.get`). */
  traces?: TraceSummary[]
  /** Reviewers' notes and labels, oldest first. */
  notes?: ReviewNote[]
}

// ── traces ──────────────────────────────────────────────────────────────────────────────────────────────────

export type TraceKind = 'turn' | 'run' | 'eval' | 'workflow'
/** What content a trace holds: `none` (timings and counts), `masked` (identifiers masked) or `full`. */
export type TraceContent = 'none' | 'masked' | 'full'
/** A trace without its spans. Usage is a share of the monthly cap (`capPct`), never an amount. */
export interface TraceSummary {
  id: string
  kind: TraceKind
  conversationId: string | null
  /** A keyed hash of the subject (search with it, or with the subject itself). */
  subjectKey: string
  agent: string
  version: string
  mode: ChatMode
  status: string
  reason: string | null
  startedAt: string
  durationMs: number
  firstTokenMs: number | null
  model: string | null
  fallback: boolean
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheWriteTokens: number
  modelCalls: number
  toolCalls: number
  toolErrors: number
  guardEvents: number
  error: boolean
  feedback: number | null
  content: TraceContent
  capPct: number | null
}
export type SpanKind = TraceKind | 'model' | 'tool' | 'guard' | 'retrieval' | 'voice' | 'step'
/** One step of a trace; `start` and `ms` are milliseconds from the trace's start. */
export interface TraceSpan {
  id: string
  parent?: string
  kind: SpanKind
  name: string
  start: number
  ms: number
  status: 'ok' | 'error' | 'cancelled'
  attrs?: Record<string, unknown>
  /** Messages, tool arguments and results, as the application's tracing content allows. */
  content?: Record<string, unknown>
}
export interface TraceDetail extends TraceSummary {
  spans: TraceSpan[]
  /** The conversation, and whether it can be opened (kept in Evokai and still alive). */
  conversation: { id: string; readable: boolean } | null
  notes: ReviewNote[]
}
export interface TraceQuery {
  from?: Date | string
  to?: Date | string
  agent?: string
  version?: string
  /** A subject, or its key as a trace shows it. */
  subject?: string
  /** A status (`done`, `failed`, `blocked`, `interrupted`) or a reason (`end_turn`, `confirm`, `limit`...). */
  outcome?: string
  kind?: TraceKind
  /** Only traces with a guard decision. */
  guard?: boolean
  feedback?: 'up' | 'down' | 'any'
  /** Only traces at least this long. */
  minMs?: number
  /** Only traces with an error (a failed turn, a failed model call or tool). */
  errors?: boolean
  before?: string
  limit?: number
}
export interface ReviewNote {
  id: string
  turnId: string | null
  label: string | null
  note: string
  author: string
  createdAt: string
}
export interface ReviewNoteBody {
  turnId?: string | null
  /** A short label: lower case letters, digits, `_`, `.`, `-` (`good`, `wrong_answer`...). */
  label?: string | null
  note?: string
}
/** One version of an agent over a period. Rates and shares are percentages; nothing is an amount. */
export interface VersionStats {
  version: string
  active: boolean
  createdAt: string | null
  turns: number
  medianMs: number | null
  p95Ms: number | null
  medianFirstTokenMs: number | null
  errorRate: number | null
  toolCalls: number
  toolErrorRate: number | null
  guardTurns: number
  guardRate: number | null
  thumbsUp: number
  thumbsDown: number
  /** Thumbs up among all thumbs, in percent. */
  feedbackRatio: number | null
  /** The version's part of the agent's usage in the period, in percent. */
  usageShare: number | null
  avgInputTokens: number | null
  avgOutputTokens: number | null
  /** Turns by reason (or status). */
  outcomes: Record<string, number>
}
export interface VersionComparison { agent: string; items: VersionStats[]; from: string; to: string }
export interface Health { database: string; voiceEnabled: boolean; modelAllow: string[]; version: string }
export type ConsoleRole = 'viewer' | 'operator' | 'owner'
export interface ConsoleMe {
  /** `op` the operator key, `adm` an admin key, `sso` single sign-on. */
  kind: 'op' | 'adm' | 'sso'
  applicationId: string | null
  expiresAt: number
  /** The admin key of the login (the login ends when it is revoked). */
  keyId: string | null
  keyPrefix: string | null
  /** Single sign-on: who signed in. */
  email?: string | null
  name?: string | null
  /** Application slug (or `*` for every application) to role; `own` for an admin key's application. */
  roles?: Record<string, ConsoleRole>
  /** Owns the deployment: creates applications, manages sign-in and console users. */
  deploymentOwner?: boolean
  modelAllow: string[]
  voiceEnabled: boolean
}
/**
 * One person's console preferences (version 1), kept per console identity (single sign-on user, admin key or the
 * operator). Every field is one of a few choices; the service refuses anything else.
 */
export interface ConsolePreferences {
  /** 2 since the logo became the mascot's default (a version 1 document's `on` reads as `hidden`). */
  version: 2
  /** `auto` follows the browser. */
  language: 'auto' | 'en' | 'it'
  /** `auto` is the language's own order. */
  dateFormat: 'auto' | 'dmy' | 'mdy' | 'ymd'
  timeFormat: 'auto' | '24h' | '12h'
  theme: 'system' | 'light' | 'dark'
  density: 'comfortable' | 'compact'
  /** `system` follows the operating system's reduced motion setting. */
  motion: 'system' | 'reduce' | 'full'
  /** Beside Evokai in the console's sidebar: the mage animated, still, or the logo (`hidden`, the default). */
  mascot: 'on' | 'still' | 'hidden'
  /** Percent. */
  textSize: 100 | 112 | 125
  /** `system` follows the operating system's contrast setting. */
  contrast: 'system' | 'more' | 'normal'
  /** The focus outline on every focused control, also after a click. */
  focusAlways: boolean
  underlineLinks: boolean
}
export interface ConsolePreferencesView {
  preferences: ConsolePreferences
  /** When they were last saved; null when this identity never saved any (the defaults). */
  updatedAt: string | null
}
export interface OperatorApplication {
  id: string
  slug: string
  name: string
  status: string
  storage_mode: 'evokai' | 'external'
  monthly_cap_usd: number | null
  daily_cap_usd: number | null
}
export type NewApplication = Schemas['NewApplication']
export interface NewApplicationKeys { id: string; serverKey: string; adminKey: string }

export type LimitMessageKey =
  | 'limit.app'
  | 'limit.appWeek'
  | 'limit.appDay'
  | 'limit.subjectMonth'
  | 'limit.subjectWeek'
  | 'limit.subjectDay'
  | 'limit.resetsDay'
  | 'limit.resetsWeek'
  | 'limit.resetsMonth'
export interface ApiKeyInfo {
  id: string
  kind: 'srv' | 'adm'
  prefix: string
  label: string
  createdAt: string
  lastUsedAt: string | null
  revoked: boolean
  /** The key making this request (it cannot revoke itself). */
  current: boolean
}
export type McpStatus = 'ok' | 'slow' | 'down'
export interface McpToolInfo { name: string; description: string; readOnly: boolean | null; destructive: boolean | null; mutating: boolean }
export interface McpServerInfo {
  name: string
  url: string
  auth: 'forward_user' | 'secret' | 'none'
  hasSecret: boolean
  timeout_seconds: number
  tool_prefix: string
  /** At its last listing or test (null when never seen). */
  status: McpStatus | null
  latencyMs: number | null
  checkedAt: string | null
  lastError: string | null
  /** The tools it listed last (null when never seen). */
  tools: McpToolInfo[] | null
  /** A ready connector served by Evokai (`sql`, `openapi`, `google_drive`, `microsoft_files`, `imap`), else null. */
  connector?: string | null
  /** The connector's configuration (environment variable names for secrets, never secrets). */
  config?: Record<string, unknown> | null
  /** An Evokai capability worker: its calls carry the conversation, the person and the agent. */
  worker?: boolean
}
/** A ready connector an application can enable, with the JSON Schema of its configuration. */
export interface ConnectorKind { kind: string; title: string; schema: Record<string, unknown> }
export interface SkillInfo {
  name: string
  description: string
  size: number
  /** Files besides SKILL.md, and how many of them are scripts. */
  files: number
  scripts: number
  updatedAt: string
}
/** `script` (scripts/*.py), `data` (under assets/ or data/) or `reference` (any other text file). */
export type SkillFileKind = 'script' | 'data' | 'reference'
/** A file of a skill, without its text. */
export interface SkillFileInfo { path: string; kind: SkillFileKind; size: number; title: string }
/** A file of a skill with its text. */
export interface SkillFile extends SkillFileInfo { content: string }
/** Whether a skill's scripts can run: code execution must be on for the deployment, the application (grant) and the agent. */
export interface SkillScripts {
  runnable: boolean
  /** The first level that is off, or null when they can run. */
  reason: 'deployment' | 'grant' | 'agent' | null
  /** Agents that may run them. */
  agents: string[]
}
/** The limits a skill is checked against (src/evokai/agents/skills.py). */
export interface SkillLimits {
  descriptionChars: number
  bodyChars: number
  readChars: number
  skillBytes: number
  files: number
  scriptBytes: number
}
export interface SkillDetail extends Omit<SkillInfo, 'files' | 'scripts'> {
  body: string
  files: SkillFileInfo[]
  scripts: SkillScripts
  limits: SkillLimits
}
/**
 * A version of an application prompt. A prompt module (`parameters` set) has a Handlebars text checked against its
 * parameter schema, and optionally a lookup table (`data`) its text reads with `{{lookup data <parameter>}}`.
 */
export interface PromptVersion {
  name: string
  version: number
  text: string
  description: string
  /** The module's parameter schema (a restricted JSON Schema), or null for a plain prompt. */
  parameters: Record<string, unknown> | null
  data: Record<string, string> | null
  /** `handlebars`, or `legacy` for a version saved before Handlebars (it keeps rendering as it did). */
  templateSyntax: 'legacy' | 'handlebars'
  createdAt: string
}
/** A prompt to save: a changed text, schema, description or data becomes the next version. */
export type PromptBody = Schemas['PromptBody']
/** Sample values for a preview, and optionally an unsaved draft (`text`, `parameters`, `data`) to render instead. */
export type PromptPreview = Schemas['PreviewBody']
/** A prompt module an agent may use, with its default state and parameters. */
export type ModuleRef = Schemas['ModuleRef']
/** A session's choice for one prompt module, at token mint (server key only). */
export type ModuleChoice = Schemas['ModuleChoice']
export interface BugReport {
  id: string
  subject: string
  sessionId: string
  description: string
  context: Record<string, unknown>
  status: string
  createdAt: string
}

// ── grants ──────────────────────────────────────────────────────────────────────────────────────────────────────

export type GrantKind =
  | 'model' | 'mcp' | 'tool' | 'skill' | 'client' | 'voice' | 'stt' | 'tts' | 'storage' | 'channel' | 'knowledge'
  | 'workflow' | 'code' | 'connection' | 'capability' | 'module' | 'openai' | 'builtin'
export type ModelGrant = 'any' | 'fallback' | 'off'
export type GrantValue = boolean | ModelGrant
export type ClientToolName = 'navigate' | 'chart' | 'diagram' | 'ask' | 'confirm' | 'memory'

/** One item an application could be granted, resolved: its default, its override (null = follows the default), its value. */
export interface GrantItem {
  kind: GrantKind
  name: string
  value: GrantValue | null
  default: GrantValue
  override: GrantValue | null
  label?: string
  description?: string
  /** Agents that use it. */
  usedBy?: string[]
  /** Models: whether the deployment and the application's allow-list let it be used at all. */
  allowed?: boolean
  pattern?: boolean
  /** Models: where its list price comes from (`fallback`: nobody prices it, it counts like an expensive model). */
  rateSource?: 'catalog' | 'genai-prices' | 'fallback' | 'free' | null
  /** Tools: whether it changes data (it asks the user first). */
  mutating?: boolean
  /** MCP servers and tools: whether it was seen in a listing. */
  listed?: boolean
  auth?: 'forward_user' | 'secret' | 'none' | null
  status?: McpStatus | null
  /** Server voice: whether this deployment runs it; STT: whether it is the configured engine. */
  enabled?: boolean
  configured?: boolean
  language?: string | null
  provider?: string | null
  region?: string | null
  /** Overridden, but no longer registered. */
  missing?: boolean
  /** Capabilities: their group, whether the deployment offers it, whether its dependencies are in
   * the image, the environment variables it still needs, whether it reaches a host (`local`,
   * `workspace`, `remote`), and what Evokai still has to wire for it. */
  group?: string
  offered?: boolean
  installed?: boolean
  missingCredentials?: string[]
  host?: 'local' | 'workspace' | 'remote' | null
  wiring?: string | null
  children?: GrantItem[]
}
export interface GrantGroup {
  id: 'models' | 'mcp' | 'skills' | 'client' | 'voice' | 'storage' | 'channels' | 'knowledge' | 'workflows' | 'code' | 'connections' | 'capabilities'
  items: GrantItem[]
}
export interface GrantSummary {
  models: { on: number; total: number; fallbackOnly: string[] }
  mcpServers: { on: number; total: number }
  tools: { on: number; total: number }
  skills: { on: number; total: number }
  clientTools: { on: number; total: number; off: string[] }
  voice: { browser: boolean; server: boolean; voices: number }
  storage: StorageMode[]
  serverRuns: boolean
  browserSessions: boolean
  /** Whether knowledge bases may be searched at all. */
  knowledge: boolean
  /** Agents may be published over A2A (off until granted). */
  a2a?: boolean
}
/** An ask rule as listed with its level and id (the deployment's defaults). */
export type PlacedAskRule = AskRule & { level: 'deployment' | 'application' | 'agent'; ruleId: string }
/** The application's ask rules, the deployment's before them, each agent's after them, and the tools they can name. */
export interface AskRulesView {
  rules: AskRule[]
  deployment: PlacedAskRule[]
  agents: Array<{ name: string; rules: AskRule[] }>
  tools: Array<{ name: string; server: string; mutating: boolean; description: string }>
}
export interface GrantView {
  groups: GrantGroup[]
  summary: GrantSummary
  /** How many items are overridden. */
  overrides: number
  modelAllow: string[]
  applicationModels: string[]
  storageMode: StorageMode
}
/** Grant or revoke an item; `value: null` resets it to the default. */
export interface GrantChange { kind: GrantKind; name: string; value: GrantValue | null }

// ── console views ───────────────────────────────────────────────────────────────────────────────────────────────

export interface ConsoleApplication {
  id: string
  slug: string
  name: string
  status: string
  storageMode: StorageMode
  voiceEngine: VoiceEngineKind
  locale: string
  timezone: string
  conversationTtlDays: number | null
  /** Which caps the application has (their amounts are never sent to the console). */
  caps: CapsSet
  /** This month and today as used and left shares of the caps. */
  usage: AppShares
  turnsMonth: number
  subjectsMonth: number
  /** `approvals`: workflow approvals waiting for a decision. */
  counts: { agents: number; mcpServers: number; skills: number; keys: number; overrides: number; workflows?: number; approvals?: number }
  /** The worst status among its MCP servers (null when none was seen). */
  mcpStatus: McpStatus | null
  grants: GrantSummary
  /** The role of this login on the application. */
  role?: ConsoleRole | null
}
export interface ConsoleHealth {
  database: { status: 'ok' | 'down'; error: string | null; latencyMs: number; dialect: string; schema: string | null; revision: string | null; head: string | null }
  models: {
    region: string
    allow: string[]
    items: Array<{ model: string; calls: number; failures: number; p50Ms: number | null; lastOkAt: number | null; lastErrorAt: number | null; lastError: string | null }>
  }
  mcp: Array<{ application: string; applicationName: string; name: string; auth: string; status: McpStatus | null; latencyMs: number | null; checkedAt: string | null; tools: number | null; error: string | null }>
  voice: { enabled: boolean; stt: string; tts: string; smartTurn: boolean; connections: number }
  /** Where rate counters and sign-in codes live: this instance's memory, or Redis shared by every instance. */
  sharedState?: { kind: 'memory' | 'redis'; ok: boolean; error: string | null }
  /** Whether traces are exported over OpenTelemetry. */
  otel?: boolean
  purge: { lastRunAt?: number; deleted?: number; nextRunAt?: number; error?: string | null; ttlApplications: number }
  build: { version: string; commit: string | null; python: string }
}
export interface ActivityItem {
  at: string
  kind: 'agent' | 'key_created' | 'key_revoked' | 'grant'
  name: string
  detail: unknown
  by: string | null
}
export interface UsageSummary {
  turns: number
  /** Turns a fallback model answered. */
  fallbackTurns: number
  medianFirstTokenMs: number | null
  subjects: number
  /** Every tag seen in the period. */
  tags: string[]
  from: string
  to: string
}
export interface ExportedConfig { yaml: string; filename: string }

// ── security layer ──────────────────────────────────────────────────────────────────────────────────────────────

export type SecurityMode = 'off' | 'monitor' | 'enforce'
/** A security policy of an application or an agent: every field optional (unset inherits). */
export type SecurityPolicy = Schemas['SecurityPolicy']

// ── compliance ──────────────────────────────────────────────────────────────────────────────────────────────────

/** What only the controller can state about an application (no personal data: refused on save). */
export type ComplianceProfile = Schemas['ComplianceProfile']
export type CompliancePackName = 'ai-act' | 'gdpr' | 'controls'
export type ComplianceLang = 'en' | 'it'
export interface ComplianceItem { id: string; text?: string; cells?: string[]; sources: string[]; flag?: 'warn' | 'human' }
export interface ComplianceBlock {
  id: string
  kind: 'p' | 'list' | 'table' | 'fields'
  title?: string
  text?: string
  columns?: string[]
  items?: ComplianceItem[]
  sources: string[]
  human?: boolean
}
/** A generated pack: sections of blocks; every statement names its sources, numbered as footnotes. */
export interface CompliancePack {
  pack: CompliancePackName
  lang: ComplianceLang
  title: string
  subtitle: string
  disclaimer: string
  meta: Array<{ label: string; value: string }>
  sections: Array<{ id: string; title: string; blocks: ComplianceBlock[] }>
  footnotes: Array<{ n: number; key: string; text: string }>
  classification?: { level: string; interacts: boolean; synthetic: string[]; obligations: string[] }
  counts?: Record<string, Record<string, number>>
}
export interface ComplianceSnapshot {
  id: string
  createdAt: string
  label: string
  byKind: string
  classification: string
  agents: number
}
export interface ComplianceDiff {
  from: { id: string; createdAt: string }
  to: { id: string; createdAt: string }
  lang: ComplianceLang
  packs: Record<
    CompliancePackName,
    {
      added: Array<{ id: string; section: string; text: string }>
      removed: Array<{ id: string; section: string; text: string }>
      changed: Array<{ id: string; section: string; before: string; after: string }>
      classification?: { before: string; after: string }
    }
  >
  facts: Array<{ path: string; before: unknown; after: unknown }>
  counts: { statements: number; facts: number }
}
export interface SecurityControl {
  id: string
  label: string
  /** OWASP Top 10 for LLM Applications 2025 ids. */
  owasp: string[]
  default: SecurityMode
  /** What enforce does. */
  enforce: string
}
export interface SecurityView {
  /** False when the deployment turned the security layer off (`EVOKAI_GUARD_ENABLED=false`). */
  enabled: boolean
  /** The application's policy as set. */
  policy: SecurityPolicy
  /** What applies to the application (before agent overrides). */
  effective: { modes: Record<string, SecurityMode>; settings: Record<string, unknown> }
  controls: SecurityControl[]
  /** Each agent's own policy and the modes that apply to it. */
  agents: Array<{ name: string; security: SecurityPolicy | null; modes: Record<string, SecurityMode> }>
  eventTtlDays: number | null
  defaultEventTtlDays: number | null
  /** The prompt-injection classifiers and whether this deployment can use each (with the reason when not). */
  classifiers: SecurityClassifier[]
  /** What `default` means in this deployment (null: no classifier available). */
  defaultClassifier: SecurityClassifierId | null
  /** The moderation classifiers (`input.moderation`) and whether this deployment can use each. */
  moderationClassifiers?: Array<{ id: ModerationClassifierId; available: boolean; reason: string | null }>
}
export type SecurityClassifierId =
  | 'bedrock_guardrails'
  | 'prompt_guard'
  | 'azure_prompt_shields'
  | 'model_armor'
  | 'mistral_moderation'
  | 'custom'
/** A moderation classifier (`moderation.classifier`): harmful content, not prompt injection. */
export type ModerationClassifierId = 'azure_content_safety' | 'openai_moderation' | 'mistral_moderation' | 'model_armor' | 'llama_guard'
/** A choice of `injection.classifier` besides `none` and `default`. */
export interface SecurityClassifier {
  id: SecurityClassifierId
  available: boolean
  /** Why it cannot be used, or the last failure of an available one. */
  reason: string | null
}
export type SecurityAction = 'logged' | 'blocked' | 'redacted' | 'removed' | 'tokenized'
/** A block, redaction or finding. The snippet is masked: never a secret or raw personal data. */
export interface SecurityEvent {
  id: string
  at: string
  control: string
  rule: string
  action: SecurityAction
  mode: SecurityMode
  severity: 'low' | 'medium' | 'high'
  direction: string | null
  agent: string
  subject: string | null
  conversationId: string | null
  turnId: string | null
  tool: string | null
  snippet: string
  details: Record<string, unknown>
}
export interface SecurityEventQuery {
  from?: Date | string
  to?: Date | string
  /** A control id, or a prefix ending with `.` (e.g. `tools.`). */
  control?: string
  rule?: string
  action?: SecurityAction
  severity?: 'low' | 'medium' | 'high'
  agent?: string
  subject?: string
  /** The `next` of the previous page. */
  before?: string
  limit?: number
}
export interface SecuritySummary {
  total: number
  byRule: Array<{ control: string; rule: string; count: number; acted: number }>
  byControl: Record<string, number>
  byAction: Record<string, number>
  bySeverity: Record<string, number>
}

// ── data protection ─────────────────────────────────────────────────────────────────────────────────────────────

/** Where an application's data lives and the inference geographies (or model patterns) its models may use. */
export type Residency = Schemas['Residency']
/** An application's data protection profile (`PATCH /v1/admin/application` `data_protection`). */
export type DataProtection = Schemas['DataProtection']

/** A source-backed fact sheet of a processor, as the provider catalog states it (no prices). */
export interface ProcessorFacts {
  provider: string
  title: string
  endpoint: string | null
  kind: string
  inProcess: boolean
  selfHosted: boolean
  geography: string | null
  countries: string[] | null
  regions: string[] | null
  entity: string | null
  controlledFrom: string | null
  /** `eu` for the EU or EEA, else the controlling country; null when unknown. */
  jurisdiction: string | null
  training: boolean | null
  retention: 'none' | 'abuse_monitoring' | 'stored' | null
  retentionDays: number | null
  zeroRetention: 'default' | 'on_request' | 'no' | null
  declaredBy: 'catalog' | 'operator' | string
  sources: string[]
  checked: string | null
}
/** Why a processor is excluded: the dimension and what failed (`unknown`: the fact is not known). */
export interface ProtectionReason {
  dimension: 'residency' | 'jurisdiction' | 'retention'
  code: 'outside' | 'unknown' | 'training' | 'retained'
  value?: unknown
}
export interface ProcessorVerdict {
  name: string
  kind: 'model' | 'embeddings' | 'classifier' | 'sandbox' | 'stt' | 'tts' | 'connection' | 'observability'
  allowed: boolean
  reasons: ProtectionReason[]
  facts: ProcessorFacts | null
  usedBy?: string[]
  configured?: boolean
  processor?: string
}
/** `GET /v1/admin/data-protection`: the profile and every external processor under it. */
export interface DataProtectionReport {
  profile: DataProtection | null
  residency: Residency | null
  empty: boolean
  processors: ProcessorVerdict[]
  browser: { online_voices: boolean; online_recognition: boolean; vendors?: string[] | null } | null
  /** The agents' provider built-in tools, and whether the profile lets the ones that keep data at the provider run. */
  builtinTools?: Array<{ agent: string; kind: string; keepsData: boolean; allowed: boolean }>
}
export interface ProviderEndpointView {
  id: string
  kind: string | null
  howToSelect: string | null
  models: string[]
  offered: string[] | null
  geography: string | null
  countries: string[] | null
  regions: string[] | null
  fromDeploymentRegion: boolean
  dataAtRest: string | null
  declaredBy: string
  connected: boolean
  notes: string | null
  sources: string[]
  checked: string | null
}
export interface ProviderEntry {
  id: string
  title: string
  aliases: string[]
  selfHosted: boolean
  entity: { name: string | null; country: string | null; controlledFrom: string | null; euContractingEntity: string | null; sources: string[] }
  terms: {
    training: boolean | null
    retention: 'none' | 'abuse_monitoring' | 'stored' | null
    retentionDays: number | null
    zeroRetention: 'default' | 'on_request' | 'no' | null
    notes: string | null
    sources: string[]
  }
  caching: { style: 'explicit' | 'automatic' | 'implicit_and_explicit' | 'none' | null; reportsUsage: boolean | null; notes: string | null; sources: string[] }
  /** Whether Evokai has list prices for it (the amounts never reach the console). */
  rated: boolean
  endpoints: ProviderEndpointView[]
  notes: string | null
  sources: string[]
  checked: string | null
  unknowns: string[]
}
/** `GET /v1/admin/providers`: the provider catalog, without prices. */
export interface ProviderCatalogView {
  version: number
  checked: string | null
  coverage: { providers: number; endpoints: number; processors: number; rates: number; known: number; unknown: number }
  providers: ProviderEntry[]
  processors: Array<{
    id: string
    kind: string
    title: string
    provider: string | null
    detects: 'injection' | 'moderation' | 'both' | null
    facts: ProcessorFacts | null
    notes: string | null
    unknowns: string[]
  }>
}
/** Everything Evokai keeps about a subject (`POST /v1/privacy/export`). Amounts only with a server key. */
export interface SubjectExport {
  subject: string
  exportedAt: string
  conversations: Array<{
    id: string
    title: string
    agent: string
    agentVersion: string
    storageMode: StorageMode | null
    status: string
    consent: Record<string, unknown> | null
    createdAt: string
    updatedAt: string
    messages: Array<{ role: string; text: string; events: unknown[]; interrupted: boolean; feedback: number | null; createdAt: string }>
    turns: Array<{ id: string; mode: string; status: string; reason: string | null; startedAt: string; endedAt: string | null }>
  }>
  memories: MemoryFact[]
  usage: Array<{ at: string; agent: string; kind: string; model: string; inputTokens: number; outputTokens: number; toolCalls: number; costUsd?: number }>
  securityEvents: Array<{ at: string; control: string; rule: string; action: string; snippet: string }>
  bugReports: Array<{ description: string; context: Record<string, unknown>; createdAt: string }>
}
/** What an erase removed (`POST /v1/privacy/erase`). */
export interface EraseResult {
  ok: true
  conversations: number
  memories: number
  bugReports: number
  /** Usage rows kept for the caps, without the subject. */
  usageDeidentified: number
  securityEventsDeidentified: number
}

// ── console sign-in and the audit log ──────────────────────────────────────────────────────────────────────────

export interface SsoInfo { enabled: boolean; label: string | null }
/** The console's OpenID Connect provider; `client_secret` is write-only (left out: the stored one stays). */
export type SsoConfig = Schemas['SsoConfigBody']
export interface SsoConfigView {
  /** `console` (set here), `environment` (`EVOKAI_SSO`) or null (no single sign-on). */
  source: 'console' | 'environment' | null
  config: (Omit<SsoConfig, 'client_secret'> & { hasClientSecret: boolean }) | null
  /** What to register at the provider as the redirect URI. */
  redirectUri: string | null
}
export interface ConsoleUser {
  id: string
  email: string
  name: string
  roles: Record<string, ConsoleRole>
  disabled: boolean
  createdAt: string
  lastLoginAt: string | null
}
/** A change: who, when, what, the state before and after (secrets masked, subjects hashed). */
export interface AuditEntry {
  id: string
  at: string
  /** The application's slug (the deployment log only; null for deployment changes). */
  application: string | null
  actor: string
  actorKind: string
  role: ConsoleRole | null
  action: string
  target: string
  before: unknown
  after: unknown
  ip: string
  correlationId: string
}
export interface AuditQuery {
  /** An action, or a prefix ending with `.` (e.g. `agent.`). */
  action?: string
  /** A part of the actor (e.g. an e-mail). */
  actor?: string
  from?: Date | string
  to?: Date | string
  before?: string
  limit?: number
}

// ── evals ──────────────────────────────────────────────────────────────────────────────────────────────────────

/** What a case's answer must satisfy. A rubric is judged by the application's judge model (skipped without one). */
export type EvalCheck =
  | { type: 'contains' | 'not_contains'; value: string; case_sensitive?: boolean }
  | { type: 'regex'; pattern: string; ignore_case?: boolean }
  | { type: 'json_schema'; schema: Record<string, unknown> }
  | { type: 'tool_called'; tool: string; args?: Record<string, unknown> | null }
  | { type: 'tool_not_called'; tool: string }
  | { type: 'rubric'; rubric: string }

export interface EvalCase {
  name: string
  input: string
  page: PageContext | null
  checks: EvalCheck[]
  enabled: boolean
  updatedAt: string
}
export interface EvalCaseBody {
  input: string
  page?: PageContext | null
  checks?: EvalCheck[]
  enabled?: boolean
}
/** A version's figures in a run: counts, the median latency, tokens, and its spend as a share of the eval budget. */
export interface EvalSummary {
  total: number
  passed: number
  failed: number
  errors: number
  skippedChecks: number
  passRate: number | null
  latencyMs: number | null
  inputTokens: number
  outputTokens: number
  capPct: number
}
export interface EvalRun {
  id: string
  agent: string
  version: string
  baseline: string | null
  trigger: 'manual' | 'save'
  status: 'running' | 'done' | 'limit' | 'failed' | 'interrupted'
  startedAt: string
  endedAt: string | null
  cases: number | null
  /** By version (the one under test and the baseline). */
  summary: Record<string, EvalSummary>
  comparison?: { passRateDelta: number | null; latencyDeltaMs: number | null; tokensVsBaselinePct: number | null }
}
export interface EvalCheckResult { check: EvalCheck; outcome: 'passed' | 'failed' | 'skipped'; detail: string }
export interface EvalCaseResult {
  passed: boolean
  checks: EvalCheckResult[]
  answer: string
  tools: Array<{ name: string; args: Record<string, unknown> }>
  reason: string
  latencyMs: number
  inputTokens: number
  outputTokens: number
  error: string | null
}
/** A run with every case and its result per version; `changed` when it passes on one version and fails on the other. */
export interface EvalRunDetail extends Omit<EvalRun, 'cases'> {
  cases: Array<{ name: string; results: Record<string, EvalCaseResult>; changed: boolean; sameAnswer: boolean }>
}
export interface EvalRuns {
  items: EvalRun[]
  budget: { usedPct: number; judge: boolean; onSave: boolean; subject: string }
}

// ── red team ───────────────────────────────────────────────────────────────────────────────────────────────────

export type RedTeamCategory =
  | 'direct_injection'
  | 'indirect_injection'
  | 'prompt_extraction'
  | 'exfiltration'
  | 'excessive_agency'
  | 'pii_leakage'
  | 'jailbreak'
  | 'obfuscation'
export interface RedTeamAttack {
  id: string
  category: RedTeamCategory
  language: string
  severity: 'high' | 'medium' | 'low'
  text: string
  /** Where the injected content comes from: the message itself, the open page, or an earlier tool result. */
  vector: 'message' | 'page' | 'tool'
  injected: string | null
  graders: string[]
}
export interface RedTeamTally { score: number | null; passed: number; failed: number; total: number }
export interface RedTeamSummary extends RedTeamTally {
  skipped: number
  byCategory: Record<string, RedTeamTally>
  byLanguage: Record<string, RedTeamTally>
  bySeverity: Record<string, RedTeamTally>
  inputTokens: number
  outputTokens: number
}
export interface RedTeamRun {
  id: string
  agent: string
  version: string
  previous: string | null
  trigger: 'manual' | 'save'
  status: 'running' | 'done' | 'limit' | 'failed' | 'interrupted'
  startedAt: string
  endedAt: string | null
  attacks: number | null
  summary: Partial<RedTeamSummary>
  /** The security score: the share of attacks that did not land, weighted by severity (null until graded). */
  score: number | null
  /** The run's spend as a share of the eval budget. */
  budgetPct: number
  /** The score is below the application's gate (monitor or enforce). */
  belowGate: boolean
}
export interface RedTeamCheck { type: string; outcome: 'passed' | 'failed' | 'skipped'; detail: string }
export interface RedTeamResult extends RedTeamAttack {
  outcome: 'passed' | 'failed' | 'skipped'
  checks: RedTeamCheck[]
  /** Masked: identifiers and secrets removed. */
  answer: string
  tools: Array<{ name: string; args: Record<string, unknown> }>
  reason: string
  error: string | null
  latencyMs: number
}
export interface RedTeamRunDetail extends RedTeamRun {
  /** Every attack with its outcome, failures first. */
  results: RedTeamResult[]
  comparison: {
    version: string
    runId: string
    score: number | null
    scoreDelta: number | null
    newlyFailing: string[]
    fixed: string[]
  } | null
}
export interface RedTeamRuns {
  items: RedTeamRun[]
  trend: Array<{ version: string; score: number; at: string }>
  versions: Array<{
    version: string
    active: boolean
    publishState: 'pending' | 'blocked' | null
    createdAt: string
    score: number | null
  }>
  gate: { mode: 'off' | 'monitor' | 'enforce'; minScore: number; onSave: boolean; judge: boolean }
  budget: { usedPct: number; subject: string }
}

// ── costs ──────────────────────────────────────────────────────────────────────────────────────────────────────

/** Month-end forecast shares of a cap (never amounts). */
export interface ForecastShares {
  usedPct: number | null
  forecastPct: number | null
  lowPct: number | null
  highPct: number | null
}
export interface CostAlert {
  id: string
  scope: 'application' | 'subject'
  subjectKey: string | null
  /** The cap's window: `2026-11` (a month from the 1st) or its first day and period letter, `2026-11-14w`. */
  period: string
  /** The cap's period. */
  cap?: CapPeriod
  threshold: number
  forecastPct: number
  usedPct: number
  firedAt: string
  delivery: { status?: number | string; at?: number; reason?: string }
  acknowledgedAt: string | null
}
export interface CostForecast {
  period: string
  application: ForecastShares & {
    capSet: boolean
    trend: 'rising' | 'flat' | 'falling'
    daysLeft: number
    /** Days so far as `usedPct`, days left as `forecastPct` (shares of the monthly cap). */
    daily: Array<{ day: string; usedPct?: number | null; forecastPct?: number | null }>
    /** When the monthly cap resets (ISO time). */
    resetsAt?: string
  }
  /** Every application cap, forecast at the end of its window. */
  caps?: Array<
    ForecastShares & {
      limit: LimitKind
      period: CapPeriod
      resets: Weekday | number | null
      resetsAt: string
      window: string
      trend: 'rising' | 'flat' | 'falling'
      daysLeft: number
    }
  >
  /** Each person with the cap they are most likely to reach (`cap`: theirs, or the application's month). */
  subjects: Array<ForecastShares & { subject: string; subjectKey: string; cap: 'subject' | 'application'; period?: CapPeriod; window?: string }>
  thresholds: number[]
  alerts: CostAlert[]
  webhook: { set: boolean; signed: boolean }
}
export interface CacheSuggestion {
  agent: string
  model: string
  stableTokens: number
  minTokens: number
  /** The share of an average call's input tokens the cache would serve (null without usage yet). */
  inputSharePct: number | null
  suggested: '5m' | '1h'
}
export interface ModelComparisonRow {
  model: string
  label: string
  passRate: number | null
  passRateDelta: number | null
  /** The model's cost on the same cases as a percentage of the current model's (100 for the current one). */
  relativeCostPct: number | null
  latencyMs: number | null
  cases: number
  errors: number
  passes: boolean
  current: boolean
  recommended: boolean
}
export interface ModelComparison {
  id: string
  status: 'running' | 'done' | 'limit' | 'failed' | 'interrupted'
  version: string
  current: string
  candidates: string[]
  tolerance: number
  cases: number | null
  startedAt: string
  endedAt: string | null
  models: ModelComparisonRow[]
  recommended: string | null
}
export interface ModelRecommendations {
  latest: ModelComparison | null
  current: string | null
  candidates: string[]
  history: Array<{ id: string; status: string; startedAt: string }>
}

// ── knowledge bases ─────────────────────────────────────────────────────────────────────────────────────────────

export type KnowledgeBaseBody = Schemas['KnowledgeBaseBody']
export type KnowledgeChunking = Schemas['ChunkSettings']
export type KnowledgeRetrieval = Schemas['RetrievalSettings']
export type KnowledgeSource = Schemas['SourceDef']
export type DocumentStatus = 'queued' | 'ingesting' | 'ready' | 'failed'

/** A knowledge base with its document counts. */
export interface KnowledgeBase {
  name: string
  title: string
  description: string
  embeddingModel: string
  dimensions: number
  language: string
  chunking: KnowledgeChunking
  retrieval: KnowledgeRetrieval
  access: string[]
  linkTemplate: string | null
  sources: KnowledgeSource[]
  retentionDays: number | null
  documents: number
  ready: number
  failed: number
  /** Queued or being ingested. */
  pending: number
  chunks: number
  updatedAt: string | null
}
export interface KnowledgeList {
  items: KnowledgeBase[]
  /** Embedding models this application may use (allow-list and data residency). */
  embeddingModels: string[]
  defaultModel: string
  languages: string[]
  /** Domains documents may be fetched from (empty: fetching by address is off). */
  fetchAllowed: string[]
  /** Google Drive and SharePoint connectors of the application, usable as sources. */
  connectors: string[]
  maxFileMb: number
}
export interface KnowledgeDocument {
  id: string
  source: string
  sourceKind: 'upload' | 'url' | 'connector'
  title: string
  mime: string
  size: number
  status: DocumentStatus
  reason: string | null
  version: number
  metadata: Record<string, unknown>
  access: string[]
  chunks: number
  pages: number | null
  tokens: number
  updatedAt: string | null
  ingestedAt: string | null
  /** A signed, short-lived link to the kept file (null for connector files). */
  url: string | null
}
export interface KnowledgeBaseDetail extends KnowledgeBase { items: KnowledgeDocument[] }
export interface KnowledgePassage {
  position: number
  kind: 'text' | 'table'
  text: string
  page: number | null
  pageEnd: number | null
  section: string
  charStart: number
  charEnd: number
  tokens: number
}
export interface KnowledgeDocumentDetail extends KnowledgeDocument { passages: KnowledgePassage[] }
export type UploadOutcome = 'created' | 'updated' | 'unchanged'
export interface KnowledgeUpload { document: KnowledgeDocument; outcome: UploadOutcome }
export interface KnowledgeSearchQuery {
  query: string
  /** Search as a caller with these groups; null or omitted: no access filter. */
  groups?: string[] | null
  filters?: Record<string, unknown> | null
  topK?: number | null
}
/** A ranked passage of a test search, with its scores and its citation. */
export interface KnowledgeHit {
  index: number
  /** Fused relevance, 0 to 1. */
  score: number
  /** Cosine similarity with the question (null when only the word search found it). */
  similarity: number | null
  vectorRank: number | null
  textRank: number | null
  kind: 'text' | 'table'
  text: string
  document: { id: string; title: string; source: string }
  location: CitationLocation
  citation: Citation
}
export interface KnowledgeSearchResult { items: KnowledgeHit[]; retrieval: KnowledgeRetrieval }
export interface KnowledgeSyncResult { queued: number; unchanged: number; removed: number; failed: number }

// ── workflows ───────────────────────────────────────────────────────────────────────────────────────────────────

export type WorkflowRunStatus = 'running' | 'waiting' | 'done' | 'failed' | 'rejected' | 'cancelled'
export type WorkflowStepKind = 'agent' | 'tool' | 'approval' | 'condition' | 'wait' | 'notify' | 'parallel'
export type WorkflowStepStatus = 'pending' | 'running' | 'waiting' | 'done' | 'failed' | 'skipped' | 'cancelled'
export type WorkflowApprovalStatus = 'pending' | 'approved' | 'rejected' | 'cancelled'

/** A workflow definition as data (see docs/workflows.md): steps, triggers, input schema, output template. */
export interface WorkflowDefinition {
  name?: string
  title?: string
  description?: string
  steps: Array<Record<string, unknown>>
  triggers?: Array<Record<string, unknown>>
  input_schema?: Record<string, unknown>
  output?: unknown
  timezone?: string
  retention_days?: number
  tags?: string[]
  [key: string]: unknown
}

export interface WorkflowTriggerInfo {
  kind: 'schedule' | 'webhook' | 'event'
  cron?: string | null
  /** The next occurrence of a schedule (ISO time). */
  nextAt?: string | null
  event?: string | null
  agent?: string | null
  status?: 'done' | 'failed' | 'any'
  secretEnv?: string | null
}

/** A step as the console draws it (a vertical flow). */
export interface WorkflowOutlineStep {
  id: string
  kind: WorkflowStepKind | '?'
  title: string
  agent?: string
  tool?: string
  if?: string
  then?: string
  else?: string
  next?: string
  approval?: string
  question?: string
  seconds?: number
  until?: string
  url?: string
  branches?: Array<{ name: string; steps: WorkflowOutlineStep[] }>
}

export interface WorkflowSummary {
  name: string
  title: string
  description: string
  version: string
  steps: number
  kinds: WorkflowStepKind[]
  triggers: WorkflowTriggerInfo[]
  nextRunAt: string | null
  lastRunAt: string | null
  /** Runs of the last 30 days per status. */
  runs: Partial<Record<WorkflowRunStatus, number>>
  timezone: string
  retentionDays: number
  createdAt: string | null
  createdBy: string | null
}
export interface WorkflowList { items: WorkflowSummary[]; pendingApprovals: number; notifyAllowed: boolean }
export interface WorkflowDetail extends WorkflowSummary {
  definition: WorkflowDefinition
  outline: WorkflowOutlineStep[]
  versions: Array<{ version: string; active: boolean; createdAt: string | null; createdBy: string | null }>
  /** Where an outside system posts its signed calls, when the workflow has a webhook trigger. */
  hookPath: string | null
}
export interface WorkflowProblem { path: string; message: string; line: number | null }
export interface WorkflowValidation {
  ok: boolean
  problems: WorkflowProblem[]
  definition: WorkflowDefinition | null
  version: string | null
  outline: WorkflowOutlineStep[]
}

/** A run: usage is a share of the application's monthly cap (`capPct`), never an amount. */
export interface WorkflowRun {
  id: string
  workflow: string
  version: string
  status: WorkflowRunStatus
  reason: string | null
  error: string | null
  trigger: 'manual' | 'api' | 'schedule' | 'webhook' | 'event'
  startedAt: string | null
  finishedAt: string | null
  durationMs: number
  capPct: number
  /** Inputs and outputs are kept with personal data masked. */
  masked: boolean
  /** With the application's key. */
  subject?: string
  /** In the console: a keyed hash of the subject. */
  subjectKey?: string
  /** Only when starting: false when the idempotency key was seen before. */
  created?: boolean
}
export interface WorkflowStepState {
  id: string
  kind: WorkflowStepKind
  /** `<parallel id>/<branch index>` for a step of a branch. */
  branch: string | null
  status: WorkflowStepStatus
  attempt: number
  dueAt: string | null
  startedAt: string | null
  finishedAt: string | null
  error: string | null
  input: unknown
  output: unknown
  /** The agent run's id (its trace), the model, the tool, the decision, the next step. */
  meta: Record<string, unknown>
  capPct: number
}
export interface WorkflowApproval {
  id: string
  runId: string
  workflow: string
  step: string
  status: WorkflowApprovalStatus
  question: string
  /** What will happen, one line each. */
  summary: string[]
  details: Record<string, unknown>
  approvers: { roles?: string[]; groups?: string[] }
  onTimeout: 'approve' | 'reject'
  expiresAt: string | null
  createdAt: string | null
  decidedBy: string | null
  decidedVia: 'console' | 'api' | 'timeout' | null
  comment: string | null
  decidedAt: string | null
  subject?: string
  subjectKey?: string
}
export interface WorkflowRunDetail extends WorkflowRun {
  input: unknown
  output: unknown
  context: unknown
  steps: WorkflowStepState[]
  approvals: WorkflowApproval[]
  outline: WorkflowOutlineStep[]
  traceId: string
}
export interface WorkflowStart {
  input?: Record<string, unknown>
  /** Whose data the run handles (erased with them). */
  subject?: string
  tags?: string[]
  /** The same key starts at most one run. */
  idempotencyKey?: string
}
export interface WorkflowRunQuery {
  workflow?: string
  status?: WorkflowRunStatus | 'active'
  subject?: string
  before?: string
  limit?: number
}
export interface WorkflowApprovalQuery {
  status?: WorkflowApprovalStatus | 'all'
  workflow?: string
  /** Server key: only the approvals a user in one of these groups may decide. */
  groups?: string[]
  subject?: string
}
export interface WorkflowDecision { decision: 'approve' | 'reject'; comment?: string }
/** A decision taken in the application's own interface, for one of its users (it vouches for their groups). */
export interface WorkflowServerDecision extends WorkflowDecision { by: { name: string; groups?: string[] } }

// ── connections (browser) ──────────────────────────────────────────────────────────────────────────────────────

/** A person's connection to a provider, as `GET /v1/session/connections` lists it. */
export type Connection = Omit<Schemas['Connection'], 'provider' | 'status'> & {
  provider: ConnectionProvider
  status: 'connected' | 'expired' | 'not_connected'
}

/** Where to send the person to connect a provider (`POST /v1/session/connections/{provider}/start`). */
export type ConnectionStarted = Schemas['ConnectionStarted']

// ── connections and channels (console) ─────────────────────────────────────────────────────────────────────────

/** A provider as the application configured it, for the console (counts only, never tokens or secrets). */
export interface ProviderSetupView {
  provider: ConnectionProvider
  title: string
  /**
   * `connector`: a delegated connector (Microsoft 365, Google Workspace) configures it, its secret in an environment
   * variable; `application`: the application's OAuth app (GitHub, Linear, Notion, Slack), set with
   * `admin.connections.setApp`, its secret encrypted and write-only.
   */
  configuredBy: 'connector' | 'application'
  /** The harness integration (capability id) that acts as the person with this connection, if any. */
  integration: string | null
  configured: boolean
  /** The MCP server (delegated connector) that configures it. */
  server: string | null
  valid: boolean
  error: string | null
  /** The environment variable holding the client secret, and whether it has a value. */
  secretEnv: string | null
  secretSet: boolean
  clientId: string | null
  tenant: string | null
  writes: boolean
  granted: boolean
  families: Array<{ family: string; granted: boolean }>
  scopes: string[]
  /** People connected, and connections that expired. */
  connected: number
  expired: number
}

/** The application's OAuth app at GitHub, Linear, Notion or Slack (`PUT /v1/admin/connections/{provider}/app`). */
export interface OAuthAppInput {
  clientId: string
  /** Required the first time; omitted keeps the stored secret (it is never returned). */
  clientSecret?: string
  /** Ask the provider's write scopes too. */
  writes?: boolean
}

export interface ConnectionsAdminView {
  providers: ProviderSetupView[]
  /** What to register at the providers: `{EVOKAI_PUBLIC_URL}/v1/oauth/callback`. */
  redirectUri: string | null
  publicUrlSet: boolean
}

export type ChannelKind = 'email' | 'teams' | 'slack'

/** A channel for the console: its configuration, the endpoints to copy, its environment and status. */
export interface ChannelView {
  kind: ChannelKind
  name: string
  agent: string
  enabled: boolean
  config: Record<string, unknown>
  valid: boolean
  error: string | null
  status: string | null
  lastError: string | null
  lastEventAt: string | null
  endpoints: Record<string, string>
  env: Array<{ name: string; set: boolean }>
  conversations: number
  granted: boolean
  agentFound: boolean
  agentAllows: boolean
}

export interface ChannelsView {
  channels: ChannelView[]
  baseUrl: string
  publicUrlSet: boolean
}

/** One check of a channel test (nothing is sent to anyone). */
export interface ChannelCheck {
  check: string
  ok: boolean
  detail: string
}
