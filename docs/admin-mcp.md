# Operate Evokai from an AI agent

Evokai serves its own admin API as an MCP server, so an AI agent (Claude Code, an IDE assistant, an agent of your own)
can read and change an application the way a person does in the console: with the same rights, the same checks and
the same audit log. It is served at:

```
https://HOST/agent/mcp/admin
```

(`/agent` is the base path, `EVOKAI_BASE_PATH`.) The transport is Streamable HTTP, stateless, with JSON responses.

## What it exposes

The tools are generated from Evokai's OpenAPI, restricted to an explicit allow-list
(`src/evokai/mcp_admin/tools.py`): every exposed route is named there, and a route that is not listed is never a tool.

Every route tool takes an optional `application` (a slug): an admin key acts on its own application and may leave it
out; the console assistant's delegation token is valid on every application its login can see and names one on each
call (it reaches the route as the `X-Evokai-Application` header). `list_applications` lists the applications the
caller may act on, with the role on each.

| Area | Tools |
| --- | --- |
| Applications | `list_applications` |
| The application | `get_application`, `update_application` (a subset of the settings, below), `get_health`, `get_usage_summary` |
| Agents | `list_agents`, `put_agent`, `delete_agent`, `list_agent_versions`, `get_agent_version_stats`, `switch_agent_model`, `get_model_recommendations` |
| Evals and red team | `try_agent` (one message to an agent, read-only: nothing is kept, changes stop at their confirmation, paid from the eval budget), `list_eval_cases`, `put_eval_case`, `delete_eval_case`, `list_eval_runs`, `start_eval_run`, `get_eval_run`, `list_redteam_runs`, `start_redteam_run`, `get_redteam_run` |
| Prompts and skills | `list_prompts`, `put_prompt`, `list_skills`, `get_skill`, `put_skill`, `delete_skill`, `get_skill_file`, `put_skill_file`, `delete_skill_file` |
| MCP servers | `list_mcp_servers`, `put_mcp_server` (configuration only), `delete_mcp_server`, `list_connectors` |
| Grants and policy | `get_grants`, `change_grants`, `get_security_policy`, `get_ask_rules` |
| Traces and conversations | `list_traces`, `list_conversations` (lists only, never their contents) |

Left out on purpose, so no agent can reach them through this server: API keys (create, rotate, revoke), secrets and
capability credentials, per-user connections and messaging channels, console users and single sign-on, the security
policy and ask rules as changes, the audit log, personal data (erase, export, transcripts, trace contents), the
configuration export, publishing an older agent version (it could bring back a weaker security policy), and the
routes that unlock or reset a system application.

Some changes take only part of their body; any other field is refused before the request reaches the route:

- `update_application`: name, caps, models, conversation retention, voice engine, locale, time zone, messages, eval
  settings, memory, red team gate, cost alert thresholds. Never the eval credential, the cost alert webhook or its
  secret, allowed origins, storage mode, residency and data protection, or the retention of the audit log and
  security events.
- `put_mcp_server`: url, auth, timeout, tool prefix, connector and its configuration. Never a secret (a person sets it
  in the console) and never a capability worker.

Reads are annotated `readOnlyHint`; every other tool is annotated `destructiveHint`, so an agent that honours MCP
annotations (Evokai's own agents do) asks the person before running it.

## Roles

A tool calls its route in process, with the caller's `Authorization` header. What the route allows is what the
caller gets ([roles](security.md)):

- an **admin key** acts as the owner of its application;
- the console assistant's **delegation token** acts with the role of the console login behind it (viewer, operator
  or owner), checked again on every call.

`tools/list` shows only the tools the caller's role may use: a viewer sees reads only, an operator does not see the
owner's settings. A call made anyway is refused by the route itself (`403 role`). Without a valid bearer the server
answers `401` before any MCP message is read; a server key is refused.

The audit log records changes made through the server as actor kind `mcp`, with the key that made them
(`mcp:key:<prefix>`), and changes by the console assistant as kind `assistant`, with the person it acted for.

## Connect an agent

Create an admin key on the **Keys** page of the application (the page shows the address and the command below), then:

**Claude Code**

```bash
claude mcp add --transport http evokai-admin https://HOST/agent/mcp/admin \
  --header "Authorization: Bearer evk_adm_..."
```

**Any MCP client (Streamable HTTP)**

```json
{
  "mcpServers": {
    "evokai-admin": {
      "type": "http",
      "url": "https://HOST/agent/mcp/admin",
      "headers": { "Authorization": "Bearer evk_adm_..." }
    }
  }
}
```

A raw request, to check the connection (with the key in `EVOKAI_ADMIN_KEY`):

```bash
curl -s https://HOST/agent/mcp/admin \
  -H "Authorization: Bearer $EVOKAI_ADMIN_KEY" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Security notes

- An admin key is the owner of its application. Give an agent the least it needs: a separate admin key per agent, so
  you can revoke it alone, and read the audit log for its `mcp:` entries.
- Everything an agent reads (agent descriptions, prompts, skill files, trace lists) may hold text written to steer it.
  Use an agent that asks before changes, and read what it proposes.
- The server adds no rights of its own: roles, validation, grants and the lock of a system application are the routes'
  own rules, so a new restriction on a route applies to its tool at once.
- Secrets never come back: MCP servers are listed with `hasSecret`, the application with `hasEvalCredential` and
  `hasCostAlertSecret`.
