"""Console delegation tokens: the assistant acts with the rights of the person in the console."""

from __future__ import annotations

import time

import jwt

from evokai.assistant.delegation import AUDIENCE, ISSUER, PREFIX, mint_delegation
from evokai.identity.console import ConsoleLogin
from tests.conftest import Env

IDP = "https://idp.example"


async def _sso(env: Env, roles: dict[str, str], sub: str = "ada") -> ConsoleLogin:
    user = await env.svc.store.console_users.signed_in(IDP, sub, f"{sub}@example.com", sub, roles)
    return ConsoleLogin(
        "sso",
        None,
        int(time.time()) + 3600,
        user_id=user.id,
        email=user.email,
        roles=roles,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_token_works_only_on_its_application(env: Env) -> None:
    other = await env.svc.store.applications.create("other", "Other")
    login = await _sso(env, {"test": "owner"})
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    assert token.startswith(PREFIX)
    r = await env.client.get("/agent/v1/admin/application", headers=_bearer(token))
    assert r.status_code == 200 and r.json()["id"] == env.app_id
    # The application comes from the token only: a header naming another one changes nothing.
    r = await env.client.get(
        "/agent/v1/admin/application",
        headers={**_bearer(token), "X-Evokai-Application": other.id},
    )
    assert r.json()["id"] == env.app_id
    # A token for an application the login has no role on is refused when used.
    forged = mint_delegation(env.svc.keyring, login, other.id, "owner")
    r = await env.client.get("/agent/v1/admin/application", headers=_bearer(forged))
    assert r.status_code == 401
    # Never a session token.
    r = await env.client.get("/agent/v1/session", headers=_bearer(token))
    assert r.status_code == 401


async def test_role_is_capped_by_the_live_login(env: Env) -> None:
    login = await _sso(env, {"test": "owner"})
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    body = {"name": "Renamed"}
    r = await env.client.patch("/agent/v1/admin/application", json=body, headers=_bearer(token))
    assert r.status_code == 200, r.text
    log = (await env.client.get("/agent/v1/admin/audit", headers=_bearer(env.admin_key))).json()
    entry = next(e for e in log["items"] if e["action"] == "application.update")
    assert entry["actor"] == "assistant:sso:ada@example.com", entry
    assert entry["actorKind"] == "assistant", entry
    # Signed in again with fewer rights: the token minted before follows the live role.
    await _sso(env, {"test": "viewer"})
    r = await env.client.patch("/agent/v1/admin/application", json=body, headers=_bearer(token))
    assert r.status_code == 403 and r.json()["error"] == "role"
    r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(token))
    assert r.status_code == 200
    # A claim above the login's role never lifts it.
    viewer = await _sso(env, {"test": "viewer"}, sub="bo")
    lifted = mint_delegation(env.svc.keyring, viewer, env.app_id, "owner")
    r = await env.client.patch("/agent/v1/admin/application", json=body, headers=_bearer(lifted))
    assert r.status_code == 403


async def test_revoked_key_kills_the_token(env: Env) -> None:
    keys = await env.svc.store.applications.keys(env.app_id)
    adm = next(k for k in keys if k.kind == "adm")
    login = ConsoleLogin("adm", env.app_id, int(time.time()) + 3600, key_id=adm.id)
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(token))
    assert r.status_code == 200
    await env.svc.store.applications.revoke_key(env.app_id, adm.id)
    await env.svc.auth.forget_cached_keys()
    r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(token))
    assert r.status_code == 401


async def test_disabled_sso_user_kills_the_token(env: Env) -> None:
    login = await _sso(env, {"test": "operator"})
    token = mint_delegation(env.svc.keyring, login, env.app_id, "operator")
    r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(token))
    assert r.status_code == 200
    await env.svc.store.console_users.set_disabled(login.user_id or "", True)
    await env.svc.auth.forget_user(login.user_id or "")
    r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(token))
    assert r.status_code == 401


async def test_expired_token_is_401(env: Env) -> None:
    login = await _sso(env, {"test": "owner"})
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner", ttl=-10)
    r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(token))
    assert r.status_code == 401


async def test_wrong_audience_is_401(env: Env) -> None:
    login = await _sso(env, {"test": "owner"})
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    claims = jwt.decode(
        token[len(PREFIX) :], env.svc.keyring.signing_key, algorithms=["HS256"], audience=AUDIENCE
    )
    assert claims["iss"] == ISSUER
    for change in ({"aud": "evokai-other"}, {"iss": "evokai"}):
        forged = PREFIX + jwt.encode(
            {**claims, **change}, env.svc.keyring.signing_key, algorithm="HS256"
        )
        r = await env.client.get("/agent/v1/admin/agents", headers=_bearer(forged))
        assert r.status_code == 401, change
