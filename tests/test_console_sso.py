"""Console single sign-on (a fake OpenID Connect provider), roles on every route, the audit log."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import jwt.algorithms
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from evokai.core.util import utcnow
from evokai.identity import sso
from tests.conftest import Env

ISSUER = "http://idp.test"
CLIENT = "evokai-console"
SECRET = "client-secret-of-the-console"
OPERATOR_KEY = "evk-operator-key-for-tests-0123456789"  # gitleaks:allow
CONSOLE = {"X-Evokai-Console": "1"}


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    return {
        **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
    }


@dataclass
class FakeIdp:
    """An OpenID Connect provider: discovery, keys, a token endpoint that checks PKCE."""

    key: rsa.RSAPrivateKey = field(default_factory=lambda: rsa.generate_private_key(65537, 2048))
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    token_requests: list[dict[str, Any]] = field(default_factory=list)
    sign_with: rsa.RSAPrivateKey | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                },
            )
        if path == "/jwks":
            return httpx.Response(200, json={"keys": [_jwk(self.key, "k1")]})
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            grant = self.codes.pop(form.get("code", ""), None)
            basic = base64.b64encode(f"{CLIENT}:{SECRET}".encode()).decode()
            if grant is None or request.headers.get("authorization") != f"Basic {basic}":
                return httpx.Response(400, json={"error": "invalid_grant"})
            challenge = (
                base64
                .urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if challenge != grant["challenge"] or form["redirect_uri"] != grant["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "aud": CLIENT,
                "iat": now,
                "exp": now + 300,
                "nonce": grant["nonce"],
                **grant["claims"],
            }
            token = jwt.encode(
                claims, self.sign_with or self.key, algorithm="RS256", headers={"kid": "k1"}
            )
            return httpx.Response(200, json={"id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


CONFIG = {
    "issuer": ISSUER,
    "client_id": CLIENT,
    "client_secret": SECRET,
    "allowed_domains": ["inkwell-books.example", "partner.example"],
    "require_mfa": True,
    "roles": [
        {"claim": "evokai-owners", "role": "owner", "applications": ["*"]},
        {"claim": "test-ops", "role": "operator", "applications": ["test"]},
        {"claim": "test-viewers", "role": "viewer", "applications": ["test"]},
        {"email": "*@partner.example", "role": "viewer", "applications": ["test"]},
    ],
}


@pytest.fixture
async def idp(env: Env) -> FakeIdp:
    fake = FakeIdp()
    mcp = env.mcp.handler

    def route(request: httpx.Request) -> httpx.Response:
        return fake.handler(request) if request.url.host == "idp.test" else mcp(request)

    env.svc.http = httpx.AsyncClient(transport=httpx.MockTransport(route))
    env.svc.settings.operator_key = OPERATOR_KEY
    sso.forget_metadata()
    r = await env.client.post("/agent/v1/console/login", json={"key": OPERATOR_KEY})
    assert r.status_code == 204
    r = await env.client.put("/agent/v1/console/sso/config", json=CONFIG, headers=CONSOLE)
    assert r.status_code == 200, r.text
    assert (
        r.json()["config"]["hasClientSecret"] is True and "client_secret" not in r.json()["config"]
    )
    env.client.cookies.clear()
    return fake


def _person(
    email: str, groups: list[str], amr: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "sub": f"id-{email}",
        "email": email,
        "email_verified": True,
        "name": email.split("@")[0],
        "groups": groups,
        "amr": amr if amr is not None else ["pwd", "mfa"],
        **extra,
    }


async def _sign_in(
    env: Env, idp: FakeIdp, claims: dict[str, Any], *, state: str | None = None
) -> str:
    """The whole flow; returns where the console was sent (its hash route)."""
    env.client.cookies.clear()
    r = await env.client.get("/agent/v1/console/sso/start")
    assert r.status_code == 302, r.text
    to = urlsplit(r.headers["location"])
    assert f"{to.scheme}://{to.netloc}{to.path}" == f"{ISSUER}/authorize"
    q = {k: v[0] for k, v in parse_qs(to.query).items()}
    assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
    assert q["client_id"] == CLIENT and "openid" in q["scope"]
    code = f"code-{len(idp.codes)}-{time.time_ns()}"
    idp.codes[code] = {
        "nonce": q["nonce"],
        "challenge": q["code_challenge"],
        "redirect_uri": q["redirect_uri"],
        "claims": claims,
    }
    r = await env.client.get(
        "/agent/v1/console/sso/callback", params={"code": code, "state": state or q["state"]}
    )
    assert r.status_code == 302
    return r.headers["location"].split("#", 1)[1]


def _app(env: Env) -> dict[str, str]:
    return {**CONSOLE, "X-Evokai-Application": env.app_id}


AGENT = {
    "name": "helper",
    "model": "function:primary",
    "mcp_servers": ["app"],
    "instructions": "Be brief.",
}


async def test_a_viewer_signs_in_with_pkce_and_only_reads(env: Env, idp: FakeIdp) -> None:
    assert (
        await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-viewers"])) == "/apps"
    )
    assert idp.token_requests[-1]["grant_type"] == "authorization_code"
    me = (await env.client.get("/agent/v1/console/me", headers=CONSOLE)).json()
    assert (
        me["kind"] == "sso"
        and me["email"] == "ada@inkwell-books.example"
        and me["roles"] == {"test": "viewer"}
    )
    assert me["deploymentOwner"] is False
    apps = (await env.client.get("/agent/v1/console/applications", headers=CONSOLE)).json()
    assert [(a["slug"], a["role"]) for a in apps["items"]] == [("test", "viewer")] and apps[
        "canCreate"
    ] is False
    assert (await env.client.get("/agent/v1/admin/agents", headers=_app(env))).status_code == 200
    r = await env.client.put("/agent/v1/admin/agents/helper", json=AGENT, headers=_app(env))
    assert r.status_code == 403 and r.json()["error"] == "role"
    r = await env.client.patch("/agent/v1/admin/application", json={"name": "X"}, headers=_app(env))
    assert r.status_code == 403
    assert (
        await env.client.get("/agent/v1/console/sso/config", headers=CONSOLE)
    ).status_code == 401


async def test_an_operator_changes_agents_but_not_settings_or_security(
    env: Env, idp: FakeIdp
) -> None:
    await _sign_in(env, idp, _person("bob@inkwell-books.example", ["test-ops"]))
    r = await env.client.put("/agent/v1/admin/agents/helper", json=AGENT, headers=_app(env))
    assert r.status_code == 200, r.text
    r = await env.client.post("/agent/v1/admin/keys", json={"kind": "srv"}, headers=_app(env))
    assert r.status_code == 200
    r = await env.client.put(
        "/agent/v1/admin/agents/helper",
        json={**AGENT, "security": {"modes": {"input.injection": "off"}}},
        headers=_app(env),
    )
    assert r.status_code == 403 and r.json()["error"] == "role"
    r = await env.client.put("/agent/v1/admin/security", json={"mode": "off"}, headers=_app(env))
    assert r.status_code == 403
    r = await env.client.patch("/agent/v1/admin/application", json={"name": "X"}, headers=_app(env))
    assert r.status_code == 403
    entries = (
        await env.client.get(
            "/agent/v1/admin/audit", params={"action": "agent."}, headers=_app(env)
        )
    ).json()
    (put,) = entries["items"]
    assert (
        put["actor"] == "sso:bob@inkwell-books.example"
        and put["actorKind"] == "console"
        and put["role"] == "operator"
    )
    assert put["before"]["instructions"] == "You are an assistant. Today is {date}."
    assert put["after"]["instructions"] == "Be brief."


async def test_an_owner_of_every_application_owns_the_deployment(env: Env, idp: FakeIdp) -> None:
    await _sign_in(env, idp, _person("cy@inkwell-books.example", ["evokai-owners"]))
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"name": "Renamed"}, headers=_app(env)
    )
    assert r.status_code == 200, r.text
    assert (
        await env.client.get("/agent/v1/console/sso/config", headers=CONSOLE)
    ).status_code == 200
    r = await env.client.post(
        "/agent/v1/operator/applications", json={"slug": "other", "name": "Other"}, headers=CONSOLE
    )
    assert r.status_code == 201
    log = (await env.client.get("/agent/v1/console/audit", headers=CONSOLE)).json()["items"]
    actions = [e["action"] for e in log]
    assert {"application.create", "application.update", "sso.config", "console.login"} <= set(
        actions
    )
    update = next(e for e in log if e["action"] == "application.update")
    assert update["before"] == {"name": "Test App"} and update["after"] == {"name": "Renamed"}
    assert update["application"] == "test"
    sso_change = next(e for e in log if e["action"] == "sso.config")
    assert SECRET not in json.dumps(sso_change) and sso_change["actor"] == "operator-key"


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        (_person("ada@inkwell-books.example", ["test-viewers"], amr=["pwd"]), "mfa_required"),
        (_person("eve@evil.example", ["evokai-owners"]), "not_allowed"),
        (_person("dan@inkwell-books.example", []), "no_role"),
        (
            _person("fay@inkwell-books.example", ["test-ops"], email_verified=False),
            "email_unverified",
        ),
    ],
)
async def test_sign_ins_that_are_refused(
    env: Env, idp: FakeIdp, claims: dict[str, Any], reason: str
) -> None:
    assert await _sign_in(env, idp, claims) == f"/login?sso_error={reason}"
    assert (await env.client.get("/agent/v1/console/me", headers=CONSOLE)).status_code == 401


async def test_a_partner_domain_gets_its_role_from_the_email(env: Env, idp: FakeIdp) -> None:
    await _sign_in(env, idp, _person("gil@partner.example", []))
    me = (await env.client.get("/agent/v1/console/me", headers=CONSOLE)).json()
    assert me["roles"] == {"test": "viewer"}


async def test_forged_state_and_tokens_are_refused(env: Env, idp: FakeIdp) -> None:
    assert (
        await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-ops"]), state="forged")
        == "/login?sso_error=state"
    )
    idp.sign_with = rsa.generate_private_key(65537, 2048)
    assert (
        await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-ops"]))
        == "/login?sso_error=token"
    )
    idp.sign_with = None
    assert (
        await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-ops"], nonce="other"))
        == "/login?sso_error=token"
    )
    r = await env.client.get("/agent/v1/console/sso/callback", params={"code": "x", "state": "y"})
    assert r.headers["location"].endswith("sso_error=state")


async def test_a_disabled_user_is_signed_out_at_once(env: Env, idp: FakeIdp) -> None:
    await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-ops"]))
    viewer_cookie = env.client.cookies.get("evokai_console")
    await _sign_in(env, idp, _person("cy@inkwell-books.example", ["evokai-owners"]))
    users = (await env.client.get("/agent/v1/console/users", headers=CONSOLE)).json()["items"]
    ada = next(u for u in users if u["email"] == "ada@inkwell-books.example")
    r = await env.client.patch(
        f"/agent/v1/console/users/{ada['id']}", json={"disabled": True}, headers=CONSOLE
    )
    assert r.status_code == 200 and r.json()["disabled"] is True
    env.client.cookies.clear()
    env.client.cookies.set("evokai_console", viewer_cookie or "", path="/agent")
    assert (await env.client.get("/agent/v1/admin/agents", headers=_app(env))).status_code == 401
    assert (
        await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-ops"]))
        == "/login?sso_error=disabled"
    )


def _fill(path: str) -> str:
    return path.replace("{", "").replace("}", "")


async def test_a_viewer_is_refused_on_every_change(env: Env, idp: FakeIdp) -> None:
    """Every route that changes something answers a viewer 403 (or 401 for the deployment's)."""
    await _sign_in(env, idp, _person("ada@inkwell-books.example", ["test-viewers"]))
    paths = (await env.client.get("/agent/openapi.json")).json()["paths"]
    checked = 0
    for path, operations in paths.items():
        if path.startswith((
            "/agent/v1/session/",
            "/agent/v1/console/sso",
            "/agent/v1/console/log",
        )):
            continue  # session tokens, the sign-in flow and logging in or out
        if path in {
            "/agent/v1/console/skills/parse",
            "/agent/v1/admin/knowledge/{name}/search",
            "/agent/v1/admin/workflows/validate",
        }:
            continue  # parses text, or tries a search: changes nothing
        if path.startswith(("/agent/v1/workflow-hooks/", "/agent/v1/channels/")):
            continue  # an outside system's signed webhook or channel delivery, not a console route
        if path == "/agent/v1/console/me/preferences":
            continue  # the viewer's own console preferences, not the deployment's
        if path == "/agent/v1/console/assistant/session":
            continue  # the viewer's own assistant session: it acts with the viewer's role
        for method in set(operations) - {"get", "head"}:
            r = await env.client.request(method.upper(), _fill(path), json={}, headers=_app(env))
            assert r.status_code in {401, 403}, (method, path, r.status_code, r.text)
            checked += 1
    assert checked >= 25


async def test_an_admin_key_login_is_the_owner_of_its_application(env: Env) -> None:
    r = await env.client.post("/agent/v1/console/login", json={"key": env.admin_key})
    assert r.status_code == 204
    me = (await env.client.get("/agent/v1/console/me", headers=CONSOLE)).json()
    assert me["kind"] == "adm" and me["deploymentOwner"] is False
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"name": "Mine"}, headers=CONSOLE
    )
    assert r.status_code == 200
    r = await env.client.put(
        "/agent/v1/admin/mcp-servers/svc",
        json={"url": "http://app.test/mcp", "auth": "secret", "secret": "s3cret-value-123"},
        headers=CONSOLE,
    )
    assert r.status_code == 200
    log = (await env.client.get("/agent/v1/admin/audit", headers=CONSOLE)).json()["items"]
    mcp = next(e for e in log if e["action"] == "mcp_server.put")
    assert mcp["after"]["hasSecret"] is True and "s3cret-value-123" not in json.dumps(log)
    assert mcp["actor"].startswith("admin-key:") and log[-1]["action"] == "console.login"


async def test_audit_entries_follow_their_retention(env: Env) -> None:
    store = env.svc.store
    await store.audit.add(
        application_id=env.app_id,
        actor="x",
        actor_kind="admin_key",
        role="owner",
        action="a",
        target="",
        before=None,
        after=None,
        ip="",
        correlation_id="",
    )
    await store.audit.add(
        application_id=None,
        actor="x",
        actor_kind="operator_key",
        role="owner",
        action="b",
        target="",
        before=None,
        after=None,
        ip="",
        correlation_id="",
    )
    assert await store.audit.purge(400, now=utcnow() + timedelta(days=10)) == 0
    await store.applications.update(env.app_id, audit_ttl_days=5)
    assert await store.audit.purge(400, now=utcnow() + timedelta(days=10)) == 1
    assert await store.audit.purge(400, now=utcnow() + timedelta(days=500)) == 1
