# Reasoning, tool arguments and tool types

Three things a chat can show or use while an agent works, besides its answer: a summary of the model's reasoning, the
arguments of its tool calls as the model writes them (and a structured answer filling in), and TypeScript types of its
tools. The first two are model output on their way to a browser: each piece passes the same output checks as the
answer's text before it is sent, and both are off until an agent turns them on.

## The model's reasoning (`show_reasoning`)

```yaml
agents:
  - name: helper
    model: bedrock:eu.anthropic.claude-sonnet-4-6
    show_reasoning: true
    model_settings:
      thinking: medium
```

While the model reasons, the session stream sends `reasoning` events (`{"type": "reasoning", "delta": "..."}`), before
the text or the tool call they lead to. The web component and the console playground show them in a collapsed
**Reasoning** block; `EvokaiSession` gives them as `{kind: 'reasoning', text}` parts.

Only a model that reasons and returns its reasoning sends one, through pydantic-ai:

| Provider | What comes back | How to turn it on |
|---|---|---|
| Anthropic, Bedrock (Claude) | a summary of the thinking | `thinking` in `model_settings`; Claude 5 models and Opus 4.7, 4.8 return an empty summary unless asked: `anthropic_thinking: {type: adaptive, display: summarized}` (Anthropic) or `bedrock_additional_model_requests_fields: {thinking: {type: adaptive, display: summarized}}` (Bedrock) |
| OpenAI reasoning models (Responses API) | a reasoning summary | `thinking` or `openai_reasoning_effort`; with `show_reasoning` Evokai asks for `openai_reasoning_summary: auto` unless the agent chose another |
| Google Gemini | thought summaries | `thinking` |
| OpenAI-compatible servers (DeepSeek, vLLM, Ollama...) | often the raw reasoning, not a summary | the server's own setting |

Every piece goes through the agent's output checks (`output.secrets`, `pii.browser`, `output.links`, tokens put
back) exactly like the answer, so a secret the model thinks about is redacted and an identifier masked. If the
reasoning repeats the agent's instructions (`output.system_prompt` enforced) the reasoning stops being shown for
that step and the event is recorded; the answer goes on and is checked on its own.

**After a reload.** The reasoning is stored with the message as a part (`reasoning`, consecutive pieces joined), so a
reloaded conversation shows it where it was, collapsed, as it was shown live. It is what the person was already
shown, after the checks, and it explains the answer next to it; with `pii.history` enforced it is masked like the
text, and it lives and dies with the message (time to live, export, erasure). An application that does not want it
kept turns `show_reasoning` off: nothing is shown nor stored.

**What the model sees again.** Never the reasoning of an earlier turn. The one exception is what the providers
require: while a step's tool calls are not answered, its reasoning goes back with them unchanged (Anthropic's
thinking blocks with their signatures, OpenAI's encrypted reasoning). That is every later request of the same turn,
whether or not the agent shows its reasoning, and the turn that carries out a confirmation: the reasoning of the
calls that waited is kept with them (server side only, never shown) and sent back with them once. With `pii.history`
enforced, a reasoning that holds an identifier is not kept for this (it can only go back unchanged): the confirmed
calls then go back without it.

## Tool arguments as they are written (`show_tool_args`)

By default a browser sees a tool's name and its status, never its arguments nor its result. With
`show_tool_args: true` it also sees the arguments of each call of a tool that shows a card (an application tool,
`knowledge_search`, `code_run`, `skill_run`) while the model writes them. This is a change of what leaves the server,
so it is a decision per agent: the arguments are what the model wrote, after the output checks, and they can hold
what the person or the tools said earlier in the conversation.

A call's `tool` events carry its `id`, so they update one card:

| `status` | Meaning |
|---|---|
| `args` | the call is being written: `args` is the object as far as it goes (a partial JSON closed where it stops), sent at most every 150 ms, then once complete |
| `start` | it runs (as before); then `done` or `error` |
| `waiting` | it changes data and waits for the person: the `confirm` card follows, unchanged |
| `skipped` | it did not run (refused by the security policy, or the turn was stopped) |

A card in `args`, `waiting` or `skipped` never says the call ran: only `start`, `done` and `error` do. A call still
being written when the turn is cut off is dropped, live and in the stored conversation alike. The stored part keeps
the last arguments shown, so a reload shows the same card.

AG-UI does not get `args`, `waiting` and `skipped` (a `TOOL_CALL_START` is still sent when the call runs), and gets
the reasoning as `CUSTOM evokai.reasoning`.

## A structured answer filling in

An agent with an `output_schema` writes its answer as one tool call. Over the streaming APIs the object is sent as it
is written, through the output checks, each one replacing the previous:

- OpenAI-compatible API: chunks with an empty `delta` and `evokai.output` (the object so far), before the usual
  content chunk with the validated JSON;
- A2A `message/stream`: `artifact-update` frames of the `answer` artifact with `append: false` and
  `lastChunk: false`, before the final one.

The partial objects are not validated against the schema yet (an invalid answer is retried, and the next partial
object starts again): only the last frame is the answer.

## TypeScript types of an agent's tools

```bash
export EVOKAI_ADMIN_KEY=evk_adm_...
evokai types --agent helper --url https://agents.example.com/agent --out src/evokai-tools.d.ts
```

or `GET /v1/admin/agents/{name}/types` with the admin key. The file declares, from the tools' JSON Schemas:

- per MCP tool, `<Name>Args` (its `inputSchema`) and `<Name>Result` (its `outputSchema`, `unknown` without one);
- per custom client tool of the agent, `<Name>Args` (its `parameters`);
- `ToolArgs`, `ToolResults` and `ClientToolArgs`, maps from the tool name to those types, and `Output` for an agent
  with an `output_schema`.

```ts
import type { ToolArgs } from './evokai-tools'

chat.toolRenderers = {
  orders_month: (part) => card(`Orders of ${(part.args as ToolArgs['orders_month'] | undefined)?.period ?? '...'}`),
}
```

An MCP server that forwards the person's credential lists its tools only with one: pass a test user's with
`--credential-env NAME` (the `X-Evokai-Credential` header); it is used for that listing and never kept. Without it
the file names the server as not listed. The conversion covers what tool schemas use (types, `enum`, `const`,
objects, arrays and tuples, `anyOf`, `oneOf`, `allOf`, local `$ref`); anything else is `unknown`, never narrower than
the schema. Generate the file again when the tools change.
