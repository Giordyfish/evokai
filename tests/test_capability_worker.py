"""Capability workers: marked MCP servers, a call token per turn, model calls through Evokai.

A worker gets no Evokai key: with each call of a turn it gets a token Evokai signs for that turn
(application, conversation, person, agent, tags), valid only on ``/v1/workers/model`` and only
while the turn runs; usage is the token's person's, whatever the worker sends.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import sqlalchemy as sa

from evokai.core.security import WORKER_ISSUER
from evokai.db import models as m
from tests.conftest import MCP_URL, Env, Tool
from tests.test_capabilities import _adm

SECRET = "w" * 24  # gitleaks:allow (a fake secret of the tests)


async def _register(env: Env, name: str, *, worker: bool) -> None:
    r = await env.client.put(
        f"/agent/v1/admin/mcp-servers/{name}",
        json={
            "url": MCP_URL,
            "auth": "secret",
            "secret": SECRET,
            "tool_prefix": f"{name}_",
            "worker": worker,
        },
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text


def _spy(env: Env) -> list[dict[str, Any]]:
    bodies: list[dict[str, Any]] = []
    inner = env.mcp.handler

    def spy(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return inner(request)

    env.svc.http._transport.handler = spy  # ty: ignore[missing-attribute]
    return bodies


async def _use_servers(env: Env, *servers: str) -> None:
    cur = (await env.client.get("/agent/v1/admin/agents", headers=_adm(env))).json()["items"]
    helper = next(a for a in cur if a["name"] == "helper")
    helper.pop("version")
    helper["mcp_servers"] = ["app", *servers]
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
    assert r.status_code == 200, r.text


async def test_only_a_worker_gets_the_conversation_and_a_call_token(env: Env) -> None:
    bodies = _spy(env)
    await _register(env, "worker", worker=True)
    await _register(env, "partner", worker=False)
    await _use_servers(env, "worker", "partner")
    listed = (await env.client.get("/agent/v1/admin/mcp-servers", headers=_adm(env))).json()
    assert {x["name"]: x["worker"] for x in listed["items"]}["worker"] is True
    env.script.add(
        "function:primary",
        [Tool("worker_orders_month", {"period": "2026-10"}, "a")],
        [Tool("partner_orders_month", {"period": "2026-10"}, "b")],
        [Tool("orders_month", {"period": "2026-09"}, "c")],
        ["ok"],
    )
    tok = await env.token(tags=["team:north"])
    ev = await env.chat(tok["token"], text="October")
    calls = [b["params"] for b in bodies if b.get("method") == "tools/call"]
    meta = calls[0]["_meta"]
    assert set(meta) == {"evokai/conversation", "evokai/token"}
    assert meta["evokai/conversation"] == tok["conversationId"]
    claims = env.svc.keyring.verify_worker(meta["evokai/token"])
    assert (claims["sub"], claims["agt"], claims["tags"], claims["cid"], claims["tid"]) == (
        "u1",
        "helper",
        ["team:north"],
        tok["conversationId"],
        ev[0]["turnId"],
    )
    assert claims["exp"] - claims["iat"] <= 600
    # Another server with a secret, and a server acting as the person, learn nothing.
    assert "_meta" not in calls[1] and "_meta" not in calls[2]
    # A worker token is not a session token.
    r = await env.client.get(
        "/agent/v1/session/conversation",
        headers={"Authorization": f"Bearer {meta['evokai/token']}", "Origin": "http://app.test"},
    )
    assert r.status_code == 401
    # The turn is over: its token no longer calls models.
    r = await _model_call(env, meta["evokai/token"])
    assert (r.status_code, r.json()["error"]) == (401, "token")


async def test_a_worker_must_be_a_secret_url(env: Env) -> None:
    r = await env.client.put(
        "/agent/v1/admin/mcp-servers/w",
        json={"url": MCP_URL, "auth": "none", "worker": True},
        headers=_adm(env),
    )
    assert r.status_code == 400 and "worker" in r.text


async def _model_call(env: Env, bearer: str, **extra: Any) -> httpx.Response:
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart

    messages = ModelMessagesTypeAdapter.dump_python(
        [ModelRequest(parts=[UserPromptPart(content="Which button opens the invoice?")])],
        mode="json",
    )
    return await env.client.post(
        "/agent/v1/workers/model",
        json={"messages": messages, **extra},
        headers={"Authorization": f"Bearer {bearer}"},
    )


async def _running_token(env: Env, run_id: str = "turn-1", **over: Any) -> str:
    args: dict[str, Any] = {
        "run_id": run_id,
        "conversation": "conv-1",
        "subject": "u1",
        "agent": "helper",
        "tags": ["team:north"],
        "calls": 5,
        **over,
    }
    return await env.svc.mcp._worker_token(env.app_id, "worker", **args)


async def _usage(env: Env) -> list[m.UsageRecord]:
    async with env.svc.store.db.session() as s:
        q = sa.select(m.UsageRecord).where(m.UsageRecord.agent_name == "worker:helper")
        return list((await s.execute(q)).scalars().all())


async def test_a_valid_token_records_usage_as_the_person_whatever_the_worker_says(
    env: Env,
) -> None:
    await _register(env, "worker", worker=True)
    token = await _running_token(env)
    env.script.add("function:primary", ["The blue one."])
    # The body cannot name another person: who it is for is the token's.
    r = await _model_call(
        env, token, subject="someone-else", tags=["team:south"], settings={"openai_store": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["response"]["parts"][0]["content"] == "The blue one."
    (row,) = await _usage(env)
    assert (row.subject, row.tags, row.kind, row.session_id, row.turn_id) == (
        "u1",
        ",team:north,",
        "cap",
        "conv-1",
        "turn-1",
    )


async def test_tokens_that_must_not_work_are_refused(env: Env) -> None:
    await _register(env, "worker", worker=True)
    # The server key, and no token at all.
    assert (await _model_call(env, env.server_key)).status_code == 401
    assert (await _model_call(env, env.admin_key)).status_code == 401
    # A session token of the person.
    tok = await env.token()
    assert (await _model_call(env, tok["token"])).status_code == 401
    # An expired token (signed by Evokai, its time over).
    now = int(time.time())
    expired = jwt.encode(
        {
            "aud": env.app_id,
            "sub": "u1",
            "cid": "conv-1",
            "agt": "helper",
            "tags": [],
            "tid": "turn-1",
            "srv": "worker",
            "mcl": 5,
            "iss": WORKER_ISSUER,
            "iat": now - 900,
            "exp": now - 600,
            "jti": "x",
        },
        env.svc.keyring.signing_key,
        algorithm="HS256",
    )
    await _running_token(env)  # turn-1 still runs
    assert (await _model_call(env, expired)).status_code == 401
    # A token of another conversation's turn, which is over.
    other = await _running_token(env, run_id="turn-9", conversation="conv-9")
    await env.svc.mcp.end_run("turn-9")
    assert (await _model_call(env, other)).status_code == 401
    # A token whose turn ended.
    token = await _running_token(env, run_id="turn-2")
    await env.svc.mcp.end_run("turn-2")
    r = await _model_call(env, token)
    assert (r.status_code, r.json()["error"]) == (401, "token")
    assert await _usage(env) == []


async def test_the_turn_s_budget_and_a_spent_cap_stop_worker_calls(env: Env) -> None:
    await _register(env, "worker", worker=True)
    token = await _running_token(env, calls=1)
    env.script.add("function:primary", ["One."])
    assert (await _model_call(env, token)).status_code == 200
    r = await _model_call(env, token)
    assert (r.status_code, r.json()["error"]) == (429, "limit")
    fresh = await _running_token(env, run_id="turn-3")
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"monthly_cap_usd": 0}, headers=_adm(env)
    )
    assert r.status_code == 200
    r = await _model_call(env, fresh)
    assert (r.status_code, r.json()["error"]) == (429, "limit")
    # A worker no longer registered gets nothing either.
    await _register(env, "worker", worker=False)
    r = await _model_call(env, await _running_token(env, run_id="turn-4"))
    assert r.status_code == 403
