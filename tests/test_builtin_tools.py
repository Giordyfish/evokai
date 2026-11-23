"""Provider built-in tools (pydantic-ai native tools): granted per application, turned on per agent,
refused when they would keep data at the provider without the data protection allowing it."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart

from evokai.agents.definitions import AgentDef
from evokai.protection import DataProtection
from tests.conftest import Env


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


HELPER = {
    "name": "helper",
    "title": "Helper",
    "model": "function:primary",
    "mcp_servers": ["app"],
    "instructions": "You are an assistant.",
}


async def _grant(env: Env, *names: str) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "builtin", "name": n, "value": True} for n in names]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text


async def _put(env: Env, tools: list[dict[str, Any]]) -> Any:
    return await env.client.put(
        "/agent/v1/admin/agents/helper", json={**HELPER, "builtin_tools": tools}, headers=_adm(env)
    )


def _spy(env: Env) -> list[Any]:
    seen: list[Any] = []
    original = env.script.model

    def spy(name: str) -> Any:
        model = original(name)
        inner = model.stream_function
        assert inner is not None

        async def stream(messages: Any, info: Any) -> Any:
            seen.append(list(info.model_request_parameters.native_tools))
            async for x in inner(messages, info):
                yield x

        model.stream_function = stream
        return model

    env.svc.catalog._factory = spy
    env.svc.catalog._models.clear()
    return seen


def test_a_definition_names_known_kinds_once() -> None:
    base = {"name": "a", "model": "function:x", "instructions": "x"}
    agent = AgentDef.model_validate({**base, "builtin_tools": [{"kind": "web_search"}]})
    assert agent.builtin_tools[0].kind == "web_search"
    with pytest.raises(ValidationError):
        AgentDef.model_validate({**base, "builtin_tools": [{"kind": "teleport"}]})
    with pytest.raises(ValidationError):
        AgentDef.model_validate({**base, "builtin_tools": [{"kind": "web_search"}] * 2})
    with pytest.raises(ValidationError, match="secret"):
        AgentDef.model_validate({
            **base,
            "builtin_tools": [
                {
                    "kind": "mcp_server",
                    "config": {"id": "x", "url": "https://mcp.example", "authorization_token": "t"},
                }
            ],
        })


def test_a_profile_without_retention_cannot_keep_data_at_the_provider() -> None:
    assert DataProtection.model_validate({"provider_tool_storage": True}).provider_tool_storage
    with pytest.raises(ValidationError):
        DataProtection.model_validate({
            "retention": "zero_retention",
            "provider_tool_storage": True,
        })


async def test_not_granted_means_not_saved_and_not_offered(env: Env) -> None:
    r = await _put(env, [{"kind": "web_search"}])
    assert (r.status_code, r.json()["error"]) == (400, "not_granted"), r.text
    seen = _spy(env)
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hi")
    assert seen == [[]]


async def test_a_granted_web_search_follows_the_egress_allow_list(env: Env) -> None:
    await _grant(env, "web_search")
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"modes": {"tools.egress": "enforce"}, "tools": {"egress_allow": ["docs.example"]}},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    r = await _put(env, [{"kind": "web_search", "config": {"max_uses": 2}}])
    assert r.status_code == 200, r.text
    seen = _spy(env)
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hi")
    ((tool,),) = seen
    assert tool.kind == "web_search" and tool.max_uses == 2
    assert tool.allowed_domains == ["docs.example"]


async def test_tools_that_keep_data_need_the_data_protection(env: Env) -> None:
    await _grant(env, "file_search")
    spec = [{"kind": "file_search", "config": {"file_store_ids": ["vs_1"]}}]
    r = await _put(env, spec)
    assert (r.status_code, r.json()["error"]) == (400, "provider_storage"), r.text
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"data_protection": {"provider_tool_storage": True}},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    r = await _put(env, spec)
    assert r.status_code == 200, r.text
    seen = _spy(env)
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hi")
    assert [t.kind for t in seen[0]] == ["file_search"]
    report = (await env.client.get("/agent/v1/admin/data-protection", headers=_adm(env))).json()
    assert report["builtinTools"] == [
        {"agent": "helper", "kind": "file_search", "keepsData": True, "allowed": True}
    ]


async def test_web_search_results_are_tool_and_citation_events(env: Env) -> None:
    await _grant(env, "web_search")
    r = await _put(env, [{"kind": "web_search"}])
    assert r.status_code == 200, r.text
    call = NativeToolCallPart(tool_name="web_search", args={"query": "evokai"}, tool_call_id="w1")
    ret = NativeToolReturnPart(
        tool_name="web_search",
        content=[{"url": "https://docs.example/a", "title": "Page A", "type": "web_search_result"}],
        tool_call_id="w1",
    )
    env.script.add("function:primary", ["Looking. ", {1: call}, {2: ret}, "Found it [1]."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Search")
    tools = [(e["name"], e["status"]) for e in ev if e["type"] == "tool"]
    assert tools == [("web_search", "start"), ("web_search", "done")]
    (cite,) = [e for e in ev if e["type"] == "citation"]
    assert cite["url"] == "https://docs.example/a" and cite["title"] == "Page A"
    assert cite["index"] == 1
    text = "".join(e["delta"] for e in ev if e["type"] == "text")
    assert text == "Looking. Found it [1]."


@pytest.mark.parametrize(
    ("allow", "config", "expected"),
    [
        (["docs.example"], {"allowed_domains": ["evil.example"]}, None),
        (
            ["docs.example"],
            {"allowed_domains": ["evil.example", "api.docs.example"]},
            ["api.docs.example"],
        ),
        ([], {}, None),
    ],
)
async def test_configured_domains_never_escape_the_egress_allow_list(
    env: Env, allow: list[str], config: dict[str, Any], expected: list[str] | None
) -> None:
    await _grant(env, "web_fetch")
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"modes": {"tools.egress": "enforce"}, "tools": {"egress_allow": allow}},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    assert (await _put(env, [{"kind": "web_fetch", "config": config}])).status_code == 200
    seen = _spy(env)
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hi")
    if expected is None:
        assert seen == [[]]
    else:
        ((tool,),) = seen
        assert tool.allowed_domains == expected


async def test_web_sources_follow_the_link_policy(env: Env) -> None:
    await _grant(env, "web_search")
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"modes": {"output.links": "enforce"}, "links": {"allow": ["docs.example"]}},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    assert (await _put(env, [{"kind": "web_search"}])).status_code == 200
    ret = NativeToolReturnPart(
        tool_name="web_search",
        content=[
            {"url": "https://evil.example/x", "title": "Elsewhere"},
            {"url": "https://docs.example/a", "title": "Page A"},
        ],
        tool_call_id="w1",
    )
    env.script.add("function:primary", [{1: ret}, "Found."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Search")
    assert [e["url"] for e in ev if e["type"] == "citation"] == ["https://docs.example/a"]
