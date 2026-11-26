# AG-UI

Evokai speaks [AG-UI](https://docs.ag-ui.com) (the Agent User Interaction Protocol), so a
[CopilotKit](https://copilotkit.ai) frontend, or any client that speaks AG-UI, can talk to an Evokai agent without
Evokai's own client.

```
POST {base}/v1/session/agui
Authorization: Bearer <session token>
Content-Type: application/json
Accept: text/event-stream

RunAgentInput  →  AG-UI events (SSE, one JSON event per data line)
```

It is the session chat (`POST /v1/session/chat`) with another event vocabulary, not a second engine: the same
session token, origin check, rate limit (240 requests a minute per token), turn engine, grants, security policy,
caps and storage. Closing the stream cancels the turn. A refused run (no token, a wrong origin, a revoked grant, an
abuse limit) is an HTTP error before the stream starts, with Evokai's usual `{error, message}` body.

## The input

| `RunAgentInput` | What Evokai does with it |
|---|---|
| `threadId`, `runId` | Echoed in `RUN_STARTED` and `RUN_FINISHED`. The conversation is always the session token's own (its id is in the state snapshot). |
| `messages` | The last user message is the turn's input. With Evokai storage the earlier messages are ignored (Evokai has the conversation); with external storage they become the history (text only), unless `forwardedProps.evokai.history` carries the full `HistoryMessage` list. |
| `resume` | Answers to the previous run's interrupts (see [Human in the loop](#human-in-the-loop)). |
| `context` | Becomes the page the user is looking at (each entry as `description: value`), handled like the page of a session chat: in the prompt's volatile part, scanned as untrusted content. |
| `forwardedProps.evokai.page` | `{path, title, text}`: the open page, instead of `context`. |
| `tools` | Not used: an Evokai agent's client tools are its own (charts, diagrams, questions, navigation), and arrive as `CUSTOM` events. |
| `state` | Not used: Evokai keeps no frontend-writable agent state. |

## The events

| Evokai event | AG-UI events |
|---|---|
| (start) | `RUN_STARTED`, then `STATE_SNAPSHOT` `{evokai: {conversationId, agent, turnId}}` |
| `text` | `TEXT_MESSAGE_START` (role `assistant`), `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END` before a tool call or at the end |
| `tool` start, done or error | `TOOL_CALL_START` and `TOOL_CALL_END` (no arguments are streamed), then `TOOL_CALL_RESULT` whose `content` is `{"status": "done"}` or `{"status": "error"}`: a tool's output never reaches the browser. A call that has not run (`args`, `waiting`, `skipped`, an agent with `show_tool_args`) sends nothing |
| `confirm` | `CUSTOM evokai.confirm` `{actionId, items}`, and an interrupt at the end of the run |
| `ask` | `CUSTOM evokai.ask` `{askId, question, options, multiple, other}`, and an interrupt at the end of the run |
| `chart`, `diagram`, `navigate`, `say`, `speech`, `citation`, `reasoning` | `CUSTOM evokai.chart`, `evokai.diagram`, `evokai.citation` (a knowledge base passage the answer cites), `evokai.reasoning` (an agent that [shows its reasoning](streaming.md))... with the same payload as the session chat |
| `usage` | `CUSTOM evokai.usage` and `STATE_DELTA` `[{op: add, path: /evokai/usage, value}]`: shares of the caps (`dayPct`, `weekPct`, `monthPct`, `companyPct`, `monthOut`, `out`, `binding`, `resetsAt`, `caps`), never amounts |
| `error` | `RUN_ERROR` `{message, code}` (`guard`, `limit`, `model`, `internal`...), the run's last event |
| `done` | `RUN_FINISHED` with `result: {reason, conversationId, messages?}` (`messages` with external storage, to append to the application's copy) and `outcome` |

## Human in the loop

Evokai never runs a tool that changes data without the user's yes, and an agent can ask a question with options.
Both end the turn. AG-UI offers two ways to model that: a frontend tool the agent calls, or an **interrupt**. Evokai
uses interrupts, the protocol's own pause and resume, because the decision belongs to the service: the tool call
waits on the server, already labelled for the user, and only the server can run it.

A run that stops for the user ends with:

```json
{
  "type": "RUN_FINISHED",
  "threadId": "t-1",
  "runId": "r-1",
  "result": {"reason": "confirm", "conversationId": "c3f..."},
  "outcome": {
    "type": "interrupt",
    "interrupts": [{
      "id": "6f1c...",
      "reason": "confirmation",
      "message": "Classify the differences of 2026-10 as Accepted",
      "responseSchema": {"type": "object", "properties": {"approved": {"type": "boolean"}}, "required": ["approved"]},
      "metadata": {"items": ["Classify the differences of 2026-10 as Accepted"]}
    }]
  }
}
```

- a **confirmation** is `reason: "confirmation"`; its `id` is the confirmation's action id and one interrupt covers
  every change the turn proposed (`metadata.items` lists them, `metadata.why` says why each waits, as the `confirm`
  event's `why`: a change, the policy or an [ask rule](security.md#ask-rules));
- a **question** is `reason: "input_required"`, with the options as an `enum` of `answer` (an array when several can
  be picked; no enum when the user may write their own).

The client resumes on the same thread:

```json
{"threadId": "t-1", "runId": "r-2", "messages": [], "tools": [], "context": [], "forwardedProps": {},
 "resume": [{"interruptId": "6f1c...", "status": "resolved", "payload": {"approved": true}}]}
```

`payload.approved: false`, or `status: "cancelled"`, refuses the change; a question is answered with
`payload: {"answer": "September"}`. Unlike the protocol's strict reading, a new user message without `resume` is
accepted: as in the session chat, it leaves the open confirmation unconfirmed and starts a new turn.

Frontends that do not handle interrupts yet can render the `CUSTOM evokai.confirm` and `evokai.ask` events with their
own components and send the decision back as `resume`.

## CopilotKit

CopilotKit reaches AG-UI agents through `HttpAgent` from `@ag-ui/client`. Mint the session token on your backend
(`POST /v1/session-tokens` with the server key) and give it to the browser, as with Evokai's own client:

```ts
import { HttpAgent } from '@ag-ui/client'

export function evokaiAgent(token: string) {
  return new HttpAgent({
    url: 'https://app.example/evokai/v1/session/agui',
    headers: { Authorization: `Bearer ${token}` },
  })
}
```

```tsx
import { CopilotKit } from '@copilotkit/react-core'
import { CopilotChat } from '@copilotkit/react-ui'

export function Assistant({ token }: { token: string }) {
  return (
    <CopilotKit runtimeUrl="/api/copilotkit" agent="evokai">
      <CopilotChat />
    </CopilotKit>
  )
}
```

with a CopilotKit runtime route that registers the agent (Next.js):

```ts
import { CopilotRuntime, ExperimentalEmptyAdapter, copilotRuntimeNextJSAppRouterEndpoint } from '@copilotkit/runtime'
import { HttpAgent } from '@ag-ui/client'

export const POST = async (req: Request) => {
  const token = await mintEvokaiToken(req) // your backend: POST /v1/session-tokens
  const runtime = new CopilotRuntime({
    agents: {
      evokai: new HttpAgent({
        url: `${process.env.EVOKAI_URL}/v1/session/agui`,
        headers: { Authorization: `Bearer ${token}` },
      }),
    },
  })
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: '/api/copilotkit',
  })
  return handleRequest(req)
}
```

A request made by a server (the runtime) carries no `Origin` header, so the origin check does not apply to it; a
browser that calls Evokai directly must be on one of the application's allowed origins.

## Without a framework

`@evokai/evokai-client/browser` has `runAgui` and `interruptsOf`, with the events typed:

```ts
import { interruptsOf, runAgui } from '@evokai/evokai-client/browser'

for await (const e of runAgui({ baseUrl, token, input: { threadId, runId, messages } })) {
  if (e.type === 'TEXT_MESSAGE_CONTENT') append(e.delta)
  for (const i of interruptsOf(e)) askUser(i)
}
```

Or with `curl`:

```sh
curl -N "$EVOKAI/v1/session/agui" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"threadId":"t","runId":"r","messages":[{"id":"1","role":"user","content":"How was October?"}],"tools":[],"context":[],"forwardedProps":{}}'
```

## Not supported

- the protobuf encoding (`application/vnd.ag-ui.event+proto`): events are JSON over SSE;
- client-defined tools and frontend-writable state (see the input table);
- `MESSAGES_SNAPSHOT`: the conversation is read with `GET /v1/session/conversation`;
- streamed tool arguments (`TOOL_CALL_ARGS`) and tool outputs: a browser sees which tool runs, not its data;
- voice: AG-UI runs are text turns (`say` segments still arrive as `CUSTOM evokai.say`).
