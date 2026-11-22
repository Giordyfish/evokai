# Per-user connections: Microsoft 365, Google Workspace, GitHub, Linear, Notion, Slack

With a **delegated connector** the agent acts as the person in a third-party system: it reads *their* mail, *their*
calendar, *their* files, with *their* permissions, and drafts or creates only after they confirm. Each person connects
their own account once (OAuth 2.0 authorization code with PKCE); Evokai keeps their tokens encrypted, refreshes them,
and deletes them when the person disconnects, when the application revokes all of them, or when the person is erased.

| Connector | Provider | Families and tools | Writes (`writes: true`, always confirmed) |
| --- | --- | --- | --- |
| `microsoft_365` | Microsoft Graph | `mail`: `mail_search`, `mail_read`; `calendar`: `calendar_events`; `files`: `files_search`, `files_read` (OneDrive and SharePoint) | `mail_draft` (a draft in the person's Drafts folder, never sent), `calendar_create_event` |
| `google_workspace` | Gmail, Google Calendar, Google Drive | `mail`: `mail_search`, `mail_read`; `calendar`: `calendar_events`; `files`: `files_search`, `files_read` (Docs, Sheets and Slides exported as text) | `mail_draft` (a Gmail draft, never sent), `calendar_create_event` |

GitHub, Linear, Notion and Slack have no connector of their own: their tools are the harness integrations of the same
name ([capabilities](../capabilities.md)), which act as the signed-in person with that person's connection. They have
no tool families: the provider is the whole grant, and the scopes are the read scopes, plus the write scopes when the
application's OAuth app has `writes: true`.

Every provider is a data definition in `evokai.connections.providers` (endpoints, scopes per family, extra
parameters, how a token is revoked, how the account is named); the flow, the vault and the refresh are the same code
for all of them.

| Provider | Authorization server | PKCE | Client auth at the token endpoint | Tokens | Revocation |
| --- | --- | --- | --- | --- | --- |
| `microsoft365` | Microsoft identity platform (per tenant) | S256 | secret in the form | expire in about an hour, refreshed | none per token (the refresh token ages out) |
| `google` | Google OAuth 2.0 | S256 | secret in the form | expire in an hour, refreshed (`access_type=offline`) | `oauth2.googleapis.com/revoke` |
| `github` | `github.com/login/oauth` (a GitHub App) | S256 | secret in the form; JSON only when asked, and a refusal is a 200 with `error` | a GitHub App's user tokens expire after 8 hours and refresh for 6 months; an OAuth App's never expire (no refresh: a 401 asks to reconnect) | `DELETE api.github.com/applications/{client_id}/grant` with the app's Basic auth (removes the whole authorization) |
| `linear` | `linear.app/oauth` | S256 | secret in the form | expire after 24 hours, refreshed (the refresh token rotates, 30 days) | `api.linear.app/oauth/revoke` |
| `notion` | Notion MCP's own server, `mcp.notion.com` (not the REST API's public integrations, whose tokens the MCP server does not take) | S256 (required) | secret in the form; tokens bound to `resource=https://mcp.notion.com/mcp` | expire after about 8 hours, refreshed (rotating, 180 days at most) | `mcp.notion.com/token` (RFC 7009, with the client's credentials) |
| `slack` | Slack's user-only flow, `slack.com/oauth/v2_user/authorize` and `oauth.v2.user.access` | S256 | secret in the form; a refusal is a 200 with `ok: false` | user tokens (`xoxp-`), never bot tokens; with token rotation on they expire after 12 hours and refresh, without it they last until revoked (a 401 asks to reconnect) | `auth.revoke` with the access token |

## Enable one

```yaml
mcp_servers:
  - name: m365
    connector: microsoft_365
    config:
      client_id: 11111111-2222-3333-4444-555555555555   # the Entra app registration
      client_secret_env: M365_CLIENT_SECRET              # its secret, from the environment
      tenant: contoso.onmicrosoft.com                    # or the tenant id; "organizations" for any work account
      families: [mail, calendar, files]                  # what is offered (and the scopes asked)
      writes: true                                       # mail_draft and calendar_create_event
  - name: google
    connector: google_workspace
    tool_prefix: google_                                 # two providers expose the same tool names
    config:
      client_id: 1234567890-abc.apps.googleusercontent.com
      client_secret_env: GOOGLE_CLIENT_SECRET
      families: [mail, calendar]

agents:
  - name: assistant
    mcp_servers: [m365]
    ...
```

GitHub, Linear, Notion and Slack are configured by the **application's OAuth app**, one per provider: the client id,
the client secret (encrypted with the deployment's key, write-only: never shown again, kept when omitted) and
`writes`. Set it on the console's **Connections** page, with `PUT /v1/admin/connections/{provider}/app`
(`{clientId, clientSecret?, writes}`, `DELETE` to remove; audited without the secret), or in the application file:

```yaml
oauth_apps:
  github: {client_id: Iv23liExample, client_secret_env: GITHUB_OAUTH_SECRET}
  linear: {client_id: "${LINEAR_CLIENT_ID}", client_secret_env: LINEAR_OAUTH_SECRET, writes: true}
  notion: {client_id: "${NOTION_MCP_CLIENT_ID}", client_secret_env: NOTION_MCP_SECRET}
  slack: {client_id: "1234.5678", client_secret_env: SLACK_OAUTH_SECRET}
```

The file's secret (`client_secret_env`, or `client_secret` with a `${VAR}`) is read at bootstrap and stored encrypted;
without a value the stored secret stays.

Then **grant the provider** (connections are off until granted): console **Permissions**, or

```yaml
grants:
  connections: {microsoft365: true, google: true, "microsoft365/files": false, github: true}
```

Grants are per provider (`microsoft365`, `google`, `github`, `linear`, `notion`, `slack`) and, for the two office
providers, per tool family (`microsoft365/mail`, `microsoft365/calendar`, `microsoft365/files`, and the same for
`google`); a family follows its provider. The MCP server and tool grants apply
as for any connector, and without the `confirm` client tool the write tools are not offered at all.

Set **`EVOKAI_PUBLIC_URL`**: the redirect URI is `{EVOKAI_PUBLIC_URL}/v1/oauth/callback` (for example
`https://<your host>/evokai/v1/oauth/callback`). The console's **Connections** page shows it, ready to copy.

## How a person connects

- **In the chat panel** (`@evokai/evokai-ui`): when a tool needs a provider the person has not connected, the turn shows
  a «Connect Microsoft 365» card (a `connect` event: `{provider, title, server}`); the button opens the provider in a
  new window. The panel's **Connections** view lists every provider with its state, account and last use, and a
  **Disconnect** button.
- **From the application's own interface**, with the session token:

  ```http
  GET    /v1/session/connections                       → {connections: [{provider, title, status, account, scopes, families, connectedAt, lastUsedAt}]}
  POST   /v1/session/connections/{provider}/start      {returnTo?} → {provider, authorizeUrl, expiresAt}
  DELETE /v1/session/connections/{provider}            → 204
  ```

  Open `authorizeUrl` in a popup or a new tab. After the provider, the browser comes back to the callback, which
  shows «connected, you can close this window» or redirects to `returnTo` (it must be on one of the application's
  allowed origins). Poll `GET /v1/session/connections` (or refresh it when the window closes).
- **From Teams or Slack** ([channels](../channels/README.md)): the card is a button with the authorize link, made for
  the person the channel mapped.
- **The application's backend** (server key): `GET /v1/connections?subject=` and
  `DELETE /v1/connections/{provider}?subject=`.

`status` is `connected`, `expired` (the provider refused a refresh, or refused the token with a 401: the person
connects again) or `not_connected`.

### The harness integrations

The `github`, `linear`, `notion`, `slack` and `google_workspace` integrations act as the signed-in person by default
(`acts_as: person`): they take the person's token from `ConnectionService.person(app, subject, provider)`, which
answers `{connected, token, title, server}` (refreshing the token when it is about to expire), or None when nobody is
signed in or the provider is not configured and granted. A person who has not connected gets the same `connect` card
as with Google; nobody else's token stands in. A 401 from the provider on a tool acting as the person (found in the
error or anything it wraps) goes to `ConnectionService.expired(...)`, which asks the person to connect again at their
next turn; the tool's span records `connection: expired`. A 401 through a shared account marks nobody's connection. `acts_as: application` (the application's own credential, a shared account) stays
the explicit opt-in.

## Security

- **State**: random, single use, valid ten minutes, kept server-side (in the shared state, Redis with several
  instances) together with the application, the subject, the provider, the session's conversation and the PKCE
  verifier. The browser never sees the verifier; a callback cannot be replayed or bound to another person.
- **PKCE S256** on every flow, and the client secret on the token request (confidential client). The client secret
  of a connector comes from the environment; the OAuth app's secret is encrypted in the database.
- **Tokens** are encrypted with the deployment's Fernet key (`EVOKAI_ENCRYPTION_KEY`), like MCP secrets; the client
  secret is read from the environment at each use and never stored. Access tokens are refreshed 90 seconds before they
  expire, once per person even with concurrent turns; a refused refresh marks the connection `expired`. A token the
  provider refuses (401) does the same.
- **Least privilege**: only the scopes of the configured families are asked, and write scopes only with `writes`.
  Tools act within what the person can see at the provider.
- **Confirmation**: `mail_draft` and `calendar_create_event` are annotated destructive: the turn always stops for the
  person's confirmation, and server runs (e-mail channel, back-office) never get them. Drafts are never sent by Evokai.
- **Revocation**: «Disconnect», the console's **Revoke all** and the person's erasure delete the tokens, and revoke
  them at the provider its way (the table above): Google, GitHub (the whole authorization of the app), Linear,
  Notion and Slack. Microsoft has no per-token revocation for delegated tokens, so the deleted refresh token simply
  ages out (90 days of inactivity), and an administrator can revoke the person's sessions in Entra if needed. A
  failed revocation is logged and never keeps the tokens.
- **Tokens without expiry** (a GitHub OAuth App, Slack without token rotation) stay usable until revoked; the first
  401 marks the connection `expired`.
- **Erase and export**: the export lists the person's connections (provider, account, scopes, dates; never tokens);
  the erase deletes them.

## What the owner must register

These registrations can only be made by the owner of the tenant, the Google Cloud project, or the GitHub, Linear,
Notion and Slack workspaces. Evokai is fully tested against fakes; nothing works against the real providers until they
exist. Every provider gets the same redirect URI, `{EVOKAI_PUBLIC_URL}/v1/oauth/callback` (the console's
**Connections** page shows it), one per environment (staging, production).

### Microsoft Entra (for `microsoft_365`)

1. **Entra admin center → App registrations → New registration**
   - Name: e.g. «Inkwell Books Assistant».
   - Supported account types: **Accounts in this organizational directory only** (single tenant; set `tenant` to the
     tenant id or domain), or **Accounts in any organizational directory** for several customers' tenants (set
     `tenant: organizations`).
   - Redirect URI: platform **Web**, `{EVOKAI_PUBLIC_URL}/v1/oauth/callback`
     (e.g. `https://inkwell-books.example/evokai/v1/oauth/callback`). Add one per environment (staging, production).
2. **Certificates & secrets → New client secret**; put its value in the secret store as the variable named by
   `client_secret_env` (e.g. `M365_CLIENT_SECRET`). Note its expiry (24 months at most) and rotate before it.
3. **API permissions → Add a permission → Microsoft Graph → Delegated permissions** (only what the families need):

   | Family | Read | With `writes` |
   | --- | --- | --- |
   | always | `openid`, `profile`, `email`, `offline_access`, `User.Read` | |
   | `mail` | `Mail.Read` | `Mail.ReadWrite` (drafts) |
   | `calendar` | `Calendars.Read` | `Calendars.ReadWrite` |
   | `files` | `Files.Read.All`, `Sites.Read.All` | |

   None of these need admin consent by default, so each person consents for themselves the first time. If the tenant
   blocks user consent (common in companies), an administrator clicks **Grant admin consent for <tenant>** once.
   Application permissions are **not** needed: everything acts as the signed-in person.
4. Copy the **Application (client) ID** into `client_id` and the **Directory (tenant) ID** into `tenant`.

### Google Cloud (for `google_workspace`)

1. **Google Cloud console → APIs & Services → Library**: enable the **Gmail API**, **Google Calendar API** and
   **Google Drive API** (only those of the configured families).
2. **OAuth consent screen**
   - User type: **Internal** for a Google Workspace organization (only its users; no Google verification needed),
     or **External** for any Google account.
   - Scopes (only what the families need):

     | Family | Read | With `writes` |
     | --- | --- | --- |
     | always | `openid`, `email`, `profile` | |
     | `mail` | `https://www.googleapis.com/auth/gmail.readonly` | `https://www.googleapis.com/auth/gmail.compose` |
     | `calendar` | `https://www.googleapis.com/auth/calendar.readonly` | `https://www.googleapis.com/auth/calendar.events` |
     | `files` | `https://www.googleapis.com/auth/drive.readonly` | |

   - External apps: `gmail.readonly`, `gmail.compose` and `drive.readonly` are **restricted** scopes: publishing the
     app needs Google's verification and a yearly security assessment. Until then, keep it in **Testing** and add the
     testers' addresses (refresh tokens of testing apps expire after 7 days).
3. **Credentials → Create credentials → OAuth client ID**, type **Web application**, authorized redirect URI
   `{EVOKAI_PUBLIC_URL}/v1/oauth/callback`. Copy the client id into `client_id` and put the secret in the variable
   named by `client_secret_env`.

Then: set the variables in the service's environment (ECS secrets), register the connector, grant the provider, and
try it from the playground: ask the agent to read your mail and press the card.

### Register the OAuth app: GitHub

1. **GitHub → Settings → Developer settings → GitHub Apps → New GitHub App** (under the organization's settings for
   an organization-owned app). A GitHub App, not an OAuth App: its permissions are fine-grained and its user tokens
   expire and refresh.
   - Callback URL: `{EVOKAI_PUBLIC_URL}/v1/oauth/callback` (add one per environment).
   - **Expire user authorization tokens**: on. **Request user authorization (OAuth) during installation**: off.
     **Webhook**: off.
   - Permissions (the scope parameter is ignored for a GitHub App; these are its scopes). Read only by default:
     Repository **Contents**, **Issues**, **Pull requests**, **Metadata**: read-only; Account **Email addresses**:
     read-only. If agents may change data (`writes: true`): **Issues** and **Pull requests** read and write (and
     **Contents** read and write only if they must push).
   - Where can this app be installed: **Only on this account** for one organization.
2. **Generate a new client secret**; copy the **Client ID** and the secret into the console's Connections page (or
   `oauth_apps.github`). Install the app on the organization and choose the repositories it may reach: a person's
   token never reaches more than both the app and the person can.
3. An OAuth App also works (tokens without expiry, the `read:user read:org` scopes, and `repo` with writes, which is
   read and write on every private repository: prefer the GitHub App).

### Register the OAuth app: Linear

1. **Linear → Settings → API → OAuth applications → New** (a workspace admin).
   - Callback URLs: `{EVOKAI_PUBLIC_URL}/v1/oauth/callback`.
   - Public: off (only this workspace), unless several customers' workspaces connect.
2. Copy the **Client ID** and **Client secret** into the console (or `oauth_apps.linear`).
3. Scopes are asked by Evokai: `read`, and `write` with `writes: true`. Access tokens last 24 hours and are refreshed;
   the hosted Linear MCP server takes them as bearer tokens.

### Register the OAuth app: Notion

The hosted Notion MCP server (`mcp.notion.com/mcp`, the one the `notion` integration uses) only takes tokens from its
own authorization server: a public integration of the REST API (`api.notion.com/v1/oauth`) does not work there. Its
clients are registered with dynamic client registration, once per environment, from a terminal:

```bash
curl -s https://mcp.notion.com/register -H 'Content-Type: application/json' -d '{
  "client_name": "Inkwell Books Assistant",
  "redirect_uris": ["https://inkwell-books.example/evokai/v1/oauth/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "client_secret_post"
}'
```

Copy the returned `client_id` and `client_secret` into the console (or `oauth_apps.notion`); keep the response, the
secret is not shown again. There are no scopes: each person picks the workspace on Notion's consent screen and Notion
applies their own permissions; `writes` only matters to the integration's `read_only` filter. Business and Enterprise
workspaces may restrict which MCP clients are allowed (Notion's workspace settings, Notion AI, MCP): allow this one.

### Register the OAuth app: Slack

Only internal apps (or apps published in the Slack Marketplace) may use Slack's MCP server.

1. **api.slack.com/apps → Create New App → From scratch**, in the company's workspace.
2. **OAuth & Permissions**
   - Redirect URLs: `{EVOKAI_PUBLIC_URL}/v1/oauth/callback`.
   - **User Token Scopes** (not bot scopes). Read: `search:read.public`, `search:read.private`, `search:read.im`,
     `search:read.mpim`, `search:read.files`, `search:read.users`, `channels:history`, `groups:history`,
     `im:history`, `mpim:history`, `channels:read`, `groups:read`, `im:read`, `mpim:read`, `users:read`,
     `files:read`, `canvases:read`. With `writes: true` also `chat:write`, `canvases:write`, `reactions:write`.
   - **Token rotation**: on is recommended (tokens expire after 12 hours and are refreshed); without it a token lasts
     until revoked.
3. If the workspace restricts app installs, an admin approves the app (the usual Slack app approval). If the app has
   an IP allowlist (**OAuth & Permissions → Restrict API Token Usage**), add the service's egress addresses.
4. **Basic Information → App Credentials**: copy the **Client ID** and **Client Secret** into the console (or
   `oauth_apps.slack`). Install the app in the workspace if the admins approve apps.

Then, for each of the four: grant the provider (Permissions), enable the integration on an agent with
`acts_as: person` (the default), and try it from the playground: ask for something there and press the card.
