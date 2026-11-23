"""The console assistant session: only for a console login, scoped to the selected application."""

from __future__ import annotations

import time

import jwt

from evokai.assistant.activation import MEASURED_KEY
from evokai.assistant.bootstrap import SLUG, ensure_assistant
from evokai.assistant.delegation import AUDIENCE, PREFIX, mint_delegation
from evokai.identity import console
from evokai.identity.console import ConsoleLogin
from tests.conftest import Env

URL = "/agent/v1/console/assistant/session"


async def _system(env: Env, model: str | None = "function:primary") -> str:
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    app = await env.svc.store.applications.by_slug(SLUG)
    assert app is not None
    if model:
        from evokai.assistant.activation import activate

        numbers = {"tool_calls_pct": 100, "confirm_pct": 100, "redteam_pct": 100}
        defaults = {**(app.defaults or {}), MEASURED_KEY: {model: numbers}}
        await env.svc.store.applications.update(app.id, defaults=defaults)
        fresh = await env.svc.store.applications.get(app.id)
        assert fresh is not None
        await activate(env.svc, fresh, model)
    return app.id


async def _console_as(env: Env, roles: dict[str, str], sub: str = "cy") -> str:
    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", sub, f"{sub}@example.com", sub, roles
    )
    login = ConsoleLogin(
        "sso", None, int(time.time()) + 3600, user_id=user.id, email=user.email, roles=roles
    )
    env.client.cookies.clear()
    env.client.cookies.set(console.COOKIE, console.issue(env.svc.keyring, login), path="/agent")
    return user.id


def _on(app_id: str) -> dict[str, str]:
    return {"X-Evokai-Console": "1", "X-Evokai-Application": app_id}


async def test_session_needs_a_console_login(env: Env) -> None:
    await _system(env)
    r = await env.client.post(URL, headers={"Authorization": f"Bearer {env.admin_key}"})
    assert r.status_code == 403 and r.json()["error"] == "console_required"
    login = ConsoleLogin("op", None, int(time.time()) + 3600)
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    r = await env.client.post(URL, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    assert (await env.client.post(URL)).status_code == 401


async def test_session_is_scoped_to_the_selected_application(env: Env) -> None:
    system_id = await _system(env)
    user_id = await _console_as(env, {"test": "viewer"})
    r = await env.client.post(URL, headers=_on(env.app_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applicationId"] == system_id and body["token"] and body["conversationId"]
    # A refresh continues the same conversation.
    again = await env.client.post(
        URL, json={"conversationId": body["conversationId"]}, headers=_on(env.app_id)
    )
    assert again.status_code == 200 and again.json()["conversationId"] == body["conversationId"]
    claims = env.svc.keyring.verify(body["token"])
    assert claims.application_id == system_id and claims.subject == f"console:{user_id}"
    assert claims.agent == "assistant" and claims.has_credential
    stored = await env.svc.store.credentials.get(claims.token_id, system_id)
    assert stored is not None
    delegation = env.svc.keyring.decrypt(stored)
    assert delegation and delegation.startswith(PREFIX)
    granted = jwt.decode(
        delegation[len(PREFIX) :],
        env.svc.keyring.signing_key,
        algorithms=["HS256"],
        audience=AUDIENCE,
    )
    assert granted["app"] == env.app_id and granted["role"] == "viewer"
    # The forwarded credential acts on the selected application only, with the viewer's role.
    r = await env.client.get(
        "/agent/v1/admin/application", headers={"Authorization": f"Bearer {delegation}"}
    )
    assert r.json()["id"] == env.app_id
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"name": "x"},
        headers={"Authorization": f"Bearer {delegation}"},
    )
    assert r.status_code == 403
    # Another application the login cannot see is refused.
    r = await env.client.post(URL, headers=_on(system_id))
    assert r.status_code == 404


async def test_session_is_409_while_the_assistant_is_inactive(env: Env) -> None:
    await _system(env, model=None)
    await _console_as(env, {"*": "owner"})
    r = await env.client.post(URL, headers=_on(env.app_id))
    assert r.status_code == 409 and r.json()["error"] == "assistant_inactive"
