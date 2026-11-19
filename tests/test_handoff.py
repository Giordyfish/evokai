"""Handoff: an agent hands the conversation to another agent of the same application."""

from __future__ import annotations

from typing import Any

from tests.conftest import Env, Tool, system_text, types

RETURNS = {
    "name": "returns",
    "title": "Returns desk",
    "model": "function:returns",
    "instructions": "You handle returns only.",
    "skills": [],
    "client_tools": {
        "chart": False,
        "diagram": False,
        "ask": False,
        "memory": False,
        "navigate": {"enabled": False},
    },
}


async def _put(env: Env, body: dict[str, Any]) -> None:
    r = await env.client.put(
        f"/agent/v1/admin/agents/{body['name']}",
        json=body,
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


async def _helper(env: Env, **extra: Any) -> None:
    r = await env.client.get(
        "/agent/v1/admin/agents", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    helper = next(a for a in r.json()["items"] if a["name"] == "helper")
    helper.pop("version", None)
    await _put(env, {**helper, **extra})


async def test_an_agent_hands_the_conversation_over_and_the_next_turn_is_the_other_agents(
    env: Env,
) -> None:
    await _put(env, RETURNS)
    await _helper(env, handoff_to=["returns", "ghost"])
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"}, "c0")],
        [Tool("handoff", {"agent": "returns", "reason": "a return question"}, "c1")],
        ["The returns desk takes over."],
    )
    env.script.add("function:returns", ["Your refund of 42 euros is on its way."])
    tok = await env.token()
    events = await env.chat(tok["token"], text="How do I return a book?")
    assert "handoff" in env.script.tools_seen
    (h,) = [e for e in events if e["type"] == "handoff"]
    assert h["from"] == "helper" and h["to"] == "returns" and h["title"] == "Returns desk"
    assert h["reason"] == "a return question"
    assert types(events)[-1] == "done"
    tools = next(x for x in env.script.seen if x[0] == "function:primary")
    # Only agents that exist and take browser sessions are offered.
    assert "ghost" not in str(tools)

    events = await env.chat(tok["token"], text="And November?")
    name, messages = env.script.seen[-1]
    assert name == "function:returns"
    assert "You handle returns only." in system_text(messages)
    # The history is kept, the earlier agent's tool calls included (as text: returns has no tools).
    assert "How do I return a book?" in str(messages)
    assert "[called orders_month" in str(messages)
    r = await env.client.get(
        "/agent/v1/session",
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": "http://app.test"},
    )
    assert r.json()["agent"] == "returns"


async def test_no_handoff_tool_without_targets_and_unknown_targets_are_refused(env: Env) -> None:
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hello")
    assert "handoff" not in env.script.tools_seen

    await _put(env, RETURNS)
    await _helper(env, handoff_to=["returns"])
    env.script.add(
        "function:primary",
        [Tool("handoff", {"agent": "sales", "reason": "x"}, "c1")],
        ["I stay."],
    )
    tok = await env.token()
    events = await env.chat(tok["token"], text="Sales please")
    assert [e for e in events if e["type"] == "handoff"] == []
    assert "Unknown agent" in str(env.script.seen[-1][1])


async def test_a_handoff_target_that_is_not_granted_is_not_offered(env: Env) -> None:
    await _put(env, {**RETURNS, "model": "function:returns"})
    await _helper(env, handoff_to=["returns"])
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "model", "name": "function:returns", "value": "off"}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hello")
    assert "handoff" not in env.script.tools_seen
