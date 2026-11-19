"""A server-sent events parser that does not care where the network cut the bytes.

Chunks may split a line, a field, a CRLF pair or a multi-byte character. It follows the WHATWG
event-stream rules: comments (``: ping``), ``event:``, multi-line ``data:``, ``id:``, ``retry:``,
one optional space after the colon, and CR, LF or CRLF line ends.
"""

from __future__ import annotations

import codecs
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from dataclasses import dataclass

__all__ = ["SseMessage", "SseParser", "aiter_sse", "iter_sse"]


@dataclass(frozen=True)
class SseMessage:
    """One dispatched message.

    Attributes:
        event: The ``event:`` field, or ``message`` when absent.
        data: The ``data:`` lines joined with newlines.
        id: The last event id seen, if any.
        retry: The last reconnection time (milliseconds) seen, if any.
    """

    event: str
    data: str
    id: str | None = None
    retry: int | None = None


class SseParser:
    """Incremental parser: feed bytes or text, get the messages completed so far."""

    def __init__(self) -> None:
        self._buf = ""
        self._data: list[str] = []
        self._event = ""
        self._id: str | None = None
        self._retry: int | None = None
        self._saw_cr = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def feed(self, chunk: bytes | str) -> list[SseMessage]:
        """Feed a chunk.

        Args:
            chunk: Raw bytes (decoded as UTF-8, split characters included) or text.

        Returns:
            The messages that the chunk completed.
        """
        text = chunk if isinstance(chunk, str) else self._decoder.decode(chunk)
        return self._consume(text)

    def end(self) -> list[SseMessage]:
        """End of stream: a trailing message without its blank line is dropped, as the spec says.

        Returns:
            The messages completed by the last decoded bytes, if any.
        """
        out = self._consume(self._decoder.decode(b"", final=True))
        self._buf = ""
        self._data = []
        self._event = ""
        return out

    def _consume(self, text: str) -> list[SseMessage]:
        out: list[SseMessage] = []
        if not text:
            return out
        # A CR at the end of the previous chunk already ended its line: skip the LF of that CRLF.
        if self._saw_cr and text.startswith("\n"):
            text = text[1:]
        self._saw_cr = False
        buf = self._buf + text
        start = 0
        i = 0
        n = len(buf)
        while i < n:
            ch = buf[i]
            if ch not in "\r\n":
                i += 1
                continue
            line = buf[start:i]
            if ch == "\r":
                if i + 1 < n:
                    if buf[i + 1] == "\n":
                        i += 1
                else:
                    self._saw_cr = True
            i += 1
            start = i
            msg = self._line(line)
            if msg is not None:
                out.append(msg)
        self._buf = buf[start:]
        return out

    def _line(self, line: str) -> SseMessage | None:
        if line == "":
            if not self._data:
                self._event = ""
                return None
            msg = SseMessage(
                event=self._event or "message",
                data="\n".join(self._data),
                id=self._id,
                retry=self._retry,
            )
            self._data = []
            self._event = ""
            return msg
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if not sep:
            value = ""
        value = value.removeprefix(" ")
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id" and "\0" not in value:
            self._id = value
        elif field == "retry" and value.isdigit():
            self._retry = int(value)
        return None


def iter_sse(chunks: Iterable[bytes | str]) -> Iterator[SseMessage]:
    """Parse an iterable of chunks as server-sent events.

    Args:
        chunks: Bytes or text in arrival order.

    Yields:
        Each message as soon as it is complete.
    """
    parser = SseParser()
    for chunk in chunks:
        yield from parser.feed(chunk)
    yield from parser.end()


async def aiter_sse(chunks: AsyncIterable[bytes | str]) -> AsyncIterator[SseMessage]:
    """Parse an async iterable of chunks as server-sent events.

    Args:
        chunks: Bytes or text in arrival order.

    Yields:
        Each message as soon as it is complete.
    """
    parser = SseParser()
    async for chunk in chunks:
        for msg in parser.feed(chunk):
            yield msg
    for msg in parser.end():
        yield msg
