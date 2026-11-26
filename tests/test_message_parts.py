"""Messages as ordered parts: what a turn showed is stored with its answers, in order, and a
reloaded conversation replays it (``evokai.sessions.parts``)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from evokai.db.repositories import Who
from evokai.sessions.parts import PartLog, ordered
from tests.conftest import ORIGIN, Env, Tool

CF = "RSSMRA85T10A562S"
CHART = Tool(
    "ui_chart",
    {
        "type": "donut",
        "title": "Quote",
        "points": [{"label": "A", "value": 3}, {"label": "B", "value": 1}],
    },
    "c",
)
SHOWN = {"text", "tool", "chart", "citation", "error", "confirm", "client_tool", "ask"}


def _h(tok: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _conversation(env: Env, tok: dict[str, Any]) -> list[dict[str, Any]]:
    r = await env.client.get("/agent/v1/session/conversation", headers=_h(tok))
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def _parts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a client draws from a turn's live events: the shown ones, deltas joined, a tool's
    start and end as one card."""
    log = PartLog()
    for e in events:
        log.add({k: v for k, v in e.items() if k not in ("turnId", "seq")})
    return log.take()


async def test_a_reloaded_turn_holds_what_was_shown_in_the_same_order(env: Env) -> None:
    env.script.add(
        "function:primary",
        ["Let me ", "check. ", Tool("orders_month", {"period": "2026-10"}, "a")],
        ["24 orders. ", CHART],
        ["Done."],
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Go")
    msgs = await _conversation(env, tok)
    turn = live[0]["turnId"]
    answers = [m for m in msgs if m["role"] == "assistant"]
    assert {m["turnId"] for m in msgs} == {turn}
    stored = [e for m in answers for e in m["events"]]
    assert stored == _parts(live)
    assert [e["type"] for e in stored] == ["text", "tool", "text", "chart", "text"]
    assert stored[1] == {"type": "tool", "name": "orders_month", "status": "done", "id": "a"}
    # Nothing a browser was not shown: never the tool's arguments or result.
    assert "2026-10" not in json.dumps(stored) and "24" not in json.dumps(stored[1])
    # Each answer keeps its own text as before, for readers of the older shape.
    assert [m["text"] for m in answers] == ["Let me check. ", "24 orders. ", "Done."]
    assert all(ordered(m["events"]) for m in answers)


async def test_what_comes_after_the_last_answer_joins_it(env: Env) -> None:
    env.script.add(
        "function:primary", *[["Looking. ", Tool("orders_month", {}, f"t{i}")] for i in range(10)]
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Loop")
    assert live[-1]["reason"] == "max_steps"
    msgs = await _conversation(env, tok)
    stored = [e for m in msgs if m["role"] == "assistant" for e in m["events"]]
    assert stored == _parts(live)
    # The tool-call cap's sentence came after the last stored answer: it went with it.
    last = [m for m in msgs if m["role"] == "assistant"][-1]["events"]
    assert [e["type"] for e in last[-2:]] == ["tool", "text"]
    assert "Looking" not in last[-1]["delta"]


async def test_a_confirmation_and_its_decision_replay_in_two_turns(env: Env) -> None:
    env.script.add("function:primary", [Tool("order_refund", {})], ["Refunded."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Refund")
    action = next(e for e in ev if e["type"] == "confirm")["actionId"]
    done = await env.chat(tok["token"], confirm={"actionId": action, "approve": True})
    msgs = await _conversation(env, tok)
    assert [(m["role"], [e["type"] for e in m["events"]]) for m in msgs] == [
        ("user", []),
        ("assistant", ["confirm"]),
        ("user", ["confirmed"]),
        ("assistant", ["tool", "text"]),
    ]
    assert msgs[2]["turnId"] == msgs[3]["turnId"] == done[0]["turnId"] != msgs[1]["turnId"]


async def test_a_client_tool_that_waited_keeps_how_it_ended_not_the_result(env: Env) -> None:
    light = {
        "name": "kiosk_light",
        "description": "Turn the kiosk light on or off.",
        "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}},
        "wait": True,
    }
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "client", "name": "session", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    tok = await env.token(clientTools=[light])
    env.script.add(
        "function:primary", [Tool("client_kiosk_light", {"on": True}, "c1")], ["It is on."]
    )
    turn = asyncio.create_task(env.chat(tok["token"], text="Light, please"))
    for _ in range(200):
        if env.svc.engine.client_calls.waiting():
            break
        await asyncio.sleep(0.01)
    r = await env.client.post(
        "/agent/v1/session/tool-results",
        json={"callId": "c1", "result": {"secret": "lamp-42"}},
        headers=_h(tok),
    )
    assert r.status_code == 200, r.text
    await asyncio.wait_for(turn, 5)
    msgs = await _conversation(env, tok)
    (call,) = [e for m in msgs for e in m["events"] if e["type"] == "client_tool"]
    assert call == {
        "type": "client_tool",
        "callId": "c1",
        "name": "kiosk_light",
        "args": {"on": True},
        "wait": True,
        "status": "done",
    }
    assert "lamp-42" not in json.dumps([m["events"] for m in msgs])


async def test_parts_are_masked_like_the_text_and_covered_by_export_and_erase(env: Env) -> None:
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"pii.history": "enforce"}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    env.script.add(
        "function:primary",
        [Tool("orders_month", {}, "a")],
        [f"Il codice è {CF}.", Tool("ui_chart", {**CHART.args, "title": CF}, "c")],
        ["Ok."],
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Vai")
    assert CF in json.dumps(live)  # the person was shown it
    msgs = await _conversation(env, tok)
    assert CF not in json.dumps(msgs)
    texts = [e["delta"] for m in msgs for e in m["events"] if e["type"] == "text"]
    assert "".join(texts) == "".join(m["text"] for m in msgs if m["role"] == "assistant")
    srv = {"Authorization": f"Bearer {env.server_key}"}
    r = await env.client.post("/agent/v1/privacy/export", json={"subject": "u1"}, headers=srv)
    (conv,) = r.json()["conversations"]
    kinds = [e["type"] for m in conv["messages"] for e in m["events"]]
    assert {"tool", "chart", "text"} <= set(kinds)
    who = Who(env.app_id, "u1")
    thread = await env.svc.store.sessions.thread(tok["conversationId"])
    assert await env.svc.store.messages.recent(who, thread.id, 10)
    r = await env.client.post("/agent/v1/privacy/erase", json={"subject": "u1"}, headers=srv)
    assert r.status_code == 200, r.text
    assert await env.svc.store.sessions.get(who, tok["conversationId"]) is None
    assert not await env.svc.store.messages.recent(who, thread.id, 10)


async def test_external_storage_returns_the_parts_and_takes_them_back(env: Env) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"storage_mode": "external"}, headers=_adm(env)
    )
    assert r.status_code == 200
    env.script.add(
        "function:primary",
        *[["Step. ", Tool("orders_month", {}, f"t{i}")] for i in range(3)],
        ["Done."],
        ["Again."],
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Go", history=[])
    added = live[-1]["messages"]
    stored = [e for m in added if m["role"] == "assistant" for e in m["events"]]
    assert stored == _parts(live)
    history = [{"role": "user", "text": "Go", "content": [{"text": "Go"}]}, *added]
    ev = await env.chat(tok["token"], text="More", history=history)
    assert ev[-1]["reason"] == "end_turn"


async def test_an_older_message_is_returned_as_it_was_stored(env: Env) -> None:
    tok = await env.token()
    await env.chat(tok["token"], text="Hi")
    who = Who(env.app_id, "u1")
    sess = await env.svc.store.sessions.get(who, tok["conversationId"])
    assert sess is not None
    thread = await env.svc.store.sessions.thread(sess.id)
    chart = {"type": "chart", "chart": {"type": "kpi", "title": "T", "items": []}}
    await env.svc.store.messages.add(
        who, sess.id, thread.id, role="assistant", content=[{"text": "Old."}], text="Old.",
        events=[chart],
    )  # fmt: skip
    msgs = await _conversation(env, tok)
    assert msgs[-1]["text"] == "Old." and msgs[-1]["events"] == [chart]
    assert not ordered(msgs[-1]["events"])
