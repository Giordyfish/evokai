# Capabilities

A capability adds something to an agent's runs on the pydantic-ai engine ([pydantic-ai agent](pydantic-ai-agent.md)):
Evokai's own Code Mode, compaction and subagents, and every capability of pydantic-ai-harness 0.69. The catalog is
`evokai.capabilities`; this page lists it.

## Three switches

Every capability is off until all three say yes, like `code_run`:

1. **The deployment** offers it: `EVOKAI_CAPABILITIES` (a JSON list of ids, or `["*"]`). Empty by default.
2. **The application** is granted it: grant kind `capability`, the id as item (console: Permissions, Capabilities).
   Off by default for every capability.
3. **The agent** turns it on, with its settings: `capabilities: {<id>: {enabled: true, config: {...}}}` (console: the
   agent editor, Capabilities). Settings are checked against the capability's own constructor when the agent is
   saved; a setting that names a model (`model`) must be a model the application may use, resolved by Evokai.

Listing the catalog (Permissions, the agent editor, saving an agent) imports no capability: whether one is installed
is read from the installed packages' metadata, its description is the catalog's own text, and a capability's class is
imported only when a run of an agent that enables it builds it (where its settings are checked against its
constructor too). An agent that enables a capability its application is not granted is refused at save (400
`not_granted`). A
capability that cannot be built at run time (not installed, a missing credential, a model not allowed) is left out of
that turn with a `capability_unavailable` warning in the logs; the turn goes on. The capabilities a turn used are on
its trace (`capabilities` on the root span).

## What always holds

Capabilities are added after Evokai's own rules, which no capability can switch off:

- the guard on input, tool calls (the tool policy, arguments, egress, loop, rate), tool results and output;
- the grants and MCP calls as the user (`forward_user`);
- caps before every model call and usage per call with the model that answered, whatever made the call: a
  subagent (kind `subagent`), a summary (kind `summary`), or a capability's own model, such as a judge, a reviewer or
  an advisor (kind `cap`; the engine hands such a capability a wrapped model that records each call and passes its
  settings through `enforce_settings`);
- traces;
- no provider-side state: every attempt's settings pass `evokai.llm.policy.enforce_settings` last;
- the tools a capability brings (web search, an integration's MCP tools, plans, memory files, a spilled tool
  result) follow the same rules as MCP tools (`evokai.engine.capability_tools`): the tool policy decides whether
  they are offered, every call is checked (policy, arguments, egress, loop, rate), counts against the turn's
  tool-call cap, is a `tool` span with `source: capability` and a `tool` event, and its text result is untrusted
  content, scanned and marked; a tool of a hosted MCP server that its server does not mark `readOnlyHint` changes
  data and waits for the person's confirmation, like `tools.confirm` of the policy;
- what a capability does is in the trace: each event it emits (a reminder fired, a plan changed, a loop found) is
  a `capability` event with its name and numbers, never its text;
- confirmation before a change: a tool that changes data waits for the person. `approval_reviewer` (a second model
  that reviews calls waiting for approval) and `permissions` are in the catalog, off by default; turned on for an
  agent, they act only within what that agent is granted (the guard checks an approved call again), and a call they
  approve is in the trace as `confirmed` with `approvedBy: capability`.

## Host access

No capability runs a shell, touches files, starts containers or a kernel, or opens ssh on the machine of the Evokai
service:

- **`local`** capabilities (local Docker, Bubblewrap, BoxLite, Smol, LocalStack, IPython, a local browser) run on the
  service's own machine: they are listed, but saving an agent that enables one is refused.
- **`workspace`** capabilities (shell, files, coder, repository context, code search, Macroscope, capability authoring,
  harness skills) work on the run's workspace: an agent must name a remote sandbox in their settings
  (`config: {sandbox: e2b_sandbox}`) and enable that sandbox too; otherwise saving is refused and they are never built.
- **`remote`** capabilities are the sandboxes that supply that workspace from outside (E2B, Modal, Daytona,
  Kubernetes, Render, Sprites, OpenSandbox, Boxd, ssh to another machine; ssh to `localhost` is refused).
- Every run with a workspace capability also gets `NoLocalWorkspace`, which refuses the run before it starts if its
  workspace is the local machine, whatever supplied it.

## Dependencies

The full image installs every harness extra (`evokai[harness]`); the lite image, the default, installs the extras
under 30 MB (`evokai[harness-lite]`): a capability the lite image leaves out (pixeltable, the ML prompt-injection
defender, playwright, boxlite, smol, temporal, ipython, daytona) shows `installed: false` with «not in the lite image»
([deployment](deployment.md#images-lite-lite-with-voice-full)). No image has two extras:

- `browser-use`: it does not resolve in Evokai's lock: browser-use 0.13.10 to 0.13.14 pin `pydantic-settings==2.15.0`
  (and 0.13.6 to 0.13.10 `anthropic==0.76.0`), and Evokai needs `pydantic-settings>=2.16.3`. It drives a browser on
  the machine it runs on anyway, so it runs in the browser worker ([Separate workers](#separate-workers)).
- `kubernetes`: its grpc client needs protobuf 7, and the `voice` extra's Pipecat needs protobuf below 7.

Installing them took the local virtual environment from 940 MB to 2.0 GB (308 locked packages).

## Separate workers

Some capabilities cannot run in the service's process: `browser_use` drives a browser on the machine it runs on and
its pins do not resolve with Evokai's lock, `kubernetes_sandbox` needs a gRPC client the voice extra cannot share,
and the local sandboxes (`docker_container`, `bubblewrap_workspace`, `boxlite_sandbox`, `smol_sandbox`,
`ipython_code_mode`, `playwright`) run on the host. In Evokai they stay in the catalog and are refused at save
(`host = local`) or left out at run time (not installed).

**The capability worker** (`workers/capability`, its own uv project and images) hosts one of them and serves its
tools over MCP (streamable HTTP, JSON answers):

- Two images, one lock with two conflicting extras (browser-use pins pydantic and protobuf below what the Kubernetes
  client needs): `CAPABILITY=kubernetes`, the Kubernetes sandbox (`KubernetesSandbox`, settings in
  `EVOKAI_WORKER_CONFIG` such as `warm_pool` and `namespace`) with the shell and file tools on its pod; and
  `CAPABILITY=browser`, with Playwright's Chromium, serving `browser_use` (default) or `playwright`
  (`EVOKAI_WORKER_CAPABILITY=playwright`).
- Chromium runs without its renderer sandbox in the browser image, for both capabilities: the sandbox needs user
  namespaces, which Docker's default seccomp profile refuses (Chromium then dies at launch). The container is the
  boundary instead: read-only root, uid 1001, no key. Where the runtime grants user namespaces (Docker with
  Playwright's seccomp profile, a Kubernetes `seccompProfile` that allows them), `playwright` takes it back with
  `EVOKAI_WORKER_CONFIG='{"chromium_sandbox": true}'`.
- The application registers it as an MCP server with `auth: secret` (the worker's `EVOKAI_WORKER_SECRET`) and
  **`worker: true`** (console: MCP servers, «Evokai capability worker»; application file: `worker: true`), and an
  agent lists it in `mcp_servers`. Everything Evokai does for MCP tools applies unchanged: the grants, the tool
  policy and ask rules, the argument, egress, loop and rate checks, a confirmation for every tool not marked
  `readOnlyHint` (only file reads and Playwright's page reads are: every command, every navigation, click or typing,
  and `browse_web` wait for the person), the untrusted-content guard, traces, the tool-call cap.
- Only a server marked `worker` gets `_meta` with each call: `evokai/conversation` and `evokai/token`, a call token
  Evokai signs for that turn (HS256 with its signing key, another issuer than session tokens): bound to the
  application, the conversation, the person, the agent, the tags and the turn, valid five minutes at most and only
  while the turn or run is going (its end revokes it, in the shared state, whatever the expiry), for at most
  `EVOKAI_WORKER_MODEL_CALLS` (50) model calls. No other server, with a secret or not, gets either. The worker runs
  each conversation's calls inside one long-lived pydantic-ai run (its model is the worker itself, each MCP call the
  next tool call), so the pod or the browser stays across calls; an idle conversation
  (`EVOKAI_WORKER_IDLE_SECONDS`, 900) ends its run and its resources. A call without a conversation is refused.
- **Model calls go through Evokai, with least privilege.** browser-use's own agent calls a model; the worker holds no
  provider key and **no Evokai key** (it refuses to start with `EVOKAI_SERVER_KEY` or `EVOKAI_ADMIN_KEY` in its
  environment; it needs only `EVOKAI_URL`). Its model (`evokai_worker.model.EvokaiModel`) sends each request to
  `POST /v1/workers/model` with the current turn's call token as the bearer. That endpoint accepts nothing else (a
  session token, a server or admin key are 401) and refuses an expired token, one whose turn ended and one past its
  call budget. Usage is attributed from the token, never from what the worker sends: the person, the agent
  (`worker:<agent>`), the tags, the conversation and the turn. Evokai uses a model the agent may use (allow-lists,
  grants), checks the caps first (a spent cap is 429 `limit` and the browsing stops), applies the no-provider-storage
  policy and passes the text through the guard's output checks. A compromised worker can at most spend the running
  turn's model calls as that turn's person.
- The images run as uid 1001 on a read-only root, port 8790. Measured locally on 18 November 2026 (linux/amd64,
  uncompressed): `kubernetes` 0.52 GB, `browser` 2.44 GB (Playwright's Chromium and its system libraries included, used by
  browser-use too).

`scripts/ci.sh worker` runs its gates (ruff, tests with a stateful fake capability: tools listed with their hints, state
per conversation, the secret).

## Wired to Evokai

The harness capabilities that take a function, a callback or a store are built with Evokai's own
(`evokai.capabilities.harness`); the agent's settings are checked against the class's constructor, and the objects
Evokai provides cannot be set by an agent.

| Id | What Evokai provides |
|---|---|
| `ask_user` | Its question is shown like `ui_ask`: an `ask` event (spoken in voice), and the turn ends with reason `ask`. The person's choice is their next message («Answer to your question ...»), which resumes the conversation; nobody waits inside a turn. A call with several questions shows them one at a time: each answer is the person's next message and shows the next question without a model call; after the last one the model gets every question with its answer. The questions still to ask wait with the conversation (`evokai_capability_records`, a day; an hour with external storage, like a pending confirmation). Two to five options per question. In a server run, or for an application not granted the client tool `ask`, the model is told to choose and say which choice it made. |
| `input_guardrail` | Evokai's guard on input (size, hidden text, injection, scope). A prompt the turn or run already checked is not checked twice. |
| `output_guardrail` | The turn's output filter (secrets, links, personal data, a repeated system prompt) applied to a server run's text answer before it is returned. A turn's answer is filtered as it streams. |
| `tool_guardrail` | The guard's argument (`tools.args`) and egress (`tools.egress`) checks on every tool call, whatever brought the tool. |
| `system_reminders` | `reminders: [{content, interval, first_after, max_fires, scope, skip_unchanged}]`, texts written by an admin. |
| `tool_budgets` | `per_tool`, `total`, `refuse_repeats`; the total never above the turn's tool-call cap. A refused call answers the model and is a `turn.budget` security event (`tool_budget_per_tool`, `tool_budget_total`, `tool_budget_repeat`). |
| `detect_loops` | A loop is a `tools.loop` security event. `action: nudge` (default) tells the model; `action: stop` ends a turn as `max_steps` with the usual message, and fails a server run. In a turn only thinking and tool arguments are checked as they stream: text the person already read is never taken back. |
| `tool_output_limits` | `over` (characters, the turn's tool-result limit by default) and `action` (`spill` or `truncate`). A spilled result is never written to local files: see [Stores](#stores). |
| `refine_output` | Server runs only, at most three rounds (each one a model call under the caps). A turn's answer is shown while it is written, so it is left out of turns. |
| `repair_tool_arguments` | Each repair is a `capability` event of the trace. |
| `warn_on_cache_busts` | Judged across the turns of a conversation (the marks are kept per process, keyed by conversation); a collapse of the prompt cache, or a long request with caching off, is a `capability` event of the trace and a `prompt_cache_bust` log line. Nothing is shown to the person. |

Capabilities add system parts to a run (a reminder, a loop nudge, a review round): Evokai's view of the history,
rebuilt before every request, carries them to the request they belong to.

## Stores

What a harness capability keeps is in Evokai's database (`evokai_capability_records`, migration `0024`), scoped to
the application and the person like everything else, never in local files or a store of the harness's own
(`evokai.capabilities.stores`):

| Id | Kept | Storage modes | Goes away |
|---|---|---|---|
| `memory` | the agent's memory files, per person and agent: a separate store from Evokai's memories, which are single facts with consolidation and recall ([memory](memory.md#the-harness-memory-capability)); every write passes `memory.write` | only while the person's memory is active (Evokai storage, `memory_mode`, consent); otherwise left out | `memory_ttl_days`; a withdrawn consent, «forget everything», an erasure |
| `planning` | the plan, per conversation and agent; the next turn continues it | Evokai storage; with external storage the plan lives for the turn | with the conversation (deleted, its time to live, an erasure) |
| `step_persistence` | the step log (runs, events, the newest 20 snapshots per run, tool effects), per conversation | Evokai storage; with external storage for the turn only | with the conversation |
| `tool_output_limits` | a spilled tool result, read back with its tool | Evokai storage; with external storage for the turn only | with the conversation, a week at most |
| `conversation_search` | nothing: it reads the person's own conversations kept by Evokai (the latest 50), text only; its results are untrusted content | Evokai storage only; otherwise left out |  |
| `spend_limits` | counters per budget window (no content) | both | when the window rolls over |

`spend_limits` takes `budgets: [{name, window, scope, share, tokens, warn_at}]`: a budget is a **share of one of
Evokai's caps** (`share` of the application's or the person's `day` or `month` cap) or a number of tokens (any window:
`day`, `month`, `run`, `conversation`, `total`), never an amount; `scope` is `person` (default), `application` or
`conversation`. Its spend is priced with Evokai's prices; Evokai's own caps still apply before every call and usage is
recorded as for any call. A spent budget ends a turn like a cap (`error` with code `limit`, `limit: agent`, a message
without amounts, a `turn.budget` security event) and fails a server run with 429 `limit`. The model gets no spend
tools.

Each record is in the person's export (`capabilityRecords`) and erased with them; expired ones are purged hourly
(and by `evokai purge`).

## Credentials

Web search providers, third-party integrations and remote sandboxes need an account's key. Each application sets its
own (`evokai.capabilities.credentials`, table `evokai_capability_credentials`, migration `0025`): encrypted with the
deployment's Fernet key like MCP secrets, written from the console (Permissions, Capabilities, Credentials: one form
per provider, generated from its fields; secrets are write-only), from the API (`GET` and
`PUT /v1/admin/capability-credentials/{provider}`, `DELETE` to remove; every change is audited without its values)
or from the application file (`capability_credentials`, see [configuration](configuration.md#capabilities)). The
deployment's environment variable of a field is the fallback. A capability whose credentials are missing stays listed
with `missingCredentials` (`provider.field`) and is left out of a run (`capability_unavailable`).

| Provider | Fields (environment fallback) | Capabilities | Built with |
|---|---|---|---|
| `exa` | `api_key` (`EXA_API_KEY`) | `exa_search`, `exa_agent` | an `AsyncExa` client with the key |
| `keenable` | `api_key` (`KEENABLE_API_KEY`) | `keenable_search` | an HTTP client with the key |
| `youdotcom` | `api_key` (`YDC_API_KEY`) | `you_search`, `you_research` | a `You` client with the key |
| `github`, `linear`, `notion`, `slack`, `google_workspace`, `posthog`, `pylon`, `ordinal`, `day_ai`, `grain`, `logfire_mcp` | `token` (`GITHUB_TOKEN`, `LINEAR_ACCESS_TOKEN`, `NOTION_ACCESS_TOKEN`, `SLACK_USER_TOKEN`, `GOOGLE_ACCESS_TOKEN`, `POSTHOG_PERSONAL_API_KEY`, `PYLON_ACCESS_TOKEN`, `ORDINAL_ACCESS_TOKEN`, `DAY_AI_ACCESS_TOKEN`, `GRAIN_ACCESS_TOKEN`, `LOGFIRE_API_KEY`) | the capability of the same name | the hosted MCP server's `auth` |
| `stackone` | `api_key` (`STACKONE_API_KEY`) | `stackone` | `api_key` |
| `e2b` | `api_key` (`E2B_API_KEY`) | `e2b_sandbox` | an `e2b.E2B` client |
| `modal` | `token_id`, `token_secret` (`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`) | `modal_sandbox` | `modal.Client.from_credentials` |
| `daytona` | `api_key` (`DAYTONA_API_KEY`) | `daytona_sandbox` | an `AsyncDaytona` client |
| `render` | `api_key`, `workspace_id` (not secret) (`RENDER_API_KEY`, `RENDER_WORKSPACE_ID`) | `render_sandbox` | a `RenderAsync` client |
| `sprites` | `token` (`SPRITE_TOKEN`) | `sprites_sandbox` | an `AsyncSpritesClient` |
| `opensandbox` | `api_key`, `domain` (not secret) (`OPEN_SANDBOX_API_KEY`, `OPEN_SANDBOX_DOMAIN`) | `opensandbox` | a `ConnectionConfig` |
| `boxd` | `api_key` (`BOXD_API_KEY`) | `boxd_sandbox` | an `AsyncBoxd` client |

`managed_prompt` keeps the deployment's `LOGFIRE_TOKEN` (one Logfire project per deployment). `kubernetes_sandbox` and
`ssh_workspace` use the cluster's or the host's own configuration, outside the main image (see
[Separate workers](#separate-workers)).

### Whom an integration acts as

An agent never does more than the signed-in person. An integration whose service lets each person sign in
(`google_workspace`, `github`, `linear`, `notion`, `slack`) **acts as the person by default** (`acts_as: person`):

- `google_workspace` uses the person's own Google token from Evokai's connection (the application's
  `google_workspace` connector and the `connection/google` grant, [connections](connectors/connections.md)). A person who has not
  connected gets a `connect` card and the model is told why the tools are missing; nobody else's token stands in.
- `github`, `linear`, `notion`, `slack`: Evokai has no connection for these services yet, so a person cannot connect
  their own account and the capability is left out (`capability_unavailable`, the reason in the log). **Next step**
  (a later wave): OAuth connections for GitHub, Linear, Notion and Slack in `evokai.connections`, like Microsoft 365
  and Google Workspace, so these integrations act as each person by default.

`acts_as: application` (an agent's setting) uses the application's credential, a shared account: the console shows
the agent with a warning label, the trace records it (`actsAsApplication` on the turn, `actsAs` on each tool span),
and every write waits for the person's own confirmation; a capability (`approval_reviewer`) cannot approve it.
Services with API keys only (`exa`, `keenable`, `youdotcom`, `posthog`, `pylon`, `ordinal`, `stackone`, `day_ai`,
`grain`, `logfire_mcp`) use the application's credential. Every integration's tools follow the tool rules above (the
tool policy, the checks, a confirmation for any tool its server does not mark read-only, traces).

## The catalog

"Still to wire" is what Evokai has to provide before agents rely on a capability beyond enabling and constructing it
(a store in Evokai's database, an answerer, credentials kept per application, a worker): the next wave.

### Evokai's own, wired end to end

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `code_mode` | Code Mode over the read-only tools, with Evokai's trace, events and counters. | codemode |  |  |  |
| `compaction` | The ``compaction`` capability: turns compaction on for an agent, with its policy. |  |  |  |  |
| `subagents` | ``SubAgents`` over Evokai agents, with a span per call and a cap per turn. |  |  |  |  |

### Context and history

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `clamp_oversized_messages` | Zero-cost head/tail truncation of any single oversized message part. |  |  |  |  |
| `clear_tool_results` | Zero-cost in-place clearing of old tool results. |  |  |  |  |
| `compact_on_request` | Give the model a `compact_context` tool to ask for a compaction when its task reaches a break. |  |  |  | a strategy object (built from a summarizing compaction in the settings) |
| `deduplicate_file_reads` | Zero-cost in-place clearing of superseded file reads. |  |  |  | a file key function (only meaningful with file tools) |
| `fallback_compaction` | Try compaction strategies in order, falling back when one raises. |  |  |  | a chain of strategy objects |
| `relevance_compaction` | Compact by judging each older step of the history: keep it verbatim, reduce it, or drop it. |  |  |  |  |
| `report_context_usage` | Report context usage to the application before each model request. |  |  |  | a callback to the console |
| `sliding_window_compaction` | Zero-cost sliding-window trimmer. |  |  |  |  |
| `summarizing_compaction` | Compact the conversation by summarizing older messages with a model call. |  |  |  | the summary lives in the run only: Evokai's own compaction stores it |
| `tiered_compaction` | Escalation orchestrator over a sequence of compaction strategies. |  |  |  | tiers of strategy objects |
| `warn_near_limits` | Warns the model when the run approaches configured limits. |  |  |  |  |
| `tool_output_limits` | Reduce oversized tool returns when they are produced, persisting the reduction. |  |  |  |  |
| `system_reminders` | Inject periodic or conditional reminders to counter instruction fade in long sessions. |  |  |  |  |
| `warn_on_cache_busts` | Warn when a conversation's prompt cache hit collapses between requests. |  |  |  |  |

### Safety and review

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `approval_reviewer` | Ask a second model about each call waiting for approval, and approve the low-risk ones. |  |  |  | acts only inside what the agent is granted; its decisions are traced |
| `permissions` | Ask before tool calls that write files or run shell commands, according to a permission mode. |  |  |  | Evokai's confirmation stays the rule; its modes apply to workspace tools only |
| `tool_call_judge` | Ask a second model whether a tool call may run, and block it before its body does. |  |  |  |  |
| `trajectory_judge` | Review a live run with a second model on a cadence, and steer it mid-run. |  |  |  |  |
| `input_guardrail` | Validate the user prompt before it reaches the model. |  |  |  |  |
| `output_guardrail` | Validate the agent output as it is produced. |  |  |  |  |
| `tool_guardrail` | Validate tool arguments before execution and tool results after it. |  |  |  |  |
| `prompt_injection_defender` | Classify tool results for indirect prompt injection and withhold the risky ones. | prompt-injection-defender |  |  |  |
| `artifact_scanner` | Scan agent-written artifacts, such as memory files and skills, for attack patterns. |  |  |  |  |
| `detect_loops` | Detect when an agent repeats the same actions without making progress, and interrupt it. |  |  |  |  |
| `tool_budgets` | Cap tool calls per tool and per run, and refuse exact repeats, by answering the model instead of raising. |  |  |  |  |
| `spend_limits` | Accumulate spend per window and refuse a request once a window is exhausted. |  |  |  | Evokai's caps stay the rule; budgets are shares of caps or tokens |

### Flow and reasoning

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `advisor` | Let an agent consult another model through a provider-native tool or local fallback. |  |  |  |  |
| `planning` | Structured task planning, with the current plan kept in front of the model. |  |  |  |  |
| `repair_tool_arguments` | Repair malformed JSON tool arguments before they are checked. | repair-tool-arguments |  |  |  |
| `refine_output` | Send the agent back to review and revise its output before the run ends. |  |  |  | server runs only: a turn's answer is shown while it is written |
| `background_tools` | Run selected tools in the background while the agent continues. |  |  |  |  |
| `dynamic_workflow` | Capability that lets the model orchestrate named sub-agents from a Python script. | dynamic-workflow |  |  | named agents (Evokai agents of the application) |
| `ask_user` | Let the model ask the user multiple-choice questions mid-run. |  |  |  |  |
| `memory` | Persistent agent memory across sessions. |  |  |  |  |
| `conversation_search` | Search persisted conversation history with a dependency-free BM25 tool. |  |  |  |  |
| `step_persistence` | Append-only step log + continuable snapshots + tool-effect ledger. |  |  |  |  |
| `peers` | Let an agent list and message other sessions over a host-provided network. |  |  |  | a peer network between sessions |
| `capability_creation` | Create Pydantic AI capabilities during one run for activation on the next. |  |  | needs a remote sandbox | writes Python modules: only in a remote sandbox |
| `managed_prompt` | Back an agent's instructions with a Logfire-managed prompt. | logfire | `LOGFIRE_TOKEN` |  |  |

### Search and research

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `exa_search` | Web research for agents, backed by the [Exa](https://exa.ai) search API. | exa | `EXA_API_KEY` |  |  |
| `exa_agent` | Delegation of deep research tasks to the [Exa](https://exa.ai) Agent API. | exa | `EXA_API_KEY` |  |  |
| `keenable_search` | Web research for agents, backed by the [Keenable](https://keenable.ai) search API. |  | `KEENABLE_API_KEY` |  |  |
| `you_search` | Web research for agents, backed by the [You.com](https://you.com) search API. | youdotcom | `YDC_API_KEY` |  |  |
| `you_research` | Cited answers and multi-step research, backed by the [You.com](https://you.com) APIs. | youdotcom | `YDC_API_KEY` |  |  |
| `researcher` | Web research with search, page fetching, a research delegate, and tool-output limits. | researcher |  |  |  |
| `smart_search` | Find code by the behavior a plain-English query describes. |  |  | needs a remote sandbox |  |
| `pydantic_ai_docs` | Locate and return Pydantic AI documentation on demand. |  |  |  |  |

### Integrations (third-party MCP servers)

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `day_ai` | Let an agent search and update the Day AI CRM. | day-ai | `DAY_AI_ACCESS_TOKEN` |  |  |
| `grain` | Let an agent search and read Grain meetings, transcripts, and notes, and create clips and tag meetings. | grain | `GRAIN_ACCESS_TOKEN` |  |  |
| `ordinal` | Let an agent draft, schedule, and analyze social posts in Ordinal. | ordinal | `ORDINAL_ACCESS_TOKEN` |  |  |
| `posthog` | Let an agent query PostHog product analytics and manage its resources. | posthog | `POSTHOG_PERSONAL_API_KEY` |  |  |
| `pylon` | Let an agent manage support issues, look up and update accounts, and look up contacts in Pylon. | pylon | `PYLON_ACCESS_TOKEN` |  |  |
| `stackone` | Actions on the user's SaaS account (HRIS, ATS, CRM, and more) via StackOne. | stackone | `STACKONE_API_KEY` |  |  |
| `github` | Use GitHub's hosted tools with the permissions of the connected credential. | github | `GITHUB_TOKEN` |  |  |
| `linear` | Give an agent the tools of Linear's hosted MCP server, with the permissions of the connected user. | linear | `LINEAR_ACCESS_TOKEN` |  |  |
| `notion` | Give the agent the tools of Notion's hosted MCP server, with the permissions of the connected user. | notion | `NOTION_ACCESS_TOKEN` |  |  |
| `slack` | Give an agent Slack's hosted tools, acting as the connected user. | slack | `SLACK_USER_TOKEN` |  |  |
| `google_workspace` | Give an agent the tools of Google's hosted Workspace MCP servers for the selected products. | google-workspace | `GOOGLE_ACCESS_TOKEN` |  |  |
| `logfire_mcp` | Query Logfire telemetry and manage observability resources through its hosted tools. | logfire-mcp | `LOGFIRE_API_KEY` |  |  |

### Data and media

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `pixeltable` | Read-only tools over existing Pixeltable tables. | pixeltable |  |  | a Pixeltable home outside the read-only image |
| `browser_use` | A browser the agent drives. | browser-use (not in the image) |  | never (the server's machine) | in the capability worker (`workers/capability`, `CAPABILITY=browser_use`), an MCP server: [Separate workers](#separate-workers) |
| `localstack` | Access to an emulated AWS environment via LocalStack. |  |  | never (the server's machine) |  |
| `playwright` | A real, stateful Chromium browser the agent drives. | playwright |  | never (the server's machine) | in the capability worker (`workers/capability`, browser image, `EVOKAI_WORKER_CAPABILITY=playwright`), an MCP server |

### Workspaces: shell, files, code (remote sandbox only)

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `shell` | Shell command execution for agents. |  |  | needs a remote sandbox |  |
| `file_system` | File system access to the run's workspace, scoped to a root directory. |  |  | needs a remote sandbox |  |
| `coder` | Autonomous coding with six tools, delegation, and context management, in the run's workspace. | coder |  | needs a remote sandbox |  |
| `repo_context` | Discover and load a repo's accumulated coding-assistant context engineering. |  |  | needs a remote sandbox |  |
| `skills_dirs` | Offer Agent Skill instructions from the run's workspace as deferred capabilities. | skills |  | needs a remote sandbox | harness skills from workspace folders (Evokai's own skills are separate) |
| `macroscope` | Runs the `macroscope` CLI code review and hands the findings to the agent. |  |  | needs a remote sandbox |  |
| `ipython_code_mode` | Capability that gives the model a `run_code` tool backed by a persistent IPython kernel. | ipython |  | never (the server's machine) |  |

### Sandboxes

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `docker_container` | Supply a local [Docker](https://www.docker.com/) container as the run's workspace, created on first use. |  |  | never (the server's machine) |  |
| `docker_sandbox` | Deprecated name for `DockerContainer`. |  |  | never (the server's machine) |  |
| `bubblewrap_workspace` | A [`Workspace`][pydantic_ai.workspaces.Workspace] that runs commands in a bubblewrap (`bwrap`) sandbox. |  |  | never (the server's machine) |  |
| `boxlite_sandbox` | Supply a [BoxLite](https://github.com/boxlite-ai/boxlite) micro-VM on this machine as the run's workspace. | boxlite |  | never (the server's machine) |  |
| `smol_sandbox` | Supply a [smolvm](https://github.com/smol-machines/smolvm) microVM on this machine as the run's workspace. | smol |  | never (the server's machine) |  |
| `ssh_workspace` | Gives runs a [workspace](https://pydantic.dev/docs/ai/core-concepts/workspace/) on a remote host over SSH, using your `ssh` client and its configuration. |  |  | remote sandbox |  |
| `e2b_sandbox` | Supply an isolated [E2B](https://e2b.dev) sandbox as the run's workspace. | e2b | `E2B_API_KEY` | remote sandbox |  |
| `modal_sandbox` | Supply a Modal sandbox as the run's workspace. | modal | `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | remote sandbox |  |
| `daytona_sandbox` | Supply an isolated [Daytona](https://www.daytona.io) sandbox as the run's workspace. | daytona | `DAYTONA_API_KEY` | remote sandbox |  |
| `kubernetes_sandbox` | A sandbox pod per run in a Kubernetes cluster. | kubernetes (not in the image) |  | remote sandbox | in the capability worker (`workers/capability`, `CAPABILITY=kubernetes`), an MCP server |
| `render_sandbox` | Supply an isolated [Render sandbox](https://render.com/docs/sandboxes) as the run's workspace. | render | `RENDER_API_KEY` | remote sandbox |  |
| `sprites_sandbox` | Run the agent's workspace in a persistent [Fly.io Sprite](https://sprites.dev). | sprites | `SPRITE_TOKEN` | remote sandbox |  |
| `opensandbox` | Supply an [OpenSandbox](https://open-sandbox.ai) sandbox as the run's workspace, created on first use. | opensandbox |  | remote sandbox |  |
| `boxd_sandbox` | Supply an isolated [boxd](https://boxd.sh) machine as the run's workspace. | boxd |  | remote sandbox |  |

### Durable execution

| Id | What it does | Extra | Credentials | Host | Still to wire |
|---|---|---|---|---|---|
| `absurd_durability` | Capability that makes an agent durable by checkpointing its I/O into Absurd steps. | absurd |  |  | runs only inside an Absurd task: an Absurd queue and worker running Evokai runs ([workflows](workflows.md#the-harnesss-durable-execution-backends)) |
| `azure_durable_task` | Run agent operations as activities in user-written Azure Durable Task orchestrations. | azure-durable-task |  |  | runs only inside an orchestration: a Task Hub and a worker in Azure |
| `pgtask_durability` | Capability that makes an agent durable by checkpointing its I/O into pgtask steps. | pgtask |  |  | through workflows: durable agent steps ([workflows](workflows.md#durable-agent-steps)) |
| `aws_lambda_durability` | Capability that checkpoints an agent's I/O into AWS Lambda durable steps. | aws-lambda |  |  | runs only inside a durable Lambda function holding Evokai's code |
| `render_workflows` | Route supported agent operations through an explicit Workflows app. | render | `RENDER_API_KEY` |  | runs only inside a Render Workflows app and its service |

