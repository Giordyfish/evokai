from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from evokai import (
    AskEvent,
    AsyncEvokaiServer,
    AsyncEvokaiSession,
    ConfirmEvent,
    DoneEvent,
    EvokaiAuthError,
    EvokaiConflictError,
    EvokaiConnectionError,
    EvokaiRateLimitError,
    EvokaiServer,
    EvokaiSession,
    EvokaiValidationError,
    HistoryMessage,
    PageContext,
    SessionToken,
    TextEvent,
    UnknownEvent,
    UsageEvent,
)
from evokai.events import ChatResult, SessionConversation
from evokai.models import Memory, MemoryConsentResult, MemoryList, SessionInfo
from tests.support import BASE, SERVER_KEY, TOKEN, frame, sent_json, sse_response

TURN = [
    frame({"type": "conversation", "id": "c1", "turnStarted": True, "turnId": "t1", "seq": 1}),
    ": ping\n\n",
    frame({"type": "text", "delta": "Hel", "turnId": "t1", "seq": 2}),
    frame({"type": "text", "delta": "lo", "turnId": "t1", "seq": 3}),
    frame({
        "type": "usage",
        "dayPct": 1.0,
        "monthPct": None,
        "companyPct": 2.0,
        "monthOut": False,
        "turnId": "t1",
        "seq": 4,
    }),
    frame({"type": "highlight", "target": "price", "turnId": "t1", "seq": 5}),
    frame({"type": "done", "reason": "end_turn", "conversationId": "c1", "turnId": "t1", "seq": 6}),
]


def _sync(**kw: Any) -> EvokaiSession:
    return EvokaiSession(BASE, TOKEN, replay_delay=0, **kw)


def _async(**kw: Any) -> AsyncEvokaiSession:
    return AsyncEvokaiSession(BASE, TOKEN, replay_delay=0, **kw)


def _chunked(frames: list[str], size: int) -> list[bytes]:
    raw = "".join(frames).encode()
    return [raw[i : i + size] for i in range(0, len(raw), size)]


# Streaming


@pytest.mark.parametrize("size", [1, 5, 64, 10_000])
def test_chat_streams_typed_events_whatever_the_chunking(
    router: respx.MockRouter, size: int
) -> None:
    response, body = sse_response(_chunked(TURN, size))
    route = router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s:
        stream = s.chat("Hello?")
        events = list(stream)
    assert [e.type for e in events] == ["conversation", "text", "text", "usage", "highlight", "done"]
    assert isinstance(events[3], UsageEvent)
    assert isinstance(events[4], UnknownEvent)
    assert events[4].data["target"] == "price"
    assert stream.turn_id == "t1"
    assert stream.text == "Hello"
    assert isinstance(stream.done, DoneEvent)
    assert stream.done.reason == "end_turn"
    assert s.last_seq == 6
    assert body.closed
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["accept"] == "text/event-stream"
    assert sent_json(route) == {"mode": "text", "text": "Hello?"}


def test_chat_body_carries_page_mode_history_and_end_of_speech(router: respx.MockRouter) -> None:
    response, _ = sse_response(TURN)
    route = router.post("/v1/session/chat").mock(return_value=response)
    history = [
        HistoryMessage(role="user", text="Hi"),
        {"role": "assistant", "text": "Hello", "content": [{"text": "Hello"}]},
    ]
    with _sync() as s:
        s.chat(
            "sì",
            page=PageContext(path="/clients/7", title="Client 7"),
            mode="voice",
            history=history,
            end_of_speech_at=1_700_000_000.5,
        ).until_done()
    assert sent_json(route) == {
        "mode": "voice",
        "text": "sì",
        "page": {"path": "/clients/7", "title": "Client 7"},
        "history": [
            {"role": "user", "text": "Hi"},
            {"role": "assistant", "text": "Hello", "content": [{"text": "Hello"}]},
        ],
        "endOfSpeechAt": 1_700_000_000.5,
    }


def test_multi_line_data_and_event_name_without_type(router: respx.MockRouter) -> None:
    raw = (
        'event: text\ndata: {"delta": "a",\ndata:  "turnId": "t1", "seq": 1}\n\n'
        "data: not json\n\n"
        "data: [1, 2]\n\n"
        f"{frame({'type': 'done', 'reason': 'end_turn', 'turnId': 't1', 'seq': 2}, name=False)}"
    )
    response, _ = sse_response([raw])
    router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s:
        events = list(s.chat("x"))
    assert isinstance(events[0], TextEvent)
    assert events[0].delta == "a"
    assert isinstance(events[1], DoneEvent)
    assert len(events) == 2


def test_stream_stops_at_done_and_ignores_trailing_bytes(router: respx.MockRouter) -> None:
    response, body = sse_response([*TURN, frame({"type": "text", "delta": "late", "turnId": "t1"})])
    router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s:
        stream = s.chat("x")
        assert isinstance(stream.until_done(), DoneEvent)
    assert "late" not in stream.text
    assert body.closed


def test_turn_id_from_the_events_when_the_header_is_missing(router: respx.MockRouter) -> None:
    response, _ = sse_response(TURN, turn_id=None)
    router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s, s.chat("x") as stream:
        assert stream.turn_id is None
        next(stream)
        assert stream.turn_id == "t1"


def test_breaking_out_closes_the_connection(router: respx.MockRouter) -> None:
    response, body = sse_response(TURN)
    router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s:
        with s.chat("x") as stream:
            assert stream.turn_id == "t1"
            for event in stream:
                if isinstance(event, TextEvent):
                    break
        assert body.closed
        assert stream.done is None


def test_confirm_streams_the_resumed_turn(router: respx.MockRouter) -> None:
    response, _ = sse_response([
        frame({
            "type": "confirm",
            "actionId": "act_1",
            "items": ["Delete"],
            "turnId": "t1",
            "seq": 1,
        }),
        frame({"type": "done", "reason": "end_turn", "turnId": "t1", "seq": 2}),
    ])
    route = router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s:
        events = list(s.confirm("act_1", True, page={"path": "/x"}))
    assert isinstance(events[0], ConfirmEvent)
    assert sent_json(route) == {
        "mode": "text",
        "confirm": {"actionId": "act_1", "approve": True},
        "page": {"path": "/x"},
    }


@pytest.mark.parametrize(("option", "text"), [("Second", "Second"), (["A", "C"], "A, C")])
def test_answer_sends_the_option_as_the_next_message(
    router: respx.MockRouter, option: Any, text: str
) -> None:
    response, _ = sse_response(TURN)
    route = router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s:
        s.answer(option, mode="voice").until_done()
    assert sent_json(route) == {"mode": "voice", "text": text}


def test_refused_chat_raises_before_streaming(router: respx.MockRouter) -> None:
    router.post("/v1/session/chat").mock(
        return_value=httpx.Response(
            401, json={"error": "unauthorized", "message": "Invalid session."}
        )
    )
    with _sync() as s, pytest.raises(EvokaiAuthError) as info:
        list(s.chat("x"))
    assert (info.value.status, info.value.code) == (401, "unauthorized")


def test_rate_limited_chat_carries_retry_after(router: respx.MockRouter) -> None:
    router.post("/v1/session/chat").mock(
        return_value=httpx.Response(
            429, json={"error": "rate_limited", "message": "Wait."}, headers={"Retry-After": "30"}
        )
    )
    with _sync() as s, pytest.raises(EvokaiRateLimitError) as info, s.chat("x"):
        pass
    assert info.value.retry_after == 30
    assert info.value.code == "rate_limited"


def test_broken_stream_is_completed_by_the_replay(router: respx.MockRouter) -> None:
    response, _ = sse_response(TURN[:3], fail=httpx.ReadError("connection reset"))
    router.post("/v1/session/chat").mock(return_value=response)
    replay = router.get("/v1/session/events").mock(
        side_effect=[
            httpx.Response(500, json={"error": "internal", "message": "x"}),
            httpx.Response(
                200,
                json={
                    "events": [
                        {"type": "text", "delta": "Hel", "turnId": "t1", "seq": 2},
                        {"type": "text", "delta": "other", "turnId": "t0", "seq": 3},
                        {"type": "text", "delta": "lo", "turnId": "t1", "seq": 4},
                        {"type": "done", "reason": "end_turn", "turnId": "t1", "seq": 5},
                    ]
                },
            ),
        ]
    )
    with _sync() as s:
        stream = s.chat("x")
        events = list(stream)
    assert [e.type for e in events] == ["conversation", "text", "text", "done"]
    assert stream.text == "Hello"
    assert replay.calls[0].request.url.params["after"] == "2"
    assert replay.call_count == 2


def test_replay_that_never_finds_done_raises(router: respx.MockRouter) -> None:
    response, _ = sse_response(TURN[:3])
    router.post("/v1/session/chat").mock(return_value=response)
    router.get("/v1/session/events").mock(return_value=httpx.Response(200, json={"events": []}))
    with _sync(replay_attempts=2) as s, pytest.raises(EvokaiConnectionError) as info:
        list(s.chat("x"))
    assert info.value.code == "incomplete_turn"


def test_stream_without_turn_id_cannot_be_replayed(router: respx.MockRouter) -> None:
    response, _ = sse_response([": ping\n\n"], turn_id=None)
    router.post("/v1/session/chat").mock(return_value=response)
    with _sync() as s, pytest.raises(EvokaiConnectionError):
        s.chat("x").until_done()


def test_network_failure_on_open(router: respx.MockRouter) -> None:
    router.post("/v1/session/chat").mock(side_effect=httpx.ConnectError("refused"))
    with _sync() as s, pytest.raises(EvokaiConnectionError) as info:
        s.chat("x").open()
    assert info.value.code == "network"


async def test_async_chat_streams_and_replays(router: respx.MockRouter) -> None:
    response, body = sse_response(_chunked(TURN, 7))
    route = router.post("/v1/session/chat").mock(return_value=response)
    async with _async() as s:
        stream = s.chat("Hello?", page={"path": "/"})
        events = [e async for e in stream]
        assert stream.text == "Hello"
        assert stream.turn_id == "t1"
        assert isinstance(events[-1], DoneEvent)
        assert s.last_seq == 6
    assert body.closed
    assert sent_json(route) == {"mode": "text", "text": "Hello?", "page": {"path": "/"}}

    broken, _ = sse_response(TURN[:2], fail=httpx.ReadError("reset"))
    router.post("/v1/session/chat").mock(return_value=broken)
    router.get("/v1/session/events").mock(
        return_value=httpx.Response(
            200,
            json={"events": [{"type": "done", "reason": "cancelled", "turnId": "t1", "seq": 9}]},
        )
    )
    async with _async() as s, s.chat("x") as stream:
        done = await stream.until_done()
    assert done.reason == "cancelled"


async def test_async_confirm_answer_and_early_close(router: respx.MockRouter) -> None:
    response, body = sse_response(TURN)
    route = router.post("/v1/session/chat").mock(return_value=response)
    async with _async() as s:
        async with s.confirm("a1", False) as stream:
            async for _ in stream:
                break
        assert body.closed
        assert sent_json(route)["confirm"] == {"actionId": "a1", "approve": False}
        response2, _ = sse_response(TURN)
        route.mock(return_value=response2)
        await s.answer(["x", "y"]).until_done()
        assert sent_json(route) == {"mode": "text", "text": "x, y"}


async def test_async_errors_and_incomplete(router: respx.MockRouter) -> None:
    router.post("/v1/session/chat").mock(
        return_value=httpx.Response(403, json={"error": "origin", "message": "Origin not allowed."})
    )
    async with _async() as s:
        with pytest.raises(EvokaiAuthError) as info:
            await s.chat("x").open()
        assert info.value.code == "origin"
        response, _ = sse_response(TURN[:1])
        router.post("/v1/session/chat").mock(return_value=response)
        router.get("/v1/session/events").mock(return_value=httpx.Response(200, json={"events": []}))
        s.replay_attempts = 2
        with pytest.raises(EvokaiConnectionError):
            await s.chat("x").until_done()
        router.post("/v1/session/chat").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(EvokaiConnectionError) as timeout:
            await s.chat("x").open()
        assert timeout.value.code == "timeout"


# JSON calls


INFO = {
    "conversationId": "c1",
    "agent": "assistant",
    "expiresAt": 1_900_000_000,
    "usage": {"dayPct": None, "monthPct": 50.0, "companyPct": 1.0, "monthOut": False},
    "memories": [{"id": "f1", "fact": "Likes tea"}],
    "suggestions": ["What can you do?"],
}
CONVERSATION = {
    "messages": [{"id": "m1", "role": "assistant", "text": "x", "events": [], "feedback": None}],
    "pending": None,
    "lastSeq": 12,
    "runningTurnId": "t9",
}
CHAT_RESULT = {
    "conversationId": "c1",
    "turnId": "t1",
    "reason": "ask",
    "text": "Which one?",
    "events": [
        {
            "type": "ask",
            "askId": "k",
            "question": "Which one?",
            "options": ["A"],
            "turnId": "t1",
            "seq": 14,
        }
    ],
    "messages": None,
}


def _mock_json_routes(router: respx.MockRouter) -> dict[str, respx.Route]:
    return {
        "info": router.get("/v1/session").mock(return_value=httpx.Response(200, json=INFO)),
        "conversation": router.get("/v1/session/conversation").mock(
            return_value=httpx.Response(200, json=CONVERSATION)
        ),
        "events": router.get("/v1/session/events").mock(
            return_value=httpx.Response(
                200, json={"events": [{"type": "text", "delta": "x", "turnId": "t9", "seq": 13}]}
            )
        ),
        "chat": router.post("/v1/session/chat").mock(
            return_value=httpx.Response(200, json=CHAT_RESULT)
        ),
        "cancel": router.post("/v1/session/turns/t%2F1/cancel").mock(
            return_value=httpx.Response(200, json={"ok": True, "cancelled": True})
        ),
        "feedback": router.post("/v1/session/messages/m1/feedback").mock(
            return_value=httpx.Response(200, json={"ok": True})
        ),
        "exit": router.post("/v1/session/exit").mock(return_value=httpx.Response(204)),
        "bug": router.post("/v1/session/bug-reports").mock(
            return_value=httpx.Response(201, json={"id": "b1"})
        ),
    }


def _check_json_routes(routes: dict[str, respx.Route]) -> None:
    for name, route in routes.items():
        assert route.call_count == 1, name
        assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert routes["events"].calls.last.request.url.params["after"] == "12"
    chat = routes["chat"].calls.last.request
    assert chat.headers["accept"] == "application/json"
    assert json.loads(chat.content) == {
        "mode": "text",
        "confirm": {"actionId": "a1", "approve": True},
        "history": [{"role": "user", "text": "x"}],
    }
    assert sent_json(routes["feedback"]) == {"value": -1}
    assert sent_json(routes["exit"]) == {"code": "1234"}
    assert sent_json(routes["bug"]) == {"description": "Stuck", "context": {"page": "/"}}


def test_session_json_calls(router: respx.MockRouter) -> None:
    routes = _mock_json_routes(router)
    with _sync() as s:
        info = s.info()
        assert isinstance(info, SessionInfo)
        assert info.usage.month_pct == pytest.approx(50.0)
        assert info.memories[0].fact == "Likes tea"
        assert s.conversation_id == "c1"
        conv = s.conversation()
        assert isinstance(conv, SessionConversation)
        assert conv.running_turn_id == "t9"
        assert s.last_seq == 12
        events = s.events()
        assert isinstance(events[0], TextEvent)
        assert s.last_seq == 13
        result = s.chat_json(confirm=("a1", True), history=[{"role": "user", "text": "x"}])
        assert isinstance(result, ChatResult)
        assert isinstance(result.events[0], AskEvent)
        assert s.last_seq == 14
        assert s.cancel("t/1") is True
        assert s.feedback("m1", -1) is None
        assert s.exit("1234") is None
        assert s.report_bug("Stuck", {"page": "/"}) == "b1"
    _check_json_routes(routes)


async def test_async_session_json_calls(router: respx.MockRouter) -> None:
    routes = _mock_json_routes(router)
    async with _async() as s:
        assert (await s.info()).agent == "assistant"
        assert (await s.conversation()).last_seq == 12
        assert (await s.events())[0].seq == 13
        result = await s.chat_json(confirm=("a1", True), history=[{"role": "user", "text": "x"}])
        assert result.reason == "ask"
        assert await s.cancel("t/1") is True
        await s.feedback("m1", -1)
        await s.exit("1234")
        assert await s.report_bug("Stuck", {"page": "/"}) == "b1"
    _check_json_routes(routes)


def test_events_with_explicit_after(router: respx.MockRouter) -> None:
    route = router.get("/v1/session/events").mock(
        return_value=httpx.Response(200, json={"events": []})
    )
    with _sync() as s:
        assert s.events(after=0) == []
    assert route.calls.last.request.url.params["after"] == "0"


# Tokens and sharing


def test_session_accepts_a_minted_token_and_a_refreshed_one(router: respx.MockRouter) -> None:
    route = router.get("/v1/session").mock(return_value=httpx.Response(200, json=INFO))
    minted = SessionToken(token="tok-a", expires_at=1, conversation_id="c1")
    with EvokaiSession(BASE, minted) as s:
        assert s.conversation_id == "c1"
        s.info()
        s.use_token("tok-b")
        s.info()
    assert [c.request.headers["authorization"] for c in route.calls] == [
        "Bearer tok-a",
        "Bearer tok-b",
    ]
    with pytest.raises(ValueError):
        EvokaiSession(BASE, "")


async def test_server_session_shares_the_http_client(router: respx.MockRouter) -> None:
    router.get("/v1/session").mock(return_value=httpx.Response(200, json=INFO))
    with httpx.Client() as http:
        server = EvokaiServer(BASE, SERVER_KEY, http_client=http)
        session = server.session(TOKEN, replay_attempts=1)
        assert session._transport.client is http
        assert session.replay_attempts == 1
        session.info()
        session.close()
        server.close()
        assert not http.is_closed
    async with httpx.AsyncClient() as http_async:
        aserver = AsyncEvokaiServer(BASE, SERVER_KEY, http_client=http_async)
        asession = aserver.session(TOKEN)
        assert (await asession.info()).agent == "assistant"
        await asession.aclose()
        await aserver.aclose()
        assert not http_async.is_closed


def test_connections_list_start_and_disconnect(router: respx.MockRouter) -> None:
    router.get("/v1/session/connections").mock(
        return_value=httpx.Response(
            200,
            json={
                "connections": [
                    {
                        "provider": "google",
                        "title": "Google Workspace",
                        "status": "connected",
                        "account": "ada@example.com",
                        "scopes": ["openid"],
                        "families": ["mail"],
                        "connectedAt": "2026-11-16T10:00:00Z",
                    }
                ]
            },
        )
    )
    start = router.post("/v1/session/connections/google/start").mock(
        return_value=httpx.Response(
            200,
            json={"provider": "google", "authorizeUrl": "https://accounts.test/a", "expiresAt": 9},
        )
    )
    gone = router.delete("/v1/session/connections/google").mock(return_value=httpx.Response(204))
    with _sync() as s:
        (c,) = s.connections()
        assert c.status == "connected" and c.connected_at == "2026-11-16T10:00:00Z"
        started = s.start_connection("google", "https://app.test/back")
        assert started.authorize_url == "https://accounts.test/a"
        s.disconnect("google")
    assert sent_json(start) == {"returnTo": "https://app.test/back"}
    assert gone.called


async def test_async_connections(router: respx.MockRouter) -> None:
    router.get("/v1/session/connections").mock(
        return_value=httpx.Response(200, json={"connections": []})
    )
    router.post("/v1/session/connections/microsoft365/start").mock(
        return_value=httpx.Response(
            200, json={"provider": "microsoft365", "authorizeUrl": "u", "expiresAt": 1}
        )
    )
    async with _async() as s:
        assert await s.connections() == []
        assert (await s.start_connection("microsoft365")).expires_at == 1


# Memory


MEMORY_ON = {"mode": "on", "consent": "unknown", "active": True, "ask": False}
MEMORIES = {
    "memory": MEMORY_ON,
    "items": [
        {
            "id": "m1",
            "text": "Prefers short answers",
            "kind": "preference",
            "agent": "assistant",
            "source": "agent",
            "createdAt": "2026-11-03T10:00:00Z",
            "updatedAt": None,
            "expiresAt": None,
            "lastUsedAt": "2026-11-12T10:00:00Z",
            "uses": 4,
        },
        {"id": "m2", "text": "Asked about October", "kind": "episodic", "source": "summary"},
    ],
}
EDITED = {
    "id": "m1",
    "text": "Short answers, with a table",
    "kind": "preference",
    "source": "person",
}


def _mock_memory_routes(router: respx.MockRouter) -> dict[str, respx.Route]:
    return {
        "list": router.get("/v1/session/memories").mock(
            return_value=httpx.Response(200, json=MEMORIES)
        ),
        "consent": router.post("/v1/session/memory-consent").mock(
            return_value=httpx.Response(
                200,
                json={
                    "memory": {"mode": "ask", "consent": "declined", "active": False, "ask": False},
                    "deleted": 2,
                },
            )
        ),
        "edit": router.patch("/v1/session/memories/m%2F1").mock(
            return_value=httpx.Response(200, json=EDITED)
        ),
        "forget_one": router.delete("/v1/session/memories/m2").mock(
            return_value=httpx.Response(200, json={"deleted": 1})
        ),
        "forget_all": router.delete("/v1/session/memories").mock(
            return_value=httpx.Response(200, json={"deleted": 3})
        ),
    }


def _check_memory_routes(routes: dict[str, respx.Route]) -> None:
    for name, route in routes.items():
        assert route.call_count == 1, name
        assert route.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent_json(routes["consent"]) == {"granted": False}
    assert sent_json(routes["edit"]) == {"text": "Short answers, with a table"}


def _check_list(listed: MemoryList) -> None:
    assert isinstance(listed, MemoryList)
    assert listed.memory.active is True
    assert [type(m) for m in listed.items] == [Memory, Memory]
    first = listed.items[0]
    assert (first.kind, first.source, first.uses) == ("preference", "agent", 4)
    assert first.last_used_at is not None
    assert listed.items[1].created_at is None


def test_session_memory_calls(router: respx.MockRouter) -> None:
    routes = _mock_memory_routes(router)
    with _sync() as s:
        _check_list(s.memories())
        result = s.memory_consent(False)
        assert isinstance(result, MemoryConsentResult)
        assert (result.memory.consent, result.deleted) == ("declined", 2)
        edited = s.edit_memory("m/1", "Short answers, with a table")
        assert isinstance(edited, Memory)
        assert edited.source == "person"
        assert s.forget_memory("m2") == 1
        assert s.forget_memory() == 3
    _check_memory_routes(routes)


async def test_async_session_memory_calls(router: respx.MockRouter) -> None:
    routes = _mock_memory_routes(router)
    async with _async() as s:
        _check_list(await s.memories())
        assert (await s.memory_consent(False)).deleted == 2
        assert (await s.edit_memory("m/1", "Short answers, with a table")).text.startswith("Short")
        assert await s.forget_memory("m2") == 1
        assert await s.forget_memory() == 3
    _check_memory_routes(routes)


def test_memory_refusals_and_conflicts_are_typed(router: respx.MockRouter) -> None:
    router.patch("/v1/session/memories/m1").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "memory_refused",
                "message": "A memory may not hold instructions, secrets or personal data.",
            },
        )
    )
    router.post("/v1/session/memory-consent").mock(
        return_value=httpx.Response(
            409, json={"error": "memory_off", "message": "Memory is off for this application."}
        )
    )
    with _sync() as s:
        with pytest.raises(EvokaiValidationError) as refused:
            s.edit_memory("m1", "My password is hunter2")
        assert refused.value.code == "memory_refused"
        with pytest.raises(EvokaiConflictError) as off:
            s.memory_consent(True)
        assert off.value.code == "memory_off"


def test_session_info_carries_the_memory_state(router: respx.MockRouter) -> None:
    ask = {"mode": "ask", "consent": "unknown", "active": False, "ask": True}
    router.get("/v1/session").mock(return_value=httpx.Response(200, json={**INFO, "memory": ask}))
    with _sync() as s:
        info = s.info()
    assert info.memory is not None
    assert info.memory.ask is True
    assert info.memories[0].fact == "Likes tea"
