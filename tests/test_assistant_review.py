"""The console assistant's hardening after the security review."""

from __future__ import annotations

import os
import time

from evokai.assistant.activation import MEASURED_KEY, activate
from evokai.assistant.bootstrap import SLUG, apply_shipped, ensure_assistant
from evokai.assistant.delegation import mint_delegation
from evokai.core.security import generate_key
from evokai.identity import console
from evokai.identity.console import ConsoleLogin
from tests.conftest import Env

CONSOLE = {"X-Evokai-Console": "1"}


async def _system(env: Env) -> str:
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    app = await env.svc.store.applications.by_slug(SLUG)
    assert app is not None
    return app.id


async def _console_as(env: Env, roles: dict[str, str], sub: str = "cy") -> None:
    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", sub, f"{sub}@example.com", sub, roles
    )
    login = ConsoleLogin(
        "sso", None, int(time.time()) + 3600, user_id=user.id, email=user.email, roles=roles
    )
    env.client.cookies.clear()
    env.client.cookies.set(console.COOKIE, console.issue(env.svc.keyring, login), path="/agent")


def _on(app_id: str) -> dict[str, str]:
    return {**CONSOLE, "X-Evokai-Application": app_id}


async def test_a_model_id_never_reads_the_environment(env: Env) -> None:
    os.environ["EVOKAI_TEST_LEAK"] = "s3cr3t-value"
    try:
        app_id = await _system(env)
        await _console_as(env, {"*": "owner"})
        for url in ("/agent/v1/admin/assistant/activate", "/agent/v1/admin/assistant/try"):
            r = await env.client.post(
                url,
                json={"model": "bedrock:eu.${EVOKAI_TEST_LEAK}", "acceptNotRecommended": True},
                headers=_on(app_id),
            )
            assert r.status_code == 400, (url, r.text)
        # Even a model stored before (a start re-applies it) is never expanded.
        await apply_shipped(
            env.svc.store, env.svc.keyring, env.svc.settings, model="bedrock:eu.${EVOKAI_TEST_LEAK}"
        )
        agent = await env.svc.store.agents.active(app_id, "assistant")
        assert agent is not None and "s3cr3t" not in str(agent.definition)
    finally:
        os.environ.pop("EVOKAI_TEST_LEAK", None)


async def test_malformed_bodies_are_400_for_a_delegation(env: Env) -> None:
    login = env.operator_login()
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for body in (b"{not json", b"[1, 2]"):
        r = await env.client.patch("/agent/v1/admin/application", content=body, headers=headers)
        assert r.status_code == 400, r.text


async def _activated(env: Env) -> str:
    app_id = await _system(env)
    app = await env.svc.store.applications.get(app_id)
    assert app is not None
    numbers = {"tool_calls_pct": 100, "confirm_pct": 100, "redteam_pct": 100}
    await env.svc.store.applications.update(
        app_id, defaults={**(app.defaults or {}), MEASURED_KEY: {"function:primary": numbers}}
    )
    fresh = await env.svc.store.applications.get(app_id)
    assert fresh is not None
    await activate(env.svc, fresh, "function:primary")
    return app_id


async def test_no_session_while_the_assistant_forwards_to_another_server(env: Env) -> None:
    app_id = await _activated(env)
    await _console_as(env, {"*": "owner"})
    url = "/agent/v1/console/assistant/session"
    assert (await env.client.post(url, headers=_on(env.app_id))).status_code == 200
    # Unlocked, its MCP server is pointed elsewhere: the person's delegation would go there.
    await env.svc.store.applications.update(app_id, locked=False)
    r = await env.client.put(
        "/agent/v1/admin/mcp-servers/evokai-admin",
        json={"url": "https://collector.example/mcp", "auth": "forward_user"},
        headers=_on(app_id),
    )
    assert r.status_code == 200, r.text
    r = await env.client.post(url, headers=_on(env.app_id))
    assert r.status_code == 409 and r.json()["error"] == "assistant_altered", r.text


async def test_session_tokens_of_the_system_app_come_only_from_the_console_route(
    env: Env,
) -> None:
    app_id = await _activated(env)
    await _console_as(env, {"*": "owner"})
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "console:someone-else", "agent": "assistant"},
        headers=_on(app_id),
    )
    assert r.status_code == 403 and r.json()["error"] == "system_app", r.text


async def test_the_assistant_cannot_weaken_security_by_recreating_an_agent(env: Env) -> None:
    admin = {"Authorization": f"Bearer {env.admin_key}"}
    agent = (await env.client.get("/agent/v1/admin/agents", headers=admin)).json()["items"][0]
    agent.pop("version", None)
    strict = {**agent, "security": {"mode": "enforce"}}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=strict, headers=admin)
    assert r.status_code == 200, r.text
    login = env.operator_login()
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    bearer = {"Authorization": f"Bearer {token}"}
    r = await env.client.delete("/agent/v1/admin/agents/helper", headers=bearer)
    assert r.status_code == 200, r.text
    loose = {**agent, "security": None}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=loose, headers=bearer)
    assert r.status_code == 403 and r.json()["error"] == "role", r.text
    # Publishing an older version is not a tool of the assistant.
    r = await env.client.post(
        "/agent/v1/admin/agents/helper/versions/abc/publish", json={}, headers=bearer
    )
    assert r.status_code == 403 and r.json()["error"] == "assistant_route", r.text


async def test_reset_revokes_every_key_of_the_system_app(env: Env) -> None:
    app_id = await _system(env)
    srv = generate_key("srv")
    from evokai.core.security import hash_secret, parse_key

    parsed = parse_key(srv.value)
    assert parsed is not None
    await env.svc.store.applications.add_key(app_id, "srv", parsed[1], hash_secret(parsed[2]), "x")
    await apply_shipped(env.svc.store, env.svc.keyring, env.svc.settings)
    keys = await env.svc.store.applications.keys(app_id)
    assert keys and all(k.revoked_at is not None for k in keys)
