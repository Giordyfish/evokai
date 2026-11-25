"""Turns through the HTTP API, with scripted models and a fake MCP server."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from tests.conftest import Env, Tool, parse_sse, system_text, types


async def test_plain_answer_streams_text_and_records_usage(env: Env) -> None:
    env.script.add("function:primary", ["Ciao", "!"])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ciao")
    assert types(ev) == ["conversation", "text", "text", "usage", "done"]
    assert ev[0]["id"] == tok["conversationId"]
    assert all(e["turnId"] == ev[0]["turnId"] for e in ev)
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))
    assert ev[-1]["reason"] == "end_turn"
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    assert [(m["role"], m["text"]) for m in conv["messages"]] == [
        ("user", "Ciao"),
        ("assistant", "Ciao!"),
    ]


async def test_read_tool_runs_as_the_user_and_its_result_goes_back(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        ["Ottobre ha 24 ordini."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Come va ottobre?")
    assert env.mcp.calls == [("orders_month", {"period": "2026-10"})]
    assert all(a == "Bearer user-token" for a in env.mcp.auth)
    assert [e["status"] for e in ev if e["type"] == "tool"] == ["start", "done"]
    _, second = env.script.seen[-1]
    returns = [
        p
        for m in second
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert "24" in str(returns[0].content)


async def test_change_waits_for_confirmation_then_runs(env: Env) -> None:
    env.script.add(
        "function:primary",
        ["Rimborso.", Tool("order_refund", {"period": "2026-10", "reason": "damaged"})],
        ["Fatto."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa tutto")
    confirm = next(e for e in ev if e["type"] == "confirm")
    assert confirm["items"] == ["Refund the orders of 2026-10 as Damaged"]
    assert ev[-1]["reason"] == "confirm"
    assert env.mcp.calls == []
    ev2 = await env.chat(tok["token"], confirm={"actionId": confirm["actionId"], "approve": True})
    assert env.mcp.calls == [("order_refund", {"period": "2026-10", "reason": "damaged"})]
    assert "Fatto." in "".join(e["delta"] for e in ev2 if e["type"] == "text")


async def test_refusal_answers_without_calling_the_model(env: Env) -> None:
    env.script.add("function:primary", [Tool("order_refund", {"period": "2026-10"})])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa")
    confirm = next(e for e in ev if e["type"] == "confirm")
    calls_before = len(env.script.seen)
    ev2 = await env.chat(tok["token"], confirm={"actionId": confirm["actionId"], "approve": False})
    assert (
        "".join(e["delta"] for e in ev2 if e["type"] == "text")
        == "OK, I have not changed anything."
    )
    assert len(env.script.seen) == calls_before
    assert env.mcp.calls == []


async def test_a_new_message_supersedes_an_open_confirmation(env: Env) -> None:
    env.script.add("function:primary", [Tool("order_refund", {})], ["Va bene."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa")
    action = next(e for e in ev if e["type"] == "confirm")["actionId"]
    await env.chat(tok["token"], text="No aspetta, mostrami solo i numeri")
    stale = await env.chat(tok["token"], confirm={"actionId": action, "approve": True})
    assert next(e for e in stale if e["type"] == "error")["code"] == "expired"
    assert env.mcp.calls == []


async def test_client_tools_emit_navigation_and_sanitised_charts(env: Env) -> None:
    env.script.add(
        "function:primary",
        [
            Tool("ui_navigate", {"path": "/backend/x"}, "a"),
            Tool(
                "ui_chart",
                {
                    "type": "donut",
                    "title": "Quote",
                    "points": [{"label": "A", "value": 3}, {"label": "B", "value": 1}],
                },
                "b",
            ),
        ],
        ["Ecco."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Mostra")
    assert next(e for e in ev if e["type"] == "navigate")["path"] == "/backend/x"
    chart = next(e for e in ev if e["type"] == "chart")["chart"]
    assert [p["label"] for p in chart["points"]] == ["A", "B"]


async def test_navigation_takes_a_hash_route_and_explains_a_refused_path(env: Env) -> None:
    env.script.add(
        "function:primary",
        [
            Tool("ui_navigate", {"path": "#/backend/y"}, "a"),
            Tool("ui_navigate", {"path": "https://example.com/x"}, "b"),
        ],
        ["Ecco."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Apri")
    assert [e["path"] for e in ev if e["type"] == "navigate"] == ["/backend/y"]
    _, messages = env.script.seen[-1]
    refused = [
        str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id == "b"
    ]
    assert refused and "starts with /" in refused[0], refused


async def test_ask_ends_the_turn_and_the_answer_is_labelled(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("ui_ask", {"question": "Quale mese?", "options": ["Ottobre", "Settembre"]})],
        ["Ok ottobre."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Mostrami un mese")
    assert ev[-1]["reason"] == "ask"
    assert len(env.script.seen) == 1
    await env.chat(tok["token"], text="Ottobre")
    _, messages = env.script.seen[-1]
    prompts = [
        p.content
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart)
    ]
    assert prompts[-1] == 'Answer to your question "Quale mese?": Ottobre'


async def test_capacity_error_moves_the_step_to_the_fallback(env: Env) -> None:
    env.script.add("function:primary", ModelHTTPError(503, "primary", "unable to process"))
    env.script.add("function:backup", ["Eccomi."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ciao")
    assert [name for name, _ in env.script.seen] == ["function:primary", "function:backup"]
    assert "".join(e["delta"] for e in ev if e["type"] == "text") == "Eccomi."
    report = (
        await env.client.get(
            "/agent/v1/usage?groupBy=model", headers={"Authorization": f"Bearer {env.server_key}"}
        )
    ).json()
    assert [r["model"] for r in report["rows"]] == ["backup"]


async def test_a_request_error_does_not_switch_model(env: Env) -> None:
    env.script.add("function:primary", ModelHTTPError(400, "primary", "bad"))
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ciao")
    assert [name for name, _ in env.script.seen] == ["function:primary"]
    assert next(e for e in ev if e["type"] == "error")["code"] == "model"


async def test_application_cap_stops_before_the_model(env: Env) -> None:
    await env.svc.store.applications.update(env.app_id, monthly_cap_usd=0.0)
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ciao")
    err = next(e for e in ev if e["type"] == "error")
    assert (err["code"], err["limit"]) == ("limit", "app_month")
    assert env.script.seen == []


async def test_subject_limits_from_the_token_are_enforced(env: Env) -> None:
    tok = await env.token(limits={"subject_daily_usd": 0})
    ev = await env.chat(tok["token"], text="Ciao")
    assert next(e for e in ev if e["type"] == "error")["limit"] == "subject_day"
    assert not [e for e in ev if e["type"] == "say"]


async def test_in_voice_a_reached_cap_is_said_aloud_not_silent(env: Env) -> None:
    tok = await env.token(limits={"subject_daily_usd": 0})
    ev = await env.chat(tok["token"], text="Ciao", mode="voice")
    error = next(e for e in ev if e["type"] == "error")
    said = [e["text"] for e in ev if e["type"] == "say"]
    assert error["limit"] == "subject_day" and said and said[0]


async def test_tool_loop_is_capped(env: Env) -> None:
    env.script.add("function:primary", *[[Tool("orders_month", {}, f"t{i}")] for i in range(10)])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Loop")
    assert ev[-1]["reason"] == "max_steps"
    assert len(env.mcp.calls) == 4


async def test_mcp_down_is_not_fatal(env: Env) -> None:
    env.mcp.down = True
    env.script.add("function:primary", ["Senza strumenti."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ciao")
    assert ev[-1]["reason"] == "end_turn"


async def test_skills_are_listed_and_loaded_on_demand(env: Env) -> None:
    env.script.add(
        "function:primary", [Tool("skill_load", {"name": "procedura"})], ["Ecco i passi."]
    )
    tok = await env.token()
    await env.chat(tok["token"], text="Come si fa?")
    first_system = system_text(env.script.seen[0][1])
    assert "procedura: Come si gestisce un reso." in first_system
    _, second = env.script.seen[1]
    returns = [
        p
        for m in second
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert "Primo passo" in str(returns[0].content)


async def test_voice_mode_speaks_sentences_with_identifiers_redacted(env: Env) -> None:
    env.script.add("function:primary", ["Customer RSSMRA80A01H501U ", "has 3 orders. Show them?"])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Dimmi", mode="voice")
    says = [e["text"] for e in ev if e["type"] == "say"]
    assert says == ["Customer the tax code has 3 orders.", "Show them?"]
    system = system_text(env.script.seen[0][1])
    assert "read aloud" in system


async def test_voice_say_events_carry_the_numbers_in_words_for_the_speech_engine(env: Env) -> None:
    await env.svc.store.applications.update(env.app_id, defaults={"locale": "it"})
    env.script.add("function:primary", ["Sono 1.000 punti. ", "Ciao."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Quanti punti?", mode="voice")
    says = [e for e in ev if e["type"] == "say"]
    # What is shown keeps the digits; only the speech engine gets the words.
    assert says[0]["text"] == "Sono 1.000 punti."
    assert says[0]["spoken"] == "Sono mille punti."
    assert says[1]["text"] == "Ciao." and "spoken" not in says[1]
    text = "".join(e["delta"] for e in ev if e["type"] == "text")
    assert text == "Sono 1.000 punti. Ciao."
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    assert conv["messages"][-1]["text"] == "Sono 1.000 punti. Ciao."


async def test_a_text_turn_never_carries_a_spoken_form(env: Env) -> None:
    await env.svc.store.applications.update(env.app_id, defaults={"locale": "it"})
    env.script.add("function:primary", ["Sono 1.000 punti."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Quanti punti?")
    assert not [e for e in ev if e["type"] == "say" or "spoken" in e]
    assert "".join(e["delta"] for e in ev if e["type"] == "text") == "Sono 1.000 punti."


async def test_english_say_events_have_no_spoken_form(env: Env) -> None:
    env.script.add("function:primary", ["You have 1.000 points."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Points?", mode="voice")
    says = [e for e in ev if e["type"] == "say"]
    assert says and all("spoken" not in e for e in says)


async def test_cancel_keeps_the_partial_answer_and_ends_the_turn(env: Env) -> None:
    env.script.gate = asyncio.Event()
    env.script.add("function:primary", ["Sto ", "WAIT", "rispondendo"])
    tok = await env.token()
    headers = {**_h(tok), "Origin": "http://app.test"}

    async def start() -> list[dict]:
        r = await env.client.post("/agent/v1/session/chat", json={"text": "Lungo"}, headers=headers)
        return parse_sse(r.text)

    task = asyncio.create_task(start())
    for _ in range(100):
        running = env.svc.turns.running(tok["conversationId"])
        if running:
            break
        await asyncio.sleep(0.01)
    assert running is not None
    # The model has said «Sto » and waits: cancel then (a fixed sleep was too short on slow CI).
    await asyncio.wait_for(env.script.waiting.wait(), 10)
    await asyncio.sleep(0.05)
    r = await env.client.post(f"/agent/v1/session/turns/{running.turn_id}/cancel", headers=headers)
    assert r.json()["cancelled"] is True
    ev = await task
    assert ev[-1] == {**ev[-1], "type": "done", "reason": "cancelled"}
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    last = conv["messages"][-1]
    assert (last["text"], last["interrupted"]) == ("Sto ", True)


async def test_events_can_be_replayed_after_a_sequence_number(env: Env) -> None:
    env.script.add("function:primary", ["Uno."], ["Due."])
    tok = await env.token()
    first = await env.chat(tok["token"], text="a")
    await env.chat(tok["token"], text="b")
    r = await env.client.get(f"/agent/v1/session/events?after={first[-1]['seq']}", headers=_h(tok))
    replay = r.json()["events"]
    assert replay[0]["seq"] == first[-1]["seq"] + 1
    assert "".join(e.get("delta", "") for e in replay if e["type"] == "text") == "Due."


async def test_a_channel_still_saving_its_events_is_not_dropped(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A stream closes as soon as it sees ``done``, while the turn is still saving its last events.
    # Dropping the channel then would let the next message reopen it from a stale last number and
    # number its events again from there.
    store, hub = env.svc.store, env.svc.hub
    tok = await env.token()
    session_id = (await env.chat(tok["token"], text="a"))[-1]["conversationId"]
    saving, saved = asyncio.Event(), asyncio.Event()
    append = store.events.append

    async def slow_append(sid: str, rows: list[dict[str, Any]]) -> None:
        saving.set()
        await saved.wait()
        await append(sid, rows)

    monkeypatch.setattr(store.events, "append", slow_append)
    channel = await hub.get(session_id)
    last = channel.publish({"type": "status"}, None)["seq"]
    flushing = asyncio.create_task(channel.flush())
    await saving.wait()
    hub.drop_if_idle(session_id)
    assert await hub.get(session_id) is channel
    saved.set()
    await flushing
    hub.drop_if_idle(session_id)
    reopened = await hub.get(session_id)
    assert reopened is not channel
    assert reopened.publish({"type": "status"}, None)["seq"] == last + 1


async def test_memories_persist_across_conversations(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("memory_remember", {"fact": "preferisce gli importi arrotondati"})],
        ["Ricordato."],
        ["Ok."],
    )
    tok = await env.token()
    await env.chat(tok["token"], text="Arrotonda sempre")
    tok2 = await env.token()
    await env.chat(tok2["token"], text="Ciao")
    system = system_text(env.script.seen[-1][1])
    assert "preferisce gli importi arrotondati" in system


def _h(tok: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}"}


async def test_a_chart_from_tools_shows_only_numbers_a_tool_returned(env: Env) -> None:
    head = {"Authorization": f"Bearer {env.admin_key}"}
    r = await env.client.get("/agent/v1/admin/agents", headers=head)
    helper = next(a for a in r.json()["items"] if a["name"] == "helper")
    helper.pop("version", None)
    helper["client_tools"] = {**helper["client_tools"], "chart_from_tools": True}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=head)
    assert r.status_code == 200, r.text

    def kpi(value: float, cid: str) -> Tool:
        return Tool(
            "ui_chart",
            {"type": "kpi", "title": "Ottobre", "items": [{"label": "Ordini", "value": value}]},
            cid,
        )

    env.script.add(
        "function:primary",
        [kpi(99, "a")],
        [Tool("orders_month", {"period": "2026-10"}, "b")],
        [kpi(24, "c")],
        ["Ecco."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Quanti ordini?")
    charts = [e["chart"] for e in ev if e["type"] == "chart"]
    assert [c["items"][0]["value"] for c in charts] == [24]
    _, messages = env.script.seen[-1]
    returns = {
        p.tool_call_id: str(p.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    }
    assert "no tool returned 99" in returns["a"], returns
