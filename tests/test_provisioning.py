"""Provisioning for infrastructure: applications and keys from the environment, database TLS."""

from __future__ import annotations

import ssl

import pytest

from evokai.agents.bootstrap import bootstrap_apps
from evokai.core.config import BootstrapApp, Settings
from evokai.core.errors import ConfigError
from evokai.core.security import generate_key
from evokai.db.engine import postgres_tls
from tests.conftest import ORIGIN, Env


async def test_an_application_key_provisioned_from_the_environment_works(env: Env) -> None:
    key = generate_key("srv").value
    apps = [BootstrapApp(id="test", serverKey=key, origins=[ORIGIN, "https://b.example"])]
    assert await bootstrap_apps(env.svc.store, apps) == [env.app_id]
    assert await bootstrap_apps(env.svc.store, apps) == [env.app_id]  # idempotent
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    app = await env.svc.store.applications.get(env.app_id)
    assert app is not None and app.allowed_origins == [ORIGIN, "https://b.example"]


async def test_a_key_cannot_be_moved_to_another_application(env: Env) -> None:
    with pytest.raises(ConfigError, match="another application"):
        await bootstrap_apps(env.svc.store, [BootstrapApp(id="other", serverKey=env.server_key)])


async def test_a_new_application_from_the_environment(env: Env) -> None:
    [app_id] = await bootstrap_apps(env.svc.store, [BootstrapApp(id="new", voiceEngine="server")])
    app = await env.svc.store.applications.get(app_id)
    assert app is not None and app.slug == "new" and app.defaults == {"voice_engine": "server"}


def test_environment_names_used_by_infrastructure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVOKAI_CONFIG_FILE", "/config/bookshop.yaml")
    monkeypatch.setenv("EVOKAI_DB_CA_FILE", "/certs/rds.pem")
    monkeypatch.setenv("EVOKAI_BOOTSTRAP_APPS", '[{"id": "bookshop", "serverKey": "evk_srv_x_y"}]')
    s = Settings()
    assert s.bootstrap_file == "/config/bookshop.yaml"
    assert s.db_ca_file == "/certs/rds.pem"
    assert s.bootstrap_apps[0].id == "bookshop"


def test_postgres_tls_modes() -> None:
    base = "postgresql+asyncpg://u:p@db:5432/x"
    url, tls = postgres_tls(f"{base}?sslmode=verify-full&application_name=e", Settings())
    assert url == f"{base}?application_name=e"
    assert isinstance(tls, ssl.SSLContext) and tls.check_hostname
    _, tls = postgres_tls(f"{base}?sslmode=require", Settings())
    assert isinstance(tls, ssl.SSLContext) and tls.verify_mode == ssl.CERT_NONE
    assert postgres_tls(f"{base}?sslmode=disable", Settings()) == (base, False)
    assert postgres_tls(base, Settings()) == (base, None)
    assert postgres_tls("sqlite+aiosqlite:///x.db", Settings()) == (
        "sqlite+aiosqlite:///x.db",
        None,
    )
