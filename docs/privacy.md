# Privacy and data protection

What Evokai stores about the people who talk to its agents, where, for how long, and how an application answers a
person's request to see or erase their data. Evokai is a processor for the application that runs it: the application
decides why personal data is processed, Evokai processes it on its behalf.

Evokai never authenticates people: it knows each one by the application's own opaque id, the **subject**. If the
application uses e-mail addresses as subjects, those are personal data in every table below; an opaque id is better.

## What is stored, where, for how long

| Data | Table | When | Kept | Personal data |
|---|---|---|---|---|
| Conversations: title, agent and version, storage mode, consent record, kiosk exit code (hashed) | `evokai_sessions` | Evokai storage mode | `conversation_ttl_days` after the last message (else until deleted) | subject, title (first words of the first message) |
| Messages: what the model saw and what the user saw, events shown | `evokai_messages`, `evokai_threads` | Evokai storage mode | as the conversation | everything the user and the tools wrote (masked with `pii.history`) |
| Replay events (the stream as sent) | `evokai_events` | Evokai storage mode | as the conversation | as the messages |
| Turns: mode, outcome, timings | `evokai_turns` | always | as the conversation | subject |
| Traces: the steps of each turn and server run (model calls, tools, guard decisions, speech) with timings and counts; content only as `tracing_content` allows (masked by default, none with external storage) | `evokai_traces` | every turn and server run | `conversation_ttl_days` after the turn (else until deleted) | subject (shown only as a keyed hash); the masked content |
| Reviewers' notes and labels on a conversation | `evokai_review_notes` | a console user adds one | as the conversation | subject; what the reviewer wrote |
| Pending confirmations: the tool calls waiting for a yes | `evokai_pending_actions` | a change awaits confirmation | until decided; one hour with external storage | the calls' arguments |
| Memories: preferences, facts about the person's work, summaries of finished conversations (opt-in per agent), with their embeddings | `evokai_memories` | the agent remembers, with the person's consent per `memory_mode` ([memory](memory.md)) | until forgotten, erased, consent withdrawn, or `memory_ttl_days` after written or confirmed; superseded ones 30 days | subject, the text (`memory.write` keeps instructions, secrets and identifiers out; episodes are masked) |
| What harness capabilities keep: memory files (per person and agent), plans and step logs (per conversation), spilled tool results (per conversation, a week at most), spend counters (per window, no person's data) | `evokai_capability_records` | an agent with the capability turned on ([capabilities](capabilities.md#stores)); conversation content only with Evokai storage, memory files only with the person's memory active | memory files as memories (`memory_ttl_days`, consent withdrawn, «forget everything»); a conversation's records as the conversation; spend counters until their window rolls over | subject; what the agent wrote (`memory.write` checks memory files) |
| Memory consent: the person's answer, its source and time | `evokai_memory_consents` | the person or the application answers | until erased | subject |
| Cost alerts: thresholds a forecast crossed, as shares | `evokai_cost_alerts` | a forecast crosses a threshold | kept | a person only as a keyed hash |
| Usage: tokens, model, cost per call | `evokai_usage_records` | every model call | for good (caps and invoices) | subject, tags |
| Forwarded credential (the user's token for the application's tools) | `evokai_session_credentials` | a session token asks for it | the token's life (at most 15 minutes), encrypted (Fernet) | a credential |
| Security events | `evokai_security_events` | a control finds or acts | `security_event_ttl_days` (default 180) | subject; the snippet is always masked |
| Bug reports | `evokai_bug_reports` | the user reports a problem | until deleted or erased | subject, description, context the client sent |
| Session revocations | `evokai_subject_revocations` | erase or sign-out everywhere | 20 minutes | a keyed hash of the subject only |
| Knowledge bases: documents, their files and chunks | `evokai_knowledge_*` | the application adds documents | until deleted, or the base's `retention_days` | whatever the documents contain: they belong to the application, not to a subject |
| Workflow runs: input, output, each step's input and output, approvals (question, summary, who decided) | `evokai_workflow_runs`, `evokai_workflow_steps`, `evokai_workflow_approvals` | a workflow runs | the workflow's `retention_days` after the run started (default 90) | the run's subject when it has one; what its steps read and wrote (masked with `pii.history`) |
| Connections: a person's OAuth tokens for Microsoft 365 or Google Workspace, the account and the scopes | `evokai_oauth_connections` | the person connects a provider | until they disconnect, the application revokes all, or the person is erased; tokens encrypted (Fernet) | subject, the account (an e-mail), the tokens |
| Channel threads: which conversation an e-mail thread, Teams conversation or Slack thread belongs to, and where to reply | `evokai_channel_threads` | a person writes over a channel | until the channel is deleted or the person erased | subject (an e-mail subject is a keyed hash), the reply reference (Teams conversation id, Slack channel); thread ids only as hashes |
| Console audit log | `evokai_audit_log` | a console or admin change | `audit_ttl_days` (default 400) | who made the change (an e-mail for SSO users); a subject only as a keyed hash |

With **external storage** (`storage_mode: external`) the application keeps the conversation: Evokai stores no
message, event, title or memory, only turns, traces without content, usage, security events and a pending confirmation
for at most an hour.

**Never stored at a model provider.** Every request carries the history explicitly; no provider-side conversations,
threads, stored responses or memories are ever used (`evokai.llm.policy`). Bedrock prompt caching keeps a request's
prefix for 5 minutes or an hour with no id to address it and is opt-in per agent.

**Logs** carry no message content. With `pii.logs` enforced (the default) every log line has identifiers and secrets
masked; a subject that is an e-mail address appears masked too.

**Encryption.** MCP server secrets, forwarded credentials and connection tokens are encrypted with the deployment's
Fernet key. Everything
else relies on the database's encryption at rest and TLS (`sslmode=verify-full`), which the deployment provides.

## A person's requests

The application relays them with its server key (its backend knows who the person is), or an operator makes them
from the console's **Privacy** page. The subject travels in the request body, never in the address, so it stays out
of access logs. Every request is written to the audit log with a keyed hash of the subject.

```bash
curl -X POST $BASE/v1/privacy/export -H "Authorization: Bearer $SERVER_KEY" -d '{"subject": "u-123"}'
curl -X POST $BASE/v1/privacy/erase  -H "Authorization: Bearer $SERVER_KEY" -d '{"subject": "u-123"}'
curl -X POST $BASE/v1/privacy/revoke-sessions -H "Authorization: Bearer $SERVER_KEY" -d '{"subject": "u-123"}'
```

- **Export** (access, portability): JSON with every conversation (messages, turns, consent), memories (superseded
  ones too) and the memory consent, usage (with amounts for a server key, without for the console), traces, security
  events (masked), bug reports and the person's connections (provider, account, scopes, dates: never the tokens).
- **Erase** (right to be forgotten): conversations, messages, replay events, turns, traces (server runs included),
  reviewers' notes, pending confirmations, memories, the memory consent, bug reports, connections (Google's refresh
  tokens are revoked at Google first) and channel threads are deleted at once; usage rows keep their tokens and cost under a random `erased:` pseudonym, so the
  application's monthly and daily caps stay correct (the person's own caps start again: there is no one left to
  attribute them to); security events lose the subject, the conversation and their snippet; every session token of
  the person stops working immediately and their running turns are cancelled. Running it again is harmless.
- Knowledge bases are the application's documents, not a person's data: erase leaves them. An application that puts
  personal data in a document deletes the document (or sets the base's `retention_days`); deleting a document removes
  its chunks and its file.
- **Sign out everywhere**: every session token minted until now is refused (the application's logout can call it).

With external storage the application erases its own copy of the conversations; Evokai's erase still removes the
rest.

## Data residency

An application can declare where its data lives and where its models may run:

```yaml
application:
  residency:
    region: eu-north-1          # where the data is stored (a declaration for reviewers)
    model_regions: [eu]         # eu, us, apac, us-gov, ca, jp, au, global
    models: ["ollama:*"]        # further patterns allowed (self-hosted models)
```

`model_regions: [eu]` allows the models whose endpoint the provider catalog places in the EU: Bedrock's `eu.*`
inference profiles and in-region model ids when the deployment's region (`EVOKAI_AWS_REGION`) is in the EU, and the
EU endpoints of other providers (Scaleway, IONOS, STACKIT, OVHcloud, Mistral's `mistral-eu:`...); `us.*`, `global.*`
and providers whose geography is global or unknown are refused unless a pattern in `models` allows them. It is
enforced on top of the deployment allow-list (`EVOKAI_MODEL_ALLOW`), the application's `allowed_models` and the
grants: at agent save (400 `residency`), at token mint for a requested model (403 `model_residency`) and on every
model call (a fallback outside the residency is never tried). Set it with the admin API (`PATCH
/v1/admin/application`), the console's Privacy page, the YAML or `residency` in `EVOKAI_BOOTSTRAP_APPS`.

### The data protection profile

`data_protection` goes further, with three independent rules that every external processor must meet, not only the
models: where data may be processed (`residency`: geographies or countries), who may control the provider
(`jurisdiction`: `eu` for the EU or EEA, or countries) and what it may keep (`retention`: `no_training` or
`zero_retention`). A fact nobody verified never satisfies a rule. It applies to models, embeddings, guard classifiers,
the code sandbox, server speech and connections, and the GDPR pack lists every processor with its facts. See
[data protection](data-protection.md#the-data-protection-profile).

## Retention

| What | Setting | Default |
|---|---|---|
| Conversations (Evokai storage) | `conversation_ttl_days` | kept |
| Traces (every turn and server run) | `conversation_ttl_days` | kept |
| Security events | `security_event_ttl_days`, else `EVOKAI_SECURITY_EVENT_TTL_DAYS` | 180 days |
| Audit log | `audit_ttl_days`, else `EVOKAI_AUDIT_TTL_DAYS` (also for deployment-wide entries) | 400 days |
| Forwarded credentials | the session token's life | at most 15 minutes |
| Pending confirmations (external storage) | fixed | one hour |
| Session revocations | the longest token life plus a margin | 20 minutes |
| Knowledge documents | the base's `retention_days` | kept |
| Memories | `memory_ttl_days` (and withdrawing consent deletes them at once) | kept |
| Capability records | memory files: `memory_ttl_days`; a conversation's: as the conversation (spilled results a week at most); spend counters: their window | kept |

The service purges hourly; `evokai purge` does the same from a scheduler.

## Sub-processors

Who else may process personal data, depending on how the deployment is configured:

- **Model providers**: Amazon Bedrock in the deployment's region with EU inference profiles (for example `eu-north-1`
  with `bedrock:eu.*` only). Other providers (OpenAI, Mistral, a local Ollama) only when the deployment allow-list includes
  them; a residency declaration or the data protection profile can keep them out per application. The provider
  catalog ([data protection](data-protection.md)) states each one's legal entity, who controls it, where it processes data
  and what its API terms let it keep, with sources, and the GDPR pack lists them from it.
- **Guard classifiers**: only the one the security policy chooses, and only when the profile allows it: Amazon
  Bedrock Guardrails, Azure AI Content Safety, Google Model Armor, Mistral or OpenAI moderation, or a self-hosted
  one; the local Prompt Guard sends nothing anywhere.
- **Embeddings**: Amazon Titan Text Embeddings v2 on Bedrock in the deployment's region, for knowledge bases (their
  documents and the questions that search them) and memories (their text and the message that recalls them). The data residency applies to embedding models too.
- **Code execution**: only with `EVOKAI_CODE_ENABLED` and the application's `code` grant, Amazon Bedrock AgentCore Code
  Interpreter in the deployment's EU region processes the data an agent passes to `code_run`, in a session that ends
  with the call ([code](code.md)).
- **Speech providers**: with server voice (`EVOKAI_VOICE_ENABLED`). Browser voice runs on the device and sends no
  audio anywhere, unless the application opts in to the browser vendor's online services: `allowOnlineVoices` (the
  answers' text, to the browser's speech service, only where the device has no voice for the language) and
  `allowOnlineRecognition` (the user's audio, to the browser's recogniser, only where there is no on-device one:
  Safari and every iOS browser send it to Apple). On-device always comes first; an application that turns either on
  lists that vendor among its sub-processors.
- **The database host** (for example Amazon RDS) and the compute that runs the containers (for example an Amazon
  EC2 instance).
- **Observability**: only when OpenTelemetry is enabled (`EVOKAI_OTEL_ENABLED`), to the configured exporter (an
  OpenTelemetry Collector, Langfuse, LangSmith, Grafana): traces with the subject as a keyed hash and content only as
  the application's `tracing_content` allows; logs go to the platform's log service (for example CloudWatch), masked.
- **Shared state**: with `EVOKAI_REDIS_URL`, the Redis or Valkey that holds rate counters and sign-in codes, under
  keyed hashes only (no subject, address or code in clear), each with an expiry of minutes.
- **The identity provider** of console sign-in (SSO), for the console's users only.

The application lists these in its own records of processing and its privacy notice; the GDPR pack
([compliance](compliance.md)) lists them from the live configuration.

## Limits

- Masking and the export find identifiers with a recognisable format (see [security](security.md)), the built-in ones
  and those the application declares ([application identifiers](security.md#application-identifiers)); names and
  free text are erased with the conversation, not masked.
- Backups of the database follow the deployment's backup retention: an erased person disappears from backups when they
  expire.
- Usage rows of an erased person keep the agent, model, tags and time of each call (without the person).
