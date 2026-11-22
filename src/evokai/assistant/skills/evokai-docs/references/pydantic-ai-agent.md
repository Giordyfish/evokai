# Turns and runs on the pydantic-ai Agent

Until November 2026 Evokai drove its own loop: `sessions/turns.py` called
`pydantic_ai.direct.model_request_stream` step by step and ran the tools itself, `sessions/runs.py` did the
same with `model_request`. The loop was hand-written on day one, before pydantic-ai had deferred tool
approvals, capabilities with per-attempt hooks and a fallback that can be steered from a hook. It now runs on
pydantic-ai's `Agent` (2.69) and the [pydantic-ai-harness](https://github.com/pydantic/pydantic-ai)
capabilities plug into it, while everything an application sees stays the same: the session API, every event
and field, replay, the clients and the console.

This page says, feature by feature, what pydantic-ai does now and what stays Evokai's own code, and why.

## The shape

```
turn / server run / eval / channel / workflow step / A2A / AG-UI / OpenAI-compatible
        │
        ▼
evokai.sessions.turns (streamed, events)  ·  evokai.sessions.runs (answer, schema)
        │  builds per turn: RunState (deps), tools, models, prompt
        ▼
pydantic_ai.Agent.iter(message_history=…, deferred_tool_results=…)
        ├── EvokaiCapability      caps · history view · cache settings · fallback · traces · usage
        ├── harness capabilities  Code Mode · compaction · subagents · the catalog (switched per agent)
        └── GuardedToolset( EvokaiToolset )   tool policy · checks · confirmation · spans
                 └── handlers: MCP as the user · client tools · knowledge · code · skills · handoff · memory
```

The module is `evokai.engine`: `state.py` (the run's deps), `tools.py` (toolset and guard), `capability.py`
(per-request rules), `messages.py` (pydantic-ai messages back to Evokai's blocks), `agent.py` (the Agent of
one turn or run).

The driver iterates the agent's graph node by node (`Agent.iter`): a model request node is streamed and its
text goes through the output filter to `text` and `say` events; a tool call node is run and its results are
stored; between nodes the driver decides where Evokai ends a turn (an answer, a confirmation, a question, the
tool-call cap, the output filter). Tools run one at a time
(`ToolManager.parallel_execution_mode("sequential")`, every tool `sequential=True`) so events keep the order
they had.

## Feature by feature

| Feature | On the Agent | Evokai's own code |
|---|---|---|
| The loop (model, tools, model) | `Agent.iter`, `ModelRequestNode.stream`, `CallToolsNode.stream` | where a turn ends; storing each step |
| Tools of a turn | one `EvokaiToolset` (an `AbstractToolset`) with every tool and its handler | the handlers: MCP client, connectors, client tools, knowledge, code, skills, handoff, memory |
| MCP as the user (`forward_user`), grants | unchanged: the toolbox is built per turn with the session's credential and the grants | `evokai.mcp.registry` (not pydantic-ai's MCP toolsets: Evokai's registry keeps per-user clients, connectors in process, name clashes, health) |
| Guard on tool calls | `GuardedToolset` (a `WrapperToolset`): `offered` filters `get_tools`, `check_call` before every call | `evokai.guard` |
| Guard on tool results | in the handlers (`tool_result`, spotlight, clip), and the history view marks and protects every result before each request | `evokai.guard` |
| Guard on input and output | input checked before the agent runs; the output filter runs on the streamed text in the driver | `evokai.guard.output` |
| Confirm before a mutating tool | the guarded toolset raises `ApprovalRequired` (also for a call an [ask rule](security.md#ask-rules) asks for); the run ends with `DeferredToolRequests`; the next turn passes `DeferredToolResults` (approved, or `ToolDenied` with the rejection text) | the waiting calls and the results gathered before them live in `evokai_pending_actions` (one-hour TTL with external storage); labels; the fixed refusal without a model call; «sì»/«annulla» by rule |
| A new message instead of confirming | | the waiting calls are answered «not run» (superseded) and stored before the new message, as before |
| History | `message_history` is built from the store (`to_model_messages`), the system prompt first | the provider-neutral block format, `window`, `repair`, `flatten_tools` |
| What the model sees | `EvokaiCapability.before_model_request` replaces the request's messages (not the run's history) with the windowed, guarded view, every step | `RunState.view` |
| Volatile system prompt (voice rule, memories, page) | dynamic `instructions` (after the Bedrock cache point) | `evokai.agents.prompts` |
| Prompt caching | `prepare_model_request` adds `evokai.llm.caching` settings for the model of that attempt only | `evokai.llm.caching` |
| No provider-side state | `prepare_model_request` passes every attempt's settings through `evokai.llm.policy.enforce_settings` last, so no capability can turn storage or chaining on; `tests/test_no_provider_storage.py` | `evokai.llm.policy` |
| Fallback model, only if nothing reached the user | `on_model_request_error` raises `RetryModelRequest(model=next)` on a capacity error when the step has shown nothing | the rule (`is_capacity_error`, the chain from grants and allow-lists). `ModelFallback`/`FallbackModel` hand a stream over at its first event, which is close but not the same: Evokai also forbids the switch once any text was shown, and needs its own span, log and health record per attempt |
| Caps before each call | `before_model_request` calls `RunState.before_call`, which raises `StopRun` (limit, budget) | `evokai.sessions.limits`, `evokai.core.caps` |
| Usage per call, with the model that answered | `after_model_request` (and the driver when the output filter stopped a stream early) | `usage` rows (kind, model, tokens, cached tokens, cost), the `usage` event with shares only |
| Traces | per attempt `model` spans from the capability; `tool` spans from the guarded toolset (nested: a call inside a program is a child of `run_code`) | `evokai.observability.tracing` |
| One turn, one cancellation | the whole `Agent.iter` runs as one task raced against the turn's cancel event: the model stream, the tool in flight and speech stop together | `TurnRegistry`, barge-in, superseded |
| Server runs, schema output | `StructuredDict` output with an output validator (JSON Schema, one retry with the errors) | the guard's final check, the size limit, the answer shape |
| Handoff | an ordinary tool of the toolset | `handoff`: the next turn is the other agent's |
| Subagents | harness `SubAgents`, each child an Evokai agent built per call: see [Subagents](#subagents) | read-only tools, depth 1, the cap per turn |
| Code Mode | harness `CodeMode` over the read-only tools, see [Code Mode](#code-mode) | the three switches, limits, usage, traces |
| Compaction | see [Compaction](#compaction) | Evokai's own: the summary in Evokai's database |
| Every other harness capability | the catalog adds the enabled ones to the Agent ([capabilities](capabilities.md)) | the three switches, host access, model resolution |
| Voice | | sentence splitting, spoken numbers, redaction, fillers, «sì»/«annulla», option picking: deterministic code, unchanged |
| External storage | the same engine; nothing is kept but the waiting calls | `ExternalThreads` |

## What changed, even slightly

- **The window is applied per request, on the run's own history.** Before, each step re-read the store; now the
  run starts from the stored conversation (twice the window) and the capability windows the run's history before
  every request. What the model sees is the same.
- **A tool the model invents** (not offered) gets pydantic-ai's retry prompt instead of «Unknown tool: …». It is
  stored as a failed tool result.
- **Fallback** happens on the same conditions (a capacity error, nothing shown in this step); a model that fails
  after the first streamed event without showing text (while writing a tool call) still falls back.
- **Skill loading in a server run** now passes the guard's call checks like every other tool.
- **Tool start/done events in server runs** (A2A, OpenAI-compatible streaming) come from the guarded toolset,
  so a call the guard blocks is reported as an error, as before.

## Capabilities

Every pydantic-ai-harness capability is in Evokai's catalog, off until the deployment, the application's grant and
the agent all turn it on ([capabilities](capabilities.md)). The engine builds the enabled ones per turn or run
(`TurnEngine.capabilities_for`) and adds them after `EvokaiCapability`, so Evokai's invariants hold whatever they do.
Code Mode, compaction and subagents are wired end to end; the others can be enabled and constructed, and the catalog
says what each still needs.

## Code Mode

The harness `CodeMode` gives the model one `run_code` tool: a Python program (Monty, in process) that calls tools as
functions, loops, filters and returns only what matters (`evokai.engine.code_mode`).

- The capability `code_mode`: off unless the deployment (`EVOKAI_CAPABILITIES`), the application's grant
  (`capability`/`code_mode`) and the agent (`capabilities: {code_mode: {enabled: true}}`) all turn it on, like
  `code_run`.
- Only read-only tools go inside `run_code` (MCP tools that do not change data, `skill_load`); they stay available as
  normal calls too. A tool an ask rule may ask for is not read-only (the toolset says so per turn), so it stays out.
  Tools that change data, client tools, knowledge search, handoff and code execution stay normal calls, with the
  usual confirmation.
- Every call inside a program goes through the guarded toolset (policy, checks, MCP as the user), is a child span of
  `run_code` in the trace, and is not counted against the turn's tool-call cap (`run_code` itself is). Tools run one
  at a time, so a program calls them as plain functions (no `await`); a JSON answer reaches the program as data.
- An MCP tool whose server declares an `outputSchema` in `tools/list` gets it as its return schema
  (`ToolDefinition.return_schema`), so the program sees its typed return instead of `-> Any`; the schema is not sent
  to the model with the tool's own definition. A call's `structuredContent`, when present and valid against that
  schema, is the result (its JSON is what the model reads and what the program indexes, through the guard like any
  result); content that breaks the schema is dropped and the call's text blocks are used (`evokai.mcp.client`).
- Limits tighter than the harness defaults: 10 s of execution per program, 128 MiB of memory, 50 tool calls per
  program (`EVOKAI_CODE_MODE_*`; an agent may set less); no filesystem or OS access (no `mount`, no `os_access`).

## Compaction

Evokai's own (`evokai.engine.compaction`), not the harness `SummarizingCompaction`: the harness keeps its summary in
the run's messages, and Evokai keeps it in its database. See [configuration](configuration.md#compaction). The summary
of the older part of a conversation is made by a model call recorded with its own usage kind (`summary`) and counted
against the caps, and stored in `evokai_conversation_summaries`, never at the provider. External storage keeps
today's windowing (nothing is kept at rest). A model call refused as too long is retried once with a compacted
history, if nothing reached the user yet. The summary prompt is adapted from the harness; context windows come from a
vendored models.dev snapshot.

## Subagents

An agent's definition may list `subagents`: agents of the same application it can call as tools, through the harness
`SubAgents` (`evokai.engine.subagents`). Each child is built per call with its own `RunState` in place of the
parent's deps (its tools and models, usage under its own name), sharing the parent's guard, caps and trace. See
[workflows](workflows.md#subagents-and-handoff).
