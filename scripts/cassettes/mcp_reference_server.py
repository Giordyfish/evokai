"""A small MCP server on the reference SDK, to record Evokai's MCP cassettes against.

Not part of Evokai: it runs only while recording (``scripts/record-cassettes.sh``), with the SDK
fetched for the occasion:

    uv run --no-project --with "mcp>=1.20,<2" python scripts/cassettes/mcp_reference_server.py --port 8765

Streamable HTTP at ``http://127.0.0.1:<port>/mcp``: one read-only tool and one that changes data,
answering with fixed values, so a cassette holds what a real server sends and nothing private.
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


def main() -> None:
    """Serve until stopped."""
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    port = p.parse_args().port
    server = FastMCP("evokai-cassettes", host="127.0.0.1", port=port)

    @server.tool(
        description="Orders of a month (YYYY-MM).",
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    def orders_month(period: str) -> dict[str, object]:
        return {"period": period, "orders": 24, "open": 3}

    @server.tool(
        description="Refund the orders of a month.",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
    def order_refund(period: str, reason: str) -> str:
        return f"{period}: refunded as {reason}"

    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
