"""Rewrite the last question: the last turn is replaced, never after a write."""

from __future__ import annotations

import asyncio
from typing import Any

import sqlalchemy as sa

from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.sessions.rewrite import rewritable_index, wrote
from tests.conftest import ORIGIN, Env, Tool, parse_sse, types


def _h(tok: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


async def _rewrite(env: Env, tok: dict[str, Any], text: str, **rewrite: Any) -> Any:
    return await env.client.post(
        "/agent/v1/session/chat",
        json={"text": text, "rewrite": rewrite},
        headers=_h(tok),
    )


async def _conversation(env: Env, tok: dict[str, Any]) -> dict[str, Any]:
    r = await env.client.get("/agent/v1/session/conversation", headers=_h(tok))
    return r.json()


def test_the_rules_of_a_rewrite_on_plain_messages() -> None:
    q = {"role": "user", "text": "Ciao", "content": [{"text": "Ciao"}]}
    a = {"role": "assistant", "text": "Ciao!", "content": [{"text": "Ciao!"}]}
    write = {
        "role": "user",
        "content": [{"toolResult": {"id": "t", "name": "x", "text": "ok", "wrote": True}}],
    }
    legacy = {"role": "user", "content": [], "events": [{"type": "confirmed", "approve": True}]}
    read = {
        "role": "user",
        "content": [],
        "events": [{"type": "confirmed", "approve": True, "writes": False}],
    }
    assert rewritable_index([q, a, q, a]) == 2
    assert rewritable_index([q, a, write, a]) is None
    assert wrote([legacy]) and not wrote([read])
    assert rewritable_index([a]) is None
    # An application's history may carry the question as a text block only.
    blocks_only = {"role": "user", "content": [{"text": "Ciao"}]}
    assert rewritable_index([q, a, blocks_only, a]) == 2


async def test_the_last_question_is_replaced_and_replay_stays_consistent(env: Env) -> None:
    env.script.add("function:primary", ["Primo."], ["Secondo."], ["Terzo, riscritto."])
    tok = await env.token()
    await env.chat(tok["token"], text="Uno")
    second = await env.chat(tok["token"], text="Due")
    assert second[-1]["rewritable"] is True
    old_turn = second[0]["turnId"]
    conv = await _conversation(env, tok)
    users = [x for x in conv["messages"] if x["role"] == "user"]
    assert [x["rewritable"] for x in users] == [False, True]
    usage_before = await _usage_rows(env)

    r = await _rewrite(env, tok, "Due, anzi tre", messageId=users[-1]["id"])
    assert r.status_code == 200, r.text
    ev = parse_sse(r.text)
    assert types(ev)[:2] == ["conversation", "rewritten"]
    rewritten = ev[1]
    assert rewritten["replaced"] == [old_turn] and rewritten["messageId"] == users[-1]["id"]
    assert rewritten["turnId"] == ev[0]["turnId"] != old_turn
    assert ev[-1]["rewritable"] is True
    # The model saw the first exchange and the new question, never the replaced one.
    _, messages = env.script.seen[-1]
    assert "Uno" in str(messages) and "Due, anzi tre" in str(messages)
    assert "Secondo." not in str(messages)
    conv = await _conversation(env, tok)
    assert [(x["role"], x["text"]) for x in conv["messages"]] == [
        ("user", "Uno"),
        ("assistant", "Primo."),
        ("user", "Due, anzi tre"),
        ("assistant", "Terzo, riscritto."),
    ]
    # A replay from the start has no event of the replaced turn, and the rewrite in place.
    replay = (await env.client.get("/agent/v1/session/events?after=0", headers=_h(tok))).json()
    events = replay["events"]
    assert old_turn not in {e["turnId"] for e in events}
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert any(e["type"] == "rewritten" for e in events)
    # What was spent stays recorded.
    assert await _usage_rows(env) > usage_before


async def _usage_rows(env: Env) -> int:
    async with env.svc.db.session() as s:
        return (await s.execute(sa.select(sa.func.count()).select_from(m.UsageRecord))).scalar_one()


async def test_a_turn_past_its_done_is_over_for_readers_and_a_rewrite_waits_for_it(
    env: Env,
) -> None:
    # A slow database (Postgres under load): the turn has sent done, its stream is closed, and it
    # is still storing its row. The conversation must not call it running, and a rewrite must
    # wait for it instead of refusing it or deleting its events while it still writes them.
    finish = env.svc.store.turns.finish
    slow = asyncio.Event()

    async def slow_finish(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.3)
        slow.set()
        return await finish(*args, **kwargs)

    env.svc.store.turns.finish = slow_finish
    env.script.add("function:primary", ["Primo."], ["Nuovo."])
    tok = await env.token()
    first = await env.chat(tok["token"], text="Uno")
    conv = await _conversation(env, tok)
    assert not slow.is_set()  # read while the turn was still finishing
    assert conv["runningTurnId"] is None
    assert [x["rewritable"] for x in conv["messages"] if x["role"] == "user"] == [True]
    r = await _rewrite(env, tok, "Uno bis", messageId=conv["messages"][0]["id"])
    assert r.status_code == 200, r.text
    replay = (await env.client.get("/agent/v1/session/events?after=0", headers=_h(tok))).json()
    assert first[0]["turnId"] not in {e["turnId"] for e in replay["events"]}


async def test_a_rewrite_is_refused_after_a_write(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("order_refund", {"period": "2026-10"})],
        ["Fatto."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa")
    confirm = next(e for e in ev if e["type"] == "confirm")
    assert ev[-1]["rewritable"] is True  # nothing written yet
    ev2 = await env.chat(tok["token"], confirm={"actionId": confirm["actionId"], "approve": True})
    assert env.mcp.calls and ev2[-1]["rewritable"] is False
    r = await _rewrite(env, tok, "Non rimborsare")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "rewrite_after_write"
    assert "already changed data" in body["message"]
    conv = await _conversation(env, tok)
    assert not any(x.get("rewritable") for x in conv["messages"])


async def test_a_rejected_change_or_an_open_confirmation_can_be_rewritten(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("order_refund", {"period": "2026-10"})],
        [Tool("order_refund", {"period": "2026-11"})],
        ["Va bene."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa ottobre")
    first = next(e for e in ev if e["type"] == "confirm")["actionId"]
    await env.chat(tok["token"], confirm={"actionId": first, "approve": False})
    r = await _rewrite(env, tok, "Rimborsa novembre")
    assert r.status_code == 200, r.text
    open_action = next(e for e in parse_sse(r.text) if e["type"] == "confirm")["actionId"]
    r = await _rewrite(env, tok, "Lascia stare")
    assert r.status_code == 200, r.text
    stale = await env.chat(tok["token"], confirm={"actionId": open_action, "approve": True})
    assert next(e for e in stale if e["type"] == "error")["code"] == "expired"
    assert env.mcp.calls == []
    async with env.svc.db.session() as s:
        statuses = (await s.execute(sa.select(m.PendingAction.status))).scalars().all()
    assert "rewritten" in statuses


async def test_only_the_last_question_and_only_when_there_is_one(env: Env) -> None:
    tok = await env.token()
    r = await _rewrite(env, tok, "Niente")
    assert (r.status_code, r.json()["error"]) == (409, "rewrite_nothing")
    env.script.add("function:primary", ["A."], ["B."])
    await env.chat(tok["token"], text="Uno")
    await env.chat(tok["token"], text="Due")
    first = (await _conversation(env, tok))["messages"][0]
    r = await _rewrite(env, tok, "Uno bis", messageId=first["id"])
    assert (r.status_code, r.json()["error"]) == (409, "rewrite_not_last")


async def test_summaries_and_memories_keep_nothing_of_the_replaced_turn(env: Env) -> None:
    env.script.add("function:primary", ["Primo."], ["Secondo."], ["Nuovo."])
    tok = await env.token()
    await env.chat(tok["token"], text="Uno")
    await env.chat(tok["token"], text="Due")
    conv = await _conversation(env, tok)
    question = conv["messages"][2]
    who = Who(env.app_id, "u1")
    session_id = tok["conversationId"]
    async with env.svc.db.session() as s:
        msg = (
            await s.execute(sa.select(m.Message).where(m.Message.id == question["id"]))
        ).scalar_one()
        thread_id, position = msg.thread_id, msg.position
    old = await env.svc.store.memories.add(who, "Prefers tables", kind="preference", source="agent")
    new = await env.svc.store.memories.add(
        who, "Prefers charts", kind="preference", source="agent", session_id=session_id
    )
    await env.svc.store.memories.supersede(who, old, new)
    episode = await env.svc.store.memories.add(
        who, "Worked on two", kind="episodic", source="summary", session_id=session_id
    )
    kept = await env.svc.store.memories.add(who, "Works in Rome", kind="semantic", source="person")
    await env.svc.store.summaries.save(
        who,
        session_id,
        thread_id,
        up_to_position=position + 1,
        up_to_message_id=question["id"],
        summary="Uno, Due e Secondo.",
        messages=4,
        model="function:primary",
    )
    r = await _rewrite(env, tok, "Due bis")
    assert r.status_code == 200, r.text
    async with env.svc.db.session() as s:
        summaries = (await s.execute(sa.select(m.ConversationSummary))).scalars().all()
        mems = {x.id: x for x in (await s.execute(sa.select(m.Memory))).scalars().all()}
    assert summaries == []
    assert new not in mems and episode not in mems
    assert mems[old].superseded_by is None and kept in mems


# external storage


async def _external(env: Env) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"storage_mode": "external"},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200


HISTORY = [
    {"role": "user", "text": "Uno", "content": [{"text": "Uno"}]},
    {"role": "assistant", "text": "Primo.", "content": [{"text": "Primo."}]},
    {"role": "user", "text": "Due", "content": [{"text": "Due"}]},
    {
        "role": "assistant",
        "text": "",
        "content": [{"toolUse": {"id": "t1", "name": "orders_month", "input": {}}}],
    },
    {
        "role": "user",
        "content": [{"toolResult": {"id": "t1", "name": "orders_month", "text": "24"}}],
    },
    {"role": "assistant", "text": "Secondo.", "content": [{"text": "Secondo."}]},
]


async def test_external_storage_cuts_the_sent_history_and_says_where(env: Env) -> None:
    await _external(env)
    env.script.add("function:primary", ["Nuovo."])
    tok = await env.token()
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "Due bis", "rewrite": {}, "history": HISTORY},
        headers={**_h(tok), "Accept": "application/json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replacedFrom"] == 2 and body["rewritable"] is True
    assert [(x["role"], x["text"]) for x in body["messages"]] == [
        ("user", "Due bis"),
        ("assistant", "Nuovo."),
    ]
    rewritten = next(e for e in body["events"] if e["type"] == "rewritten")
    assert rewritten["replacedFrom"] == 2 and rewritten["replaced"] == []
    _, messages = env.script.seen[-1]
    assert "Uno" in str(messages) and "Secondo." not in str(messages)


async def test_external_storage_refuses_a_rewrite_after_a_write(env: Env) -> None:
    await _external(env)
    env.script.add("function:primary", [Tool("order_refund", {"period": "2026-10"})], ["Fatto."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa", history=HISTORY[:2])
    history = HISTORY[:2] + ev[-1]["messages"]
    confirm = next(e for e in ev if e["type"] == "confirm")
    ev2 = await env.chat(
        tok["token"],
        confirm={"actionId": confirm["actionId"], "approve": True},
        history=history,
    )
    history += ev2[-1]["messages"]
    assert any(
        b.get("toolResult", {}).get("wrote") for x in ev2[-1]["messages"] for b in x["content"]
    )
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "Non rimborsare", "rewrite": {}, "history": history},
        headers=_h(tok),
    )
    assert (r.status_code, r.json()["error"]) == (409, "rewrite_after_write")
