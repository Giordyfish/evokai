# The console assistant

Evokai ships its own assistant, built into the admin console. It answers questions about the deployment (with
Evokai's documentation as a skill) and operates the application the person is looking at: it reads its agents,
prompts, skills, MCP servers, grants, evals and usage, and proposes changes that the person confirms in the chat.
The mascot, Evokai's own, is its face.

It runs on Evokai itself, as an agent of a **system application**, and acts only through the
[admin MCP server](admin-mcp.md), with the rights of the person using the console.

## How it is built

- **A system application.** `evokai-assistant` is defined inside the package (`src/evokai/assistant/app.yaml`, its
  prompt `prompt.md`, and the docs skill generated from `docs/` by `scripts/assistant_docs.py`) and created at start.
  It is **locked**: every change to it through the admin API answers `409 locked`, a check made once in `admin_app`
  for every admin route, routes added later included. While it is locked, a newer shipped definition is applied at
  start. An owner can unlock it (typing its slug, `POST /v1/admin/application/unlock`) to change it; it then keeps their
  changes and records that an update is available. `POST /v1/admin/application/reset` applies the shipped definition
  again and locks it. Neither route is a tool of the admin MCP server, and the assistant itself is refused on both.
- **Its agent** has two kinds of tools only: the admin MCP server `evokai-admin` (auth `forward_user`, at
  `EVOKAI_INTERNAL_URL/mcp/admin`) and the docs skill. Its client tools are `navigate` (console pages only), `ask` and
  `chart`. It has no web or egress tools, no code execution, no connectors, knowledge bases or capabilities, and no
  handoffs or subagents. The guard's injection detection and spotlighting are enforced.
- **Its rights are the person's.** Opening the panel calls `POST /v1/console/assistant/session` with the console
  login. Evokai mints a **delegation token** for the selected application (`evk_cdt_`, its own issuer and the
  `evokai-admin-mcp` audience, valid on that application only) and a session token for the assistant that forwards it.
  The admin API re-checks the token against the live login on every call: a revoked admin key or a disabled single
  sign-on user ends it at once, and the role used is the lower of the token's and the login's current role. A viewer's
  assistant only reads; an operator's cannot change the owner's settings. Even used directly, the token opens only
  the routes of the admin MCP server's allow-list and the body fields its tools may send (`403 assistant_route`,
  `403 assistant_field` otherwise), and the server API (session tokens, a person's data) refuses it.
- **Every change is confirmed.** The admin MCP server marks every tool that is not a read `destructiveHint`; Evokai
  asks the person before running such a tool, as for any agent. The assistant never changes an agent's security
  policy, whoever it acts for.
- **What it never gets**: tools that read secrets, create, rotate or revoke keys, change console users, single sign-on
  or the security policy, read the audit log, erase or export personal data, or unlock or reset a system application.
  It points the person to the console page instead.

The audit log records its changes as actor kind `assistant`, with the person it acted for
(`assistant:sso:ada@example.com`).

## Activation and the model

The assistant is **inactive** until an owner of its application picks a model on the console's **Assistant** page;
until then the panel shows the activation screen and minting a session answers `409 assistant_inactive`.

A model is **recommended** when its measured numbers meet every requirement:

| Requirement | Value |
| --- | --- |
| Tool calls correct on the assistant's evals | at least 95% |
| Confirmations respected | 100% |
| Red team attacks resisted | at least 90% |
| Context window | at least 128k tokens |
| Streaming and tool calling | supported |

The numbers come from `src/evokai/assistant/models.yaml` (measured at release time per model id pattern by
`scripts/assistant_evals.py`) or from the owner's own try. The page shows each model the deployment allows with a
badge: **Recommended**, **Not recommended** or **Not measured**. Choosing a model that is not recommended opens a
second disclaimer that must be ticked (`acceptNotRecommended`).

**Try this model** (`POST /v1/admin/assistant/try`) runs the assistant's eval suite (`src/evokai/assistant/evals/`)
on one model with Evokai's evals runner. Each case is a real turn of the assistant, but its admin MCP server answers
from recorded responses (`recorded.yaml`): no application is touched, and changes stop at their confirmation as in
every eval. The suite covers tool choice and arguments, confirmation discipline, refusing excluded and locked
actions, and injection through what the tools return. The page shows the progress and the share of the eval budget
used, never an amount.

| API | Who |
| --- | --- |
| `GET /v1/admin/assistant` | any console role: state, requirements, candidates, the latest try |
| `POST /v1/admin/assistant/activate` `{model, acceptNotRecommended?}` | an owner of the assistant's application |
| `POST /v1/admin/assistant/try` `{model}` | an owner of the assistant's application |
| `POST /v1/console/assistant/session` `{conversationId?}` | a console login on an application |

## The panel and the mascot

The console embeds `<evokai-chat>` from the UI components, opened from the top bar. The mascot sits in the chat's
header (the voice stage in voice mode) and follows `data-state`, the conversation's state the component exposes to
slotted content:

| State | Mascot |
| --- | --- |
| idle | the idle animation |
| panel opened | the hop greeting |
| thinking | embers |
| a tool runs | the rune circle |
| waiting for a confirmation | the dark flame |
| a change applied | the staff sparkles |
| listening | live: the hat perks up, a rune appears at each syllable and orbits the hat, then the runes fly into the hat tip on a pause |
| speaking | live: the eyes light up with the voice |

The live states are drawn with WebGL; the console's "mascot still" and "hidden" preferences and reduced motion are
respected. The voice is the browser's: a Google voice when the browser has one (an online voice: the spoken text goes
to the browser vendor's speech service, only in voice mode), pitch 1.3, rate 1.05. Piper's neural voice can do the
same "small and bright" voice with `pitchShift` (synthesis stretched and playback sped up by the same factor).

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `EVOKAI_ASSISTANT` | `on` | `off` creates nothing at start (an existing assistant stays as it is) |
| `EVOKAI_INTERNAL_URL` | `http://127.0.0.1:{port}{base}` | where the service reaches itself, for the assistant's admin MCP server |

## Red team

The red team suite runs extra attacks on system applications (`ASSISTANT_SUITE` in
`src/evokai/evals/redteam/attacks.py`): an agent description or a prompt that tells the assistant to delete or unlock,
requests to show an MCP server's secret, create a key or turn the guard off, a chained request to unlock and then
act, and a tool output that claims the person already confirmed. A test runs the real assistant against the real
admin MCP server with a model that obeys an injected description: the turn stops at the confirmation and nothing is
deleted.
