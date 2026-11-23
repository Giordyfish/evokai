# evokai-client

Python client for [Evokai](https://github.com/Giordyfish/evokai), the agents runtime. One package, `import evokai`:

| Class | Credential | What it is |
| --- | --- | --- |
| `EvokaiServer` / `AsyncEvokaiServer` | server key (and admin key for `.admin`) | Your backend: session tokens, server runs, conversations, memories, usage, privacy requests, the admin API. |
| `EvokaiSession` / `AsyncEvokaiSession` | session token | One subject, one agent, one conversation: chats streamed as typed events, confirmations, questions, replay, cancel, thumbs. |

It is the Python sibling of the TypeScript client (`@evokai/evokai-client`): same routes, same names in snake_case.
Built on `httpx` and `pydantic` v2, sync and async, fully typed (`py.typed`), Python 3.10 or newer.

`base_url` is where the service is mounted, base path included, e.g. `https://agents.example.com/agent`. Keys
never belong in a browser.

## Install

```sh
uv add evokai-client
# or
pip install evokai-client
# or, unreleased changes, from the repository:
uv add "evokai-client @ git+https://github.com/Giordyfish/evokai#subdirectory=clients/python"
```

## Backend: minting a session token

```python
import os

from evokai import EvokaiServer

evokai = EvokaiServer(
    os.environ["EVOKAI_URL"],  # https://agents.example.com/agent
    os.environ["EVOKAI_SERVER_KEY"],  # evk_srv_...
    admin_key=os.environ.get("EVOKAI_ADMIN_KEY"),  # evk_adm_..., only for evokai.admin
)

token = evokai.mint_session_token(
    "user-42",  # your own opaque id of the end user
    "assistant",
    tags=["team:ops"],
    forward_credential=request_jwt,  # forward_user MCP servers call your API as this user
    limits={"caps": [{"period": "week", "amount": 5}]},  # or subject_daily_usd (USD)
    voice_engine="browser",
)
# Hand the browser what it needs: token.token, token.expires_at, token.conversation_id,
# token.usage (shares of the caps, never amounts) and token.suggestions.
```

Every field of `POST /v1/session-tokens` is a keyword argument: `conversation_id` (continue a conversation),
`ttl_seconds`, `model`, `voice`, `voice_engine`, `storage_mode`, `consent`, `exit_code` (kiosks), `memory_consent`
(the person's answer to long-term memory, asked in your own interface; `token.memory` is their memory state).

With FastAPI, a token route is a few lines:

```python
from fastapi import Depends, FastAPI

from evokai import AsyncEvokaiServer

app = FastAPI()
evokai = AsyncEvokaiServer(EVOKAI_URL, EVOKAI_SERVER_KEY)


@app.post("/api/evokai/token")
async def evokai_token(user=Depends(current_user)):
    t = await evokai.mint_session_token(user.id, "assistant")
    return {"token": t.token, "expiresAt": t.expires_at, "conversationId": t.conversation_id}
```

## Running an agent from the server

An agent whose `channels` include `server` runs with no browser and no user:

```python
result = evokai.run(
    "mail-triage",
    "Classify this message",
    subject="customer-9137",
    attachments=[{"name": "mail.txt", "text": body}],
)
result.output  # the schema-valid JSON when the agent has an output_schema, else the answer text
result.model  # the model that answered
result.usage.cost_usd  # server to server: amounts are fine here
```

`input` can also be a conversation: `[{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}]`.
`store=True` keeps the run as a conversation of the subject.

## Streaming a session chat

```python
from evokai import AskEvent, ConfirmEvent, EvokaiSession, TextEvent

with EvokaiSession(EVOKAI_URL, token) as session:  # a token string or the minted SessionToken
    with session.chat("How many orders are waiting to ship?", page={"path": "/orders"}) as stream:
        for event in stream:
            match event:
                case TextEvent(delta=delta):
                    print(delta, end="", flush=True)
                case AskEvent(question=q, options=options):
                    print(f"\n{q} {options}")
                case ConfirmEvent(action_id=action_id, items=items):
                    pending = action_id
        print(stream.done.reason)  # end_turn, confirm, ask, limit, ...
```

- Iterating yields typed events (pydantic models): `conversation`, `text`, `say`, `tool`, `chart`, `diagram`, `ask`,
  `navigate`, `confirm`, `rewritten`, `usage`, `speech`, `error` and `done`. Each carries `turn_id` and `seq`. A type this client
  does not know yet arrives as `UnknownEvent` with every field in `event.data`, so a newer service never breaks you.
- `stream.turn_id` (from the `X-Turn-Id` header), `stream.text` (the deltas so far), `stream.done` and
  `stream.until_done()` help when you do not need every event.
- Leaving the `with` block (or `break`, or `stream.close()`) closes the connection, and the service cancels that
  turn. `session.cancel(turn_id)` cancels it explicitly and the stream still ends with `done`.
- If the connection drops before `done`, the stream completes itself from the replay endpoint
  (`GET /v1/session/events?after=seq`); after `replay_attempts` polls without `done` it raises
  `EvokaiConnectionError` with code `incomplete_turn`.
- Server-sent events are parsed by the client itself: comments (`: ping` every 10 seconds), multi-line `data:`,
  CR/LF/CRLF line ends and chunks that split anything, multi-byte characters included.

### Confirmations and questions

A tool that changes data never runs directly: the turn ends with a `confirm` event and `done` reason `confirm`.
Send the user's decision and stream the turn that resumes:

```python
for event in session.confirm(pending, approve=True):
    ...
```

`confirm.why[i]` says why `items[i]` waits: `key` is `confirm.why.writes` (a change), `confirm.why.policy` or
`confirm.why.rule` (an ask rule, with its `label` in the application's language).

The person may edit their last question and send it again while it is `rewritable` (on the stored user message, and on
every `done`): the last turn is replaced, and the stream starts with `conversation` and `rewritten`. A turn that
changed data cannot be rewritten (`EvokaiConflictError`, code `rewrite_after_write`):

```python
for event in session.rewrite("Show September instead"):
    ...
```

An `ask` event is answered by sending the chosen option as the next message:

```python
session.answer("The second one").until_done()
session.answer(["Rome", "Milan"])  # several options of a multiple-choice question: "Rome, Milan"
```

### Without streaming

```python
result = session.chat_json("Summarise my week")  # Accept: application/json, waits for the end
result.text, result.reason, result.events
```

Files go with `attachments=[...]`: paths, `(name, bytes)` pairs or `Attachment(name, data)`, when the agent takes them
(`session.info().attachments`, see the service's `docs/attachments.md`).

External storage (the application keeps the conversation): pass `history=[...]` (`HistoryMessage` or plain dicts)
to `chat`, `confirm`, `answer` or `chat_json`, and append `done.messages` (or `result.messages`) to your copy. A
rewrite (`session.rewrite(text, history=...)`, `chat_json(text, history=..., rewrite=True)`) says where its messages go:
drop your copy from `replaced_from` first.

### The rest of the session API

```python
session.info()  # conversation, agent, usage shares, memories, starter questions
session.conversation()  # stored messages (events typed), open confirmation, last seq, running turn
session.events(after=0)  # replay
session.feedback(message_id, 1)  # thumbs up (1), down (-1) or none (None)
session.cancel(turn_id)
session.report_bug("The chart is empty", {"page": "/reports"})
session.exit("1234")  # kiosk sessions
session.use_token(new_token)  # a refreshed token for the same conversation

# Long-term memory, as the person sees it
listed = session.memories()  # listed.memory (mode, consent, active, ask), listed.items (Memory)
session.memory_consent(True)  # a no deletes what was remembered; 409 memory_off when the mode is off
session.edit_memory(memory_id, "Prefers short answers")  # 400 memory_refused: instructions, secrets, personal data
session.forget_memory(memory_id)  # one; forget_memory() deletes everything. Returns how many.
```

`EvokaiServer.session(token)` gives a session that shares the server client's HTTP connection pool.

### Async

Every class has an async twin with the same methods:

```python
import asyncio

from evokai import AsyncEvokaiServer, TextEvent


async def main() -> None:
    async with AsyncEvokaiServer(EVOKAI_URL, EVOKAI_SERVER_KEY) as evokai:
        token = await evokai.mint_session_token("user-42", "assistant")
        async with evokai.session(token) as session, session.chat("Hi") as stream:
            async for event in stream:
                if isinstance(event, TextEvent):
                    print(event.delta, end="")


asyncio.run(main())
```

## Conversations, memories, usage, catalogue

```python
evokai.conversations.list("user-42", limit=10)
evokai.conversations.get(conversation_id, "user-42")
evokai.conversations.delete(conversation_id, "user-42")
evokai.memories.list("user-42")
evokai.memories.forget("user-42")  # everything, or memory_id="..." for one fact
evokai.memories.consent("user-42", False)  # the person's answer; False deletes what was remembered
evokai.agents()
evokai.voices(language="it-IT")
report = evokai.usage(group_by=["subject", "day"], from_="2026-11-01")
```

`usage()` returns amounts (server key only). People never see amounts: before usage reaches a browser, turn it into
shares of the caps with `to_usage_shares(report)`, which drops every amount.

## Privacy requests

The subject travels in the body, never in the address:

```python
data = evokai.privacy.export("user-42")  # everything Evokai keeps about them
erased = evokai.privacy.erase("user-42")  # right to be forgotten; idempotent
erased.conversations, erased.usage_deidentified
evokai.privacy.revoke_sessions("user-42")  # sign-out everywhere
```

## Admin API

```python
from evokai import AgentDef, AppPatch

admin = evokai.admin  # needs admin_key

admin.application.get()
admin.application.patch(daily_cap_usd=20, allowed_origins=["https://app.example.com"])
admin.application.patch(AppPatch(daily_cap_usd=None))  # None clears; unset fields are not sent

admin.agents.put(
    AgentDef(
        name="assistant",
        model="bedrock:eu.anthropic.claude-sonnet-4-5",
        instructions="You help the bookshop staff with orders and returns.",
        mcp_servers=["crm"],
        channels=["browser"],
    )
)
admin.agents.list()
admin.agents.versions("assistant")
admin.agents.delete("assistant")

admin.mcp_servers.put("crm", {"url": "https://crm.example.com/mcp", "auth": "forward_user"})
admin.mcp_servers.test("crm", credential=user_jwt)
admin.mcp_servers.connectors()

admin.skills.put("refunds", {"description": "How refunds work", "body": "..."})
admin.prompts.put("assistant", "You are ...")  # returns the version

admin.security.put({"mode": "enforce", "tools": {"deny": ["crm_delete_client"]}})
admin.security.events(control="input.", limit=50)

admin.grants.get()
admin.grants.change([{"kind": "model", "name": "openai:gpt-5", "value": "off"}])
admin.grants.change([{"kind": "code", "name": "*", "value": True}])  # code_run, off until granted

admin.compliance.put_profile({"controller": "Acme S.p.A.", "ai_act": {"annex_iii": []}})
admin.compliance.pack("gdpr", lang="it")                 # JSON, every statement with its sources
admin.compliance.pack("ai-act", format="html", lang="en")  # printable HTML (or "md")
snap = admin.compliance.snapshot("before the November release")
admin.compliance.diff(snap.id, admin.compliance.snapshot().id, lang="en")

admin.keys.rotate(key_id)  # the new key is shown only now
admin.usage_summary(from_="2026-11-01")
admin.activity(limit=20)
admin.export()  # the application as a config file
```

## Errors

Every failure raises an `EvokaiError` with `status`, the service's stable `code` and a `message` that is safe to show
(plus `fields` for invalid requests and `body` for the rest, e.g. `problems` of `not_granted`):

| Class | When |
| --- | --- |
| `EvokaiAuthError` | 401, 403 (`unauthorized`, `origin`, `channel`, `model_not_granted`, ...) |
| `EvokaiNotFoundError` | 404 |
| `EvokaiConflictError` | 409 (`closed`, `storage_mode`, `expired_conversation`, ...) |
| `EvokaiValidationError` | 400, 422 (`bad_request` with `fields`, `not_granted`, ...) |
| `EvokaiRateLimitError` | 429 (`limit`, `rate_limited`), with `retry_after` |
| `EvokaiServerError` | 5xx (`internal`, `invalid_output`) |
| `EvokaiConnectionError` | no answer (`network`, `timeout`) or a turn that ended without `done` (`incomplete_turn`) |

```python
from evokai import EvokaiError, is_expired_conversation

try:
    token = evokai.mint_session_token("user-42", "assistant", conversation_id=cid)
except EvokaiError as e:
    if is_expired_conversation(e):
        token = evokai.mint_session_token("user-42", "assistant")
    else:
        raise
```

## Models

Requests and answers are pydantic models with snake_case attributes; on the wire they keep the service's names.
Every model keeps fields it does not know yet in `model_extra`. Methods that take a body accept the model or a plain
mapping (attribute or wire names). A test checks the request models against the service's OpenAPI schema
(`clients/typescript/openapi.json`), so a renamed or newly required field fails CI.

## Development

```sh
cd clients/python
uv sync
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check
uv run pytest -q
uv build
```

`scripts/ci.sh python` runs the same gates from the repository root.

## License

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). "Evokai" and its logo are trademarks of Giordano
Pescetelli and are not licensed under the Apache License.
