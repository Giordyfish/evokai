"""MCP output schemas: a tool's ``outputSchema`` types its return in Code Mode, and a call's
``structuredContent`` is the result the model and the program read."""

from __future__ import annotations

from typing import Any

import httpx

from evokai.mcp.client import McpClient, output_schema, structured_result
from tests.conftest import ORDERS_MONTH_SCHEMA, Env, FakeMcp, Tool
from tests.test_capabilities import _enable, _returns


def _client(mcp: FakeMcp) -> McpClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(mcp.handler))
    return McpClient(name="app", url="http://app.test/mcp", http=http)


async def test_the_client_keeps_a_tools_output_schema_and_reads_structured_content() -> None:
    mcp = FakeMcp(structured=True)
    client = _client(mcp)
    tools = {t.name: t for t in await client.list_tools()}
    assert tools["orders_month"].output_schema == ORDERS_MONTH_SCHEMA
    assert tools["stock_report"].output_schema is None
    r = await client.call_tool("orders_month", {"period": "2026-10"})
    # The structured content is the result; the text block was only a copy for older clients.
    assert r.ok and r.structured["orders"] == 31
    assert '"orders": 31' in r.text and "31 orders" not in r.text
    # A tool without structured content still answers with its text.
    plain = await client.call_tool("stock_report", {})
    assert plain.structured is None and '"orders": 24' in plain.text


async def test_structured_content_that_breaks_the_schema_is_not_used() -> None:
    client = _client(FakeMcp(structured=True, bad_structured=True))
    await client.list_tools()
    r = await client.call_tool("orders_month", {"period": "2026-10"})
    assert r.structured is None and r.text == "31 orders"


def test_only_a_valid_object_schema_counts_as_an_output_schema() -> None:
    assert output_schema({"name": "x"}) is None
    assert output_schema({"name": "x", "outputSchema": {"type": "string"}}) is None
    assert output_schema({"name": "x", "outputSchema": {"type": "object", "required": 3}}) is None
    good: dict[str, Any] = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert output_schema({"name": "x", "outputSchema": good}) == good
    # An error result's structured content is never taken as the answer.
    assert structured_result({"isError": True, "structuredContent": {}}, None, "x") == (None, None)
    assert structured_result({"structuredContent": {"n": 1}}, None, "x") == ({"n": 1}, '{"n": 1}')


async def test_code_mode_types_a_tool_with_an_output_schema_and_reads_its_structured_result(
    env: Env,
) -> None:
    env.mcp.structured = True
    await _enable(env, "code_mode")
    program = "r = orders_month(period='2026-10')\nr['orders'] + 1"
    env.script.add("function:primary", [Tool("run_code", {"code": program})], ["Fatto."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ottobre?")
    assert ev[-1]["reason"] == "end_turn"
    run_code = env.script.tool_defs["run_code"].description or ""
    signature = next(line for line in run_code.splitlines() if "def orders_month" in line)
    other = next(line for line in run_code.splitlines() if "def stock_report" in line)
    # The tool with an output schema is typed; the one without still shows Any.
    assert "-> Any" not in signature and "-> Any" in other
    # The return schema stays out of the tool's own definition sent to the model.
    assert env.script.tool_defs["orders_month"].return_schema is None
    # The program indexed the structured result (31), not the text block.
    _, second = env.script.seen[-1]
    assert "32" in _returns(second)[-1]
