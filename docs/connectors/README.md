# Ready connectors

An application gives its agents tools through MCP servers. When the data lives in a database, a REST API, Google
Drive, SharePoint or a mailbox, it does not need to write a server: Evokai ships **connectors**, MCP servers it runs
itself, enabled by configuration.

| Connector | Reads | Writes (opt-in) | Guide |
| --- | --- | --- | --- |
| `sql` | Rows and grouped figures of allowed tables, named queries | Insert and update in `write_tables` | [sql.md](sql.md) |
| `openapi` | The GET operations you select from an OpenAPI document | The other selected operations | [openapi.md](openapi.md) |
| `google_drive` | Search and read files in allowed folders | None | [google-drive.md](google-drive.md) |
| `microsoft_files` | Search and read files in allowed SharePoint or OneDrive folders | None | [microsoft-files.md](microsoft-files.md) |
| `imap` | Search and read messages in allowed folders | None (never) | [imap.md](imap.md) |
| `microsoft_365` | The connected person's Outlook mail, calendar, OneDrive and SharePoint files | Mail drafts, calendar events | [connections.md](connections.md) |
| `google_workspace` | The connected person's Gmail, Google Calendar and Drive | Gmail drafts, calendar events | [connections.md](connections.md) |

The last two are **delegated**: each person connects their own account (OAuth with PKCE) and the tools act as them,
with their permissions; the others act with the application's own credentials.

## Enable one

In the application's config file, a connector is an MCP server with `connector` and `config` instead of `url`:

```yaml
mcp_servers:
  - name: crm
    connector: sql
    config:
      dsn_env: CRM_DATABASE_URL            # the URL comes from this environment variable
      tables: [public.customers, public.invoices]
      hidden_columns: {public.customers: [email, phone]}
      row_limit: 100

agents:
  - name: assistant
    model: eu.anthropic.claude-haiku-4-5-20251001-v1:0
    mcp_servers: [crm]
    instructions: Answer from the CRM. Never invent figures.
```

The same with the admin API (`PUT /v1/admin/mcp-servers/crm` with `{"connector": "sql", "config": {...}}`), or in
the console: **MCP servers**, **Add server**, kind **Ready connector**. `GET /v1/admin/connectors` lists the kinds
with the JSON Schema of their configuration; `evokai connectors list` prints the same.

## The rules every connector follows

- **Read-only by default.** Tools that change data exist only with `writes: true`. They are annotated destructive,
  so a turn stops with a `confirm` event and runs them only on the user's approval; server runs never get them.
- **Grants apply.** A connector is an MCP server of the application: the console's Permissions page grants or
  revokes it and each of its tools (`crm/query`), and without the `confirm` client tool its write tools are not
  offered at all.
- **Secrets only from the environment.** Keys ending in `_env` name environment variables of the service (from your
  secret store, e.g. ECS secrets). A configuration with a secret in clear (`password`, `token`, `secret`, `dsn`,
  `api_key`...) is refused, by the API and at start.
- **Bounded.** Each connector reaches only what you list (tables, operations and hosts, folders) and returns at most
  `max_result_chars` (12,000 by default) per call.
- **Fail soft.** A connector that cannot reach its system answers the model with an error message; the turn goes on.

## Try a configuration outside Evokai

```bash
# connector.yaml: {connector: sql, config: {dsn_env: CRM_DATABASE_URL, tables: [customers]}}
evokai connectors check connector.yaml          # validates it and lists its tools
evokai connectors serve connector.yaml --port 8790 --token-env CONNECTOR_TOKEN
                                                # a standalone MCP server (Streamable HTTP) at /mcp
```

`serve` is for another agent runtime or a quick test; inside Evokai connectors run in process.
