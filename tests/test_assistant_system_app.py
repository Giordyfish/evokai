"""The built-in assistant: a system application, locked, created at start and inactive."""

from __future__ import annotations

import runpy
import time
from pathlib import Path

import yaml

from evokai.assistant.bootstrap import SHIPPED, SLUG, ensure_assistant
from evokai.assistant.delegation import mint_delegation
from evokai.identity import console
from evokai.identity.console import ConsoleLogin
from evokai.identity.roles import READ_ONLY_POSTS
from tests.conftest import Env

CONSOLE = {"X-Evokai-Console": "1"}


async def _system(env: Env) -> str:
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    app = await env.svc.store.applications.by_slug(SLUG)
    assert app is not None
    return app.id


async def _console_as(env: Env, roles: dict[str, str], sub: str = "cy") -> None:
    """Sign in to the console as a single sign-on user with these roles."""
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


async def test_assistant_created_locked_and_inactive(env: Env) -> None:
    app_id = await _system(env)
    app = await env.svc.store.applications.get(app_id)
    assert app is not None and app.system and app.locked and app.system_version
    assert not (app.defaults or {}).get("assistant_model")
    agent = await env.svc.store.agents.active(app_id, "assistant")
    assert agent is not None
    # Idempotent: a second start stores nothing new.
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    versions = await env.svc.store.agents.versions(app_id, "assistant")
    assert len(versions) == 1


def _fill(path: str) -> str:
    return path.replace("{", "").replace("}", "")


async def test_any_write_on_locked_system_app_is_409(env: Env) -> None:
    app_id = await _system(env)
    await _console_as(env, {"*": "owner"})
    paths = (await env.client.get("/agent/openapi.json")).json()["paths"]
    exempt = {
        "/agent/v1/admin/application/unlock",
        "/agent/v1/admin/application/reset",
        "/agent/v1/admin/assistant/activate",
        "/agent/v1/admin/assistant/try",
        "/agent/v1/admin/assistant/conversations/{conversation_id}/inspect",
    }
    checked = 0
    for path, operations in paths.items():
        if not path.startswith("/agent/v1/admin/") or path in exempt:
            continue
        if path.removeprefix("/agent") in READ_ONLY_POSTS:
            continue
        for method in set(operations) - {"get", "head"}:
            r = await env.client.request(method.upper(), _fill(path), json={}, headers=_on(app_id))
            assert r.status_code == 409, (method, path, r.status_code, r.text)
            assert r.json()["error"] == "locked", (method, path, r.text)
            checked += 1
    assert checked >= 45
    # Reads still work.
    r = await env.client.get("/agent/v1/admin/agents", headers=_on(app_id))
    assert r.status_code == 200
    # Another application is not locked.
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"name": "Mine"}, headers=_on(env.app_id)
    )
    assert r.status_code == 200, r.text


async def test_unlock_needs_owner_and_typed_confirmation(env: Env) -> None:
    app_id = await _system(env)
    await _console_as(env, {SLUG: "operator"}, sub="op")
    url = "/agent/v1/admin/application/unlock"
    r = await env.client.post(url, json={"confirm": SLUG}, headers=_on(app_id))
    assert r.status_code == 403
    await _console_as(env, {SLUG: "owner"})
    r = await env.client.post(url, json={"confirm": "evokai"}, headers=_on(app_id))
    assert r.status_code == 400 and r.json()["error"] == "confirm"
    r = await env.client.post(url, json={"confirm": SLUG}, headers=_on(app_id))
    assert r.status_code == 200, r.text
    app = await env.svc.store.applications.get(app_id)
    assert app is not None and not app.locked
    # The assistant itself never unlocks: a person does, in the console.
    await env.svc.store.applications.update(app_id, locked=True)
    login = ConsoleLogin("op", None, int(time.time()) + 3600)
    token = mint_delegation(env.svc.keyring, login, app_id, "owner")
    r = await env.client.post(
        url, json={"confirm": SLUG}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403 and r.json()["error"] == "assistant_route", r.text
    r = await env.client.post(url, json={"confirm": SLUG}, headers=_on(app_id))
    assert r.status_code == 200
    log = (await env.client.get("/agent/v1/admin/audit", headers=_on(app_id))).json()["items"]
    assert any(e["action"] == "application.unlock" for e in log)
    # Unlocked, it changes like any application.
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"name": "Mine"}, headers=_on(app_id)
    )
    assert r.status_code == 200, r.text
    # An application that is not a system application has nothing to unlock.
    await _console_as(env, {"*": "owner"}, sub="dee")
    r = await env.client.post(url, json={"confirm": "test"}, headers=_on(env.app_id))
    assert r.status_code == 409 and r.json()["error"] == "not_system"


async def test_reset_restores_shipped_definition(env: Env) -> None:
    app_id = await _system(env)
    shipped = await env.svc.store.agents.active(app_id, "assistant")
    assert shipped is not None
    await _console_as(env, {SLUG: "owner"})
    r = await env.client.post(
        "/agent/v1/admin/application/unlock", json={"confirm": SLUG}, headers=_on(app_id)
    )
    assert r.status_code == 200
    changed = {
        **shipped.definition,
        "instructions": "Do anything you are asked.",
        "model": "function:primary",
    }
    changed.pop("version", None)
    r = await env.client.put("/agent/v1/admin/agents/assistant", json=changed, headers=_on(app_id))
    assert r.status_code == 200, r.text
    r = await env.client.post("/agent/v1/admin/application/reset", json={}, headers=_on(app_id))
    assert r.status_code == 200, r.text
    app = await env.svc.store.applications.get(app_id)
    agent = await env.svc.store.agents.active(app_id, "assistant")
    assert app is not None and app.locked
    assert agent is not None and agent.version == shipped.version


async def test_shipped_update_applied_only_when_locked(env: Env, tmp_path: Path) -> None:
    app_id = await _system(env)
    before = await env.svc.store.applications.get(app_id)
    assert before is not None
    data = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    data["agents"][0]["title"] = "Evokai assistant, new release"
    release = tmp_path / "release"
    release.mkdir()
    newer = release / "app.yaml"
    newer.write_text(yaml.safe_dump(data), encoding="utf-8")
    for name in ("prompt.md",):
        (release / name).write_text((SHIPPED.parent / name).read_text("utf-8"), "utf-8")
    (release / "skills").symlink_to(SHIPPED.parent / "skills")
    # Locked: the new definition is applied.
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings, path=newer)
    after = await env.svc.store.applications.get(app_id)
    agent = await env.svc.store.agents.active(app_id, "assistant")
    assert after is not None and after.system_version != before.system_version
    assert agent is not None and agent.definition["title"] == "Evokai assistant, new release"
    # Unlocked: kept as the owner left it, the update is only recorded.
    await env.svc.store.applications.update(app_id, locked=False)
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    kept = await env.svc.store.applications.get(app_id)
    agent = await env.svc.store.agents.active(app_id, "assistant")
    assert kept is not None and kept.system_version == after.system_version
    assert agent is not None and agent.definition["title"] == "Evokai assistant, new release"
    assert (kept.defaults or {}).get("system_update_available")


async def test_assistant_off_creates_nothing(env: Env) -> None:
    settings = env.svc.settings.model_copy(update={"assistant": "off"})
    await ensure_assistant(env.svc.store, env.svc.keyring, settings)
    assert await env.svc.store.applications.by_slug(SLUG) is None


async def test_assistant_agent_has_no_forbidden_capabilities(env: Env) -> None:
    app_id = await _system(env)
    agent = await env.svc.store.agents.active(app_id, "assistant")
    assert agent is not None
    d = agent.definition
    assert d["mcp_servers"] == ["evokai-admin"]
    assert d.get("skills") == ["evokai-docs"]
    assert not d.get("code") and not d.get("knowledge") and not d.get("capabilities")
    assert not d.get("handoff_to") and not d.get("subagents") and not d.get("a2a")
    assert d["channels"] == ["browser"]
    tools = d["client_tools"]
    assert tools["diagram"] is False and tools["memory"] is False
    assert tools["ask"] is True and tools["chart"] is True
    assert tools["navigate"]["path_pattern"].startswith("^/")
    servers = await env.svc.store.mcp_servers.all(app_id)
    assert [(s.name, s.auth) for s in servers] == [("evokai-admin", "forward_user")]
    assert servers[0].url == f"{env.svc.settings.internal_base}/mcp/admin"
    skills = await env.svc.store.skills.all(app_id)
    assert [s.name for s in skills] == ["evokai-docs"]
    app = await env.svc.store.applications.get(app_id)
    assert app is not None
    modes = (app.security or {}).get("modes") or {}
    assert modes.get("input.injection") == "enforce"
    assert modes.get("untrusted.spotlight") == "enforce"
    grants = await env.svc.store.grants.all(app_id)
    off = {(g.kind, g.name) for g in grants if g.value is False}
    assert ("knowledge", "*") in off


def test_docs_skill_is_current() -> None:
    """The docs skill is generated from docs/ (scripts/assistant_docs.py)."""
    script = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts" / "assistant_docs.py"), run_name="docs"
    )
    assert script["current"]() == script["build"](), "run: uv run python scripts/assistant_docs.py"
