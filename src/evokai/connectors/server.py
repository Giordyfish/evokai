"""One connector as a standalone MCP server: ``evokai connectors serve``.

Streamable HTTP with JSON answers. For another agent runtime, or to try a connector's configuration
before registering it. Inside Evokai connectors run in process and need none of this. With
``--token-env`` every request must carry ``Authorization: Bearer <that variable's value>``.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from evokai.connectors.base import Connector
from evokai.mcp.client import PROTOCOL_VERSION
from evokai.version import __version__


def _tool(t: Any) -> dict[str, Any]:
    out = {
        "name": t.name,
        "description": t.description,
        "inputSchema": t.input_schema,
        "annotations": {"readOnlyHint": bool(t.read_only), "destructiveHint": bool(t.destructive)},
    }
    if getattr(t, "output_schema", None):
        out["outputSchema"] = t.output_schema
    return out


def mcp_app(connector: Connector, token: str | None = None, path: str = "/mcp") -> FastAPI:
    """An MCP server for one connector.

    Args:
        connector: The connector.
        token: A bearer token every request must carry (None: no check, for a private network).
        path: Where the endpoint is.

    Returns:
        The app.
    """
    app = FastAPI(
        title=f"Evokai connector {connector.name}", docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.post(path)
    async def rpc(request: Request) -> Response:
        if token is not None:
            given = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(given.encode(), token.encode()):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        if "id" not in body:
            return Response(status_code=202)
        rid, method, params = body["id"], body.get("method"), body.get("params") or {}

        def ok(result: dict[str, Any]) -> JSONResponse:
            return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})

        if method == "initialize":
            return ok({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": f"evokai-{connector.kind}", "version": __version__},
            })
        if method == "tools/list":
            return ok({"tools": [_tool(t) for t in await connector.list_tools()]})
        if method == "tools/call":
            r = await connector.call_tool(str(params.get("name")), params.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": r.text}], "isError": not r.ok})
        if method == "ping":
            return ok({})
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32601, "message": "method not found"},
        })

    return app
