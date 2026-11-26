"""Nothing survives a reset of the assistant's system application, and nothing else is touched."""

from __future__ import annotations

import time
from typing import Any

import pytest

from evokai.assistant.bootstrap import SLUG, ensure_assistant
from evokai.core.security import generate_key, hash_secret, parse_key
from evokai.identity import console
from evokai.identity.console import ConsoleLogin
from tests.conftest import Env

PREVIEW = "/agent/v1/admin/application/reset/preview"
RESET = "/agent/v1/admin/application/reset"
SECRET = "sk-very-secret-value-0123456789"


async def _system(env: Env) -> str:
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    app = await env.svc.store.applications.by_slug(SLUG)
    assert app is not None
    return app.id


async def _owner(env: Env) -> None:
    roles = {"*": "owner"}
    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", "olga", "olga@example.com", "olga", roles
    )
    login = ConsoleLogin(
        "sso", None, int(time.time()) + 3600, user_id=user.id, email=user.email, roles=roles
    )
    env.client.cookies.clear()
    env.client.cookies.set(console.COOKIE, console.issue(env.svc.keyring, login), path="/agent")


def _on(app_id: str) -> dict[str, str]:
    return {"X-Evokai-Console": "1", "X-Evokai-Application": app_id}


async def _leftovers(env: Env, app_id: str) -> set[tuple[str, str]]:
    """One resource of every kind an unlocked system application could have kept, made directly.

    Returns:
        (kind, name) of each.
    """
    s = env.svc.store
    await s.prompts.put(app_id, "extra-prompt", "Say hello.")
    await s.skills.put(app_id, "extra-skill", "An extra skill", "Body", [])
    await s.mcp_servers.put(
        app_id, "extra-mcp", url="https://mcp.example/mcp", auth="static", secret_encrypted=SECRET
    )
    helper = await s.agents.active(env.app_id, "helper")
    assert helper is not None
    definition = {**helper.definition, "name": "extra-agent"}
    await s.agents.put(app_id, "extra-agent", "v1", definition)
    await s.evals.put_case(app_id, "assistant", "extra-case", input="hi", checks=[])
    await s.workflows.put(app_id, "extra-flow", "v1", {"name": "extra-flow", "steps": []})
    await s.knowledge.put_base(app_id, "extra-kb", embedding_model="test:embed")
    await s.channels.put(
        app_id, "email", "extra-mail", agent="assistant", enabled=False, config={"x": "y"}
    )
    await s.connections.put_app(app_id, "github", client_id="cid", secret=SECRET, writes=False)
    await s.capability_credentials.put(
        app_id, "search", values_encrypted=SECRET, names=["key"], plain=None
    )
    key = parse_key(generate_key("srv").value)
    assert key is not None
    key_id = await s.applications.add_key(app_id, "srv", key[1], hash_secret(key[2]), "extra")
    return {
        ("prompt", "extra-prompt"),
        ("skill", "extra-skill"),
        ("mcp_server", "extra-mcp"),
        ("agent", "extra-agent"),
        ("eval_case", "assistant/extra-case"),
        ("workflow", "extra-flow"),
        ("knowledge", "extra-kb"),
        ("channel", "email/extra-mail"),
        ("connection_app", "github"),
        ("capability_credential", "search"),
        ("key", key_id),
    }


async def _present(env: Env, app_id: str) -> set[tuple[str, str]]:
    s = env.svc.store
    out: set[tuple[str, str]] = set()
    out |= {("prompt", p.name) for p in await s.prompts.all(app_id)}
    out |= {("skill", k.name) for k in await s.skills.all(app_id)}
    out |= {("mcp_server", k.name) for k in await s.mcp_servers.all(app_id)}
    out |= {("agent", a.name) for a in await s.agents.active_all(app_id)}
    for agent in {"assistant", "helper"}:
        out |= {("eval_case", f"{agent}/{c.name}") for c in await s.evals.cases(app_id, agent)}
    out |= {("workflow", w.name) for w in await s.workflows.active_all(app_id)}
    out |= {("knowledge", b.name) for b in await s.knowledge.bases(app_id)}
    out |= {("channel", f"{c.kind}/{c.name}") for c in await s.channels.all(app_id)}
    out |= {("connection_app", a.provider) for a in await s.connections.apps(app_id)}
    out |= {
        ("capability_credential", c.provider) for c in await s.capability_credentials.all(app_id)
    }
    out |= {("key", k.id) for k in await s.applications.keys(app_id) if k.revoked_at is None}
    return out


async def test_system_app_refuses_foreign_resource_kinds(env: Env) -> None:
    app_id = await _system(env)
    await _owner(env)
    h = _on(app_id)
    foreign: list[tuple[str, str, Any]] = [
        ("PUT", "/agent/v1/admin/knowledge/kb", {"embedding_model": "test:embed"}),
        ("PUT", "/agent/v1/admin/workflows/flow", {"steps": []}),
        ("PUT", "/agent/v1/admin/agents/other", {"model": "test:x", "instructions": "x"}),
        ("PUT", "/agent/v1/admin/channels/email/box", {"agent": "assistant"}),
        ("PUT", "/agent/v1/admin/connections/github/app", {"client_id": "x"}),
        ("PUT", "/agent/v1/admin/capability-credentials/search", {"values": {}}),
        ("POST", "/agent/v1/admin/keys", {"kind": "srv"}),
        ("PUT", "/agent/v1/admin/mcp-servers/other", {"url": "https://mcp.example/mcp"}),
    ]
    for locked in (True, False):
        await env.svc.store.applications.update(app_id, locked=locked)
        for method, url, body in foreign:
            r = await env.client.request(method, url, json=body, headers=h)
            assert r.status_code == 409, (locked, method, url, r.text)
            assert r.json()["error"] == "system_app", (locked, method, url, r.text)
    # Unlocked, the assistant's own resources stay writable.
    r = await env.client.put("/agent/v1/admin/prompts/greeting", json={"text": "Hello."}, headers=h)
    assert r.status_code == 200, r.text
    r = await env.client.patch("/agent/v1/admin/application", json={"name": "Assistant"}, headers=h)
    assert r.status_code == 200, r.text
    # Another application is not concerned.
    admin = {"Authorization": f"Bearer {env.admin_key}"}
    r = await env.client.put(
        "/agent/v1/admin/knowledge/kb", json={"embedding_model": "test:embed"}, headers=admin
    )
    assert r.status_code != 409, r.text


async def _plan(env: Env, headers: dict[str, str]) -> str:
    r = await env.client.get(PREVIEW, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["plan"]


async def test_a_reset_deletes_only_what_its_preview_showed(env: Env) -> None:
    app_id = await _system(env)
    await _owner(env)
    h = _on(app_id)
    plan = await _plan(env, h)
    # Added after the owner looked: the reset would delete it without the owner having seen it.
    await env.svc.store.applications.update(app_id, locked=False)
    r = await env.client.put(
        "/agent/v1/admin/prompts/late", json={"text": "Added after the preview."}, headers=h
    )
    assert r.status_code == 200, r.text
    r = await env.client.post(RESET, json={"confirm": SLUG, "plan": plan}, headers=h)
    assert (r.status_code, r.json()["error"]) == (409, "plan_changed"), r.text
    assert await env.svc.store.prompts.get(app_id, "late") is not None
    r = await env.client.post(RESET, json={"confirm": SLUG}, headers=h)
    assert r.status_code == 400, r.text  # no plan, no reset
    fresh = await env.client.get(PREVIEW, headers=h)
    assert ("prompt", "late") in {(i["kind"], i["name"]) for i in fresh.json()["items"]}
    body = {"confirm": SLUG, "plan": fresh.json()["plan"]}
    assert (await env.client.post(RESET, json=body, headers=h)).status_code == 200


async def test_a_reset_revokes_peoples_connections_at_the_provider(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = await _system(env)
    s = env.svc.store
    await s.connections.put_app(app_id, "github", client_id="cid", secret=SECRET, writes=False)
    await s.connections.put(
        app_id,
        "console:ada",
        "github",
        account="ada",
        scopes=["repo"],
        access=env.svc.keyring.encrypt("gho_token"),
        refresh=None,
        expires_at=None,
    )
    revoked: list[str] = []

    async def revoke(row: Any, cfg: Any = None) -> None:  # ruff: ignore[unused-async]
        del cfg
        revoked.append(f"{row.subject}/{row.provider}")

    monkeypatch.setattr(env.svc.connections, "_revoke_at_provider", revoke)
    await _owner(env)
    h = _on(app_id)
    r = await env.client.post(RESET, json={"confirm": SLUG, "plan": await _plan(env, h)}, headers=h)
    assert r.status_code == 200, r.text
    assert revoked == ["console:ada/github"]  # not only deleted here: still live at GitHub
    assert await s.connections.of_subject(app_id, "console:ada") == []


async def test_reset_preview_matches_what_reset_deletes(env: Env) -> None:
    app_id = await _system(env)
    expected = await _leftovers(env, app_id)
    await _owner(env)
    r = await env.client.get(PREVIEW, headers=_on(app_id))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    deletes = {(i["kind"], i["name"]) for i in items if i["action"] == "delete"}
    assert deletes == expected
    restores = {(i["kind"], i["name"]) for i in items if i["action"] == "restore"}
    assert ("agent", "assistant") in restores and ("mcp_server", "evokai-admin") in restores
    before = await _present(env, app_id)
    plan = await _plan(env, _on(app_id))
    r = await env.client.post(RESET, json={"confirm": "wrong", "plan": plan}, headers=_on(app_id))
    assert r.status_code == 400 and r.json()["error"] == "confirm", r.text
    r = await env.client.post(RESET, json={"confirm": SLUG, "plan": plan}, headers=_on(app_id))
    assert r.status_code == 200, r.text
    after = await _present(env, app_id)
    assert before - after == expected
    after_preview = (await env.client.get(PREVIEW, headers=_on(app_id))).json()["items"]
    assert [i for i in after_preview if i["action"] == "delete"] == []


async def test_reset_touches_nothing_outside_the_system_app(env: Env) -> None:
    app_id = await _system(env)
    await _leftovers(env, app_id)
    others = await _leftovers(env, env.app_id)
    before = await _present(env, env.app_id)
    assert others <= before
    await _owner(env)
    plan = await _plan(env, _on(app_id))
    r = await env.client.post(RESET, json={"confirm": SLUG, "plan": plan}, headers=_on(app_id))
    assert r.status_code == 200, r.text
    assert await _present(env, env.app_id) == before


async def test_reset_export_contains_the_deleted_items(env: Env) -> None:
    app_id = await _system(env)
    expected = await _leftovers(env, app_id)
    await _owner(env)
    r = await env.client.get(PREVIEW, headers=_on(app_id))
    assert SECRET not in r.text
    items = {(i["kind"], i["name"]): i for i in r.json()["items"] if i["action"] == "delete"}
    assert set(items) == expected
    for key, item in items.items():
        assert isinstance(item.get("item"), dict) and item["item"], key
    assert items["prompt", "extra-prompt"]["item"]["text"] == "Say hello."
    assert items["mcp_server", "extra-mcp"]["item"]["url"] == "https://mcp.example/mcp"
    assert items["skill", "extra-skill"]["item"]["body"] == "Body"
    assert items["agent", "extra-agent"]["item"]["name"] == "extra-agent"


async def test_reset_restores_the_settings_an_owner_changed(env: Env) -> None:
    app_id = await _system(env)
    await _owner(env)
    h = _on(app_id)
    await env.svc.store.applications.update(app_id, locked=False)
    changes = {
        "allowed_origins": ["https://elsewhere.example"],
        "data_protection": {"residency": {"geographies": ["eu"]}},
        "audit_ttl_days": 60,
        "locale": "it",
    }
    r = await env.client.patch("/agent/v1/admin/application", json=changes, headers=h)
    assert r.status_code == 200, r.text
    app = await env.svc.store.applications.get(app_id)
    assert app is not None
    extra = {"assistant_model": "function:primary", "eval_credential": "x"}
    await env.svc.store.applications.update(app_id, defaults={**(app.defaults or {}), **extra})
    preview = (await env.client.get(PREVIEW, headers=h)).json()["items"]
    settings = next(i for i in preview if (i["kind"], i["name"]) == ("settings", "application"))
    assert settings["action"] == "restore"
    assert {"allowed_origins", "data_protection", "audit_ttl_days", "locale"} <= set(
        settings["item"]
    )
    assert settings["item"]["eval_credential"] == "***"
    r = await env.client.post(RESET, json={"confirm": SLUG, "plan": await _plan(env, h)}, headers=h)
    assert r.status_code == 200, r.text
    app = await env.svc.store.applications.get(app_id)
    assert app is not None and app.locked
    assert app.allowed_origins == [] and app.data_protection is None
    assert app.audit_ttl_days is None
    assert "locale" not in (app.defaults or {}) and "eval_credential" not in (app.defaults or {})
    # The activated model stays: a reset brings back the definition, not an inactive assistant.
    assert (app.defaults or {}).get("assistant_model") == "function:primary"
    assert app.conversation_ttl_days == 30


def test_every_application_setting_is_restored_by_a_reset() -> None:
    from evokai.assistant.reset import FRESH, IDENTITY
    from evokai.db import models as m

    columns = {c.key for c in m.Application.__table__.columns}
    assert columns == set(FRESH) | IDENTITY | {"defaults"}, columns ^ (set(FRESH) | IDENTITY)
