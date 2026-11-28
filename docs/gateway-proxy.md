# The gateway proxy

The [gateway's](gateway.md) base URL door works for tools that can be pointed at another address (Claude Code, Codex).
Claude Desktop (Chat, Cowork, its Code tab), ChatGPT and GitHub Copilot cannot, and Codex signed in with ChatGPT is
simpler to leave as it is. The **gateway proxy** applies the same gateway rules to those apps:
it is a TLS-inspecting forward proxy that PCs reach through a PAC file, limited to the AI providers' hosts and to the
organisation's accounts.

!!! warning "Preview, not yet for every employee"
    The proxy reads every request an app sends to the AI providers it serves (it stores none of it). Before turning it
    on for employees the organisation needs its own agreements and notices (in Italy: art. 4 of the Statuto dei
    lavoratori, art. 13 GDPR notice, a DPIA). The console pages and the format watch come in later releases; the
    ChatGPT and GitHub Copilot adapters are best effort, not measured.

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
`EVOKAI_PROXY_DEFAULT_GATEWAY`), and the console setting `proxy` (`{"allowed_orgs": {...}, "unidentified": "block",
"default_gateway": "<slug or id>"}` in the console settings table) overrides them.

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
- Not in this release: the console pages, the format watch, claude.ai uploads.
