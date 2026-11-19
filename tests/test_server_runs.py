"""Server runs: structured agents for back-office automation, called by an app backend."""

from __future__ import annotations

from typing import Any

from tests.conftest import Env, Tool

TRIAGE = {
    "name": "triage",
    "model": "function:primary",
    "fallbacks": ["function:backup"],
    "mcp_servers": ["app"],
    "channels": ["server"],
    "temperature": 0,
    "instructions": "Classify the customer's e-mail.",
    "output_schema": {
        "type": "object",
        "required": ["category", "priority"],
        "properties": {
            "category": {"type": "string", "enum": ["order", "technical", "other"]},
            "priority": {"type": "integer", "minimum": 1, "maximum": 3},
        },
        "additionalProperties": False,
    },
}


async def _put(env: Env, body: dict[str, Any]) -> None:
    r = await env.client.put(
        f"/agent/v1/admin/agents/{body['name']}",
        json=body,
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


async def _run(env: Env, name: str, **body: Any) -> Any:
    return await env.client.post(
        f"/agent/v1/agents/{name}/run",
        json={"input": "My order arrived twice this month.", **body},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )


async def test_a_structured_run_returns_schema_valid_output_and_usage(env: Env) -> None:
    await _put(env, TRIAGE)
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        [Tool("final_result", {"category": "order", "priority": 2}, "out1")],
    )
    r = await _run(
        env,
        "triage",
        attachments=[{"name": "message.txt", "mediaType": "text/plain", "text": "Message 42"}],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["output"] == {"category": "order", "priority": 2}
    assert body["usage"]["toolCalls"] == 1 and body["conversationId"] is None
    assert "Message 42" in str(env.script.seen[0][1])
    assert "order_refund" not in env.script.tools_seen  # data-changing tools are not offered
    assert "final_result" in env.script.tools_seen


async def test_an_invalid_answer_gets_one_retry_then_fails(env: Env) -> None:
    await _put(env, TRIAGE)
    env.script.add(
        "function:primary",
        [Tool("final_result", {"category": "weather", "priority": 9}, "o1")],
        [Tool("final_result", {"category": "other", "priority": 3}, "o2")],
    )
    ok = await _run(env, "triage")
    assert ok.json()["output"] == {"category": "other", "priority": 3}
    retry = env.script.seen[-1][1]
    assert "Invalid answer" in str(retry)

    env.script.add(
        "function:primary",
        [Tool("final_result", {"category": "weather"}, "o3")],
        [Tool("final_result", {"priority": 1}, "o4")],
    )
    bad = await _run(env, "triage")
    assert (bad.status_code, bad.json()["error"]) == (502, "invalid_output")


async def test_server_agents_cannot_be_used_from_browsers_and_vice_versa(env: Env) -> None:
    await _put(env, TRIAGE)
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "triage"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert (r.status_code, r.json()["error"]) == (403, "channel")
    browser_only = await _run(env, "helper")
    assert (browser_only.status_code, browser_only.json()["error"]) == (403, "channel")


async def test_runs_count_against_the_application_cap(env: Env) -> None:
    await _put(env, TRIAGE)
    await env.client.patch(
        "/agent/v1/admin/application",
        json={"monthly_cap_usd": 0},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    r = await _run(env, "triage")
    assert (r.status_code, r.json()["error"]) == (429, "limit")


async def test_a_run_can_be_kept_as_a_conversation(env: Env) -> None:
    await _put(env, TRIAGE)
    env.script.add("function:primary", [Tool("final_result", {"category": "other", "priority": 1})])
    r = await _run(env, "triage", subject="system:triage", store=True)
    cid = r.json()["conversationId"]
    one = await env.client.get(
        f"/agent/v1/conversations/{cid}?subject=system:triage",
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert [x["role"] for x in one.json()["messages"]] == ["user", "assistant"]
