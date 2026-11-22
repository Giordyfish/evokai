# Changelog

All notable changes to Evokai are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may break
interfaces; breaking changes are listed under **Changed** or **Removed** with what to do.

## [Unreleased]

### Added

- Prompt modules: versioned application prompts with a restricted JSON Schema of parameters and an optional data table,
  rendered as Handlebars templates (pydantic-handlebars, no HTML escaping) after an agent's instructions. Agents list
  the modules they use with defaults and whether a session may change them; the application backend switches and
  fills them in `POST /v1/session-tokens` (and server runs) with `modules`, checked against the schema, the `module`
  grants and, for free-text values, the guard's input checks. Versions are pinned per conversation and traced as
  `prompt.modules`. Declared in the application YAML (`prompts:` and the agents' `modules:`), managed in the console's
  new Prompts page (visual parameter builder, data table, version diff, live check and preview through
  `POST /v1/admin/prompts/{name}/preview`), the agent editor and the playground. See `docs/prompt-modules.md`.
- A per-session `locale` at token mint: speech rules and messages follow the shipped pack of its language
  (400 `unknown_locale` without one).
- The TypeScript and Python clients send `modules` and `locale` when minting, and save prompt modules and preview them.

### Changed

- Prompt texts without `{{` replace only `{date}` and `{app}`: other braces stay as written (before, a JSON example
  such as `{"ok": true}` in instructions failed to render).
- Neutral wording: the default voice rule no longer names domain-specific codes ("codes, account numbers or personal
  data"); agents that keep the default rule keep their version.

- The README explains what Evokai brings into a product (the agent backend, the interface, the admin console,
  governance, monitoring and integrations) and its governance in detail; the demo GIF is no longer in it.

## [0.1.3] - 2026-11-21

### Added

- A documentation website built from `docs/` with MkDocs and the Material theme, published to GitHub Pages at
  <https://giordyfish.github.io/evokai/> on every push to `main` and built with `--strict` on every pull request that
  touches it.
- A short demo at the top of the README: the bookshop example's agent answering from the shop's catalogue tool,
  recorded live with qwen3 8B through Ollama.

### Changed

- Development tooling of the npm packages: ESLint 10 with @eslint/js 10 and jsdom 31. The published client and
  components behave the same.
- The service requires FastAPI 0.164 or newer.
- A Support section in the README, with GitHub Sponsors and Ko-fi.

### Fixed

- Events of a message sent right after an answer are no longer lost. The answer's stream let go of the session's
  channel while its last events were still being saved; the next message reopened it from a stale last number, its
  events collided with the saved ones and were dropped, and a client reconnecting could not replay them.
- The capability worker's `playwright` capability works in the browser image: Chromium launched with its renderer
  sandbox, which Docker's default seccomp profile does not allow, so it died at launch and every browser tool answered
  "the active tab has closed". It now runs without it, as browser-use already did, and `{"chromium_sandbox": true}`
  in `EVOKAI_WORKER_CONFIG` turns it back on where the runtime grants user namespaces (#25).

## [0.1.2] - 2026-11-20

### Changed

- Development tooling of the npm packages: Vitest 5, Vite 8, tsup 9 and typescript-eslint 8.73; the UI components
  and the console run on Preact 11. The published client and components behave the same.

### Security

- The browser capability worker image (`workers/capability`, `browser` extra) now installs Pillow 13.0.0, pypdf
  6.21.4, mcp 2.3.0 and anyio 4.14.2 instead of the vulnerable versions browser-use 0.13.14 pins exactly.
- The npm packages' test tooling no longer pulls the vulnerable tinypool, minimatch and esbuild versions (Vitest 4 and
  newer, tsup 9).

## [0.1.1] - 2026-11-20

### Changed

- Supported versions say what is tested: the service runs on Python 3.12 to 3.14 (onnxruntime, behind optional
  extras, has no Python 3.15 wheels yet), the Python client on 3.10 to 3.15, and the TypeScript client, the UI
  components and the console need Node 22 or newer. CI tests the oldest and the newest of each.
- The service reports its version from the installed package (`evokai.version`) in the API, the console's health
  page and MCP handshakes, instead of a number written in the code.
- Dependencies: FastAPI 0.163.1, NumPy 2.5.5; the console is built on Node 26 in the image; the release workflow's
  actions are at their current major versions, pinned to commit SHAs.
- npm packages are published with trusted publishing (OpenID Connect): no npm token is stored anywhere.

### Fixed

- Console: in the dark theme the native scrollbars were light and plain links in the content were the browser's
  blue.
- The UI components' tests on Node 25 and newer, whose own `localStorage` shadowed jsdom's.
- The bookshop example starts a new conversation when the one the page remembers is gone, instead of showing
  `token 404`.

## [0.1.0] - 2026-11-20

The first public release. Evokai is experimental: see the [status](README.md#status).

### Added

- **Agents runtime** (Python 3.12 to 3.14, FastAPI, pydantic-ai): applications with server, admin and session keys;
  agents with versions, instructions, an ordered model fallback chain and per-agent prompt caching on Bedrock
  Anthropic models; any provider pydantic-ai supports, behind deployment and application allow-lists.
- **Tools over MCP**, called as the signed-in user (`forward_user`) or with a service secret; ready connectors for
  SQL databases, REST APIs from an OpenAPI document, Google Drive, SharePoint and OneDrive, and IMAP mailboxes;
  per-user OAuth connections.
- **Confirmations and questions**: tools that change data stop the turn with a `confirm` event; ask rules add
  confirmations by tool, argument and resource.
- **Skills** with references, data and scripts, loaded on demand or preloaded; **knowledge bases** with hybrid search
  (pgvector on Postgres), access groups and citations; **long-term memory** with consent; **handoff** and
  **subagents**; capabilities from pydantic-ai-harness (Code Mode, compaction and more) behind three switches;
  separate **capability workers**.
- **Server runs** with JSON output schemas, and durable **workflows** (agents, tools, approvals, conditions, waits,
  parallel branches, schedules, signed webhooks) written as data.
- **Voice**: dictation, conversation and barge-in, in the browser or as server voice on Pipecat over WebRTC.
- **Budgets**: hard caps per application and per person (month, week, day), enforced before every model call;
  people only ever see shares of a cap.
- **Security layer** mapped to the OWASP LLM Top 10: injection heuristics and optional classifiers, spotlighting of
  untrusted content, personal data masking or tokenisation (EU and Italian formats), secrets and links filtered in
  answers, a tool policy, abuse limits, scope rules, security events; a red team suite per agent version.
- **Data protection**: data residency, jurisdiction and retention rules for every external processor, from a
  provider catalog with sourced facts; privacy export, erasure and session revocation; retention per kind of data;
  nothing ever stored at model providers.
- **Compliance packs** generated from the live configuration: AI Act, GDPR, and an ISO/IEC 27001 and SOC 2 mapping,
  in English and Italian, with snapshots and diffs.
- **Evals** per agent, run on save and on demand.
- **Open standards**: AG-UI, an OpenAI-compatible Chat Completions API and A2A agent cards.
- **Admin console** in English and Italian, with single sign-on (OIDC, PKCE, MFA), roles and an audit log.
- **Clients**: `@evokai/evokai-client` (headless TypeScript: server, browser and React entry points),
  `@evokai/evokai-ui` (Web Components and React wrappers) and the Python client `evokai-client`; the plain name
  `evokai` on PyPI and npm installs the client.
- **Operations**: a Docker Compose quick start, read-only container images in `lite` and `full` flavours, SBOMs,
  keyless image signing, OpenTelemetry, Redis or Valkey for limits shared across instances, an example application
  (`config/bookshop.yaml`).
- **Application identifiers** (`application.identifiers`): an application declares its own codes (an order number,
  a library card) with a pattern or a number of digits, an optional Luhn check and context, a label for masks and
  tokens, and how speech says them. They are found, tokenised and masked like the built-in personal data types in
  every PII control and may be named in `security.pii.types` ([security](docs/security.md#application-identifiers)).
