# Evokai architecture

Evokai is an agents runtime. An application (for example a shop's back office) runs it next to itself; the
application's users talk to **agents** that use **any model provider**, the application's tools
over **MCP**, **skills** and **voice**, always as the signed-in user, within budgets, and with a
confirmation before anything changes data.

One service, one event vocabulary. Text and voice are two ways into the same conversation.

## Principles

- **The application owns identity and data.** Evokai never authenticates end users. The
  application's backend vouches for them with a short-lived session token and forwards their own
  credential, so MCP calls are made *as the user* and an agent can never do more than the user.
- **One turn, one id, one cancellation.** Every event carries the `turnId` and a per-session
  `seq`; cancelling a turn stops its model call, tools and speech and nothing else.
- **Server-side truth, or none.** With Evokai storage, conversations, turn state and pending
  actions live in Evokai's database and a reconnecting client replays from it. With external
  storage the application keeps the conversation and Evokai keeps no message content at rest.
- **Deterministic where possible.** Sentence splitting for speech, redaction of personal data,
  spoken «sì»/«annulla», option picking and echo detection are code, not prompts.
- **Fail soft, never silently.** A model without capacity falls back to the next one (only if
  nothing reached the user yet); an MCP server that is down drops its tools for that turn; a
  failing speech provider turns the session to text; a budget that runs out ends the turn with a
  clear message.
- **Renderer-agnostic speech.** Speech is emitted as ordered `say` segments that any renderer
  (browser TTS, server TTS, subtitles, a visual) can follow. A session declares its actor
  (`agent` today).

## Hard rule: no conversations at model providers

Conversations are **never** stored at an LLM provider. Every request carries the history
explicitly, from Evokai's store or from the application. No stored responses, response chaining
(`previous_response_id`), provider conversations or threads, assistants APIs, cached contents or
provider memory features, ever.

Enforced in code (`evokai.llm.policy`): `openai_store` is forced to `false` on every call, chaining
and provider-state settings are refused in agent definitions and stripped from every request, and
a test fails if any code path references provider-side conversation APIs.

**Prompt caching is allowed**, per agent (`prompt_cache`), because it is not provider-side
conversation state: the provider keeps a copy of the request's own prefix for minutes or an hour, with
no id to address it, and every request still carries the whole history. See
[Prompt caching](#prompt-caching).

## Hard rule: no amounts in front of people

Cost amounts (dollars, euros, cents) are never shown to a person, anywhere: not in the application's
interface and not in Evokai's admin console. People see only shares of a defined maximum: used and
left percentages of the caps. Amounts live in the database and in the server-to-server endpoints an
application calls with its own key (`GET /v1/usage`, the answer of a server run), never in anything
a browser receives:

- session tokens, `GET /v1/session` and every `usage` event carry `dayPct`, `weekPct`,
  `monthPct`, `companyPct`, `monthOut`, `out`, the `binding` cap, its `resetsAt` and every cap's
  share (`caps`);
- a console login gets shares from the same endpoints (`GET /v1/usage` answers a console login with
  `capPct` per row, `dayCapPct` for days, `subjectCapPct` and `subjectTodayPct` for subjects, and the
  application's month and day used and left), the application cards carry `usage` (used and left)
  and which caps are set, never their amounts in spend, and a turn's timings carry `capPct` in place
  of its cost;
- the console draws and exports only those shares.

Tests check both: no key that names money in what browsers and console logins receive.

## Concepts

| Concept | What it is |
|---|---|
| **Application** | A client of Evokai (for example a back office). Has keys, allowed browser origins, allowed models, hard monthly/daily caps, a storage mode and defaults (e.g. the voice engine), and **capability grants**. Everything below is per application. |
| **Grant** | What an application may use: models (primary and fallback, fallback only, off), MCP servers and single tools, skills, client tools, browser and server speech with STT engines and voices, storage modes, channels. Each item follows its default or is overridden; the application still makes every runtime choice among what is granted. |
| **Subject** | The application's own, opaque id for an end user. Evokai stores it, never interprets it. Tags on the token (e.g. `team:ops`) group usage. |
| **Agent** | A versioned definition: instructions or a versioned prompt, model + allowed alternatives + ordered fallbacks, MCP servers, skills, client tools, limits, voice settings, model settings (reasoning effort, priority). Each turn of a conversation runs on the agent's active version (recorded in its trace), so a conversation left open across a release gets the new definition at its next turn. |
| **Model** | Any pydantic-ai model (`bedrock:eu.anthropic…`, `openai:…`, `mistral:…`). Only models in the deployment allow-list *and* the application's allow-list can be used. |
| **MCP server** | Streamable-HTTP endpoint. Auth `forward_user` (the session's forwarded credential), `secret` (encrypted at rest) or `none`. Tools are what `tools/list` returns for that user; name clashes are a configuration error. |
| **Memory** | What an agent remembers about a person across conversations: preferences, facts about their work and (opt-in per agent) summaries of finished conversations; consolidated, recalled by relevance and recency, spotlighted as data, with the person's consent per the application's `memory_mode` ([memory](memory.md)). |
| **Eval case** | An input (and page) of an agent with checks on its answer, run as a real turn of a test subject on demand and when a new version is saved ([evals](evals.md)). Its usage (kind `eval`) has its own budget and never counts against the caps. |
| **Code execution** | `code_run`: Python for analysis and charts in Amazon Bedrock AgentCore Code Interpreter, one sandbox per call with no internet, in the deployment's EU region; off until the deployment, the application's `code` grant and the agent all turn it on; the same switches let a skill's own scripts run with `skill_run` ([code](code.md)). |
| **Compliance pack** | The AI Act pack, the GDPR pack and the ISO 27001 / SOC 2 mapping of an application, written from its configuration and activity (never personal data), every statement with its source; snapshots keep what was true on a date ([compliance](compliance.md)). |
| **Connector** | A ready MCP server Evokai runs in process (`evokai.connectors`): `sql`, `openapi`, `google_drive`, `microsoft_files`, `imap`. Registered like an MCP server (`connector` and `config` instead of a URL), read-only unless `writes`, secrets from the environment only ([connectors](connectors/README.md)). |
| **Connector** | A ready MCP server Evokai runs in process (`evokai.connectors`): `sql`, `openapi`, `google_drive`, `microsoft_files`, `imap`, and the delegated `microsoft_365` and `google_workspace`. Registered like an MCP server (`connector` and `config` instead of a URL), read-only unless `writes`, secrets from the environment only ([connectors](connectors/README.md)). |
| **Connection** | A person's own OAuth connection to Microsoft 365, Google Workspace, GitHub, Linear, Notion or Slack (`evokai.connections`, each provider a data definition): authorization code with PKCE, tokens encrypted and refreshed, revocable; delegated connectors and the harness integrations act as the person with it ([connections](connectors/connections.md)). |
| **Channel** | A way to an agent besides the web panel (`evokai.channels`): an e-mail mailbox (server runs), a Teams bot and a Slack app (turns with cards and buttons); each maps its person to a subject and binds its threads to conversations ([channels](channels/README.md)). |
| **Knowledge base** | Documents of the application (PDF, Word, PowerPoint, HTML, Markdown, text, CSV, uploaded, fetched by address or synced from a connector) cut into chunks, embedded in the deployment's region and searched by meaning and words; agents that list it get `knowledge_search` and cite what they find ([knowledge](knowledge.md)). |
| **Workflow** | Steps Evokai runs on its own, durably: agents (server runs), tools, approvals by people, conditions, waits, parallel branches and signed webhooks, started by hand, by the server key, on a schedule, by a signed webhook or when an agent run ends. Versioned; every run and step state is in the database, leased by workers so it resumes after a restart ([workflows](workflows.md)). |
| **Handoff** | An agent whose definition lists `handoff_to` can hand the conversation to another agent of the application; the next turns are that agent's, with the history kept ([workflows](workflows.md#handoff-between-agents)). |
| **Skill** | `SKILL.md` (front matter `name`, `description`), a body and optionally files: references, data and templates, `scripts/*.py`. Only names and descriptions go into the prompt; the agent loads a body with `skill_load` when it needs it (except the agent's `preload_skills`, whose text is part of its system prompt), gets the list of the files with it, reads one with `skill_load` and `file`, and runs a script with `skill_run` only where code execution is on ([configuration](configuration.md#skills-files-scripts-and-limits)). |
| **Client tool** | Executed by the client: navigate, chart, diagram, ask a question with options, remember/forget. Emitted as events. |
| **Mutating tool** | An MCP tool annotated `destructiveHint` or not `readOnlyHint`. It never runs directly: the turn ends with `confirm` and resumes on the user's decision. [Ask rules](security.md#ask-rules) can make other calls wait too, never fewer. |
| **Session / conversation / turn** | A session token is bound to one conversation (created at mint). A turn is one user input and everything it caused: `done` with reason `end_turn`, `confirm`, `ask`, `limit`, `max_steps`, `cancelled`, `barge_in`, `superseded`, `expired`, `model`, `internal`. |

## Identity: applications, keys, session tokens

```
application backend ── server key ──▶ POST /v1/session-tokens ──▶ session token (JWT, ≤15 min, with the user's groups)
browser ── session token + allowed Origin ──▶ /v1/session/*  (chat, cancel, replay, voice offer)
admin tooling ── admin key ──▶ /v1/admin/*  (application, keys, agents, MCP, skills, prompts, voices, grants)
admin console ── admin key once ──▶ /v1/console/login ──▶ HttpOnly cookie ──▶ /v1/admin/*, /v1/console/*
admin console ── OIDC (code + PKCE) ──▶ /v1/console/sso/* ──▶ the same cookie, with roles
```

- **Keys** `evk_srv_<prefix>_<secret>` and `evk_adm_<prefix>_<secret>`, stored as sha256 of the
  secret, rotatable (create, deploy, revoke). Infrastructure can provision them without the admin
  API through `EVOKAI_BOOTSTRAP_APPS`.
- **Session token** (HS256, issuer `evokai`): application, subject, agent, conversation, tags,
  optional per-subject caps, optional model and voice, **voice engine**, and whether a credential
  was forwarded. The forwarded credential itself is stored encrypted (Fernet) for the token's life
  and purged after it.
- The mint may also choose the session's **prompt modules** (`modules`: which of the agent's
  modules are on, with which parameters) and its **locale** (a BCP 47 tag with a shipped language
  pack). They are checked (free-text parameters by the guard) and stored on the conversation, not
  in the token: the browser never sees or changes them ([Prompt modules](prompt-modules.md)).
- A session can record the **consent** the user gave (version, time, notice hash) and, for kiosks,
  a hashed **exit code** (`POST /v1/session/exit`: 204, 403 wrong, 429 too many attempts).

## Limits and costs

Hard per application and optional per subject, each per day, week or month over a fixed
calendar window in the application's time zone and in its currency (see
[Limits](configuration.md#limits)); a session token may set the subject's. Every model call is priced by the model that actually answered and
recorded with subject, tags, agent, kind and model, with its input tokens, the part of them read from
or written to the prompt cache, and its output tokens. People never see amounts (see the hard rule
above): the session and its `usage` events carry only shares of the caps and when they reset,
and the console only shares (beside the maxima an admin sets); the application backend reads
amounts from `GET /v1/usage?groupBy=subject|tag|agent|kind|model|day` with its server key.

## Prompt caching

An agent may set `prompt_cache: 5m` or `1h` (default `off`). Every request then asks its model's provider to cache
the stable prefix, the way that provider takes it (`evokai.llm.caching`); on Bedrock it carries cache points:

```
tools ── cachePoint(ttl) ── system: stable part ── cachePoint(ttl) ── volatile part ── messages ── cachePoint(5m)
```

- The **stable part** of the system prompt is the same on every request of an agent version: the
  instructions, the text of the agent's `preload_skills` and the list of the skills it loads on
  demand. `{date}` in the instructions changes it once a day.
- The **volatile part** (voice rule, memories, open page) follows the cache point, so nothing per
  request is ever inside the cached prefix. A server run has no volatile part: its input is the
  user's message.
- The **conversation** gets a 5 minute cache point when the agent has tools to call, so the next step
  of the same turn or run reads it instead of paying for it again.

| Provider | How | TTL |
|---|---|---|
| Bedrock: Anthropic Claude | cache points after tools, the stable system part and the conversation (`bedrock_cache_*`) | 5m, or 1h where the model offers it |
| Bedrock: Amazon Nova | cache points after the stable system part and the conversation (no tool caching) | 5m |
| Anthropic API (and Claude through it on other clouds) | `cache_control` breakpoints (`anthropic_cache_*`) at the same places | 5m or 1h |
| OpenAI models with explicit breakpoints, Azure OpenAI where its profile allows, Claude through OpenRouter | pydantic-ai's provider-neutral `cache` setting | snapped to the provider's nearest tier |
| OpenAI's other models, Azure, Gemini (implicit), DeepSeek, Mistral, Groq, Scaleway | the provider caches a repeated prefix by itself: nothing is sent, the cached tokens are read from the usage (`promptCache: auto`) | the provider's |
| Bedrock models without caching (Llama...), other providers, test models | nothing | none |

- Gemini's explicit context caching is a cached content resource created at Google and addressed by its name
  (`google_cached_content`): provider-side state, refused by the hard rule above, so Gemini is cached implicitly only.
- A fallback chain that mixes models gets each model's own way; where caching does not apply the call just runs
  uncached.

Cost: cache read and write tokens are priced from the provider's own cache prices (`evokai.llm.prices`: the
catalog's, then genai-prices'; on Anthropic models a read costs 0.1x the input price, a write 1.25x for 5 minutes
or 2x for 1 hour); input tokens include both. Each model call logs `cacheReadTokens`, `cacheWriteTokens` and
`promptCache` in its `provider_operation` record and its trace, and usage records and reports carry
`cacheReadTokens` and `cacheWriteTokens`, for every provider that reports them. The minimum cacheable prefix is
model-dependent (4,096 tokens on Claude Haiku 4.5, 1,024 on OpenAI): a shorter prefix is simply not cached.

## Capability grants

The client application decides its runtime choices (voice engine, voice, storage mode, model); the console and the
admin API only grant or revoke what those choices may be, item by item (`evokai.grants`). An item without an override
follows its default, and the defaults are what an application could do before grants existed: every model the
deployment and the application allow, as primary or fallback; every MCP server, tool, skill and client tool; both
voice engines with every STT engine and voice; both channels; and the application's own storage mode. Overrides are
rows of `evokai_grants` (application, kind, name, value); the config file's `grants:` section, when present, replaces
them at start.

```
token mint ─────── channel browser · model (as primary) · voiceEngine · voice · storageMode · STT of server voice
turn / server run ─ channel · storage mode · voice engine of a voice turn
                    toolbox: granted servers ∩ granted tools (no tools that change data without confirm)
                    skills ∩ granted · client tools ∩ granted · model chain without off models,
                    never led by a fallback-only one
voice offer ─────── server voice · its STT engine · the session's voice
agent save ──────── no reference to an ungranted model, server, skill, channel or client tool (400 not_granted)
```

Refusals are 403 with a code per item (`model_not_granted`, `voice_engine_not_granted`, `voice_not_granted`,
`stt_not_granted`, `storage_mode_not_granted`, `channel_not_granted`). Turns re-read the grants, so a revocation takes
effect from the next turn even for sessions minted before it. The admin console shows each item with its default,
override and effective value, and greys out in the agent editor what is not granted, with a link to Permissions.

## Storage modes (per conversation)

The application's `storage_mode` is the default; a session token may ask for another granted mode for a new
conversation (`storageMode`), and the conversation keeps it for good (older conversations follow the application).

- **`evokai`** (default): messages (model-facing content blocks + the text and events the user
  saw), turns, numbered events (for replay), pending confirmations and memories in Evokai's
  database.
- **`external`**: the application sends `history` (the stable `HistoryMessage` contract: `role`,
  `content` blocks `text` / `toolUse` / `toolResult`, `text`, `events`, `interrupted`) with every
  request; Evokai validates and windows it, runs the turn and returns the new messages in the
  `done` event or the JSON response (`Accept: application/json`). No message content, events or
  memories are kept at rest; a pending confirmation holds only the waiting tool calls and the
  results gathered before them, with a one-hour TTL.

History is windowed and repaired before each model call (no dangling tool calls; an interrupted
answer is kept and marked as interrupted).

## Rewriting the last question

A person may edit their last message and send it again: `POST /v1/session/chat` with `{text, rewrite: {messageId?}}`
replaces the last turn, that is the last question and everything after it (the answer, its tool calls and results,
an open or answered confirmation), and runs a new turn on the new text (`evokai.sessions.rewrite`).

- **Only the last question**, and only while nothing after it changed data: a result of a tool that changes data is
  marked `wrote: true` (in the stored blocks and in an application's history), and the `confirmed` event of a
  decision says whether the change ran (`writes`). A change cannot be undone, so its turn cannot be taken back.
- **Refusals** are 409 before the turn starts, with the application's message (`turn.rewrite*` in the language
  packs): `rewrite_after_write`, `rewrite_not_last` (the `messageId` the client edits is no longer the last
  question), `rewrite_nothing` (no question yet) and `rewrite_running` (a turn of the conversation is running: stop
  it or wait; one that has already sent `done` is waited for briefly).
- **The flag.** In `GET /v1/session/conversation` every user message has `rewritable` (true only on the last question
  while it can be rewritten and no turn runs), and every `done` event carries `rewritable` for the conversation's
  last question, so a client knows without reloading.
- **What goes (Evokai storage).** The replaced messages and their replay events are deleted, an open confirmation is
  closed (`rewritten`), a conversation summary that covers them is dropped (the next compaction writes it again), and
  memory forgets what came from them: the conversation's episode, and the memories its agents wrote since the
  replaced question (an older memory one of them had superseded comes back). Usage stays recorded: it was spent.
- **What a rewrite does not undo.** A handoff made in the replaced turn (the conversation stays with the agent it was
  handed to, and the new question goes to that agent); a memory the agent only merged into an existing near-duplicate
  (the merged text stays); the replaced turn's trace, kept under the traces' own retention like every trace; and, of
  course, anything outside Evokai, which is why a turn that changed data is refused.
- **The event stream.** The new turn starts with `conversation` and then `rewritten`: `{type: "rewritten", turnId: <the
  new turn>, replaced: [<turn ids>], messageId, replacedFrom?}`, persisted at once. A client drops what it shows of
  the replaced turns (by their turn ids, or the question `messageId` and what follows it). A replay
  (`/v1/session/events`) after a rewrite never returns the replaced turns' events, and finds `rewritten` in its place
  in the sequence; sequence numbers keep growing (the replaced numbers are simply gone).
- **External storage.** The application owns the history, so the rewrite is its call: it sends `rewrite: {}` with
  `text` and its `history` as it has it (the old question and its answer included). Evokai checks the history the
  same way (a `wrote` result or an approved `confirmed` without `writes: false` after the last question refuses it
  with 409 `rewrite_after_write`), runs the turn on the history up to that question, and answers with `replacedFrom`
  (in `done` and in the JSON answer): the application drops its copy from that index and appends `messages`. Nothing
  is kept at rest, as for every external turn.

## Protocol (HTTP + SSE)

Mounted under `EVOKAI_BASE_PATH` (`/agent` by default). OpenAPI at `{base}/openapi.json`.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/health` | anyone | liveness |
| POST | `/v1/session-tokens` | server key | mint a session token (creates the conversation) |
| GET/DELETE | `/v1/conversations[/{id}]?subject=` | server key | a subject's conversations |
| GET | `/v1/usage` | server key | usage grouped by subject, tag, agent, kind, model, day |
| GET/DELETE | `/v1/memories` | server key | a subject's memories ([memory](memory.md)) |
| POST | `/v1/memories/consent` | server key | a subject's answer to long-term memory |
| GET | `/v1/agents`, `/v1/voices` | server key | catalogue |
| POST | `/v1/privacy/export`, `/erase`, `/revoke-sessions` | server key | a subject's data as JSON, the right to be forgotten, sign-out everywhere ([privacy](privacy.md)) |
| GET | `/v1/session` | session | conversation, agent, usage shares, memories and the memory state |
| GET, PATCH, DELETE | `/v1/session/memories[/{id}]` | session | what the agents remember about the person: list, rewrite, delete |
| POST | `/v1/session/memory-consent` | session | the person's answer (a no deletes what was remembered) |
| GET | `/v1/session/conversation` | session | messages, open confirmation, last seq, running turn |
| POST | `/v1/session/chat` | session | a turn: `{text?, confirm?, rewrite?, page?, mode: text\|voice, history?, endOfSpeechAt?}` → SSE (or JSON); `rewrite` replaces the last question ([below](#rewriting-the-last-question)) |
| POST | `/v1/session/turns/{id}/cancel` | session | cancel a turn (barge-in) |
| GET | `/v1/session/events?after=` | session | replay after a reconnect |
| POST | `/v1/session/messages/{id}/feedback` | session | thumbs |
| POST | `/v1/session/exit`, `/v1/session/bug-reports` | session | kiosk exit, bug report |
| POST/PATCH | `/v1/session/voice/offer` | session | server voice: WebRTC offer / ICE candidates (only when enabled) |
| POST | `/v1/session/agui` | session | a turn as an [AG-UI](ag-ui.md) run: `RunAgentInput` in, AG-UI events out (SSE); confirmations and questions are interrupts |
| POST | `/v1/openai/chat/completions` | server key or session | [OpenAI-compatible](openai-compatible.md) Chat Completions: model = agent, server-run semantics, streaming |
| GET | `/v1/openai/models` | server key or session | the agents the caller can use, as OpenAI models |
| GET | `/v1/a2a/{app}/{agent}/.well-known/agent-card.json` | anyone, when published | the [A2A](a2a.md) Agent Card of an agent marked `a2a` (application granted `a2a`) |
| POST | `/v1/a2a/{app}/{agent}` | server key | A2A JSON-RPC (1.0 and 0.3): send, stream, get and cancel tasks, as server runs |
| * | `/v1/admin/...` | admin key | application, keys (rotate), agents (versions), MCP servers (test), skills, prompts, voices, grants, activity, usage summary, export, bug reports |
| POST | `/v1/console/login`, `/logout` | admin or operator key, or a one-time code | the console's cookie login |
| POST | `/v1/console/login-code` | admin or operator key (Bearer) | a one-time sign-in code (60 s, single use) for a sign-in link |
| GET | `/v1/console/sso`, `/sso/start`, `/sso/callback` | anyone (the browser) | single sign-on to the console |
| * | `/v1/admin/knowledge/...` | admin key | knowledge bases, documents (upload, address, sync, re-ingest), test search ([knowledge](knowledge.md)) |
| GET | `/v1/knowledge/files/{id}?e=&s=` | a citation's signed link | a document's file, for a few minutes |
| * | `/v1/admin/workflows/...` | console role or admin key | workflows (versions, validation with lines), runs (steps, retry, cancel, run now), the approvals queue ([workflows](workflows.md)) |
| * | `/v1/workflows/...` | server key | start a run, read or cancel it, list approvals and decide them for the application's users |
| POST | `/v1/workflow-hooks/{app}/{workflow}` | the webhook's HMAC signature | start a run from an outside system |
| GET/PUT/DELETE | `/v1/console/sso/config`, `/v1/console/users`, `/v1/console/audit` | deployment owner | the sign-in provider, console users, the whole audit log |
| GET, POST, DELETE | `/v1/session/connections[/{provider}[/start]]` | session | the person's [connections](connectors/connections.md): list, start (the authorize URL), disconnect |
| GET | `/v1/oauth/callback` | the provider's redirect (single-use state) | finish a connection |
| GET, DELETE | `/v1/connections[/{provider}]?subject=` | server key | a subject's connections |
| GET, DELETE | `/v1/admin/connections[/{provider}]` | console role or admin key | providers, connected counts, revoke all |
| PUT, DELETE | `/v1/admin/connections/{provider}/app` | console role or admin key | the OAuth app of GitHub, Linear, Notion or Slack (client id, write-only secret, writes) |
| POST | `/v1/channels/{app}/email/{name}/inbound`, `/teams/{name}/messages`, `/slack/{name}/events`, `/slack/{name}/interactions` | the provider's signature or JWT | [channel](channels/README.md) deliveries |
| GET, PUT, DELETE, POST | `/v1/admin/channels[/{kind}/{name}[/test]]` | console role or admin key | channels: configuration, endpoints, status, a test that sends nothing |
| GET | `/v1/admin/audit` | console role or admin key | the application's audit log |
| GET, POST | `/v1/admin/agents/{agent}/redteam/runs`, `/v1/admin/redteam/...`, `/v1/admin/agents/{agent}/versions/{version}/publish` | console role or admin key (publish: owner) | the attack suite per version, its score and the publish gate ([red team](redteam.md)) |
| GET, POST | `/v1/admin/costs/...`, `/v1/admin/agents/{agent}/model-recommendations`, `/switch-model` | console role or admin key | month-end forecasts and alerts, the cheapest model that passes, prompt cache hints, as shares ([costs](costs.md)) |
| GET, PUT, POST | `/v1/admin/compliance/{pack}`, `/profile`, `/snapshots[/{id}/{pack}]`, `/snapshots/diff` | console role or admin key (profile: owner) | the compliance packs as JSON, Markdown or HTML in English or Italian, the controller's statements, snapshots and their diff ([compliance](compliance.md)) |
| GET | `/v1/admin/traces[/{id}]`, `/v1/admin/agents/{name}/version-stats` | console role or admin key | traces with filters and their spans; each version of an agent side by side (shares, never amounts) |
| GET, POST, DELETE | `/v1/admin/conversations[/{id}[/notes[/{noteId}]]]` | console role or admin key (notes: operator) | conversation review with thumbs and notes filters, each turn's trace, reviewers' notes (audited) |
| GET | `/v1/console/me`, `/applications`, `/health` | console cookie | who is logged in, its applications, health |
| GET, PUT, DELETE | `/v1/console/me/preferences` | console cookie | the signed-in person's console preferences (language, appearance, accessibility), per console identity; audited |

Events: `conversation`, `text` (delta), `say` (`{index, text, spoken?, filler?}`), `tool` (start/done/error),
`chart` (a chart, or an `image` the agent's code drew), `diagram`, `ask`, `navigate`, `confirm` (`{actionId, items, why}`: `why[i]` says why
`items[i]` waits, an i18n key and an [ask rule](security.md#ask-rules)'s label), `rewritten` (`{replaced, messageId, replacedFrom?}`, the
last question was [rewritten](#rewriting-the-last-question)), `citation` (`{index, kb, documentId, title,
location, snippet, url}`, a knowledge passage the answer cites as `[index]`), `handoff` (`{from, to, title, reason}`,
the conversation goes on with another agent from the next turn), `connect` (`{provider, title, server}`, a tool needs
the person to connect a provider first: the client shows a card that starts the connection), `usage`, `speech`
(voice turned off), `error` (`{code, message}`, never raw exception text) and `done`
(`{reason, conversationId, rewritable, messages?, replacedFrom?}`). Every event has `turnId` and `seq`.

`say` comes from a deterministic splitter over the streamed answer: markdown, tables, code and
links are dropped, a «Fonti:» section is never spoken, numbers stay readable, and a redactor
removes tax codes, IBANs, VAT numbers, e-mails and phone numbers first, and says the application's
own identifiers the way each one asks. Charts and schemas are never read aloud.

A speech engine reads «1.000» as «uno punto zero zero zero», so a `say` also carries `spoken`, the
same sentence with its numbers in words, whenever that differs from `text`
(`evokai.audio.speech.numbers`, by the application's locale; Italian today, other languages are
spoken as written). Italian covers integers with thousand dots, decimals («12,5»: «dodici virgola
cinque»), percentages, euros («1.234,56 €»: «milleduecentotrentaquattro euro e cinquantasei»),
dates («16/11/2026»: «sedici novembre duemilaventisei»), times («17:26»), signs and ordinals
(«1°»: «primo»); digits inside identifiers, versions («2.0.1») and codes (a leading zero, more than
twelve digits) are left alone. The voice speaks `spoken ?? text`; everything shown (the chat, the
subtitles) keeps `text`, and the stored messages and `text` events never change.

## Server runs (back-office agents)

An agent whose `channels` include `server` can be run by the application's backend with its server
key, with no browser and no user: `POST /v1/agents/{name}/run` with
`{input: string | [{role, text}], subject?, tags?, attachments?: [{name, mediaType, text}], store?}`.
The answer is `{runId, output, model, usage: {inputTokens, outputTokens, cacheReadTokens,
cacheWriteTokens, costUsd, toolCalls},
conversationId}` (amounts are fine here: server to server).

- With an `output_schema` (JSON Schema of an object) the model must call the `final_result` tool;
  its arguments are validated, one invalid answer gets one retry with the errors, then the run
  fails with `502 invalid_output`. Without a schema, `output` is the answer text.
- Only read-only MCP tools are offered (nobody could confirm a change). MCP servers for server
  agents use `auth: secret` with `secret_env` (e.g. a read-only API key of the application).
- `temperature` applies to every call. The application's caps apply (`429 limit`); usage is
  recorded under the subject, `system:<agent>` by default. Nothing is stored unless `store: true`.
- A server-only agent cannot be used for a browser session token (`403 channel`), and a browser
  agent cannot be run from the server.
- The same runner serves the open standards: the [OpenAI-compatible API](openai-compatible.md) (a server
  run, or with a session token a run as the session's subject with its credential and caps) and
  [A2A](a2a.md) (a run on the `a2a` channel of an agent marked `a2a: true`).

## Voice

A session declares its **voice engine** (token claim, else the application default, else
`browser`). It is stored on the session and logged with every turn.

- **`browser`**: the client does speech on the device: on-device recognition only
  (`SpeechRecognition` with `processLocally`, language packs installed on the device) and local
  synthesis voices only; never a vendor cloud. It sends the transcript as a normal turn with
  `mode: "voice"`; Evokai applies the voice prompt rule, emits `say` segments, matches spoken
  replies to an open confirmation («sì», «annulla») or question («la seconda», an option's
  words) by rule, and the client cancels the turn (`/turns/{id}/cancel`) when the user talks over
  it. No speech usage is recorded; no Pipecat pipeline is opened.
- **`server`**: Pipecat over WebRTC (`voice` extra, `EVOKAI_VOICE_ENABLED=true`). The browser
  (Pipecat JS client, SmallWebRTC transport) posts its offer with the session token; each
  connection runs:

  ```
  WebRTC in → Silero VAD → STT → user turn (Smart Turn v3) → Evokai agent → TTS → WebRTC out
  ```

  The agent step *is* an Evokai turn in voice mode on the same conversation (same tools,
  confirmations, budgets, storage). `say` segments go to the TTS; every event reaches the client
  as an RTVI server message `{"evokai": event}`. Pipecat handles interruptions; the agent cancels
  its turn on barge-in (`barge_in`). A failing TTS (quota, outage) turns the session to text and
  says so. STT and TTS are pluggable (registered names or `module:factory`); EU-hosted providers
  plug in without touching the pipeline. Connections stay bound to their session.

## Module map (`src/evokai`)

| Module | Responsibility |
|---|---|
| `core` | settings, errors, keys/tokens/encryption, the state shared by instances (memory or Redis), small utilities |
| `observability` | JSON logs with correlation id, provider-operation logs, traces of turns and runs (`tracing`), optional OpenTelemetry with the GenAI conventions |
| `db` | async SQLAlchemy engine (TLS, schema), models, repositories, Postgres provisioning |
| `identity` | key and session-token authentication, console logins, single sign-on (OIDC) and console roles |
| `audit` | the audit log of changes (who, when, before and after) |
| `agents` | definitions, registry, prompts, skills, bootstrap (YAML and environment) |
| `mcp` | MCP client and per-turn toolbox |
| `llm` | model catalogue and allow-lists, the provider catalog (`providers.yaml`: who processes data, where, under whose control, with sources), prices (catalog, genai-prices, fallback), prompt caching per provider, capacity errors, no-provider-storage policy |
| `protection` | the data protection profile: residency, jurisdiction and retention rules on every external processor, and the report of each processor's verdict ([data protection](data-protection.md)) |
| `tools` | client tools (charts, diagrams, ask, memory, skills) and confirmation labels |
| `sessions` | turns and server runs (drivers of the engine), storage modes, events and replay, cancellation, limits |
| `engine` | turns and runs on pydantic-ai's `Agent`: the guarded toolset, the per-request capability (history view, caps, fallback, caching, traces, usage), Code Mode, compaction, subagents ([pydantic-ai agent](pydantic-ai-agent.md)) |
| `audio/speech` | speakable text, splitter, redaction, spoken replies |
| `audio/voice` | Pipecat agent processor, speech providers, WebRTC voice server |
| `api` | session, server, admin, grants, console and voice routes; the open standards (`agui_routes`, `openai_routes`, `a2a_routes`); the console's static files |
| `grants` | capability grants: defaults, overrides, enforcement helpers |
| `privacy` | a subject's export and erase, session revocations, data residency ([privacy](privacy.md)) |
| `memory` | long-term memory: consent, consolidation, recall, episodes, expiry ([memory](memory.md)) |
| `costs` | month-end forecasts, cost alerts (webhooks signed with HMAC), the cheapest model that passes, prompt cache hints ([costs](costs.md)) |
| `knowledge` | knowledge bases: extraction, chunking, embeddings, the pgvector and portable indexes, hybrid search, SSRF-safe fetching, ingestion workers, citations ([knowledge](knowledge.md)) |
| `workflows` | durable workflows: definitions and validation, the safe expression language, cron, the engine (leases, retries, approvals, parallel joins, schedules, triggers), signed webhooks ([workflows](workflows.md)) |
| `compliance` | compliance packs: facts from the configuration, the AI Act classification, the AI Act, GDPR and control-mapping packs in English and Italian, Markdown and HTML rendering, snapshots and diffs ([compliance](compliance.md)) |
| `sandbox` | isolated code execution: the AgentCore Code Interpreter client, the `code_run` tool and `skill_run` for skill scripts ([code](code.md)) |
| `connections` | per-user OAuth connections: providers and scopes, the token vault, the authorization flow with PKCE, refresh and revocation |
| `channels` | e-mail, Teams and Slack: configuration, subject mapping, each protocol's verification and replies, runs and turns from deliveries |
| `mcp_admin` | the admin MCP server: the admin API's allow-listed routes as MCP tools, called in process with the caller's rights ([admin MCP](admin-mcp.md)) |
| `assistant` | the console assistant: its locked system application, delegation tokens, activation and model recommendations, its evals ([assistant](assistant.md)) |
| `guard` | the security layer: policies, injection and personal-data detectors, spotlighting, output filter, tool policy, abuse limits, security events ([security](security.md)) |

## Database

SQLAlchemy 2 async on any SQL database (Postgres in production, SQLite in tests); Alembic
migrations. On Postgres, `evokai db-setup` (owner URL) creates the runtime role `evokai_app`
and the schema `evokai`, migrates and grants the role USAGE and CRUD only; the service connects
as `evokai_app` with `search_path=evokai`. TLS from libpq-style URLs (`sslmode=verify-full`) with
a CA file. `db-setup` also installs pgvector (`CREATE EXTENSION IF NOT EXISTS vector`) for the knowledge bases' vector
index; without it they use a portable index ([knowledge](knowledge.md)).

## Admin console

`admin/` is a static single-page app (Vite, TypeScript, Preact) built into the image and served by the service at
`{EVOKAI_BASE_PATH}/admin/` with a strict Content Security Policy (everything from its own origin, no frames). It uses
the headless client's `EvokaiConsole` from the repository's sources. Sign-in exchanges an admin key (or the operator
key) for a signed, short-lived cookie: `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS, scoped to the API path; the
key is never stored in the browser. Every request must also carry `X-Evokai-Console: 1`, a header a cross-site form
cannot send, so the cookie alone authorises nothing (CSRF). An admin login is bound to its key and ends when the key
is revoked; logout deletes the cookie. An admin key sees its own application; the operator key (`EVOKAI_OPERATOR_KEY`)
sees every application and creates new ones. People can also sign in with single sign-on (OpenID Connect, code with
PKCE, MFA required from the provider when configured); their roles (viewer, operator, owner, per application or for
all of them) come from the provider's claims and are enforced by the service on every route, and every change lands
in the audit log ([configuration](configuration.md#console-sign-in-and-roles)).

Pages: knowledge bases per application (documents, uploads, sync, a test search with scores and citations); workflows (the list with their runs, a YAML or JSON editor that points at the line of each problem next to a read-only drawing of the flow, runs with status filters, a run's step timeline with inputs and outputs as the tracing settings allow, retry, cancel and run now) and approvals (the queue of decisions with what will happen, approve or reject with a comment); evals per agent (cases, runs with their comparison and answer diffs, the red team with its score trend and failures, the cheapest model that passes); applications (with a first-run setup that creates an application, its first agent, an optional MCP server tested
before it is saved, skills and the keys, for an operator with no application yet); per application its overview, agents (editor with versions and the live summary), permissions
(the grants), MCP servers (tools and a connection test), skills (SKILL.md), keys (created and rotated keys are shown
once), limits, usage, security (the policy, events and counts), privacy (a person's data, residency, retention), compliance (the AI Act, GDPR and control-mapping packs, the
controller's statements, snapshots and their diff),
audit (changes with before and after), traces (every turn and run as a waterfall of spans, filters, and the versions
of an agent side by side) and conversations (review with thumbs, each turn's trace, reviewers' notes and labels);
across applications the playground (a real session for a test subject, with every event of the
turn), usage and health (database and migrations, models answering, MCP servers, voice, the purge job, build).
The console shows no amount anywhere (pages, charts, tables, exports): only used and left shares of the caps, computed
by the service (see the hard rule above). The caps themselves are set on the Limits page as plain numbers, never with a
currency.

The console embeds Evokai's own assistant (`<evokai-chat>` with the mascot), which operates the selected application
through the admin MCP server with the person's rights and asks before every change ([assistant](assistant.md)).

The console is internal: it is not meant to be published on the internet (the application's proxy should block
`{base}/admin`; reach it through a port-forward to the service, for example with SSM).

## Deployment

One container (`evokai`), stateless apart from the database (and, with several instances, a Redis or Valkey for the
limits they share, `EVOKAI_REDIS_URL`): Python 3.14, uid 1001, read-only
root with a tmpfs `/tmp`, port 8787, health check on `{base}/health`. Configuration from the
environment (12-factor) plus an application YAML (agents, MCP servers, skills). AWS credentials
from the default chain (ECS task role). `scripts/ci.sh` runs the gates, `scripts/build-image.sh`
builds linux/arm64, smoke-tests read-only and pushes; application infrastructure deploys a pinned
Evokai commit through its own pipeline.

## TypeScript client

`clients/typescript/`, package `@evokai/evokai-client` (npm),
versioned with the service; types generated from `evokai openapi` (voice routes included):

- `/server` for application backends: session tokens, external history, usage, admin.
The client is **headless**: it gives an application everything it needs to build its own
interface, and draws nothing.

- `/browser` framework-free core: session, chat and conversation calls with typed events, cancel
  and replay; microphone (permission, capture, mute, device); voice modes (dictation,
  conversation, barge-in); speech engines chosen by device (in-browser neural TTS on desktop with
  download progress and caching, local `speechSynthesis` on phones, server voice later); audio
  signals for any visual (level, spectrum, syllable onsets, estimated from text when the browser
  speaks); turn phases (listening, thinking, speaking, interrupted) and triggers (confirm, ask,
  navigate, chart, diagram); optional microphone sounds; usage as percentages only.
- `/react`: thin hooks over `/browser` (`useEvokaiSession`, `useVoice`, `useAudioSignals`,
  `useUsage`), no visual components.

## UI components

`clients/ui/`, package `@evokai/evokai-ui`: the interface for applications that do not build their own, on top of the
client's `/browser` entry (a peer dependency). `<evokai-chat>` is a launcher and a chat panel; `<evokai-voice>` the
voice stage alone; both render with Preact in a shadow root, read `--evokai-*` custom properties or a theme object,
speak English or Italian (every text overridable), and have React wrappers (`@evokai/evokai-ui/react`). The visual
payloads are the service's (`chart`, `diagram`); usage is shown as shares only. Pages on
another origin call the session routes directly: the service answers CORS for `{base}/v1/session/*` when an
application lists the page's origin (`evokai.api.hardening`), and every request is still checked against the token's own
application.
