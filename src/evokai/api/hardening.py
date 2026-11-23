"""HTTP hardening of every response: security headers, CORS for browser sessions, body limits.

One pure ASGI middleware (it never buffers a streamed answer):

- **headers** on every response: ``X-Content-Type-Options: nosniff``, a ``Referrer-Policy``, no
  framing (``X-Frame-Options: DENY`` and a CSP ``frame-ancestors 'none'``; API responses get
  ``default-src 'none'``, the console keeps its own policy), ``Strict-Transport-Security`` when the
  request came over HTTPS (directly or through a proxy saying so), ``Cache-Control: no-store`` on
  API answers that do not set one (tokens, conversations);
- **CORS** on the browser session API (``/v1/session/*``) only: the origins every application
  allows, never with cookies (no ``Access-Control-Allow-Credentials``: session tokens travel in
  ``Authorization``); each application's own origin list is still checked on every request
  (``api.deps.session``);
- **body size**: requests larger than the limit get 413 before the route reads them (the session
  API takes less than the server API, whose runs carry attachments, except a chat message, which
  may carry the files a person attaches: ``EVOKAI_ATTACHMENTS_REQUEST_MB``).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from evokai.core.util import TtlCache

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
HSTS = "max-age=31536000; includeSubDomains"
SESSION_BODY_LIMIT = 2_000_000
"""Bytes: a chat message with its page context and an external history (at most 400,000 characters
are kept of it)."""
BODY_LIMIT = 8_000_000
"""Bytes: a server run with its attachments, an agent definition, a skill."""
CHAT_EXTRA = 2_000_000
"""Bytes a chat message may carry beside its attachments (text, page context, external history)."""
UPLOAD_LIMIT = 200_000_000
"""Bytes: a knowledge document upload (the service applies ``EVOKAI_KNOWLEDGE_MAX_FILE_MB``)."""
CORS_HEADERS = "authorization, content-type, accept, last-event-id, x-correlation-id"
CORS_EXPOSE = "x-turn-id, x-correlation-id"
CORS_METHODS = "GET, POST, PATCH, DELETE, OPTIONS"

OriginsFn = Callable[[], Awaitable[set[str]]]


class Hardening:
    """Security headers, CORS for the session API and body limits."""

    def __init__(
        self, app: ASGIApp, *, base_path: str, origins: OriginsFn, attachments_mb: int = 0
    ) -> None:
        """Wrap the application.

        Args:
            app: The wrapped ASGI app.
            base_path: Where the API is mounted (``/agent``).
            origins: The browser origins every application allows (cached by the caller).
            attachments_mb: The files one chat message may carry
                (``EVOKAI_ATTACHMENTS_REQUEST_MB``): the body limit of ``POST /v1/session/chat``
                grows by them, base64 included.
        """
        self.app = app
        self.chat_limit = max(SESSION_BODY_LIMIT, attachments_mb * 1_000_000 * 4 // 3 + CHAT_EXTRA)
        self.base = base_path.rstrip("/")
        self.origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle a request."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        session_api = path == f"{self.base}/v1/session" or path.startswith(
            f"{self.base}/v1/session/"
        )
        console = path.startswith(f"{self.base}/admin")
        https = scope.get("scheme") == "https" or headers.get("x-forwarded-proto", "") == "https"
        origin = headers.get("origin")
        cors_origin = origin if session_api and origin and origin in await self.origins() else None

        if session_api and scope.get("method") == "OPTIONS":
            await self._preflight(send, cors_origin, https)
            return

        limit = SESSION_BODY_LIMIT if session_api else BODY_LIMIT
        if path == f"{self.base}/v1/session/chat":
            limit = self.chat_limit
        if path.startswith(f"{self.base}/v1/admin/knowledge/") and path.endswith("/documents"):
            limit = UPLOAD_LIMIT
        length = headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            await _too_large(send, https)
            return
        received = {"n": 0, "over": False, "answered": False}

        async def counted() -> Message:
            if received["over"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                received["n"] += len(message.get("body", b""))
                if received["n"] > limit:
                    # End the body here: whatever the route answers, the client gets 413.
                    received["over"] = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def send_hardened(message: Message) -> None:
            if received["over"]:
                if not received["answered"]:
                    received["answered"] = True
                    await _too_large(send, https)
                return
            if message["type"] == "http.response.start":
                h = list(message.get("headers", []))
                names = {k.lower() for k, _ in h}
                for k, v in _security_headers(console, https, cors_origin):
                    if k not in names:
                        h.append((k, v))
                if not console and b"cache-control" not in names:
                    h.append((b"cache-control", b"no-store"))
                message["headers"] = h
            await send(message)

        await self.app(scope, counted, send_hardened)

    async def _preflight(self, send: Send, origin: str | None, https: bool) -> None:
        headers = list(_security_headers(False, https, None))
        if origin:
            headers += [
                (b"access-control-allow-origin", origin.encode()),
                (b"access-control-allow-methods", CORS_METHODS.encode()),
                (b"access-control-allow-headers", CORS_HEADERS.encode()),
                (b"access-control-max-age", b"600"),
                (b"vary", b"Origin"),
            ]
        await send({
            "type": "http.response.start",
            "status": 204 if origin else 403,
            "headers": headers,
        })
        await send({"type": "http.response.body", "body": b""})


def _security_headers(
    console: bool, https: bool, cors_origin: str | None
) -> list[tuple[bytes, bytes]]:
    out = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"same-origin" if console else b"no-referrer"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]
    if not console:
        out.append((b"content-security-policy", API_CSP.encode()))
    if https:
        out.append((b"strict-transport-security", HSTS.encode()))
    if cors_origin:
        out += [
            (b"access-control-allow-origin", cors_origin.encode()),
            (b"access-control-expose-headers", CORS_EXPOSE.encode()),
            (b"vary", b"Origin"),
        ]
    return out


async def _too_large(send: Send, https: bool) -> None:
    body = json.dumps({"error": "too_large", "message": "Request too large."}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        *_security_headers(False, https, None),
    ]
    await send({"type": "http.response.start", "status": 413, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def origin_cache(store: Any) -> OriginsFn:
    """The browser origins of every application, read at most every 30 seconds.

    Returns:
        An async function returning the set of origins.
    """
    cache: TtlCache[str, set[str]] = TtlCache(1, 30)

    async def origins() -> set[str]:
        hit = cache.get("all")
        if hit is None:
            hit = {
                o.rstrip("/")
                for a in await store.applications.all()
                if a.status == "active"
                for o in a.allowed_origins or []
            }
            cache.set("all", hit)
        return hit

    return origins
