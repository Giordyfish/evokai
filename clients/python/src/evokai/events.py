"""Session events: the service's stream contract, as typed models.

Every event carries ``turn_id`` and a per-session ``seq``. Known types parse into their model
(a discriminated union on ``type``); an event type this client does not know yet, or a known one
whose payload no longer fits, becomes an :class:`UnknownEvent` that keeps every field, so a newer
service never breaks a stream.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError

from evokai.models import ConfirmReason, HistoryMessage, PendingConfirmation, WireModel

__all__ = [
    "AskEvent",
    "ChartEvent",
    "ChatResult",
    "ConfirmEvent",
    "ConfirmedEvent",
    "ConnectEvent",
    "ConversationDetail",
    "ConversationEvent",
    "DiagramEvent",
    "DoneEvent",
    "ErrorEvent",
    "Event",
    "KnownEvent",
    "NavigateEvent",
    "RewrittenEvent",
    "SayEvent",
    "SessionConversation",
    "SpeechEvent",
    "StoredMessage",
    "TextEvent",
    "ToolEvent",
    "UnknownEvent",
    "UsageEvent",
    "parse_event",
    "parse_events",
]


class BaseEvent(WireModel):
    """What every event carries: its turn and its sequence number (for replay)."""

    turn_id: str | None = None
    seq: int | None = None


class ConversationEvent(BaseEvent):
    """The turn started on this conversation."""

    type: Literal["conversation"] = "conversation"
    id: str
    turn_started: bool = False


class TextEvent(BaseEvent):
    """A piece of the answer's text."""

    type: Literal["text"] = "text"
    delta: str = ""


class SayEvent(BaseEvent):
    """Voice mode: a speakable segment, numbered from 0 within the turn.

    ``text`` is what is shown; ``spoken``, set only when it differs, is what the speech engine
    says (numbers in words: «1.000» is shown, «mille» is said); ``tagged`` the spoken form with its
    speech tags (``[laughs]``); ``marks`` the answer's cues (``{name, arg?, at, spokenAt?}``). An
    empty ``text`` carries cues only.
    """

    type: Literal["say"] = "say"
    index: int
    text: str
    spoken: str | None = None
    tagged: str | None = None
    marks: list[dict[str, Any]] = Field(default_factory=list)
    filler: bool = False


class ToolEvent(BaseEvent):
    """A tool call started, finished or failed."""

    type: Literal["tool"] = "tool"
    name: str
    status: str
    """``start``, ``done`` or ``error``."""


class ChartEvent(BaseEvent):
    """A chart (``bar``, ``column``, ``line``, ``area``, ``donut``, ``stacked``, ``kpi``)."""

    type: Literal["chart"] = "chart"
    chart: dict[str, Any]


class DiagramEvent(BaseEvent):
    """A diagram to draw (``steps``, ``tree``, ``compare``)."""

    type: Literal["diagram"] = "diagram"
    diagram: dict[str, Any]


class AskEvent(BaseEvent):
    """A question with options; answer it by sending the chosen option as the next message."""

    type: Literal["ask"] = "ask"
    ask_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    multiple: bool = False
    other: bool = False


class NavigateEvent(BaseEvent):
    """Take the user to a page of the application."""

    type: Literal["navigate"] = "navigate"
    path: str


class ClientToolEvent(BaseEvent):
    """A custom client tool the agent called (``docs/client-tools.md``).

    With ``wait``, the turn waits until the client answers (``on_tool`` handlers do it, or call
    ``tool_result``); without, the model was already told ``ok``.
    """

    type: Literal["client_tool"] = "client_tool"
    call_id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    wait: bool = False


class ConnectEvent(BaseEvent):
    """A tool needs the person to connect a provider first (a card that starts the connection)."""

    type: Literal["connect"] = "connect"
    provider: str
    """``microsoft365``, ``google``, ``github``, ``linear``, ``notion`` or ``slack``."""
    title: str = ""
    server: str = ""


class ConfirmEvent(BaseEvent):
    """A call waits for the user's decision; the turn ends with ``done`` reason ``confirm``.

    ``why[i]`` says why ``items[i]`` waits: the tool changes data, the security policy, or an ask
    rule (with its label).
    """

    type: Literal["confirm"] = "confirm"
    action_id: str
    items: list[str] = Field(default_factory=list)
    why: list[ConfirmReason] = Field(default_factory=list)


class ConfirmedEvent(BaseEvent):
    """Stored on the user message that answered a confirmation (history only, never streamed).

    ``writes``: a tool that changes data ran (the question can no longer be rewritten).
    """

    type: Literal["confirmed"] = "confirmed"
    approve: bool
    writes: bool | None = None


class RewrittenEvent(BaseEvent):
    """The last question was rewritten: the first event of the new turn after ``conversation``.

    Drop what you show of the ``replaced`` turns (the question ``message_id`` and everything after
    it). With external storage ``replaced_from`` is the index of the sent history the new messages
    replace from. A replay never returns the replaced turns' events.
    """

    type: Literal["rewritten"] = "rewritten"
    replaced: list[str] = Field(default_factory=list)
    message_id: str | None = None
    replaced_from: int | None = None


class UsageEvent(BaseEvent):
    """Shares of the caps after a model call: percentages only, never amounts."""

    type: Literal["usage"] = "usage"
    day_pct: float | None = None
    week_pct: float | None = None
    month_pct: float | None = None
    company_pct: float | None = None
    month_out: bool = False
    out: bool = False
    """A cap is reached: nothing more until ``resets_at``."""
    binding: dict[str, Any] | None = None
    """The cap that limits most: limit, scope, period, usedPct, leftPct, resetsAt."""
    resets_at: str | None = None
    caps: list[dict[str, Any]] = Field(default_factory=list)


class ErrorEvent(BaseEvent):
    """A failure the user may see (``limit``, ``internal``, ``model``, ``empty``, ``guard``...).

    With code ``limit``, ``limit`` says which cap blocked the turn (``app_month``, ``app_week``,
    ``app_day``, ``subject_month``, ``subject_week``, ``subject_day``, or ``eval``), the message
    says when it resets and ``resets_at`` is that reset (an ISO time).
    """

    type: Literal["error"] = "error"
    code: str
    message: str = ""
    limit: str | None = None
    resets_at: str | None = None


class DoneEvent(BaseEvent):
    """The end of a turn, with its reason.

    Reasons: ``end_turn``, ``confirm``, ``ask``, ``limit``, ``max_steps``, ``cancelled``, ``empty``,
    ``expired``, ``model``, ``internal``, ``disconnected``, ``barge_in``, ``superseded``,
    ``closed``.
    """

    type: Literal["done"] = "done"
    conversation_id: str | None = None
    reason: str = ""
    messages: list[HistoryMessage] | None = None
    """External storage: this turn's new messages, to append to the application's copy."""
    replaced_from: int | None = None
    """External storage, a rewrite: drop the sent history from this index before appending."""
    rewritable: bool | None = None
    regenerable: bool | None = None
    """Whether the conversation's last question can still be rewritten."""


class SpeechEvent(BaseEvent):
    """Server voice: speech went off (provider failure); the session goes on in text."""

    type: Literal["speech"] = "speech"
    enabled: bool
    reason: str | None = None
    message: str | None = None


class UnknownEvent(BaseEvent):
    """An event this client does not know (or whose payload no longer fits its model).

    Every field is kept: read them from :attr:`data`.
    """

    type: str

    @property
    def data(self) -> dict[str, Any]:
        """The whole event as it arrived."""
        return self.model_dump(mode="json", exclude_none=False)


KnownEvent = Annotated[
    ConversationEvent
    | TextEvent
    | SayEvent
    | ToolEvent
    | ChartEvent
    | DiagramEvent
    | AskEvent
    | NavigateEvent
    | ConnectEvent
    | ConfirmEvent
    | ConfirmedEvent
    | UsageEvent
    | ErrorEvent
    | DoneEvent
    | SpeechEvent
    | RewrittenEvent
    | ClientToolEvent,
    Field(discriminator="type"),
]
"""Every event type the client knows, discriminated on ``type``."""

Event = (
    ConversationEvent
    | TextEvent
    | SayEvent
    | ToolEvent
    | ChartEvent
    | DiagramEvent
    | AskEvent
    | NavigateEvent
    | ConnectEvent
    | ConfirmEvent
    | ConfirmedEvent
    | UsageEvent
    | ErrorEvent
    | DoneEvent
    | SpeechEvent
    | RewrittenEvent
    | ClientToolEvent
    | UnknownEvent
)
"""What a stream, a replay or a stored message yields."""

_KNOWN: TypeAdapter[Any] = TypeAdapter(KnownEvent)
_TYPES = frozenset({
    "conversation",
    "text",
    "say",
    "tool",
    "chart",
    "diagram",
    "ask",
    "navigate",
    "connect",
    "confirm",
    "confirmed",
    "usage",
    "error",
    "done",
    "speech",
    "rewritten",
    "client_tool",
})


def parse_event(data: dict[str, Any], *, default_type: str | None = None) -> Event:
    """Parse one event.

    Args:
        data: The event's JSON object.
        default_type: The type to assume when the object has none (the SSE ``event:`` name).

    Returns:
        The typed event, or an :class:`UnknownEvent` for a type this client does not know or a
        payload that does not fit.
    """
    if "type" not in data and default_type:
        data = {**data, "type": default_type}
    kind = data.get("type")
    if isinstance(kind, str) and kind in _TYPES:
        try:
            return _KNOWN.validate_python(data)
        except ValidationError:
            pass
    if not isinstance(kind, str):
        data = {**data, "type": str(kind) if kind is not None else "unknown"}
    return UnknownEvent.model_validate(data)


def parse_events(items: Any) -> list[Event]:
    """Parse a list of events (already parsed ones are kept).

    Returns:
        The typed events.
    """
    if not isinstance(items, list):
        return []
    return [
        x if isinstance(x, BaseEvent) else parse_event(x)
        for x in items
        if isinstance(x, dict | BaseEvent)
    ]


Events = Annotated[list[Event], BeforeValidator(parse_events)]


class StoredMessage(WireModel):
    """A stored message of a conversation, with the events the user saw."""

    id: str
    role: Literal["user", "assistant"]
    text: str = ""
    events: Events = Field(default_factory=list)
    feedback: int | None = None
    interrupted: bool = False
    rewritable: bool | None = None
    regenerable: bool | None = None
    """User messages: the last question, which can still be rewritten (``session.rewrite``)."""


class ConversationDetail(WireModel):
    """A subject's conversation (``GET /v1/conversations/{id}``)."""

    id: str
    title: str = ""
    agent: str = ""
    messages: list[StoredMessage] = Field(default_factory=list)
    pending: PendingConfirmation | None = None


class SessionConversation(WireModel):
    """The session's conversation (``GET /v1/session/conversation``)."""

    messages: list[StoredMessage] = Field(default_factory=list)
    pending: PendingConfirmation | None = None
    last_seq: int = 0
    running_turn_id: str | None = None


class ChatResult(WireModel):
    """A whole turn, from ``chat_json`` (``Accept: application/json``)."""

    conversation_id: str
    turn_id: str
    reason: str
    text: str
    events: Events = Field(default_factory=list)
    messages: list[HistoryMessage] | None = None
    replaced_from: int | None = None
    """External storage, a rewrite: drop the sent history from this index before appending
    ``messages``."""
    rewritable: bool | None = None
    regenerable: bool | None = None
