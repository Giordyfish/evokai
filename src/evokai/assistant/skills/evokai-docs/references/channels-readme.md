# Channels beyond the web panel: e-mail, Microsoft Teams, Slack

An agent can be reached outside the application's web panel: people write to its **mailbox**, chat with it in
**Microsoft Teams**, or message it in **Slack**. Each channel is configured per application (console **Channels**, or
`PUT /v1/admin/channels/{kind}/{name}`), names the agent that answers, and keeps every secret in the environment.

| | E-mail | Teams | Slack |
| --- | --- | --- | --- |
| In | IMAP poll, or a signed webhook from an inbound mail provider | Bot Framework activities | Events API, interactivity |
| Verified by | the webhook's HMAC signature (or the mailbox login) | the Bot Framework's JWT (signature, issuer, audience, `serviceurl`) | `X-Slack-Signature` (HMAC with the signing secret, 5 minutes) |
| Runs as | a server run (input untrusted, read-only tools) | a turn (confirmations, questions, connection cards) | a turn |
| Out | a reply sent (SMTP or Graph) or a draft (IMAP or Graph) | a proactive reply, adaptive cards | a reply in the thread, buttons |
| Thread | `Message-ID`, `In-Reply-To`, `References` | the Teams conversation | the Slack thread (`thread_ts`) |
| Person → subject (default) | `hashed`: `email:<keyed hash of the address>` | `id`: `teams:<Entra object id>` | `id`: `slack:<team>:<user>` |

## Before a channel answers

1. **Grant the channel**: `email`, `teams` and `slack` are off until granted (console **Permissions**, or
   `grants: {channels: {slack: true}}`).
2. **Let the agent use it**: list it in the agent's `channels` (`channels: [browser, slack]`). An agent that does not
   list a channel is never run from it.
3. **Set `EVOKAI_PUBLIC_URL`**: the endpoints the providers call, shown by the console ready to copy, are
   `{EVOKAI_PUBLIC_URL}/v1/channels/{application slug}/{kind}/{channel name}/...`. They must be reachable from the
   internet (behind a proxy, forward `{base}/v1/channels/` while still blocking `{base}/admin`).
4. Teams and Slack conversations are kept in Evokai (`evokai` storage must be granted); an e-mail conversation is kept
   when the application's storage mode is `evokai`, else each message is answered on its own.

The console's **Test** button checks a channel without sending anything to anyone: the configuration, the environment
variables, the grant, the agent, and a login at the provider (IMAP and SMTP login, a Graph or Bot Framework token,
Slack's `auth.test`).

## Who is writing: mapping a person to a subject

Every channel has a `mapping`:

```yaml
mapping:
  strategy: hashed | id | webhook
  webhook_url: https://backoffice.internal/evokai/who # webhook only
  webhook_secret_env: CHANNEL_MAPPING_SECRET         # webhook only
  cache_seconds: 300
```

- `hashed`: an HMAC of the channel's id of the person with the deployment's signing key, stable and opaque. No
  address is stored as a subject.
- `id`: the id with the channel as a prefix.
- `webhook`: Evokai POSTs `{channel, channelName, id, name, email, tenant}` to the application, signed like workflow
  webhooks (`X-Evokai-Timestamp`, `X-Evokai-Signature: v1=<HMAC-SHA256 of "<timestamp>.<body>">`), and the
  application answers `{"subject": "u-123"}`, or `{"subject": null}` for someone it does not know (the person is told
  their account is not enabled). Use it when the agent's tools need the application's own user id. The URL is called
  as configured (it can be on the application's private network, like an MCP server).

A thread of a channel belongs to one subject: a reply, a card or a button from someone else never continues another
person's conversation.

## Limits and the guard

Each channel has `rate: {per_user_per_hour: 60, per_channel_per_minute: 120}`; beyond them a person is told to try
later (e-mail is dropped silently). The application's security policy (abuse limits, injection, personal data,
output filter) and caps apply to every run and turn, as in the web panel. Repeated deliveries (Slack and Teams retry,
mail providers redeliver) are handled once.

## E-mail

```yaml
kind: email
name: support
agent: desk
config:
  address: assistant@acme.example        # replies come from it; mail from it (and aliases) is never answered
  aliases: [help@acme.example]
  display_name: Acme Assistant
  inbound: imap                          # or webhook
  imap: {host: imap.acme.example, username: assistant@acme.example, password_env: MAILBOX_PASSWORD,
         folder: INBOX, poll_seconds: 60}
  # inbound: webhook
  # webhook_secret_env: INBOUND_MAIL_SECRET
  reply: draft                           # send | draft | none
  transport: smtp                        # or graph
  smtp: {host: smtp.acme.example, port: 587, starttls: true, username: assistant@acme.example,
         password_env: MAILBOX_PASSWORD}
  # graph: {tenant_id: ..., client_id: ..., client_secret_env: MAIL_APP_SECRET, mailbox: assistant@acme.example}
  drafts_folder: Drafts
  allowed_senders: ["@acme.example", partner@other.example]   # empty: anyone
  max_replies_per_sender_per_hour: 10
  attachments: {mode: input, max_count: 5, max_bytes: 5000000, max_chars: 20000}
  # attachments: {mode: knowledge, knowledge_base: inbox-documents}
```

- **IMAP**: the mailbox is polled (one instance at a time); messages are fetched with `BODY.PEEK` (never marked read);
  the first poll only notes where the mailbox is, so old mail is never answered.
- **Webhook**: the provider (or a small relay in front of it: SES, Postmark, Mailgun, SendGrid inbound parse) posts
  the raw RFC 822 message to `.../email/{name}/inbound` with `X-Evokai-Timestamp` and `X-Evokai-Signature`
  (as above, with `webhook_secret_env`); Evokai answers 202 and handles it in the background.
- **Untrusted input**: the message is the input of a server run on the `email` channel, so the guard scans it for
  hidden instructions and marks it as data (`untrusted.input: auto`), and only read-only tools are offered (nobody
  confirms in an e-mail). Attachments are always marked as data; their text is read (PDF, Word, PowerPoint, HTML,
  text, CSV) within `max_count`, `max_bytes` and `max_chars`, the others are only named. With `mode: knowledge` they
  are also added to a knowledge base (with care: everyone who can search that base can find them).
- **Threading**: the reply has `In-Reply-To` and `References` and `Re:` in the subject; a reply to it (any message
  whose `References` name a message of the thread, from the same person) goes on with the same conversation, without
  the quoted text.
- **Loop protection**: no reply to `Auto-Submitted` mail (other than `no`), `X-Autoreply` and kin, `Precedence:
  bulk|junk|list`, mailing lists (`List-Id`, `List-Unsubscribe`), delivery reports, an empty `Return-Path`, mailer
  daemons and no-reply senders, the channel's own addresses, Evokai's own replies (`X-Evokai-Channel`), and more than
  `max_replies_per_sender_per_hour` to one address. Replies carry `Auto-Submitted: auto-replied`.
- **Drafts**: with `reply: draft`, the answer is saved in the drafts folder (IMAP `APPEND` with `\Draft`, or a Graph
  draft) for a person to review and send.

What the owner registers: the mailbox and an app password (or an IMAP/SMTP account) in the secret store; for Graph,
an Entra app registration with **application** permissions `Mail.Send` and `Mail.ReadWrite`, admin consent, and an
application access policy that limits it to the agent's mailbox (`New-ApplicationAccessPolicy` in Exchange Online).

## Microsoft Teams

```yaml
kind: teams
name: desk
agent: desk
config:
  app_id: 11111111-2222-3333-4444-555555555555     # the bot's Microsoft App ID
  app_password_env: TEAMS_BOT_SECRET
  tenant_id: 99999999-8888-7777-6666-555555555555  # single-tenant bot
  allowed_tenants: []                              # default: the bot's own tenant
  service_url_hosts: [smba.trafficmanager.net, "*.botframework.com"]
```

- Every request to `.../teams/{name}/messages` carries a Bot Framework JWT: it is checked against the keys of
  `login.botframework.com` (signature, RS256, issuer `https://api.botframework.com`, audience `app_id`, expiry, the
  key's endorsement of `msteams`, and the `serviceurl` claim equal to the activity's `serviceUrl`). Anything else is
  401.
- People outside `allowed_tenants` (or the bot's tenant) are ignored; only Entra users (with an `aadObjectId`) are
  answered.
- The request is answered at once; the reply is posted later to the conversation (`{serviceUrl}/v3/conversations/...`,
  only on `service_url_hosts`) with a token the bot gets from Entra (client credentials, scope
  `https://api.botframework.com/.default`).
- Confirmations and questions are **adaptive cards**: `Action.Submit` buttons whose data comes back as the next
  message; a connection card is an `Action.OpenUrl` to the provider. A card pressed by someone else is that person's
  own message (their own conversation), so it confirms nothing.

What the owner registers (Microsoft Entra and Azure):

1. **Entra → App registrations → New registration**, single tenant, no redirect URI. **Certificates & secrets → New
   client secret**: its value goes in `TEAMS_BOT_SECRET`. Copy the client id (`app_id`) and the tenant id.
2. **Azure portal → Create → Azure Bot** (or deploy [`azure-bot.bicep`](azure-bot.bicep)): type of app **Single
   Tenant**, the app registration above, pricing tier F0 (Teams messages are free), **Messaging endpoint**
   `{EVOKAI_PUBLIC_URL}/v1/channels/{application}/teams/{channel}/messages` (the console shows it).
3. **Channels → Microsoft Teams → Apply** (accept the terms).
4. **Teams app**: fill [`teams-manifest.json`](teams-manifest.json) (`{{BOT_APP_ID}}`, names, URLs, the public host),
   add two icons (`color.png` 192x192, `outline.png` 32x32 transparent), zip the three files and upload it in the
   **Teams admin center → Teams apps → Manage apps → Upload** (or Developer Portal). Allow it in an app setup policy
   for the people who should see it.

## Slack

```yaml
kind: slack
name: team
agent: desk
config:
  signing_secret_env: SLACK_SIGNING_SECRET
  bot_token_env: SLACK_BOT_TOKEN          # xoxb-...
  team_id: T0123456                       # the workspace allowed to write (none: any)
```

- Every request to `.../slack/{name}/events` and `.../interactions` must carry `X-Slack-Signature`: `v0=` and the
  HMAC-SHA256, with the signing secret, of `v0:<X-Slack-Request-Timestamp>:<body>`, with a timestamp within five
  minutes. The URL verification challenge is answered.
- Direct messages to the app and mentions of it (`app_mention`) start or continue a conversation; the answer goes in
  the message's thread. Messages from bots (the app's own included), edits and other subtypes are ignored.
- Confirmations and questions are buttons; a pressed button is checked against the person's own thread, its message
  loses its buttons (`chat.update`), and the turn goes on. A connection card is a link button.

What the owner registers (Slack):

1. **api.slack.com/apps → Create New App → From an app manifest**, pick the workspace, paste
   [`slack-manifest.yaml`](slack-manifest.yaml) with the URLs the console shows.
2. **Install to Workspace** (a workspace admin may need to approve it). Copy the **Bot User OAuth Token** (`xoxb-`)
   into `SLACK_BOT_TOKEN` and **Basic Information → Signing Secret** into `SLACK_SIGNING_SECRET`.
3. When Slack verifies the event URL, the channel must already be saved in Evokai and the variables set.

Scopes: `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`. Add `users:read.email` only if a
mapping webhook needs the person's e-mail (the channel then has to look it up; not done by default).
