# `imap`: a mailbox

Search and read messages of an IMAP mailbox, in the folders you allow. Always read-only: folders are opened with
EXAMINE and messages fetched with `BODY.PEEK`, so nothing is marked as read, moved or deleted (`writes` is refused).

```yaml
mcp_servers:
  - name: mailbox
    connector: imap
    config:
      host: imap.example.com
      port: 993
      ssl: true
      username_env: SUPPORT_MAILBOX_USER       # or username: support@example.com
      password_env: SUPPORT_MAILBOX_PASSWORD   # an app password where the provider has them
      folders: [INBOX, Customers]
      max_messages: 20
      max_body_chars: 8000
```

Tools: `list_folders`, `search_messages` (`folder`, `text`, `from`, `since` as YYYY-MM-DD, `unseen`, `limit`;
newest first: uid, from, subject, date), `read_message` (`folder`, `uid`: sender, recipients, subject, date, text,
attachment names; HTML mail is read as text).

Search terms travel as IMAP quoted strings without line breaks, so a term cannot add commands. Messages carry
personal data: grant the server only to agents that need it, and keep `max_body_chars` small.
