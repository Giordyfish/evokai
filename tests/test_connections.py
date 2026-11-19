"""Per-user OAuth connections: the flow with PKCE, the token vault, the delegated tools, revocation.

Every provider is a fake behind httpx's mock transport: no call leaves the process.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
import sqlalchemy as sa

from evokai.connectors import validate
from evokai.core.errors import BadRequest
from evokai.core.util import utcnow
from evokai.db import models as m
from tests.conftest import ORIGIN, Env, Tool, types

ADMIN = "/agent/v1/admin"


def _id_token(**claims: Any) -> str:
    return jwt.encode(
        {"sub": "x", **claims}, "not-checked-by-evokai-at-all-0123456789", algorithm="HS256"
    )


class FakeProviders:
    """Microsoft's and Google's token endpoints and APIs."""

    def __init__(self, fallback: Any) -> None:
        self.fallback = fallback
        self.challenges: dict[str, str] = {}
        self.token_calls: list[dict[str, str]] = []
        self.api_calls: list[tuple[str, str, str, Any]] = []
        self.refuse_refresh = False
        self.revoked: list[str] = []
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if host in {"login.microsoftonline.com", "oauth2.googleapis.com"}:
            if path.endswith("/revoke"):
                self.revoked.append(parse_qs(request.content.decode())["token"][0])
                return httpx.Response(200)
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_calls.append(form)
            if form["grant_type"] == "authorization_code":
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if self.challenges.get(form["code"]) != challenge or form["client_secret"] != "s3":
                    return httpx.Response(400, json={"error": "invalid_grant"})
            elif form["grant_type"] == "refresh_token":
                if self.refuse_refresh:
                    return httpx.Response(400, json={"error": "invalid_grant"})
            self.issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"at-{self.issued}",
                    "refresh_token": f"rt-{self.issued}",
                    "expires_in": 3600,
                    "scope": "openid Mail.Read Mail.ReadWrite",
                    "id_token": _id_token(preferred_username="ada@contoso.example"),
                },
            )
        if host == "graph.microsoft.com":
            auth = request.headers.get("authorization", "")
            body = json.loads(request.content) if request.content else None
            self.api_calls.append((request.method, path, auth, body))
            if auth == "Bearer revoked":
                return httpx.Response(401, json={"error": {"message": "expired"}})
            if path.endswith("/me/messages") and request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "value": [
                            {
                                "id": "m1",
                                "subject": "Invoice 42",
                                "from": {"emailAddress": {"name": "Bob", "address": "b@x.example"}},
                                "receivedDateTime": "2026-11-15T10:00:00Z",
                                "bodyPreview": "Please pay",
                            }
                        ]
                    },
                )
            if path.endswith("/me/messages") and request.method == "POST":
                return httpx.Response(
                    201, json={"id": "d1", "subject": (body or {}).get("subject")}
                )
            if path.endswith("/me/calendarView"):
                return httpx.Response(200, json={"value": [{"id": "e1", "subject": "Standup"}]})
            return httpx.Response(404, json={"error": {"message": "nope"}})
        if host in {"gmail.googleapis.com", "www.googleapis.com"}:
            auth = request.headers.get("authorization", "")
            self.api_calls.append((request.method, path, auth, None))
            if path.endswith("/messages"):
                return httpx.Response(200, json={"messages": [{"id": "g1"}]})
            if "/messages/g1" in path:
                return httpx.Response(
                    200,
                    json={
                        "id": "g1",
                        "threadId": "t1",
                        "snippet": "Hello",
                        "payload": {
                            "headers": [
                                {"name": "From", "value": "Eve <e@x.example>"},
                                {"name": "Subject", "value": "Lunch"},
                            ]
                        },
                    },
                )
            if path.endswith("/events"):
                return httpx.Response(200, json={"items": [{"id": "c1", "summary": "Lunch"}]})
            return httpx.Response(404, json={"error": {"message": "nope"}})
        return self.fallback(request)


@pytest.fixture
def fx(env: Env, monkeypatch: pytest.MonkeyPatch) -> FakeProviders:
    monkeypatch.setenv("M365_SECRET", "s3")
    monkeypatch.setenv("GOOGLE_SECRET", "s3")
    fakes = FakeProviders(env.mcp.handler)
    env.svc.http._transport.handler = fakes.handler  # ty: ignore[missing-attribute]
    return fakes


def _admin(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _setup(env: Env, *, writes: bool = False, grant: bool = True) -> None:
    r = await env.client.put(
        f"{ADMIN}/mcp-servers/m365",
        json={
            "connector": "microsoft_365",
            "config": {
                "client_id": "app-1",
                "client_secret_env": "M365_SECRET",
                "tenant": "contoso.example",
                "writes": writes,
            },
        },
        headers=_admin(env),
    )
    assert r.status_code == 200, r.text
    r = await env.client.put(
        f"{ADMIN}/mcp-servers/gws",
        json={
            "connector": "google_workspace",
            "tool_prefix": "google_",
            "config": {"client_id": "g-1", "client_secret_env": "GOOGLE_SECRET"},
        },
        headers=_admin(env),
    )
    assert r.status_code == 200, r.text
    if grant:
        r = await env.client.patch(
            f"{ADMIN}/grants",
            json={
                "changes": [
                    {"kind": "connection", "name": "microsoft365", "value": True},
                    {"kind": "connection", "name": "google", "value": True},
                ]
            },
            headers=_admin(env),
        )
        assert r.status_code == 200, r.text
    r = await env.client.put(
        f"{ADMIN}/agents/office",
        json={
            "name": "office",
            "model": "function:primary",
            "mcp_servers": ["m365", "gws"],
            "instructions": "You help with mail.",
        },
        headers=_admin(env),
    )
    assert r.status_code == 200, r.text


async def _session(env: Env, subject: str = "u1") -> dict[str, str]:
    tok = await env.token(subject, agent="office")
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


async def _connect(env: Env, fx: FakeProviders, s: dict[str, str], provider: str = "microsoft365"):
    r = await env.client.post(f"/agent/v1/session/connections/{provider}/start", json={}, headers=s)
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorizeUrl"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    fx.challenges["code-1"] = q["code_challenge"]
    cb = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "code": "code-1"}
    )
    return url, q, cb


def test_configuration_needs_secrets_from_the_environment() -> None:
    with pytest.raises(BadRequest) as e:
        validate(
            "microsoft_365", {"client_id": "a", "client_secret": "x", "client_secret_env": "E"}
        )
    assert "client_secret_env" in e.value.message
    with pytest.raises(BadRequest):
        validate("microsoft_365", {"client_id": "a", "client_secret_env": "E", "tenant": "a/b"})
    cfg = validate("google_workspace", {"client_id": "a", "client_secret_env": "E"})
    assert getattr(cfg, "families", None) == ["mail", "calendar", "files"] and not cfg.writes


@pytest.mark.usefixtures("fx")
async def test_a_provider_is_off_until_granted(env: Env) -> None:
    await _setup(env, grant=False)
    s = await _session(env)
    r = await env.client.post(
        "/agent/v1/session/connections/microsoft365/start", json={}, headers=s
    )
    assert r.status_code == 403 and r.json()["error"] == "connection_not_granted"
    assert (await env.client.get("/agent/v1/session/connections", headers=s)).json() == {
        "connections": []
    }


async def test_the_flow_with_pkce_stores_encrypted_tokens_once(env: Env, fx: FakeProviders) -> None:
    await _setup(env)
    s = await _session(env)
    view = (await env.client.get("/agent/v1/session/connections", headers=s)).json()
    assert {c["provider"]: c["status"] for c in view["connections"]} == {
        "microsoft365": "not_connected",
        "google": "not_connected",
    }
    url, q, cb = await _connect(env, fx, s)
    assert url.netloc == "login.microsoftonline.com"
    assert url.path == "/contoso.example/oauth2/v2.0/authorize"
    assert q["code_challenge_method"] == "S256" and q["client_id"] == "app-1"
    assert q["redirect_uri"].endswith("/agent/v1/oauth/callback")
    assert set(q["scope"].split()) >= {"offline_access", "Mail.Read", "Calendars.Read"}
    assert "Mail.ReadWrite" not in q["scope"]  # no write scopes without writes
    assert cb.status_code == 200 and "connected" in cb.text
    assert "style-src 'unsafe-inline'" in cb.headers["content-security-policy"]
    # The state is single use.
    again = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "code": "code-1"}
    )
    assert again.status_code == 400
    async with env.svc.db.session() as db:
        row = (await db.execute(sa.select(m.OAuthConnection))).scalar_one()
    assert row.subject == "u1" and row.provider == "microsoft365"
    assert "at-1" not in row.access_token_encrypted and "rt-1" not in (
        row.refresh_token_encrypted or ""
    )
    assert env.svc.keyring.decrypt(row.access_token_encrypted) == "at-1"
    view = (await env.client.get("/agent/v1/session/connections", headers=s)).json()
    ms = next(c for c in view["connections"] if c["provider"] == "microsoft365")
    assert ms["status"] == "connected" and ms["account"] == "ada@contoso.example"
    assert ms["families"] == ["mail", "calendar", "files"]


async def test_a_wrong_verifier_or_a_declined_consent_connects_nothing(
    env: Env, fx: FakeProviders
) -> None:
    await _setup(env)
    s = await _session(env)
    r = await env.client.post("/agent/v1/session/connections/google/start", json={}, headers=s)
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["authorizeUrl"]).query).items()}
    assert q["access_type"] == "offline" and q["prompt"] == "consent"
    fx.challenges["code-x"] = "something-else"
    cb = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "code": "code-x"}
    )
    assert cb.status_code == 400
    r = await env.client.post("/agent/v1/session/connections/google/start", json={}, headers=s)
    q = {k: v[0] for k, v in parse_qs(urlparse(r.json()["authorizeUrl"]).query).items()}
    cb = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "error": "access_denied"}
    )
    assert cb.status_code == 400 and "access_denied" in cb.text
    bad = await env.client.post(
        "/agent/v1/session/connections/google/start",
        json={"returnTo": "https://evil.example/x"},
        headers=s,
    )
    assert bad.status_code == 400
    ok = await env.client.post(
        "/agent/v1/session/connections/google/start",
        json={"returnTo": f"{ORIGIN}/settings"},
        headers=s,
    )
    assert ok.status_code == 200


async def test_without_a_connection_the_turn_shows_a_connect_card(
    env: Env, fx: FakeProviders
) -> None:
    await _setup(env)
    s = await _session(env)
    env.script.add(
        "function:primary", [Tool("mail_search", {"query": "invoice"})], ["Connect first."]
    )
    tok = s["Authorization"][7:]
    events = await env.chat(tok, text="Find the invoice mail")
    connect = [e for e in events if e["type"] == "connect"]
    assert connect == [
        {
            "type": "connect",
            "provider": "microsoft365",
            "title": "Microsoft 365",
            "server": "m365",
            "turnId": connect[0]["turnId"],
            "seq": connect[0]["seq"],
        }
    ]
    assert not fx.api_calls
    # The card stays in the conversation (a reload shows it).
    conv = (await env.client.get("/agent/v1/session/conversation", headers=s)).json()
    assert any(e["type"] == "connect" for x in conv["messages"] for e in x["events"])


async def test_tools_run_as_the_person_and_refresh_their_token(env: Env, fx: FakeProviders) -> None:
    await _setup(env)
    s = await _session(env)
    await _connect(env, fx, s)
    tok = s["Authorization"][7:]
    env.script.add("function:primary", [Tool("mail_search", {"query": "invoice"})], ["Found it."])
    events = await env.chat(tok, text="Find the invoice mail")
    assert "connect" not in types(events)
    method, path, auth, _ = fx.api_calls[-1]
    assert (method, path, auth) == ("GET", "/v1.0/me/messages", "Bearer at-1")
    tool_return = env.script.seen[-1][1][-1].parts[0]
    assert "Invoice 42" in str(getattr(tool_return, "content", ""))
    # An expiring token is refreshed with the refresh token, once.
    async with env.svc.db.session() as db:
        await db.execute(
            sa.update(m.OAuthConnection).values(expires_at=utcnow() - timedelta(minutes=1))
        )
    env.script.add(
        "function:primary", [Tool("calendar_events", {"start": "a", "end": "b"})], ["ok"]
    )
    await env.chat(tok, text="My calendar")
    assert fx.token_calls[-1]["grant_type"] == "refresh_token"
    assert fx.token_calls[-1]["refresh_token"] == "rt-1"
    assert fx.api_calls[-1][2] == "Bearer at-2"
    # A refused refresh marks the connection expired: the person connects again.
    async with env.svc.db.session() as db:
        await db.execute(
            sa.update(m.OAuthConnection).values(expires_at=utcnow() - timedelta(minutes=1))
        )
    fx.refuse_refresh = True
    env.script.add(
        "function:primary", [Tool("calendar_events", {"start": "a", "end": "b"})], ["ok"]
    )
    events = await env.chat(tok, text="My calendar again")
    assert "connect" in types(events)
    view = (await env.client.get("/agent/v1/session/connections", headers=s)).json()
    assert (
        next(c for c in view["connections"] if c["provider"] == "microsoft365")["status"]
        == "expired"
    )


async def test_a_token_the_provider_refuses_asks_to_connect_again(
    env: Env, fx: FakeProviders
) -> None:
    await _setup(env)
    s = await _session(env)
    await _connect(env, fx, s)
    async with env.svc.db.session() as db:
        await db.execute(
            sa.update(m.OAuthConnection).values(
                access_token_encrypted=env.svc.keyring.encrypt("revoked")
            )
        )
    env.script.add("function:primary", [Tool("mail_search", {"query": "x"})], ["ok"])
    events = await env.chat(s["Authorization"][7:], text="mail")
    assert "connect" in types(events)


async def test_drafts_and_events_wait_for_the_persons_confirmation(
    env: Env, fx: FakeProviders
) -> None:
    await _setup(env, writes=True)
    s = await _session(env)
    _, q, _ = await _connect(env, fx, s)
    assert "Mail.ReadWrite" in q["scope"] and "Calendars.ReadWrite" in q["scope"]
    tok = s["Authorization"][7:]
    env.script.add(
        "function:primary",
        [Tool("mail_draft", {"to": ["b@x.example"], "subject": "Hi", "body": "Hello"})],
    )
    events = await env.chat(tok, text="Draft a mail to Bob")
    confirm = next(e for e in events if e["type"] == "confirm")
    assert not [c for c in fx.api_calls if c[0] == "POST"]
    env.script.add("function:primary", ["Drafted."])
    await env.chat(tok, confirm={"actionId": confirm["actionId"], "approve": True})
    post = next(c for c in fx.api_calls if c[0] == "POST")
    assert post[1] == "/v1.0/me/messages" and post[3]["subject"] == "Hi"


@pytest.mark.usefixtures("fx")
async def test_grants_per_family_hide_tools(env: Env) -> None:
    await _setup(env)
    r = await env.client.patch(
        f"{ADMIN}/grants",
        json={"changes": [{"kind": "connection", "name": "microsoft365/mail", "value": False}]},
        headers=_admin(env),
    )
    assert r.status_code == 200
    s = await _session(env)
    await env.chat(s["Authorization"][7:], text="hello")
    seen = set(env.script.tools_seen)
    assert "calendar_events" in seen and "files_search" in seen
    assert "mail_search" not in seen
    view = (await env.client.get("/agent/v1/session/connections", headers=s)).json()
    ms = next(c for c in view["connections"] if c["provider"] == "microsoft365")
    assert ms["families"] == ["calendar", "files"]


async def test_google_tools_and_disconnect_revoke_at_google(env: Env, fx: FakeProviders) -> None:
    await _setup(env)
    r = await env.client.put(
        f"{ADMIN}/agents/office",
        json={
            "name": "office",
            "model": "function:primary",
            "mcp_servers": ["gws"],
            "instructions": "Mail.",
        },
        headers=_admin(env),
    )
    assert r.status_code == 200, r.text
    s = await _session(env)
    await _connect(env, fx, s, "google")
    env.script.add("function:primary", [Tool("google_mail_search", {"query": "lunch"})], ["ok"])
    await env.chat(s["Authorization"][7:], text="lunch mail")
    assert ("GET", "/gmail/v1/users/me/messages/g1") == fx.api_calls[-1][:2]
    tool_return = env.script.seen[-1][1][-1].parts[0]
    assert "Lunch" in str(getattr(tool_return, "content", ""))
    r = await env.client.delete("/agent/v1/session/connections/google", headers=s)
    assert r.status_code == 204
    assert fx.revoked == ["rt-1"]
    view = (await env.client.get("/agent/v1/session/connections", headers=s)).json()
    assert (
        next(c for c in view["connections"] if c["provider"] == "google")["status"]
        == "not_connected"
    )


async def test_erase_export_and_the_applications_view(env: Env, fx: FakeProviders) -> None:
    await _setup(env)
    s = await _session(env)
    await _connect(env, fx, s)
    srv = {"Authorization": f"Bearer {env.server_key}"}
    exported = (
        await env.client.post("/agent/v1/privacy/export", json={"subject": "u1"}, headers=srv)
    ).json()
    assert exported["connections"][0]["provider"] == "microsoft365"
    assert "at-1" not in json.dumps(exported) and "rt-1" not in json.dumps(exported)
    by_server = (await env.client.get("/agent/v1/connections?subject=u1", headers=srv)).json()
    by_provider = {c["provider"]: c["status"] for c in by_server["connections"]}
    assert by_provider == {"microsoft365": "connected", "google": "not_connected"}
    admin = (await env.client.get(f"{ADMIN}/connections", headers=_admin(env))).json()
    ms = next(p for p in admin["providers"] if p["provider"] == "microsoft365")
    assert ms["configured"] and ms["secretSet"] and ms["connected"] == 1
    assert ms["secretEnv"] == "M365_SECRET" and "s3" not in json.dumps(admin)
    assert admin["redirectUri"].endswith("/v1/oauth/callback")
    erased = (
        await env.client.post("/agent/v1/privacy/erase", json={"subject": "u1"}, headers=srv)
    ).json()
    assert erased["connections"] == 1
    async with env.svc.db.session() as db:
        assert (
            await db.execute(sa.select(sa.func.count()).select_from(m.OAuthConnection))
        ).scalar() == 0


async def test_revoke_all_deletes_every_connection_of_a_provider(
    env: Env, fx: FakeProviders
) -> None:
    await _setup(env)
    for subject in ("u1", "u2"):
        await _connect(env, fx, await _session(env, subject))
    r = await env.client.delete(f"{ADMIN}/connections/microsoft365", headers=_admin(env))
    assert r.json() == {"revoked": 2}
    admin = (await env.client.get(f"{ADMIN}/connections", headers=_admin(env))).json()
    assert next(p for p in admin["providers"] if p["provider"] == "microsoft365")["connected"] == 0
