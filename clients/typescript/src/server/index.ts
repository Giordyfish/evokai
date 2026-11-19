/**
 * Backend SDK: the application's server (and admin) API. Fetch-based, no Node-only dependency, so it runs in Node,
 * edge runtimes and serverless functions alike. Keys never belong in a browser.
 */
import {
  defaultFetch,
  EvokaiError,
  requestJson,
  type FetchLike,
  type Query,
  type RequestOptions,
} from '../core/http'
import type {
  ActivityItem,
  AgentDef,
  AgentSummary,
  ConsoleApplication,
  ConsoleHealth,
  ExportedConfig,
  AskRule,
  AskRulesView,
  GrantChange,
  GrantView,
  SkillDetail,
  UsageSummary,
  ApiKeyInfo,
  Application,
  AppPatch,
  AdminConversation,
  AdminConversationDetail,
  AdminConversationQuery,
  ReviewNote,
  ReviewNoteBody,
  TraceDetail,
  TraceQuery,
  TraceSummary,
  VersionComparison,
  AgentVersion,
  BugReport,
  ChatMode,
  EvalCase,
  EvalCaseBody,
  RedTeamAttack,
  RedTeamRunDetail,
  RedTeamRuns,
  CostAlert,
  CostForecast,
  CacheSuggestion,
  ModelRecommendations,
  MemoryState,
  EvalRunDetail,
  EvalRuns,
  ConnectorKind,
  ConsoleMe,
  ConsolePreferences,
  ConsolePreferencesView,
  Health,
  McpTestResult,
  ModelSuggestion,
  NewApplication,
  ParsedSkill,
  NewApplicationKeys,
  OperatorApplication,
  RunRequest,
  AuditEntry,
  AuditQuery,
  ConsoleUser,
  EraseResult,
  SsoConfig,
  SsoConfigView,
  SsoInfo,
  SubjectExport,
  SecurityEvent,
  SecurityEventQuery,
  SecurityPolicy,
  ComplianceDiff,
  ComplianceLang,
  CompliancePack,
  CompliancePackName,
  ComplianceProfile,
  ComplianceSnapshot,
  SecuritySummary,
  SecurityView,
  ProviderCatalogView,
  DataProtectionReport,
  RunResult,
  ChatResult,
  ConsoleUsageReport,
  ConversationDetail,
  ConversationSummary,
  HistoryMessage,
  KeyRequest,
  McpServerBody,
  McpServerInfo,
  MemoryFact,
  Connection,
  ConnectionProvider,
  ConnectionsAdminView,
  OAuthAppInput,
  ChannelCheck,
  ChannelKind,
  ChannelView,
  ChannelsView,
  PageContext,
  PromptVersion,
  SessionToken,
  SkillBody,
  SkillFile,
  SkillInfo,
  TokenRequest,
  UsageGroup,
  UsageReport,
  UsageRow,
  VoiceBody,
  VoiceInfo,
  KnowledgeBase,
  KnowledgeBaseBody,
  KnowledgeBaseDetail,
  KnowledgeDocument,
  KnowledgeDocumentDetail,
  KnowledgeList,
  KnowledgeSearchQuery,
  KnowledgeSearchResult,
  KnowledgeSyncResult,
  KnowledgeUpload,
  WorkflowApproval,
  WorkflowApprovalQuery,
  WorkflowDecision,
  WorkflowDefinition,
  WorkflowDetail,
  WorkflowList,
  WorkflowRun,
  WorkflowRunDetail,
  WorkflowRunQuery,
  WorkflowServerDecision,
  WorkflowStart,
  WorkflowValidation,
} from '../core/types'

export * from '../core/types'
export {
  isExpiredConversation,
  EvokaiError,
  EvokaiAuthError,
  EvokaiConflictError,
  EvokaiNotFoundError,
  EvokaiRateLimitError,
  EvokaiValidationError,
} from '../core/http'
export type { FetchLike, KnownErrorCode } from '../core/http'

export interface EvokaiServerOptions {
  /** Where the service is mounted, base path included (e.g. `https://agents.example.com/agent`). */
  baseUrl: string
  /** The application's server key (`srv_…`). */
  serverKey: string
  /** The application's admin key (`adm_…`), needed only for `admin.*`. */
  adminKey?: string
  /** A fetch implementation (defaults to the global one). */
  fetch?: FetchLike
}

export interface MintOptions extends Omit<TokenRequest, 'actor'> {
  subject: string
  agent: string
}

export interface ChatJsonInput {
  text?: string | null
  confirm?: { actionId: string; approve: boolean } | null
  page?: PageContext | null
  mode?: ChatMode
  /** External storage: the conversation so far, oldest first. */
  history?: HistoryMessage[] | null
  /**
   * Rewrite the last question with `text`. With external storage drop your history from `result.replacedFrom` before
   * appending `result.messages`. 409 `rewrite_after_write` when its turn changed data.
   */
  rewrite?: { messageId?: string | null } | null
}

export interface UsageQuery {
  from?: Date | string
  to?: Date | string
  /** One or more of subject, agent, kind, model, day, tags. */
  groupBy?: UsageGroup | UsageGroup[]
  subject?: string
  tag?: string
  agent?: string
}

const iso = (d: Date | string | undefined) => (d instanceof Date ? d.toISOString() : d)
const enc = encodeURIComponent
/** A relative path whose folders stay folders (`references/terms.md`), each part encoded. */
const encPath = (path: string): string => path.split('/').map(enc).join('/')

function auditQuery(q: AuditQuery): Query {
  return { action: q.action, actor: q.actor, from: iso(q.from), to: iso(q.to), before: q.before, limit: q.limit }
}

function traceQuery(q: TraceQuery): Query {
  const flag = (v: boolean | undefined) => (v ? 'true' : undefined)
  return {
    from: iso(q.from),
    to: iso(q.to),
    agent: q.agent,
    version: q.version,
    subject: q.subject,
    outcome: q.outcome,
    kind: q.kind,
    guard: flag(q.guard),
    feedback: q.feedback,
    minMs: q.minMs,
    errors: flag(q.errors),
    before: q.before,
    limit: q.limit,
  }
}

function usageQuery(q: UsageQuery): Query {
  return {
    from: iso(q.from),
    to: iso(q.to),
    groupBy: Array.isArray(q.groupBy) ? q.groupBy.join(',') : q.groupBy,
    subject: q.subject,
    tag: q.tag,
    agent: q.agent,
  }
}

export class EvokaiServer {
  protected readonly baseUrl: string
  private readonly serverKey: string
  private readonly adminKey?: string
  protected readonly fetchFn: FetchLike

  constructor(opts: EvokaiServerOptions) {
    if (!opts.baseUrl) throw new Error('EvokaiServer: baseUrl is required')
    if (!opts.serverKey) throw new Error('EvokaiServer: serverKey is required')
    this.baseUrl = opts.baseUrl
    this.serverKey = opts.serverKey
    this.adminKey = opts.adminKey
    this.fetchFn = opts.fetch ?? defaultFetch()
  }

  /** A server-API call (server key). */
  protected call<T>(path: string, opts?: RequestOptions): Promise<T> {
    return requestJson<T>(this.fetchFn, this.baseUrl, path, this.serverKey, opts)
  }

  /** An admin-API call (admin key), path under `/v1/admin`. */
  protected adminCall<T>(path: string, opts?: RequestOptions): Promise<T> {
    if (!this.adminKey) return Promise.reject(new EvokaiError(0, 'no_admin_key', 'EvokaiServer: adminKey is required for admin calls'))
    return requestJson<T>(this.fetchFn, this.baseUrl, `/v1/admin${path}`, this.adminKey, opts)
  }

  /**
   * Run an agent that allows the `server` channel and wait for its answer: the schema-valid JSON when the agent has an
   * `output_schema`, else the answer text. Errors: 403 `channel`, 429 `limit`, 502 `invalid_output`.
   */
  runAgent(name: string, req: RunRequest): Promise<RunResult> {
    const body: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(req)) if (v !== undefined) body[k] = v
    return this.call<RunResult>(`/v1/agents/${enc(name)}/run`, { method: 'POST', body })
  }

  /**
   * Mint a short-lived session token for the browser. `subject` is the application's own opaque id for the end user;
   * omit `conversationId` for a new conversation.
   */
  mintSessionToken(opts: MintOptions): Promise<SessionToken> {
    const body: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(opts)) if (v !== undefined) body[k] = v
    return this.call<SessionToken>('/v1/session-tokens', { method: 'POST', body })
  }

  /**
   * Run one turn and wait for its end, with a session token (for applications that keep conversations themselves:
   * append `result.messages` to your copy).
   */
  chatJson(token: string, input: ChatJsonInput): Promise<ChatResult> {
    return requestJson<ChatResult>(this.fetchFn, this.baseUrl, '/v1/session/chat', token, {
      method: 'POST',
      body: { mode: 'text', ...input },
    })
  }

  readonly conversations = {
    list: (q: { subject: string; limit?: number }): Promise<ConversationSummary[]> =>
      this.call<{ items: ConversationSummary[] }>('/v1/conversations', { query: q }).then((r) => r.items),
    get: (conversationId: string, q: { subject: string }): Promise<ConversationDetail> =>
      this.call<ConversationDetail>(`/v1/conversations/${enc(conversationId)}`, { query: q }),
    delete: (conversationId: string, q: { subject: string }): Promise<void> =>
      this.call<unknown>(`/v1/conversations/${enc(conversationId)}`, { method: 'DELETE', query: q }).then(() => undefined),
  }

  /** Usage of the application in a period (default: this month), grouped, with amounts (server key only). */
  usage(q: UsageQuery = {}): Promise<UsageReport> {
    return this.call<UsageReport>('/v1/usage', { query: usageQuery(q) })
  }

  readonly memories = {
    list: (q: { subject: string }): Promise<MemoryFact[]> =>
      this.call<{ items: MemoryFact[] }>('/v1/memories', { query: q }).then((r) => r.items),
    /** Forget one fact (`id`) or everything about the subject. Returns how many facts were deleted. */
    forget: (q: { subject: string; id?: string }): Promise<number> =>
      this.call<{ deleted: number }>('/v1/memories', { method: 'DELETE', query: q }).then((r) => r.deleted),
    /** Record the subject's answer to long-term memory (asked in the application's own interface); false deletes. */
    consent: (subject: string, granted: boolean): Promise<{ memory: MemoryState; deleted: number }> =>
      this.call('/v1/memories/consent', { method: 'POST', body: { subject, granted } }),
  }

  /** A subject's connections to the providers people connect (their tokens never leave the service). */
  readonly connections = {
    list: (q: { subject: string }): Promise<Connection[]> =>
      this.call<{ connections: Connection[] }>('/v1/connections', { query: q }).then((r) => r.connections),
    /** Disconnect the subject from a provider (their tokens are deleted). */
    disconnect: (provider: ConnectionProvider, q: { subject: string }): Promise<void> =>
      this.call<unknown>(`/v1/connections/${enc(provider)}`, { method: 'DELETE', query: q }).then(() => undefined),
  }

  /**
   * Data protection requests about one subject (the subject travels in the body, never in the address). `export`
   * returns everything Evokai keeps (amounts only with a server key), `erase` deletes it and de-identifies what the
   * caps still count, `revokeSessions` signs the subject out of every session token minted until now.
   */
  readonly privacy = {
    export: (subject: string): Promise<SubjectExport> =>
      this.call<SubjectExport>('/v1/privacy/export', { method: 'POST', body: { subject } }),
    erase: (subject: string): Promise<EraseResult> =>
      this.call<EraseResult>('/v1/privacy/erase', { method: 'POST', body: { subject } }),
    revokeSessions: (subject: string): Promise<void> =>
      this.call<unknown>('/v1/privacy/revoke-sessions', { method: 'POST', body: { subject } }).then(() => undefined),
  }

  /**
   * Durable workflows (docs/workflows.md): start runs, follow them, and show their approvals in the application's own
   * interface, deciding them for its users.
   */
  readonly workflows = {
    /** Start a run of a workflow (202). With `idempotencyKey`, a repeated call returns the first run. */
    start: (name: string, body: WorkflowStart = {}): Promise<WorkflowRun> =>
      this.call<WorkflowRun>(`/v1/workflows/${enc(name)}/runs`, { method: 'POST', body: { input: {}, ...body } }),
    runs: (q: WorkflowRunQuery = {}): Promise<{ items: WorkflowRun[]; next: string | null }> =>
      this.call('/v1/workflows/runs', { query: { ...q } }),
    run: (runId: string): Promise<WorkflowRunDetail> => this.call<WorkflowRunDetail>(`/v1/workflows/runs/${enc(runId)}`),
    cancel: (runId: string): Promise<WorkflowRun> =>
      this.call<WorkflowRun>(`/v1/workflows/runs/${enc(runId)}/cancel`, { method: 'POST' }),
    /** Pending approvals by default; `groups` keeps those a user in one of these groups may decide. */
    approvals: (q: WorkflowApprovalQuery = {}): Promise<WorkflowApproval[]> =>
      this.call<{ items: WorkflowApproval[] }>('/v1/workflows/approvals', {
        query: { status: q.status, workflow: q.workflow, subject: q.subject, group: q.groups },
      }).then((r) => r.items),
    /** Approve or reject for one of your users; 403 `not_approver` when their groups do not match, 409 `decided`. */
    decide: (approvalId: string, body: WorkflowServerDecision): Promise<WorkflowApproval> =>
      this.call<WorkflowApproval>(`/v1/workflows/approvals/${enc(approvalId)}`, { method: 'POST', body }),
  }

  /** The application's active agents. */
  agents(): Promise<AgentSummary[]> {
    return this.call<{ items: AgentSummary[] }>('/v1/agents').then((r) => r.items)
  }

  /** Text-to-speech voices of the deployment. */
  voices(q: { language?: string } = {}): Promise<VoiceInfo[]> {
    return this.call<{ items: VoiceInfo[] }>('/v1/voices', { query: q }).then((r) => r.items)
  }

  /** Admin API (needs `adminKey`). */
  readonly admin = {
    application: {
      get: (): Promise<Application> => this.adminCall<Application>('/application'),
      /** Caps, origins, models, storage mode, default voice engine. */
      patch: (patch: AppPatch): Promise<void> =>
        this.adminCall<unknown>('/application', { method: 'PATCH', body: patch }).then(() => undefined),
    },
    keys: {
      list: (): Promise<ApiKeyInfo[]> => this.adminCall<{ items: ApiKeyInfo[] }>('/keys').then((r) => r.items),
      /** The key is returned only now. */
      create: (req: KeyRequest = {}): Promise<{ id: string; key: string }> =>
        this.adminCall<{ id: string; key: string }>('/keys', { method: 'POST', body: req }),
      revoke: (keyId: string): Promise<void> =>
        this.adminCall<unknown>(`/keys/${enc(keyId)}`, { method: 'DELETE' }).then(() => undefined),
      /** A successor with the same kind and label, shown only now; the old key stays valid until revoked. */
      rotate: (keyId: string): Promise<{ id: string; key: string; replaces: string }> =>
        this.adminCall<{ id: string; key: string; replaces: string }>(`/keys/${enc(keyId)}/rotate`, { method: 'POST' }),
    },
    /**
     * Ask rules: tool calls that wait for the person's confirmation (they only add confirmations). `put` replaces the
     * application's rules; 400 `ask_rules` explains a refused rule. Effective from the next turn.
     */
    askRules: {
      get: (): Promise<AskRulesView> => this.adminCall<AskRulesView>('/ask-rules'),
      put: (rules: AskRule[]): Promise<AskRulesView> => this.adminCall<AskRulesView>('/ask-rules', { method: 'PUT', body: { rules } }),
    },
    /** What the application may use: every item with its default, override and value. */
    grants: {
      get: (): Promise<GrantView> => this.adminCall<GrantView>('/grants'),
      /** Grant, revoke or reset (value null) items; returns the new view. Effective from the next turn. */
      change: (changes: GrantChange[]): Promise<GrantView> =>
        this.adminCall<GrantView>('/grants', { method: 'PATCH', body: { changes } }),
    },
    agents: {
      list: (): Promise<Array<AgentDef & { version: string }>> =>
        this.adminCall<{ items: Array<AgentDef & { version: string }> }>('/agents').then((r) => r.items),
      /** Every version of an agent, newest first (history and diffs). */
      versions: (name: string): Promise<AgentVersion[]> =>
        this.adminCall<{ items: AgentVersion[] }>(`/agents/${enc(name)}/versions`).then((r) => r.items),
      /** Create or update; a changed definition becomes a new active version. */
      put: (agent: AgentDef): Promise<{ version: string; created: boolean }> =>
        this.adminCall<{ version: string; created: boolean }>(`/agents/${enc(agent.name)}`, { method: 'PUT', body: agent }),
      delete: (name: string): Promise<void> =>
        this.adminCall<unknown>(`/agents/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
    },
    mcpServers: {
      list: (): Promise<McpServerInfo[]> =>
        this.adminCall<{ items: McpServerInfo[] }>('/mcp-servers').then((r) => r.items),
      put: (name: string, body: McpServerBody): Promise<void> =>
        this.adminCall<unknown>(`/mcp-servers/${enc(name)}`, { method: 'PUT', body }).then(() => undefined),
      delete: (name: string): Promise<void> =>
        this.adminCall<unknown>(`/mcp-servers/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
      /** The ready connectors (kind, title, configuration schema); register one with `put(name, {connector, config})`. */
      connectors: (): Promise<ConnectorKind[]> =>
        this.adminCall<{ items: ConnectorKind[] }>('/connectors').then((r) => r.items),
      /** Connect and list its tools; `credential` is a user's token for forward_user servers (never stored). */
      test: (name: string, credential?: string): Promise<McpTestResult> =>
        this.adminCall<McpTestResult>(`/mcp-servers/${enc(name)}/test`, { method: 'POST', body: credential ? { credential } : {} }),
    },
    skills: {
      list: (): Promise<SkillInfo[]> => this.adminCall<{ items: SkillInfo[] }>('/skills').then((r) => r.items),
      get: (name: string): Promise<SkillDetail> => this.adminCall<SkillDetail>(`/skills/${enc(name)}`),
      put: (name: string, body: SkillBody): Promise<void> =>
        this.adminCall<unknown>(`/skills/${enc(name)}`, { method: 'PUT', body }).then(() => undefined),
      delete: (name: string): Promise<void> =>
        this.adminCall<unknown>(`/skills/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
      /** The files of a skill besides SKILL.md: references, data and templates, scripts. */
      files: {
        /** One file with its text (`path` like `references/terms.md`). */
        get: (name: string, path: string): Promise<SkillFile> =>
          this.adminCall<SkillFile>(`/skills/${enc(name)}/files/${encPath(path)}`),
        /** Add or replace a file; 400 `skill_limit` for a path or a size outside the limits. */
        put: (name: string, path: string, content: string): Promise<void> =>
          this.adminCall<unknown>(`/skills/${enc(name)}/files/${encPath(path)}`, { method: 'PUT', body: { content } }).then(
            () => undefined,
          ),
        delete: (name: string, path: string): Promise<void> =>
          this.adminCall<unknown>(`/skills/${enc(name)}/files/${encPath(path)}`, { method: 'DELETE' }).then(() => undefined),
      },
    },
    prompts: {
      /** Every version of every prompt. */
      list: (): Promise<PromptVersion[]> => this.adminCall<{ items: PromptVersion[] }>('/prompts').then((r) => r.items),
      /** Save a prompt; a changed text becomes the next version. */
      put: (name: string, text: string): Promise<number> =>
        this.adminCall<{ version: number }>(`/prompts/${enc(name)}`, { method: 'PUT', body: { text } }).then((r) => r.version),
    },
    voices: {
      /** Add or replace a voice in the deployment's catalogue (list with `voices()`). */
      put: (provider: string, voiceId: string, body: VoiceBody): Promise<void> =>
        this.adminCall<unknown>(`/voices/${enc(provider)}/${enc(voiceId)}`, { method: 'PUT', body }).then(() => undefined),
    },
    bugReports: {
      list: (): Promise<BugReport[]> => this.adminCall<{ items: BugReport[] }>('/bug-reports').then((r) => r.items),
    },
    /** Conversations across subjects (Evokai storage only; `storage` says which mode the application uses). */
    conversations: {
      /** Newest first, with their turns, thumbs and notes; pass `next` back as `before` for the following page. */
      list: (q: AdminConversationQuery = {}): Promise<{ storage: 'evokai' | 'external'; items: AdminConversation[]; next?: string | null }> =>
        this.adminCall('/conversations', {
          query: { subject: q.subject, agent: q.agent, feedback: q.feedback, noted: q.noted ? 'true' : undefined, before: q.before, limit: q.limit },
        }),
      /** Messages, events, turns, their traces and the reviewers' notes. Turn timings may carry server-side cost fields. */
      get: (conversationId: string): Promise<AdminConversationDetail> => this.adminCall(`/conversations/${enc(conversationId)}`),
      /** Add a reviewer's note or label (operator role); audited by label and length, not text. */
      addNote: (conversationId: string, body: ReviewNoteBody): Promise<ReviewNote> =>
        this.adminCall<ReviewNote>(`/conversations/${enc(conversationId)}/notes`, { method: 'POST', body }),
      deleteNote: (conversationId: string, noteId: string): Promise<void> =>
        this.adminCall<unknown>(`/conversations/${enc(conversationId)}/notes/${enc(noteId)}`, { method: 'DELETE' }).then(() => undefined),
    },
    /** Traces of turns and server runs: spans of model calls, tools, guard decisions; no amounts. */
    traces: {
      /** Newest first, without spans; pass `next` back as `before` for the following page. */
      list: (q: TraceQuery = {}): Promise<{ items: TraceSummary[]; next: string | null; storage: 'evokai' | 'external' }> =>
        this.adminCall('/traces', { query: traceQuery(q) }),
      /** One trace with its spans (the waterfall) and the conversation it belongs to. */
      get: (traceId: string): Promise<TraceDetail> => this.adminCall<TraceDetail>(`/traces/${enc(traceId)}`),
      /** Each version of an agent over a period (default the last 30 days): latency, errors, guard, thumbs, usage share. */
      versions: (agent: string, q: { from?: Date | string; to?: Date | string } = {}): Promise<VersionComparison> =>
        this.adminCall<VersionComparison>(`/agents/${enc(agent)}/version-stats`, { query: { from: iso(q.from), to: iso(q.to) } }),
    },
    /** Test cases per agent, runs that compare two versions, their history (shares and counts only). */
    evals: {
      cases: (agent: string): Promise<EvalCase[]> =>
        this.adminCall<{ items: EvalCase[] }>(`/agents/${enc(agent)}/evals/cases`).then((r) => r.items),
      putCase: (agent: string, name: string, body: EvalCaseBody): Promise<void> =>
        this.adminCall<unknown>(`/agents/${enc(agent)}/evals/cases/${enc(name)}`, { method: 'PUT', body }).then(() => undefined),
      deleteCase: (agent: string, name: string): Promise<void> =>
        this.adminCall<unknown>(`/agents/${enc(agent)}/evals/cases/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
      /** Start a run in the background (409 `eval_running` while one is going); poll `run(id)`. */
      start: (agent: string, opts: { version?: string; baseline?: string | null } = {}): Promise<string> =>
        this.adminCall<{ runId: string }>(`/agents/${enc(agent)}/evals/runs`, { method: 'POST', body: opts }).then((r) => r.runId),
      runs: (agent: string, q: { limit?: number } = {}): Promise<EvalRuns> =>
        this.adminCall<EvalRuns>(`/agents/${enc(agent)}/evals/runs`, { query: q }),
      run: (runId: string): Promise<EvalRunDetail> => this.adminCall<EvalRunDetail>(`/evals/runs/${enc(runId)}`),
    },
    /**
     * The built-in attack suite per agent version: runs (on demand and on save), the security score and its trend,
     * failures with masked answers, and the publish gate.
     */
    redteam: {
      suite: (): Promise<{ categories: string[]; languages: string[]; items: RedTeamAttack[] }> => this.adminCall('/redteam/suite'),
      /** Start a run in the background (409 `redteam_running` while one is going); poll `run(id)`. */
      start: (agent: string, opts: { version?: string; categories?: string[]; languages?: string[] } = {}): Promise<string> =>
        this.adminCall<{ runId: string }>(`/agents/${enc(agent)}/redteam/runs`, { method: 'POST', body: opts }).then((r) => r.runId),
      runs: (agent: string, q: { limit?: number } = {}): Promise<RedTeamRuns> =>
        this.adminCall<RedTeamRuns>(`/agents/${enc(agent)}/redteam/runs`, { query: q }),
      run: (runId: string): Promise<RedTeamRunDetail> => this.adminCall<RedTeamRunDetail>(`/redteam/runs/${enc(runId)}`),
      /** Publish a version (owner); `force` overrides the gate for a version below the score. Audited. */
      publish: (agent: string, version: string, opts: { force?: boolean } = {}): Promise<{ version: string; published: boolean }> =>
        this.adminCall(`/agents/${enc(agent)}/versions/${enc(version)}/publish`, { method: 'POST', body: opts }),
    },
    /** Cost governance: month-end forecasts and alerts, the cheapest model that passes, prompt cache hints (shares only). */
    costs: {
      forecast: (): Promise<CostForecast> => this.adminCall<CostForecast>('/costs/forecast'),
      alerts: (q: { period?: string; open?: boolean } = {}): Promise<CostAlert[]> =>
        this.adminCall<{ items: CostAlert[] }>('/costs/alerts', { query: { period: q.period, open: q.open ? 'true' : undefined } }).then((r) => r.items),
      acknowledge: (alertId: string): Promise<void> =>
        this.adminCall<unknown>(`/costs/alerts/${enc(alertId)}/ack`, { method: 'POST' }).then(() => undefined),
      cacheSuggestions: (): Promise<CacheSuggestion[]> =>
        this.adminCall<{ items: CacheSuggestion[] }>('/costs/cache-suggestions').then((r) => r.items),
      /** Run the agent's cases on its model and on the cheaper allowed ones (409 `comparison_running`). */
      compare: (agent: string, opts: { tolerance?: number } = {}): Promise<{ runId: string; current: string; candidates: string[] }> =>
        this.adminCall(`/agents/${enc(agent)}/model-recommendations`, { method: 'POST', body: opts }),
      recommendations: (agent: string): Promise<ModelRecommendations> =>
        this.adminCall<ModelRecommendations>(`/agents/${enc(agent)}/model-recommendations`),
      /** Save a new version of the agent on another model (allow-lists, grants, evals and the red team gate apply). */
      switchModel: (agent: string, model: string): Promise<{ version: string; created: boolean; published: boolean; publishState?: string }> =>
        this.adminCall(`/agents/${enc(agent)}/switch-model`, { method: 'POST', body: { model } }),
    },
    /**
     * Knowledge bases: settings, documents (uploaded, fetched by address or synced from connectors), ingestion status
     * and a test search. Ingestion runs in the background: poll `get(name)` for the documents' status.
     */
    /** Per-user OAuth connections: providers, their configuration and counts; revoke everyone's. */
    connections: {
      get: (): Promise<ConnectionsAdminView> => this.adminCall<ConnectionsAdminView>('/connections'),
      /** Delete every person's connection to a provider; returns how many. */
      revokeAll: (provider: ConnectionProvider): Promise<number> =>
        this.adminCall<{ revoked: number }>(`/connections/${enc(provider)}`, { method: 'DELETE' }).then((r) => r.revoked),
      /** Set the OAuth app at GitHub, Linear, Notion or Slack (the secret is encrypted and never returned). */
      setApp: (provider: ConnectionProvider, app: OAuthAppInput): Promise<void> =>
        this.adminCall<unknown>(`/connections/${enc(provider)}/app`, { method: 'PUT', body: app }).then(() => undefined),
      /** Remove the OAuth app at a provider. */
      deleteApp: (provider: ConnectionProvider): Promise<void> =>
        this.adminCall<unknown>(`/connections/${enc(provider)}/app`, { method: 'DELETE' }).then(() => undefined),
    },
    /** Channels beyond the web panel: e-mail, Teams, Slack. */
    channels: {
      list: (): Promise<ChannelsView> => this.adminCall<ChannelsView>('/channels'),
      put: (kind: ChannelKind, name: string, body: { agent: string; enabled?: boolean; config: Record<string, unknown> }): Promise<ChannelView> =>
        this.adminCall<ChannelView>(`/channels/${enc(kind)}/${enc(name)}`, { method: 'PUT', body }),
      delete: (kind: ChannelKind, name: string): Promise<void> =>
        this.adminCall<unknown>(`/channels/${enc(kind)}/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
      /** Validate without sending anything: configuration, environment, grant, agent, a login at the provider. */
      test: (kind: ChannelKind, name: string): Promise<{ ok: boolean; checks: ChannelCheck[] }> =>
        this.adminCall<{ ok: boolean; checks: ChannelCheck[] }>(`/channels/${enc(kind)}/${enc(name)}/test`, { method: 'POST' }),
    },
    knowledge: {
      list: (): Promise<KnowledgeList> => this.adminCall<KnowledgeList>('/knowledge'),
      get: (name: string): Promise<KnowledgeBaseDetail> => this.adminCall<KnowledgeBaseDetail>(`/knowledge/${enc(name)}`),
      /** Create a base or change it; a new embedding model, size, chunking or language ingests every document again. */
      put: (name: string, body: Partial<KnowledgeBaseBody>): Promise<KnowledgeBase & { reingested: number }> =>
        this.adminCall(`/knowledge/${enc(name)}`, { method: 'PUT', body }),
      delete: (name: string): Promise<void> =>
        this.adminCall<unknown>(`/knowledge/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
      /**
       * Upload a document. An unchanged file is not ingested again (`outcome: 'unchanged'`); the same content under
       * another name is refused with 409 `duplicate`.
       */
      upload: (
        name: string,
        file: Blob | ArrayBuffer | Uint8Array,
        opts: { filename: string; contentType?: string; title?: string; access?: string[]; metadata?: Record<string, unknown> },
      ): Promise<KnowledgeUpload> =>
        this.adminCall<KnowledgeUpload>(`/knowledge/${enc(name)}/documents`, {
          method: 'POST',
          rawBody: file,
          headers: { 'Content-Type': opts.contentType || 'application/octet-stream' },
          query: {
            filename: opts.filename,
            title: opts.title,
            access: opts.access?.length ? opts.access.join(',') : undefined,
            metadata: opts.metadata ? JSON.stringify(opts.metadata) : undefined,
          },
        }),
      /** Fetch a document by address (on a domain the deployment allows). */
      addUrl: (
        name: string,
        body: { url: string; title?: string; access?: string[]; metadata?: Record<string, unknown> },
      ): Promise<KnowledgeUpload> => this.adminCall(`/knowledge/${enc(name)}/urls`, { method: 'POST', body }),
      /** Re-sync the base's sources (addresses and connectors). */
      sync: (name: string): Promise<KnowledgeSyncResult> =>
        this.adminCall<KnowledgeSyncResult>(`/knowledge/${enc(name)}/sync`, { method: 'POST' }),
      document: (name: string, id: string): Promise<KnowledgeDocumentDetail> =>
        this.adminCall<KnowledgeDocumentDetail>(`/knowledge/${enc(name)}/documents/${enc(id)}`),
      patchDocument: (
        name: string,
        id: string,
        body: { title?: string; access?: string[]; metadata?: Record<string, unknown> },
      ): Promise<KnowledgeDocument> => this.adminCall(`/knowledge/${enc(name)}/documents/${enc(id)}`, { method: 'PATCH', body }),
      reingest: (name: string, id: string): Promise<KnowledgeDocument> =>
        this.adminCall<KnowledgeDocument>(`/knowledge/${enc(name)}/documents/${enc(id)}/reingest`, { method: 'POST' }),
      deleteDocument: (name: string, id: string): Promise<void> =>
        this.adminCall<unknown>(`/knowledge/${enc(name)}/documents/${enc(id)}`, { method: 'DELETE' }).then(() => undefined),
      /** Ranked passages with their scores and citations (embedding usage is recorded). */
      search: (name: string, q: KnowledgeSearchQuery): Promise<KnowledgeSearchResult> =>
        this.adminCall<KnowledgeSearchResult>(`/knowledge/${enc(name)}/search`, { method: 'POST', body: q }),
    },
    /** Workflows: definitions with versions, validation with lines, runs and their steps, the approvals queue. */
    workflows: {
      list: (): Promise<WorkflowList> => this.adminCall<WorkflowList>('/workflows'),
      get: (name: string): Promise<WorkflowDetail> => this.adminCall<WorkflowDetail>(`/workflows/${enc(name)}`),
      /** Check a definition (YAML or JSON text, or an object) without saving it. */
      validate: (
        body: { text: string; format?: 'yaml' | 'json' } | { definition: WorkflowDefinition },
      ): Promise<WorkflowValidation> => this.adminCall<WorkflowValidation>('/workflows/validate', { method: 'POST', body }),
      /** Save; a changed definition becomes a new active version. 400 `invalid_workflow` with `problems`. */
      put: (name: string, definition: WorkflowDefinition): Promise<{ version: string; created: boolean }> =>
        this.adminCall(`/workflows/${enc(name)}`, { method: 'PUT', body: definition }),
      delete: (name: string): Promise<void> =>
        this.adminCall<unknown>(`/workflows/${enc(name)}`, { method: 'DELETE' }).then(() => undefined),
      /** Start a run now. */
      start: (name: string, body: WorkflowStart = {}): Promise<WorkflowRun> =>
        this.adminCall<WorkflowRun>(`/workflows/${enc(name)}/runs`, { method: 'POST', body: { input: {}, ...body } }),
      runs: (q: WorkflowRunQuery = {}): Promise<{ items: WorkflowRun[]; next: string | null }> =>
        this.adminCall('/workflows/runs', { query: { ...q } }),
      run: (runId: string): Promise<WorkflowRunDetail> => this.adminCall<WorkflowRunDetail>(`/workflows/runs/${enc(runId)}`),
      cancel: (runId: string): Promise<WorkflowRun> =>
        this.adminCall<WorkflowRun>(`/workflows/runs/${enc(runId)}/cancel`, { method: 'POST' }),
      retry: (runId: string): Promise<WorkflowRun> =>
        this.adminCall<WorkflowRun>(`/workflows/runs/${enc(runId)}/retry`, { method: 'POST' }),
      approvals: (q: { status?: WorkflowApprovalQuery['status']; workflow?: string } = {}): Promise<WorkflowApproval[]> =>
        this.adminCall<{ items: WorkflowApproval[] }>('/workflows/approvals', { query: { ...q } }).then((r) => r.items),
      decide: (approvalId: string, body: WorkflowDecision): Promise<WorkflowApproval> =>
        this.adminCall<WorkflowApproval>(`/workflows/approvals/${enc(approvalId)}`, { method: 'POST', body }),
    },
    health: (): Promise<Health> => this.adminCall<Health>('/health'),
    /** What changed lately: agent versions, keys and grants. */
    activity: (q: { limit?: number } = {}): Promise<ActivityItem[]> =>
      this.adminCall<{ items: ActivityItem[] }>('/activity', { query: q }).then((r) => r.items),
    /** Turns, fallback answers, time to the first word, subjects and tags of a period (default: this month). */
    usageSummary: (q: Omit<UsageQuery, 'groupBy' | 'subject'> = {}): Promise<UsageSummary> =>
      this.adminCall<UsageSummary>('/usage/summary', { query: { from: iso(q.from), to: iso(q.to), tag: q.tag, agent: q.agent } }),
    /** The application as a config file (secrets left out). */
    export: (): Promise<ExportedConfig> => this.adminCall<ExportedConfig>('/export'),
    /** The application's audit log, newest first; pass `next` back as `before` for the following page. */
    audit: (q: AuditQuery = {}): Promise<{ items: AuditEntry[]; next: string | null }> =>
      this.adminCall('/audit', { query: auditQuery(q) }),
    /** The provider catalog: who processes data, where, under whose control, with the sources (no prices). */
    providers: {
      catalog: (): Promise<ProviderCatalogView> => this.adminCall<ProviderCatalogView>('/providers'),
    },
    /** The data protection profile and every external processor under it, allowed or excluded and why. */
    dataProtection: {
      get: (): Promise<DataProtectionReport> => this.adminCall<DataProtectionReport>('/data-protection'),
    },
    /** The security layer: the policy, what applies, the events (snippets are always masked). */
    security: {
      get: (): Promise<SecurityView> => this.adminCall<SecurityView>('/security'),
      /** Replace the application's policy (unset fields follow the defaults). Effective from the next turn. */
      put: (policy: SecurityPolicy): Promise<SecurityView> =>
        this.adminCall<SecurityView>('/security', { method: 'PUT', body: policy }),
      /** Newest first; pass `next` back as `before` for the following page. */
      events: (q: SecurityEventQuery = {}): Promise<{ items: SecurityEvent[]; next: string | null }> =>
        this.adminCall('/security/events', {
          query: {
            from: iso(q.from),
            to: iso(q.to),
            control: q.control,
            rule: q.rule,
            action: q.action,
            severity: q.severity,
            agent: q.agent,
            subject: q.subject,
            before: q.before,
            limit: q.limit,
          },
        }),
      /** Counts of a period by rule, control, action and severity. */
      summary: (q: { from?: Date | string; to?: Date | string; agent?: string } = {}): Promise<SecuritySummary> =>
        this.adminCall<SecuritySummary>('/security/summary', { query: { from: iso(q.from), to: iso(q.to), agent: q.agent } }),
    },
    /**
     * Compliance packs generated from the configuration (AI Act, GDPR, control mapping), the controller's profile,
     * snapshots and their diff. A support for the controller's assessment, not legal advice.
     */
    compliance: {
      profile: (): Promise<{ profile: ComplianceProfile; saved: boolean }> => this.adminCall('/compliance/profile'),
      /** Replace the profile (owner); text with personal data or secrets is refused (400 `personal_data`). */
      putProfile: (profile: ComplianceProfile): Promise<{ profile: ComplianceProfile }> =>
        this.adminCall('/compliance/profile', { method: 'PUT', body: profile }),
      pack: (pack: CompliancePackName, lang?: ComplianceLang): Promise<CompliancePack> =>
        this.adminCall<CompliancePack>(`/compliance/${enc(pack)}`, { query: { format: 'json', lang } }),
      /** The same pack as Markdown or printable HTML. */
      packText: (pack: CompliancePackName, format: 'md' | 'html', lang?: ComplianceLang): Promise<string> =>
        this.adminCall<string>(`/compliance/${enc(pack)}`, { query: { format, lang }, responseType: 'text' }),
      snapshots: (): Promise<ComplianceSnapshot[]> =>
        this.adminCall<{ items: ComplianceSnapshot[] }>('/compliance/snapshots').then((r) => r.items),
      /** Record what is true now (operator). */
      snapshot: (label = ''): Promise<ComplianceSnapshot> =>
        this.adminCall<ComplianceSnapshot>('/compliance/snapshots', { method: 'POST', body: { label } }),
      snapshotPack: (id: string, pack: CompliancePackName, lang?: ComplianceLang): Promise<CompliancePack> =>
        this.adminCall<CompliancePack>(`/compliance/snapshots/${enc(id)}/${enc(pack)}`, { query: { format: 'json', lang } }),
      snapshotText: (id: string, pack: CompliancePackName, format: 'md' | 'html', lang?: ComplianceLang): Promise<string> =>
        this.adminCall<string>(`/compliance/snapshots/${enc(id)}/${enc(pack)}`, { query: { format, lang }, responseType: 'text' }),
      /** What changed between two snapshots (ordered by time whatever the order given). */
      diff: (from: string, to: string, lang?: ComplianceLang): Promise<ComplianceDiff> =>
        this.adminCall<ComplianceDiff>('/compliance/snapshots/diff', { query: { from, to, lang } }),
    },
  }
}

export interface EvokaiConsoleOptions {
  /** Where the service is mounted, base path included. */
  baseUrl: string
  /** Operator logins: the application to act on (sent as `X-Evokai-Application`). */
  application?: string | null
  fetch?: FetchLike
}

/**
 * The admin console's client: logs in with an admin or operator key exchanged for an httpOnly cookie (the key is never
 * kept), then calls the server and admin APIs with that cookie. Every request sends `X-Evokai-Console: 1`; an operator
 * also sends the chosen application. For pages served by the service itself (same origin).
 */
export class EvokaiConsole extends EvokaiServer {
  /** The application an operator acts on. */
  application: string | null

  constructor(opts: EvokaiConsoleOptions) {
    super({ baseUrl: opts.baseUrl, serverKey: 'console-cookie', ...(opts.fetch ? { fetch: opts.fetch } : {}) })
    this.application = opts.application ?? null
  }

  private cookieOpts(opts: RequestOptions = {}): RequestOptions {
    return {
      ...opts,
      credentials: 'same-origin',
      headers: { 'X-Evokai-Console': '1', ...(this.application ? { 'X-Evokai-Application': this.application } : {}), ...opts.headers },
    }
  }

  protected override call<T>(path: string, opts?: RequestOptions): Promise<T> {
    return requestJson<T>(this.fetchFn, this.baseUrl, path, null, this.cookieOpts(opts))
  }

  protected override adminCall<T>(path: string, opts?: RequestOptions): Promise<T> {
    return requestJson<T>(this.fetchFn, this.baseUrl, `/v1/admin${path}`, null, this.cookieOpts(opts))
  }

  /**
   * Usage of the application as the console may see it: tokens, calls and shares of the caps, never amounts (the
   * service sends a console login no amount; `usage()` is for server keys).
   */
  consoleUsage(q: UsageQuery = {}): Promise<ConsoleUsageReport> {
    return this.call<ConsoleUsageReport>('/v1/usage', { query: usageQuery(q) })
  }

  /** Exchange an admin or operator key for the console cookie. The key is not kept. */
  login(key: string): Promise<void> {
    return requestJson<unknown>(this.fetchFn, this.baseUrl, '/v1/console/login', null, this.cookieOpts({ method: 'POST', body: { key } })).then(() => undefined)
  }

  /**
   * Exchange a one-time sign-in code (from `POST /v1/console/login-code`, valid for a minute, single use) for the
   * console cookie. An unknown, expired or used code fails with 401 and code `login_code`.
   */
  loginWithCode(code: string): Promise<void> {
    return requestJson<unknown>(this.fetchFn, this.baseUrl, '/v1/console/login', null, this.cookieOpts({ method: 'POST', body: { code } })).then(() => undefined)
  }

  logout(): Promise<void> {
    return requestJson<unknown>(this.fetchFn, this.baseUrl, '/v1/console/logout', null, this.cookieOpts({ method: 'POST' })).then(() => undefined)
  }

  /** Who is logged in (401 when nobody). */
  me(): Promise<ConsoleMe> {
    return this.call<ConsoleMe>('/v1/console/me')
  }

  /** The signed-in person's console preferences, kept per console identity (follow the person across browsers). */
  readonly preferences = {
    get: (): Promise<ConsolePreferencesView> => this.call<ConsolePreferencesView>('/v1/console/me/preferences'),
    /** Save the whole document; a value outside a field's choices is refused (400 `bad_request`). Audited. */
    put: (prefs: ConsolePreferences): Promise<ConsolePreferencesView> =>
      this.call<ConsolePreferencesView>('/v1/console/me/preferences', { method: 'PUT', body: prefs }),
    /** Forget the saved preferences: the defaults apply again. */
    reset: (): Promise<ConsolePreferencesView> => this.call<ConsolePreferencesView>('/v1/console/me/preferences', { method: 'DELETE' }),
  }

  /** The applications of this login (every one for the operator), with this month's figures. */
  applications(): Promise<{ items: ConsoleApplication[]; canCreate: boolean }> {
    return this.call<{ items: ConsoleApplication[]; canCreate: boolean }>('/v1/console/applications')
  }

  /** Whether the console offers single sign-on (no login needed). */
  sso(): Promise<SsoInfo> {
    return this.call<SsoInfo>('/v1/console/sso')
  }

  /** Where the browser goes to sign in with single sign-on (a full page navigation, not a fetch). */
  ssoStartUrl(): string {
    return `${this.baseUrl}/v1/console/sso/start`
  }

  /** Deployment owners: the sign-in provider, the console's users and the whole audit log. */
  readonly deployment = {
    ssoConfig: (): Promise<SsoConfigView> => this.call<SsoConfigView>('/v1/console/sso/config'),
    /** Set the provider from the console (it wins over `EVOKAI_SSO`); leave `client_secret` out to keep it. */
    putSsoConfig: (config: SsoConfig): Promise<SsoConfigView> =>
      this.call<SsoConfigView>('/v1/console/sso/config', { method: 'PUT', body: config }),
    /** Forget the console's provider: `EVOKAI_SSO` applies again. */
    resetSsoConfig: (): Promise<SsoConfigView> => this.call<SsoConfigView>('/v1/console/sso/config', { method: 'DELETE' }),
    users: (): Promise<ConsoleUser[]> => this.call<{ items: ConsoleUser[] }>('/v1/console/users').then((r) => r.items),
    /** A disabled user's login ends at once. */
    setUserDisabled: (id: string, disabled: boolean): Promise<ConsoleUser> =>
      this.call<ConsoleUser>(`/v1/console/users/${enc(id)}`, { method: 'PATCH', body: { disabled } }),
    audit: (q: AuditQuery & { application?: string } = {}): Promise<{ items: AuditEntry[]; next: string | null }> =>
      this.call('/v1/console/audit', { query: { ...auditQuery(q), application: q.application } }),
  }

  /** The service's health; `check` lists the MCP servers that need no user now. */
  health(check = false): Promise<ConsoleHealth> {
    return this.call<ConsoleHealth>('/v1/console/health', { query: { check: check ? 'true' : undefined } })
  }

  /** Models to offer (setup wizard, agent editor), and the allow-list for typed-in ones. */
  models(): Promise<{ items: ModelSuggestion[]; allow: string[] }> {
    return this.call<{ items: ModelSuggestion[]; allow: string[] }>('/v1/console/models')
  }

  /** Read a SKILL.md as the service would; 400 `skill_format` without front matter, name and description. */
  parseSkill(text: string): Promise<ParsedSkill> {
    return this.call<ParsedSkill>('/v1/console/skills/parse', { method: 'POST', body: { text } })
  }

  /** Operator only. */
  readonly operator = {
    /** Try an MCP server before registering it: connect and list its tools (the token is never stored). */
    mcpTest: (body: { url: string; token?: string | null }): Promise<McpTestResult> =>
      this.call<McpTestResult>('/v1/operator/mcp-test', { method: 'POST', body }),
    applications: {
      list: (): Promise<OperatorApplication[]> =>
        this.call<{ items: OperatorApplication[] }>('/v1/operator/applications').then((r) => r.items),
      /** Create an application; its server and admin keys are returned only now. */
      create: (body: NewApplication): Promise<NewApplicationKeys> =>
        this.call<NewApplicationKeys>('/v1/operator/applications', { method: 'POST', body }),
    },
  }
}

/** A usage row without amounts: tokens, calls and the share of the application's monthly cap. */
export interface UsageShareRow extends Omit<UsageRow, 'costUsd'> {
  /** This group's spend as a share of the application's monthly cap (null without a cap). */
  capPct: number | null
}

export interface UsageShareReport {
  rows: UsageShareRow[]
  application: {
    /** Month to date, as a share of the monthly cap. */
    monthPct: number | null
    /** Today, as a share of the daily cap (null without one). */
    dayPct: number | null
  }
}

const share = (spent: number, cap: number | null | undefined): number | null =>
  cap === null || cap === undefined ? null : cap <= 0 ? 100 : Math.round((spent / cap) * 1000) / 10

/**
 * Turn a usage report into percentages and drop every amount, for interfaces that must not show money (the admin
 * console). Do the conversion where the data is fetched, so amounts never reach a component.
 */
export function toUsageShares(report: UsageReport): UsageShareReport {
  const cap = report.application.monthlyCapUsd
  return {
    rows: report.rows.map(({ costUsd, ...rest }) => ({ ...rest, capPct: share(costUsd, cap) })),
    application: {
      monthPct: share(report.application.monthUsd, cap),
      dayPct: share(report.application.dayUsd, report.application.dailyCapUsd),
    },
  }
}
