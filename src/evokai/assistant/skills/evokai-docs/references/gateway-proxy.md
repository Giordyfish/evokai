# The gateway proxy

The [gateway's](gateway.md) base URL door works for tools that can be pointed at another address (Claude Code, Codex).
Claude Desktop (Chat, Cowork, its Code tab), ChatGPT and GitHub Copilot cannot, and Codex signed in with ChatGPT is
simpler to leave as it is. The **gateway proxy** applies the same gateway rules to those apps:
it is a TLS-inspecting forward proxy that PCs reach through a PAC file, limited to the AI providers' hosts and to the
organisation's accounts.

!!! warning "Preview, not yet for every employee"
    The proxy reads every request an app sends to the AI providers it serves (it stores none of it). Before turning it
    on for employees the organisation needs its own agreements and notices: see the
    [legal checklist](#legal-checklist-italy). The format watch comes in a later release; the ChatGPT and GitHub
    Copilot adapters are best effort, not measured.

This page is for the administrators who run the proxy and decide its rules. Setting up the PCs (Intune, macOS, Linux,
each tool, the providers' IP allowlists) is on [Gateway proxy: setting up the PCs](gateway-proxy-setup.md); the
console's **Gateways → Connect → Company network** shows the same steps with this deployment's values.

## The whole picture

```
                       company network or the internet                                 the providers
 PC (managed by Intune or another MDM)
 ├─ root certificate of the proxy, trusted         ┌───────────── Evokai ─────────────┐
 ├─ PAC: AI hosts -> proxy, the rest direct ──────>│ API      /proxy.pac, /proxy/ca.crt│
 │                                                 │          console, gateways, people│
 ├─ Claude Desktop, ChatGPT, Copilot ── CONNECT ──>│ evokai proxy  (own process,       │── fixed egress IP ──> claude.ai
 ├─ Claude Code, Codex (HTTPS_PROXY) ── CONNECT ──>│   same image, same database)      │                       api.anthropic.com
 └─ https://evokai.check (answered by the proxy)   │   reads, checks, records, refuses │                       chatgpt.com ...
                                                   └───────────────────────────────────┘
                                         the providers' IP allowlists accept the organisation's
                                         accounts only from the egress IP: no way around the proxy
```

- **The PC** trusts the proxy's root and sends only the AI providers' hosts to the proxy (the PAC). Nothing else of
  its traffic passes through Evokai.
- **The proxy** opens TLS for those hosts, learns which company account sends each request, applies the policy of
  that person's gateway, records usage and security events without content, and passes the answer on as it streams.
- **The API** serves the PAC and the root certificate, and the console where people, gateways, the proxy's settings
  and its status are managed.
- **The providers** accept the organisation's accounts only from the proxy's egress address, where they offer an IP
  allowlist. That is what makes the proxy the only way in, also for a laptop off the company network.

## How it works

```
PC (Claude Desktop, Claude Code)                     evokai proxy                       provider
  PAC: AI hosts -> proxy ---- CONNECT claude.ai:443 ---> refuses any host outside the
                                                         catalogue (403)
                         <--- TLS with a leaf minted --- intermediate CA with Name
                              for claude.ai                Constraints
  POST .../completion  -------------------------------> adapter reads the new text,
                                                         gateway checks, verdict --------> forwarded (or rewritten)
                       <----------------- streamed as it arrives <------------------------ answer
```

- **Own process, same image.** `evokai proxy` runs from the image of the API, on the same database. The network engine
  is [mitmproxy](https://mitmproxy.org) (MIT) used as a library behind one module (`evokai.gateway.proxy.engine`),
  pinned in the `evokai[proxy]` extra that the published images include.
- **Only the catalogue.** A `CONNECT` to any host outside the catalogue gets 403: the proxy is never an open proxy.
  Today's catalogue for Anthropic: `api.anthropic.com`, `claude.ai`, `*.claudeusercontent.com`, `claude.com`, and
  `platform.claude.com` for identity only (Claude Code's OAuth token answer is read for its account; nothing else
  there is read or masked, and no token is logged or kept). OpenAI: `chatgpt.com`, `api.openai.com`. GitHub
  Copilot: `api.githubcopilot.com`, `api.individual.githubcopilot.com`, `api.business.githubcopilot.com`,
  `api.enterprise.githubcopilot.com`, `copilot-proxy.githubusercontent.com`, and `api.github.com` for identity only
  (`GET /user`, `/copilot_internal/user` and `/copilot_internal/v2/token` are read for the account; everything else
  there passes unread and unmasked). A provider is intercepted when it is enabled on an active gateway (`anthropic`,
  `openai`, `github`) and this build has adapters for it; any other host is refused, never tunnelled unread. Static
  assets and updates (`downloads.claude.ai`, `s-cdn.anthropic.com`,
  `assets-proxy.anthropic.com`) pass without opening TLS when a PC sends them.
- **Streaming.** Answers are passed on as they arrive, never buffered (Cowork's event stream never ends). Request bodies
  are read whole only on the paths an adapter inspects, up to `EVOKAI_PROXY_MAX_BODY_MB` (default 32); above that the
  request is refused with a readable message (fail closed).
- **Upstream TLS** is verified against the public roots.

### Certificates

`evokai proxy init-ca` creates a root (ECDSA P-256, ten years) and an intermediate (three years) with **X.509 Name
Constraints**: the intermediate may sign only for `api.anthropic.com`, `claude.ai`, `claudeusercontent.com`,
`claude.com`, `chatgpt.com`, `api.openai.com`, `githubcopilot.com`, `copilot-proxy.githubusercontent.com`,
`api.github.com` and the check host `evokai.check`, and for no IP address. A stolen intermediate key cannot impersonate
any other site: TLS clients reject a certificate for `example.com` under it (a test checks this with
`openssl verify`). The proxy refuses to start with an intermediate that has no Name Constraints.

```
evokai proxy init-ca --out ./proxy-ca --org "Example Org"
```

| File | What to do with it |
|---|---|
| `root.crt` | Distribute to the PCs (Intune trusted certificate profile, macOS configuration profile, `NODE_EXTRA_CA_CERTS` for Claude Code). The API serves it at `/proxy/ca.crt`. |
| `intermediate.crt` | The intermediate and the root: `EVOKAI_PROXY_CA_CERT` (proxy and API). |
| `intermediate.key` | `EVOKAI_PROXY_CA_KEY`, for the proxy process only: store it as a secret (Azure Key Vault) and delete the file. |
| `root.key` | Needed only to issue a new intermediate. Keep it offline. |

Leaf certificates are minted per host and kept in memory; a leaf names only the host the PC asked for.

### Who is asking (passive identity)

The proxy never calls a provider with a person's credentials. It reads a key from each request and binds it to the
account that a provider's own answer named, when that answer passes through:

| Surface | Key in each request |
|---|---|
| claude.ai (Chat, Cowork, titles) | the `sessionKey` cookie, read from every `cookie` header (HTTP/2 splits them) |
| Claude Code (Desktop's Code tab, the CLI) | the Anthropic account uuid in `metadata.user_id` of every `/v1/messages` body |
| Cowork's bridge (files read on the PC) | none of its own: the identity seen last from the same PC address (best effort behind a shared NAT) |
| Codex, ChatGPT (`chatgpt.com`, `api.openai.com`) | the ChatGPT login's JWT in `Authorization`: its claims name the user, their e-mail and the ChatGPT account (`chatgpt_account_id`) |
| GitHub Copilot (best effort) | the Copilot token's `tid` in `Authorization` |

The person is bound to the **Anthropic account uuid**, learned from any of these answers, each naming the account
with its e-mail:

- `GET claude.ai/api/account` (Desktop calls it at start; it also binds the session cookie);
- `GET api.anthropic.com/api/oauth/profile` (Claude Code after signing in);
- `POST platform.claude.com/v1/oauth/token` (Claude Code's token refresh; only its `account` and `organization`
  are read).

They share one value space: claude.ai's account uuid, the profile's and the token answer's are what Claude Code
sends as `account_uuid` in `metadata.user_id` (read in the Claude Code 2.1.332 bundle). So a person seen once
through Desktop or claude.ai is recognised on the CLI too, and a signed-in CLI, which does not call the profile
again, is recognised at its next token refresh (or after signing out and in).

**Codex and ChatGPT** need no answer: every request carries the ChatGPT login's JWT, whose claims are read (never
verified, and trusted for nothing but attribution: a forged token is refused upstream anyway) and bound the first time
the user is seen. An OpenAI API key names nobody: such requests are unidentified.

**GitHub Copilot** (best effort, not measured): the person's GitHub token is bound to the account by `GET
api.github.com/user` or `/copilot_internal/user` (login, the e-mail when it is public, the organisations), and the
Copilot token that `GET /copilot_internal/v2/token` mints from it inherits that binding through its `tid`. A GitHub
account without a public e-mail is found by a gateway person whose label is the GitHub login.

Only a digest of each key is stored (`evokai_gateway_proxy_identities`) with the e-mail, account and organisation,
and an expiry 30 days after its last use. The e-mail maps to a gateway person: give each person their work e-mail
(`POST /v1/admin/gateways/{id}/people` with `{label, email}`; one live key per e-mail across every gateway). A person
whose label is their e-mail is found too. A person just added is recognised within 30 seconds.

- **Organisation.** A claude.ai request states its organisation (`x-organization-uuid`); an Anthropic API request is
  of the organisation its account's profile named; a Codex or ChatGPT request, of the ChatGPT account its JWT names; a
  Copilot request, of the GitHub organisations `/copilot_internal/user` listed (one allowed is enough). When
  `allowed_orgs` lists organisations for the provider, a request of any other one is refused ("account outside the
  company") whatever the rest, with a `proxy.org_refused` event.
- **Unidentified.** No binding yet, or an e-mail in no gateway: `unidentified = block` (the default) refuses with a
  message to restart the app or contact IT (`proxy.unidentified` event); `record` lets the request through on the
  **default gateway** (`EVOKAI_PROXY_DEFAULT_GATEWAY`, a slug or an id), recorded as `unidentified <e-mail>`: that
  gateway's rules, records and retention apply. **Without a default gateway, `record` blocks.**
- **Events need a gateway.** A proxy event (`proxy.format_unknown`, `proxy.org_refused`, `proxy.unidentified`) goes to
  the person's gateway, else the default gateway; without either it is only logged.

These settings come from the environment (`EVOKAI_PROXY_ALLOWED_ORGS`, `EVOKAI_PROXY_UNIDENTIFIED`,
`EVOKAI_PROXY_DEFAULT_GATEWAY`), and the console setting `proxy` overrides them. A deployment owner sets it on the
console's **Gateway proxy** page (`PUT /v1/admin/proxy/settings` with `{"allowed_orgs": {"anthropic": ["<org
uuid>"]}, "unidentified": "block", "default_gateway": "<slug or id>"}`; `DELETE` goes back to the environment). Each
change writes an audit row (`proxy.settings`, `proxy.settings_reset`); the proxy reads it within 30 seconds. A
default gateway left empty in the console means none, even when `EVOKAI_PROXY_DEFAULT_GATEWAY` is set.

### Identity and its limits

What the proxy knows about a request is what the provider's own answers told it about the account. That is reliable
for attribution, and it has limits an administrator should know:

- **An account, not a human.** The proxy names the company account signed in to the app. Someone using a
  colleague's unlocked PC, or a shared account, is recorded as that account.
- **Learned, not asked.** A binding exists only after an app made one of the identity calls through the proxy
  (Desktop at start, Claude Code at sign in or token refresh). An app already running when the PC was put behind
  the proxy is unidentified until it is restarted; with `unidentified = block` its people read a message that says
  so.
- **The e-mail must match.** A person is found by the work e-mail given on the gateway's People page (or a label
  that is that e-mail; for GitHub Copilot, a label that is the GitHub login when the account's e-mail is not public).
  An account whose e-mail is in no gateway is unidentified, and so is an OpenAI API key (it names nobody).
- **Organisations, where the provider states them.** claude.ai requests carry their organisation, Claude Code's
  comes from its account's profile, a ChatGPT login's token carries its account id (`chatgpt_account_id`), and
  GitHub's identity answers list the account's organisations. With `allowed_orgs` empty for a provider, personal
  accounts of that provider are not refused by the organisation check; the providers' IP allowlists do not stop them
  either (they apply to the organisation's accounts only). Set `allowed_orgs` for every provider in use.
- **The bridge is best effort.** Cowork's file reads carry no key of their own: they get the identity seen last from
  the same PC address, which behind a shared address can be another person's.
- **PCs are counted by address.** The status page's "PCs seen" counts network addresses with a recognised person;
  PCs behind one company address count once.

### In the console

- **Gateways → People**: each person's work e-mail, set when the person is added and editable later (one live key
  per e-mail across every gateway; `PATCH /v1/admin/gateways/{id}/people/{key_id}` with `{label, email}`, audited as
  `gateway.key_update`).
- **Gateways → Connect → Company network**: the PAC address, the root certificate (PEM and DER), the proxy's address,
  the apps served now, the setup steps per system and per tool with this deployment's values and copy buttons, a
  ready macOS configuration profile, the providers' IP allowlists with the egress address, the PAC fallback and the
  check page. The values come from `GET /v1/admin/proxy/setup` (any console login that sees a gateway; nothing in it
  is secret).
- **Gateway proxy** (deployment owners, and single sign-on operators of every gateway, `*`): whether the proxy runs,
  the last 24 hours (PCs, people, accounts, model requests per surface, unidentified requests), format alerts
  (`proxy.format_unknown`) and refused organisations (`proxy.org_refused`) of the last 7 days, and the settings
  above (`GET /v1/admin/proxy`). Each running proxy instance reports every minute in the console settings table
  (`proxy.status:<host name>`); an instance silent for three minutes shows as not running.
- **Gateways → Notice**: once the proxy is configured, the staff notice template says that the proxy reads all the
  PC's traffic to the AI providers and stores none of its content.

### Adapters and refusals

Each surface has an adapter. It finds the text that is new in a request (the newest user turn and the tool results
since the previous assistant message, as the base URL door does), the model, and how to refuse where the person will
read it. The checks are the gateway's own: provider enabled, model allowed, data protection, rate and token caps,
`input.size`, `input.secrets`, `pii.model`, `scope` (RE2 only). In `enforce` a check refuses; in `monitor` it records.
The person's own e-mail and name are never a `pii.model` finding in their own requests (Claude Code puts the
person's e-mail in its context); anyone else's still are.

| Adapter | Matches | Refusal | Status |
|---|---|---|---|
| `anthropic.messages` | `POST api.anthropic.com/v1/messages` | a synthetic assistant answer in the public format (SSE when `stream`, JSON otherwise) | verified with the Claude Code CLI; measured in Desktop's Code tab |
| `anthropic.count_tokens` | `POST .../v1/messages/count_tokens` | `{"input_tokens": <estimate>}`, not forwarded | tested with fixtures |
| `claude.chat` | `POST claude.ai/api/organizations/{org}/chat_conversations/{id}/completion` (gzip JSON) | the prompt is rewritten to make the real model repeat the refusal; attachments and files are dropped | tested with fixtures; to verify with Desktop |
| `claude.cowork` | `POST claude.ai/v1/code/sessions/{id}/events` | each user event that breaks the policy becomes a visible marker and the instruction | tested with fixtures; to verify with Desktop |
| `claude.bridge` | WebSocket `bridge.claudeusercontent.com`, the app's JSON-RPC results | the result is replaced by a notice the agent relays | tested with fixtures; to verify with Desktop |
| `openai.codex_ws` | WebSocket `chatgpt.com/backend-api/codex/responses` (and `api.openai.com/v1/responses`), each `response.create` from Codex; the warm-up (`generate: false`, no input) is not a model request | the person's input is rewritten to make the real model repeat the refusal, and an offending tool output becomes a notice carrying the same instruction; refusals before the checks (format, account, organisation) drop the message and answer Codex with a synthetic event sequence in the public Responses streaming format | verified with the Codex CLI 0.167.2 |
| `openai.responses_http` | `POST chatgpt.com/backend-api/codex/responses`, `POST api.openai.com/v1/responses` (Codex without WebSocket) | a synthetic answer in the public Responses format (SSE when `stream`, JSON otherwise) | verified with the Codex CLI 0.167.2 |
| `openai.chatgpt` | `POST chatgpt.com/backend-api/conversation` (ChatGPT on the web and the desktop app) | the newest user message's parts are rewritten to the instruction; its attachments are dropped | best effort, not measured |
| `github.copilot_chat` | `POST *.githubcopilot.com/chat/completions`, and `/responses`, `/v1/messages` | a synthetic answer in the request's format: OpenAI chat completion chunks (JSON when not streaming), Responses events, Anthropic Messages events | best effort, not measured |
| `github.copilot_completions` | `POST .../v1/engines/{engine}/completions` or `.../completions` on the Copilot hosts (inline suggestions): `prompt` and `suffix` | an empty completion (no suggestion) and a security event, one per file every few minutes; not a usage row and not rate limited | best effort, not measured |
| `mask` | any other `POST`, `PUT`, `PATCH` on an intercepted host (titles carry the text) | matched values become `[bloccato]` (secrets, and personal data when `pii.model` is enforced); sign-in paths and fields holding credentials are left alone | tested with fixtures |

**Why Codex is rewritten.** The real model answers the instruction, so Codex shows the refusal as its own reply and
the conversation goes on server side (`previous_response_id`) without the blocked text: the server keeps the rewritten
input, never the original. It was verified with the Codex CLI (a prompt, and a file read by a tool), as was the
synthetic answer for refusals that must not reach the server at all.

**Fail closed.** A request on a known path whose body does not parse, or lacks the field the adapter expects, is
refused ("format not recognised by the company policy") and writes a `proxy.format_unknown` event. A failure of the
checks themselves (a database outage) refuses too.

The refusal speaks the gateway's language (English or Italian, from its locale) and never repeats the value that
matched: "⛔ Richiesta bloccata dalla policy aziendale: il contenuto include un segreto o una credenziale. Toglila e
riprova. Per dubbi: l'IT." A gateway may set its own `contact` and replace any text (`proxy_messages`) in its settings.

### Records

One usage row per model request, as the base URL door, with its **surface** (`desktop_chat`, `desktop_cowork`,
`desktop_code`, `claude_code`, `web_chat`, `codex`, `chatgpt`, `copilot`): tokens when the answer carries them
(Anthropic's `message_start` and `message_delta`, the Responses `response.completed`, the chat completions `usage`;
none for claude.ai Chat, Cowork and ChatGPT, recorded without tokens), timings and outcome. A Codex request on the
WebSocket is recorded when the server's `response.completed` passes (status 101, the upgrade's). Security events
as the door, never content. Records are written off the request's path.

## PAC, root certificate and the check page

- `GET {base}/proxy.pac` (public, on the API): the catalogue's hosts of the served providers and `evokai.check` go to
  `EVOKAI_PROXY_PUBLIC_ADDRESS`, everything else direct. `EVOKAI_PROXY_PAC_FALLBACK=block` (default) names only the
  proxy; `direct` adds `DIRECT` for when the proxy cannot be reached. 404 until the address is set.
- `GET {base}/proxy/ca.crt` (`?format=der` for DER): the root, read from `EVOKAI_PROXY_CA_CERT`. The API never reads
  the CA's key.
- `https://evokai.check` (through the proxy): the proxy answers it itself: whether the PC goes through the proxy, whether
  its certificate is trusted, and the identities seen lately from that PC (e-mail masked) with their gateway.

**Which fallback.** With `block` an unreachable proxy stops the AI apps on every PC: nothing goes around the policy,
and people notice at once. With `direct` the apps keep working and go straight to the providers, without checks,
records or the organisation check, until the proxy is back. Choose `direct` only together with the providers' IP
allowlists (which refuse those direct requests anyway, so the choice then only changes the error people see) or
when availability matters more than the policy for a while. `block` is the default.

## Run it locally

```bash
uv sync --extra proxy                     # mitmproxy (the images already have it)
evokai proxy init-ca --out /tmp/proxy-ca
export EVOKAI_PROXY=on \
  EVOKAI_PROXY_CA_KEY=/tmp/proxy-ca/intermediate.key \
  EVOKAI_PROXY_CA_CERT=/tmp/proxy-ca/intermediate.crt \
  EVOKAI_PROXY_PORT=3128
evokai proxy                              # same EVOKAI_DATABASE_URL as the API; it does not migrate
```

`evokai proxy` refuses to start, with the reason, unless `EVOKAI_PROXY=on`, the gateway module is on and a constrained
CA is configured. Then, with a gateway that enables Anthropic and a person whose e-mail is the account's:

```bash
HTTPS_PROXY=http://127.0.0.1:3128 NODE_EXTRA_CA_CERTS=/tmp/proxy-ca/root.crt claude -p "reply only: ok"
```

Nothing on the PC changes: no system trust store, no system proxy. For Claude Desktop the organisation sets the PAC
(`egressProxyPacUrl` or the system proxy) and the root through its device management.

Codex (with a gateway that enables OpenAI) takes the root from `CODEX_CA_CERTIFICATE` (else `SSL_CERT_FILE`):

```bash
HTTPS_PROXY=http://127.0.0.1:3128 CODEX_CA_CERTIFICATE=/tmp/proxy-ca/root.crt codex exec "reply only: ok"
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `EVOKAI_PROXY` | `off` | `on` lets `evokai proxy` start |
| `EVOKAI_PROXY_PORT` | `3128` | the explicit proxy's port |
| `EVOKAI_PROXY_LISTEN_HOST` | `0.0.0.0` | the address it binds |
| `EVOKAI_PROXY_CA_KEY`, `EVOKAI_PROXY_CA_CERT` | | the intermediate's key, and its certificate followed by the root (PEM or file path) |
| `EVOKAI_PROXY_MAX_BODY_MB` | `32` | the largest body read on an inspected path |
| `EVOKAI_PROXY_PUBLIC_ADDRESS` | | `host:port` in the PAC |
| `EVOKAI_PROXY_PAC_FALLBACK` | `block` | `block` or `direct` |
| `EVOKAI_PROXY_EGRESS_IPS` | | the proxy's public egress addresses, comma separated: shown in the console for the providers' IP allowlists (informational) |
| `EVOKAI_PROXY_UNIDENTIFIED` | `block` | `block` or `record` (`record` needs a default gateway, else it blocks) |
| `EVOKAI_PROXY_DEFAULT_GATEWAY` | | the gateway (slug or id) of unidentified requests in `record` |
| `EVOKAI_PROXY_ALLOWED_ORGS` | `{}` | JSON, per provider: `{"anthropic": ["<org uuid>"], "openai": ["<ChatGPT account id>"], "github": ["<organisation login>"]}` |
| `EVOKAI_PROXY_CONTACT` | IT | who people should ask, in refusals |
| `EVOKAI_PROXY_LOCALE` | `en` | the language of refusals tied to no gateway |

## Sizing

Measured on 2026-11-28 on a developer workstation, the proxy pinned to **one core**, a local fake upstream on TLS,
SQLite, 300 people with their bindings, Claude Code-like requests of about 100 KB (history, system prompt, a 12 KB new
turn), `input.secrets` enforced and `pii.model` in monitor:

| Load | Added time to first byte | Proxy CPU | Peak memory |
|---|---|---|---|
| 300 concurrent streams of 20 s, starts spread over 20 s, 0.5 to 2 s between requests (1,200 requests) | p50 3.7 ms, p99 10.4 ms on an open connection; p50 5.5 ms more for a new connection (CONNECT and two TLS handshakes) | 12% of one core | 670 MB |
| 300 clients starting at the same instant (a burst of 300 new connections) | p50 460 ms, p99 1.0 s on the burst; p50 69 ms, p99 267 ms on the next synchronised waves | | 750 MB |
| 300 req/s sustained (300 clients sending a new 100 KB request every second) | the one core saturates: p99 1.9 s | 79% | 1.3 GB |

The target (300 concurrent streams on 2 vCPU / 4 GB, p99 added before the first byte under 50 ms) holds for realistic
arrivals on this machine; a cloud vCPU is slower. One proxy process uses one core (about 3 ms of CPU per 100 KB
request); a second replica, or a second process behind the same address, doubles it. Synchronised bursts of new
connections (every PC reconnecting at once after an outage) take seconds to absorb. Plan B, if production numbers
miss: an engine in Go or Rust behind the same adapter interface.

## What is verified and what is best effort

- Verified end to end with the Claude Code CLI 2.1.332 through a local `evokai proxy` and the real Anthropic API: a
  normal request forwarded with its usage recorded (tokens from the stream), a request with a test key refused with the
  synthetic answer, the person found from the account uuid in `metadata.user_id`. The binding itself was seeded as an
  identity answer would have made it (the signed-in CLI called none during the check); learning from `/api/account`,
  `/api/oauth/profile` and the token answer is tested with fixtures, the token answer also end to end.
- Tested with sanitised fixtures and mitmproxy end to end against a local fake upstream: every adapter above,
  HTTP/2 with split cookies, gzip bodies re-encoded as they came, WebSocket replacement, a never-ending stream passed
  without buffering, the body limit, the catalogue refusal, the PAC, the check host and the CA constraints.
- Verified end to end with the Codex CLI 0.167.2 (ChatGPT login) through a local `evokai proxy` and the real
  ChatGPT backend: on the WebSocket, a normal request forwarded and recorded with its tokens from `response.completed`,
  a prompt with a test key and a tool output with a test key each rewritten (Codex showed the refusal as its reply),
  and an unidentified account answered with the synthetic event sequence without reaching the server; over HTTP (a
  provider with `supports_websockets = false`), a normal request recorded with its tokens and a test key refused with
  the synthetic Responses answer. The person was found from the JWT's e-mail.
- To verify with the owner on Claude Desktop: Chat, Cowork and the bridge, and the `GET /api/account` key names.
- Best effort, not measured (built from the public formats, tested with fixtures): ChatGPT on the web and the desktop
  app, and GitHub Copilot (chat, inline suggestions, the `api.github.com` identity answers and their key names).
- Best effort, built from public documentation and not checked on a managed PC: the Intune paths, the macOS
  configuration profile (its `com.apple.proxy.http.global` payload with `ProxyType Auto`), Claude Desktop's managed
  keys `egressProxyPacUrl` and `egressProxyUrl`, Firefox's enterprise policy and the providers' IP allowlists. Try
  them on one test PC and one test account before a rollout.
- Not in this release: the format watch, claude.ai uploads.

## Legal checklist (Italy)

The proxy reads the content of every request a company PC sends to the AI providers it serves. That is a check on
the use of a work tool and a processing of employees' personal data. Before turning it on for employees, the
organisation (DPO, legal, HR) needs at least the following. This list is not legal advice.

- **Art. 4 of the Statuto dei lavoratori (Law 300/1970).** A tool from which remote monitoring of workers' activity
  can also follow needs an agreement with the workers' representatives (RSU or RSA) or, failing that, the
  authorisation of the Ispettorato del lavoro (art. 4, para. 1). Whether the AI apps count as "tools used to perform
  the work" (para. 2, no agreement needed) is the organisation's assessment: the proxy reads more than the work
  requires to be done, so assume para. 1 unless counsel says otherwise. Under para. 3 the information can be used for
  employment purposes only after adequate notice of how the tools are used and how checks are made.
- **Notice under art. 13 GDPR.** Every person whose PC goes through the proxy receives a notice before it does:
  the console's **Gateways → Notice** is a template with this deployment's values, including the paragraph on the
  proxy; complete the parts in square brackets (purpose, legal basis, controller, contacts).
- **DPIA under art. 35 GDPR.** Systematic monitoring of employees with a new technology is on the Garante's list of
  processing that needs a data protection impact assessment. Document the purpose, why less intrusive means (the
  base URL door, the providers' own enterprise controls) are not enough, the controls on the data, and the residual
  risk.
- **Retention.** Usage rows and security events hold no content and follow the retention of the gateway they belong
  to (its event retention, on the gateway's Events and Notice pages). Identity bindings (a digest of the session key,
  the account's e-mail, account and organisation ids) expire 30 days after their last use. The audit log keeps its
  own retention. State all of them in the notice and the record of processing (art. 30).
- **Purpose limitation and access.** Decide who may read the gateway's usage and events (gateway roles in the
  console), and write down that they are used for security and licence management, not to evaluate people, unless the
  agreement and the notice say otherwise.
- **The CA key.** Whoever holds the intermediate key can read the AI traffic of every PC that trusts the root. Keep it
  in a key vault readable only by the proxy's identity, and the root key offline.
