"""Pass-through to the provider: headers in and out, the body and the stream byte for byte.

What is dropped on the way up: the hop-by-hop headers (``connection``, ``keep-alive``,
``transfer-encoding``, ``te``, ``trailer``, ``upgrade``, ``proxy-*``), ``host`` and
``content-length`` (recomputed for the same bytes), and ``x-evokai-key``. Everything else goes as it
came, ``authorization`` (the person's own login) and every ``anthropic-*`` header included.

On the way down: the provider's status, headers (but the hop-by-hop ones, and ``date`` and
``server``, which this server sets itself) and body, streamed as each chunk arrives, without
decoding it (``content-encoding`` stays as the provider sent it). The tokens are read from a copy of
the stream as it passes: nothing is buffered before the person's tool gets it.

A person's tool that goes away cancels the request to the provider at once.
"""

from __future__ import annotations

import json
import logging
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
import httpx
from starlette.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

log = logging.getLogger("evokai.gateway")

HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
})
KEY_HEADER = "x-evokai-key"
"""The header that carries a person's gateway key; it never reaches the provider."""
REQUEST_DROP = HOP_BY_HOP | {"host", "content-length", KEY_HEADER}
RESPONSE_DROP = HOP_BY_HOP | {"date", "server"}
_HTTPX_DEFAULTS = ("accept", "accept-encoding", "user-agent")
"""Headers httpx adds on its own: removed again when the tool did not send them."""
MAX_JSON_USAGE = 4_000_000
"""Bytes of a JSON (not streamed) answer read for its usage; a longer one is not parsed."""


def upstream_headers(raw: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """The request headers that go to the provider, in their order, duplicates kept.

    Returns:
        (name, value) pairs.
    """
    return [(k, v) for k, v in raw if k.decode("latin-1").lower() not in REQUEST_DROP]


def downstream_headers(raw: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """The provider's response headers that go back to the tool.

    Returns:
        (name, value) pairs (names lower-case, as ASGI wants them).
    """
    out = []
    for k, v in raw:
        name = k.decode("latin-1").lower()
        if name not in RESPONSE_DROP:
            out.append((name.encode("latin-1"), v))
    return out


def build_request(
    client: httpx.AsyncClient,
    url: str,
    raw: list[tuple[bytes, bytes]],
    body: bytes,
    method: str = "POST",
) -> httpx.Request:
    """The request to the provider: the tool's headers (but the dropped ones) and its bytes.

    Returns:
        The request.
    """
    content = body if method == "POST" else None
    req = client.build_request(method, url, headers=upstream_headers(raw), content=content)
    sent = {k.decode("latin-1").lower() for k, _ in raw}
    for name in _HTTPX_DEFAULTS:
        if name not in sent and name in req.headers:
            del req.headers[name]
    return req


# Usage, read from the answer as it passes


class Usage:
    """Tokens read from an answer: the SSE events or the JSON body, never kept otherwise."""

    def __init__(self, provider: str, content_type: str, encoding: str = "") -> None:
        """Start reading.

        Args:
            provider: ``anthropic`` or ``openai`` (which events carry the usage).
            content_type: The answer's content type (none: told from its first bytes, as
                ChatGPT's Codex backend streams without one).
            encoding: Its ``content-encoding``: a copy of a ``gzip`` or ``deflate`` answer is
                decompressed to read it (the tool still gets the provider's bytes); the usage of
                another encoding is not read.
        """
        self.provider = provider
        self.sse = content_type.startswith("text/event-stream")
        self.json = content_type.startswith("application/json")
        encoding = encoding.strip().lower()
        self._inflate: Any = None
        if encoding in ("gzip", "x-gzip"):
            self._inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif encoding == "deflate":
            self._inflate = zlib.decompressobj()
        self._sniff = not content_type.strip()
        self.off = (encoding not in ("", "identity") and self._inflate is None) or not (
            self.sse or self.json or self._sniff
        )
        self._buf = b""
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_write = 0
        self.bytes = 0
        self.complete = False
        """The answer's last event passed (``message_stop``, ``response.completed``): a tool
        that hangs up after it got a whole answer."""

    def feed(self, chunk: bytes) -> None:
        """Read a chunk."""
        self.bytes += len(chunk)
        if self.off:
            return
        if self._inflate is not None:
            try:
                chunk = self._inflate.decompress(chunk, MAX_JSON_USAGE)
            except zlib.error:
                self.off = True
                return
        if self._sniff:
            head = (self._buf + chunk).lstrip()
            if not head:
                self._buf += chunk
                return
            self._sniff = False
            self.sse = head.startswith((b"event:", b"data:", b":", b"id:", b"retry:"))
            self.json = head.startswith(b"{")
            self.off = not (self.sse or self.json)
            chunk, self._buf = self._buf + chunk, b""
            if self.off:
                return
        if self.json:
            if len(self._buf) + len(chunk) > MAX_JSON_USAGE:
                self.off = True
                self._buf = b""
                return
            self._buf += chunk
            return
        self._buf += chunk
        while True:
            cut = _event_end(self._buf)
            if cut is None:
                break
            event, self._buf = self._buf[: cut[0]], self._buf[cut[1] :]
            if b'"usage"' in event or b"message_stop" in event:
                self._event(event)
        if len(self._buf) > MAX_JSON_USAGE:
            # An event this long carries text, not usage: what is left of it is skipped.
            self._buf = b""

    def finish(self) -> None:
        """The answer ended: read a JSON body, or the last SSE event without its blank line."""
        if self.off:
            return
        if self.json and self._buf:
            try:
                data = json.loads(self._buf)
            except ValueError:
                return
            if isinstance(data, dict):
                self._usage(data.get("usage"))
                if isinstance(data.get("response"), dict):
                    self._usage(data["response"].get("usage"))
        elif self._buf and b'"usage"' in self._buf:
            self._event(self._buf)
        self._buf = b""

    def _event(self, event: bytes) -> None:
        data = b"\n".join(
            line[5:].lstrip() for line in event.splitlines() if line.startswith(b"data:")
        )
        try:
            payload = json.loads(data)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        kind = payload.get("type")
        if kind == "message_start" and isinstance(payload.get("message"), dict):
            self._usage(payload["message"].get("usage"))
        elif kind == "message_delta":
            self._usage(payload.get("usage"))
        elif kind == "message_stop":
            self.complete = True
        elif kind == "response.completed" and isinstance(payload.get("response"), dict):
            self._usage(payload["response"].get("usage"))
            self.complete = True

    def _usage(self, u: Any) -> None:
        """Take the counts a usage object states (later events state the running totals)."""
        if not isinstance(u, dict):
            return

        def num(v: Any) -> int | None:
            return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else None

        if self.provider == "openai":
            details = u.get("input_tokens_details")
            cached = num(details.get("cached_tokens")) if isinstance(details, dict) else None
            if (v := num(u.get("input_tokens"))) is not None:
                self.input_tokens = v
            if (v := num(u.get("output_tokens"))) is not None:
                self.output_tokens = v
            if cached is not None:
                self.cache_read = cached
            return
        # Anthropic: input_tokens excludes the cache; the record counts every input token.
        plain = num(u.get("input_tokens"))
        read = num(u.get("cache_read_input_tokens"))
        write = num(u.get("cache_creation_input_tokens"))
        if read is not None:
            self.cache_read = read
        if write is not None:
            self.cache_write = write
        if plain is not None:
            self.input_tokens = plain + self.cache_read + self.cache_write
        elif read is not None or write is not None:
            self.input_tokens = max(self.input_tokens, self.cache_read + self.cache_write)
        if (v := num(u.get("output_tokens"))) is not None:
            self.output_tokens = v


def _event_end(buf: bytes) -> tuple[int, int] | None:
    """Where the first SSE event of a buffer ends (a blank line), and where the next starts.

    Returns:
        (end of the event, start of the next), or None when no event is complete.
    """
    at = [(i, n) for i, n in ((buf.find(b"\n\n"), 2), (buf.find(b"\r\n\r\n"), 4)) if i >= 0]
    if not at:
        return None
    i, n = min(at)
    return i, i + n


# The answer


class Relay(Response):
    """Streams the provider's answer to the tool as it arrives, and cancels it if the tool goes."""

    def __init__(
        self,
        upstream: httpx.Response,
        usage: Usage,
        done: Callable[[str], Awaitable[None]],
    ) -> None:
        """Hold the provider's answer.

        Args:
            upstream: The provider's streamed answer (its headers already read).
            usage: Reads the tokens from a copy of each chunk.
            done: Called once with the outcome (``forwarded``, ``upstream_error``, ``failed``,
                ``cancelled``) when the answer has ended, whatever happened.
        """
        self.upstream = upstream
        self.usage = usage
        self.done = done
        self.status_code = upstream.status_code
        self.background = None
        self.raw_headers = downstream_headers(upstream.headers.raw)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # ruff: ignore[unused-method-argument]
        """Stream the answer."""
        outcome = "cancelled"
        try:
            async with anyio.create_task_group() as tg:

                async def watch() -> None:
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            tg.cancel_scope.cancel()
                            return

                tg.start_soon(watch)
                outcome = await self._stream(send)
                tg.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()
                self.usage.finish()
                if outcome == "cancelled" and self.usage.complete:
                    outcome = "forwarded"  # the tool hung up after the answer's last event
                try:
                    await self.done(outcome)
                except Exception as e:
                    log.warning("gateway_not_recorded", extra={"error_type": type(e).__name__})

    async def _stream(self, send: Send) -> str:
        """Send the provider's answer as it arrives.

        Returns:
            ``forwarded`` or ``upstream_error`` once it ended cleanly; ``failed`` when the
            provider's stream broke and ``cancelled`` when the tool went away: the tool's
            connection then ends without a clean end, so it knows the answer is incomplete.
        """
        try:
            await send({
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            })
            async for chunk in self.upstream.aiter_raw():
                self.usage.feed(chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except (httpx.HTTPError, OSError) as e:
            log.info("gateway_stream_ended", extra={"error_type": type(e).__name__})
            return "failed" if isinstance(e, httpx.HTTPError) else "cancelled"
        return "forwarded" if self.status_code < 400 else "upstream_error"


def anthropic_error(status: int, kind: str, message: str, **headers: str) -> JSONResponse:
    """An error in Anthropic's shape, which Claude Code shows the person.

    Returns:
        The response.
    """
    return JSONResponse(
        {"type": "error", "error": {"type": f"{kind}_error", "message": message}},
        status_code=status,
        headers=headers or None,
    )


def openai_error(
    status: int, kind: str, message: str, code: str | None = None, **headers: str
) -> JSONResponse:
    """An error in OpenAI's shape, which Codex shows the person.

    Returns:
        The response.
    """
    return JSONResponse(
        {"error": {"message": message, "type": f"{kind}_error", "code": code, "param": None}},
        status_code=status,
        headers=headers or None,
    )


def elapsed_ms(t0: float) -> int:
    """Milliseconds since a ``perf_counter`` reading.

    Returns:
        The time.
    """
    return int((time.perf_counter() - t0) * 1000)
