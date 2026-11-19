"""Capability credentials per application: encrypted, write-only, from the console and the file."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa

from evokai.capabilities import credentials, harness
from evokai.db import models as m
from tests.conftest import Env
from tests.test_capabilities import _adm

SECRET = "exa-secret-123456"  # gitleaks:allow (a fake key of the tests)


async def _put(env: Env, provider: str, values: dict[str, Any]) -> Any:
    return await env.client.put(
        f"/agent/v1/admin/capability-credentials/{provider}",
        json={"values": values},
        headers=_adm(env),
    )


async def _item(env: Env, provider: str) -> dict[str, Any]:
    r = await env.client.get("/agent/v1/admin/capability-credentials", headers=_adm(env))
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["items"] if x["provider"] == provider)


async def test_a_secret_is_encrypted_write_only_and_audited_without_its_value(
    env: Env, monkeypatch: Any
) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    before = await _item(env, "exa")
    assert before["fields"][0] == {
        "name": "api_key",
        "secret": True,
        "required": True,
        "env": "EXA_API_KEY",
        "set": False,
        "fromEnvironment": False,
    }
    assert before["capabilities"] == ["exa_agent", "exa_search"]
    r = await _put(env, "exa", {"api_key": SECRET})
    assert r.status_code == 200, r.text
    assert SECRET not in r.text and r.json()["fields"][0]["set"] is True
    # Never returned, never stored in clear, never in the audit log.
    listing = await env.client.get("/agent/v1/admin/capability-credentials", headers=_adm(env))
    assert SECRET not in listing.text
    async with env.svc.store.db.session() as s:
        row = (await s.execute(sa.select(m.CapabilityCredential))).scalar_one()
        audit = (await s.execute(sa.select(m.AuditEntry))).scalars().all()
    assert SECRET not in row.values_encrypted and row.names == ["api_key"]
    assert env.svc.keyring.decrypt(row.values_encrypted) == f'{{"api_key": "{SECRET}"}}'
    assert not any(SECRET in str(a.after) + str(a.before) for a in audit)
    assert any(a.action == "capability_credentials.set" for a in audit)
    # An empty value keeps it; null clears it.
    await _put(env, "exa", {"api_key": ""})
    assert (await _item(env, "exa"))["fields"][0]["set"] is True
    await _put(env, "exa", {"api_key": None})
    assert (await _item(env, "exa"))["fields"][0]["set"] is False


async def test_unknown_providers_and_fields_are_refused(env: Env) -> None:
    assert (await _put(env, "nope", {"x": "y"})).status_code == 404
    r = await _put(env, "exa", {"password": "x"})
    assert r.status_code == 400 and "has no credential password" in r.text


async def test_the_catalog_shows_missing_credentials_per_application(
    env: Env, monkeypatch: Any
) -> None:
    monkeypatch.delenv("POSTHOG_PERSONAL_API_KEY", raising=False)

    async def item(name: str) -> dict[str, Any]:
        view = (await env.client.get("/agent/v1/admin/grants", headers=_adm(env))).json()
        group = next(g for g in view["groups"] if g["id"] == "capabilities")
        return next(x for x in group["items"] if x["name"] == name)

    got = await item("posthog")
    assert got["credentials"] == "posthog" and got["missingCredentials"] == ["posthog.token"]
    await _put(env, "posthog", {"token": "phx_x"})
    assert (await item("posthog"))["missingCredentials"] == []
    # The deployment's environment is the fallback.
    await _put(env, "posthog", {"token": None})
    monkeypatch.setenv("POSTHOG_PERSONAL_API_KEY", "from-env")
    assert (await item("posthog"))["missingCredentials"] == []
    assert (await _item(env, "posthog"))["fields"][0]["fromEnvironment"] is True
    # A per-person integration needs no application credential: each person's own account.
    assert (await item("linear"))["missingCredentials"] == []


async def test_the_application_file_sets_credentials(env: Env, monkeypatch: Any) -> None:
    from evokai.agents.bootstrap import bootstrap

    monkeypatch.setenv("ACME_RENDER_KEY", "rnd_secret")
    import pathlib
    import tempfile

    from tests.conftest import AGENTS_YAML

    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "skills").mkdir()
        text = AGENTS_YAML + (
            "\ncapability_credentials:\n"
            "  render:\n"
            '    api_key: "${ACME_RENDER_KEY}"\n'
            "    workspace_id: tea-123\n"
        )
        (root / "agents.yaml").write_text(text, encoding="utf-8")
        app_id = await bootstrap(env.svc.store, env.svc.keyring, root / "agents.yaml", {})
    kept = await credentials.stored(env.svc.store, env.svc.keyring, app_id)
    assert kept["render"] == {"api_key": "rnd_secret", "workspace_id": "tea-123"}
    view = await credentials.view(env.svc.store, app_id, env={})
    render = next(x for x in view if x["provider"] == "render")
    assert {f["name"]: f.get("value") for f in render["fields"]} == {
        "api_key": None,
        "workspace_id": "tea-123",
    }


def test_integrations_get_the_application_s_token() -> None:
    ctx = harness.Context(
        engine=None,
        agent=None,
        rs=None,  # ty: ignore[invalid-argument-type]
        scope=None,  # ty: ignore[invalid-argument-type]
        secrets={"github": {"token": "ghp_app"}, "posthog": {"token": "ph_app"}},
    )
    gh = harness.with_credentials(ctx, "github", {"read_only": True, "acts_as": "application"})
    assert gh.wrapped.auth == "ghp_app" and gh.wrapped.read_only is True
    assert harness.with_credentials(ctx, "posthog", {}).auth == "ph_app"


def test_search_and_sandboxes_get_clients_with_the_application_s_keys() -> None:
    ctx = harness.Context(
        engine=None,
        agent=None,
        rs=None,  # ty: ignore[invalid-argument-type]
        scope=None,  # ty: ignore[invalid-argument-type]
        secrets={
            "exa": {"api_key": "exa_app"},
            "e2b": {"api_key": "e2b_app"},
            "boxd": {"api_key": "boxd_app"},
            "opensandbox": {"api_key": "os_app", "domain": "sandbox.example"},
        },
    )
    exa = harness.with_credentials(ctx, "exa_search", {})
    assert getattr(exa.client, "api_key", None) == "exa_app" or "exa_app" in str(vars(exa.client))
    e2b = harness.with_credentials(ctx, "e2b_sandbox", {})
    assert e2b.client is not None
    boxd = harness.with_credentials(ctx, "boxd_sandbox", {})
    assert boxd.client is not None
    os_box = harness.with_credentials(ctx, "opensandbox", {})
    assert os_box.connection.domain == "sandbox.example"


def test_a_missing_credential_leaves_the_capability_out(monkeypatch: Any) -> None:
    from evokai import capabilities as caps

    monkeypatch.delenv("PYLON_ACCESS_TOKEN", raising=False)
    ctx = SimpleNamespace(secrets={})
    try:
        harness.with_credentials(ctx, "pylon", {})  # ty: ignore[invalid-argument-type]
    except caps.CapabilityError as e:
        assert "pylon.token" in str(e)
    else:
        raise AssertionError("built without a token")
