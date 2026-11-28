"""Modules: activation, route gating, the console switch and its pinning, purge, background jobs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from evokai import cli, module_purge
from evokai.core import modules
from evokai.core.config import Settings
from evokai.db import models as m
from evokai.db.kinds import APPLICATION, GATEWAY, applications_only, gateways_only
from tests.conftest import Env

OPERATOR_KEY = "evk_op_modules_tests_operator_key_0123456789"  # gitleaks:allow
OP = {"Authorization": f"Bearer {OPERATOR_KEY}"}
GW = "/agent/v1/admin/gateways"
MODULES = "/agent/v1/console/modules"


@pytest.fixture
def owner(env: Env) -> Env:
    env.svc.settings.operator_key = OPERATOR_KEY
    return env


async def _gateway(env: Env, slug: str = "development") -> tuple[str, str]:
    r = await env.client.post(
        GW, json={"slug": slug, "name": slug.title(), "providers": ["anthropic"]}, headers=OP
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    r = await env.client.post(f"{GW}/{gid}/people", json={"label": "ada@example.com"}, headers=OP)
    assert r.status_code == 201, r.text
    return gid, r.json()["id"]


def _admin(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _switch(env: Env, *names: str) -> dict:
    r = await env.client.put(MODULES, json={"enabled": list(names), "confirm": True}, headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


# Configuration


def test_the_configuration_names_at_least_one_known_module() -> None:
    with pytest.raises(ValidationError, match="Turn on at least one of applications, gateway"):
        Settings(modules=" , ")
    with pytest.raises(ValidationError, match="Unknown module agents"):
        Settings(modules="applications,agents")
    assert Settings(modules="Gateway").modules == "Gateway"
    assert modules.pinned(Settings(modules="gateway, applications")) == modules.ALL
    assert modules.pinned(Settings()) is None


def test_require_module_knows_only_the_modules() -> None:
    with pytest.raises(ValueError, match="unknown module"):
        modules.require_module("proxy")


# Route gating


async def test_both_modules_are_on_by_default(owner: Env) -> None:
    r = await owner.client.get(MODULES, headers=OP)
    assert r.json() == {
        "modules": ["applications", "gateway"],
        "enabled": ["applications", "gateway"],
        "pinned": False,
    }
    assert (await owner.client.get("/agent/v1/admin/agents", headers=_admin(owner))).status_code
    assert (await owner.client.get(GW, headers=OP)).status_code == 200


async def test_a_pinned_module_off_answers_404_module_disabled(owner: Env) -> None:
    owner.svc.settings.modules = "gateway"
    r = await owner.client.get("/agent/v1/admin/agents", headers=_admin(owner))
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "module_disabled",
            "message": "The applications module is off on this deployment.",
            "module": "applications",
        }
    }
    # The OpenAI-compatible API, sessions and the console's application routes too.
    assert (await owner.client.post("/agent/v1/openai/chat/completions", json={})).json()["error"][
        "code"
    ] == "module_disabled"
    r = await owner.client.post("/agent/v1/session-tokens", json={"subject": "u"})
    assert r.json()["error"]["code"] == "module_disabled"
    r = await owner.client.get("/agent/v1/operator/applications", headers=OP)
    assert r.json()["error"]["code"] == "module_disabled"
    # The gateway module and the core still answer.
    assert (await owner.client.get(GW, headers=OP)).status_code == 200
    assert (await owner.client.get("/agent/health")).status_code == 200
    r = await owner.client.get("/agent/v1/admin/security", headers=_admin(owner))
    assert r.status_code == 200, r.text


async def test_the_gateway_module_off_closes_its_admin_and_its_door(owner: Env) -> None:
    gid, _ = await _gateway(owner)
    owner.svc.settings.modules = "applications"
    for method, path in (
        ("GET", GW),
        ("GET", f"{GW}/{gid}"),
        ("POST", "/agent/gw/anthropic/v1/messages"),
        ("GET", "/agent/gw/openai/v1/models"),
    ):
        r = await owner.client.request(method, path, headers=OP)
        assert r.status_code == 404, (path, r.text)
        assert r.json()["error"] == {
            "code": "module_disabled",
            "message": "The gateway module is off on this deployment.",
            "module": "gateway",
        }
    assert (await owner.client.get("/agent/v1/admin/agents", headers=_admin(owner))).status_code


# The console switch


async def test_the_owner_switches_a_module_with_a_confirmation_and_an_audit_row(
    owner: Env,
) -> None:
    body = {"enabled": ["gateway"]}
    r = await owner.client.put(MODULES, json=body, headers=OP)
    assert (r.status_code, r.json()["error"]) == (400, "confirm_required")
    r = await owner.client.put(MODULES, json={"enabled": [], "confirm": True}, headers=OP)
    assert (r.status_code, r.json()["error"]) == (400, "no_module")
    r = await owner.client.put(MODULES, json={"enabled": ["proxy"], "confirm": True}, headers=OP)
    assert (r.status_code, r.json()["error"]) == (400, "unknown_module")
    # Not the owner: an application's admin key is refused.
    r = await owner.client.put(MODULES, json={**body, "confirm": True}, headers=_admin(owner))
    assert r.status_code == 401

    assert (await _switch(owner, "gateway"))["enabled"] == ["gateway"]
    # At once, without a restart.
    r = await owner.client.get("/agent/v1/admin/agents", headers=_admin(owner))
    assert r.json()["error"]["code"] == "module_disabled"
    entries = await owner.svc.store.audit.query(None, everything=True, action="deployment.modules")
    assert len(entries) == 1
    assert entries[0].before == {"enabled": ["applications", "gateway"]}
    assert entries[0].after == {"enabled": ["gateway"]}
    # The data stays: turned on again, the application is back as it was.
    await _switch(owner, "applications", "gateway")
    r = await owner.client.get("/agent/v1/admin/agents", headers=_admin(owner))
    assert r.status_code == 200
    assert {a["name"] for a in r.json()["items"]} >= {"helper"}


async def test_a_pinned_configuration_shows_read_only_and_refuses_a_switch(owner: Env) -> None:
    owner.svc.settings.modules = "applications"
    r = await owner.client.get(MODULES, headers=OP)
    assert r.json()["pinned"] is True
    assert r.json()["enabled"] == ["applications"]
    r = await owner.client.put(MODULES, json={"enabled": ["gateway"], "confirm": True}, headers=OP)
    assert (r.status_code, r.json()["error"]) == (409, "modules_pinned")


async def test_the_console_reads_the_modules_from_me(owner: Env) -> None:
    from evokai.identity import console

    cookie = console.issue(owner.svc.keyring, owner.operator_login())
    owner.client.cookies.set(console.COOKIE, cookie, path="/agent")
    await _switch(owner, "applications")
    r = await owner.client.get("/agent/v1/console/me", headers={"X-Evokai-Console": "1"})
    assert r.status_code == 200, r.text
    assert (r.json()["modules"], r.json()["modulesPinned"]) == (["applications"], False)


async def test_the_stored_choice_is_shared_by_every_reader(owner: Env) -> None:
    await _switch(owner, "gateway")
    async with owner.svc.db.session() as s:
        assert await modules.enabled(s) == {"gateway"}
        assert await modules.is_enabled(s, "applications") is False
    assert await modules.enabled(owner.svc.store) == {"gateway"}
    assert await modules.stored(owner.svc) == {"gateway"}


# Separation


async def test_rows_of_a_module_off_are_invisible_through_the_helpers(owner: Env) -> None:
    gid, _ = await _gateway(owner)
    store = owner.svc.store
    assert [g.id for g in await store.applications.all(kind=GATEWAY)] == [gid]
    await _switch(owner, "applications")
    assert await store.applications.all(kind=GATEWAY) == []
    assert await store.applications.one(gid, GATEWAY) is None
    assert [a.id for a in await store.applications.all(kind=APPLICATION)]
    # Retention still sees them.
    async with owner.svc.db.session() as s:
        q = gateways_only(sa.select(m.Application.id), even_off=True)
        assert list((await s.execute(q)).scalars()) == [gid]
        q = applications_only(sa.select(m.Application.id))
        assert gid not in list((await s.execute(q)).scalars())


async def test_retention_runs_on_the_data_of_a_module_that_is_off(owner: Env) -> None:
    from datetime import timedelta

    from evokai.core.util import utcnow

    gid, key_id = await _gateway(owner)
    async with owner.svc.db.session() as s:
        s.add(
            m.GatewayRequest(
                application_id=gid,
                key_id=key_id,
                provider="anthropic",
                model="claude-sonnet-4-5",
                outcome="forwarded",
                created_at=utcnow() - timedelta(days=400),
            )
        )
    await _switch(owner, "applications")
    assert await owner.svc.store.gateway.purge(30) == 1


# Background jobs


async def test_a_module_off_stops_its_jobs_and_on_starts_them(owner: Env) -> None:
    from evokai import module_jobs

    svc = owner.svc
    await module_jobs.apply(svc, modules.ALL)
    assert svc.channels._poller is not None
    await _switch(owner, "gateway")
    assert svc.modules_on == {"gateway"}
    assert svc.channels._poller is None
    assert svc.workflows.tasks == []
    await _switch(owner, "applications", "gateway")
    assert svc.channels._poller is not None


# Purge


async def _counts(env: Env, app_id: str) -> tuple[int, int]:
    async with env.svc.db.session() as s:
        apps = await s.scalar(
            sa.select(sa.func.count()).select_from(m.Application).where(m.Application.id == app_id)
        )
        keys = await s.scalar(
            sa
            .select(sa.func.count())
            .select_from(m.ApiKey)
            .where(m.ApiKey.application_id == app_id)
        )
        return int(apps or 0), int(keys or 0)


async def test_purge_refuses_a_module_that_is_on(owner: Env) -> None:
    with pytest.raises(module_purge.PurgeRefused, match="is on"):
        await module_purge.plan(owner.svc.store, "gateway")
    owner.svc.settings.modules = "gateway,applications"
    with pytest.raises(module_purge.PurgeRefused, match="EVOKAI_MODULES"):
        await module_purge.plan(owner.svc.store, "gateway")


async def test_purge_counts_exports_revokes_deletes_and_audits(owner: Env, tmp_path: Path) -> None:
    gid, _ = await _gateway(owner)
    r = await owner.client.patch(
        f"{GW}/{gid}", json={"security": {"modes": {"input.secrets": "enforce"}}}, headers=OP
    )
    assert r.status_code == 200, r.text
    await _switch(owner, "applications")
    store = owner.svc.store

    plan = await module_purge.plan(store, "gateway")
    assert plan.ids == [gid] and plan.slugs == ["development"]
    assert plan.keys == 1
    assert plan.counts["evokai_applications"] == 1
    assert plan.counts["evokai_api_keys"] == 1
    assert plan.counts["evokai_policy_versions"] >= 1
    assert "evokai_audit_log" not in plan.counts

    out = tmp_path / "gateway.json"
    written = await module_purge.export(store, plan, out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert written == plan.total
    assert doc["module"] == "gateway"
    assert "secret_hash" not in doc["tables"]["evokai_api_keys"][0]

    deleted = await module_purge.purge(store, plan, "cli:tester")
    assert deleted["evokai_applications"] == 1
    assert await _counts(owner, gid) == (0, 0)
    # The applications module's rows are untouched.
    assert await _counts(owner, owner.app_id) != (0, 0)
    entries = await store.audit.query(None, everything=True, action="module.purge")
    assert [(e.target, e.actor) for e in entries] == [("gateway", "cli:tester")]
    assert (entries[0].before or {})["keysRevoked"] == 1
    # The audit log keeps what happened to the gateway.
    assert await store.audit.query(gid, action="gateway.")


async def test_the_cli_purges_with_yes_and_refuses_while_on(
    owner: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gid, _ = await _gateway(owner)
    monkeypatch.setattr(cli, "get_settings", lambda: owner.svc.settings)

    def run(*args: str) -> int:
        return cli.main(["module", "purge", "gateway", *args])

    # The command runs its own event loop, as from a shell.
    assert await asyncio.to_thread(run, "--yes") == 2
    assert "is on" in capsys.readouterr().err
    await _switch(owner, "applications")
    assert await asyncio.to_thread(run, "--dry-run", "--yes") == 0
    out = capsys.readouterr().out
    assert "evokai_applications" in out and "Dry run" in out and "Key Vault" in out
    assert await _counts(owner, gid) == (1, 1)
    assert await asyncio.to_thread(run, "--yes") == 0
    assert "Purged module gateway" in capsys.readouterr().out
    assert await _counts(owner, gid) == (0, 0)


# Startup


@pytest.mark.parametrize("pinned", ["gateway", "applications,gateway"])
async def test_startup_runs_the_jobs_of_the_modules_that_are_on(
    tmp_path: Path, pinned: str
) -> None:
    import os

    from evokai.assistant.bootstrap import SLUG
    from evokai.main import create_app
    from evokai.services import build_services

    if os.environ.get("EVOKAI_TEST_DATABASE_URL"):
        pytest.skip("a fresh SQLite database is enough to start the service")
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'start.db'}", modules=pinned)
    svc = build_services(settings)
    await svc.db.create_all()
    app = create_app(settings, svc)
    async with app.router.lifespan_context(app):
        assert svc.modules_on == modules.parse(pinned)
        # The console assistant belongs to the applications module.
        async with svc.db.session() as s:
            q = sa.select(sa.func.count()).where(m.Application.slug == SLUG)
            assert bool(await s.scalar(q)) == ("applications" in pinned)
        assert (svc.channels._poller is not None) == ("applications" in pinned)
