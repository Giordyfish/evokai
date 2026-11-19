"""Channels beyond the web panel: e-mail, Microsoft Teams and Slack.

Deliveries are recorded or fake payloads, signed (or not) as their providers sign them; every
outgoing call (SMTP, IMAP, the Bot Connector, Slack's Web API) goes to a fake.
"""

from __future__ import annotations

import json
import time
from email.message import EmailMessage
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
import jwt
import jwt.algorithms
import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric import rsa

from evokai.channels import email as mail
from evokai.channels.config import EmailConfig, validate
from evokai.channels.service import strip_quoted
from evokai.channels.slack import sign as slack_sign
from evokai.core.errors import BadRequest
from evokai.db import models as m
from evokai.workflows.webhooks import sign as hook_sign
from tests.conftest import Env, Tool

ADMIN = "/agent/v1/admin"
BOT_ID = "bot-app-id"
SERVICE_URL = "https://smba.trafficmanager.net/emea/"


class Fakes:
    """The Bot Framework, Entra's token endpoint, Slack's Web API and a mapping webhook."""

    def __init__(self, fallback: Any) -> None:
        self.fallback = fallback
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "endorsements": ["msteams"]}]}
        self.teams_posts: list[tuple[str, dict, str]] = []
        self.slack_calls: list[tuple[str, dict, str]] = []
        self.mapping_calls: list[tuple[dict, dict]] = []
        self.mapping: dict[str, str | None] = {}

    def teams_token(self, **over: Any) -> str:
        claims = {
            "iss": "https://api.botframework.com",
            "aud": BOT_ID,
            "exp": int(time.time()) + 600,
            "nbf": int(time.time()) - 10,
            "serviceurl": SERVICE_URL,
            **over,
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "login.botframework.com":
            if path.endswith("openidconfiguration"):
                return httpx.Response(
                    200, json={"jwks_uri": "https://login.botframework.com/v1/.well-known/keys"}
                )
            return httpx.Response(200, json=self.jwks)
        if host == "login.microsoftonline.com":
            form = parse_qs(request.content.decode())
            assert form["scope"] == ["https://api.botframework.com/.default"]
            assert form["client_secret"] == ["bot-secret"]
            return httpx.Response(200, json={"access_token": "bot-token", "expires_in": 3600})
        if host == "smba.trafficmanager.net":
            self.teams_posts.append((
                path,
                json.loads(request.content),
                request.headers.get("authorization", ""),
            ))
            return httpx.Response(200, json={"id": "reply-1"})
        if host == "slack.test":
            method = path.rsplit("/", 1)[-1]
            body = json.loads(request.content) if request.content else {}
            self.slack_calls.append((method, body, request.headers.get("authorization", "")))
            if method == "auth.test":
                return httpx.Response(
                    200, json={"ok": True, "team": "Acme", "team_id": "T1", "user": "evokai"}
                )
            return httpx.Response(200, json={"ok": True, "ts": "1700000000.000200"})
        if host == "mapping.test":
            body = json.loads(request.content)
            self.mapping_calls.append((body, dict(request.headers)))
            return httpx.Response(200, json={"subject": self.mapping.get(body["id"])})
        return self.fallback(request)


class FakeSmtp:
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.checked = 0

    def send(self, _cfg: Any, msg: EmailMessage) -> None:
        self.sent.append(msg)

    def check(self, _cfg: Any) -> None:
        self.checked += 1


class FakeImap:
    """A mailbox: SEARCH, FETCH (peek), SELECT, APPEND."""

    def __init__(self, messages: dict[int, bytes] | None = None) -> None:
        self.messages = messages or {}
        self.appended: list[tuple[str, str, bytes]] = []
        self.fetched: list[str] = []

    def select(self, _mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        assert readonly
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code: str) -> tuple[str, list[bytes]]:
        return code, [b"7"]

    def uid(self, command: str, *args: Any) -> tuple[str, Any]:
        if command == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u in sorted(self.messages))]
        assert args[1] == "(BODY.PEEK[])"
        self.fetched.append(args[0])
        return "OK", [(b"x", self.messages[int(args[0])])]

    def append(self, folder: str, flags: str, _when: Any, data: bytes) -> tuple[str, list]:
        self.appended.append((folder, flags, data))
        return "OK", []

    def logout(self) -> None:
        pass


def run_input(env: Env) -> str:
    """The input the last run's model saw (its first user message)."""
    return str(getattr(env.script.seen[-1][1][0].parts[-1], "content", ""))


def raw_mail(
    *,
    sender: str = "Ada Lovelace <ada@customer.example>",
    to: str = "assistant@acme.example",
    subject: str = "Opening hours",
    body: str = "When are you open on Saturday?",
    message_id: str = "<m1@customer.example>",
    headers: dict[str, str] | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(body)
    for name, mime, data in attachments or []:
        main, _, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


@pytest.fixture
async def fx(env: Env, monkeypatch: pytest.MonkeyPatch) -> Fakes:
    for k, v in {
        "MAIL_HOOK_SECRET": "hook-secret",
        "SMTP_PASSWORD": "pw",
        "IMAP_PASSWORD": "pw",
        "BOT_PASSWORD": "bot-secret",
        "SLACK_SIGNING": "slack-signing",
        "SLACK_BOT": "xoxb-1",
        "MAPPING_SECRET": "map-secret",
    }.items():
        monkeypatch.setenv(k, v)
    fakes = Fakes(env.mcp.handler)
    env.svc.http._transport.handler = fakes.handler  # ty: ignore[missing-attribute]
    admin = {"Authorization": f"Bearer {env.admin_key}"}
    r = await env.client.put(
        f"{ADMIN}/mcp-servers/ops",
        json={"url": "http://app.test/mcp", "auth": "none"},
        headers=admin,
    )
    assert r.status_code == 200, r.text
    r = await env.client.patch(
        f"{ADMIN}/grants",
        json={
            "changes": [
                {"kind": "channel", "name": c, "value": True} for c in ("email", "teams", "slack")
            ]
        },
        headers=admin,
    )
    assert r.status_code == 200, r.text
    r = await env.client.put(
        f"{ADMIN}/agents/desk",
        json={
            "name": "desk",
            "model": "function:primary",
            "mcp_servers": ["ops"],
            "channels": ["email", "teams", "slack"],
            "instructions": "You answer the help desk.",
        },
        headers=admin,
    )
    assert r.status_code == 200, r.text
    return fakes


def _admin(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


EMAIL = {
    "address": "assistant@acme.example",
    "inbound": "webhook",
    "webhook_secret_env": "MAIL_HOOK_SECRET",
    "reply": "send",
    "transport": "smtp",
    "smtp": {"host": "smtp.acme.example", "username": "assistant", "password_env": "SMTP_PASSWORD"},
}


async def _channel(env: Env, kind: str, name: str, config: dict[str, Any]) -> dict[str, Any]:
    r = await env.client.put(
        f"{ADMIN}/channels/{kind}/{name}",
        json={"agent": "desk", "config": config},
        headers=_admin(env),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _mail(env: Env, raw: bytes, *, key: str = "hook-secret") -> httpx.Response:
    ts = int(time.time())
    r = await env.client.post(
        "/agent/v1/channels/test/email/support/inbound",
        content=raw,
        headers={
            "Content-Type": "message/rfc822",
            "X-Evokai-Timestamp": str(ts),
            "X-Evokai-Signature": hook_sign(key, ts, raw),
        },
    )
    await env.svc.channels.drain()
    return r


# configuration


def test_channel_configurations_refuse_secrets_in_clear_and_incomplete_setups() -> None:
    with pytest.raises(BadRequest) as e:
        validate("slack", {"signing_secret": "x", "signing_secret_env": "A", "bot_token_env": "B"})
    assert "signing_secret_env" in e.value.message
    with pytest.raises(BadRequest, match="smtp"):
        validate("email", {**EMAIL, "smtp": None})
    with pytest.raises(BadRequest, match="imap"):
        validate("email", {**EMAIL, "reply": "draft"})
    with pytest.raises(BadRequest, match="webhook_url"):
        validate(
            "teams", {"app_id": "a", "app_password_env": "B", "mapping": {"strategy": "webhook"}}
        )


async def test_the_console_lists_endpoints_and_environment_and_tests_without_sending(
    env: Env, fx: Fakes
) -> None:
    bad = await env.client.put(
        f"{ADMIN}/channels/slack/team",
        json={"agent": "desk", "config": {"signing_secret": "x", "bot_token_env": "SLACK_BOT"}},
        headers=_admin(env),
    )
    assert bad.status_code == 400
    row = await _channel(
        env,
        "slack",
        "team",
        {
            "signing_secret_env": "SLACK_SIGNING",
            "bot_token_env": "SLACK_BOT",
            "api_url": "https://slack.test/api",
        },
    )
    assert row["endpoints"]["events"].endswith("/agent/v1/channels/test/slack/team/events")
    assert row["endpoints"]["interactivity"].endswith("/channels/test/slack/team/interactions")
    assert {e["name"]: e["set"] for e in row["env"]} == {"SLACK_SIGNING": True, "SLACK_BOT": True}
    assert row["granted"] and row["agentAllows"]
    r = await env.client.post(f"{ADMIN}/channels/slack/team/test", headers=_admin(env))
    assert r.json()["ok"], r.json()
    assert [c[0] for c in fx.slack_calls] == ["auth.test"]  # nothing sent to anyone
    smtp = FakeSmtp()
    env.svc.channels.mailer = mail.Mailer(env.svc.http, smtp=smtp.send, smtp_check=smtp.check)
    await _channel(env, "email", "support", EMAIL)
    r = await env.client.post(f"{ADMIN}/channels/email/support/test", headers=_admin(env))
    assert r.json()["ok"] and smtp.checked == 1 and not smtp.sent
    listed = (await env.client.get(f"{ADMIN}/channels", headers=_admin(env))).json()
    assert {(c["kind"], c["name"]) for c in listed["channels"]} == {
        ("email", "support"),
        ("slack", "team"),
    }
    assert "hook-secret" not in json.dumps(listed)


# e-mail


@pytest.mark.usefixtures("fx")
async def test_an_email_is_answered_in_its_thread_as_untrusted_input(env: Env) -> None:
    smtp = FakeSmtp()
    env.svc.channels.mailer = mail.Mailer(env.svc.http, smtp=smtp.send)
    await _channel(env, "email", "support", EMAIL)
    unsigned = await env.client.post(
        "/agent/v1/channels/test/email/support/inbound", content=raw_mail()
    )
    assert unsigned.status_code == 401
    assert (await _mail(env, raw_mail(), key="wrong")).status_code == 401
    env.script.add("function:primary", ["We are open 9 to 13 on Saturday."])
    r = await _mail(env, raw_mail())
    assert r.status_code == 202
    assert len(smtp.sent) == 1
    reply = smtp.sent[0]
    assert reply["To"] == "Ada Lovelace <ada@customer.example>"
    assert reply["Subject"] == "Re: Opening hours"
    assert reply["In-Reply-To"] == "<m1@customer.example>"
    assert reply["Auto-Submitted"] == "auto-replied"
    assert "9 to 13" in reply.get_content()
    # The run saw the mail as data (the server-run guard marks untrusted input).
    first_input = run_input(env)
    assert "When are you open on Saturday?" in first_input
    async with env.svc.db.session() as db:
        thread = (await db.execute(sa.select(m.ChannelThread))).scalars().first()
        session = (await db.execute(sa.select(m.Session))).scalars().first()
    assert thread is not None and session is not None
    # The subject is a keyed hash: no address is kept as the subject.
    assert thread.subject.startswith("email:") and "ada" not in thread.subject
    assert session.subject == thread.subject
    # A reply in the thread goes on with the same conversation, without the quoted text.
    env.script.add("function:primary", ["Yes, also on Sunday morning."])
    follow = raw_mail(
        subject="Re: Opening hours",
        body="And Sunday?\n\nOn Mon, 16 Nov 2026 Assistant wrote:\n> We are open 9 to 13",
        message_id="<m2@customer.example>",
        headers={
            "In-Reply-To": str(reply["Message-ID"]),
            "References": f"<m1@customer.example> {reply['Message-ID']}",
        },
    )
    await _mail(env, follow)
    assert len(smtp.sent) == 2
    second = run_input(env)
    assert "We are open 9 to 13 on Saturday." in second and "And Sunday?" in second
    assert smtp.sent[1]["References"].startswith("<m1@customer.example>")
    async with env.svc.db.session() as db:
        sessions = (await db.execute(sa.select(sa.func.count()).select_from(m.Session))).scalar()
    assert sessions == 1
    # The same delivery again is handled once.
    await _mail(env, follow)
    assert len(smtp.sent) == 2


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"headers": {"Auto-Submitted": "auto-replied"}}, "auto_submitted"),
        ({"headers": {"X-Autoreply": "yes"}}, "autoreply"),
        ({"headers": {"Precedence": "bulk"}}, "bulk"),
        ({"headers": {"List-Id": "<news.example>"}}, "list"),
        ({"sender": "MAILER-DAEMON@mx.example"}, "daemon"),
        ({"sender": "no-reply@shop.example"}, "daemon"),
        ({"sender": "assistant@acme.example"}, "own"),
        ({"headers": {"X-Evokai-Channel": "support"}}, "own"),
    ],
)
def test_loop_protection_never_answers_automatic_mail(kwargs: dict, reason: str) -> None:
    cfg = EmailConfig.model_validate(EMAIL)
    msg = mail.parse(raw_mail(**kwargs), 10_000)
    assert mail.loop_reason(msg, cfg) == reason


@pytest.mark.usefixtures("fx")
async def test_loops_rates_and_allowed_senders_stop_before_any_run(env: Env) -> None:
    smtp = FakeSmtp()
    env.svc.channels.mailer = mail.Mailer(env.svc.http, smtp=smtp.send)
    await _channel(
        env,
        "email",
        "support",
        {**EMAIL, "max_replies_per_sender_per_hour": 2, "allowed_senders": ["@customer.example"]},
    )
    app, rec, cfg = await env.svc.channels.channel("test", "email", "support")
    outcomes = [
        await env.svc.channels.email(app, rec, cfg, raw)
        for raw in (
            raw_mail(headers={"Auto-Submitted": "auto-generated"}),
            raw_mail(sender="eve@elsewhere.example", message_id="<x1@e>"),
            raw_mail(message_id="<a1@c>"),
            raw_mail(message_id="<a2@c>"),
            raw_mail(message_id="<a3@c>"),
        )
    ]
    assert outcomes == ["auto_submitted", "not_allowed", "sent", "sent", "rate"]
    assert len(smtp.sent) == 2


@pytest.mark.usefixtures("fx")
async def test_attachments_are_read_with_caps_and_drafts_go_to_the_drafts_folder(env: Env) -> None:
    imap = FakeImap()
    env.svc.channels.mailer = mail.Mailer(env.svc.http, imap_factory=lambda _cfg: imap)
    await _channel(
        env,
        "email",
        "support",
        {
            **EMAIL,
            "reply": "draft",
            "imap": {
                "host": "imap.acme.example",
                "username": "assistant",
                "password_env": "IMAP_PASSWORD",
            },
            "attachments": {"max_bytes": 1_000},
        },
    )
    env.script.add("function:primary", ["The order total is 42."])
    raw = raw_mail(
        body="See the order attached.",
        attachments=[
            ("order.txt", "text/plain", b"Order 7: total 42 EUR"),
            ("huge.txt", "text/plain", b"x" * 5_000),
        ],
    )
    await _mail(env, raw)
    seen = run_input(env)
    assert "Order 7: total 42 EUR" in seen
    assert "huge.txt" in seen and "x" * 2_000 not in seen
    assert len(imap.appended) == 1
    folder, flags, data = imap.appended[0]
    assert folder == '"Drafts"' and "\\Draft" in flags and b"The order total is 42." in data


@pytest.mark.usefixtures("fx")
async def test_an_imap_mailbox_is_polled_from_now_on_and_never_marked_read(env: Env) -> None:
    imap = FakeImap({3: raw_mail(message_id="<old@c>")})
    smtp = FakeSmtp()
    env.svc.channels.mailer = mail.Mailer(
        env.svc.http, imap_factory=lambda _cfg: imap, smtp=smtp.send
    )
    await _channel(
        env,
        "email",
        "support",
        {
            **EMAIL,
            "inbound": "imap",
            "webhook_secret_env": None,
            "imap": {
                "host": "imap.acme.example",
                "username": "assistant",
                "password_env": "IMAP_PASSWORD",
            },
        },
    )
    assert await env.svc.channels.poll_once() == 0  # the first poll notes where the mailbox is
    imap.messages[4] = raw_mail(message_id="<new@c>")
    await env.svc.shared.delete(
        f"channel:poll:{(await env.svc.store.channels.all(env.app_id))[0].id}"
    )
    env.script.add("function:primary", ["Hello Ada."])
    assert await env.svc.channels.poll_once() == 1
    assert imap.fetched == ["4"] and len(smtp.sent) == 1


def test_quoted_text_is_left_out_of_a_reply() -> None:
    assert strip_quoted("Thanks!\n\nIl giorno lun Mario ha scritto:\n> ciao") == "Thanks!"
    assert strip_quoted("> only quoted") == "> only quoted"


# Slack


async def _slack(
    env: Env,
    path: str,
    body: bytes,
    *,
    key: str = "slack-signing",
    ts: int | None = None,
    form: bool = False,
) -> httpx.Response:
    ts = ts or int(time.time())
    r = await env.client.post(
        f"/agent/v1/channels/test/slack/team/{path}",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
            "X-Slack-Request-Timestamp": str(ts),
            "X-Slack-Signature": slack_sign(key, ts, body),
        },
    )
    await env.svc.channels.drain()
    return r


def _event(text: str, *, user: str = "U1", event_id: str = "Ev1", **extra: Any) -> bytes:
    return json.dumps({
        "type": "event_callback",
        "team_id": "T1",
        "event_id": event_id,
        "authorizations": [{"user_id": "UBOT"}],
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": user,
            "text": text,
            "ts": "1700000000.000100",
            **extra,
        },
    }).encode()


SLACK = {
    "signing_secret_env": "SLACK_SIGNING",
    "bot_token_env": "SLACK_BOT",
    "team_id": "T1",
    "api_url": "https://slack.test/api",
}


async def test_slack_events_are_verified_and_answered_in_the_thread(env: Env, fx: Fakes) -> None:
    await _channel(env, "slack", "team", SLACK)
    challenge = json.dumps({"type": "url_verification", "challenge": "c-1"}).encode()
    assert (await _slack(env, "events", challenge)).json() == {"challenge": "c-1"}
    assert (await _slack(env, "events", challenge, key="wrong")).status_code == 401
    old = int(time.time()) - 600
    assert (await _slack(env, "events", challenge, ts=old)).status_code == 401
    env.script.add("function:primary", ["Your order **ships** tomorrow."])
    assert (await _slack(env, "events", _event("When does my order ship?"))).status_code == 200
    posts = [c for c in fx.slack_calls if c[0] == "chat.postMessage"]
    assert len(posts) == 1
    body = posts[0][1]
    assert body["channel"] == "D1" and body["thread_ts"] == "1700000000.000100"
    assert body["text"] == "Your order *ships* tomorrow."
    assert posts[0][2] == "Bearer xoxb-1"
    # A retry, the app's own messages, other bots and edits are not answered.
    await _slack(env, "events", _event("When does my order ship?"))
    await _slack(env, "events", _event("me", user="UBOT", event_id="Ev2"))
    await _slack(env, "events", _event("bot", event_id="Ev3", bot_id="B9"))
    await _slack(env, "events", _event("edit", event_id="Ev4", subtype="message_changed"))
    assert len([c for c in fx.slack_calls if c[0] == "chat.postMessage"]) == 1
    async with env.svc.db.session() as db:
        thread = (await db.execute(sa.select(m.ChannelThread))).scalar_one()
    assert thread.subject == "slack:T1:U1"


async def test_slack_confirmations_are_buttons_only_their_person_can_press(
    env: Env, fx: Fakes
) -> None:
    await _channel(env, "slack", "team", SLACK)
    env.script.add("function:primary", [Tool("order_refund", {"period": "2026-10"})])
    await _slack(env, "events", _event("Classify October"))
    post = [c for c in fx.slack_calls if c[0] == "chat.postMessage"][-1][1]
    actions = next(b for b in post["blocks"] if b["type"] == "actions")["elements"]
    assert [a["action_id"] for a in actions] == ["evokai_confirm_yes", "evokai_confirm_no"]
    assert not env.mcp.calls

    def press(user: str, trigger: str) -> bytes:
        payload = {
            "type": "block_actions",
            "trigger_id": trigger,
            "team": {"id": "T1"},
            "user": {"id": user},
            "channel": {"id": "D1"},
            "message": {"ts": "1700000000.000200", "thread_ts": "1700000000.000100", "text": "x"},
            "actions": [{**actions[0], "action_ts": trigger}],
        }
        return urlencode({"payload": json.dumps(payload)}).encode()

    await _slack(env, "interactions", press("U2", "t1"), form=True)  # someone else
    assert not env.mcp.calls
    env.script.add("function:primary", ["Done: classified."])
    await _slack(env, "interactions", press("U1", "t2"), form=True)
    assert env.mcp.calls == [("order_refund", {"period": "2026-10"})]
    methods = [c[0] for c in fx.slack_calls]
    assert "chat.update" in methods
    assert fx.slack_calls[-1][1]["text"] == "Done: classified."


async def test_a_channel_not_granted_does_not_run_the_agent(env: Env, fx: Fakes) -> None:
    await _channel(env, "slack", "team", SLACK)
    await env.client.patch(
        f"{ADMIN}/grants",
        json={"changes": [{"kind": "channel", "name": "slack", "value": False}]},
        headers=_admin(env),
    )
    await _slack(env, "events", _event("hello"))
    posts = [c for c in fx.slack_calls if c[0] == "chat.postMessage"]
    assert posts[-1][1]["text"] == "The assistant is not available on this channel."
    assert not env.script.seen


async def test_a_mapping_webhook_names_the_subject_or_refuses(env: Env, fx: Fakes) -> None:
    await _channel(
        env,
        "slack",
        "team",
        {
            **SLACK,
            "mapping": {
                "strategy": "webhook",
                "webhook_url": "https://mapping.test/who",
                "webhook_secret_env": "MAPPING_SECRET",
            },
        },
    )
    fx.mapping["T1:U1"] = "crm-user-77"
    env.script.add("function:primary", ["Hi."])
    await _slack(env, "events", _event("hello"))
    body, headers = fx.mapping_calls[0]
    assert body["channel"] == "slack" and body["id"] == "T1:U1"
    raw = json.dumps(body, ensure_ascii=False).encode()
    assert headers["x-evokai-signature"] == hook_sign(
        "map-secret", int(headers["x-evokai-timestamp"]), raw
    )
    async with env.svc.db.session() as db:
        assert (await db.execute(sa.select(m.ChannelThread.subject))).scalar_one() == "crm-user-77"
    await _slack(env, "events", _event("hello", user="U9", event_id="Ev9"))
    posts = [c for c in fx.slack_calls if c[0] == "chat.postMessage"]
    assert "not enabled" in posts[-1][1]["text"]


# Teams


def _activity(text: str = "Hello", **over: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "act-1",
        "channelId": "msteams",
        "serviceUrl": SERVICE_URL,
        "text": text,
        "from": {"id": "29:abc", "aadObjectId": "oid-1", "name": "Ada"},
        "recipient": {"id": "28:bot", "name": "Desk"},
        "conversation": {"id": "a:conv-1", "tenantId": "tenant-1"},
        **over,
    }


TEAMS = {"app_id": BOT_ID, "app_password_env": "BOT_PASSWORD", "tenant_id": "tenant-1"}


async def _teams(env: Env, activity: dict[str, Any], token: str | None) -> httpx.Response:
    r = await env.client.post(
        "/agent/v1/channels/test/teams/desk/messages",
        json=activity,
        headers={"Authorization": f"Bearer {token}"} if token else {},
    )
    await env.svc.channels.drain()
    return r


async def test_teams_activities_need_a_bot_framework_token(env: Env, fx: Fakes) -> None:
    await _channel(env, "teams", "desk", TEAMS)
    assert (await _teams(env, _activity(), None)).status_code == 401
    assert (await _teams(env, _activity(), fx.teams_token(aud="other-bot"))).status_code == 401
    assert (
        await _teams(env, _activity(), fx.teams_token(iss="https://evil.example"))
    ).status_code == 401
    expired = fx.teams_token(exp=int(time.time()) - 3600)
    assert (await _teams(env, _activity(), expired)).status_code == 401
    other_url = fx.teams_token(serviceurl="https://smba.trafficmanager.net/other/")
    assert (await _teams(env, _activity(), other_url)).status_code == 401
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": "https://api.botframework.com", "aud": BOT_ID, "exp": int(time.time()) + 60},
        forged_key,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    assert (await _teams(env, _activity(), forged)).status_code == 401
    assert not fx.teams_posts


async def test_teams_replies_proactively_with_adaptive_cards_for_confirmations(
    env: Env, fx: Fakes
) -> None:
    await _channel(env, "teams", "desk", TEAMS)
    env.script.add("function:primary", ["Hi Ada."])
    r = await _teams(env, _activity(), fx.teams_token())
    assert r.status_code == 200
    path, body, auth = fx.teams_posts[-1]
    assert path == "/emea/v3/conversations/a:conv-1/activities/act-1"
    assert auth == "Bearer bot-token" and body["text"] == "Hi Ada." and body["replyToId"] == "act-1"
    env.script.add("function:primary", [Tool("order_refund", {"period": "2026-10"})])
    await _teams(env, _activity("Classify October", id="act-2"), fx.teams_token())
    card = fx.teams_posts[-1][1]["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    submit = card["content"]["actions"][0]["data"]
    assert submit["evokai"] == "confirm" and submit["approve"] is True
    assert not env.mcp.calls
    env.script.add("function:primary", ["Classified."])
    await _teams(env, _activity("", id="act-3", value=submit), fx.teams_token())
    assert env.mcp.calls == [("order_refund", {"period": "2026-10"})]
    assert fx.teams_posts[-1][1]["text"] == "Classified."
    async with env.svc.db.session() as db:
        thread = (await db.execute(sa.select(m.ChannelThread))).scalar_one()
    assert thread.subject == "teams:oid-1"
    assert thread.reference["serviceUrl"] == SERVICE_URL


async def test_teams_refuses_other_tenants_and_service_urls_off_the_list(
    env: Env, fx: Fakes
) -> None:
    await _channel(env, "teams", "desk", TEAMS)
    other = _activity(conversation={"id": "a:conv-9", "tenantId": "tenant-9"}, id="act-9")
    assert (await _teams(env, other, fx.teams_token())).status_code == 200
    assert not fx.teams_posts and not env.script.seen
    evil = "https://evil.example/"
    act = _activity(serviceUrl=evil, id="act-10")
    env.script.add("function:primary", ["Hi."])
    assert (await _teams(env, act, fx.teams_token(serviceurl=evil))).status_code == 200
    assert not fx.teams_posts
    rows = (await env.client.get(f"{ADMIN}/channels", headers=_admin(env))).json()["channels"]
    assert rows[0]["status"] == "error", rows
