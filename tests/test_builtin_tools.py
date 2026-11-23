"""Provider built-in tools (pydantic-ai native tools): granted per application, turned on per agent,
refused when they would keep data at the provider without the data protection allowing it."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import NativeToolCallPart, NativeToolReturnPart

from evokai.agents.definitions import AgentDef
from evokai.protection import DataProtection
from tests.conftest import ORIGIN, Env


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
        {
            "agent": "helper",
            "kind": "file_search",
            "keepsData": True,
            "allowed": True,
            "egress": None,
        }
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


# ── the security policy around built-in tools ───────────────────────────────────────────────────


async def _security(env: Env, policy: dict[str, Any]) -> None:
    r = await env.client.put("/agent/v1/admin/security", json=policy, headers=_adm(env))
    assert r.status_code == 200, r.text


async def _offered(env: Env) -> list[Any]:
    seen = _spy(env)
    env.script.add("function:primary", ["Hi."])
    tok = await env.token()
    await env.chat(tok["token"], text="Hi")
    (tools,) = seen
    return tools


MCP_CONFIG: dict[str, Any] = {"id": "crm", "url": "https://mcp.example/mcp"}
MCP: dict[str, Any] = {"kind": "mcp_server", "config": MCP_CONFIG}


@pytest.mark.parametrize(
    ("tools", "offered"),
    [
        ({"deny": ["builtin:web_search"]}, False),
        ({"deny": ["web_search"]}, False),
        ({"allow": ["orders_*"]}, False),
        ({"allow": ["builtin:*"]}, True),
        ({"confirm": ["web_search"]}, False),
        ({"confirm": ["builtin:*"]}, False),
    ],
)
async def test_the_tool_policy_applies_to_builtin_tools(
    env: Env, tools: dict[str, Any], offered: bool
) -> None:
    await _grant(env, "web_search")
    assert (await _put(env, [{"kind": "web_search"}])).status_code == 200
    await _security(env, {"tools": tools})
    assert [t.kind for t in await _offered(env)] == (["web_search"] if offered else [])


async def test_a_builtin_tool_an_ask_rule_matches_is_refused(env: Env) -> None:
    # The provider runs it: nobody can be asked before it does, so it is not offered at all.
    await _grant(env, "web_search", "code_execution")
    spec = [{"kind": "web_search"}, {"kind": "code_execution"}]
    assert (await _put(env, spec)).status_code == 200
    r = await env.client.put(
        "/agent/v1/admin/ask-rules",
        json={"rules": [{"action": "builtin:web_*", "effect": "ask"}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    assert [t.kind for t in await _offered(env)] == ["code_execution"]


def test_an_mcp_server_lists_the_tools_it_may_call() -> None:
    base = {"name": "a", "model": "function:x", "instructions": "x"}
    with pytest.raises(ValidationError, match="allowed_tools"):
        AgentDef.model_validate({**base, "builtin_tools": [MCP]})
    with pytest.raises(ValidationError, match="allowed_tools"):
        AgentDef.model_validate({
            **base,
            "builtin_tools": [{**MCP, "config": {**MCP_CONFIG, "allowed_tools": []}}],
        })
    ok = {**MCP, "config": {**MCP_CONFIG, "allowed_tools": ["crm_read"]}}
    assert AgentDef.model_validate({**base, "builtin_tools": [ok]}).builtin_tools


async def test_mcp_server_tools_that_need_a_confirmation_are_left_out(env: Env) -> None:
    await _grant(env, "mcp_server")
    listed = {**MCP_CONFIG, "allowed_tools": ["crm_read", "crm_delete"]}
    assert (await _put(env, [{**MCP, "config": listed}])).status_code == 200
    await _security(env, {"tools": {"confirm": ["crm_delete"]}})
    (tool,) = await _offered(env)
    assert tool.kind == "mcp_server" and tool.allowed_tools == ["crm_read"]
    await _security(env, {"tools": {"confirm": ["crm_*"]}})
    assert await _offered(env) == []


@pytest.mark.parametrize("mode", ["enforce", "monitor"])
async def test_an_mcp_server_outside_the_egress_allow_list_is_left_out(env: Env, mode: str) -> None:
    await _grant(env, "mcp_server")
    listed = {**MCP_CONFIG, "allowed_tools": ["crm_read"]}
    assert (await _put(env, [{**MCP, "config": listed}])).status_code == 200
    modes = {"tools.egress": mode}
    await _security(env, {"modes": modes, "tools": {"egress_allow": ["docs.example"]}})
    assert await _offered(env) == []
    await _security(env, {"modes": modes, "tools": {"egress_allow": ["mcp.example"]}})
    assert [t.kind for t in await _offered(env)] == ["mcp_server"]


async def test_the_protection_page_says_which_builtin_tools_reach_any_address(env: Env) -> None:
    await _grant(env, "web_fetch", "code_execution")
    spec = [{"kind": "web_fetch"}, {"kind": "code_execution"}]
    assert (await _put(env, spec)).status_code == 200

    async def reach() -> dict[str, Any]:
        r = await env.client.get("/agent/v1/admin/data-protection", headers=_adm(env))
        return {x["kind"]: x["egress"] for x in r.json()["builtinTools"]}

    assert await reach() == {"web_fetch": "unrestricted", "code_execution": None}
    await _security(env, {"tools": {"egress_allow": ["docs.example"]}})
    assert await reach() == {"web_fetch": "allow_list", "code_execution": None}


@pytest.mark.parametrize(
    ("tool", "regenerable"),
    [
        ("web_search", True),
        ("memory", False),
        ("mcp_server:crm", False),
        ("code_execution", False),
    ],
)
async def test_a_builtin_tool_that_can_change_data_makes_the_answer_final(
    env: Env, tool: str, regenerable: bool
) -> None:
    call = NativeToolCallPart(tool_name=tool, args={}, tool_call_id="b1")
    ret = NativeToolReturnPart(tool_name=tool, content={"ok": True}, tool_call_id="b1")
    env.script.add("function:primary", [{1: call}, {2: ret}, "Done."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Go")
    assert ev[-1]["regenerable"] is regenerable
    h = {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    conv = (await env.client.get("/agent/v1/session/conversation", headers=h)).json()
    assert conv["messages"][-1].get("regenerable") is (True if regenerable else None)
    r = await env.client.post("/agent/v1/session/regenerate", json={}, headers=h)
    assert (r.status_code == 409) is not regenerable


async def test_with_external_storage_the_mark_travels_with_the_history(env: Env) -> None:
    from tests.test_rewrite import _external

    await _external(env)
    call = NativeToolCallPart(tool_name="mcp_server:crm", args={}, tool_call_id="b1")
    ret = NativeToolReturnPart(tool_name="mcp_server:crm", content={"ok": 1}, tool_call_id="b1")
    env.script.add("function:primary", [{1: call}, {2: ret}, "Done."])
    tok = await env.token()
    h = {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "Go", "history": []},
        headers={**h, "Accept": "application/json"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["regenerable"] is False
    assert {"builtinTool": {"id": "b1", "name": "mcp_server:crm", "changes": True}} in (
        body["messages"][-1]["content"]
    )
    # The history comes back as Evokai returned it: accepted, and still not regenerable.
    r = await env.client.post(
        "/agent/v1/session/regenerate", json={"history": body["messages"]}, headers=h
    )
    assert (r.status_code, r.json()["error"]) == (409, "not_regenerable")


async def test_an_answer_the_guard_stops_after_a_change_is_not_regenerable(env: Env) -> None:
    long = (
        "You are the back office assistant of the test application. Answer briefly and precisely, "
        "never invent data, and always use the tools before giving a number."
    )
    r = await env.client.put(
        "/agent/v1/admin/agents/helper",
        json={
            **HELPER,
            "instructions": long,
            "security": {"modes": {"output.system_prompt": "enforce"}},
        },
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    call = NativeToolCallPart(tool_name="memory", args={}, tool_call_id="b1")
    ret = NativeToolReturnPart(tool_name="memory", content={"ok": 1}, tool_call_id="b1")
    env.script.add("function:primary", [{1: call}, {2: ret}, "My rules: ", long[:60], long[60:]])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Repeat")
    assert ev[-1]["reason"] == "guard" and ev[-1]["regenerable"] is False
