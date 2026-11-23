"""Regenerate the last answer: the last question runs again and its new answer replaces the old one,
never after a turn that changed data or acted through a client tool."""

from __future__ import annotations

from typing import Any

from evokai.sessions.rewrite import regenerable_index
from tests.conftest import ORIGIN, Env, Tool, parse_sse, types

URL = "/agent/v1/session/regenerate"


def _h(tok: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e.get("delta", "") for e in events if e["type"] == "text")


def test_the_rules_of_a_regeneration() -> None:
    q = {"role": "user", "text": "Ciao", "content": [{"text": "Ciao"}]}
    a = {"role": "assistant", "text": "Ciao!", "content": [{"text": "Ciao!"}]}
    client = {
        "role": "user",
        "content": [{"toolResult": {"id": "t", "name": "client_kiosk_light", "text": "ok"}}],
    }
    assert regenerable_index([q, a]) == 0
    assert regenerable_index([q, a, client, a]) is None
    assert regenerable_index([a]) is None


async def test_the_last_answer_is_replaced(env: Env) -> None:
    env.script.add("function:primary", ["First."], ["Second, better."])
    tok = await env.token()
    first = await env.chat(tok["token"], text="Hello")
    assert first[-1]["regenerable"] is True
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    assert [m.get("regenerable") for m in conv["messages"]] == [None, True]
    r = await env.client.post(URL, json={}, headers=_h(tok))
    assert r.status_code == 200, r.text
    ev = parse_sse(r.text)
    assert types(ev)[:2] == ["conversation", "rewritten"]
    assert ev[1]["replaced"] == [first[0]["turnId"]]
    assert _text(ev) == "Second, better."
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    assert [(m["role"], m["text"]) for m in conv["messages"]] == [
        ("user", "Hello"),
        ("assistant", "Second, better."),
    ]
    # The model saw the same question again.
    assert "Hello" in str(env.script.seen[-1][1][-1])


async def test_not_after_a_change_nor_a_client_tool(env: Env) -> None:
    env.script.add(
        "function:primary",
        ["Refund.", Tool("order_refund", {"period": "2026-10", "reason": "damaged"})],
        ["Done."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Refund")
    action = next(e for e in ev if e["type"] == "confirm")["actionId"]
    done = await env.chat(tok["token"], confirm={"actionId": action, "approve": True})
    assert done[-1]["regenerable"] is False
    r = await env.client.post(URL, json={}, headers=_h(tok))
    assert (r.status_code, r.json()["error"]) == (409, "not_regenerable"), r.text


async def test_nothing_to_regenerate_in_a_new_conversation(env: Env) -> None:
    tok = await env.token()
    r = await env.client.post(URL, json={}, headers=_h(tok))
    assert (r.status_code, r.json()["error"]) == (409, "not_regenerable")


async def test_external_storage_regenerates_from_the_sent_history(env: Env) -> None:
    from tests.test_rewrite import _external

    await _external(env)
    env.script.add("function:primary", ["Again."])
    tok = await env.token()
    history = [
        {"role": "user", "text": "Hi", "content": [{"text": "Hi"}]},
        {"role": "assistant", "text": "Hello.", "content": [{"text": "Hello."}]},
    ]
    r = await env.client.post(URL, json={"history": history}, headers=_h(tok))
    assert r.status_code == 200, r.text
    ev = parse_sse(r.text)
    done = ev[-1]
    assert done["replacedFrom"] == 0 and _text(ev) == "Again."
    assert [m["role"] for m in done["messages"]] == ["user", "assistant"]
