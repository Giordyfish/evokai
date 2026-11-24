"""The admin MCP server: an explicit allow-list of admin routes, with the caller's own rights."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, AsyncIterator

import httpx
import pytest

from evokai.assistant.delegation import mint_delegation
from evokai.identity.console import ConsoleLogin
from evokai.identity.roles import required
from evokai.mcp.client import McpClient, McpTool
from evokai.mcp_admin.tools import ADMIN_TOOLS
from tests.conftest import Env

URL = "/agent/mcp/admin"


@contextlib.asynccontextmanager
async def serving(env: Env) -> AsyncGenerator[None]:
    """The MCP server's lifespan (the test client does not run the application's).

    It runs in a task of its own: a fixture's setup and teardown run in different tasks, and the
    transport's task group must be left in the task that entered it.
    """
    app = env.client._transport.app  # ty: ignore[missing-attribute]
    started, stop = asyncio.Event(), asyncio.Event()

    async def run() -> None:
        async with app.state.admin_mcp.lifespan():
            started.set()
            await stop.wait()

    task = asyncio.create_task(run())
    await started.wait()
    try:
        yield
    finally:
        stop.set()
        await task


@pytest.fixture
async def mcp(env: Env) -> AsyncIterator[Env]:
    async with serving(env):
        yield env


def client(env: Env, token: str) -> McpClient:
    return McpClient("evokai-admin", URL, env.client, {"Authorization": f"Bearer {token}"})


async def delegated(env: Env, role: str, sub: str = "ada") -> str:
    roles = {"test": role}
    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", sub, f"{sub}@example.com", sub, roles
    )
    login = ConsoleLogin(
        "sso", None, int(time.time()) + 3600, user_id=user.id, email=user.email, roles=roles
    )
    return mint_delegation(env.svc.keyring, login, env.app_id, role)  # ty: ignore[invalid-argument-type]


async def tools(env: Env, token: str) -> dict[str, McpTool]:
    return {t.name: t for t in await client(env, token).list_tools()}


async def test_tools_are_exactly_the_allow_list(mcp: Env) -> None:
    listed = await tools(mcp, mcp.admin_key)
    assert set(listed) == {t.name for t in ADMIN_TOOLS}
    # Every listed route exists in the API (a renamed route would silently drop its tool).
    paths = (await mcp.client.get("/agent/openapi.json")).json()["paths"]
    for t in ADMIN_TOOLS:
        assert t.method.lower() in paths.get(f"/agent{t.path}", {}), t


EXCLUDED = (
    "/v1/admin/keys",
    "/v1/admin/capability-credentials",
    "/v1/admin/connections",
    "/v1/admin/channels",
    "/v1/admin/audit",
    "/v1/admin/export",
    "/v1/admin/activity",
    "/v1/admin/application/unlock",
    "/v1/admin/application/reset",
    "/v1/admin/compliance",
    "/v1/admin/knowledge",
    "/v1/admin/workflows",
    "/v1/console",
    "/v1/operator",
    "/v1/privacy",
    "/v1/session",
)


async def test_excluded_routes_never_exposed(mcp: Env) -> None:
    for t in ADMIN_TOOLS:
        assert not t.path.startswith(EXCLUDED), t
        assert not (t.method != "GET" and t.path in {"/v1/admin/security", "/v1/admin/ask-rules"})
        # Contents of conversations and traces stay out: only their lists.
        assert t.path not in {
            "/v1/admin/conversations/{conversation_id}",
            "/v1/admin/traces/{trace_id}",
        }
        assert "sso" not in t.path and "users" not in t.path
    listed = await tools(mcp, mcp.admin_key)
    for name, tool in listed.items():
        props = set(tool.input_schema.get("properties") or {})
        for secret in ("eval_credential", "cost_alert_secret", "cost_alert_webhook", "secret"):
            assert secret not in props, (name, secret)


async def test_get_tools_are_read_only_others_destructive(mcp: Env) -> None:
    listed = await tools(mcp, mcp.admin_key)
    for spec in ADMIN_TOOLS:
        tool = listed[spec.name]
        if spec.method == "GET" or required(spec.method, spec.path) == "viewer":
            # Reads, and the POSTs that change nothing (trying an agent keeps nothing).
            assert tool.read_only is True and not tool.mutating, spec
        else:
            assert tool.destructive is True and tool.mutating, spec


async def test_viewer_lists_only_reads(mcp: Env) -> None:
    listed = await tools(mcp, await delegated(mcp, "viewer"))
    reads = {t.name for t in ADMIN_TOOLS if required(t.method, t.path) == "viewer"}
    assert set(listed) == reads and reads
    operator = await tools(mcp, await delegated(mcp, "operator", sub="op"))
    assert "put_agent" in operator and "update_application" not in operator


async def test_viewer_write_call_is_403(mcp: Env) -> None:
    viewer = client(mcp, await delegated(mcp, "viewer"))
    agent = {"name": "helper", "model": "function:primary", "instructions": "Changed."}
    result = await viewer.call_tool("put_agent", {"name__path": "helper", **agent})
    assert not result.ok and "403" in result.text, result.text
    listed = (await mcp.client.get("/agent/v1/admin/agents", headers=_admin(mcp))).json()
    assert listed["items"][0]["instructions"] != "Changed."


def _admin(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def test_operator_changes_an_agent_and_audit_says_mcp(mcp: Env) -> None:
    current = (await mcp.client.get("/agent/v1/admin/agents", headers=_admin(mcp))).json()
    agent = {**current["items"][0], "instructions": "Answer briefly."}
    agent.pop("version", None)
    operator = client(mcp, await delegated(mcp, "operator"))
    result = await operator.call_tool("put_agent", {"name__path": "helper", **agent})
    assert result.ok, result.text
    owner = client(mcp, mcp.admin_key)
    log = (await mcp.client.get("/agent/v1/admin/audit", headers=_admin(mcp))).json()["items"]
    put = next(e for e in log if e["action"] == "agent.put")
    assert put["actorKind"] == "assistant" and put["actor"] == "assistant:sso:ada@example.com"
    # An admin key through the MCP server is recorded as such, with the key it used.
    result = await owner.call_tool("put_prompt", {"name": "greeting", "text": "Hello."})
    assert result.ok, result.text
    log = (await mcp.client.get("/agent/v1/admin/audit", headers=_admin(mcp))).json()["items"]
    prompt = next(e for e in log if e["action"].startswith("prompt"))
    assert prompt["actorKind"] == "mcp" and prompt["actor"].startswith("mcp:key:"), prompt
    # The same header sent from outside is not believed.
    r = await mcp.client.put(
        "/agent/v1/admin/prompts/other",
        json={"text": "Hi."},
        headers={**_admin(mcp), "X-Evokai-Via": "mcp"},
    )
    assert r.status_code == 200, r.text
    log = (await mcp.client.get("/agent/v1/admin/audit", headers=_admin(mcp))).json()["items"]
    other = next(
        e for e in log if e["action"].startswith("prompt") and e["target"].startswith("other")
    )
    assert other["actorKind"] == "admin_key"


async def test_no_bearer_is_401(mcp: Env) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    r = await mcp.client.post(URL, json=body, headers=headers)
    assert r.status_code == 401
    r = await mcp.client.post(URL, json=body, headers={**headers, "Authorization": "Bearer nope"})
    assert r.status_code == 401
    r = await mcp.client.post(
        URL, json=body, headers={**headers, "Authorization": f"Bearer {mcp.server_key}"}
    )
    assert r.status_code in {401, 403}


async def test_mcp_server_secrets_never_in_tool_output(mcp: Env) -> None:
    secret = "s3cret-value-never-shown"
    r = await mcp.client.put(
        "/agent/v1/admin/mcp-servers/billing",
        json={"url": "http://app.test/mcp", "auth": "secret", "secret": secret},
        headers=_admin(mcp),
    )
    assert r.status_code == 200, r.text
    owner = client(mcp, mcp.admin_key)
    result = await owner.call_tool("list_mcp_servers", {})
    assert result.ok and "billing" in result.text and secret not in result.text
    # Fields outside a tool's allow-list are refused before the route.
    result = await owner.call_tool(
        "put_mcp_server",
        {"name": "billing", "url": "http://app.test/mcp", "auth": "secret", "secret": "x" * 20},
    )
    assert not result.ok and "secret" in result.text
    result = await owner.call_tool("update_application", {"eval_credential": "token-123"})
    assert not result.ok and "eval_credential" in result.text
    app = (await mcp.client.get("/agent/v1/admin/application", headers=_admin(mcp))).json()
    assert app["hasEvalCredential"] is False
    result = await owner.call_tool("update_application", {"name": "Renamed"})
    assert result.ok, result.text


def test_transport_is_mounted_without_a_trailing_slash() -> None:
    assert not URL.endswith("/")
    assert httpx.URL(f"http://evokai{URL}").path == URL
