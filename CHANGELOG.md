# Changelog

All notable changes to Evokai are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may break
interfaces; breaking changes are listed under **Changed** or **Removed** with what to do.

## [Unreleased]

## [0.1.0] - 2026-11-20

The first public release. Evokai is experimental: see the [status](README.md#status).

### Added

- **Agents runtime** (Python 3.12 or newer, FastAPI, pydantic-ai): applications with server, admin and session keys;
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
  `@evokai/evokai-ui` (Web Components and React wrappers) and the Python client `evokai-client`.
- **Operations**: a Docker Compose quick start, read-only container images in `lite` and `full` flavours, SBOMs,
  keyless image signing, OpenTelemetry, Redis or Valkey for limits shared across instances, an example application
  (`config/bookshop.yaml`).
- **Application identifiers** (`application.identifiers`): an application declares its own codes (an order number,
  a library card) with a pattern or a number of digits, an optional Luhn check and context, a label for masks and
  tokens, and how speech says them. They are found, tokenised and masked like the built-in personal data types in
  every PII control and may be named in `security.pii.types` ([security](docs/security.md#application-identifiers)).
