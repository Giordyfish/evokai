# Evals

Test cases per agent, run on demand and every time a new version is saved, compared with the version before. In the
console: an application's **Evals** tab.

## A case

An input, optionally the page the user would be looking at, and the checks its answer must pass:

| Check | Passes when |
| --- | --- |
| `contains` / `not_contains` | The answer has (or has not) a text (`case_sensitive`, default false). |
| `regex` | The pattern matches somewhere in the answer (`ignore_case`). |
| `json_schema` | The answer is JSON (the whole text, a fenced block or the first object) valid against the schema. |
| `tool_called` / `tool_not_called` | The agent called (or did not call) a tool; `args` (optional) must match one call's arguments. Client tools count: `ui_chart`, `ui_ask`, `ui_navigate`... |
| `rubric` | A judge model says the answer meets the rubric. **Off by default**: set the application's judge model (`eval_judge_model`); without one the check is skipped and does not count. |

A case passes when no check failed and the turn ended without an error. A case with a page sends it like a browser
does (`path`, `title`, `text`; a text alone is shown as the root page `/`).

## A run

Each case is a **real turn** of the agent, through the same engine, tools, security guard and client tools a person
gets, for a dedicated **test subject** (`eval:<agent>`) in a throw-away conversation that keeps nothing:

- read-only tools run; a tool that changes data stops the turn at its confirmation and **never runs** (the case's
  reason is `confirm`, and `tool_called` sees the call);
- MCP servers called as the user get the application's **test user's token** (`eval_credential`, stored encrypted),
  if one is set; without it their tools are simply not available;
- the conversation is deleted after the case; its usage stays, as kind `eval`.

A run compares the version under test with a **baseline**: on save, the version that was active before; on demand,
the version you pick (or none). It stores, for each case and version, every check's outcome, the answer, the tools
called, latency and tokens. The console shows the history (pass rate, its change, latency change, tokens of the new
version as a share of the baseline's, the share of the eval budget the run used) and each run's cases with the
answers side by side (a word diff).

At most 50 enabled cases run (by name); one run per agent at a time. On save the run is skipped while another one is
going, when the agent has no enabled case, or when the application turned it off (`eval_on_save: false`).

## Red team and model comparisons

Two more suites run on the same machinery: the built-in attack suite of every saved version, with its security score
and the publish gate ([red team](redteam.md)), and the agent's cases on the cheaper allowed models, for «the cheapest
model that passes» ([costs](costs.md#the-cheapest-model-that-passes)). Both spend the eval budget (tags
`evokai:redteam`, `evokai:models`).

## Budget

Eval usage is recorded as kind `eval` (it shows up in `GET /v1/usage?groupBy=kind`). It **never counts against the
caps of the application's users nor the application's own caps**: evals have their own monthly budget per
application, `eval_monthly_usd` (2 by default, a plain number on the console's Limits page). A run that reaches it
stops with status `limit`. People see only shares of it, never an amount.

## Settings (application)

| Setting | Default | Meaning |
| --- | --- | --- |
| `eval_monthly_usd` | 2 | The evals' monthly budget, apart from the caps |
| `eval_judge_model` | none | The model that judges `rubric` checks (within the allow-lists) |
| `eval_on_save` | true | Run an agent's cases when a new version is saved |
| `eval_credential` | none | A test user's token for MCP servers called as the user (write-only; `""` clears it) |

Set them with `PATCH /v1/admin/application` (owner) or on the console's Limits page.

## API (admin key or console)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/v1/admin/agents/{agent}/evals/cases` | The agent's cases |
| PUT / DELETE | `/v1/admin/agents/{agent}/evals/cases/{name}` | Create or replace (`{input, page?, checks, enabled}`), remove |
| POST | `/v1/admin/agents/{agent}/evals/runs` | Start a run now (`{version?, baseline?}`), 202 `{runId}`; 409 `eval_running` |
| GET | `/v1/admin/agents/{agent}/evals/runs` | History, newest first, and the budget used this month (share) |
| GET | `/v1/admin/evals/runs/{id}` | A run with every case's result per version |
| PUT | `/v1/admin/agents/{name}` | Saving a new version answers `evalRunId` when it started a run |

The TypeScript client has them as `admin.evals.cases`, `putCase`, `deleteCase`, `start`, `runs`, `run`.
