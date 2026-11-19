"""Integrations act as the signed-in person; a shared account only when an agent says so."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from evokai import capabilities as caps
from evokai.capabilities import harness
from evokai.engine.capability_tools import _GuardedCapabilityTools
from tests.conftest import Env
from tests.test_capabilities import _enable, _trace
from tests.test_connections import _connect, _session, _setup, fx

__all__ = ["fx"]


def _ctx(**loaded: Any) -> Any:
    trace = SimpleNamespace(
        root=SimpleNamespace(attrs={}, set=lambda **kw: trace.root.attrs.update(kw))
    )
    return harness.Context(
        engine=None,
        agent=None,
        rs=SimpleNamespace(trace=trace),  # ty: ignore[invalid-argument-type]
        scope=None,  # ty: ignore[invalid-argument-type]
        loaded=loaded,
        secrets={"linear": {"token": "lin_app"}, "github": {"token": "ghp_app"}},
    )


def test_a_per_person_integration_never_uses_the_application_s_account_by_default() -> None:
    ctx = _ctx()
    with pytest.raises(caps.CapabilityError, match="no linear connection"):
        harness.with_credentials(ctx, "linear", {})
    with pytest.raises(caps.CapabilityError, match="no google connection"):
        harness.with_credentials(ctx, "google_workspace", {"services": ["drive"]})


def test_acts_as_application_is_explicit_traced_and_tagged() -> None:
    ctx = _ctx()
    cap = harness.with_credentials(ctx, "github", {"acts_as": "application"})
    assert isinstance(cap, harness.Tagged) and cap.wrapped.auth == "ghp_app"
    assert cap.tags == {"evokai_capability": "github", "evokai_acts_as": "application"}
    assert ctx.rs.trace.root.attrs["actsAsApplication"] == ["github"]
    with pytest.raises(caps.CapabilityError, match="person or application"):
        harness.with_credentials(ctx, "github", {"acts_as": "someone"})
    # Services with API keys only keep the application's credential, untagged.
    ctx.secrets["exa"] = {"api_key": "k"}
    assert not isinstance(harness.with_credentials(ctx, "exa_search", {}), harness.Tagged)


async def test_google_workspace_uses_the_person_s_own_token() -> None:
    ctx = _ctx(**{
        "connection:google": {
            "connected": True,
            "token": "person-token",
            "title": "Google Workspace",
            "server": "gws",
        }
    })
    cap = harness.with_credentials(ctx, "google_workspace", {"services": ["drive"]})
    assert cap.tags["evokai_acts_as"] == "person"
    assert await cap.wrapped.auth(None) == "person-token"


async def test_a_person_without_a_connection_is_asked_to_connect(env: Env, fx: Any) -> None:
    del fx
    await _setup(env)
    await _enable(env, "google_workspace", {"services": ["drive"]})
    env.script.add("function:primary", ["Please connect your account."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Find my drive files")
    card = next(e for e in ev if e["type"] == "connect")
    assert card["provider"] == "google" and card["server"] == "gws"
    t = await _trace(env, ev[0]["turnId"])
    assert "google_workspace" in t["spans"][0]["attrs"].get("capabilities", [])


async def test_a_connected_person_gets_their_own_token(env: Env, fx: Any) -> None:
    await _setup(env)
    s = await _session(env)
    await _connect(env, fx, s, "google")
    from evokai.engine.state import Scope

    scope = Scope(
        app=await env.svc.store.applications.get(env.app_id),
        grants=SimpleNamespace(connection=lambda p: p == "google"),
        who=SimpleNamespace(subject="u1", application_id=env.app_id),
        tags=[],
        run_id="r1",
    )
    got = await harness._connection(env.svc.engine, scope, "google")
    assert got is not None and got["connected"] is True and got["token"]
    nobody = Scope(
        app=scope.app,
        grants=scope.grants,
        who=SimpleNamespace(subject="u9", application_id=env.app_id),
        tags=[],
        run_id="r2",
    )
    other = await harness._connection(env.svc.engine, nobody, "google")
    assert other is not None and other["connected"] is False


async def test_a_write_through_a_shared_account_needs_the_person_not_a_capability() -> None:
    from pydantic_ai.tools import ToolDefinition

    ran: list[str] = []

    class Inner:
        async def call_tool(self, *_a: Any) -> str:
            ran.append("x")
            return "done"

    spans: list[Any] = []

    class Trace:
        def start(self, *_a: Any, **attrs: Any) -> Any:
            s = SimpleNamespace(attrs=attrs, status="ok", set=lambda **kw: s.attrs.update(kw))
            spans.append(s)
            return s

        def put(self, *_a: Any, **_k: Any) -> None:
            return None

        push = pop = end = put

    async def allowed(*_a: Any) -> Any:  # ruff: ignore[unused-async]
        return SimpleNamespace(allowed=True)

    async def result(_name: str, text: str) -> str:  # ruff: ignore[unused-async]
        return text

    guard = SimpleNamespace(
        call_args=lambda a: a,
        check_call=allowed,
        forced_confirmation=lambda _n: False,
        tool_result=result,
        asks=lambda _n, _a: None,
    )
    st = SimpleNamespace(
        tools={},
        guard=guard,
        trace=Trace(),
        args={},
        ok={},
        nested=0,
        tool_calls=0,
        confirm=None,
        wrote=set(),
        locale="en",
        on_tool=None,
        emit=None,
        settings=SimpleNamespace(tool_result_chars=100),
    )
    tool = SimpleNamespace(
        tool_def=ToolDefinition(
            name="github_create_issue",
            parameters_json_schema={"type": "object"},
            metadata={"annotations": {}, "evokai_acts_as": "application"},
        ),
        args_validator=None,
    )
    ctx = SimpleNamespace(deps=st, tool_call_id="c1", tool_call_approved=True)
    wrapper = _GuardedCapabilityTools(wrapped=Inner())  # ty: ignore[invalid-argument-type]
    out = await wrapper.call_tool("github_create_issue", {}, ctx, tool)  # ty: ignore[invalid-argument-type]
    assert "person's own confirmation" in out and ran == []
    assert spans[0].attrs["actsAs"] == "application"
    st.confirm = {"approve": True, "waitedMs": 5}
    assert await wrapper.call_tool("github_create_issue", {}, ctx, tool) == "done"  # ty: ignore[invalid-argument-type]


async def test_github_and_slack_act_as_the_person_through_their_evokai_connection() -> None:
    ctx = _ctx(**{
        "connection:github": {
            "connected": True,
            "token": "ghu_person",
            "title": "GitHub",
            "server": "",
        },
        "connection:slack": {"connected": False, "token": None, "title": "Slack", "server": ""},
    })
    cap = harness.with_credentials(ctx, "github", {})
    assert cap.tags == {
        "evokai_capability": "github",
        "evokai_acts_as": "person",
        "evokai_provider": "github",
    }
    assert await cap.wrapped.auth(None) == "ghu_person"
    # Not connected yet: a card to connect, never the application's account in their place.
    card = harness.with_credentials(ctx, "slack", {})
    assert isinstance(card, harness.ConnectFirst) and card.card["provider"] == "slack"


def test_a_refused_token_is_found_inside_wrapped_errors() -> None:
    import httpx

    from evokai.engine.capability_tools import unauthorized

    req = httpx.Request("POST", "https://mcp.example/mcp")
    refused = httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
    other = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))
    assert unauthorized(refused)
    assert unauthorized(ExceptionGroup("tool", [RuntimeError("x"), refused]))
    try:
        try:
            raise refused
        except httpx.HTTPStatusError as e:
            raise RuntimeError("mcp call failed") from e
    except RuntimeError as wrapped:
        assert unauthorized(wrapped)
    assert not unauthorized(other) and not unauthorized(RuntimeError("x"))


async def test_a_401_on_a_person_s_token_asks_them_to_connect_again() -> None:
    import httpx
    from pydantic_ai.tools import ToolDefinition

    req = httpx.Request("POST", "https://api.githubcopilot.com/mcp/")

    class Inner:
        async def call_tool(self, *_a: Any) -> str:
            raise httpx.HTTPStatusError(
                "401", request=req, response=httpx.Response(401, request=req)
            )

    spans: list[Any] = []

    class Trace:
        def start(self, *_a: Any, **attrs: Any) -> Any:
            s = SimpleNamespace(attrs=attrs, status="ok", set=lambda **kw: s.attrs.update(kw))
            spans.append(s)
            return s

        def put(self, *_a: Any, **_k: Any) -> None:
            return None

        push = pop = end = put

    async def allowed(*_a: Any) -> Any:  # ruff: ignore[unused-async]
        return SimpleNamespace(allowed=True)

    expired: list[str] = []

    async def mark(provider: str) -> None:  # ruff: ignore[unused-async]
        expired.append(provider)

    st = SimpleNamespace(
        tools={},
        guard=SimpleNamespace(
            call_args=lambda a: a,
            check_call=allowed,
            forced_confirmation=lambda _n: False,
            asks=lambda _n, _a: None,
        ),
        trace=Trace(),
        args={},
        ok={},
        nested=0,
        tool_calls=0,
        confirm=None,
        wrote=set(),
        locale="en",
        on_tool=None,
        emit=None,
        expired=mark,
        settings=SimpleNamespace(tool_result_chars=100),
    )
    tool = SimpleNamespace(
        tool_def=ToolDefinition(
            name="github_list_issues",
            parameters_json_schema={"type": "object"},
            metadata={
                "annotations": {"readOnlyHint": True},
                "evokai_acts_as": "person",
                "evokai_provider": "github",
            },
        ),
        args_validator=None,
    )
    ctx = SimpleNamespace(deps=st, tool_call_id="c1", tool_call_approved=False)
    wrapper = _GuardedCapabilityTools(wrapped=Inner())  # ty: ignore[invalid-argument-type]
    with pytest.raises(httpx.HTTPStatusError):
        await wrapper.call_tool("github_list_issues", {}, ctx, tool)  # ty: ignore[invalid-argument-type]
    assert expired == ["github"]
    assert spans[0].attrs["connection"] == "expired"
    # The same error through the application's shared account marks nobody's connection.
    expired.clear()
    tool.tool_def.metadata = {
        "annotations": {"readOnlyHint": True},
        "evokai_acts_as": "application",
    }
    with pytest.raises(httpx.HTTPStatusError):
        await wrapper.call_tool("github_list_issues", {}, ctx, tool)  # ty: ignore[invalid-argument-type]
    assert expired == []
