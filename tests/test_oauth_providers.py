"""Connections to GitHub, Linear, Notion and Slack: each provider's own OAuth flow, against fakes.

One fake OAuth server plays the four providers with their documented quirks (no call leaves the
process): GitHub answers a refusal with 200 and ``error`` and form-encoded unless JSON is asked,
Linear joins scopes with commas, Notion's MCP authorization server binds tokens to its resource,
Slack answers ``ok: false`` with 200 and puts the granted scopes under ``authed_user``. Each one
checks the PKCE verifier and the client secret, issues tokens, refreshes (rotating the refresh
token), and records revocations the provider's way.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import sqlalchemy as sa

from evokai.connections.providers import PERSONAL, PROVIDERS
from evokai.core.util import utcnow
from evokai.db import models as m
from tests.conftest import ORIGIN, Env

ADMIN = "/agent/v1/admin"
SECRET = "fake-client-secret-for-tests"  # gitleaks:allow

TOKEN_HOSTS = {
    "github.com": "github",
    "api.linear.app": "linear",
    "mcp.notion.com": "notion",
    "slack.com": "slack",
}


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class FakeOAuth:
    """The four providers' token, identity and revocation endpoints."""

    def __init__(self, fallback: Any) -> None:
        self.fallback = fallback
        self.challenges: dict[str, str] = {}
        self.token_calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        self.revoked: list[tuple[str, str, Any]] = []
        self.identity_calls: list[str] = []
        self.refuse_refresh = False
        self.refresh = True
        """Issue refresh tokens with expiring access tokens (else: tokens that never expire)."""
        self.issued = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        provider = TOKEN_HOSTS.get(host)
        if provider == "github" and path == "/login/oauth/access_token":
            return self._token("github", request)
        if host == "api.github.com":
            if path == "/user":
                return self._identity(request, {"login": "ada-gh"})
            if request.method == "DELETE" and path == "/applications/gi-client/grant":
                return self._revoke("github", request, json.loads(request.content)["access_token"])
        if provider == "linear":
            if path == "/oauth/token":
                return self._token("linear", request)
            if path == "/oauth/revoke":
                return self._revoke("linear", request, _form(request)["token"])
            if path == "/graphql":
                viewer = {"viewer": {"email": "ada@linear.example"}}
                return self._identity(request, {"data": viewer})
        if provider == "notion" and path == "/token":
            form = _form(request)
            if "grant_type" not in form:
                if form.get("client_secret") != SECRET:
                    return httpx.Response(401, json={"error": "invalid_client"})
                return self._revoke("notion", request, form["token"])
            return self._token("notion", request)
        if provider == "slack":
            if path == "/api/oauth.v2.user.access":
                return self._token("slack", request)
            if path == "/api/auth.test":
                return self._identity(request, {"ok": True, "user": "ada", "team": "Acme"})
            if path == "/api/auth.revoke":
                auth = request.headers.get("authorization", "")
                return self._revoke("slack", request, auth.removeprefix("Bearer "))
        return self.fallback(request)

    def _identity(self, request: httpx.Request, body: dict[str, Any]) -> httpx.Response:
        self.identity_calls.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=body)

    def _revoke(self, provider: str, request: httpx.Request, token: str) -> httpx.Response:
        self.revoked.append((provider, token, request.headers.get("authorization")))
        return httpx.Response(200 if provider != "github" else 204, json={"ok": True})

    def _refuse(self, provider: str, error: str) -> httpx.Response:
        if provider == "github":
            return httpx.Response(200, json={"error": error})  # GitHub: 200 with an error
        if provider == "slack":
            return httpx.Response(200, json={"ok": False, "error": error})
        return httpx.Response(400, json={"error": error})

    def _token(self, provider: str, request: httpx.Request) -> httpx.Response:
        form = _form(request)
        self.token_calls.append((provider, form, dict(request.headers)))
        if provider == "github" and "json" not in request.headers.get("accept", ""):
            return httpx.Response(200, text="error=json_not_asked")
        if form.get("client_secret") != SECRET or form.get("client_id") != f"{provider[:2]}-client":
            return self._refuse(provider, "invalid_client")
        if provider == "notion" and form.get("resource") != "https://mcp.notion.com/mcp":
            return self._refuse(provider, "invalid_target")
        if form["grant_type"] == "authorization_code":
            if self.challenges.get(form["code"]) != _challenge(form["code_verifier"]):
                return self._refuse(provider, "invalid_grant")
        elif form["grant_type"] == "refresh_token":
            if self.refuse_refresh or not form["refresh_token"].startswith(f"{provider}-rt-"):
                return self._refuse(provider, "invalid_grant")
        self.issued += 1
        body: dict[str, Any] = {"access_token": f"{provider}-at-{self.issued}"}
        if self.refresh:
            body |= {"refresh_token": f"{provider}-rt-{self.issued}", "expires_in": 28800}
        if provider == "slack":
            body |= {
                "ok": True,
                "token_type": "user",
                "authed_user": {"id": "U1", "scope": "search:read.public,channels:history"},
                "team": {"id": "T1"},
            }
        elif provider == "github":
            body |= {"scope": "", "token_type": "bearer"}
        elif provider == "linear":
            body |= {"scope": "read", "token_type": "Bearer"}
        return httpx.Response(200, json=body)


@pytest.fixture
def fake(env: Env) -> FakeOAuth:
    f = FakeOAuth(env.mcp.handler)
    env.svc.http._transport.handler = f.handler  # ty: ignore[missing-attribute]
    return f


def _admin(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _configure(env: Env, provider: str, *, writes: bool = False) -> None:
    r = await env.client.put(
        f"{ADMIN}/connections/{provider}/app",
        json={"clientId": f"{provider[:2]}-client", "clientSecret": SECRET, "writes": writes},
        headers=_admin(env),
    )
    assert r.status_code == 204, r.text
    r = await env.client.patch(
        f"{ADMIN}/grants",
        json={"changes": [{"kind": "connection", "name": provider, "value": True}]},
        headers=_admin(env),
    )
    assert r.status_code == 200, r.text


async def _session(env: Env, subject: str = "u1") -> dict[str, str]:
    tok = await env.token(subject)
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


async def _start(env: Env, s: dict[str, str], provider: str) -> tuple[Any, dict[str, str]]:
    r = await env.client.post(f"/agent/v1/session/connections/{provider}/start", json={}, headers=s)
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorizeUrl"])
    return url, {k: v[0] for k, v in parse_qs(url.query).items()}


async def _connect(env: Env, fake: FakeOAuth, s: dict[str, str], provider: str) -> dict[str, str]:
    _, q = await _start(env, s, provider)
    fake.challenges["code-1"] = q["code_challenge"]
    cb = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "code": "code-1"}
    )
    assert cb.status_code == 200, cb.text
    return q


async def _person(env: Env, provider: str, subject: str = "u1") -> dict[str, Any] | None:
    app = await env.svc.store.applications.get(env.app_id)
    assert app is not None
    return await env.svc.connections.person(app, subject, provider)


async def _expire(env: Env) -> None:
    async with env.svc.db.session() as db:
        await db.execute(
            sa.update(m.OAuthConnection).values(expires_at=utcnow() - timedelta(minutes=1))
        )


AUTHORIZE = {
    "github": ("github.com", "/login/oauth/authorize", " ", {"read:user", "read:org"}),
    "linear": ("linear.app", "/oauth/authorize", ",", {"read"}),
    "notion": ("mcp.notion.com", "/authorize", None, set()),
    "slack": ("slack.com", "/oauth/v2_user/authorize", ",", {"search:read.public"}),
}
ACCOUNT = {
    "github": "ada-gh",
    "linear": "ada@linear.example",
    "notion": None,
    "slack": "ada @ Acme",
}


def test_the_providers_are_data_and_the_integrations_map_to_them() -> None:
    assert PERSONAL == {
        "google_workspace": "google",
        "github": "github",
        "linear": "linear",
        "notion": "notion",
        "slack": "slack",
    }
    for pid in ("github", "linear", "notion", "slack"):
        p = PROVIDERS[pid]
        assert p.configured_by == "application" and p.integration == pid
        # Read scopes by default, write scopes only with writes.
        assert not set(p.write_base) & set(p.scopes([], False))
        assert set(p.write_base) <= set(p.scopes([], True))
    assert PROVIDERS["microsoft365"].authorize_url("t1").endswith("/t1/oauth2/v2.0/authorize")
    assert "/organizations/" in PROVIDERS["microsoft365"].token_url()


@pytest.mark.parametrize("provider", ["github", "linear", "notion", "slack"])
async def test_connect_refresh_reconnect_and_revoke(
    env: Env, fake: FakeOAuth, provider: str
) -> None:
    await _configure(env, provider)
    s = await _session(env)
    assert await _person(env, provider) == {
        "connected": False,
        "token": None,
        "title": PROVIDERS[provider].title,
        "server": provider,
    }
    # The authorize request: the provider's endpoint, PKCE S256, read scopes only.
    url, q = await _start(env, s, provider)
    host, path, sep, scopes = AUTHORIZE[provider]
    assert (url.netloc, url.path) == (host, path)
    assert q["client_id"] == f"{provider[:2]}-client" and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"].endswith("/agent/v1/oauth/callback")
    if sep is None:
        assert "scope" not in q
    else:
        asked = set(q["scope"].split(sep))
        assert scopes <= asked and not asked & set(PROVIDERS[provider].write_base)
    if provider == "notion":
        assert q["resource"] == "https://mcp.notion.com/mcp"
    # The callback trades the code with the verifier and the secret; tokens are kept encrypted.
    fake.challenges["code-1"] = q["code_challenge"]
    cb = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "code": "code-1"}
    )
    assert cb.status_code == 200 and "connected" in cb.text
    assert fake.token_calls[-1][2]["accept"] == "application/json"
    async with env.svc.db.session() as db:
        row = (await db.execute(sa.select(m.OAuthConnection))).scalar_one()
    assert row.provider == provider and f"{provider}-at-1" not in row.access_token_encrypted
    view = (await env.client.get("/agent/v1/session/connections", headers=s)).json()
    mine = next(c for c in view["connections"] if c["provider"] == provider)
    assert mine["status"] == "connected" and mine["account"] == ACCOUNT[provider]
    if provider == "slack":
        assert mine["scopes"] == ["search:read.public", "channels:history"]
    assert await _person(env, provider) == {
        "connected": True,
        "token": f"{provider}-at-1",
        "title": PROVIDERS[provider].title,
        "server": provider,
    }
    # An expiring token is refreshed with the rotating refresh token.
    await _expire(env)
    assert (await _person(env, provider) or {})["token"] == f"{provider}-at-2"
    assert fake.token_calls[-1][1]["refresh_token"] == f"{provider}-rt-1"
    await _expire(env)
    assert (await _person(env, provider) or {})["token"] == f"{provider}-at-3"
    assert fake.token_calls[-1][1]["refresh_token"] == f"{provider}-rt-2"
    # A refused refresh: the person must connect again.
    await _expire(env)
    fake.refuse_refresh = True
    assert (await _person(env, provider) or {})["connected"] is False
    mine = next(
        c
        for c in (await env.client.get("/agent/v1/session/connections", headers=s)).json()[
            "connections"
        ]
        if c["provider"] == provider
    )
    assert mine["status"] == "expired"
    # Connecting again restores it; a 401 from the provider marks it expired again.
    fake.refuse_refresh = False
    await _connect(env, fake, s, provider)
    assert (await _person(env, provider) or {})["connected"] is True
    await env.svc.connections.expired(env.app_id, "u1", provider)
    assert (await _person(env, provider) or {})["connected"] is False
    # Disconnecting revokes at the provider, its way, and deletes the tokens.
    await _connect(env, fake, s, provider)
    issued = fake.issued
    r = await env.client.delete(f"/agent/v1/session/connections/{provider}", headers=s)
    assert r.status_code == 204
    who, token, auth = fake.revoked[-1]
    assert who == provider
    if provider == "github":
        # The grant is removed with the app's own credentials and the access token.
        assert token == f"github-at-{issued}"
        assert auth == "Basic " + base64.b64encode(f"gi-client:{SECRET}".encode()).decode()
    elif provider == "slack":
        assert token == f"slack-at-{issued}"  # auth.revoke takes the access token
    else:
        assert token == f"{provider}-rt-{issued}"
    async with env.svc.db.session() as db:
        n = (await db.execute(sa.select(sa.func.count()).select_from(m.OAuthConnection))).scalar()
    assert n == 0


@pytest.mark.parametrize("provider", ["github", "slack"])
async def test_a_token_without_expiry_stays_until_a_401(
    env: Env, fake: FakeOAuth, provider: str
) -> None:
    # A GitHub OAuth App, or Slack without token rotation: no refresh token, no expiry.
    fake.refresh = False
    await _configure(env, provider)
    s = await _session(env)
    await _connect(env, fake, s, provider)
    async with env.svc.db.session() as db:
        row = (await db.execute(sa.select(m.OAuthConnection))).scalar_one()
    assert row.expires_at is None and row.refresh_token_encrypted is None
    calls = len(fake.token_calls)
    for _ in range(3):
        assert (await _person(env, provider) or {})["token"] == f"{provider}-at-1"
    assert len(fake.token_calls) == calls  # nothing to refresh
    await env.svc.connections.expired(env.app_id, "u1", provider)
    assert await _person(env, provider) == {
        "connected": False,
        "token": None,
        "title": PROVIDERS[provider].title,
        "server": provider,
    }


@pytest.mark.parametrize("provider", ["github", "slack", "linear"])
async def test_a_refusal_connects_nothing(env: Env, fake: FakeOAuth, provider: str) -> None:
    await _configure(env, provider)
    s = await _session(env)
    _, q = await _start(env, s, provider)
    fake.challenges["code-1"] = "not-the-challenge"
    cb = await env.client.get(
        "/agent/v1/oauth/callback", params={"state": q["state"], "code": "code-1"}
    )
    # GitHub and Slack refuse with 200: the body decides.
    assert cb.status_code == 400 and "invalid_grant" in cb.text
    assert (await _person(env, provider) or {})["connected"] is False


async def test_write_scopes_only_with_writes(env: Env, fake: FakeOAuth) -> None:
    for provider, wanted in (
        ("github", {"repo"}),
        ("linear", {"write"}),
        ("slack", {"chat:write", "canvases:write", "reactions:write"}),
    ):
        await _configure(env, provider, writes=True)
        _, q = await _start(env, await _session(env), provider)
        sep = AUTHORIZE[provider][2] or " "
        assert wanted <= set(q["scope"].split(sep))
    assert not fake.token_calls


@pytest.mark.usefixtures("fake")
async def test_the_oauth_app_is_write_only_and_only_for_these_providers(env: Env) -> None:
    await _configure(env, "github")
    admin = (await env.client.get(f"{ADMIN}/connections", headers=_admin(env))).json()
    gh = next(p for p in admin["providers"] if p["provider"] == "github")
    assert gh["configuredBy"] == "application" and gh["integration"] == "github"
    assert gh["configured"] and gh["valid"] and gh["secretSet"] and gh["granted"]
    assert gh["clientId"] == "gi-client" and gh["server"] is None
    assert SECRET not in json.dumps(admin)
    async with env.svc.db.session() as db:
        app = (await db.execute(sa.select(m.OAuthApp))).scalar_one()
    assert SECRET not in app.client_secret_encrypted
    # The secret is kept when omitted.
    r = await env.client.put(
        f"{ADMIN}/connections/github/app",
        json={"clientId": "gi-client", "writes": True},
        headers=_admin(env),
    )
    assert r.status_code == 204
    setup = await env.svc.connections.setup(env.app_id, "github")
    assert setup.cfg is not None and setup.cfg.client_secret == SECRET and setup.cfg.writes
    assert SECRET not in repr(setup.cfg)
    # A new app needs its secret; Google and Microsoft are configured by their connectors.
    r = await env.client.put(
        f"{ADMIN}/connections/slack/app", json={"clientId": "x"}, headers=_admin(env)
    )
    assert r.status_code == 400
    r = await env.client.put(
        f"{ADMIN}/connections/google/app",
        json={"clientId": "x", "clientSecret": "y"},
        headers=_admin(env),
    )
    assert r.status_code == 400
    r = await env.client.delete(f"{ADMIN}/connections/github/app", headers=_admin(env))
    assert r.status_code == 204
    admin = (await env.client.get(f"{ADMIN}/connections", headers=_admin(env))).json()
    assert not next(p for p in admin["providers"] if p["provider"] == "github")["configured"]
    # The grants list the four providers, without tool families.
    grants = (await env.client.get(f"{ADMIN}/grants", headers=_admin(env))).json()
    items = json.dumps(grants)
    assert '"github"' in items and "github/" not in items


@pytest.mark.usefixtures("fake")
async def test_not_granted_or_not_configured_is_no_connection(env: Env) -> None:
    s = await _session(env)
    r = await env.client.post("/agent/v1/session/connections/linear/start", json={}, headers=s)
    assert r.status_code == 403
    assert await _person(env, "linear") is None
    r = await env.client.patch(
        f"{ADMIN}/grants",
        json={"changes": [{"kind": "connection", "name": "linear", "value": True}]},
        headers=_admin(env),
    )
    assert r.status_code == 200
    assert await _person(env, "linear") is None  # granted, but no OAuth app
    assert await _person(env, "linear", subject="") is None


async def test_bootstrap_sets_oauth_apps_from_the_config_file(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evokai.agents.bootstrap import bootstrap
    from evokai.core.errors import ConfigError

    monkeypatch.setenv("LINEAR_OAUTH_SECRET", SECRET)
    path = tmp_path / "c.yaml"
    path.write_text(
        "application: {slug: test, name: Test App}\n"
        "oauth_apps:\n"
        "  linear: {client_id: li-client, client_secret_env: LINEAR_OAUTH_SECRET, writes: true}\n",
        encoding="utf-8",
    )
    await bootstrap(env.svc.store, env.svc.keyring, path, {})
    setup = await env.svc.connections.setup(env.app_id, "linear")
    assert setup.cfg is not None and setup.cfg.client_id == "li-client"
    assert setup.cfg.client_secret == SECRET and setup.cfg.writes
    # Applied again without the variable, the stored secret stays.
    monkeypatch.delenv("LINEAR_OAUTH_SECRET")
    await bootstrap(env.svc.store, env.svc.keyring, path, {})
    setup = await env.svc.connections.setup(env.app_id, "linear")
    assert setup.cfg is not None and setup.cfg.client_secret == SECRET
    path.write_text(
        "application: {slug: test, name: Test App}\n"
        "oauth_apps:\n  google: {client_id: g, client_secret: s}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
    path.write_text(
        "application: {slug: test, name: Test App}\n"
        "oauth_apps:\n  notion: {client_id: no-client}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
