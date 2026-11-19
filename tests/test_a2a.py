"""A2A: Agent Cards and JSON-RPC (1.0 and 0.3) over server runs, off until granted."""

from __future__ import annotations

import base64
import json
from typing import Any

from tests.conftest import Env, Tool

PUBLISHED = {
    "name": "bot",
    "title": "Back-office bot",
    "model": "function:primary",
    "channels": ["server"],
    "a2a": True,
    "suggestions": ["How was October?"],
    "instructions": "Answer about the data.",
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


def _srv(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.server_key}"}


async def _grant(env: Env, value: bool | None) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "channel", "name": "a2a", "value": value}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text


async def _publish(env: Env, **extra: Any) -> None:
    await _grant(env, True)
    r = await env.client.put(
        "/agent/v1/admin/agents/bot", json={**PUBLISHED, **extra}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text


def _url(env: Env, agent: str = "bot") -> str:
    return f"/agent/v1/a2a/{env.app_id}/{agent}"


async def _rpc(env: Env, method: str, params: Any, **headers: str) -> Any:
    r = await env.client.post(
        _url(env),
        json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
        headers={**_srv(env), **headers},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _message(text: str = "How was October?", v1: bool = False, **extra: Any) -> dict[str, Any]:
    if v1:
        return {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": text}], **extra}
    return {
        "kind": "message",
        "messageId": "m1",
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        **extra,
    }


def _frames(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:])
        for block in text.split("\n\n")
        for line in block.splitlines()
        if line.startswith("data: ")
    ]


async def test_a2a_is_off_until_granted_and_the_agent_is_marked(env: Env) -> None:
    card = f"{_url(env, 'helper')}/.well-known/agent-card.json"
    assert (await env.client.get(card)).status_code == 404
    r = await env.client.put("/agent/v1/admin/agents/bot", json=PUBLISHED, headers=_adm(env))
    assert r.status_code == 400 and "A2A is not granted" in r.text
    await _grant(env, True)
    assert (await env.client.get(card)).status_code == 404  # helper is not marked a2a
    r = await env.client.post(
        _url(env, "helper"),
        json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {}},
        headers=_srv(env),
    )
    assert r.status_code == 404
    view = (await env.client.get("/agent/v1/admin/grants", headers=_adm(env))).json()
    channels = next(g for g in view["groups"] if g["id"] == "channels")["items"]
    a2a = next(x for x in channels if x["name"] == "a2a")
    assert a2a["default"] is False and a2a["value"] is True


async def test_the_agent_card_describes_the_agent_and_its_auth(env: Env) -> None:
    await _publish(env)
    r = await env.client.get(f"{_url(env)}/.well-known/agent-card.json")
    assert r.status_code == 200
    card = r.json()
    rpc = f"http://evokai{_url(env)}"
    assert card["name"] == "Back-office bot" and card["url"] == rpc
    assert card["protocolVersion"] == "0.3.0" and card["preferredTransport"] == "JSONRPC"
    assert {i["protocolVersion"] for i in card["supportedInterfaces"]} == {"1.0", "0.3"}
    assert card["capabilities"]["streaming"] is True
    scheme = card["securitySchemes"]["evokaiServerKey"]
    assert scheme["type"] == "http" and scheme["scheme"] == "bearer"
    assert scheme["httpAuthSecurityScheme"]["scheme"] == "Bearer"
    assert card["security"] == [{"evokaiServerKey": []}]
    (skill,) = card["skills"]
    assert skill["id"] == "bot" and skill["examples"] == ["How was October?"]
    v03 = (await env.client.get(f"{_url(env)}/.well-known/agent-card.json?version=0.3")).json()
    assert "supportedInterfaces" not in v03 and "httpAuthSecurityScheme" not in str(v03)
    v1 = (await env.client.get(f"{_url(env)}/.well-known/agent-card.json?version=1.0")).json()
    assert "protocolVersion" not in v1 and "securityRequirements" in v1


async def test_message_send_in_both_versions_runs_the_agent(env: Env) -> None:
    await _publish(env)
    env.script.add("function:primary", ["24 orders."], ["Again 24."])
    old = await _rpc(env, "message/send", {"message": _message(contextId="ctx-1")})
    task = old["result"]
    assert old["id"] == 7 and task["kind"] == "task" and task["contextId"] == "ctx-1"
    assert task["status"]["state"] == "completed"
    assert task["artifacts"][0]["parts"] == [{"kind": "text", "text": "24 orders."}]
    assert task["history"][-1]["role"] == "agent" and task["history"][0]["role"] == "user"
    new = await _rpc(env, "SendMessage", {"message": _message(v1=True)})
    task = new["result"]["task"]
    assert "kind" not in task and task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"] == [{"text": "Again 24."}]
    assert task["history"][-1]["role"] == "ROLE_AGENT"
    got = await _rpc(env, "GetTask", {"id": task["id"]})
    assert got["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "artifacts" not in got["result"]  # Evokai keeps no content
    got = await _rpc(env, "tasks/get", {"id": task["id"]})
    assert got["result"]["kind"] == "task" and got["result"]["status"]["state"] == "completed"
    cancel = await _rpc(env, "tasks/cancel", {"id": task["id"]})
    assert cancel["error"]["code"] == -32002
    usage = await env.client.get("/agent/v1/usage?groupBy=subject", headers=_srv(env))
    assert "a2a:bot" in str(usage.json()["rows"])


async def test_data_and_text_file_parts_become_attachments_and_structured_output_is_data(
    env: Env,
) -> None:
    schema = {
        "type": "object",
        "required": ["category"],
        "properties": {"category": {"type": "string"}},
    }
    await _publish(env, output_schema=schema)
    env.script.add("function:primary", [Tool("final_result", {"category": "order"}, "o1")])
    raw = base64.b64encode(b"Message 42").decode()
    file = {"kind": "file", "file": {"name": "t.txt", "mimeType": "text/plain", "bytes": raw}}
    msg = _message(
        parts=[{"kind": "text", "text": "Triage"}, {"kind": "data", "data": {"a": 1}}, file]
    )
    res = await _rpc(env, "message/send", {"message": msg})
    assert res["result"]["artifacts"][0]["parts"] == [
        {"kind": "data", "data": {"category": "order"}}
    ]
    seen = str(env.script.seen[-1][1])
    assert "Message 42" in seen and '{"a": 1}' in seen
    url = {"kind": "file", "file": {"uri": "https://x.test/a.pdf", "mimeType": "application/pdf"}}
    bad = await _rpc(env, "message/send", {"message": _message(parts=[url])})
    assert bad["error"]["code"] == -32005


async def test_message_stream_sends_task_progress_artifact_and_final_status(env: Env) -> None:
    await _publish(env)
    env.script.add("function:primary", ["Streamed answer."], ["Again."])
    r = await env.client.post(
        _url(env),
        json={"jsonrpc": "2.0", "id": "s1", "method": "message/stream",
              "params": {"message": _message()}},
        headers=_srv(env),
    )  # fmt: skip
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [f["result"] for f in _frames(r.text)]
    assert [f["kind"] for f in frames] == [
        "task",
        "status-update",
        "artifact-update",
        "status-update",
    ]
    assert frames[0]["status"]["state"] == "submitted"
    assert frames[1]["status"]["state"] == "working" and frames[1]["final"] is False
    assert frames[2]["artifact"]["parts"] == [{"kind": "text", "text": "Streamed answer."}]
    assert frames[-1]["status"]["state"] == "completed" and frames[-1]["final"] is True
    r = await env.client.post(
        _url(env),
        json={"jsonrpc": "2.0", "id": 2, "method": "SendStreamingMessage",
              "params": {"message": _message(v1=True)}},
        headers=_srv(env),
    )  # fmt: skip
    frames = [f["result"] for f in _frames(r.text)]
    assert [next(iter(f)) for f in frames] == [
        "task",
        "statusUpdate",
        "artifactUpdate",
        "statusUpdate",
    ]
    assert frames[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_protocol_errors(env: Env) -> None:
    await _publish(env)
    r = await env.client.post(_url(env), json={"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert r.status_code == 401
    r = await env.client.post(
        _url(env), content=b"{not json", headers={**_srv(env), "Content-Type": "application/json"}
    )
    assert r.json()["error"]["code"] == -32700
    assert (await _rpc(env, "nope", {}))["error"]["code"] == -32601
    assert (await _rpc(env, "tasks/pushNotificationConfig/set", {}))["error"]["code"] == -32003
    assert (await _rpc(env, "SubscribeToTask", {"id": "x"}))["error"]["code"] == -32004
    assert (await _rpc(env, "GetTask", {"id": "unknown"}))["error"]["code"] == -32001
    assert (await _rpc(env, "message/send", {}))["error"]["code"] == -32602
    old = await _rpc(env, "message/send", {"message": _message()}, **{"A2A-Version": "9.9"})
    assert old["error"]["code"] == -32009


async def test_caps_and_revoked_grants_apply(env: Env) -> None:
    await _publish(env)
    await env.client.patch(
        "/agent/v1/admin/application", json={"monthly_cap_usd": 0}, headers=_adm(env)
    )
    res = await _rpc(env, "message/send", {"message": _message()})
    status = res["result"]["status"]
    assert status["state"] == "failed" and status["message"]["parts"][0]["text"]
    assert env.script.seen == []
    await _grant(env, False)
    assert (await env.client.get(f"{_url(env)}/.well-known/agent-card.json")).status_code == 404
