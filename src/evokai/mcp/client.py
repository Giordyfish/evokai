"""A small, strict MCP client for the Streamable-HTTP transport.

JSON-RPC over POST; the server may answer with application/json or with a text/event-stream carrying
the response. Session ids (Mcp-Session-Id) are kept per client. One client per (server, caller): it
acts with the caller's token.
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
import jsonschema

from evokai.observability.logging import provider_operation
from evokai.version import __version__

PROTOCOL_VERSION = "2025-06-18"

log = logging.getLogger("evokai.mcp")


class McpError(Exception):
    """The server refused or failed a request."""


@dataclass(frozen=True)
class McpTool:
    """A tool as the server lists it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool | None = None
    destructive: bool | None = None
    server: str = ""
    output_schema: dict[str, Any] | None = None
    """The JSON Schema of the tool's ``structuredContent`` (MCP ``outputSchema``), when the server
    declares one: a Code Mode program then sees the tool's return type instead of ``Any``."""

    @property
    def mutating(self) -> bool:
        """Whether the tool changes data (it then needs confirmation).

        Returns:
            True for destructive or non read-only tools.
        """
        return bool(self.destructive) or self.read_only is False


@dataclass
class ToolResult:
    """What a tool returned, as text for the model."""

    ok: bool
    text: str
    connect: dict[str, Any] | None = None
    """A delegated connector's tool found no connection for the person: which provider to
    connect (``{"provider", "title", "server"}``); the turn shows a card to connect it."""
    structured: Any = None
    """The result's ``structuredContent``, when the server sent one (``text`` is then its JSON)."""


def output_schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    """A listed tool's ``outputSchema``, when it is a usable JSON Schema of an object.

    Returns:
        The schema, or None (absent, not an object schema, or not a valid schema).
    """
    schema = tool.get("outputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        log.warning("mcp_output_schema_invalid", extra={"tool": tool.get("name")})
        return None
    return schema


def structured_result(
    result: dict[str, Any], schema: dict[str, Any] | None, tool: str
) -> tuple[Any, str | None]:
    """A call's ``structuredContent`` and its JSON text, checked against the tool's schema.

    A server that declares an ``outputSchema`` must send conforming structured content (MCP
    2025-06-18); content that does not conform is dropped and the call's text blocks are used.

    Returns:
        ``(structured, text)``, both None when there is no usable structured content.
    """
    structured = result.get("structuredContent")
    if structured is None or result.get("isError"):
        return None, None
    if schema is not None:
        try:
            jsonschema.validate(structured, schema)
        except jsonschema.ValidationError:
            log.warning("mcp_structured_content_invalid", extra={"tool": tool})
            return None, None
    return structured, json.dumps(structured, ensure_ascii=False)


@dataclass
class McpClient:
    """A client bound to one server and one credential."""

    name: str
    url: str
    http: httpx.AsyncClient
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 60.0
    _ids: Any = field(default_factory=lambda: itertools.count(1))
    _session: str | None = None
    _initialized: bool = False
    _schemas: dict[str, dict[str, Any] | None] = field(default_factory=dict)

    async def _rpc(
        self, method: str, params: dict[str, Any] | None = None, *, notify: bool = False
    ) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            body["id"] = next(self._ids)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **self.headers,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        res = await self.http.post(self.url, json=body, headers=headers, timeout=self.timeout)
        if sid := res.headers.get("mcp-session-id"):
            self._session = sid
        if res.status_code in (401, 403):
            raise McpError(f"{method}: forbidden ({res.status_code})")
        if notify:
            return None
        if res.status_code >= 400:
            raise McpError(f"{method}: HTTP {res.status_code}")
        payload = _parse(res)
        if payload.get("error"):
            raise McpError(f"{method}: {payload['error'].get('message', 'error')}")
        return payload.get("result")

    async def initialize(self) -> None:
        """Handshake once per client."""
        if self._initialized:
            return
        await self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "evokai", "version": __version__},
            },
        )
        await self._rpc("notifications/initialized", notify=True)
        self._initialized = True

    async def list_tools(self) -> list[McpTool]:
        """The tools this credential may use.

        Returns:
            Tools.
        """
        with provider_operation("mcp", "tools/list", server=self.name) as op:
            await self.initialize()
            result = await self._rpc("tools/list") or {}
            tools = [
                McpTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                    read_only=(t.get("annotations") or {}).get("readOnlyHint"),
                    destructive=(t.get("annotations") or {}).get("destructiveHint"),
                    server=self.name,
                    output_schema=output_schema(t),
                )
                for t in result.get("tools", [])
            ]
            self._schemas = {t.name: t.output_schema for t in tools}
            op["tools"] = len(tools)
            return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = None
    ) -> ToolResult:
        """Call a tool.

        Args:
            name: The server's tool name.
            arguments: Arguments.
            meta: The call's ``_meta`` (the conversation, for a server holding state per
                conversation such as a capability worker).

        Returns:
            Its result.
        """
        with provider_operation("mcp", "tools/call", server=self.name, tool=name) as op:
            await self.initialize()
            params: dict[str, Any] = {"name": name, "arguments": arguments}
            if meta:
                params["_meta"] = meta
            result = await self._rpc("tools/call", params) or {}
            ok = not result.get("isError")
            op["ok"] = ok
            # Structured content is the result itself when present (the text blocks are, by the
            # specification, only its serialised copy for older clients).
            structured, text = structured_result(result, self._schemas.get(name), name)
            if text is None:
                text = "\n".join(
                    c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)
                )
            else:
                op["structured"] = True
            return ToolResult(ok=ok, text=text, structured=structured)


def _parse(res: httpx.Response) -> dict[str, Any]:
    ctype = res.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        last: dict[str, Any] = {}
        for block in res.text.split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            )
            if data:
                msg = json.loads(data)
                if "result" in msg or "error" in msg:
                    last = msg
        return last
    return res.json() if res.content else {}
