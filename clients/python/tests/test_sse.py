from __future__ import annotations

import asyncio

import pytest

from evokai.sse import SseMessage, SseParser, aiter_sse, iter_sse


def parse_all(*chunks: bytes | str) -> list[SseMessage]:
    return list(iter_sse(chunks))


def test_single_message_with_event_name() -> None:
    assert parse_all("event: text\ndata: {}\n\n") == [SseMessage(event="text", data="{}")]


def test_message_without_event_name_is_message() -> None:
    assert parse_all("data: x\n\n")[0].event == "message"


def test_multi_line_data_is_joined_with_newlines() -> None:
    msgs = parse_all("data: first\ndata: second\ndata:third\n\n")
    assert msgs == [SseMessage(event="message", data="first\nsecond\nthird")]


def test_comments_and_pings_are_ignored() -> None:
    msgs = parse_all(": ping\n\n", ": another\n", "data: a\n\n", ": ping\n\n")
    assert [m.data for m in msgs] == ["a"]


def test_blank_lines_without_data_dispatch_nothing_and_reset_the_event() -> None:
    msgs = parse_all("event: x\n\n", "data: a\n\n")
    assert msgs == [SseMessage(event="message", data="a")]


@pytest.mark.parametrize("end", ["\n", "\r", "\r\n"])
def test_every_line_end(end: str) -> None:
    text = f"event: e{end}data: 1{end}{end}data: 2{end}{end}"
    assert [(m.event, m.data) for m in parse_all(text)] == [("e", "1"), ("message", "2")]


def test_crlf_split_across_chunks_is_one_line_end() -> None:
    msgs = parse_all("data: a\r", "\n\r", "\ndata: b\r\n\r\n")
    assert [m.data for m in msgs] == ["a", "b"]


def test_lines_and_fields_split_anywhere() -> None:
    text = 'event: text\ndata: {"delta": "ciao"}\n\n'
    for size in (1, 2, 3, 7):
        chunks = [text[i : i + size] for i in range(0, len(text), size)]
        assert parse_all(*chunks) == [SseMessage(event="text", data='{"delta": "ciao"}')]


def test_multi_byte_characters_split_between_chunks() -> None:
    raw = "data: perché è così\n\n".encode()
    chunks = [raw[i : i + 1] for i in range(len(raw))]
    assert parse_all(*chunks)[0].data == "perché è così"


def test_only_one_space_after_the_colon_is_dropped() -> None:
    assert parse_all("data:  two\n\n")[0].data == " two"


def test_field_without_colon_has_empty_value() -> None:
    assert parse_all("data\n\n")[0].data == ""


def test_id_and_retry_are_kept() -> None:
    msg = parse_all("id: 7\nretry: 1500\ndata: x\n\n")[0]
    assert (msg.id, msg.retry) == ("7", 1500)
    # The id persists to later messages; an invalid retry is ignored.
    nxt = parse_all("id: 7\ndata: x\n\nretry: soon\ndata: y\n\n")[1]
    assert (nxt.id, nxt.retry) == ("7", None)


def test_id_with_nul_is_ignored() -> None:
    assert parse_all("id: a\0b\ndata: x\n\n")[0].id is None


def test_unknown_fields_are_ignored() -> None:
    assert parse_all("foo: bar\ndata: x\n\n")[0].data == "x"


def test_trailing_message_without_blank_line_is_dropped() -> None:
    assert parse_all("data: done\n\ndata: partial\n") == [SseMessage("message", "done")]


def test_feed_returns_messages_as_they_complete() -> None:
    p = SseParser()
    assert p.feed("data: a\n") == []
    assert p.feed("\n") == [SseMessage("message", "a")]
    assert p.end() == []


async def test_async_iteration() -> None:
    async def chunks():
        for c in (b"event: done\n", b"data: {}\n", b"\n"):
            await asyncio.sleep(0)
            yield c

    assert [m async for m in aiter_sse(chunks())] == [SseMessage("done", "{}")]
