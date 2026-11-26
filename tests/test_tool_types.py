"""TypeScript types of an agent's tools (``evokai.tools.typescript``, ``evokai types``)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from evokai import cli
from evokai.tools.typescript import ToolTypes, declarations, to_typescript, type_name
from tests.conftest import ORDERS_MONTH_SCHEMA, Env


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


def test_json_schema_becomes_typescript() -> None:
    assert to_typescript({"type": "string"}) == "string"
    assert to_typescript({"type": "integer"}) == "number"
    assert to_typescript({"type": ["string", "null"]}) == "string | null"
    assert to_typescript({"enum": ["a", "b", 3]}) == '"a" | "b" | 3'
    assert to_typescript({"const": True}) == "true"
    assert to_typescript({
        "type": "array",
        "items": {"anyOf": [{"type": "string"}, {"type": "number"}]},
    }) == ("(string | number)[]")
    assert to_typescript({
        "type": "array",
        "prefixItems": [{"type": "string"}, {"type": "number"}],
    }) == ("[string, number]")
    assert to_typescript({"type": "object"}) == "Record<string, unknown>"
    assert to_typescript({"type": "object", "additionalProperties": {"type": "number"}}) == (
        "Record<string, number>"
    )
    assert to_typescript({"type": "string", "nullable": True}) == "string | null"
    assert to_typescript({"allOf": [{"type": "object", "properties": {"a": {"type": "string"}}}]})
    assert to_typescript({"not": {}}) == "unknown"  # never narrower than the schema
    obj = {
        "type": "object",
        "properties": {
            "period": {"type": "string", "description": "A month, YYYY-MM */ closed"},
            "with-dash": {"type": "boolean"},
        },
        "required": ["period"],
    }
    assert to_typescript(obj) == (
        '{\n  /** A month, YYYY-MM *\\/ closed */\n  period: string\n  "with-dash"?: boolean\n}'
    )


def test_local_refs_are_inlined_and_a_recursive_one_is_unknown() -> None:
    schema = {
        "type": "object",
        "properties": {"line": {"$ref": "#/$defs/Line"}, "tree": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Line": {"type": "object", "properties": {"sku": {"type": "string"}}},
            "Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}},
        },
    }
    out = to_typescript(schema)
    assert "sku?: string" in out and "child?: unknown" in out
    assert to_typescript({"$ref": "https://example.com/x.json"}) == "unknown"


def test_type_names_and_maps() -> None:
    assert type_name("orders_month", "Args") == "OrdersMonthArgs"
    assert type_name("crm.find-lead", "Result") == "CrmFindLeadResult"
    assert type_name("3d_view", "Args") == "T3dViewArgs"
    text = declarations(
        "helper",
        "abc",
        [ToolTypes("orders_month", "Orders", {"type": "object"}, None, False)],
        [
            ToolTypes(
                "kiosk_light",
                "Light",
                {"type": "object", "properties": {"on": {"type": "boolean"}}},
            )
        ],
        {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
        ["crm"],
    )
    assert "export type OrdersMonthArgs = Record<string, unknown>" in text
    assert "export type OrdersMonthResult = unknown" in text
    assert "export interface KioskLightArgs {\n  on?: boolean\n}" in text
    assert "  orders_month: OrdersMonthArgs\n" in text and "  kiosk_light: KioskLightArgs\n" in text
    assert "export interface Output {\n  n: number\n}" in text
    assert "Not listed (their tools are missing): crm." in text
    assert "\u2014" not in text


async def test_the_admin_endpoint_types_the_agents_tools(env: Env) -> None:
    env.mcp.structured = True
    r = await env.client.get("/agent/v1/admin/agents", headers=_adm(env))
    helper = next(a for a in r.json()["items"] if a["name"] == "helper")
    helper.pop("version", None)
    helper["client_tools"] = {
        **helper["client_tools"],
        "custom": [
            {
                "name": "kiosk_light",
                "description": "Turn the light on.",
                "parameters": {"type": "object", "properties": {"on": {"type": "boolean"}}},
            }
        ],
    }
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
    assert r.status_code == 200, r.text
    r = await env.client.get("/agent/v1/admin/agents/helper/types", headers=_adm(env))
    assert r.status_code == 200, r.text
    # The fake server forwards the person's credential: without one it is not listed.
    assert "Not listed (their tools are missing): app." in r.text
    assert "OrdersMonthArgs" not in r.text and "  kiosk_light: KioskLightArgs" in r.text
    r = await env.client.get(
        "/agent/v1/admin/agents/helper/types",
        headers={**_adm(env), "X-Evokai-Credential": "user-token"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert "user-token" in env.mcp.auth[-1]  # listed as that person, for this call only
    assert "export interface OrdersMonthArgs {\n  period?: string\n}" in text
    # The declared outputSchema types the result.
    assert "export interface OrdersMonthResult {" in text and "  orders: number" in text
    assert ORDERS_MONTH_SCHEMA["required"] == ["tool", "orders"]
    assert "export type StockReportResult = unknown" in text
    assert "Changes data: waits for the person's yes." in text  # order_refund
    assert "  kiosk_light: KioskLightArgs" in text
    assert "export interface Output" not in text
    r = await env.client.get("/agent/v1/admin/agents/nobody/types", headers=_adm(env))
    assert r.status_code == 404


def test_the_cli_writes_what_the_endpoint_returns(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: dict[str, str], timeout: float) -> httpx.Response:
        seen.update(url=url, headers=headers, timeout=timeout)
        return httpx.Response(200, text="export interface ToolArgs {}\n")

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setenv("EVOKAI_ADMIN_KEY", "adm_x")
    out = tmp_path / "tools.d.ts"
    code = cli.main([
        "types",
        "--agent",
        "help desk",
        "--out",
        str(out),
        "--url",
        "https://e.test/agent/",
    ])
    assert code == 0 and out.read_text() == "export interface ToolArgs {}\n"
    assert seen["url"] == "https://e.test/agent/v1/admin/agents/help%20desk/types"
    assert seen["headers"] == {"Authorization": "Bearer adm_x"}
    monkeypatch.delenv("EVOKAI_ADMIN_KEY")
    assert cli.main(["types", "--agent", "x"]) == 2
