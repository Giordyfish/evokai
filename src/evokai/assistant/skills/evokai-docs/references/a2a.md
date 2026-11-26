# A2A (Agent2Agent)

Evokai can publish agents to other agents over [A2A](https://a2a-protocol.org), the Agent2Agent protocol of the
Linux Foundation: another agent finds an Evokai agent through its **Agent Card** and sends it messages over
JSON-RPC.

## Off by default

An agent is published only when both are true:

1. the application is granted the **`a2a` channel** (console: Permissions, Channels; admin API:
   `PATCH /v1/admin/grants` with `{"kind": "channel", "name": "a2a", "value": true}`; YAML:
   `grants: {channels: {a2a: true}}`). It is the one grant that is off until given;
2. the agent definition sets **`a2a: true`** (console: the agent editor, Instructions, "Publish over A2A"). Saving an
   agent with `a2a: true` while the application is not granted A2A is refused (`400 not_granted`).

Revoking the grant unpublishes every agent at once: their cards and endpoints answer 404.

## Endpoints

```
GET  {base}/v1/a2a/{applicationId}/{agent}/.well-known/agent-card.json    the Agent Card (public)
POST {base}/v1/a2a/{applicationId}/{agent}                                JSON-RPC 2.0 (server key)
```

The card is public, like any Agent Card: it holds the agent's name, title, starter questions as examples, its input
and output modes and how to authenticate, never a secret. A card of an agent that is not published, or of an
inactive application, is 404 (the same answer for every reason).

The JSON-RPC endpoint needs the application's **server key** (`Authorization: Bearer evk_srv_...`), declared in the
card as an HTTP bearer security scheme (`evokaiServerKey`). A key of another application is 401.

## Versions

Both A2A versions in use are spoken, chosen by the method name:

| | 1.0 | 0.3 |
|---|---|---|
| Send | `SendMessage` | `message/send` |
| Stream | `SendStreamingMessage` | `message/stream` |
| Get a task | `GetTask` | `tasks/get` |
| Cancel a task | `CancelTask` | `tasks/cancel` |
| Enums | `ROLE_USER`, `TASK_STATE_COMPLETED` | `user`, `completed` |
| Parts | `{"text"}`, `{"data", "mediaType"}` | `{"kind": "text", "text"}`, `{"kind": "data", "data"}` |
| Results | wrapped: `{"task": ...}`, `{"statusUpdate": ...}`, `{"artifactUpdate": ...}` | tagged with `kind` |

An `A2A-Version` header other than `0.3` or `1.0` is refused (`-32009`). The card by default carries the fields of
both versions (1.0's `supportedInterfaces` and `securityRequirements`, 0.3's `protocolVersion`, `url`,
`preferredTransport` and `security`); `?version=0.3` or `?version=1.0` returns a card of one version only, for strict
parsers.

## How a message runs

Each message is one **server run** of the agent on the `a2a` channel (`evokai.sessions.runs`), so everything a
server run does applies: only read-only tools (nothing that needs a confirmation), the application's security policy
(the message is untrusted data, as a server run's input: spotlighted and scanned), its caps, its abuse limits, and a
trace of the run in the console. Usage is recorded under the subject `a2a:<agent>` with the tag `a2a`. Nothing is
stored.

- **Input**: text parts are the message; data parts and inline text files (`text/*` or JSON, base64) become
  attachments. A file by URL or a binary file is refused (`-32005`).
- **Output**: one artifact, `answer`, with a text part, or a data part when the agent has an `output_schema` (over
  `message/stream` its data fills in as it is written: `artifact-update` frames with `append: false` and
  `lastChunk: false`, each replacing the previous, before the final one; see [streaming](streaming.md)). The
  answer is also the last message of the task's `history`. The task's `metadata.evokai` carries the run id, the
  number of tool calls and, when the agent searched a knowledge base, the passages it cites (`citations`).
- **States**: a task goes `submitted`, `working`, then `completed`, `failed` (a cap, a model failure, an invalid
  structured answer) or `rejected` (the security policy refused it). A server run asks nothing, so there is no
  `input-required`.

### message/send (0.3)

```sh
curl "$EVOKAI/v1/a2a/$APP/triage" -H "Authorization: Bearer $SERVER_KEY" -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "message/send",
  "params": {"message": {"kind": "message", "messageId": "m1", "role": "user",
                         "parts": [{"kind": "text", "text": "My order arrived twice this month."}]}}}'
```

```json
{"jsonrpc": "2.0", "id": 1, "result": {
  "kind": "task", "id": "9b1...", "contextId": "4c2...",
  "status": {"state": "completed", "timestamp": "2026-11-16T09:12:03+00:00"},
  "artifacts": [{"artifactId": "answer-9b1...", "name": "answer",
                 "parts": [{"kind": "data", "data": {"category": "order", "priority": 2}}]}],
  "history": [{"kind": "message", "role": "user", ...}, {"kind": "message", "role": "agent", ...}],
  "metadata": {"evokai": {"runId": "9b1...", "toolCalls": 1}}}}
```

### SendStreamingMessage (1.0)

The answer is an SSE stream; each `data:` line is a JSON-RPC response whose `result` is, in order: the `task`
(submitted), a `statusUpdate` (working), one `statusUpdate` per tool call step (`metadata.evokai.tool`), the
`artifactUpdate` with the answer (`lastChunk: true`), and the final `statusUpdate` (completed, failed or rejected).
0.3 streams the same with `kind` tags and `final: true` on the last status. The answer comes whole, once the guard
has checked it, not token by token.

### Tasks

Evokai keeps only a task's id, context and state, for an hour, and never its content: `GetTask` / `tasks/get`
returns the state without history or artifacts. `CancelTask` / `tasks/cancel` stops a task that is still streaming
on the instance that runs it; a finished task is `-32002` (not cancelable). A `contextId` sent with a message is
echoed back, but each message is a new task without the previous ones: send what the agent needs in the message.

## Errors

| Code | When |
|---|---|
| `-32700` | the body is not JSON |
| `-32600` | not a JSON-RPC 2.0 request |
| `-32601` | unknown method |
| `-32602` | no `params.message` with parts, no task id |
| `-32001` | unknown or expired task |
| `-32002` | the task is no longer running (cancel) |
| `-32003` | push notifications |
| `-32004` | `SubscribeToTask`, `ListTasks`, the extended card |
| `-32005` | a file part by URL or binary |
| `-32009` | an `A2A-Version` other than 0.3 and 1.0 |

HTTP errors come before JSON-RPC: 401 without the application's server key, 404 when the agent is not published.

## Not supported

Push notifications, resubscription, task lists, the authenticated extended card, multi-turn tasks
(`input-required`), files by URL and binary files, and the gRPC and HTTP+JSON bindings (JSON-RPC only).
