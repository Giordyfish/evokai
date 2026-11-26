"""Admin console: key → cookie login, CSRF header, operator, console views, static files."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import evokai.core.util as util
from evokai.api.console_routes import _migration_head
from evokai.main import create_app
from tests.conftest import Env, Tool

CONSOLE = {"X-Evokai-Console": "1"}
OPERATOR_KEY = "evk_op_test_operator_key_0123456789"  # gitleaks:allow


async def _login(env: Env, key: str) -> httpx.Response:
    return await env.client.post("/agent/v1/console/login", json={"key": key})


async def test_an_admin_key_becomes_a_strict_httponly_cookie(env: Env) -> None:
    r = await _login(env, env.admin_key)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/agent" in cookie
    assert env.admin_key not in r.headers["set-cookie"]
    me = (await env.client.get("/agent/v1/console/me")).json()
    assert (me["kind"], me["applicationId"]) == ("adm", env.app_id)

    no_header = await env.client.get("/agent/v1/admin/application")
    assert (no_header.status_code, no_header.json()["error"]) == (403, "console_header")
    ok = await env.client.get("/agent/v1/admin/application", headers=CONSOLE)
    assert ok.status_code == 200 and ok.json()["id"] == env.app_id
    usage = await env.client.get("/agent/v1/usage", headers=CONSOLE)
    assert usage.status_code == 200

    assert (await env.client.post("/agent/v1/console/logout")).status_code == 204
    assert (await env.client.get("/agent/v1/console/me")).status_code == 401


async def test_server_keys_and_wrong_keys_cannot_log_in_and_retries_are_capped(env: Env) -> None:
    assert (await _login(env, env.server_key)).status_code == 401
    for _ in range(9):
        assert (await _login(env, "evk_adm_nope_wrongsecret")).status_code == 401
    assert (await _login(env, env.admin_key)).status_code == 429


async def test_an_operator_login_ends_when_the_operator_key_changes(env: Env) -> None:
    from evokai.assistant.delegation import mint_delegation

    env.svc.settings.operator_key = OPERATOR_KEY
    assert (await _login(env, OPERATOR_KEY)).status_code == 204
    code = await env.client.post("/agent/v1/console/login-code", headers=_bearer(OPERATOR_KEY))
    login = env.operator_login()
    token = mint_delegation(env.svc.keyring, login, env.app_id, "owner")
    assistant = {"Authorization": f"Bearer {token}"}
    assert (await env.client.get("/agent/v1/console/me")).status_code == 200
    assert (await env.client.get("/agent/v1/admin/application", headers=assistant)).is_success

    # The key leaked and was rotated: the cookie, a pending sign-in code and the assistant's
    # delegation made with the old key all stop at once, not when they expire.
    env.svc.settings.operator_key = OPERATOR_KEY + "_rotated"
    me = await env.client.get("/agent/v1/console/me")
    assert (me.status_code, me.json()["error"]) == (401, "console")
    r = await env.client.post("/agent/v1/console/login", json={"code": code.json()["code"]})
    assert (r.status_code, r.json()["error"]) == (401, "login_code")
    r = await env.client.get("/agent/v1/admin/application", headers=assistant)
    assert (r.status_code, r.json()["error"]) == (401, "delegation")

    env.svc.settings.operator_key = None  # removed altogether
    assert (
        await env.client.get("/agent/v1/admin/application", headers=assistant)
    ).status_code == 401
    assert (await _login(env, OPERATOR_KEY + "_rotated")).status_code == 401


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_the_operator_creates_applications_and_picks_one(env: Env) -> None:
    env.svc.settings.operator_key = OPERATOR_KEY
    assert (await _login(env, OPERATOR_KEY)).status_code == 204
    created = await env.client.post(
        "/agent/v1/operator/applications",
        json={"slug": "crm", "name": "CRM", "allowed_origins": ["https://crm.example"]},
        headers=CONSOLE,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["serverKey"].startswith("evk_srv_") and body["adminKey"].startswith("evk_adm_")
    listed = (await env.client.get("/agent/v1/operator/applications", headers=CONSOLE)).json()
    assert {a["slug"] for a in listed["items"]} == {"test", "crm"}

    pick = await env.client.get("/agent/v1/admin/application", headers=CONSOLE)
    assert (pick.status_code, pick.json()["error"]) == (403, "application_required")
    crm = await env.client.get(
        "/agent/v1/admin/application", headers={**CONSOLE, "X-Evokai-Application": body["id"]}
    )
    assert crm.json()["slug"] == "crm"

    as_bearer = await env.client.get(
        "/agent/v1/operator/applications", headers={"Authorization": f"Bearer {OPERATOR_KEY}"}
    )
    assert as_bearer.status_code == 200
    admin = await env.client.get(
        "/agent/v1/operator/applications", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert admin.status_code == 401


async def test_console_views_versions_mcp_test_and_conversations(env: Env) -> None:
    await _login(env, env.admin_key)
    versions = await env.client.get("/agent/v1/admin/agents/helper/versions", headers=CONSOLE)
    assert versions.json()["items"][0]["active"] is True

    no_user = await env.client.post(
        "/agent/v1/admin/mcp-servers/app/test", json={}, headers=CONSOLE
    )
    assert no_user.json()["ok"] is False
    tested = await env.client.post(
        "/agent/v1/admin/mcp-servers/app/test", json={"credential": "someone"}, headers=CONSOLE
    )
    assert {t["name"] for t in tested.json()["tools"]} >= {"orders_month", "order_refund"}

    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["Fatto."])
    tok = (await env.client.post(
        "/agent/v1/session-tokens", json={"subject": "ada", "agent": "helper"}, headers=CONSOLE
    )).json()["token"]  # fmt: skip
    # The playground runs on the service's own origin.
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "ottobre?"},
        headers={"Authorization": f"Bearer {tok}", "Origin": "http://evokai"},
    )
    assert r.status_code == 200
    items = (await env.client.get("/agent/v1/admin/conversations", headers=CONSOLE)).json()
    assert items["storage"] == "evokai" and items["items"][0]["subject"] == "ada"
    one = await env.client.get(
        f"/agent/v1/admin/conversations/{items['items'][0]['id']}", headers=CONSOLE
    )
    assert [m["role"] for m in one.json()["messages"]][:2] == ["user", "assistant"]
    assert one.json()["turns"][0]["status"] == "done"

    await env.client.patch(
        "/agent/v1/admin/application", json={"storage_mode": "external"}, headers=CONSOLE
    )
    external = (await env.client.get("/agent/v1/admin/conversations", headers=CONSOLE)).json()
    assert external == {"storage": "external", "items": []}


async def test_the_console_is_served_with_client_side_routes(env: Env, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>console</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    settings = env.svc.settings.model_copy(update={"admin_dist_dir": str(dist)})
    app = create_app(settings, env.svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evokai"
    ) as c:
        bare = await c.get("/agent/admin")
        assert (bare.status_code, bare.headers["location"]) == (307, "/agent/admin/")
        index = await c.get("/agent/admin/")
        assert index.status_code == 200 and "console" in index.text
        page = await c.get("/agent/admin/agents/helper")
        assert page.status_code == 200 and "console" in page.text
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        asset = await c.get("/agent/admin/assets/app.js")
        assert asset.text == "console.log(1)" and "immutable" in asset.headers["cache-control"]
        escape = await c.get("/agent/admin/..%2Fsecret.txt")
        assert "nope" not in escape.text


@pytest.mark.parametrize("enabled", [False])
async def test_the_console_can_be_turned_off(env: Env, enabled: bool) -> None:
    app = create_app(env.svc.settings.model_copy(update={"admin_ui_enabled": enabled}), env.svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evokai"
    ) as c:
        assert (await c.get("/agent/admin")).status_code == 404


async def test_a_console_login_ends_when_its_key_is_revoked(env: Env) -> None:
    second = await env.client.post(
        "/agent/v1/admin/keys",
        json={"kind": "adm", "label": "second"},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    key, key_id = second.json()["key"], second.json()["id"]
    await _login(env, key)
    me = (await env.client.get("/agent/v1/console/me")).json()
    assert me["keyId"] == key_id and key.startswith(f"evk_adm_{me['keyPrefix']}_")
    keys = (await env.client.get("/agent/v1/admin/keys", headers=CONSOLE)).json()["items"]
    assert [k["current"] for k in keys if k["id"] == key_id] == [True]
    mine = await env.client.delete(f"/agent/v1/admin/keys/{key_id}", headers=CONSOLE)
    assert (mine.status_code, mine.json()["error"]) == (400, "self_revoke")
    revoked = await env.client.delete(
        f"/agent/v1/admin/keys/{key_id}", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert revoked.status_code == 200
    gone = await env.client.get("/agent/v1/admin/application", headers=CONSOLE)
    assert (gone.status_code, gone.json()["error"]) == (401, "console")
    assert (await env.client.get("/agent/v1/console/me")).status_code == 401


async def test_rotating_a_key_keeps_the_old_one_until_it_is_revoked(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    srv = next(
        k
        for k in (await env.client.get("/agent/v1/admin/keys", headers=adm)).json()["items"]
        if k["kind"] == "srv"
    )
    r = await env.client.post(f"/agent/v1/admin/keys/{srv['id']}/rotate", headers=adm)
    body = r.json()
    assert body["key"].startswith("evk_srv_") and body["replaces"] == srv["id"]
    both = {"Authorization": f"Bearer {body['key']}"}
    assert (await env.client.get("/agent/v1/agents", headers=both)).status_code == 200
    assert (await env.client.get("/agent/v1/agents", headers=_srv(env))).status_code == 200
    missing = await env.client.post("/agent/v1/admin/keys/nope/rotate", headers=adm)
    assert missing.status_code == 404


def _srv(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.server_key}"}


async def test_the_console_lists_its_applications_with_their_month(env: Env) -> None:
    env.script.add("function:primary", ["Ciao."])
    tok = await env.token()
    await env.chat(tok["token"], text="Ciao")
    await _login(env, env.admin_key)
    assert (await env.client.get("/agent/v1/console/applications")).status_code == 403
    r = await env.client.get("/agent/v1/console/applications", headers=CONSOLE)
    body = r.json()
    assert body["canCreate"] is False
    (app,) = body["items"]
    assert app["slug"] == "test" and app["turnsMonth"] == 1 and app["subjectsMonth"] == 1
    assert app["counts"] == {
        "agents": 1,
        "mcpServers": 1,
        "skills": 1,
        "keys": 2,
        "overrides": 0,
        "workflows": 0,
        "approvals": 0,
    }
    assert app["caps"]["monthly"] is True and app["usage"]["monthPct"] >= 0
    assert abs(app["usage"]["monthLeftPct"] + app["usage"]["monthPct"] - 100) < 0.01
    assert not [k for k in app if "spend" in k.lower()] and "monthlyUsd" not in app["caps"]
    assert app["grants"]["serverRuns"] is True

    env.svc.settings.operator_key = OPERATOR_KEY
    await env.client.post(
        "/agent/v1/operator/applications",
        json={"slug": "crm", "name": "CRM"},
        headers={"Authorization": f"Bearer {OPERATOR_KEY}"},
    )
    await _login(env, OPERATOR_KEY)
    body = (await env.client.get("/agent/v1/console/applications", headers=CONSOLE)).json()
    assert body["canCreate"] is True and {a["slug"] for a in body["items"]} == {"test", "crm"}


async def test_health_checks_what_needs_no_user(env: Env) -> None:
    env.script.add("function:primary", ["Ciao."])
    await env.chat((await env.token())["token"], text="Ciao")
    await env.client.put(
        "/agent/v1/admin/mcp-servers/service",
        json={"url": "http://app.test/mcp", "auth": "secret", "secret": "s3cret"},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    await _login(env, env.admin_key)
    h = (await env.client.get("/agent/v1/console/health?check=true", headers=CONSOLE)).json()
    assert h["database"]["status"] == "ok" and h["database"]["head"] == _migration_head()
    assert [m["model"] for m in h["models"]["items"]] == ["primary"]
    assert h["models"]["items"][0]["calls"] == 1
    servers = {s["name"]: s for s in h["mcp"]}
    assert servers["service"]["status"] == "ok" and servers["service"]["tools"] == 3
    assert servers["app"]["auth"] == "forward_user"
    assert "Bearer s3cret" in env.mcp.auth
    assert h["voice"]["enabled"] is False and h["build"]["python"]


async def test_activity_usage_summary_skill_and_mcp_details(env: Env) -> None:
    env.script.add("function:primary", ["Ciao."])
    tok = await env.token(tags=["team:ops"])
    await env.chat(tok["token"], text="Ciao")
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "client", "name": "memory", "value": False}]},
        headers=adm,
    )
    items = (await env.client.get("/agent/v1/admin/activity", headers=adm)).json()["items"]
    assert {i["kind"] for i in items} >= {"agent", "key_created", "grant"}
    summary = (await env.client.get("/agent/v1/admin/usage/summary", headers=adm)).json()
    assert summary["turns"] == 1 and summary["subjects"] == 1 and summary["tags"] == ["team:ops"]
    assert summary["fallbackTurns"] == 0 and summary["medianFirstTokenMs"] is not None
    none = await env.client.get("/agent/v1/admin/usage/summary?tag=team:sales", headers=adm)
    assert none.json()["turns"] == 0
    by_tags = await env.client.get("/agent/v1/usage?groupBy=subject,tags", headers=adm)
    assert by_tags.json()["rows"][0]["tags"] == ["team:ops"]
    skill = (await env.client.get("/agent/v1/admin/skills/procedura", headers=adm)).json()
    assert skill["body"].startswith("Primo passo") and skill["size"] > 0
    assert (await env.client.get("/agent/v1/admin/skills/nope", headers=adm)).status_code == 404
    servers = (await env.client.get("/agent/v1/admin/mcp-servers", headers=adm)).json()["items"]
    assert servers[0]["status"] == "ok" and len(servers[0]["tools"]) == 3


async def test_the_export_is_a_config_file_that_bootstraps_the_same_application(
    env: Env, tmp_path: Path
) -> None:
    from evokai.agents.bootstrap import bootstrap

    adm = {"Authorization": f"Bearer {env.admin_key}"}
    await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "model", "name": "function:backup", "value": "fallback"}]},
        headers=adm,
    )
    r = (await env.client.get("/agent/v1/admin/export", headers=adm)).json()
    assert r["filename"] == "test.yaml"
    text = r["yaml"].replace("slug: test", "slug: copy")
    (tmp_path / "copy.yaml").write_text(text, encoding="utf-8")
    copy_id = await bootstrap(env.svc.store, env.svc.keyring, tmp_path / "copy.yaml", {})
    original = await env.svc.store.agents.active(env.app_id, "helper")
    copied = await env.svc.store.agents.active(copy_id, "helper")
    assert original and copied and original.version == copied.version
    grants = await env.svc.store.grants.all(copy_id)
    assert [(g.kind, g.name, g.value) for g in grants] == [("model", "function:backup", "fallback")]


async def test_saving_an_agent_records_who_saved_it_and_restoring_reactivates(env: Env) -> None:
    await _login(env, env.admin_key)
    versions = (
        await env.client.get("/agent/v1/admin/agents/helper/versions", headers=CONSOLE)
    ).json()["items"]
    first = versions[0]
    changed = {**first["definition"], "title": "Helper 2"}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=changed, headers=CONSOLE)
    assert r.json()["created"] is True
    versions = (
        await env.client.get("/agent/v1/admin/agents/helper/versions", headers=CONSOLE)
    ).json()["items"]
    me = (await env.client.get("/agent/v1/console/me")).json()
    assert versions[0]["createdBy"] == me["keyId"] and versions[1]["createdBy"] == "config"
    restored = await env.client.put(
        "/agent/v1/admin/agents/helper", json=first["definition"], headers=CONSOLE
    )
    assert restored.json() == {"version": first["version"], "created": False, "published": True}
    versions = (
        await env.client.get("/agent/v1/admin/agents/helper/versions", headers=CONSOLE)
    ).json()["items"]
    assert [v["active"] for v in versions] == [False, True]


async def _code(env: Env, key: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return await env.client.post("/agent/v1/console/login-code", headers=headers)


async def test_a_login_code_needs_a_valid_key_and_signs_in_once(env: Env) -> None:
    assert (await _code(env, None)).status_code == 401
    assert (await _code(env, env.server_key)).status_code == 401
    issued = await _code(env, env.admin_key)
    assert issued.status_code == 200
    code = issued.json()["code"]
    assert len(code) >= 43 and issued.json()["expiresIn"] == 60
    assert "set-cookie" not in issued.headers

    r = await env.client.post("/agent/v1/console/login", json={"code": code})
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/agent" in cookie
    me = (await env.client.get("/agent/v1/console/me")).json()
    assert (me["kind"], me["applicationId"]) == ("adm", env.app_id)
    assert me["keyId"] is not None

    env.client.cookies.clear()
    again = await env.client.post("/agent/v1/console/login", json={"code": code})
    assert (again.status_code, again.json()["error"]) == (401, "login_code")
    assert (await env.client.get("/agent/v1/console/me")).status_code == 401


async def test_the_operator_key_gets_an_operator_code(env: Env) -> None:
    env.svc.settings.operator_key = OPERATOR_KEY
    code = (await _code(env, OPERATOR_KEY)).json()["code"]
    assert (
        await env.client.post("/agent/v1/console/login", json={"code": code})
    ).status_code == 204
    assert (await env.client.get("/agent/v1/console/me")).json()["kind"] == "op"


async def test_a_login_code_expires_after_a_minute(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = (await _code(env, env.admin_key)).json()["code"]
    now = util.time.monotonic()
    monkeypatch.setattr(util.time, "monotonic", lambda: now + 61)
    r = await env.client.post("/agent/v1/console/login", json={"code": code})
    assert (r.status_code, r.json()["error"]) == (401, "login_code")


async def test_login_takes_exactly_one_of_key_or_code(env: Env) -> None:
    code = (await _code(env, env.admin_key)).json()["code"]
    both = await env.client.post(
        "/agent/v1/console/login", json={"key": env.admin_key, "code": code}
    )
    assert both.status_code == 400
    assert (await env.client.post("/agent/v1/console/login", json={})).status_code == 400


async def test_bad_codes_and_bad_code_requests_count_as_failed_attempts(env: Env) -> None:
    for _ in range(5):
        r = await env.client.post("/agent/v1/console/login", json={"code": "x" * 43})
        assert (r.status_code, r.json()["error"]) == (401, "login_code")
    for _ in range(5):
        assert (await _code(env, "evk_adm_nope_wrongsecret")).status_code == 401
    assert (await _code(env, env.admin_key)).status_code == 429
    assert (await _login(env, env.admin_key)).status_code == 429


async def test_a_code_from_a_key_revoked_meanwhile_does_not_sign_in(env: Env) -> None:
    code = (await _code(env, env.admin_key)).json()["code"]
    await _login(env, env.admin_key)
    key_id = (await env.client.get("/agent/v1/console/me")).json()["keyId"]
    assert await env.svc.store.applications.revoke_key(env.app_id, key_id)
    env.svc.auth._alive.clear()
    env.client.cookies.clear()
    r = await env.client.post("/agent/v1/console/login", json={"code": code})
    assert (r.status_code, r.json()["error"]) == (401, "login_code")
