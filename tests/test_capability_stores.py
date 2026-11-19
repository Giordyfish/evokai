"""Harness capability stores in Evokai's database: per person or conversation, then erased."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from pydantic_ai.messages import ModelRequest, ToolReturnPart

from evokai.db import models as m
from tests.conftest import Env, Tool
from tests.test_capabilities import _adm, _enable


def _returns(messages: list[Any]) -> list[str]:
    return [
        str(p.content)
        for x in messages
        if isinstance(x, ModelRequest)
        for p in x.parts
        if isinstance(p, ToolReturnPart)
    ]


async def _records(env: Env, capability: str) -> list[m.CapabilityRecord]:
    async with env.svc.store.db.session() as s:
        q = sa.select(m.CapabilityRecord).where(m.CapabilityRecord.capability == capability)
        return list((await s.execute(q)).scalars().all())


async def test_memory_files_are_kept_per_person_and_follow_consent(env: Env) -> None:
    await _enable(env, "memory")
    env.script.add(
        "function:primary",
        [Tool("write_memory", {"content": "Prefers short answers."})],
        ["Noted."],
    )
    tok = await env.token()
    await env.chat(tok["token"], text="Remember: short answers")
    rows = await _records(env, "memory")
    assert len(rows) == 1 and rows[0].subject == "u1" and rows[0].scope == "helper"
    assert "short answers" in rows[0].value and rows[0].session_id is None
    # A later conversation of the same person reads it back.
    env.script.add(
        "function:primary",
        [Tool("read_memory", {"file": "MEMORY.md"})],
        ["You like short answers."],
    )
    tok2 = await env.token()
    await env.chat(tok2["token"], text="What do you know?")
    assert any("short answers" in r for r in _returns(env.script.seen[-1][1]))
    # Another person sees nothing of it.
    env.script.add("function:primary", [Tool("read_memory", {"file": "MEMORY.md"})], ["Nothing."])
    other = await env.token(subject="u2")
    await env.chat(other["token"], text="What do you know?")
    assert not any("short answers" in r for r in _returns(env.script.seen[-1][1]))
    # Withdrawing consent deletes the files.
    r = await env.client.post(
        "/agent/v1/session/memory-consent",
        json={"granted": False},
        headers={"Authorization": f"Bearer {tok2['token']}", "Origin": "http://app.test"},
    )
    assert r.status_code == 200, r.text
    assert await _records(env, "memory") == []


async def _external(env: Env) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"storage_mode": "external"}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text


async def test_memory_needs_evokai_storage(env: Env) -> None:
    await _enable(env, "memory")
    await _external(env)
    tok = await env.token()
    await env.chat(tok["token"], text="Hello")
    assert "write_memory" not in env.script.tools_seen


async def test_a_plan_is_kept_with_the_conversation_and_goes_with_it(env: Env) -> None:
    await _enable(env, "planning")
    plan = {"items": [{"content": "Check September"}, {"content": "Check October"}]}
    env.script.add("function:primary", [Tool("write_plan", plan)], ["Planned."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Plan the review")
    rows = await _records(env, "planning")
    assert len(rows) == 1 and "Check October" in rows[0].value
    assert rows[0].session_id is not None
    # The next turn reads the same plan.
    env.script.add("function:primary", [Tool("read_plan", {})], ["Two steps."])
    await env.chat(tok["token"], text="Where are we?")
    assert any("Check September" in r for r in _returns(env.script.seen[-1][1]))
    # Deleting the conversation deletes its plan.
    del ev
    r = await env.client.delete(
        f"/agent/v1/conversations/{rows[0].session_id}",
        params={"subject": "u1"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 200, r.text
    assert await _records(env, "planning") == []


async def test_external_storage_keeps_no_plan_at_rest(env: Env) -> None:
    await _enable(env, "planning")
    plan = {"items": [{"content": "Check September"}]}
    env.script.add("function:primary", [Tool("write_plan", plan)], ["Planned."])
    await _external(env)
    tok = await env.token()
    await env.chat(tok["token"], text="Plan", history=[])
    assert await _records(env, "planning") == []


async def test_step_persistence_logs_the_conversation_in_the_database(env: Env) -> None:
    await _enable(env, "step_persistence")
    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["October."])
    tok = await env.token()
    await env.chat(tok["token"], text="October?")
    rows = await _records(env, "step_persistence")
    kinds = {r.key.split("/")[0] for r in rows}
    assert {"run", "event", "snapshots"} <= kinds
    assert all(r.session_id is not None for r in rows)
    # Erasing the person deletes them.
    r = await env.client.post(
        "/agent/v1/privacy/erase",
        json={"subject": "u1"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 200, r.text
    assert await _records(env, "step_persistence") == []


async def test_a_spilled_tool_result_is_kept_with_the_conversation(env: Env) -> None:
    from pydantic_ai_harness.tool_output_limits import READ_TOOL_NAME

    await _enable(env, "tool_output_limits", {"over": 300})
    env.script.add("function:primary", [Tool("orders_month", {"period": "y" * 900})])

    def read_back(messages: list[Any]) -> list[Any]:
        got = _returns(messages)[-1]
        handle = got.split("handle '")[1].split("'")[0]
        return [Tool(READ_TOOL_NAME, {"handle": handle, "offset": 0, "limit": 50}, "r1")]

    env.script.add("function:primary", read_back, ["Done."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Big")
    assert ev[-1]["reason"] == "end_turn"
    rows = await _records(env, "tool_output_limits")
    assert len(rows) == 1 and rows[0].session_id is not None and rows[0].expires_at is not None
    assert "yyyy" in _returns(env.script.seen[-1][1])[-1]


async def test_conversation_search_reads_only_the_person_s_own_conversations(env: Env) -> None:
    env.script.add("function:primary", ["The tulip budget is ready."])
    first = await env.token()
    await env.chat(first["token"], text="Prepare the tulip budget")
    env.script.add("function:primary", ["Sure."])
    stranger = await env.token(subject="u2")
    await env.chat(stranger["token"], text="My secret orchid plan")
    await _enable(env, "conversation_search")
    env.script.add(
        "function:primary",
        [Tool("search_conversation_history", {"query": "tulip budget"}, "q1")],
        [Tool("search_conversation_history", {"query": "orchid"}, "q2")],
        ["Found it."],
    )
    tok = await env.token()
    await env.chat(tok["token"], text="What did we say about the budget?")
    got = _returns(env.script.seen[-1][1])
    assert "tulip" in got[0].lower()
    assert "o matches" in got[1]


async def test_spend_limits_take_a_share_of_a_cap_and_end_the_turn_as_a_limit(env: Env) -> None:
    await _enable(env, "spend_limits", {"budgets": [{"window": "conversation", "tokens": 10}]})
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        ["October."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="October?")
    assert ev[-1]["reason"] == "limit"
    err = next(e for e in ev if e["type"] == "error")
    assert err["code"] == "limit" and "$" not in err["message"]
    rows = await _records(env, "spend_limits")
    assert rows and rows[0].subject == ""


async def test_spend_limits_refuse_a_budget_in_money(env: Env) -> None:
    env.svc.settings.capabilities = ["*"]
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "capability", "name": "spend_limits", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200
    from types import SimpleNamespace

    from evokai.capabilities import harness

    scope = SimpleNamespace(app=SimpleNamespace(id="a"), who=SimpleNamespace(subject="u1"))
    ctx = harness.Context(engine=env.svc.engine, agent=None, rs=None, scope=scope)  # ty: ignore[invalid-argument-type]
    try:
        harness.spend_limits(ctx, {"budgets": [{"usd": "5"}]})
    except Exception as e:
        assert "never an amount" in str(e)
    else:
        raise AssertionError("a budget in money was accepted")


async def test_the_export_holds_what_capabilities_keep(env: Env) -> None:
    await _enable(env, "memory")
    env.script.add(
        "function:primary", [Tool("write_memory", {"content": "Likes tables."})], ["Ok."]
    )
    tok = await env.token()
    await env.chat(tok["token"], text="Remember tables")
    r = await env.client.post(
        "/agent/v1/privacy/export",
        json={"subject": "u1"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 200, r.text
    kept = r.json()["capabilityRecords"]
    assert any(x["capability"] == "memory" and "Likes tables." in x["content"] for x in kept)


async def test_expired_records_are_purged(env: Env) -> None:
    from datetime import timedelta

    from evokai.core.util import utcnow

    repo = env.svc.store.capabilities
    await repo.put(
        "a", "u", "tool_output_limits", "s", "k", "v", expires_at=utcnow() - timedelta(1)
    )
    await repo.put("a", "u", "tool_output_limits", "s", "k2", "v")
    assert await repo.purge() == 1
    assert [r.key for r in await repo.items("a", "u", "tool_output_limits", "s")] == ["k2"]
