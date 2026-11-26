"""0.3 streaming: the model's reasoning and its tool calls' arguments shown while it works.

Both are opt-in per agent, go through the output checks before they are sent, are stored as parts
and replayed identically on a reload (``evokai.sessions.live``, ``evokai.sessions.parts``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, ThinkingPart

from evokai.agents.definitions import AgentDef
from evokai.llm.policy import agent_settings
from evokai.sessions import live as live_mod
from evokai.sessions.live import partial_object
from evokai.sessions.parts import PartLog
from tests.conftest import ORIGIN, Env, Think, Tool

SECRET = "AKIAABCDEFGHIJKLMNOP"
CF = "RSSMRA85T10A562S"


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _agent(env: Env, **changes: Any) -> None:
    r = await env.client.get("/agent/v1/admin/agents", headers=_adm(env))
    helper = next(a for a in r.json()["items"] if a["name"] == "helper")
    helper.pop("version", None)
    r = await env.client.put(
        "/agent/v1/admin/agents/helper", json={**helper, **changes}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text


async def _conversation(env: Env, tok: dict[str, Any]) -> list[dict[str, Any]]:
    r = await env.client.get(
        "/agent/v1/session/conversation",
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN},
    )
    assert r.status_code == 200, r.text
    return r.json()["messages"]


def _parts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    log = PartLog()
    for e in events:
        log.add({k: v for k, v in e.items() if k not in ("turnId", "seq")})
    return log.take()


def _stored(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for m in msgs if m["role"] == "assistant" for e in m["events"]]


def _thinking(messages: list[Any]) -> list[ThinkingPart]:
    return [
        p for m in messages if isinstance(m, ModelResponse) for p in m.parts
        if isinstance(p, ThinkingPart)
    ]  # fmt: skip


# ── the definition ────────────────────────────────────────────────────────────────────────────────


def test_the_new_options_keep_the_version_at_their_default() -> None:
    base = AgentDef(name="a", model="m", instructions="x")
    assert "show_reasoning" not in base.content() and "show_tool_args" not in base.content()
    same = AgentDef(name="a", model="m", instructions="x", show_reasoning=False)
    assert same.with_version().version == base.with_version().version
    on = AgentDef(name="a", model="m", instructions="x", show_reasoning=True, show_tool_args=True)
    assert on.with_version().version != base.with_version().version


def test_an_agent_that_shows_reasoning_asks_openai_for_a_summary_only_when_it_reasons() -> None:
    plain = AgentDef(name="a", model="m", instructions="x", show_reasoning=True)
    assert "openai_reasoning_summary" not in agent_settings(plain)
    thinks = plain.model_copy(update={"model_settings": {"thinking": "low"}})
    assert agent_settings(thinks)["openai_reasoning_summary"] == "auto"
    chosen = plain.model_copy(
        update={"model_settings": {"thinking": True, "openai_reasoning_summary": "detailed"}}
    )
    assert agent_settings(chosen)["openai_reasoning_summary"] == "detailed"
    hidden = thinks.model_copy(update={"show_reasoning": False})
    assert "openai_reasoning_summary" not in agent_settings(hidden)


def test_partial_json_is_read_as_far_as_it_goes() -> None:
    assert partial_object('{"period": "2026-1') == {"period": "2026-1"}
    assert partial_object('{"a": 1, "b": [1, 2') == {"a": 1, "b": [1, 2]}
    assert partial_object("") is None and partial_object("[1, 2]") is None
    assert partial_object("not json") is None


# ── reasoning ─────────────────────────────────────────────────────────────────────────────────────


async def test_reasoning_is_not_shown_unless_the_agent_shows_it(env: Env) -> None:
    env.script.add("function:primary", [Think("Let me think about ", "October."), "24 orders."])
    tok = await env.token()
    live = await env.chat(tok["token"], text="Orders?")
    assert not any(e["type"] == "reasoning" for e in live)
    assert "October" not in json.dumps(await _conversation(env, tok))


async def test_reasoning_streams_through_the_output_checks_and_replays_on_reload(env: Env) -> None:
    await _agent(env, show_reasoning=True)
    env.script.add(
        "function:primary",
        [Think("The key is AK", "IAABCDEFGHIJKLMNOP, ", "I look up October."),
         Tool("orders_month", {"period": "2026-10"}, "a")],
        [Think("24 orders came back."), "October had 24 orders."],
    )  # fmt: skip
    tok = await env.token()
    live = await env.chat(tok["token"], text="Orders?")
    reasoning = [e for e in live if e["type"] == "reasoning"]
    assert reasoning, "the reasoning was streamed"
    shown = "".join(e["delta"] for e in reasoning)
    assert SECRET not in json.dumps(live) and "[SECRET]" in shown
    assert "I look up October." in shown and "24 orders came back." in shown
    stored = _stored(await _conversation(env, tok))
    assert stored == _parts(live)
    assert [e["type"] for e in stored] == ["reasoning", "tool", "reasoning", "text"]
    # The second request of the turn carries the first step's reasoning (the providers require
    # it with tool calls); the next turn never sees it again.
    second = env.script.seen[1][1]
    assert next(p.content for p in _thinking(second)).startswith("The key is")
    env.script.add("function:primary", ["Ok."])
    await env.chat(tok["token"], text="Thanks")
    assert _thinking(env.script.seen[-1][1]) == []


async def test_reasoning_that_repeats_the_instructions_stops_but_the_answer_goes_on(
    env: Env,
) -> None:
    long = (
        "You are the back office assistant of the test application. Answer briefly and precisely, "
        "never invent data, and always use the tools before giving a number."
    )
    await _agent(
        env,
        show_reasoning=True,
        instructions=long,
        security={"modes": {"output.system_prompt": "enforce"}},
    )
    env.script.add("function:primary", [Think("My rules: ", long[:60], long[60:]), "Hello."])
    tok = await env.token()
    live = await env.chat(tok["token"], text="Hi")
    shown = "".join(e["delta"] for e in live if e["type"] == "reasoning")
    assert "never invent data" not in shown
    assert "".join(e["delta"] for e in live if e["type"] == "text") == "Hello."
    assert live[-1]["reason"] == "end_turn"


async def test_stored_reasoning_is_masked_like_the_text(env: Env) -> None:
    await _agent(env, show_reasoning=True)
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"pii.history": "enforce"}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    env.script.add("function:primary", [Think(f"The person is {CF}. "), "Ok."])
    tok = await env.token()
    live = await env.chat(tok["token"], text="Hi")
    assert CF in json.dumps(live)  # the person was shown it
    stored = _stored(await _conversation(env, tok))
    assert stored[0]["type"] == "reasoning" and CF not in json.dumps(stored)


async def test_a_confirmation_carries_out_its_calls_with_their_reasoning(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Think("A refund it is.", signature="sig-1"), Tool("order_refund", {}, "r1")],
        ["Refunded."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Refund")
    action = next(e for e in ev if e["type"] == "confirm")["actionId"]
    await env.chat(tok["token"], confirm={"actionId": action, "approve": True})
    resumed = env.script.seen[-1][1]
    (thought,) = _thinking(resumed)
    assert thought.content == "A refund it is." and thought.signature == "sig-1"
    # Never shown: the agent does not show its reasoning.
    msgs = await _conversation(env, tok)
    assert "A refund it is" not in json.dumps([m["events"] for m in msgs])
    env.script.add("function:primary", ["Ok."])
    await env.chat(tok["token"], text="Thanks")
    assert _thinking(env.script.seen[-1][1]) == []


async def test_reasoning_with_identifiers_is_not_kept_for_a_confirmation_under_pii_history(
    env: Env,
) -> None:
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"pii.history": "enforce"}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    env.script.add(
        "function:primary",
        [Think(f"Refund {CF}.", signature="sig-2"), Tool("order_refund", {}, "r2")],
        ["Refunded."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Refund")
    action = next(e for e in ev if e["type"] == "confirm")["actionId"]
    await env.chat(tok["token"], confirm={"actionId": action, "approve": True})
    assert _thinking(env.script.seen[-1][1]) == []  # dropped rather than changed


# ── tool arguments ────────────────────────────────────────────────────────────────────────────────


async def test_tool_arguments_are_not_sent_unless_the_agent_sends_them(env: Env) -> None:
    env.script.add(
        "function:primary", [Tool("orders_month", {"period": "2026-10"}, "a")], ["Done."]
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Go")
    assert "2026-10" not in json.dumps(live)
    assert not any(e.get("status") == "args" for e in live)


async def test_tool_arguments_stream_into_the_card_and_replay_on_reload(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(live_mod, "ARGS_EVERY", 0)
    await _agent(env, show_tool_args=True)
    chunks = ['{"per', 'iod": "20', "26-10", '"}']
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"}, "a", chunks=chunks)],
        ["Done."],
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Go")
    card = [e for e in live if e["type"] == "tool"]
    args = [e["args"] for e in card if e["status"] == "args"]
    # The object as far as it was written, each one parsed from the filtered text.
    assert args == [{}, {"period": "20"}, {"period": "2026-10"}]
    assert [e["status"] for e in card if e["status"] != "args"] == ["start", "done"]
    assert {e["id"] for e in card} == {"a"}
    stored = _stored(await _conversation(env, tok))
    assert stored == _parts(live)
    assert stored[0] == {
        "type": "tool",
        "name": "orders_month",
        "id": "a",
        "status": "done",
        "args": {"period": "2026-10"},
    }


async def test_arguments_of_a_change_wait_and_never_look_like_it_ran(env: Env) -> None:
    await _agent(env, show_tool_args=True)
    env.script.add(
        "function:primary",
        [Tool("order_refund", {"reason": "damaged"}, "r1"), Tool("orders_month", {}, "m1")],
    )
    tok = await env.token()
    live = await env.chat(tok["token"], text="Refund")
    refund = [e for e in live if e["type"] == "tool" and e.get("id") == "r1"]
    assert [e["status"] for e in refund] == ["args", "waiting"]
    assert refund[0]["args"] == {"reason": "damaged"}
    kinds = [e["type"] for e in live]
    assert kinds.index("confirm") > max(i for i, e in enumerate(live) if e.get("id") == "r1")
    stored = _stored(await _conversation(env, tok))
    assert stored == _parts(live)
    (card,) = [e for e in stored if e.get("id") == "r1"]
    assert card["status"] == "waiting"


async def test_tool_arguments_go_through_the_output_checks(env: Env) -> None:
    await _agent(env, show_tool_args=True)
    env.script.add("function:primary", [Tool("orders_month", {"period": SECRET}, "a")], ["Done."])
    tok = await env.token()
    live = await env.chat(tok["token"], text="Go")
    shown = [e for e in live if e.get("status") == "args"]
    assert SECRET not in json.dumps(live) and shown[-1]["args"] == {"period": "[SECRET]"}


async def test_a_refused_call_with_shown_arguments_is_skipped(env: Env) -> None:
    await _agent(env, show_tool_args=True)
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"tools.args": "enforce"}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    env.script.add("function:primary", [Tool("orders_month", {"period": 7}, "s")], ["Done."])
    tok = await env.token()
    live = await env.chat(tok["token"], text="Go")
    assert [e["status"] for e in live if e.get("id") == "s"] == ["args", "skipped"]
    stored = _stored(await _conversation(env, tok))
    assert stored == _parts(live)


# ── structured output ─────────────────────────────────────────────────────────────────────────────

SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string"}, "note": {"type": "string"}},
    "required": ["category"],
}


async def test_a_structured_answer_fills_in_over_the_openai_stream(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_openai_compat import BOT, URL, _chunks, _put, _srv

    monkeypatch.setattr(live_mod, "ARGS_EVERY", 0)
    await _put(env, {**BOT, "output_schema": SCHEMA})
    pieces = ['{"category": "ord', 'er", "note": "key ', SECRET, '"}']
    final = {"category": "order", "note": f"key {SECRET}"}
    env.script.add("function:primary", [Tool("final_result", final, "o1", chunks=pieces)])
    r = await env.client.post(
        URL,
        json={"model": "bot", "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
        headers=_srv(env),
    )
    chunks = [c for c in _chunks(r.text) if c != "[DONE]"]
    partials = [c["evokai"]["output"] for c in chunks if "output" in c.get("evokai", {})]
    assert partials[0] == {"category": "ord"} and partials[1]["category"] == "order"
    assert partials[-1] == {"category": "order", "note": "key [SECRET]"}
    assert SECRET not in json.dumps(partials)
    content = "".join(
        c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices")
    )
    assert json.loads(content) == {"category": "order", "note": "key [SECRET]"}


async def test_a_structured_answer_fills_in_as_a2a_artifact_updates(env: Env) -> None:
    from tests.test_a2a import _frames, _message, _publish, _srv, _url

    await _publish(env, output_schema=SCHEMA)
    pieces = ['{"category": "ord', 'er"}']
    env.script.add(
        "function:primary", [Tool("final_result", {"category": "order"}, "o1", chunks=pieces)]
    )
    r = await env.client.post(
        _url(env),
        json={"jsonrpc": "2.0", "id": "s1", "method": "message/stream",
              "params": {"message": _message()}},
        headers=_srv(env),
    )  # fmt: skip
    frames = [f["result"] for f in _frames(r.text)]
    updates = [f for f in frames if f["kind"] == "artifact-update"]
    assert len(updates) >= 2 and updates[-1]["lastChunk"] is True
    ids = {u["artifact"]["artifactId"] for u in updates}
    assert len(ids) == 1 and all(u["append"] is False for u in updates)
    assert updates[0]["artifact"]["parts"] == [{"kind": "data", "data": {"category": "ord"}}]
    assert updates[-1]["artifact"]["parts"] == [{"kind": "data", "data": {"category": "order"}}]
