"""Rewriting the last question, and why a confirmation is asked."""

from __future__ import annotations

import httpx
import pytest
import respx

from evokai import (
    AsyncEvokaiSession,
    ConfirmEvent,
    ConfirmReason,
    DoneEvent,
    EvokaiConflictError,
    EvokaiSession,
    HistoryMessage,
    RewrittenEvent,
    parse_event,
)
from evokai.events import ChatResult, SessionConversation
from evokai.session import chat_body
from tests.support import BASE, TOKEN, frame, sent_json, sse_response

REWRITE = [
    frame({"type": "conversation", "id": "c1", "turnStarted": True, "turnId": "t2", "seq": 6}),
    frame({
        "type": "rewritten",
        "replaced": ["t1"],
        "messageId": "q1",
        "turnId": "t2",
        "seq": 7,
    }),
    frame({"type": "text", "delta": "Again.", "turnId": "t2", "seq": 8}),
    frame({
        "type": "done",
        "reason": "end_turn",
        "conversationId": "c1",
        "rewritable": True,
        "turnId": "t2",
        "seq": 9,
    }),
]


def test_the_body_names_the_edited_question_or_just_asks_for_a_rewrite() -> None:
    assert chat_body(text="x", rewrite="q1")["rewrite"] == {"messageId": "q1"}
    assert chat_body(text="x", rewrite=True)["rewrite"] == {}
    assert "rewrite" not in chat_body(text="x")
    assert "rewrite" not in chat_body(confirm=("a", True), rewrite=True)


def test_rewrite_streams_the_new_turn(router: respx.MockRouter) -> None:
    response, _ = sse_response(REWRITE, turn_id="t2")
    route = router.post("/v1/session/chat").mock(return_value=response)
    with EvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        events = list(s.rewrite("One, again", message_id="q1"))
    assert sent_json(route) == {
        "mode": "text",
        "text": "One, again",
        "rewrite": {"messageId": "q1"},
    }
    rewritten = events[1]
    assert isinstance(rewritten, RewrittenEvent)
    assert (rewritten.replaced, rewritten.message_id) == (["t1"], "q1")
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.rewritable is True


def test_regenerate_streams_the_new_answer(router: respx.MockRouter) -> None:
    response, _ = sse_response(REWRITE, turn_id="t2")
    route = router.post("/v1/session/regenerate").mock(return_value=response)
    with EvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        events = list(s.regenerate())
    assert sent_json(route) == {"mode": "text"}
    assert isinstance(events[1], RewrittenEvent)


async def test_async_regenerate_refused(router: respx.MockRouter) -> None:
    router.post("/v1/session/regenerate").mock(
        return_value=httpx.Response(409, json={"error": "not_regenerable", "message": "No."})
    )
    async with AsyncEvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        with pytest.raises(EvokaiConflictError) as e:
            async for _ in s.regenerate():
                pass
    assert e.value.code == "not_regenerable"


async def test_async_rewrite_and_a_refusal(router: respx.MockRouter) -> None:
    router.post("/v1/session/chat").mock(
        return_value=httpx.Response(
            409, json={"error": "rewrite_after_write", "message": "Already changed data."}
        )
    )
    async with AsyncEvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        with pytest.raises(EvokaiConflictError) as e:
            async for _ in s.rewrite("Edited"):
                pass
    assert e.value.code == "rewrite_after_write"


def test_external_storage_gets_where_the_history_is_replaced_from(
    router: respx.MockRouter,
) -> None:
    route = router.post("/v1/session/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "conversationId": "c1",
                "turnId": "t2",
                "reason": "end_turn",
                "text": "Ok.",
                "events": [],
                "messages": [],
                "replacedFrom": 2,
                "rewritable": True,
            },
        )
    )
    history = [HistoryMessage(role="user", text="One"), HistoryMessage(role="assistant", text="1")]
    with EvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        result = s.chat_json("Two", history=history, rewrite=True)
    assert isinstance(result, ChatResult)
    assert (result.replaced_from, result.rewritable) == (2, True)
    assert sent_json(route)["rewrite"] == {}


def test_confirmations_say_why_and_results_keep_the_write_mark() -> None:
    e = parse_event({
        "type": "confirm",
        "actionId": "a",
        "items": ["Read VIP-1"],
        "why": [{"key": "confirm.why.rule", "rule": "vip", "level": "application", "label": "VIP"}],
    })
    assert isinstance(e, ConfirmEvent)
    assert e.why == [
        ConfirmReason(key="confirm.why.rule", rule="vip", level="application", label="VIP")
    ]
    conv = SessionConversation.model_validate({
        "messages": [
            {"id": "q", "role": "user", "text": "Hi", "events": [], "rewritable": True},
        ],
        "pending": {"actionId": "a", "items": ["x"], "why": [{"key": "confirm.why.writes"}]},
    })
    assert conv.messages[0].rewritable is True
    assert conv.pending is not None and conv.pending.why[0].key == "confirm.why.writes"
    h = HistoryMessage.model_validate({
        "role": "user",
        "content": [{"toolResult": {"id": "t", "name": "x", "text": "ok", "wrote": True}}],
    })
    dumped = h.model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert dumped["content"][0]["toolResult"]["wrote"] is True
