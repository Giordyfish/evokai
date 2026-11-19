"""Correlation id per request and one log line per request.

The id comes from ``x-correlation-id`` when it looks sane, else it is generated; it is echoed in the
response. A W3C ``traceparent`` is attached to the tracing context when tracing is on.
"""

from __future__ import annotations

import re
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from evokai.observability.correlation import correlation_id_context, generate_correlation_id
from evokai.observability.logging import logger

CORRELATION_HEADER = "x-correlation-id"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class CorrelationMiddleware:
    """Pure ASGI middleware (does not buffer streaming responses)."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap an ASGI app.

        Args:
            app: The wrapped application.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle a request or a socket."""
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get(CORRELATION_HEADER, "")
        cid = incoming if _SAFE.match(incoming) else generate_correlation_id()
        token = correlation_id_context.set(cid)
        otel_token = _attach_trace(headers)
        started = time.perf_counter()
        status = {"code": 0}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((
                    CORRELATION_HEADER.encode(),
                    cid.encode(),
                ))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            if not path.endswith("/health"):
                logger.info(
                    "request",
                    extra={
                        "method": scope.get("method", "WS"),
                        "path": path,
                        "status": status["code"],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    },
                )
            _detach_trace(otel_token)
            correlation_id_context.reset(token)


def _attach_trace(headers: dict[str, str]) -> object | None:
    if "traceparent" not in headers:
        return None
    try:
        from opentelemetry import context
        from opentelemetry.propagate import extract

        return context.attach(extract(carrier=headers))
    except ImportError:
        return None


def _detach_trace(token: object | None) -> None:
    if token is None:
        return
    from opentelemetry import context

    context.detach(token)  # ty: ignore[invalid-argument-type]
