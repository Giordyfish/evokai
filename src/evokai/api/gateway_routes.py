"""The gateways' doors: each provider's own API, forwarded unchanged after the gateway's controls.

- ``POST {base}/gw/anthropic/v1/messages`` and ``.../v1/messages/count_tokens``: the Anthropic
  Messages API, for Claude Code (``ANTHROPIC_BASE_URL={base}/gw/anthropic``).

The person's gateway key comes in ``x-evokai-key``; every other header, the query and the body go
to the provider as they came (``evokai.gateway.relay``). Errors are the provider's own shape. These
routes are not in the OpenAPI document: their contract is the provider's API (``docs/gateway.md``).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from evokai.gateway.checks import ANTHROPIC

router = APIRouter(prefix="/gw", include_in_schema=False)


@router.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request) -> Response:
    """Claude Code's model calls, to the Anthropic Messages API.

    Returns:
        The provider's answer, streamed, or the gateway's refusal.
    """
    return await request.app.state.services.gateway.relay(request, ANTHROPIC, "/v1/messages")


@router.post("/anthropic/v1/messages/count_tokens")
async def anthropic_count_tokens(request: Request) -> Response:
    """Claude Code's token counts (``/context``), to the Anthropic Messages API.

    Returns:
        The provider's answer, or the gateway's refusal.
    """
    return await request.app.state.services.gateway.relay(
        request, ANTHROPIC, "/v1/messages/count_tokens"
    )
