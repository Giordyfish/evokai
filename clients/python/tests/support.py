from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

import httpx
import respx

BASE = "http://evokai.test/agent"
SERVER_KEY = "evk_srv_test_fake"
ADMIN_KEY = "evk_adm_test_fake"
TOKEN = "session-token-fake"


class Chunks(httpx.SyncByteStream, httpx.AsyncByteStream):
    """A response body delivered in the given chunks, optionally breaking after them."""

    def __init__(self, chunks: Iterable[bytes | str], *, fail: Exception | None = None) -> None:
        self.chunks = [c.encode() if isinstance(c, str) else c for c in chunks]
        self.fail = fail
        self.closed = False
        self.sent = 0

    def __iter__(self) -> Iterator[bytes]:
        for c in self.chunks:
            self.sent += 1
            yield c
        if self.fail is not None:
            raise self.fail

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for c in self.chunks:
            self.sent += 1
            yield c
        if self.fail is not None:
            raise self.fail

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


def frame(event: dict[str, Any], *, name: bool = True) -> str:
    head = f"event: {event['type']}\n" if name else ""
    return f"{head}data: {json.dumps(event)}\n\n"


def sse_response(
    chunks: Iterable[bytes | str], *, turn_id: str | None = "t1", fail: Exception | None = None
) -> tuple[httpx.Response, Chunks]:
    body = Chunks(chunks, fail=fail)
    headers = {"content-type": "text/event-stream; charset=utf-8"}
    if turn_id:
        headers["x-turn-id"] = turn_id
    return httpx.Response(200, headers=headers, stream=body), body


def sent_json(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)
