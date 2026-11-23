from __future__ import annotations

from typing import Any

import pytest

from evokai import (
    AskEvent,
    ChartEvent,
    ConfirmedEvent,
    ConfirmEvent,
    ConversationEvent,
    DiagramEvent,
    DoneEvent,
    ErrorEvent,
    NavigateEvent,
    SayEvent,
    SpeechEvent,
    TextEvent,
    ToolEvent,
    UnknownEvent,
    UsageEvent,
    parse_event,
)
from evokai.events import ChatResult, SessionConversation, parse_events
from evokai.models import BuiltinToolBlock, TextBlock, ToolResultBlock, ToolUseBlock

META = {"turnId": "t1", "seq": 4}

CASES: list[tuple[dict[str, Any], type, dict[str, Any]]] = [
    (
        {"type": "conversation", "id": "c1", "turnStarted": True},
        ConversationEvent,
        {"id": "c1", "turn_started": True},
    ),
    ({"type": "text", "delta": "Ciao"}, TextEvent, {"delta": "Ciao"}),
    (
        {"type": "say", "index": 0, "text": "Hello.", "filler": True},
        SayEvent,
        {"index": 0, "text": "Hello.", "filler": True},
    ),
    (
        {"type": "say", "index": 1, "text": "Sono 1.000 punti.", "spoken": "Sono mille punti."},
        SayEvent,
        {"index": 1, "text": "Sono 1.000 punti.", "spoken": "Sono mille punti.", "filler": False},
    ),
    (
        {
            "type": "say",
            "index": 2,
            "text": "Hi!",
            "tagged": "[laughs] Hi!",
            "marks": [{"name": "wave", "at": 2}],
        },
        SayEvent,
        {"tagged": "[laughs] Hi!", "marks": [{"name": "wave", "at": 2}]},
    ),
    (
        {"type": "tool", "name": "crm_search", "status": "done"},
        ToolEvent,
        {"name": "crm_search", "status": "done"},
    ),
    (
        {"type": "chart", "chart": {"type": "kpi", "title": "T", "subtitle": "", "items": []}},
        ChartEvent,
        {"chart": {"type": "kpi", "title": "T", "subtitle": "", "items": []}},
    ),
    (
        {"type": "diagram", "diagram": {"kind": "steps", "title": "S", "steps": []}},
        DiagramEvent,
        {"diagram": {"kind": "steps", "title": "S", "steps": []}},
    ),
    (
        {
            "type": "ask",
            "askId": "ask_1",
            "question": "Which one?",
            "options": ["A", "B"],
            "multiple": True,
            "other": False,
        },
        AskEvent,
        {"ask_id": "ask_1", "question": "Which one?", "options": ["A", "B"], "multiple": True},
    ),
    ({"type": "navigate", "path": "/clients/7"}, NavigateEvent, {"path": "/clients/7"}),
    (
        {"type": "confirm", "actionId": "act_1", "items": ["Delete client 7"]},
        ConfirmEvent,
        {"action_id": "act_1", "items": ["Delete client 7"]},
    ),
    ({"type": "confirmed", "approve": False}, ConfirmedEvent, {"approve": False}),
    (
        {"type": "usage", "dayPct": 12.5, "monthPct": None, "companyPct": 3.0, "monthOut": False},
        UsageEvent,
        {"day_pct": 12.5, "month_pct": None, "company_pct": 3.0, "month_out": False},
    ),
    (
        {"type": "error", "code": "limit", "limit": "subject_day", "message": "Enough for today."},
        ErrorEvent,
        {"code": "limit", "limit": "subject_day", "message": "Enough for today."},
    ),
    (
        {"type": "done", "reason": "end_turn", "conversationId": "c1"},
        DoneEvent,
        {"reason": "end_turn", "conversation_id": "c1", "messages": None},
    ),
    (
        {"type": "speech", "enabled": False, "reason": "tts", "message": "Voice is off."},
        SpeechEvent,
        {"enabled": False, "reason": "tts", "message": "Voice is off."},
    ),
]


@pytest.mark.parametrize(("data", "cls", "attrs"), CASES, ids=[c[0]["type"] for c in CASES])
def test_known_events_parse_into_their_model(
    data: dict[str, Any], cls: type, attrs: dict[str, Any]
) -> None:
    event = parse_event({**data, **META})
    assert type(event) is cls
    assert event.turn_id == "t1"
    assert event.seq == 4
    assert event.type == data["type"]
    for name, value in attrs.items():
        assert getattr(event, name) == value


def test_no_amount_field_in_usage_events() -> None:
    fields = set(UsageEvent.model_fields)
    assert not {f for f in fields if "usd" in f or "cost" in f}


def test_unknown_type_keeps_every_field() -> None:
    event = parse_event({"type": "highlight", "target": "price", **META})
    assert isinstance(event, UnknownEvent)
    assert event.type == "highlight"
    assert event.data == {"type": "highlight", "target": "price", "turnId": "t1", "seq": 4}


def test_known_type_with_a_payload_that_no_longer_fits_falls_back() -> None:
    event = parse_event({"type": "say", "turnId": "t1"})
    assert isinstance(event, UnknownEvent)
    assert event.type == "say"


def test_new_fields_on_known_events_are_kept() -> None:
    event = parse_event({"type": "text", "delta": "x", "voice": "it-IT"})
    assert isinstance(event, TextEvent)
    assert event.model_extra == {"voice": "it-IT"}


def test_default_type_comes_from_the_sse_event_name() -> None:
    assert isinstance(parse_event({"delta": "x"}, default_type="text"), TextEvent)
    assert isinstance(parse_event({"x": 1}), UnknownEvent)
    assert parse_event({"type": 3}).type == "3"


def test_done_carries_external_history_messages() -> None:
    event = parse_event({
        "type": "done",
        "reason": "end_turn",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"text": "hi"},
                    {"toolUse": {"id": "u1", "name": "crm", "input": {"q": 1}}},
                    {"toolResult": {"id": "u1", "name": "crm", "ok": False, "text": "no"}},
                    {"builtinTool": {"id": "b1", "name": "mcp_server:crm", "changes": True}},
                ],
                "text": "hi",
            }
        ],
    })
    assert isinstance(event, DoneEvent)
    assert event.messages is not None
    blocks = event.messages[0].content
    assert [type(b) for b in blocks] == [TextBlock, ToolUseBlock, ToolResultBlock, BuiltinToolBlock]
    assert isinstance(blocks[1], ToolUseBlock)
    assert blocks[1].tool_use.input == {"q": 1}
    assert event.messages[0].model_dump(exclude_unset=True)["content"][1] == {
        "toolUse": {"id": "u1", "name": "crm", "input": {"q": 1}}
    }


def test_stored_events_are_typed() -> None:
    conv = SessionConversation.model_validate({
        "messages": [
            {
                "id": "m1",
                "role": "assistant",
                "text": "ok",
                "events": [{"type": "confirmed", "approve": True}, {"type": "future"}],
                "feedback": 1,
            }
        ],
        "pending": {"actionId": "a1", "items": ["x"]},
        "lastSeq": 9,
        "runningTurnId": None,
    })
    assert [type(e) for e in conv.messages[0].events] == [ConfirmedEvent, UnknownEvent]
    assert conv.pending is not None
    assert conv.pending.action_id == "a1"
    assert conv.last_seq == 9


def test_chat_result_events_are_typed() -> None:
    result = ChatResult.model_validate({
        "conversationId": "c",
        "turnId": "t",
        "reason": "end_turn",
        "text": "x",
        "events": [{"type": "text", "delta": "x"}, "garbage"],
    })
    assert [type(e) for e in result.events] == [TextEvent]


def test_parse_events_ignores_what_is_not_a_list() -> None:
    assert parse_events(None) == []
    already = TextEvent(delta="x")
    assert parse_events([already]) == [already]


def test_usage_and_limit_events_carry_windows_and_resets() -> None:
    share = {
        "limit": "subject_week",
        "scope": "subject",
        "period": "week",
        "usedPct": 100.0,
        "leftPct": 0.0,
        "resetsAt": "2026-11-20T23:00:00+00:00",
    }
    usage = parse_event({
        "type": "usage",
        "dayPct": None,
        "weekPct": 100.0,
        "monthPct": None,
        "companyPct": 3.0,
        "monthOut": False,
        "out": True,
        "binding": share,
        "resetsAt": share["resetsAt"],
        "caps": [share],
        **META,
    })
    assert isinstance(usage, UsageEvent)
    assert usage.week_pct == pytest.approx(100) and usage.out is True
    assert usage.resets_at == share["resetsAt"] and usage.caps == [share]
    error = parse_event({
        "type": "error",
        "code": "limit",
        "limit": "subject_week",
        "message": "You have reached your weekly assistant limit: it resets on Saturday at 00:00.",
        "resetsAt": share["resetsAt"],
        **META,
    })
    assert isinstance(error, ErrorEvent)
    assert error.limit == "subject_week" and error.resets_at == share["resetsAt"]


def test_session_shares_name_the_binding_cap() -> None:
    from evokai import CapShare, UsageShares

    shares = UsageShares.model_validate({
        "dayPct": 20.0,
        "monthPct": None,
        "companyPct": 5.0,
        "monthOut": False,
        "binding": {
            "limit": "app_week",
            "scope": "application",
            "period": "week",
            "usedPct": 40.0,
            "leftPct": 60.0,
            "resetsAt": "2026-11-20T23:00:00+00:00",
        },
    })
    assert isinstance(shares.binding, CapShare) and shares.binding.limit == "app_week"


def test_subject_limits_take_caps_in_the_application_currency() -> None:
    from evokai import CapConfig, SubjectLimits

    limits = SubjectLimits(caps=[CapConfig(period="week", amount=5, resets="saturday")])
    assert limits.model_dump(exclude_none=True) == {
        "caps": [{"period": "week", "amount": 5, "resets": "saturday"}]
    }
