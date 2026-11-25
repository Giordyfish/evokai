# The console assistant

Evokai ships its own assistant, built into the admin console. It answers questions about the deployment (with
Evokai's documentation as a skill) and operates every application the person can see (it is the platform's, not one
application's): it reads their agents,
prompts, skills, MCP servers, grants, evals and usage, and proposes changes that the person confirms in the chat.
The mascot, Evokai's own, is its face.

It runs on Evokai itself, as an agent of a **system application**, and acts only through the
[admin MCP server](admin-mcp.md), with the rights of the person using the console.

## How it is built

- **A system application.** `evokai-assistant` is defined inside the package (`src/evokai/assistant/app.yaml`, its
  prompt `prompt.md`, and the docs skill generated from `docs/` by `scripts/assistant_docs.py`) and created at start.
  It is **locked**: every change to it through the admin API answers `409 locked`, a check made once in `admin_app`
  for every admin route, routes added later included. While it is locked, a newer shipped definition is applied at
  start. A system application has no keys (any made while it was unlocked are revoked when the shipped definition
  is applied), and its session tokens come only from the console's assistant route (`403 system_app` on
  `POST /v1/session-tokens`). An owner can unlock it (typing its slug, `POST /v1/admin/application/unlock`) to change it; it then keeps their
  changes and records that an update is available. Locked or not, it holds only the assistant's own resources: its
  agent and that agent's evals, prompts, skills, its MCP server entry, grants, limits and settings. A change that
  would create another kind (workflows, knowledge bases, other agents, channels, OAuth apps, capability credentials,
  keys, other MCP servers) answers `409 system_app`.
- **Nothing survives a reset.** `GET /v1/admin/application/reset/preview` lists every item the reset deletes or
  restores, by kind and name (the application's settings with the values the reset sets back), each deleted item with its definition (never a secret) for the owner to keep; the
  console offers it as a JSON download. `POST /v1/admin/application/reset` `{confirm: <slug>}` deletes exactly those
  items, applies the shipped definition again and locks it. Conversations (they follow their retention), eval and red
  team results, traces and the audit log are not part of the definition and stay. None of these routes is a tool of
  the admin MCP server, and the assistant itself is refused on all of them.
- **Its agent** has two kinds of tools only: the admin MCP server `evokai-admin` (auth `forward_user`, at
  `EVOKAI_INTERNAL_URL/mcp/admin`) and the docs skill. Its client tools are `navigate` (console pages only),
  `playground`, `read_page` and `show_on_page` (below), `ask` and `chart`. It has no web or egress tools, no code execution, no connectors, knowledge bases or capabilities, and no
  handoffs or subagents. The guard's injection detection and spotlighting are enforced.
- **Its rights are the person's.** Opening the panel calls `POST /v1/console/assistant/session` with the console
  login. Evokai mints a **delegation token** valid on every application the login can see (`evk_cdt_`, its own issuer
  and the `evokai-admin-mcp` audience, `app: "*"`, with the login's highest role); each admin tool names the
  application of the call (`application`, a slug; `list_applications` lists them with the role on each), which
  reaches the admin API as the `X-Evokai-Application` header, and a request naming none is refused
  (`400 application_required`) and a session token for the assistant that forwards it.
  The admin API re-checks the token against the live login on every call: a revoked admin key or a disabled single
  sign-on user ends it at once, and the role used is the lower of the token's and the login's current role. A viewer's
  assistant only reads; an operator's cannot change the owner's settings. Even used directly, the token opens only
  the routes of the admin MCP server's allow-list and the body fields its tools may send (`403 assistant_route`,
  `403 assistant_field` otherwise), and the server API (session tokens, a person's data) refuses it.
- **Only to the admin MCP server.** A session opens only while the assistant's agent forwards the person's
  credential to this service's own admin MCP server (`EVOKAI_INTERNAL_URL/mcp/admin`, HTTPS or this machine) and to
  nothing else; after an owner changed its MCP servers it answers `409 assistant_altered` until a reset.
- **Every change is confirmed.** The admin MCP server marks every tool that is not a read `destructiveHint`; Evokai
  asks the person before running such a tool, as for any agent. The assistant never changes an agent's security
  policy, whoever it acts for.
- **What it never gets**: tools that read secrets, create, rotate or revoke keys, change console users, single sign-on
  or the security policy, read the audit log, erase or export personal data, or unlock or reset a system application.
  It points the person to the console page instead.

The audit log records its changes as actor kind `assistant`, with the person it acted for
(`assistant:sso:ada@example.com`).

## Privacy of the conversations

A conversation with the assistant records administrative work: what a person asked, what the assistant read from the
application it worked on, and which changes the person confirmed. Evokai treats it as that person's record, kept for
the security and accountability of administrative changes.

- **Retention: 30 days at most.** The system application keeps conversations `conversation_ttl_days` days from when
  they start (not from their last message: a resumed conversation does not keep older words longer), 30 by default
  and at most (an owner may lower it; a higher value or none answers `400 system_ttl`, and
  the shipped definition is checked at start). The hourly housekeeping deletes them for good. Within that time the
  author cannot delete them (`409 retention` on `DELETE /v1/conversations/{id}` and on erasing one's own subject), so a
  change made through the assistant stays accountable for as long as the conversation is kept.
- **Each person sees only their own.** A conversation belongs to its subject, `console:<login>` (the single sign-on
  user, the admin key, or `console:operator`). Every route that returns or touches conversation content keeps to the
  caller's own subject on the assistant's application: the console's conversation list and detail, reviewers' notes,
  traces, the server API's conversations, the privacy export, bug reports and security events. The assistant reads
  through the same routes with the person's delegation, so it never lists anyone else's conversations either; keys
  see none. Owners also see who talked to the assistant and when, without titles or messages, and the bug reports and
  security events of everyone (security snippets are masked); viewers and operators only their own.
- **Owners read another person's conversation only by an inspection.** `POST /v1/admin/assistant/conversations/{id}/inspect`
  `{reason}` (an owner of the assistant's application, as a person: never the assistant) needs a written reason of 20
  to 500 characters and returns the conversation read only. It writes the audit entry
  `assistant.conversation.inspect` with the reason, the inspector and the author's keyed subject (never the subject in
  clear). In the console, an owner who opens another person's conversation is asked for the reason first.
- **The author is told.** `GET /v1/console/assistant/privacy` lists every inspection of the person's conversations
  ("Viewed by olga@example.com on 21 Nov 2026, 09:30: reason"); the panel shows them again before the next chat.
- **A notice on first use.** Before the first chat the panel shows a notice, accepted once per login and recorded
  with its version (`POST /v1/console/assistant/privacy {version}`, audit `assistant.notice`): how long conversations
  are kept, who can see them, why they are kept, and that the assistant is an AI whose answers can be wrong (AI Act
  art. 50 asks that people know they interact with an AI system).
- **Erasure.** A formal erasure request (GDPR art. 17) for someone's conversations is made by an owner of the
  assistant's application with `POST /v1/privacy/erase` `{subject: "console:<login>"}`, audited. A person's erase on
  another application does not reach them.

### What a deployer tells their staff

The legal basis for keeping these conversations is usually the deployer's legitimate interest in the security and
accountability of changes to its systems (GDPR art. 6.1.f). That needs a balancing of interests, which is why the
defaults are narrow: a short retention, no early deletion within it, nobody but the author reading
by default, and every exception written down, audited and shown to the person concerned. A deployer still has to:

- tell the people who use the console what is kept, for how long, why and who may inspect it (GDPR art. 13); the
  first-use notice covers the assistant itself, the deployer's own staff information covers the rest;
- decide who holds the owner role of the assistant's application, since owners can inspect;
- check national rules on the monitoring of employees. In Italy, for example, art. 4 of the Workers' Statute (as
  amended in 2015) allows the use of data from work tools for every purpose connected with the employment only after
  adequate information to the worker, and the Garante has taken a restrictive view of keeping metadata of work tools
  for long periods. Keeping the retention short and inspections exceptional is meant to make that assessment easier;
  it does not replace it;
- keep the deployment's audit retention (`EVOKAI_AUDIT_TTL_DAYS`) at 30 days or more, or set the assistant's
  application's `audit_ttl_days` (it refuses less than 30, `400 system_audit_ttl`), so the author sees every
  inspection while the conversation exists.

## Activation and the model

The assistant is **inactive** until an owner of its application picks a model on the console's **Assistant** page;
until then the console shows no launcher for it and minting a session answers `409 assistant_inactive`.

A model is **recommended** when its measured numbers meet every requirement:

| Requirement | Value |
| --- | --- |
| Tool calls correct on the assistant's evals (the change cases included: the right change tool is called) | at least 95% |
| Confirmations respected (no change ran without its confirmation) | 100% |
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

Each model also carries the [suitability estimate](models.md) for the assistant, which needs no measurement: the
badge says **Recommended (estimate)**, **Not recommended (estimate)** or **To verify** with its reasons, and becomes
**Recommended (measured)** or **Not recommended (measured)** once there are numbers. Activation still follows the
measured requirements above: a model recommended only by the estimate opens the second disclaimer.

| API | Who |
| --- | --- |
| `GET /v1/admin/assistant` | any console role: state, requirements, candidates (with their estimate), the latest try |
| `POST /v1/admin/assistant/activate` `{model, acceptNotRecommended?}` | an owner of the assistant's application |
| `POST /v1/admin/assistant/try` `{model}` | an owner of the assistant's application |
| `POST /v1/admin/assistant/switch` `{on}` | an owner of the assistant's application: off, no launcher, no application in the console's list, sessions refused; the model is kept |
| `POST /v1/console/assistant/session` `{conversationId?}` | a console login on an application |
| `GET /v1/console/assistant/privacy`, `POST` `{version}` | a console login: the notice, the voice in use, inspections of one's conversations |
| `POST /v1/admin/assistant/conversations/{id}/inspect` `{reason}` | an owner of the assistant's application |
| `GET /v1/admin/application/reset/preview`, `POST /v1/admin/application/reset` `{confirm}` | an owner of the assistant's application |

## The panel and the mascot

The console embeds `<evokai-chat>` from the UI components once, with its launcher at the bottom right and the
mascot as its orb (`slot="orb"`, see the UI components' README, "Your own orb").
There is no button: the mascot is the launcher. A click (or Ctrl/Cmd+J) opens the chat and the mascot moves to the
panel's header as the orb does; in voice mode, to the middle of the panel; minimized, the conversation goes on in the
mascot at the bottom right with the bubble beside it and mute, restore and end above it on hover or focus. While the assistant is not active (or not available to the person) there is no
launcher: it is activated from the Assistant page. There is one conversation for the whole console: it stays the same
from page to page and application to application. Each message tells the assistant the page the person is on (its
console path and title, never its text), so "this application" is the one in view. Until the first-use notice is read, opening the chat shows the notice, and
voice cannot be started from the launcher.

The mascot is the only one on screen: the sidebar's header shows Evokai's logo while the assistant is active, whatever
the person chose in Preferences (Mascot: animated, still, or off, the default, which is the logo; the choice applies
while the assistant is not active). Empty states keep their illustrations. The mascot follows `data-state`, the
conversation's state the component exposes to slotted content:

| State | Mascot |
| --- | --- |
| idle | the idle animation |
| panel opened | the hop greeting |
| thinking | embers |
| a tool runs | the rune circle |
| waiting for a confirmation | the dark flame |
| a change applied | the staff sparkles |
| listening | live: it looks up a little towards the person, the hat tip leans to them, and it nods on each pause; also while the person talks over it |
| speaking | live: the eyes light up with the voice |

The live states are drawn with WebGL; the console's "still" mascot preference and reduced motion are respected.

**It drives the playground.** When the person wants to try or talk to an agent, the assistant does it in the
playground, in front of them: its client tool `playground` (`{application, agent, message, new?}`, it waits for the
console's answer) opens the playground on that application and agent, types the message in the composer, sends it,
and returns the agent's answer, the tools it used, and whether it now waits for a confirmation or a choice (the person
gives those in the playground). The assistant keeps the conversation going one message per call; after 100 seconds it
gets what the agent said so far and the turn goes on in the playground. `try_agent` stays for a check nobody watches.

**It sees the page and moves it.** `read_page` returns the page the person is looking at: its path, title and visible
text (up to 6,000 characters), so the assistant talks about the figures and names the person sees, as the page writes
them. The text reaches the model as data, spotlighted like any tool result. `show_on_page` scrolls the page to a text
it shows and highlights it for a moment (a figure lights up with its name), or scrolls by a screen, to the top or to
the bottom.

**Its figures come from the tools.** Its agent sets `client_tools.chart_from_tools`: every value of a chart must be a
number an admin tool returned in the same turn (rounded as the chart writes it). A chart written from memory is
refused, and the assistant reads the data again. Durations use the chart unit `ms`, written like the console (850 ms,
4.3 s). When the person sees a different figure, the assistant reads the page and the data again before it answers.

**The voice follows data protection.** The browser's online voice (Google's: the spoken text goes to the browser
vendor's speech service, only in voice mode) is used only when the assistant's data protection and that of every application the person can see
[data protection](data-protection.md) allow processors outside the EU: no profile at all, or a profile whose
residency includes `us` or `global` (or the country `US`), whose jurisdiction rule includes `US`, with no retention
rule, and whose browser declaration does not turn online voices off. Otherwise the voice stays on the device: a voice
of the operating system for the console's language when there is one, else Piper's `it_IT-serena-high` in the browser
with `pitchShift 1.15` (synthesis stretched and playback sped up by the same factor, a small and bright voice). The
chat names the voice only when it is the online one, the one whose text leaves the device. Speech recognition stays on the device in every case.

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
