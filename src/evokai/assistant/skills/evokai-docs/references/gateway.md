# Gateways for coding tools

A **gateway** lets the people of an organisation use Claude Code and Codex with their own company seats (Claude
Team or Enterprise, ChatGPT Business or Enterprise) through Evokai, under the organisation's controls. Evokai forwards each tool's own API to the provider
unchanged: the person's login, every header and the body go through as they came, and the answer streams back byte for
byte. Before forwarding, the gateway checks who is asking and what they send; it records one row per request (tokens,
timings, outcome), never the content.

A gateway is not an application. It has people, a policy, usage and events, and nothing else: no agents, knowledge,
workflows or channels. It is never listed among applications, and no application key or session token reaches it.
Each group of people ("Development", "Consultants") is one gateway with its own people and policy; a person belongs to
one gateway.

## Set it up

1. A deployment owner (the operator key, or a console owner of every application) creates the gateway: the console's
   **Gateways** page, or `POST /v1/admin/gateways` with `{slug, name, providers}` (`providers`: `anthropic` for
   Claude Code, `openai` for Codex). Slugs are shared with applications.
2. An operator gives each person their **personal gateway key** on the gateway's **People** page (or
   `POST /v1/admin/gateways/{id}/people` with `{label}`, the person's name or e-mail). The key (`evk_gw_...`) is shown
   once; only its hash is stored. Revoking it (`DELETE .../people/{key_id}`) refuses the person's requests at once.
3. Each person points their tool at this deployment and sends their key. The **Connect** page has the snippets with this
   deployment's address:

   - the organisation's **managed settings** (claude.ai admin settings, or the managed settings file) set
     `ANTHROPIC_BASE_URL` to `https://<evokai>/<base>/gw/anthropic`, so every seat goes through the gateway;
   - each person's `~/.claude/settings.json` adds their key:

     ```json
     {
       "env": {
         "ANTHROPIC_BASE_URL": "https://evokai.example.com/agent/gw/anthropic",
         "ANTHROPIC_CUSTOM_HEADERS": "x-evokai-key: evk_gw_..."
       }
     }
     ```

   The person stays logged in to Claude Code with their claude.ai account: the gateway forwards their login, it has
   no provider key of its own.

   For Codex, each person adds a model provider to `~/.codex/config.toml` that keeps their ChatGPT login
   (`requires_openai_auth`) and sends their key from an environment variable (`export EVOKAI_GATEWAY_KEY=evk_gw_...`):

   ```toml
   model_provider = "evokai"

   [model_providers.evokai]
   name = "Evokai"
   base_url = "https://evokai.example.com/agent/gw/openai/v1"
   wire_api = "responses"
   requires_openai_auth = true
   env_http_headers = { "x-evokai-key" = "EVOKAI_GATEWAY_KEY" }
   ```

The addresses are the same for everyone: the key alone says who is asking and through which gateway.

## The door

| Route | Forwards to | Tool |
| --- | --- | --- |
| `POST {base}/gw/anthropic/v1/messages` (Claude Code adds `?beta=true`) | `https://api.anthropic.com/v1/messages` | Claude Code |
| `POST {base}/gw/anthropic/v1/messages/count_tokens` | `https://api.anthropic.com/v1/messages/count_tokens` | Claude Code (`/context`) |
| `POST {base}/gw/openai/v1/responses` with a ChatGPT login | `https://chatgpt.com/backend-api/codex/responses` | Codex |
| `POST {base}/gw/openai/v1/responses` with an API key (`sk-...`) | `https://api.openai.com/v1/responses` | Codex |
| `GET {base}/gw/openai/v1/models` | the same upstream's `/models` | Codex (its model list) |

Codex's upstream is chosen from the person's token, which the gateway looks at and never verifies, logs or keeps: a
ChatGPT login's token is a JWT whose claims hold `https://api.openai.com/auth` (with `chatgpt_account_id`), and goes
to ChatGPT's Codex backend with the headers Codex sends (`chatgpt-account-id`, `originator`, `session-id`...); an API
key goes to the OpenAI API; any other token is refused (401). The body is forwarded as Codex sends it, never
translated. The model list is forwarded for a live key of a gateway with OpenAI enabled, and recorded nowhere.

`EVOKAI_GATEWAY_ANTHROPIC_URL`, `EVOKAI_GATEWAY_CHATGPT_URL` and `EVOKAI_GATEWAY_OPENAI_URL` change the upstreams (a
test double, a proxy); `EVOKAI_GATEWAY_TIMEOUT_SECONDS` (900) is
the longest the gateway waits for the provider's next bytes.

What goes up: the body bytes as they came, the query, and every header except the hop-by-hop ones (`connection`,
`keep-alive`, `transfer-encoding`, `te`, `trailer`, `upgrade`, `proxy-*`), `host` and `content-length` (recomputed
for the same bytes) and `x-evokai-key`. `authorization` (the person's login), every `anthropic-*` header
(`anthropic-beta` carries the login's capability: stripping it fails the request) and `x-claude-code-*` go through
unchanged, as the [Claude Code gateway protocol](https://code.claude.com/docs/en/llm-gateway-protocol.md) requires.

What comes down: the provider's status, headers (`x-should-retry`, `retry-after`, `anthropic-ratelimit-unified-*`,
`request-id`, `content-type`, `content-encoding`...) and body, the stream relayed chunk by chunk as it arrives, its
`ping` events included. Error bodies are the provider's own, so the tool's recovery (which matches the wording) works.
Only the hop-by-hop headers, `date` and `server` (this server sets its own) are left out. If the tool goes away, the
request to the provider is cancelled at once; if the provider's stream breaks, the tool's connection ends without a
clean end, so the tool knows the answer is incomplete.

## Controls

In order, before anything is forwarded; the first that refuses ends them.

| Check | Refusal |
| --- | --- |
| A personal key, not revoked, of an active gateway | 401 `authentication_error` |
| The provider is enabled on the gateway | 400 (control `gateway.provider`) |
| The model is allowed (the gateway's allowed models, globs such as `claude-sonnet-*`; none set: every model) | 400 (`gateway.model`) |
| Data protection: the gateway's profile and the system policy's both let the provider have the data | 400 (`gateway.protection`, with the reasons) |
| The person's requests per minute (`abuse.requests_per_minute` of the policy, control `abuse.rate`) | 429 with `retry-after` |
| The person's token caps on this gateway, per day and per month (UTC), when set | 429 (`gateway.cap`) |
| The guard's input checks on the new text: `input.size`, `input.secrets`, `pii.model`, `scope` | 400 (the control) |

A token count (`count_tokens`) passes the key, provider, model and data protection checks only: it is not a model call,
and its content is checked when it is sent for real.

A refusal is in the provider's error shape (Anthropic: `{"type": "error", "error": {"type": "invalid_request_error",
"message": ...}}` with `x-should-retry: false`; OpenAI: `{"error": {"message", "type", "code", "param"}}`), with a
message the person reads in their tool, for example:

```text
API Error: 400 Evokai gateway: this request was not sent because it contains what looks like a secret or credential
(aws_access_key; control input.secrets). Remove it from the prompt, or keep the file that holds it out of what the
tool reads, and try again.
```

The message names the kind of finding, never the text. Claude Code resends a refused 400 a few times with some
features removed (its recovery for capabilities a gateway rejects); each try is refused the same way and recorded as a
request, and the security event is written once.

### Which text is checked

Only the **new text** of a request: the newest user turn's text and the tool results since the previous assistant
message (what the person or their files add now). For Codex: the `user` messages and the tool outputs
(`function_call_output`, `custom_tool_call_output`, `local_shell_call_output`) after the model's last item; its
`instructions` and `developer` messages are the tool's own. The conversation's history was checked when it was new and is not
checked again; the tool's own system prompt (and its mid-conversation reminders) is not the person's and is not checked.
A secret pasted early in a conversation is refused on that request; once refused it never reaches the history.

### The guard's controls on a gateway

A gateway has a policy like an application's: `security` (modes and settings) and `data_protection`, with the same
[system policy](security.md#the-system-policy) floors and defaults, [versions](security.md#policy-versions),
[tests](security.md#policy-tests) and copy. The console's **Policy** page lists the controls that act on a gateway:

- `input.size`: the new text is longer than `input.max_chars`;
- `input.secrets`: API keys, tokens, private keys, passwords in URLs and the deployment's own keys (the detectors of
  `output.secrets`);
- `pii.model`: the guard's personal data types (`pii.types`: codice fiscale, partita IVA, IBAN, e-mail, phone,
  card), with their checksums (the first 20 of each detector: the gateway needs which kinds a text holds);
- `scope`: a topic of `scope.deny` (its `refusal`, when set, is the message);
- `abuse.rate`: requests per person per minute.

In `monitor` a check records a security event and the request goes on; in `enforce` it refuses the request. A gateway
never masks or changes a request: changing it would break the provider's prompt cache and the tool's own bookkeeping.
The other controls do nothing on a gateway: the injection and moderation classifiers are off (their cost on bodies of
hundreds of kilobytes), and tool controls, hidden-text cleaning and confirmations are the tool's own business.

A new gateway starts with `input.max_chars: 400000` and `abuse.requests_per_minute: 120` (a coding tool and its
subagents); every control keeps its default mode (`monitor`) until the policy or the system policy says otherwise.

Policy tests of a gateway run the gateway's checks (so a test may expect `pii.model`), offline: no provider is called
and nothing is stored.

### Linear-time matching

Every pattern run on a gateway body runs on RE2 (`google-re2`), whose time is linear in the text: Python's `re`
backtracks, and some detectors took seconds on an adversarial run of one character. The guard's lookarounds are
checked on each match instead, the next search starting where a new match could begin; a scope topic RE2 cannot run (a
lookaround, a backreference) is matched as a phrase. The whole scan takes about a second at most on 1 MB of adversarial
input (`tests/test_gateway.py`).

## Records

- **One row per request** (`evokai_gateway_requests`): the gateway, the person (key id and label), the provider, the
  model, input, output and cache tokens read from the stream (Anthropic's `message_start` and `message_delta`
  usage, OpenAI's `response.completed`), the request
  and answer sizes, the time to the provider's first byte and the duration, and the outcome: `forwarded`,
  `upstream_error` (the provider answered an error), `blocked` (with the control), `failed` (the provider could not be
  reached, or its stream broke) or `cancelled` (the tool went away). **No content, and no amount**: the traffic is the
  people's own subscriptions, and the console shows tokens only. `GET /v1/admin/gateways/{id}/usage?days=30`: per
  person, per model, by outcome, and the latest requests. Rows are deleted with the gateway's security events
  (`security_event_ttl_days`, else `EVOKAI_SECURITY_EVENT_TTL_DAYS`).
- **Security events** of the gateway's checks, without a snippet (a gateway keeps no content); the person's label is
  their subject. The console's **Events** page lists them.
- **Audit**: `gateway.create`, `gateway.update` (settings and policy), `gateway.key_create`, `gateway.key_revoke`, and
  the policy's own entries.

## The admin API

`/v1/admin/gateways`, with the operator key or a console login (an application's keys never manage gateways):

| Route | Role |
| --- | --- |
| `GET /v1/admin/gateways`: the gateways the caller can see | any |
| `POST /v1/admin/gateways`: `{slug, name, providers, allowed_models?}` | deployment owner |
| `GET /v1/admin/gateways/{id}` (id or slug) | viewer |
| `PATCH /v1/admin/gateways/{id}`: `name`, `status` (`active`, `disabled`), `providers`, `allowed_models`, `tokens_per_day`, `tokens_per_month` (0 removes a cap), `security`, `data_protection`, `note`, `force` | owner |
| `GET`/`POST /v1/admin/gateways/{id}/people`, `DELETE .../people/{key_id}` | viewer / operator |
| `GET /v1/admin/gateways/{id}/usage?days=` | viewer |

Roles on a gateway are those of an application with its slug (single sign-on roles by slug, `*` for every one). The
console edits a gateway's policy with the application routes (`/v1/admin/security`, `/v1/admin/policy/...`,
`/v1/admin/security/events`, `/v1/admin/audit`) and `X-Evokai-Application` naming the gateway: these are the only
application routes a gateway answers to (any other answers 404). A `PATCH` with `security` or `data_protection` saves a
new policy version through the same path (its tests run first).

## The notice to the people

The **Notice** page fills a template of the notice to the people who use the gateway (art. 4 of the Statuto dei
lavoratori, Law 300/1970; art. 13 GDPR) with the gateway's name, tools and retention: what is recorded and what is
not, the checks, the rights. The purpose and the legal basis are the organisation's to state, in the parts in square
brackets; the template is not legal advice.

## Sizing

Measured with a local fake provider that streams SSE with a ping a second for 40 seconds, Evokai pinned to 2 CPUs
with SQLite, 65 people: 300 concurrent streams of 515 KB requests (60 KB of new text in each, with an e-mail address
and a phone number on every line), new requests arriving at 10 per second. Once warm, the time to the first byte is
13 ms at the median and 18 to 20 ms at p99 through the gateway, against 4 and 10 ms straight to the fake provider:
about 10 ms added at both. The first run after a start is slower (p99 146 ms) while every key is verified once and
the caches fill. A burst of 300 requests at the same instant waits for the CPU (p99 2.6 s, against 0.45 s straight):
size the CPUs for the rate of new requests, not for the number of open streams. Peak resident memory was 743 MB
after the burst.
