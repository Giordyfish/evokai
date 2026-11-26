# OpenAI-compatible API

Any OpenAI client (openai-python, the OpenAI Node SDK, LangChain, LlamaIndex, tools that take a "base URL") can talk
to an Evokai agent:

```
POST {base}/v1/openai/chat/completions
GET  {base}/v1/openai/models
```

The **model is the agent's name**. Behind it the agent runs as a server run:
it uses its own read-only tools on the server and the client gets the final answer.

## Authentication

| Bearer | Subject | Agents | Notes |
|---|---|---|---|
| the application's **server key** (`evk_srv_...`) | the request's `user` field, else `system:<agent>` | agents whose `channels` include `server` | usage tags from `evokai.tags`; the subject's groups (for knowledge bases restricted to some groups) from `evokai.groups` |
| a **session token** | the token's subject | the token's agent only (another `model` is 403 `agent`) | the token's forwarded credential reaches MCP servers with `forward_user`; its subject caps apply; the origin check and the per-token rate limit apply as to every session call |

The application's grants (the `server` channel for a server key, `browser` for a session token, models, MCP servers,
tools, skills), its security policy, its caps and its abuse limits apply exactly as to a run or a turn.

## Semantics

- **Server side tools.** The agent's tools run inside the request. Tools that change data (and tools the policy
  forces to a confirmation) are **not offered**, as in every server run: nobody could confirm them, unless the
  application opts in to [confirmations in text](#confirmations-in-text).
- **Stateless.** The client sends the conversation every time (`messages`); nothing is stored and there is no
  conversation id to pass.
- **The agent's own instructions.** `system` and `developer` messages are ignored: the agent's instructions are its
  system prompt, and the guard keeps it that way. `tool` messages and assistant tool calls are ignored too.
- **The agent's own settings.** `temperature`, `max_tokens`, `top_p`, `response_format`... are not taken from the
  request: the agent definition sets them. An agent with an `output_schema` answers with its JSON as the message
  content.
- **Text only.** Content parts other than `text` are refused (`400 unsupported_content`); files go through the
  browser chat ([attachments](attachments.md)).
- **Client tools are not supported** ([custom client tools](client-tools.md) run on the session's own stream): a request with `tools` is refused (`400 tools_not_supported`); `n` above 1 is
  refused (`400 n_not_supported`).

## Confirmations in text

An application can let an agent change data over this API, with the person's confirmation typed in the next message.
It is opt-in twice, off by default: the grant `openai:confirm` (Permissions, "OpenAI-compatible API") and the agent's
`openai_confirm: true`.

1. When the model calls a tool that changes data, the call does not run. Evokai keeps the pending action (the subject,
   the agent, the exact arguments, the hash of the assistant message it answers with; it expires after 10 minutes and
   is used once) and answers in plain text in the application's language, for example "Before I go ahead, please
   confirm: Refund the orders of 2026-10. Reply yes or no.", plus `evokai.confirm` `{actionId, items, why}` for
   clients that draw buttons. A streamed answer asks in its last content chunk, after what it already said.
2. The next request must carry that assistant message unmodified, from the same subject (the server key's `user`, or
   the session token's subject), followed by the answer:
   - **yes** (the locale's yes words, or `evokai.confirm: {actionId, approve: true}` from a button) runs the stored
     arguments, never regenerated, and the agent goes on to its answer;
   - **no** cancels without calling the model ("OK, I have not changed anything.");
   - **anything else** cancels the action and is answered as a new message.

   An edited assistant message, another subject, an expired or used action: nothing runs.

Every decision is in the run's trace: the tool's span carries `confirmed` and `confirmChannel: "openai"`.

**The limit:** the protocol cannot prove that a person typed the yes. A client could send it on its own, so grant
`openai:confirm` only to applications whose clients show the question to a person.

## The answer

```json
{
  "id": "chatcmpl-0b9e...",
  "object": "chat.completion",
  "created": 1763337600,
  "model": "triage",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "...", "refusal": null},
               "finish_reason": "stop", "logprobs": null}],
  "usage": {"prompt_tokens": 1830, "completion_tokens": 92, "total_tokens": 1922,
            "prompt_tokens_details": {"cached_tokens": 1536}},
  "evokai": {"runId": "0b9e...", "model": "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "toolCalls": 1}
}
```

`usage` is in tokens; there is never an amount. `evokai` is an extension field (OpenAI clients keep unknown fields):
the run's id (to find its trace in the console), the model that actually answered (after any fallback) and the
number of tool calls, and `citations` when the agent cited knowledge base passages. A session-token caller also gets `evokai.usage`, its shares of the caps
(`dayPct`, `weekPct`, `monthPct`, `companyPct`, `monthOut`, `out`, `binding`, `resetsAt`, `caps`).

### Streaming

With `"stream": true` the answer is a stream of `chat.completion.chunk` events ending with `data: [DONE]`:

1. a chunk with `delta: {role: "assistant", content: ""}`;
2. one chunk per tool call step, with an empty `delta` and `evokai.tool: {name, status}` (`start`, `done`, `error`);
3. the answer as it is written, in content chunks:
   - **sentence by sentence** when an output control of the application's security policy is enforced (secrets,
     personal data and links towards the browser, the system prompt leak, tokens put back): each sentence passes the
     output guard before it leaves, and the guard keeps the tail of what it already saw in its scan, so a secret split
     across two sentences is still caught whole;
   - **token by token** when every output control is `off` or `monitor` (monitor findings are recorded at the end);
   - in **one chunk** for an agent with an `output_schema` (its JSON); while the model writes it, chunks with an
     empty `delta` carry the object so far as `evokai.output`, through the output checks and not validated yet, each
     replacing the previous ([streaming](streaming.md#a-structured-answer-filling-in));
4. a chunk with `finish_reason: "stop"` and the `evokai` extension;
5. with `stream_options.include_usage`, a chunk with `choices: []` and `usage`.

The answer carries every text the agent writes, also a sentence it writes before calling a tool, as in a session,
streamed or not, each step's text set apart by a blank line (`"Let me check.\n\nOctober has 24 orders."`).
If a later sentence is refused (the answer repeats the agent's instructions), the stream ends with an error
chunk carrying the guard's refusal text (`code: "guard"`) followed by `[DONE]`: what was already sent stays sent,
nothing after it goes out.

An error after the stream started (a cap reached between two steps, a model failure, the guard's refusal) is a chunk
`{"error": {...}}` followed by `[DONE]`; openai-python raises it as an `APIError`. Comments (`: ping`) keep idle
connections open while tools run.

### Errors

Errors use OpenAI's shape, so OpenAI clients raise their usual exceptions:

```json
{"error": {"message": "...", "type": "rate_limit_error", "code": "limit", "param": null}}
```

| Status | `type` | Typical `code` |
|---|---|---|
| 400 | `invalid_request_error` | `empty` (the last message is not the user's), `tools_not_supported`, `unsupported_content`, `bad_request` |
| 401 | `authentication_error` | `unauthorized`, `key`, `token` |
| 403 | `permission_error` | `channel_not_granted`, `model_not_granted`, `agent`, `origin` |
| 404 | `not_found_error` | `model_not_found` (unknown agent, or one this caller cannot reach) |
| 413 | `invalid_request_error` | `too_large` (the policy's `input.size`) |
| 429 | `rate_limit_error` | `limit` (a cap), `rate_limited` (an abuse limit or the token's rate) |

## openai-python

```python
from openai import OpenAI

client = OpenAI(base_url="https://app.example/evokai/v1/openai", api_key=EVOKAI_SERVER_KEY)

answer = client.chat.completions.create(
    model="triage",
    user="customer-4211",  # the subject usage is recorded under
    messages=[{"role": "user", "content": "My order arrived twice this month."}],
)
print(answer.choices[0].message.content, answer.usage.total_tokens)
print(answer.model_extra["evokai"])  # runId, model, toolCalls

for chunk in client.chat.completions.create(
    model="triage",
    messages=[{"role": "user", "content": "My order arrived twice this month."}],
    stream=True,
    stream_options={"include_usage": True},
):
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

## LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="https://app.example/evokai/v1/openai",
    api_key=EVOKAI_SERVER_KEY,
    model="triage",
)
print(llm.invoke("My order arrived twice this month.").content)
```

Do not `bind_tools` on it: the agent's tools are Evokai's and run on the server, and a request with `tools` is
refused. An Evokai agent fits LangChain as a model, or as a tool of a LangChain agent that calls it.

## Not supported

Client tools and function calling, images, audio and files, `n` above 1, logprobs, the Responses API, the
Assistants API and stored completions (Evokai never stores conversations at a provider, and offers no stored state
of its own here), and embeddings.
