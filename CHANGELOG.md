# Changelog

All notable changes to Evokai are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may break
interfaces; breaking changes are listed under **Changed** or **Removed** with what to do.

## [Unreleased]

### Security

- The browser capability worker image (`workers/capability`, `browser` extra) now installs Pillow 13.0.0, pypdf
  6.21.4, mcp 2.3.0 and anyio 4.14.2 instead of the vulnerable versions browser-use 0.13.14 pins exactly.

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
