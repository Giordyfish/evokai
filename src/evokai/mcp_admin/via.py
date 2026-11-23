"""How the admin API knows a request came from this process's admin MCP server."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping

VIA_HEADER = "x-evokai-via"
"""Set by the admin MCP server on the requests it makes to the admin API in process."""
_VIA = f"mcp:{secrets.token_urlsafe(24)}"
"""Its value: random per process, so an outside caller cannot pass for the MCP server in the
audit log by sending the header itself."""


def via_value() -> str:
    """The header value the MCP server sends.

    Returns:
        The value.
    """
    return _VIA


def via_mcp(headers: Mapping[str, str]) -> bool:
    """Whether a request came from this process's admin MCP server.

    Returns:
        True when the header carries this process's value.
    """
    value = headers.get(VIA_HEADER) or ""
    return hmac.compare_digest(value.encode(), _VIA.encode())
