# Workflows

A workflow is a list of steps Evokai runs on its own, durably: agents (server runs), tools of MCP servers and
connectors, decisions by people, branches, waits, parallel branches and signed webhooks. It is written as data (YAML
in the application's config file, or JSON through the admin API and the console's editor), never as code: conditions
and templates use a small, safe expression language with no access to Python.

Every run and every step's state is in Evokai's database. A run survives a restart, a step whose worker died runs
again on another instance, and a run waiting for a person can wait for days. This is the durability of LangGraph's
checkpoints or Temporal's activities, sized for what a back office needs; there is no visual editor (the console draws
the flow, read-only, next to the definition).

## A definition

```yaml
name: mail-triage              # lowercase letters, digits, - and _
title: Customer e-mail triage
description: What it does, for the console.
timezone: Europe/Rome          # schedules and dates; default the application's timezone, else UTC
retention_days: 90             # finished runs are deleted this many days after they started (default 90)
tags: [backoffice]             # usage tags of its agent steps
input_schema: {type: object, required: [text], properties: {text: {type: string}}}
triggers: [...]                # besides a manual start: schedule, webhook, event
steps: [...]                   # at least one
output: {reply: "{{ steps.draft.output.reply }}"}   # default: the output of the last step that ran
```

In the application's config file they go under `workflows:` (a list), next to `agents:`.

Every step has an `id` (lowercase, unique in the workflow) and a `kind`, and may set:

| Field | Meaning |
| --- | --- |
| `title` | A name for the console. |
| `next` | The step to go to afterwards: a later step of the same list, or `end`. |
| `timeout_seconds` | How long one attempt may take (default 300 for agents, 120 for tools, 30 for notify). |
| `retry` | `{max_attempts, backoff_seconds, max_backoff_seconds}`: attempts in all and the wait before the second, doubled each time. Agents, tools and notify make 3 attempts from 10 seconds by default; the other kinds run once. |
| `continue_on_error` | A step that fails for good does not fail the run: its output is null and the run goes on. |

Steps run in order. Jumps (`next`, a condition's `then` and `else`) only go forward in the same list, so every run
ends. The console and `POST /v1/admin/workflows/validate` refuse a backward jump, an unknown step, a duplicate id, an
approval that comes after the tool it authorises, an expression that does not parse, a bad cron or time zone, and an
agent or MCP server the application does not have, each with its path and its line in the YAML.

### Step kinds

| Kind | Fields | Output |
| --- | --- | --- |
| `agent` | `agent` (it must allow the `server` channel), `input` (a template, or `[{role, text}]`), `attachments` (`[{name, text, mediaType}]`), `output_schema` (JSON Schema of an object, default the agent's own), `groups` (knowledge access) | The structured answer, or the text |
| `tool` | `server`, `tool` (its own name on the server), `arguments` (templates), `approval` (the approval step that authorises it), `safe`, `label` and `label_values` (what the approval shows) | The tool's result (parsed when it is JSON) |
| `approval` | `question`, `summary` (lines; default the labels of the tool steps it authorises), `details` (facts shown with the question), `approvers` (`roles`, `groups`), `timeout_seconds` (default 7 days), `on_timeout` (`reject` or `approve`), `on_reject` (`end` or `continue`) | `{approved, decision, by, via, comment, at}` |
| `condition` | `if` (an expression), `then`, `else` (a later step or `end`; default the next step) | `{value}` |
| `wait` | `seconds`, or `until` (a template of an ISO date, read as midnight in the workflow's zone, or an ISO time) | `{until}` |
| `parallel` | `branches: [{name, steps}]` (2 to 10); jumps stay inside a branch, `end` ends the branch | `{<branch name>: output of its last step}` |
| `notify` | `url` (a template), `body` (default the run's id, workflow and step outputs), `secret_env` | `{status}` |

## Expressions and templates

A condition is an expression; a template is a string with `{{ expression }}` parts. A string that is exactly one part
keeps the value's type (`"{{ steps.status.output.customers }}"` is a list); otherwise each part is printed (objects as
JSON). Rendering is a single pass: a customer's message that contains `{{ ... }}` stays text.

| What | Syntax |
| --- | --- |
| Names | `input`; `steps.<id>.output` (and `.status`, `.error`); `run` (`id`, `workflow`, `version`, `subject`, `trigger`, `startedAt`); `step` (`id`, `key`, `attempt`); `trigger` and `event` (what started the run); `now` (`iso`, `date`, `time`, `month`, `previous_month`, `year`, `weekday`, in the workflow's zone) |
| Access | `a.b`, `a["b"]`, `list[0]`, `list[-1]`; a missing key or index is `null` |
| Values | numbers, `'text'` or `"text"`, `true`, `false`, `null`, `[a, b]` |
| Operators | `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`, `and`, `or`, `not`, `+ - * / %` (numbers; `+` also joins texts and lists), parentheses |
| Filters | `value \| name` or `value \| name(args)`: `length`, `lower`, `upper`, `trim`, `default(v)`, `json`, `string`, `join(sep)`, `first`, `last`, `round(n)`, `int`, `float`, `abs`, `sum`, `min`, `max`, `keys`, `date(format)` |

There is nothing else: no function calls but the filters, no attributes of Python objects (values are plain JSON), no
name that starts with an underscore, no loops, no `eval`. An expression is at most 500 characters and 24 levels deep,
and a value it builds at most 100,000 long. Filters bind tighter than operators: `steps.x.output.items | length > 0`.

`{{ step.key }}` is the step's idempotency key, the same at every attempt: pass it to a tool
(`requestId: "{{ step.key }}"`) so the system behind it can deduplicate a retried call.

## Triggers

| Start | How |
| --- | --- |
| By hand | The console's **Run now**, `POST /v1/admin/workflows/{name}/runs` (admin key or console), or the application's backend with its server key: `POST /v1/workflows/{name}/runs` with `{input, subject?, tags?, idempotencyKey?}` (202; the same idempotency key returns the first run). |
| `schedule` | `cron` (five fields, or `@daily`, `@monthly`...) read in the workflow's time zone: `0 9 * * 1` with `timezone: Europe/Rome` is nine on Monday morning in Rome, summer or winter. Each occurrence starts one run whatever the instances: the run's `trigger_key` is unique, so the instances race on one insert and one wins. An occurrence missed while the service was down still starts when it is at most a day old and came after the version was saved. |
| `webhook` | An outside system posts to `/v1/workflow-hooks/{application slug}/{workflow}` with `X-Evokai-Timestamp` (Unix seconds) and `X-Evokai-Signature: v1=<hex>`, the HMAC-SHA256 of `<timestamp>.<body>` with the secret held by the environment variable `secret_env`. A timestamp more than `EVOKAI_WORKFLOW_HOOK_TOLERANCE` seconds away is refused, and `X-Evokai-Delivery` makes a repeated delivery start no second run. The JSON body is the input unless the trigger maps one. |
| `event` | `event: agent_run.finished`, optionally `agent` and `status` (`done`, `failed`, `any`): a run of the agent from an application's backend (a server run, an A2A or OpenAI-compatible call) ended. The agent steps of workflows never fire it, so a workflow cannot start itself in a loop. |

A trigger's `input` maps what started the run (`trigger`, `event`, `now`) to the run's input, and `subject` names
whose data it handles. Every input is checked against `input_schema`: a manual or webhook start with a bad input is
refused (400 `invalid_input`), an automatic one is recorded as a failed run.

## Approvals

An approval step stops the run until a person decides. It carries the question, what will happen (`summary`, or the
labels of the tool steps it authorises, written like agents' `confirm_labels`: `{field}`, `{field|count}`,
`{field|map:table}` over the tool's arguments), facts (`details`), who may decide and until when.

- In the console, **Approvals** («Approvazioni») lists them with the question, the summary and the facts; approve or
  reject with a comment. A login may decide when its role on the application is one of the step's `approvers.roles` or
  above (default `operator`; deciding is a change, so a viewer never can).
- In the application's own interface (its back office), its backend lists them with the server key,
  `GET /v1/workflows/approvals?status=pending&group=backoffice`, and decides for one of its users with
  `POST /v1/workflows/approvals/{id}` and `{decision: "approve" | "reject", comment, by: {name, groups}}`. When the step
  names `approvers.groups`, the user must be in one of them (403 `not_approver`); the application vouches for the
  groups as it vouches for its users.
- A decision is taken once (409 `decided` afterwards), audited (`workflow.approval.decide`, with who and how), and the
  run goes on at once. When the time runs out, `on_timeout` applies, recorded as decided by `timeout`.
- A rejection ends the run as `rejected` (`on_reject: end`, the default), or lets a condition read
  `steps.<id>.output.approved` (`on_reject: continue`). A tool step whose approval was not given is skipped.

## Durability

| Property | How |
| --- | --- |
| State | `evokai_workflow_runs` (input, status, output, the definition's version), `evokai_workflow_steps` (one row per step: status, attempt, due time, lease, idempotency key, input, output), `evokai_workflow_approvals`. Nothing lives only in memory. |
| Leases | A worker takes a due step with one conditional `UPDATE` that sets its owner and a lease expiry: one worker of one instance wins. The lease is renewed while the step runs; the step's end and the next step's row are written in one transaction guarded by the lease, so a worker that lost its lease changes nothing. |
| Restarts | A step left running by a stopped process is due again when its lease expires (`EVOKAI_WORKFLOW_LEASE_SECONDS`) and runs on whichever instance takes it: at least once, with the same idempotency key. |
| Retries | A failure that may pass (a timeout, a tool error, a model without capacity, an unreachable webhook) waits `backoff_seconds`, doubled at each attempt, up to `max_backoff_seconds`; one that cannot (a refused address, a cap, a blocked call, a bad template) fails at once. |
| Timeouts | Each attempt of an agent, tool or notify step has one; approvals have their own. |
| Cancellation | `POST .../runs/{id}/cancel`: open steps and approvals are cancelled, a running step stops (on its instance at once, on another at its next lease renewal). |
| Resuming | `POST /v1/admin/workflows/runs/{id}/retry` resumes a failed or cancelled run from the steps that did not end well. |
| Versions | A changed definition is a new version (a hash of its content); a run keeps the version it started with to the end. |

Workers run in every instance (`EVOKAI_WORKFLOW_WORKERS` each; `EVOKAI_WORKFLOWS_ENABLED=false` turns them off in an
instance that should only serve requests). Redis is not needed: the database lease is the source of truth.

### The harness's durable execution backends

pydantic-ai-harness ships capabilities that make one agent run durable on an external engine (`absurd_durability`,
`pgtask_durability`, `azure_durable_task`, `aws_lambda_durability`, `render_workflows`), and pydantic-ai has agent
wrappers for Temporal and DBOS (installed with the `harness` extra). They all solve a narrower problem than Evokai's
workflows: they replay the model requests and tool calls an agent run already made when the engine runs the same task
again, so a crashed run does not pay twice. Evokai's workflows keep each step durable in Evokai's own database (leases,
retries, approvals that wait for days, versions); an `agent` step that dies runs again from its start with the same
idempotency key.

Each of them works only when the agent runs **inside a task of its engine**, started by that engine's worker; outside
one they are transparent (pydantic-ai's durability capabilities checkpoint nothing). Evokai builds its agent per turn
or run, in the service process, with deps that hold functions (the guard, the caps, the trace), so none of them can
make an Evokai turn or workflow step durable without their engine running Evokai's run as its task. They stay in the
catalog, off by default; an agent may enable one, and in a turn or server run it changes nothing.

| Backend | Engine | What a deployment needs to run Evokai's agent runs on it | Fit with Evokai |
|---|---|---|---|
| `pgtask_durability` | [pgtask](https://github.com/Kludex/pgtask), a task queue in Postgres | Postgres, `EVOKAI_WORKFLOW_DURABLE_AGENTS=true`; the tables come with migration `0026` | **wired**: `durable: true` on an `agent` step, see [durable agent steps](#durable-agent-steps) |
| `absurd_durability` | [Absurd](https://github.com/earendil-works/absurd), durable execution on Postgres | Absurd's schema and its worker, the agent run as an Absurd task handler | as pgtask, with Absurd's own engine |
| `azure_durable_task` | Durable Task with the Azure Durable Task Scheduler | a Task Hub and scheduler endpoint in Azure, a worker registering a user-written orchestrator whose activities are the run's model requests and tool calls (streamed responses are buffered in the activity, so a turn would lose streaming) | server runs only, and outside Evokai's workflow engine |
| `aws_lambda_durability` | AWS Lambda durable functions | a durable Lambda function in the project's region holding Evokai's code, network access to Evokai's database and the application's MCP servers, the handler starting the server run | a separate deployment of Evokai as a function; never for turns (they stream to the person) |
| `render_workflows` | Render Workflows | a Render Workflows app (`app` is that app's object, not a setting) and its service; `RENDER_API_KEY` | as Lambda, on Render |
| Temporal (`TemporalAgent`) | Temporal | a Temporal cluster and a worker; the agent and its deps must be serializable into the workflow: Evokai's per-turn deps are functions, so its runs would first have to be rebuilt from plain data inside the worker | not today |
| DBOS (`DBOSAgent`) | DBOS on Postgres | DBOS's system tables and its runtime in the process that runs the agent, the same deps constraint as Temporal | not today |

### Durable agent steps

An `agent` step with `durable: true` runs as a pgtask task (`evokai.agent_step`, queue `evokai`) in Evokai's own
Postgres, with the harness `PGTaskDurability`: each model request of the run (and each call of a harness tool) is
checkpointed in pgtask's tables, so a step whose instance died resumes from its last model call instead of paying for
it again. The step's idempotency key is the task's: a step Evokai retries after its lease expired finds the same task
and waits for it, and pgtask runs the task again on whichever instance's worker takes it (each instance runs one
worker, `EVOKAI_WORKFLOW_WORKERS` tasks at a time, with the workflow lease as pgtask's lease).

- It needs Postgres and `EVOKAI_WORKFLOW_DURABLE_AGENTS=true`; migration `0026` creates pgtask's tables in the
  `pgtask` schema with pgtask's own migrator (its bookkeeping in `_sqlx_migrations`). Elsewhere the step fails at once
  (`durable_unavailable`).
- Evokai's own tools (the application's MCP tools, read-only in a server run) are not checkpointed: they run again on
  a resumed step, as after Evokai's own retry. A replayed model call is not billed by the provider and its usage is recorded
  once: each call's record is keyed by the task and the call's number (`pgtask:<task>:model:<n>`), the same after a
  resume.
- A durable step does not stream (a server run never does) and keeps every other rule of a server run: grants, the
  guard, caps before each call, traces.

What Evokai provides instead: durable workflows ([durability](#durability)) for anything that must survive a restart,
`step_persistence` (a step log kept with the conversation, [capabilities](capabilities.md#stores)) for what an agent
run did, and confirmations and approvals that wait in the database.

## Security and limits

- Every step runs under the application: its grants, caps and security policy. Agent steps are server runs
  (`AgentRunner.run`, channel `server`) with the agent's guard and the application's monthly and daily caps (a cap
  stops the step for good, `limit`); usage is recorded under the run's subject (or `system:workflow:<name>`) with the
  tag `workflow:<name>`. People see a run's usage only as a share of the monthly cap (`capPct`), never an amount.
- Tool steps go through the granted toolbox (servers and tools the application is not granted are not there) and the
  policy's tool checks (deny lists, arguments, egress, rate). A tool that changes data (`destructiveHint`, not
  `readOnlyHint`, or forced to confirm by the policy) runs only after an approval decided by a person, or when the
  definition marks it `safe: true` **and** the application is granted `workflows.autonomous` (off by default; an
  approval decided by its timeout counts as autonomous too). Servers that act as a signed-in user (`forward_user`)
  cannot be used: a workflow has no user, so give it a server with `auth: secret` or a connector.
- `notify` calls only https or http addresses on a domain of `EVOKAI_WORKFLOW_NOTIFY_ALLOW` (empty: notify is off),
  resolved to public addresses only and pinned to the checked one, with no redirects and no cookies, as knowledge
  fetching does. With `secret_env` the body is signed like inbound webhooks, and `Idempotency-Key` is the step's key.
  Secrets come from the environment only.
- `workflows` (`*`) is itself a grant: revoked, no run starts (403 `workflow_not_granted`).
- Each run is a trace (kind `workflow`): a `workflow` root and one `step` span per step (kind, outcome, attempt, the
  agent run's id, the tool, the decision), with content as the application's `tracing_content` allows. The agent steps'
  own runs keep their traces, linked from the step. The trace goes to OpenTelemetry once, when the run ends.

## Privacy

- A run started for someone carries its `subject` (the application's own id). `POST /v1/privacy/export` includes the
  subject's runs; `POST /v1/privacy/erase` deletes them with their steps, approvals and traces.
- Finished runs are deleted `retention_days` after they started (default 90) by the hourly purge.
- With `pii.history` enforced, inputs, outputs, approvals and the trigger's payload are stored with personal data
  masked (`[EMAIL]`, `[CF]`...), and later steps read the masked values. The console shows content as
  `tracing_content` allows (`none` hides it).

## Handoff between agents

In a chat, an agent can hand the conversation to another agent of the same application. List the agents in its
definition:

```yaml
agents:
  - name: bookshop
    handoff_to: [returns]
```

The agent then has a `handoff(agent, reason)` tool. When it calls it, the conversation's agent changes: the next turn is
answered by the other agent, with the history so far, its own tools, prompt and security policy, within the
application's grants. The client receives a `handoff` event (`{from, to, title, reason}`), and the agent tells the user
in one sentence who takes over. Only agents that exist, take browser sessions and have a granted model are offered.
`GET /v1/session` names the conversation's current agent. If the new agent has no tools, earlier tool calls stay in
its history as text.

In a workflow, routing between agents is a condition on a classifier agent's structured output, as in the second
example below.

## Subagents and handoff

An agent can also call other agents of its application for one task and go on itself: list them in `subagents` and
turn the `subagents` capability on ([capabilities](capabilities.md)).

```yaml
agents:
  - name: bookshop
    subagents: [research]
    capabilities: {subagents: {enabled: true}}
```

The model gets `delegate_task(agent_name, task)`. Each call runs the named agent on the task in a fresh history, with
its own definition (active version, model chain, instructions, MCP tools, skills, knowledge), as the same person with
the session's forwarded credential, within the application's grants and the guard, and returns its answer. In v1 a
subagent gets only read-only tools (nobody could confirm a change inside a delegation), no client tools, voice,
handoff or code execution, and no subagents of its own; a turn makes at most `EVOKAI_SUBAGENT_CALLS` calls, each within
`EVOKAI_SUBAGENT_TIMEOUT_SECONDS`. Its model calls are usage of kind `subagent` under its own name, counted against
the caps; in the trace the call is a `delegate_task` span with the subagent's spans inside; cancelling the turn stops
it.

Which one to use:

| | Handoff | Subagent |
|---|---|---|
| Who answers next | the other agent, from the next turn on | the same agent, in the same turn |
| What the other agent sees | the whole conversation | only the task it is given |
| Its tools | all of its own, with confirmations | read-only ones |
| Good for | moving the person to a specialist | asking a specialist one question, several in a turn |

## Example 1: weekly overdue-returns reminder, with an approval

Every Monday at nine (Dublin time): read how many returns are overdue (a label was sent, no parcel came back); if
any, an agent drafts the reminder, a member of staff approves it, the reminders go out (the request id lets the
bookshop's back office deduplicate a retried call), and three days later the returns are checked again.

```yaml
name: overdue-returns-reminder
title: Overdue returns reminder
description: Every Monday, reminds the customers whose return is overdue, after a person approves the message.
timezone: Europe/Dublin
triggers:
  - kind: schedule
    cron: "0 9 * * 1"
    input: {since: "{{ now.date }}"}
input_schema:
  type: object
  required: [since]
  properties:
    since: {type: string, pattern: "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
steps:
  - id: status
    kind: tool
    server: bookshop-backoffice
    tool: returns_status
    arguments: {since: "{{ input.since }}"}
  - id: needed
    kind: condition
    if: "steps.status.output.overdue > 0"
    else: end
  - id: draft
    kind: agent
    agent: reminder-writer
    input: "{{ steps.status.output.overdue }} overdue returns from {{ steps.status.output.customers | length }} customers ({{ steps.status.output.customers | join(', ') }})."
  - id: approve
    kind: approval
    question: "Send the overdue returns reminder to {{ steps.status.output.customers | length }} customers?"
    details:
      message: "{{ steps.draft.output.text }}"
    approvers: {roles: [operator]}
    timeout_seconds: 259200
  - id: send
    kind: tool
    server: bookshop-backoffice
    tool: returns_send_reminders
    approval: approve
    arguments:
      since: "{{ input.since }}"
      message: "{{ steps.draft.output.text }}"
      requestId: "{{ step.key }}"
    label: "Send the overdue returns reminder of {since} to the customers concerned"
  - id: grace
    kind: wait
    seconds: 259200
  - id: recheck
    kind: tool
    server: bookshop-backoffice
    tool: returns_status
    arguments: {since: "{{ input.since }}"}
output:
  since: "{{ input.since }}"
  before: "{{ steps.status.output.overdue }}"
  after: "{{ steps.recheck.output.overdue }}"
```

`bookshop-backoffice` is an MCP server of the back office authenticated with a secret (`auth: secret`, `secret_env`),
whose `returns_send_reminders` is annotated as changing data: without the approval the step would fail with
`approval_required`. The approval shows «Send the overdue returns reminder of 2026-11-16 to the customers concerned».

## Example 2: customer e-mail triage, specialist agent, approval, reply

A customer's e-mail arrives (the shop's backend starts the run with `idempotencyKey: message-<id>`); a classifier
agent gives its category, priority and summary; a condition routes it to the orders specialist or to the general one;
the draft waits for a member of the `backoffice` group; once approved it is saved in the back office's `replies`
table through the `sql` connector (`writes: true`, `write_tables: [replies]`).

```yaml
name: mail-triage
title: Customer e-mail, specialist, approval, reply
description: Classifies a customer's e-mail, lets the right specialist agent draft the reply, and saves it once a person approves.
input_schema:
  type: object
  required: [messageId, text]
  properties:
    messageId: {type: integer}
    customer: {type: string}
    text: {type: string, maxLength: 20000}
retention_days: 90
tags: [backoffice]
steps:
  - id: classify
    kind: agent
    agent: mail-triage
    input: "Message {{ input.messageId }} from {{ input.customer }}: {{ input.text }}"
  - id: route
    kind: condition
    if: "steps.classify.output.category == 'order'"
    then: orders
    else: general
  - id: orders
    kind: agent
    agent: orders-specialist
    input: "Message: {{ input.text }} Summary: {{ steps.classify.output.summary }}"
    next: approve
  - id: general
    kind: agent
    agent: general-specialist
    input: "Message: {{ input.text }} Summary: {{ steps.classify.output.summary }}"
  - id: approve
    kind: approval
    question: "Send the reply to message {{ input.messageId }} (priority {{ steps.classify.output.priority }})?"
    details:
      summary: "{{ steps.classify.output.summary }}"
      draft: "{{ steps.orders.output.reply | default(steps.general.output.reply) }}"
    approvers: {roles: [operator], groups: [backoffice]}
    timeout_seconds: 172800
    on_timeout: reject
  - id: save
    kind: tool
    server: backoffice
    tool: insert_row
    approval: approve
    arguments:
      table: replies
      values:
        message_id: "{{ input.messageId }}"
        category: "{{ steps.classify.output.category }}"
        reply: "{{ steps.orders.output.reply | default(steps.general.output.reply) }}"
        created_by: "{{ steps.approve.output.by }}"
    label: "Save the reply to message {values.message_id} ({values.category}) in «{table}»"
output:
  category: "{{ steps.classify.output.category }}"
  reply: "{{ steps.orders.output.reply | default(steps.general.output.reply) }}"
```

The agents are server agents with an `output_schema`: `mail-triage` answers
`{category: order | shipping | other, priority: 1-3, summary}`, the specialists `{reply}`. The shop lists the
approval in its own back office and decides it for the member of staff:

```ts
const [a] = await evokai.workflows.approvals({ groups: ['backoffice'] })
// a.question: "Send the reply to message 1042 (priority 2)?"
// a.summary:  ["Save the reply to message 1042 (order) in «replies»"]
await evokai.workflows.decide(a.id, { decision: 'approve', by: { name: 'Ada Byrne', groups: ['backoffice'] } })
```

## Example 3: nightly anomaly report via webhook

At 02:30 every night yesterday's figures are read in two parallel branches, an agent looks for anomalies, and only when
it finds some they are posted, signed, to the back office's webhook (its domain must be in
`EVOKAI_WORKFLOW_NOTIFY_ALLOW`, the secret in `BOOKSHOP_ANOMALY_HOOK_SECRET`).

```yaml
name: nightly-anomaly-report
title: Nightly anomaly report
description: Every night, gathers yesterday's figures in parallel, asks an agent for anomalies and posts them to the back office's webhook.
timezone: Europe/Dublin
retention_days: 30
triggers:
  - kind: schedule
    cron: "30 2 * * *"
steps:
  - id: figures
    kind: parallel
    branches:
      - name: orders
        steps:
          - id: orders
            kind: tool
            server: backoffice
            tool: aggregate
            arguments: {table: orders, group_by: [title], metrics: [{fn: count, column: id}]}
      - name: returns
        steps:
          - id: returns
            kind: tool
            server: backoffice
            tool: aggregate
            arguments: {table: returns, group_by: [reason], metrics: [{fn: count, column: id}]}
  - id: analyse
    kind: agent
    agent: anomaly-analyst
    input: "Date {{ now.date }}. Orders per title: {{ steps.figures.output.orders | json }}. Returns per reason: {{ steps.figures.output.returns | json }}."
  - id: any
    kind: condition
    if: "steps.analyse.output.anomalies | length > 0"
    else: end
  - id: send
    kind: notify
    url: https://hooks.inkwell-books.example/evokai/anomalies
    secret_env: BOOKSHOP_ANOMALY_HOOK_SECRET
    body:
      date: "{{ now.date }}"
      anomalies: "{{ steps.analyse.output.anomalies }}"
      run: "{{ run.id }}"
```

The receiver checks the signature the way Evokai checks inbound webhooks:

```python
import hashlib, hmac, time

def genuine(secret: bytes, headers, body: bytes) -> bool:
    ts = headers["X-Evokai-Timestamp"]
    mac = hmac.new(secret, f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    fresh = abs(time.time() - int(ts)) < 300
    return fresh and hmac.compare_digest(headers["X-Evokai-Signature"], f"v1={mac}")
```

## API

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/admin/workflows` | console role or admin key | workflows with their runs of the last 30 days, next and last run, pending approvals |
| POST | `/v1/admin/workflows/validate` | console role or admin key | check a YAML or JSON text: problems with path and line, the flow |
| GET, PUT, DELETE | `/v1/admin/workflows/{name}` | console (operator to change) or admin key | definition, versions, webhook path; save a new version; retire |
| POST | `/v1/admin/workflows/{name}/runs` | console operator or admin key | run now |
| GET | `/v1/admin/workflows/runs[/{id}]` | console role or admin key | runs with filters; one run with its steps, approvals and flow |
| POST | `/v1/admin/workflows/runs/{id}/cancel`, `/retry` | console operator or admin key | stop, resume |
| GET, POST | `/v1/admin/workflows/approvals[/{id}]` | console role (operator to decide) or admin key | the queue; decide |
| POST | `/v1/workflows/{name}/runs` | server key | start a run |
| GET | `/v1/workflows/runs[/{id}]` | server key | runs; one run |
| POST | `/v1/workflows/runs/{id}/cancel` | server key | cancel |
| GET, POST | `/v1/workflows/approvals[/{id}]` | server key | approvals for the application's interface; decide for a user |
| POST | `/v1/workflow-hooks/{application}/{workflow}` | the webhook's signature | start a run from an outside system |

The TypeScript client has them as `server.workflows` (`start`, `runs`, `run`, `cancel`, `approvals`, `decide`) and
`server.admin.workflows` (`list`, `get`, `validate`, `put`, `delete`, `start`, `runs`, `run`, `cancel`, `retry`,
`approvals`, `decide`).

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVOKAI_WORKFLOWS_ENABLED` | `true` | Run steps and schedules in this instance (the routes stay on). |
| `EVOKAI_WORKFLOW_WORKERS` | `2` | Steps one instance runs at the same time. |
| `EVOKAI_WORKFLOW_POLL_SECONDS` | `1` | How often an idle worker looks for due steps (new steps in the same instance wake it at once). |
| `EVOKAI_WORKFLOW_LEASE_SECONDS` | `60` | How long a stopped worker's step stays leased before another instance resumes it. |
| `EVOKAI_WORKFLOW_SCHEDULE_SECONDS` | `20` | How often schedules are checked. |
| `EVOKAI_WORKFLOW_NOTIFY_ALLOW` | empty | Domains notify steps may call (a domain allows its subdomains); empty turns notify off. |
| `EVOKAI_WORKFLOW_HOOK_TOLERANCE` | `300` | Seconds an inbound webhook's timestamp may differ from now. |

## Limits

- No loops: jumps go forward. A workflow that must repeat starts again from a schedule or an event.
- At most 100 steps, 10 triggers, one webhook trigger, 10 branches per parallel step; a wait is at most a year.
- Agent steps use the agent's active version when they run: the workflow's version is pinned, the agents' are not.
- Masked storage applies to what later steps read too: with `pii.history` enforced, a template cannot recover a masked
  e-mail address.
