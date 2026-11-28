# Modules

Evokai has two **modules** on a shared core. A deployment runs one of them or both, and switches them without a
restart.

| Module | What it is |
|---|---|
| `applications` | Agents, knowledge bases, workflows, channels, evals, prompts, conversations and sessions, the [OpenAI-compatible API](openai-compatible.md), voice, the [console assistant](assistant.md) and the admin MCP tools for those objects |
| `gateway` | [Gateways for coding tools](gateway.md): their people, providers, rules, limits and policy tests, and their entrance, the base URL door (`/gw/...`) |

The **core** always runs: sign-in, console users and roles, the system policy and every policy, security events, the
audit log, records, the console shell, privacy requests, compliance packs, health and retention.

## Turning modules on and off

`EVOKAI_MODULES` is a comma list of the modules that are on (`applications`, `gateway`, or
`applications,gateway`). When it is set it **pins** them: the console shows them read-only and a change from the API
is refused (409 `modules_pinned`).

When it is not set, a deployment owner switches them in the console, under **Modules** (`GET`/`PUT
/v1/console/modules` with `{"enabled": [...], "confirm": true}`). The console asks for a confirmation first, and every
change writes an audit row (`deployment.modules`, with the modules before and after). The choice is stored in the
deployment's settings table; until something is stored, both modules are on.

At least one module is always on. `EVOKAI_MODULES` naming none (or an unknown module) stops the service at start with
"Turn on at least one of applications, gateway"; the API refuses an empty choice (400 `no_module`).

## A module that is off

- **Its routes answer 404** with `{"error": {"code": "module_disabled", "message": "...", "module": "gateway"}}`. The
  routes stay mounted and a dependency checks the switch on each request, so a switch made in the console applies at
  once on that instance and within a few seconds on the others.
- **Its console section is hidden**: the navigation leaves out its pages, and a link to one says the module is off.
- **Its background jobs stop**: for the applications module, the knowledge ingestion resumes, workflow workers and
  scheduler, durable agent steps, mailbox polling, cost alerts and memory episodes. They start again when it is back on.
- **Its data stays.** Turned on again, everything is where it was. Retention and erasure keep running on the data of a
  module that is off (they are core jobs), and its rows are invisible to everything else.

Which routes belong to which module:

- **applications**: `/v1/admin/application`, agents, MCP servers, skills, prompts, keys, knowledge, workflows,
  channels, connections, evals, red team, traces, conversations, costs, grants, `/v1/session/*`, `/v1/session-tokens`
  and the server routes, `/v1/openai/*`, A2A, voice and speech, the console assistant, `/v1/operator/applications`.
- **gateway**: `/v1/admin/gateways*` and `/gw/*`.
- **core**: `/health`, the console's sign-in, `/v1/console/me` and `/v1/console/applications`, users and single sign-on,
  the system policy, `/v1/admin/policy/*`, `/v1/admin/security*`, the audit log, privacy, compliance, providers and
  data protection, `/v1/console/modules`.

## Purging a module

A module turned off keeps its data. To delete it for good, run on the server, with the deployment's configuration:

```bash
evokai module purge gateway            # counts, offers an export, asks you to type the module's name
evokai module purge gateway --dry-run  # counts only
evokai module purge gateway --export gateway.json --yes   # for scripts
```

The command refuses while the module is on. It prints what would go (rows per table and the live keys), offers to
export those rows to a JSON file (key hashes and encrypted secrets left out), and asks for confirmation (`--yes` skips
it). It then revokes the module's keys, deletes its rows (every row of that kind and everything that carries its id)
and writes one audit row (`module.purge`, by `cli:<user>`, with what was deleted). The audit log is core: its entries
about the purged rows stay until its own retention.

It also prints what lives outside Evokai and stays: for the gateway, the proxy's CA key, the MDM profiles that install
the certificate and the proxy settings, the providers' IP allowlists and the coding tools' settings; for the
applications, files in external stores and copies kept by client applications.

## For contributors: one table, two kinds

A gateway is an application row with `kind = "gateway"` (`evokai.db.kinds`): `application_id` runs through most of
the code, and splitting the table is not worth it. Instead, `applications_only(q)` and `gateways_only(q)` are the only
way to list or fetch rows by kind. They add the kind and, while that kind's module is off, hide its rows (retention,
erasure and the purge pass `even_off=True`). `tests/test_modules_separation.py` walks the source and fails when a query
over `Application` does not go through them, when code compares `Application.kind` itself, or when a caller asks the
repository for both kinds outside a short allow list of core places (audit, retention, roles, slugs shared by both
kinds).

Code that needs the switch uses `evokai.core.modules`: `MODULES`, `pinned()`, `await enabled(session)`,
`await is_enabled(session, name)` (a database session, the store or the services) and `require_module(name)`, the
FastAPI dependency that answers `module_disabled`.
