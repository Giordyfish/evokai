# Configuration

## Environment

Everything is read from the environment (prefix `EVOKAI_`); a `.env` file works in development.
Per-application settings (caps, origins, agents, MCP servers, skills, prompts) live in the
database and come from the config YAML, `EVOKAI_BOOTSTRAP_APPS` or the admin API.

| Variable | Default | Meaning |
|---|---|---|
| `EVOKAI_PRODUCTION` | `false` (`true` in the image) | Refuse to start without signing and encryption keys |
| `EVOKAI_PORT` | `8787` | Listen port |
| `EVOKAI_BASE_PATH` | `/agent` | Where the API is mounted |
| `EVOKAI_SIGNING_KEY` | random in dev | HMAC key of session tokens (`evokai keygen signing`) |
| `EVOKAI_ENCRYPTION_KEY` | random in dev | Fernet key for MCP secrets and forwarded credentials |
| `EVOKAI_SESSION_TOKEN_DEFAULT_TTL` / `_MAX_TTL` | `300` / `900` | Session token lifetime, seconds |
| `EVOKAI_DATABASE_URL` | `sqlite+aiosqlite:///./evokai.db` | Any SQLAlchemy URL; `postgres://…` is accepted |
| `EVOKAI_DATABASE_SCHEMA` | none | Schema of the tables (Postgres: also the `search_path`) |
| `EVOKAI_DB_CA_FILE` | none | CA bundle for `?sslmode=verify-full` / `verify-ca` (e.g. the RDS bundle) |
| `EVOKAI_DATABASE_SSL` | `false` | TLS (verify-full) when the URL has no `sslmode` |
| `EVOKAI_APP_DB_ROLE` / `EVOKAI_APP_DB_PASSWORD` | `evokai_app` / none | Used by `db-setup` only |
| `EVOKAI_CONFIG_FILE` | none | Application YAML applied at start (agents, MCP servers, skills) |
| `EVOKAI_BOOTSTRAP_APPS` | `[]` | JSON: applications and keys provisioned by infrastructure (schema below) |
| `EVOKAI_BOOTSTRAP_SERVER_KEY` / `_ADMIN_KEY` | none | Keys registered for the config file's application |
| `EVOKAI_MODEL_ALLOW` | `["bedrock:eu.*","test:*","function:*"]` | Glob allow-list of `provider:model` (set `["bedrock:eu.*"]` in production) |
| `EVOKAI_AWS_REGION` | `eu-north-1` | Bedrock region; credentials from the default AWS chain (task role, env, `AWS_CONFIG_FILE` with `credential_process`) |
| `EVOKAI_PROVIDERS_FILE` | unset | A YAML file merged over the built-in provider catalog: the deployment's own endpoints (an Azure resource, a Vertex location, a self-hosted server with its declared residency and jurisdiction), facts its contracts change, its own prices ([data protection](data-protection.md#the-provider-catalog)) |
| `EVOKAI_PRICE_EUR_USD` | `1.2` | Dollars per euro for the catalog's prices in euros, so every cost is recorded in USD; an assumption that errs high, set your own ([costs](costs.md#prices)) |
| `EVOKAI_HISTORY_MESSAGES` / `_CHARS` | `30` / `60000` | History window sent to the model |
| `EVOKAI_MAX_OUTPUT_TOKENS` | `1200` | Output cap per model call |
| `EVOKAI_CLIENT_TOOL_TIMEOUT_SECONDS` | `120` | How long a turn waits for the result of a custom client tool that waits ([client tools](client-tools.md)) |
| `EVOKAI_FILLER_AFTER_MS` | `700` | Spoken filler when a tool is slow (voice) |
| `EVOKAI_VOICE_ENABLED` | `false` | Server voice (Pipecat over WebRTC); needs the `voice` extra |
| `EVOKAI_ASK_RULES` | `[]` | JSON list: the deployment's default [ask rules](security.md#ask-rules), read before every application's and agent's; checked at start |
| `EVOKAI_VOICE_STT` / `_TTS` | `fake` | Speech providers: a registered name or `module:factory` |
| `EVOKAI_VOICE_SMART_TURN` | `true` | End of turn by Smart Turn v3 (else VAD silence) |
| `EVOKAI_VOICE_ICE_SERVERS` | `[]` | STUN/TURN URLs for WebRTC |
| `EVOKAI_OTEL_ENABLED` | `false` | OpenTelemetry: request spans, metrics and every turn's trace with the GenAI conventions (exporters read `OTEL_*`; [traces](#traces-and-opentelemetry)) |
| `EVOKAI_REDIS_URL` | none | Redis or Valkey (`redis://host:6379/1`, `rediss://` for TLS) for the state every instance must share: rate windows, running turns, tool rates, sign-outs, console sign-in codes and failed sign-ins ([shared limits](#shared-limits-across-instances)). Unset: each instance keeps them in its own memory |
| `EVOKAI_REDIS_PREFIX` | `evokai:` | Put before every key, so several deployments can share one Redis |
| `EVOKAI_ADMIN_UI_ENABLED` | `true` | Serve the admin console at `{EVOKAI_BASE_PATH}/admin/` |
| `EVOKAI_ADMIN_DIST_DIR` | `admin/dist` | Where the built console is (the image ships it at `/app/admin/dist`) |
| `EVOKAI_CONSOLE_SESSION_TTL` | `1800` | Lifetime of a console login, seconds |
| `EVOKAI_OPERATOR_KEY` | none | Deployment-wide key: creates applications and logs in to the console over all of them |
| `EVOKAI_BUILD_COMMIT` | set by the image | The commit shown on the console's health page |
| `EVOKAI_STATE_DIR` | none | A private, persistent directory for generated keys (`keys.json`): signing, encryption and operator keys are created on the first start when the environment does not give them ([quick start](quickstart.md)) |
| `EVOKAI_AUTO_MIGRATE` | `false` | Apply the migrations when `serve` starts (as the URL's user; on Postgres the schema is created if missing) |
| `EVOKAI_PUBLIC_URL` | `http://localhost:{port}{base}` | Where people reach the API, for the first-start message; also the base of the OAuth redirect URI of [connections](connectors/connections.md) (`/v1/oauth/callback`) and of the [channel](channels/README.md) endpoints |
| `EVOKAI_SSO` / `EVOKAI_SSO_CLIENT_SECRET` | none | The console's OpenID Connect provider as JSON, and its client secret ([console sign-in](#console-sign-in-and-roles)) |
| `EVOKAI_AUDIT_TTL_DAYS` | `400` | Audit log entries are deleted after this many days unless the application sets `audit_ttl_days` |
| `EVOKAI_GUARD_ENABLED` | `true` | The security layer; `false` turns every control off at once ([security](security.md)) |
| `EVOKAI_GUARD_CLASSIFIER` | none | Optional third-party prompt-injection classifier, `module:factory` (the policy's `custom` choice) |
| `EVOKAI_GUARD_CLASSIFIER_TIMEOUT_MS` | `1500` | How long the `custom` classifier may take before the turn goes on without it |
| `EVOKAI_GUARD_CLASSIFIER_DEFAULT` | none | What a policy's `injection.classifier: default` (or the old `true`) means: `bedrock_guardrails`, `prompt_guard`, `custom` or `none`. Unset: `custom` when configured, else the first available of Bedrock Guardrails and Prompt Guard ([classifiers](security.md#injection-classifiers)) |
| `EVOKAI_GUARDRAIL_ID` / `EVOKAI_GUARDRAIL_VERSION` | none | The Amazon Bedrock guardrail (prompt attack filter only) and its published version, for `bedrock_guardrails`. The task role needs `bedrock:ApplyGuardrail` on it |
| `EVOKAI_GUARDRAIL_REGION` | `EVOKAI_AWS_REGION` | Where the guardrail lives (an EU region) |
| `EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT`, `EVOKAI_AZURE_CONTENT_SAFETY_KEY` | unset | An Azure AI Content Safety resource for `azure_prompt_shields` and `azure_content_safety` ([security](security.md#injection-classifiers)) |
| `EVOKAI_MODEL_ARMOR_TEMPLATE`, `EVOKAI_MODEL_ARMOR_ENDPOINT`, `EVOKAI_MODEL_ARMOR_TOKEN` | unset | A Google Model Armor template for `model_armor`; the regional host of its location by default; a token, else Application Default Credentials |
| `EVOKAI_MISTRAL_MODERATION_URL` | `https://api.mistral.ai` | Mistral's API for `mistral_moderation` (`MISTRAL_API_KEY`) |
| `EVOKAI_LLAMA_GUARD_URL`, `EVOKAI_LLAMA_GUARD_MODEL`, `EVOKAI_LLAMA_GUARD_KEY` | unset | A self-hosted Llama Guard behind an OpenAI-compatible API for `llama_guard` |
| `EVOKAI_GUARDRAIL_TIMEOUT_MS` | `1500` | How long a Bedrock Guardrails classification may take before the turn goes on without it |
| `EVOKAI_PROMPT_GUARD_MODEL` | none (`/opt/prompt-guard` in an image built with `PROMPT_GUARD=1`) | The Llama Prompt Guard 2 model for `prompt_guard`: a folder made by `scripts/export-prompt-guard.py` (ONNX), or the official files or repo id with `transformers`, `torch` and `HF_TOKEN` |
| `EVOKAI_PROMPT_GUARD_THREADS` | `2` | CPU threads of one Prompt Guard inference |
| `EVOKAI_PROMPT_GUARD_TIMEOUT_MS` | `3000` | After this no further 512-token window of a text starts; the windows scored count |
| `EVOKAI_EMBEDDING_ALLOW` | `["bedrock:amazon.titan-embed-text-v2:0","test:*"]` | Embedding models knowledge bases may use (the data residency applies on top) ([knowledge](knowledge.md)) |
| `EVOKAI_EMBEDDING_MODEL` | `bedrock:amazon.titan-embed-text-v2:0` | The embedding model of a new knowledge base |
| `EVOKAI_KNOWLEDGE_MAX_FILE_MB` | `20` | Largest knowledge document (upload, address or connector file) |
| `EVOKAI_KNOWLEDGE_FETCH_ALLOW` | `[]` | Domains documents may be fetched from by address (with their subdomains); empty: off |
| `EVOKAI_KNOWLEDGE_LINK_TTL` | `600` | Seconds a citation's signed link to a file stays valid |
| `EVOKAI_KNOWLEDGE_WORKERS` | `2` | Documents ingested at the same time by one process |
| `EVOKAI_MEMORY_EMBEDDING_MODEL` | `bedrock:amazon.titan-embed-text-v2:0` | Embeds memories for recall by meaning (within `EVOKAI_EMBEDDING_ALLOW` and the residency) ([memory](memory.md)) |
| `EVOKAI_MEMORY_EMBEDDING_DIMENSIONS` | `512` | Their size |
| `EVOKAI_MEMORY_SUMMARY_MODEL` | none (the agent's model) | Writes the episodes of agents that keep them |
| `EVOKAI_MEMORY_EPISODE_IDLE_MINUTES` | `30` | A conversation idle this long is finished (its episode is written) |
| `EVOKAI_REDTEAM_ON_SAVE` | `true` | Run the attack suite on every saved agent version ([red team](redteam.md)) |
| `EVOKAI_REDTEAM_JUDGE_MODEL` | `bedrock:eu.amazon.nova-2-lite-v1:0` | Grades the few attacks no rule can (an application's `eval_judge_model` comes first); empty: skipped |
| `EVOKAI_ALERT_WEBHOOK_ALLOW` | `[]` | Domains a cost alert webhook may call (https, with subdomains); empty: none ([costs](costs.md)) |
| `EVOKAI_WORKFLOWS_ENABLED` | `true` | Run workflow steps and schedules in this instance ([workflows](workflows.md)) |
| `EVOKAI_WORKFLOW_WORKERS` | `2` | Workflow steps one instance runs at the same time |
| `EVOKAI_WORKFLOW_POLL_SECONDS` | `1` | How often an idle workflow worker looks for due steps |
| `EVOKAI_WORKFLOW_LEASE_SECONDS` | `60` | How long a stopped worker's step stays leased before another instance resumes it |
| `EVOKAI_WORKFLOW_SCHEDULE_SECONDS` | `20` | How often schedules are checked |
| `EVOKAI_WORKFLOW_NOTIFY_ALLOW` | empty | Domains workflow `notify` steps may call; empty turns them off |
| `EVOKAI_WORKFLOW_HOOK_TOLERANCE` | `300` | Seconds an inbound workflow webhook's timestamp may differ from now |
| `EVOKAI_CODE_ENABLED` | `false` | Offer `code_run` (Amazon Bedrock AgentCore Code Interpreter) to agents that ask for it, in applications granted `code` ([code](code.md)) |
| `EVOKAI_CODE_REGION` | `EVOKAI_AWS_REGION` | Where the code sandbox runs; EU regions only |
| `EVOKAI_CODE_INTERPRETER_ID` | `aws.codeinterpreter.v1` | The AWS-managed interpreter (no internet) or a custom one |
| `EVOKAI_CODE_TIMEOUT_SECONDS` | `60` | What one `code_run` call may take; past it the sandbox session is stopped |
| `EVOKAI_CODE_USD_PER_SECOND` | `0.00003` | Estimated cost of a sandbox second, recorded for the caps (never shown) |
| `EVOKAI_SECURITY_EVENT_TTL_DAYS` | `180` | Security events are deleted after this many days unless the application sets `security_event_ttl_days` |
| `LOG_LEVEL`, `LOG_FORMAT` | `INFO`, `json` | Logging |

### `EVOKAI_BOOTSTRAP_APPS`

A JSON list; each entry creates the application (by `id`, its slug) or updates it. Keys given in
full are registered (never moved between applications); omitted fields are left as they are.

```json
[{
  "id": "bookshop",
  "name": "Inkwell Books",
  "serverKey": "evk_srv_…",
  "adminKey": "evk_adm_…",
  "origins": ["https://inkwell-books.example"],
  "storageMode": "evokai",
  "conversationTtlDays": 7,
  "voiceEngine": "browser",
  "caps": {"monthlyUsd": 9, "dailyUsd": null, "subjectMonthlyUsd": 3, "subjectDailyUsd": 0.75},
  "currency": "EUR",
  "exchangeRate": 0.9048,
  "locale": "en",
  "grants": {"channels": {"server": true}, "client_tools": {"memory": false}},
  "messages": {
    "limit.app": "The assistant of {appName} has used its monthly budget.",
    "limit.appDay": "…",
    "limit.subjectMonth": "You have reached your monthly limit for the assistant.",
    "limit.subjectDay": "You have reached today's limit. Try again tomorrow."
  }
}]
```

`caps` also takes the new form, `"application"` and `"subject"` lists of
`{"period", "amount", "resets"}` in `currency` (see [Limits](#limits)); given caps replace the
stored ones. The same settings in an application YAML go under `application:` as `caps`,
`currency`, `exchange_rate`, `monthly_cap_usd`, `daily_cap_usd`, `subject_monthly_usd`,
`subject_daily_usd`, `conversation_ttl_days`, `locale`, `timezone`, `messages`,
`storage_mode`, `voice_engine`, `allowed_origins`. `grants` has the shape of the YAML section below.


## Limits

Evokai enforces them before every model call, with an estimate of the next call: the
application's caps first, then the subject's, each in the order month, week, day. Several caps
coexist (an application month and a weekly cap per person, say).

```yaml
application:
  timezone: Europe/Rome          # the caps' windows, and dates in prompts (UTC by default)
  currency: EUR                  # what caps are written in (ISO code, USD by default)
  exchange_rate: 0.9048          # EUR per 1 USD; required when the currency is not USD
  caps:
    application:                 # the whole application
      - {period: month, amount: 9}               # resets on the 1st (resets: 15 for the 15th)
    subject:                     # each person (a session token may override them)
      - {period: week, amount: 5, resets: saturday}
      - {period: day, amount: 1.5}
```

### Fixed calendar windows

A cap is a maximum over a fixed calendar window, never a rolling one, and starts again at 00:00 in
the application's `timezone`:

| `period` | Window | `resets` |
|---|---|---|
| `day` | 00:00 to 00:00 the next day | not allowed |
| `week` | seven days from 00:00 of its weekday | `monday` (default) ... `sunday` |
| `month` | from its day of the month to the same day of the next month | `1` (default) to `31`; a month without that day resets on its last day |

Windows follow the wall clock: across a daylight saving change a window still starts at local
midnight (a week that contains the change is an hour longer or shorter). One cap per period in
each list.

### Currency

Provider prices and the recorded spend stay in USD (Bedrock bills in USD). A cap in another
currency is converted to USD with the application's `exchange_rate` (units of its currency per 1
USD, greater than zero) at the moment it is checked; Evokai never fetches rates, so change the
rate in the config or the console when you want it to move. Changing the currency keeps the
amounts as numbers: they then mean the new currency. The server-to-server usage report
(`GET /v1/usage` with a key) gives `currency` and the `exchangeRate` its amounts in that currency
(`cost` per row, `spent` per cap) were converted with.

### The old fields

`monthly_cap_usd`, `daily_cap_usd`, `subject_monthly_usd` and `subject_daily_usd` keep working and
keep their meaning: a monthly or daily cap in USD, whatever `currency` says. A config (or a console
or admin API change) that sets the same cap in both forms (`daily_cap_usd` and a `day` entry in
`caps.application`, say) is refused; setting a cap in `caps` clears its old field. An application
always has a monthly cap: when `caps.application` has no month, `monthly_cap_usd` (10 USD unless
changed) applies.

A config file that sets any cap (`caps` or an old field) owns the application's caps: at every
start they are replaced by the file's, so a cap removed from the file is removed from the
application (an old monthly cap left out goes back to 10 USD). A file without caps leaves the
stored ones (set in the console, say) alone. `caps` in `EVOKAI_BOOTSTRAP_APPS` works the same way.

### Session tokens

A session token may set the subject's caps for its session with `limits`: the old
`subject_monthly_usd` and `subject_daily_usd` (USD), or `caps`, a list in the application's
currency (`[{period: week, amount: 5}]`). Each replaces the application's default subject cap of
its period; a cap without `resets` resets when that default does. One form per period.

### Blocked turns and what people see

A blocked turn ends with an `error` event `{code: "limit", limit, message, resetsAt}`: `limit` is
`app_month`, `app_week`, `app_day`, `subject_month`, `subject_week` or `subject_day`, `resetsAt`
the reset (ISO time), and `message` says which cap was reached and when it resets, in the
application's locale and time zone, never an amount («You have reached the assistant's weekly
limit: it resets on Saturday at 00:00.»). In voice the message is also said aloud. The
messages are `limit.app`, `limit.appWeek`, `limit.appDay`, `limit.subjectMonth`,
`limit.subjectWeek` and `limit.subjectDay` (each overridable per application in `messages`;
`{appName}` and `{resets}` are filled in). Spend is recorded at the price of the model that
answered.

People see shares only, never amounts. The session-token response, `GET /v1/session` and every
`usage` event carry `dayPct`, `weekPct` and `monthPct` (the subject's caps), `companyPct` (the
application's month), `monthOut` (a monthly cap is reached), `out` (any cap is), `binding` (the
cap that limits most: a reached one that resets last, else the highest share), `resetsAt` (its
reset) and `caps` (every cap as `{limit, scope, period, usedPct, leftPct, resetsAt}`). The admin
console shows the caps an admin sets (the maxima, in the application's currency) and what is used
only as a share of each cap. Amounts are only on `GET /v1/usage` with the server or admin key, and
in a server run's answer (server to server). `inputTokens` there includes `cacheReadTokens` and
`cacheWriteTokens` (prompt caching).


## Applications, keys and tokens

1. An application (e.g. a bookshop's back office) has a **server key** (`evk_srv_…`) for its backend and optionally
   an **admin key** (`evk_adm_…`). Only hashes are stored; rotate by adding a new key and revoking
   the old one (`/v1/admin/keys`).
2. The backend mints a **session token** for its signed-in user:
   `POST {base}/v1/session-tokens` with `{subject, agent, forwardCredential, limits?, voiceEngine?, voice?,
   storageMode?, model?, groups?}` (each choice within the application's grants). `groups` are the user's groups or
   roles as the application knows them: knowledge documents with access groups are found only by sessions with one of
   them ([knowledge](knowledge.md#access)).
   `subject` is the application's own user id; `forwardCredential` is that user's token, sent to
   the application's MCP server so the agent acts as the user.
3. The browser uses the session token for `POST {base}/v1/session/chat` (SSE), cancel, replay and
   (server voice) the WebRTC offer.

Storage: `evokai` (conversations in Evokai's database) or `external` (the application sends the history with each
request and stores the new messages Evokai returns). The application's `storage_mode` is the default; a session token
may choose another granted mode (`storageMode`) for a new conversation, which keeps it.
Conversations are never stored at a model provider.


## Capability grants

What an application may use is granted per application. The application still makes every runtime choice (the
voice engine, the voice, the storage mode and the model of a session, in the session-token request); grants only say
which choices exist, and the console grants or revokes them one by one.

| Kind (YAML section) | Items | Values | Default |
|---|---|---|---|
| `model` (`models`) | a model (`eu.anthropic.claude-haiku-4-5`, provider optional for Bedrock) or a pattern (`bedrock:eu.anthropic.*`) | `any` (primary and fallback), `fallback` (fallback only), `off` | `any` |
| `mcp` (`mcp_servers`) | a registered MCP server | true / false | true |
| `tool` (`tools`) | `server/tool` (the server's own tool name) | true / false | true |
| `skill` (`skills`) | a skill | true / false | true |
| `client` (`client_tools`) | `navigate`, `chart`, `diagram`, `ask`, `confirm`, `memory` | true / false | true |
| `voice` (`voice`) | `browser` (on-device speech), `server` (Pipecat) | true / false | true |
| `stt` (`stt`) | a server speech-to-text engine (`EVOKAI_VOICE_STT`) | true / false | true |
| `tts` (`tts`) | `browser/<voice id>` or `server/<voice id>` | true / false | true |
| `storage` (`storage`) | `evokai`, `external` | true / false | the application's own `storage_mode` only |
| `channel` (`channels`) | `browser` (session tokens, the AG-UI endpoint), `server` (server runs, the OpenAI-compatible endpoint with a server key), `a2a` (agents published over [A2A](a2a.md)), `email`, `teams`, `slack` ([channels](channels/README.md)) | true / false | true; `a2a`, `email`, `teams`, `slack` false |
| `knowledge` (`knowledge`) | `*` (knowledge search as a whole) or a knowledge base | true / false | true |
| `workflow` (`workflows`) | `*` (workflows run at all), `autonomous` (tool steps marked `safe` change data without an approval) | true / false | true; `autonomous` false |
| `code` (`code`) | `*` (isolated code execution, [code](code.md)) | true / false | false |
| `connection` (`connections`) | a provider people may connect (`microsoft365`, `google`) or one of its tool families (`microsoft365/mail`, `/calendar`, `/files`) | true / false | providers false; families true (they need their provider) |
| `capability` (`capabilities`) | a capability of the catalog (`code_mode`, `compaction`, `subagents`, every pydantic-ai-harness one: [capabilities](capabilities.md)) | true / false | false |

Every item either follows its default or is overridden for the application, and an override can be reset. The
defaults keep what an application could do before grants existed, so an application without overrides behaves as
before. Models are always within the deployment allow-list (`EVOKAI_MODEL_ALLOW`) and the application's
`allowed_models`; a grant can only narrow them. An exact model override wins over a pattern; among patterns the
longest match wins. A voice of the catalogue (`/v1/admin/voices`) whose `region` is `device`, `local` or `browser` is
a browser voice; any other is a server voice. A tool can be used only when its server is granted too, server speech
needs its STT engine, and without `confirm` tools that change data are not offered at all.

In an application YAML:

```yaml
grants:
  models:
    "eu.amazon.nova-2-lite-v1:0": fallback
    "bedrock:eu.anthropic.claude-opus-*": "off"   # quote off: YAML reads a bare off as false (also accepted)
  tools: {"bookshop/order_refund": false}
  skills: {shipping-rates: false}
  client_tools: {memory: false}
  voice: {server: false}
  storage: {external: true}
  channels: {server: true}
```

When the file has a `grants:` section it is the source of truth: at every start the application's overrides become
exactly those (an empty section clears them). Without the section, overrides made in the console or with
`PATCH /v1/admin/grants` are left alone. The same shape works as `grants` in `EVOKAI_BOOTSTRAP_APPS`.

Where they are enforced, and what a refusal looks like:

| Where | What | Refusal |
|---|---|---|
| `POST /v1/session-tokens` | browser channel; the requested `model` (granted as primary), `voiceEngine`, `voice`, `storageMode`; server voice's STT engine | 403 `channel_not_granted`, `model_not_granted`, `voice_engine_not_granted`, `voice_not_granted`, `stt_not_granted`, `storage_mode_not_granted` |
| every turn (`/v1/session/chat`, server voice) | browser channel, the conversation's storage mode, the voice engine of a voice turn | 403 as above |
| every turn and server run | only granted MCP servers, tools, skills and client tools are offered; the model chain skips models that are off and never starts with a fallback-only model | no granted model: `error` event `model`, reason `model` (runs: 403 `model_not_granted`) |
| `POST /v1/agents/{name}/run` | server channel | 403 `channel_not_granted` |
| `POST /v1/session/voice/offer` | server voice, its STT engine, the session's voice | 403 as above |
| `PUT /v1/admin/agents/{name}` | the definition may not reference an ungranted model, MCP server, skill, channel or client tool it sets itself, nor enable code execution without the `code` grant | 400 `not_granted` with `problems` |

Without a request, a session uses the application's default voice engine when it is granted (else the other one), and
the application's storage mode when it is granted (else the other one; 403 when neither is). A conversation keeps the
storage mode it was created with. The config file is applied even when one of its agents references something not
granted (a warning is logged): the runtime offers only what is granted.

## Evals

Each application has its own eval budget (`eval_monthly_usd`, 2 by default), a judge model for rubric checks
(`eval_judge_model`, none by default), `eval_on_save` (true) and a test user's token (`eval_credential`): see
[evals](evals.md). Eval usage never counts against the caps above.

## Memory, red team and cost alerts

Per application (`PATCH /v1/admin/application`, the console, or the YAML):

```yaml
application:
  memory: {mode: ask, ttl_days: 365}               # off | ask | on (default on)
  redteam: {gate: monitor, min_score: 80, on_save: true}   # gate: off | monitor | enforce
  cost_alerts: {thresholds: [75, 90, 100], webhook: "https://hooks.example.org/evokai"}
```

`cost_alert_secret` (the webhook's HMAC key) is set with the admin API or the console only, never in the YAML. See
[memory](memory.md), [red team](redteam.md) and [costs](costs.md).

## Shared limits across instances

Some limits need a count every instance sees: the abuse limits (`abuse.rate`, `abuse.concurrency`), tool rates
(`tools.rate`), the requests per session token, the console's failed sign-ins per address, one-time sign-in codes, a
person's sign-out (read on every session request) and the signal that tells every instance to forget its cache of
verified keys after a revocation. They live behind a small store (`evokai.core.shared`):

- without `EVOKAI_REDIS_URL`, the instance's memory: right for one instance; with several, every instance counts on its
  own and the limits multiply by the number of instances;
- with `EVOKAI_REDIS_URL`, Redis or Valkey shared by every instance: counters are atomic (`INCRBY` with an expiry set
  when the counter is created), running turns expire 15 minutes after the subject's last new turn (an instance that dies
  does not hold anyone back for good), codes are taken with `GETDEL` so they work once on any instance.

Keys never hold a subject, an address or a code in clear: they are keyed hashes, and every key expires (from 10 seconds
to a few minutes; the key cache epoch after 30 days). A Redis that does not answer never refuses a request: each
operation falls back to the instance's own memory (as without Redis), a warning `shared_state_unavailable` is logged at
most every 30 seconds, and Redis is tried again after 5 seconds. The console's health page shows which store is in use
and whether it answers.

If the application already runs Valkey (say `valkey/valkey:8.1-alpine` with `noeviction`), give Evokai its own database index, for
example `EVOKAI_REDIS_URL=redis://valkey:6379/1`. Every Evokai key has an expiry, so `noeviction` is safe.

Still per instance, by design: the short caches of the database (verified keys, agent versions, MCP tool lists, for
seconds), the running turns themselves (cancelling a turn reaches it only on the instance that runs it, so a browser
session should stick to one instance, as SSE already needs) and the server voice connections.

## Traces and OpenTelemetry

Every turn, eval case and server run records a trace (`evokai_traces`): the turn and one span per model call (model,
provider, input, output and cache tokens, time to first token, stop reason, fallback), tool call (tool, MCP server,
outcome, result size, whether it waited for a confirmation and for how long), guard decision (control, rule, action,
mode, severity), speech (time to the first spoken sentence) and, when a step records one, retrieval. The console shows
them on the **Traces** page; the admin API is `GET /v1/admin/traces`, `GET /v1/admin/traces/{id}` and
`GET /v1/admin/agents/{name}/version-stats`.

What content a trace keeps is per application, `tracing_content` (`PATCH /v1/admin/application`, or in the YAML):

```yaml
application:
  tracing: {content: masked}   # none | masked (default) | full
```

- `none`: timings, counts and attributes only;
- `masked`: the messages a model saw and wrote, tool arguments and results, with personal identifiers masked and
  secrets removed;
- `full`: content as it was, secrets still removed; read as `masked` while the agent enforces `pii.history` or
  `pii.model`, so no raw identifier (in a tool's arguments either) is kept.

With external storage a trace never keeps content, whatever the setting. Traces follow `conversation_ttl_days` (every
trace, server runs and external storage included), go with their conversation and with the person's data on erase, and
are part of a person's export.

With `EVOKAI_OTEL_ENABLED=true` (and the `otel` extra, in the image) every trace is also exported over OTLP, using the
OpenTelemetry GenAI semantic conventions: a root span `invoke_agent {agent}` (`gen_ai.operation.name`,
`gen_ai.agent.name`, `gen_ai.agent.version`, `gen_ai.conversation.id`, `session.id`, `user.id` as the subject's keyed
hash, never the subject), `chat {model}` spans (`gen_ai.provider.name`, `gen_ai.request.model`,
`gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`,
`gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_creation.input_tokens`, `gen_ai.response.finish_reasons`),
`execute_tool {tool}` spans (`gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type`, `evokai.mcp.server`) and
`evokai.*` attributes for the rest (guard decisions, time to first token, fallback). Content goes only as the
application's `tracing_content` allows, as `gen_ai.input.messages`, `gen_ai.output.messages`,
`gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`. Amounts never do.

The exporter reads the standard variables. For **Langfuse** (its OTLP endpoint takes HTTP/protobuf with Basic auth of
the project's public and secret keys; EU cloud shown, self-hosted is `https://<host>/api/public/otel`):

```bash
EVOKAI_OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $(printf '%s' "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64 -w0)"
OTEL_SERVICE_NAME=evokai
```

The exporter adds `/v1/traces` to the endpoint (set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` to the full address to send
only traces there, and keep metrics elsewhere). LangSmith takes the same OTLP traces at
`https://eu.api.smith.langchain.com/otel` with `OTEL_EXPORTER_OTLP_HEADERS="x-api-key=...,Langsmith-Project=..."`;
Grafana (Tempo, through Alloy or an OpenTelemetry Collector) at the collector's OTLP address. An OTLP backend outside
the EU is a sub-processor of the traces' content: keep `tracing_content: none` for those applications, or send to a
collector in the region.

## Conversation time to live

`conversation_ttl_days` (admin API, YAML, `conversationTtlDays` in `EVOKAI_BOOTSTRAP_APPS`): a conversation
stored in Evokai (its own storage mode, else the application's) disappears this many days after its last message. From that moment
it is not listed and every read returns 404 (`expired_conversation` for a session), even before the
purge runs. The purge (hourly in the service, or `evokai purge` from cron) deletes its messages,
events, turns, pending confirmations and questions, thread and the conversation itself, for good.
Usage records stay, so budgets keep counting, but lose the link to the conversation and the turn.
Traces and reviewers' notes go with the conversation; the purge also deletes every other trace older than the time to
live (server runs, external storage). Without the setting, conversations and traces are kept.

## Console sign-in and roles

The console accepts three logins, all exchanged for the same short `HttpOnly`, `SameSite=Strict` cookie:

| Login | Role |
|---|---|
| An application's **admin key** (`evk_adm_...`), typed or through a one-time sign-in link | owner of that application |
| The **operator key** (`EVOKAI_OPERATOR_KEY`) | owner of every application: a **deployment owner** |
| **Single sign-on** with an OpenID Connect provider | the roles its claims map to |

Roles, enforced by the service on every admin and console route (`evokai.identity.roles`; reads need viewer,
changes need operator, the routes below need owner, and a route not listed is a change like any other):

| Role | May |
|---|---|
| `viewer` | read everything of its applications: agents, permissions, MCP servers, skills, keys (never their secrets), limits, usage as shares, conversations, security events, the audit log |
| `operator` | also change agents (not their `security` policy), permissions, MCP servers, skills, prompts and keys, use the playground, test servers, answer data requests (export, erase, sign-out everywhere) |
| `owner` | also change the application's settings (`PATCH /v1/admin/application`: caps, origins, models, storage, retention, residency), its security policy and agents' `security`, the deployment's voices |
| owner of `*` | a deployment owner: also creates applications, sets the sign-in provider, disables console users, reads the whole audit log (the console's **Access** page) |

### Single sign-on

Authorization code with PKCE (S256), `state` and `nonce` kept in a short encrypted cookie, the ID token checked
against the provider's keys (issuer, audience, expiry, nonce; RS, PS and ES algorithms only). Multi-factor
authentication is the provider's job: with `require_mfa` the ID token's `amr` must contain one of `mfa_values`
(`mfa`, `otp` by default). Set it in the console (**Access**, deployment owners; it wins and keeps the client secret
encrypted) or in the environment:

```bash
EVOKAI_SSO='{
  "issuer": "https://login.microsoftonline.com/<tenant>/v2.0",
  "client_id": "<application id>",
  "allowed_domains": ["example.com"],
  "role_claim": "groups",
  "require_mfa": true,
  "roles": [
    {"claim": "<owners group id>", "role": "owner", "applications": ["*"]},
    {"claim": "<bookshop operators group id>", "role": "operator", "applications": ["bookshop"]},
    {"email": "*@example.com", "role": "viewer", "applications": ["bookshop"]}
  ]
}'
EVOKAI_SSO_CLIENT_SECRET=...            # confidential clients; public clients rely on PKCE alone
EVOKAI_PUBLIC_URL=http://localhost:8788/evokai   # the redirect URI is {EVOKAI_PUBLIC_URL}/v1/console/sso/callback
```

Register `{EVOKAI_PUBLIC_URL}/v1/console/sso/callback` (or `redirect_uri`) at the provider; the Access page shows
it. Rules match a value of the role claim (`claim`) or the e-mail (`email`, a pattern), and give a role over
application slugs or `*`; the highest role wins, and a person no rule matches is refused. A disabled user's login ends
at once; roles are read again at every sign-in (logins last `EVOKAI_CONSOLE_SESSION_TTL`). Admin keys and the
operator key keep working, so a wrong provider never locks the console.

### Audit log

Every change through the console or the admin and server APIs is written to `evokai_audit_log`: when, who
(`sso:<e-mail>`, `key:<prefix>`, `admin-key:<id>`, `operator-key`), with which role, the action (`agent.put`,
`grants.change`, `key.revoke`, `application.update`, `security.policy`, `privacy.erase`, `console.login`...), the
target, the state before and after (secret fields masked, secret-looking values redacted, people's ids as keyed
hashes), the client address and the request's correlation id. Read it with `GET /v1/admin/audit` (an application's,
any role) or `GET /v1/console/audit` (everything, deployment owners), or on the console's **Audit** tab and Access
page. Entries are deleted after `audit_ttl_days` (application YAML, admin API) or `EVOKAI_AUDIT_TTL_DAYS` (400;
deployment-wide entries always follow it).

## Data protection

`residency` (application YAML, `EVOKAI_BOOTSTRAP_APPS`, `PATCH /v1/admin/application`, the console's Privacy page)
declares where the data lives and which inference geographies the models may use (`model_regions: [eu]`) or which
further model patterns (`models`); models outside it are never called. `data_protection` (the same places;
`dataProtection` in `EVOKAI_BOOTSTRAP_APPS`) is the data protection profile: residency, jurisdiction and retention rules
every external processor must meet, models, embeddings, classifiers, the code sandbox, server speech and connections
alike ([data protection](data-protection.md#the-data-protection-profile)):

```yaml
application:
  data_protection:
    residency: {geographies: [eu]}       # or countries: [DE, FR, IT]
    jurisdiction: [eu]                   # providers controlled from the EU or EEA
    retention: zero_retention            # any | no_training | zero_retention
    browser: {online_voices: false, online_recognition: false}
```

`GET /v1/admin/data-protection` lists every processor with its verdict and why one is excluded. `POST /v1/privacy/export`, `/erase` and
`/revoke-sessions` with the server key (or a console login) answer a person's requests. What is stored, for how long
and the sub-processors are in [privacy](privacy.md). The [compliance packs](compliance.md) (AI Act, GDPR, ISO/IEC 27001
and SOC 2 mapping) are generated from this configuration at `GET /v1/admin/compliance/{pack}`.

## Security policy

`security` (application YAML, `security` in `EVOKAI_BOOTSTRAP_APPS`, `PUT /v1/admin/security`, the console's Security
page) and `security` in an agent's definition set the controls of the security layer: each `off`, `monitor` or
`enforce`, with their settings (allow and deny lists, thresholds, limits). Without them the defaults apply: monitor,
except secrets in answers, the tool policy, egress allow-lists, personal data in logs and the marking of untrusted
content, which are enforced. `security_event_ttl_days` keeps the security events that many days (default
`EVOKAI_SECURITY_EVENT_TTL_DAYS`). Every control, its OWASP mapping and every setting are in [security](security.md).

`ask_rules` (under `application` in the config file, `PUT /v1/admin/ask-rules`, the console's Permissions page) and
`ask_rules` in an agent's definition make tool calls wait for the person's confirmation, after the deployment's
`EVOKAI_ASK_RULES`; they only add confirmations ([ask rules](security.md#ask-rules)).

## Bootstrap from a mounted config file

Infrastructure provisions an application with a read-only file and environment variables only:

```
/run/evokai/app.yaml                  EVOKAI_CONFIG_FILE=/run/evokai/app.yaml
/run/evokai/skills/<name>/SKILL.md    (skills_dir: skills, resolved next to the file; files beside it)
```

```yaml
application:
  slug: bookshop
  name: Inkwell Books
  server_key: "${EVOKAI_BOOKSHOP_SERVER_KEY}"   # optional; admin_key too
  allowed_origins: ["${BOOKSHOP_PUBLIC_ORIGIN}"]
  allowed_models: ["bedrock:eu.*"]
  currency: EUR
  exchange_rate: 0.9048
  caps:                           # the file's caps replace the stored ones at every start
    application: [{period: month, amount: 9}]
    subject: [{period: week, amount: 5, resets: saturday}]
  conversation_ttl_days: 7
  storage_mode: evokai
  voice_engine: browser
  locale: en
  timezone: Europe/Dublin
  messages: {limit.app: "..."}
  identifiers:                  # optional: the application's own codes, protected like IBANs (docs/security.md)
    - name: order_number
      match: [{pattern: 'ORD-\d{6}'}]
      speech: {words: {en: "the order number"}}
  ask_rules:                    # optional: calls that wait for the person's confirmation (docs/security.md)
    - {action: customer_get, argument: customer_code, resource: "VIP-*", label: {en: Key accounts, it: Clienti chiave}}
mcp_servers:
  - name: bookshop
    url: "${BOOKSHOP_MCP_URL}"
    auth: forward_user          # or: secret, with secret_env: NAME_OF_ENV_VAR
  - name: browser               # an Evokai capability worker (docs/capabilities.md#separate-workers)
    url: "${BROWSER_WORKER_URL}"
    auth: secret
    secret_env: BROWSER_WORKER_SECRET
    worker: true
oauth_apps:                     # optional: OAuth apps of GitHub, Linear, Notion, Slack (docs/connectors/connections.md)
  github: {client_id: Iv23liExample, client_secret_env: GITHUB_OAUTH_SECRET, writes: false}
skills_dir: skills
agents:
  - name: bookshop
    model: eu.anthropic.claude-haiku-4-5-20251001-v1:0
    skills: [returns-policy, shipping-rates]
    instructions: |
      ...
    handoff_to: [returns]       # optional: agents it may hand the conversation to
workflows:                      # optional: durable workflows (docs/workflows.md)
  - name: weekly-stock-report
    timezone: Europe/Dublin
    triggers: [{kind: schedule, cron: "0 9 * * 1", input: {week: "{{ now.date }}"}}]
    steps:
      - ...
```

A workflow of the file with problems stops the bootstrap with their paths (`workflow weekly-stock-report:
steps[2].if: ...`); an unchanged one keeps its version.

`identifiers` declares the application's own codes (an order number, a library card): found, masked, tokenised and
spoken like the built-in personal data types, with a pattern or a number of digits, an optional checksum and context,
and how speech says them. The keys, the precedence over built-in types and the speech modes are in
[security](security.md#application-identifiers). An invalid declaration, or a `security.pii.types` entry that is
neither built-in nor declared, stops the bootstrap.

### Prompt caching and preloaded skills

Two agent options cut the cost of agents that send the same long prompt again and again (a triage
agent that reads a policy skill for every e-mail):

```yaml
agents:
  - name: mail-triage
    model: eu.anthropic.claude-haiku-4-5-20251001-v1:0
    skills: [returns-policy]
    preload_skills: [returns-policy]   # its text goes in the system prompt: no skill_load round trip
    prompt_cache: "1h"                  # off (default), "5m" or "1h"
```

- `suggestions`: up to 8 starter questions, in the agent's language, returned with the session token
  (`suggestions`) and by `GET /v1/session`; the client shows them before the first message. Keep them
  generic over time ("the last month", not a month name).
- `preload_skills`: skills (each one also in `skills`, when the agent lists them) whose SKILL.md
  text, with the list of their files, is appended to the instructions, before anything that changes
  per request. The other skills stay on demand; when every skill is preloaded and none has files the
  agent gets no `skill_load` tool.
- `prompt_cache`: Bedrock prompt caching on Anthropic models (see the architecture's
  [Prompt caching](architecture.md#prompt-caching)). Quote the values: YAML reads a bare `off` as
  false (accepted too). Other models in the chain run uncached. Caching pays when the cached prefix
  is read again within its TTL: a 1 hour write costs 2x the input price and a read 0.1x, so it
  breaks even from about two requests an hour on the same prefix; `5m` writes cost 1.25x.
- Both are in the admin API and in the console's agent editor (Model: Prompt cache; Skills:
  Preloaded skills). An agent that uses neither keeps the version it had before they existed.

### Skills: files, scripts and limits

A skill works like an Anthropic Agent Skill (progressive disclosure): only its name and description are in every
prompt; `skill_load` returns the body of SKILL.md with the **list** of the skill's files (path, size, first heading),
never their text; the agent reads a file with `skill_load {"name", "file"}` when it needs it, and runs a script with
`skill_run` ([code](code.md#skill-scripts-skill_run)).

```
skills/returns-policy/
  SKILL.md                  front matter name + description, then the body
  references/carriers.md    references: any .md, .txt, .yaml or .yml
  data/shipping-rates.csv   data and templates under data/ or assets/ (.csv, .tsv, .json, .md, .txt)
  scripts/refund_total.py   scripts: .py under scripts/, run only with code execution on
```

| Limit | Value | Why |
|---|---|---|
| description | 1,024 characters | Anthropic's limit; it goes into every prompt, so keep it to a sentence or two |
| SKILL.md body | 20,000 characters | what one `skill_load` returns; move detail into reference files |
| one read of a file | 20,000 characters | a longer file is read in pages: the result names the `offset` to read on |
| whole skill | 500,000 bytes, 50 files | SKILL.md and every file together |
| one script | 50,000 bytes | |
| a path | 200 characters | |

A file longer than one read is paged rather than refused: a long policy or price list stays usable whole, while
each result stays bounded. Nothing is cut silently: a skill over a limit stops the bootstrap with its folder, its name
and the rule (`bootstrap: skill skills/returns-policy: returns-policy: SKILL.md has 23,410 characters after its front matter, the
limit is 20,000`), and the admin API and the console answer 400 `skill_limit` with the same text.

Paths are relative, with `/` between folders, letters, digits, `.`, `-` and `_` only: no `..`, no absolute path, no
hidden part. In a folder, a link that leaves the folder or a file that is not UTF-8 is an error; hidden files and
`__pycache__` are ignored, and a file of another type (an image, a `.py` outside `scripts/`) is skipped and logged as
`skill_files_skipped`. A skill with only its SKILL.md works as before.

Skills can also be written inline in the file (this is what the console's export writes, so an export bootstraps the
same skills with their files):

```yaml
skills:
  - name: returns-policy
    description: How a return works, step by step.
    body: |
      ...
    files:
      references/carriers.md: |
        # Carriers and their tracking pages
        ...
```

A skill of the file (folder or inline) is the file's: at every start its text and its files are written again when
they changed, files added in the console included. In the admin API, `PUT /v1/admin/skills/{name}` takes `files` (all
of them, replacing the stored ones; left out, they are kept), and `GET`, `PUT` and `DELETE
/v1/admin/skills/{name}/files/{path}` read, add or replace, and remove one file. `GET /v1/admin/skills/{name}` lists the
files with their kind and says whether the scripts can run (`scripts.reason`: `deployment`, `grant` or `agent`).

An MCP server can also be a [ready connector](connectors/README.md) Evokai runs itself:

```yaml
mcp_servers:
  - name: crm
    connector: sql                 # sql, openapi, google_drive, microsoft_files, imap
    config: {dsn_env: CRM_DATABASE_URL, tables: [public.customers], row_limit: 100}
```

`${VAR}` and `${VAR:-default}` are read from the environment. At every start the file is applied
idempotently: the application is created or updated, keys given in full are registered (never
moved to another application), MCP servers are upserted, skills are written only when they
changed, and an agent gets a new version only when its definition changed. Everything
`EVOKAI_BOOTSTRAP_APPS` can set, the file can set too, so one of the two is enough.

## Capabilities

A capability adds something to an agent's runs: Code Mode, compaction, subagents, and every capability of
pydantic-ai-harness ([capabilities](capabilities.md)). Each one needs three switches, like `code_run`: the deployment
(`EVOKAI_CAPABILITIES`, a JSON list of ids or `["*"]`, empty by default), the application's `capability` grant (off by
default) and the agent:

```yaml
agents:
  - name: assistant
    capabilities:
      code_mode: {enabled: true}
      compaction: {enabled: true, config: {keep_tokens: 12000}}
      subagents: {enabled: true}
    subagents: [research]
```

| Variable | Default | Meaning |
|---|---|---|
| `EVOKAI_CAPABILITIES` | `[]` | Ids of the catalog this deployment offers (`["*"]` for all) |
| `EVOKAI_CODE_MODE_TIMEOUT_SECONDS` | `10` | Execution time of one `run_code` program (an agent may set less) |
| `EVOKAI_CODE_MODE_MEMORY_MB` | `128` | Heap of one program |
| `EVOKAI_CODE_MODE_TOOL_CALLS` | `50` | Tool calls one program may make |
| `EVOKAI_COMPACTION_KEEP_TOKENS` | `15000` | Tokens of the latest messages kept word for word beside the summary (an agent may set its own) |
| `EVOKAI_COMPACTION_BUFFER` | `0.15` | Share of the context window left free (at least 16,000 tokens) |
| `EVOKAI_COMPACTION_MODEL` | the agent's model | Writes the summaries (within the allow-lists and grants) |
| `EVOKAI_CONTEXT_WINDOWS` | `{}` | Input tokens per model id over the vendored models.dev snapshot, e.g. `{"ollama:llama3.2": 8192}` |
| `EVOKAI_SUBAGENT_CALLS` | `4` | Subagent calls one turn or run may make |
| `EVOKAI_SUBAGENT_TIMEOUT_SECONDS` | `120` | What one subagent call may take |
| `EVOKAI_WORKER_MODEL_CALLS` | `50` | Model calls a capability worker may make through Evokai in one turn or run (its call token, [capabilities](capabilities.md#separate-workers)) |
| `EVOKAI_WORKFLOW_DURABLE_AGENTS` | `false` | Run workflow `agent` steps marked `durable` as pgtask tasks, their model calls checkpointed (Postgres only, [workflows](workflows.md#durable-agent-steps)) |

The keys of the providers capabilities use (web search, third-party integrations, remote sandboxes) are set per
application, encrypted like MCP secrets: in the console (Permissions, Capabilities, Credentials), through
`PUT /v1/admin/capability-credentials/{provider}` (`{values: {field: value}}`; an empty value keeps, null clears;
secrets are never returned), or in the application file:

```yaml
capability_credentials:
  exa: {api_key: "${ACME_EXA_KEY}"}
  github: {token: "${ACME_GITHUB_TOKEN}"}
  render: {api_key: "${ACME_RENDER_KEY}", workspace_id: tea-123}
```

Without a value of its own, an application uses the deployment's environment variable of that field (`EXA_API_KEY`,
`GITHUB_TOKEN`, ...). The providers and their fields are in [capabilities](capabilities.md#credentials).

### Compaction

With `compaction` on, a conversation stored by Evokai is not simply cut at the history caps: before it would lose a
message, its older part is folded into a summary by a model call, and the turn starts from the summary and the
latest messages. The summary is stored in `evokai_conversation_summaries` (deleted with the conversation, exported with
a person's data), never at the provider; each summary call is usage of kind `summary`, counted against the caps (it
is skipped when a cap is reached). A request a provider refuses as too long gets one compaction and one retry, when
nothing reached the person yet. With external storage nothing is compacted: the history is windowed as before.
A [rewrite of the last question](architecture.md#rewriting-the-last-question) drops a summary that covers any of the
replaced messages, so no summary keeps them; the next compaction writes it again.
Context windows come from a vendored [models.dev](https://models.dev) snapshot (`src/evokai/llm/context_windows.json`,
refreshed with `scripts/models-dev-sync.py` and committed); prices stay hand-maintained in `evokai.llm.prices`.

