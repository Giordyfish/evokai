"""Applications, keys and session tokens."""

from __future__ import annotations

import time

import jwt

from evokai.core.security import generate_key
from tests.conftest import ORIGIN, Env, system_text


async def test_minting_needs_a_server_key(env: Env) -> None:
    r = await env.client.post("/agent/v1/session-tokens", json={"subject": "u1", "agent": "helper"})
    assert r.status_code == 401
    bad = generate_key("srv").value
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper"},
        headers={"Authorization": f"Bearer {bad}"},
    )
    assert (r.status_code, r.json()["error"]) == (401, "key")


async def test_unknown_agent_is_404(env: Env) -> None:
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "nope"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 404


async def test_session_token_is_short_lived_and_carries_no_credential(env: Env) -> None:
    tok = await env.token(ttlSeconds=99999)
    claims = jwt.decode(tok["token"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] <= 900
    assert "user-token" not in tok["token"]
    assert claims["crd"] is True


async def test_expired_or_forged_tokens_are_refused(env: Env) -> None:
    forged = jwt.encode(
        {
            "sub": "u1",
            "aud": env.app_id,
            "iss": "evokai",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "jti": "x",
        },
        "not-the-key-but-long-enough-for-hs256!!",
        algorithm="HS256",
    )
    r = await env.client.get("/agent/v1/session", headers={"Authorization": f"Bearer {forged}"})
    assert (r.status_code, r.json()["error"]) == (401, "token")


async def test_browser_origin_must_be_allowed(env: Env) -> None:
    tok = await env.token()
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "x"},
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": "http://evil.test"},
    )
    assert (r.status_code, r.json()["error"]) == (403, "origin")
    r = await env.client.get(
        "/agent/v1/session", headers={"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    )
    assert r.status_code == 200


async def test_a_subject_cannot_continue_another_subjects_conversation(env: Env) -> None:
    env.script.add("function:primary", ["ciao"])
    mine = await env.token("alice")
    await env.chat(mine["token"], text="segreto")
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "bob", "agent": "helper", "conversationId": mine["conversationId"]},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 404
    r = await env.client.get(
        f"/agent/v1/conversations/{mine['conversationId']}?subject=bob",
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 404


async def test_admin_endpoints_need_an_admin_key(env: Env) -> None:
    r = await env.client.get(
        "/agent/v1/admin/agents", headers={"Authorization": f"Bearer {env.server_key}"}
    )
    assert (r.status_code, r.json()["error"]) == (403, "admin_key")
    r = await env.client.get(
        "/agent/v1/admin/agents", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert [a["name"] for a in r.json()["items"]] == ["helper"]


async def test_key_rotation_revokes_the_old_key(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    new = (
        await env.client.post(
            "/agent/v1/admin/keys", json={"kind": "srv", "label": "rotation"}, headers=adm
        )
    ).json()
    keys = (await env.client.get("/agent/v1/admin/keys", headers=adm)).json()["items"]
    old_id = next(k["id"] for k in keys if k["kind"] == "srv" and k["label"] == "bootstrap")
    assert (
        await env.client.delete(f"/agent/v1/admin/keys/{old_id}", headers=adm)
    ).status_code == 200
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 401
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper"},
        headers={"Authorization": f"Bearer {new['key']}"},
    )
    assert r.status_code == 200


async def test_agent_update_is_a_new_version_and_open_conversations_move_to_it(env: Env) -> None:
    # An open conversation used to keep the version it started with: after a release it kept the
    # old instructions and settings (no prompt cache) for as long as it stayed open.
    env.script.add("function:primary", ["v1"], ["ancora v1"], ["v2"])
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    old = await env.token()
    await env.chat(old["token"], text="ciao")
    agent = (await env.client.get("/agent/v1/admin/agents", headers=adm)).json()["items"][0]
    agent.pop("version")
    agent["instructions"] = "Nuove istruzioni."
    r = await env.client.put("/agent/v1/admin/agents/helper", json=agent, headers=adm)
    assert r.json()["created"] is True
    again = await env.token(conversationId=old["conversationId"])
    await env.chat(again["token"], text="ancora")
    assert "Nuove istruzioni." in system_text(env.script.seen[-1][1])
    fresh = await env.token()
    await env.chat(fresh["token"], text="nuovo")
    assert "Nuove istruzioni." in system_text(env.script.seen[-1][1])


async def test_usage_report_groups_by_subject_and_tag(env: Env) -> None:
    env.script.add("function:primary", ["a"], ["b"])
    t1 = await env.token("alice", tags=["ufficio"])
    t2 = await env.token("bob")
    await env.chat(t1["token"], text="x")
    await env.chat(t2["token"], text="y")
    srv = {"Authorization": f"Bearer {env.server_key}"}
    rows = (await env.client.get("/agent/v1/usage?groupBy=subject", headers=srv)).json()["rows"]
    assert sorted(r["subject"] for r in rows) == ["alice", "bob"]
    tagged = (
        await env.client.get("/agent/v1/usage?groupBy=subject&tag=ufficio", headers=srv)
    ).json()["rows"]
    assert [r["subject"] for r in tagged] == ["alice"]
    bad = await env.client.get("/agent/v1/usage?groupBy=password", headers=srv)
    assert bad.status_code == 400


async def test_errors_never_leak_internal_text(env: Env) -> None:
    r = await env.client.get("/agent/v1/session", headers={"Authorization": "Bearer x.y.z"})
    assert set(r.json()) == {"error", "message"}
    assert r.headers["x-correlation-id"]
