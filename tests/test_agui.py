"""AG-UI: a session's turns as AG-UI runs (POST /v1/session/agui), checked against the spec."""

from __future__ import annotations

from typing import Any

from tests import agui_spec
from tests.conftest import ORIGIN, Env, Tool, parse_sse
from tests.test_guard_turns import INJECTION

URL = "/agent/v1/session/agui"


def _input(text: str | None = "Com'è andato ottobre?", **extra: Any) -> dict[str, Any]:
    messages = [{"id": "m1", "role": "user", "content": text}] if text else []
    return {
        "threadId": "thread-1",
        "runId": "run-1",
        "messages": messages,
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": {},
        **extra,
    }


async def _run(env: Env, token: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    r = await env.client.post(
        URL, json=body, headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN}
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    agui_spec.check(events)
    return events


def _of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


async def test_a_turn_streams_valid_agui_events_with_tools_text_and_usage(env: Env) -> None:
    env.script.add(
        "function:primary",
        ["Guardo i dati. ", Tool("orders_month", {"period": "2026-10"})],
        ["Ottobre ha ", "24 ordini."],
    )
    tok = await env.token()
    events = await _run(env, tok["token"], _input())
    assert events[0] == {"type": "RUN_STARTED", "threadId": "thread-1", "runId": "run-1"}
    snapshot = events[1]["snapshot"]["evokai"]
    assert snapshot["conversationId"] == tok["conversationId"] and snapshot["agent"] == "helper"
    (start,) = _of(events, "TOOL_CALL_START")
    assert start["toolCallName"] == "orders_month"
    assert start["parentMessageId"] == _of(events, "TEXT_MESSAGE_START")[0]["messageId"]
    (result,) = _of(events, "TOOL_CALL_RESULT")
    assert result["toolCallId"] == start["toolCallId"] and '"done"' in result["content"]
    assert "24" not in result["content"]  # the tool's output never reaches the browser
    text = "".join(e["delta"] for e in _of(events, "TEXT_MESSAGE_CONTENT"))
    assert text == "Guardo i dati. Ottobre ha 24 ordini."
    assert len(_of(events, "TEXT_MESSAGE_START")) == 2  # one message before the tool, one after
    usage = next(e for e in events if e["type"] == "CUSTOM" and e["name"] == "evokai.usage")
    assert "companyPct" in usage["value"]
    assert {d["delta"][0]["path"] for d in _of(events, "STATE_DELTA")} == {"/evokai/usage"}
    assert "usd" not in str(events).lower() and "cost" not in str(events).lower()
    finished = events[-1]
    assert finished["outcome"] == {"type": "success"}
    assert finished["result"]["reason"] == "end_turn"
    assert env.mcp.auth[-1] == "Bearer user-token"  # as the session's user


async def test_a_confirmation_is_an_interrupt_and_resume_runs_the_tool(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("order_refund", {"period": "2026-10", "reason": "damaged"})],
        ["Fatto."],
    )
    tok = await env.token()
    events = await _run(env, tok["token"], _input("Rimborsa gli ordini di ottobre"))
    finished = events[-1]
    assert finished["outcome"]["type"] == "interrupt"
    (interrupt,) = finished["outcome"]["interrupts"]
    assert interrupt["reason"] == "confirmation"
    assert interrupt["message"] == "Refund the orders of 2026-10 as Damaged"
    assert interrupt["responseSchema"]["required"] == ["approved"]
    custom = next(e for e in events if e["type"] == "CUSTOM" and e["name"] == "evokai.confirm")
    assert custom["value"]["actionId"] == interrupt["id"]
    assert env.mcp.calls == []

    resume = [{"interruptId": interrupt["id"], "status": "resolved", "payload": {"approved": True}}]
    events = await _run(env, tok["token"], _input(resume=resume, runId="run-2"))
    assert env.mcp.calls == [("order_refund", {"period": "2026-10", "reason": "damaged"})]
    assert events[-1]["outcome"] == {"type": "success"}
    assert "Fatto." in "".join(e["delta"] for e in _of(events, "TEXT_MESSAGE_CONTENT"))


async def test_a_denied_confirmation_does_not_run_the_tool(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("order_refund", {"period": "2026-10", "reason": "damaged"})],
        ["Annullato."],
    )
    tok = await env.token()
    events = await _run(env, tok["token"], _input("Rimborsa"))
    action = events[-1]["outcome"]["interrupts"][0]["id"]
    resume = [{"interruptId": action, "status": "resolved", "payload": {"approved": False}}]
    await _run(env, tok["token"], _input(resume=resume))
    assert env.mcp.calls == []


async def test_a_question_is_an_input_required_interrupt_answered_by_resume(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("ui_ask", {"question": "Quale mese?", "options": ["Ottobre", "Settembre"]})],
        ["Ok, settembre."],
    )
    tok = await env.token()
    events = await _run(env, tok["token"], _input("Fammi vedere un mese"))
    (interrupt,) = events[-1]["outcome"]["interrupts"]
    assert interrupt["reason"] == "input_required" and interrupt["message"] == "Quale mese?"
    assert interrupt["responseSchema"]["properties"]["answer"]["enum"] == ["Ottobre", "Settembre"]
    resume = [
        {"interruptId": interrupt["id"], "status": "resolved", "payload": {"answer": "Settembre"}}
    ]
    events = await _run(env, tok["token"], _input(resume=resume))
    assert "Settembre" in str(env.script.seen[-1][1])
    assert events[-1]["outcome"] == {"type": "success"}


async def test_context_becomes_the_page_and_forwarded_props_can_set_it(env: Env) -> None:
    tok = await env.token()
    ctx = [{"description": "Open record", "value": "Invoice 77"}]
    await _run(env, tok["token"], _input(context=ctx))
    assert "Invoice 77" in str(env.script.seen[-1][1])
    page = {
        "evokai": {"page": {"path": "/orders", "title": "Ordini", "text": "Pagina degli ordini"}}
    }
    await _run(env, tok["token"], _input(forwardedProps=page))
    assert "Pagina degli ordini" in str(env.script.seen[-1][1])


async def test_the_guard_refusal_is_a_run_error(env: Env) -> None:
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"modes": {"input.injection": "enforce"}},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text
    tok = await env.token()
    events = await _run(env, tok["token"], _input(INJECTION))
    assert events[-1]["type"] == "RUN_ERROR" and events[-1]["code"] == "guard"
    assert env.script.seen == []


async def test_auth_origin_grants_and_empty_input(env: Env) -> None:
    tok = await env.token()
    assert (await env.client.post(URL, json=_input())).status_code == 401
    bad = await env.client.post(
        URL,
        json=_input(),
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": "http://evil.test"},
    )
    assert bad.status_code == 403
    headers = {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    empty = await env.client.post(URL, json=_input(None), headers=headers)
    assert empty.status_code == 400 and empty.json()["error"] == "empty"
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "channel", "name": "browser", "value": False}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200
    refused = await env.client.post(URL, json=_input(), headers=headers)
    assert refused.status_code == 403 and refused.json()["error"] == "channel_not_granted"


async def test_external_storage_takes_the_history_from_the_agui_messages(env: Env) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "storage", "name": "external", "value": True}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200
    tok = await env.token(storageMode="external")
    body = _input()
    body["messages"] = [
        {"id": "a", "role": "user", "content": "Ciao, sono Anna"},
        {"id": "b", "role": "assistant", "content": "Ciao Anna"},
        {"id": "c", "role": "user", "content": [{"type": "text", "text": "Come mi chiamo?"}]},
    ]
    events = await _run(env, tok["token"], body)
    seen = str(env.script.seen[-1][1])
    assert "sono Anna" in seen and "Come mi chiamo?" in seen
    assert events[-1]["result"]["messages"]  # the new messages, for the application's copy


def test_citations_and_visuals_are_custom_events() -> None:
    from evokai.api.agui_routes import Translator

    tr = Translator("t", "r")
    tr.start("c", "helper", "turn")
    cite = {
        "type": "citation",
        "index": 1,
        "kb": "docs",
        "title": "Guide",
        "seq": 4,
        "turnId": "turn",
    }
    (e,) = tr.event(cite)
    assert e["type"] == "CUSTOM" and e["name"] == "evokai.citation"
    assert e["value"]["title"] == "Guide" and "seq" not in e["value"]
    (e,) = tr.event({"type": "chart", "chart": {"kind": "kpi"}})
    assert e["name"] == "evokai.chart"
