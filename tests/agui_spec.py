"""AG-UI event schema, vendored from the protocol's TypeScript types (``@ag-ui/core``,
https://docs.ag-ui.com/sdk/js/core/events, interrupts from https://docs.ag-ui.com/concepts/interrupts)
as Pydantic models that refuse unknown fields, plus the ordering rules ``@ag-ui/client`` verifies.

Kept in tests on purpose: the service writes plain dicts, and this is the independent check that
what it writes is AG-UI.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class BaseEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: int | None = None
    rawEvent: Any = None
    metadata: dict[str, Any] | None = None


class Interrupt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    reason: str
    message: str | None = None
    toolCallId: str | None = None
    responseSchema: dict[str, Any] | None = None
    expiresAt: str | None = None
    metadata: dict[str, Any] | None = None
    subagentRunId: str | None = None


class SuccessOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["success"]


class InterruptOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["interrupt"]
    interrupts: list[Interrupt] = Field(min_length=1)


class RunStarted(BaseEvent):
    type: Literal["RUN_STARTED"]
    threadId: str
    runId: str
    parentRunId: str | None = None
    input: Any = None


class RunFinished(BaseEvent):
    type: Literal["RUN_FINISHED"]
    threadId: str
    runId: str
    result: Any = None
    outcome: Annotated[SuccessOutcome | InterruptOutcome, Field(discriminator="type")] | None = None
    usage: list[Any] | None = None


class RunError(BaseEvent):
    type: Literal["RUN_ERROR"]
    message: str
    code: str | None = None
    usage: list[Any] | None = None


class TextMessageStart(BaseEvent):
    type: Literal["TEXT_MESSAGE_START"]
    messageId: str
    role: Literal["assistant"]


class TextMessageContent(BaseEvent):
    type: Literal["TEXT_MESSAGE_CONTENT"]
    messageId: str
    delta: str = Field(min_length=1)


class TextMessageEnd(BaseEvent):
    type: Literal["TEXT_MESSAGE_END"]
    messageId: str


class ToolCallStart(BaseEvent):
    type: Literal["TOOL_CALL_START"]
    toolCallId: str
    toolCallName: str
    parentMessageId: str | None = None


class ToolCallArgs(BaseEvent):
    type: Literal["TOOL_CALL_ARGS"]
    toolCallId: str
    delta: str


class ToolCallEnd(BaseEvent):
    type: Literal["TOOL_CALL_END"]
    toolCallId: str


class ToolCallResult(BaseEvent):
    type: Literal["TOOL_CALL_RESULT"]
    messageId: str
    toolCallId: str
    content: str
    role: Literal["tool"] | None = None


class StateSnapshot(BaseEvent):
    type: Literal["STATE_SNAPSHOT"]
    snapshot: Any


class JsonPatchOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str
    value: Any = None
    from_: str | None = Field(default=None, alias="from")


class StateDelta(BaseEvent):
    type: Literal["STATE_DELTA"]
    delta: list[JsonPatchOp]


class Custom(BaseEvent):
    type: Literal["CUSTOM"]
    name: str
    value: Any


Event = Annotated[
    RunStarted
    | RunFinished
    | RunError
    | TextMessageStart
    | TextMessageContent
    | TextMessageEnd
    | ToolCallStart
    | ToolCallArgs
    | ToolCallEnd
    | ToolCallResult
    | StateSnapshot
    | StateDelta
    | Custom,
    Field(discriminator="type"),
]
EVENTS: TypeAdapter[list[Event]] = TypeAdapter(list[Event])


def check(events: list[dict[str, Any]]) -> list[Any]:
    """Validate every event against the schema and the ordering rules of a run.

    Returns:
        The parsed events.
    """
    parsed = EVENTS.validate_python(events)
    assert parsed and parsed[0].type == "RUN_STARTED", "a run starts with RUN_STARTED"
    assert parsed[-1].type in {"RUN_FINISHED", "RUN_ERROR"}, "a run ends with RUN_FINISHED/ERROR"
    assert sum(e.type in {"RUN_FINISHED", "RUN_ERROR"} for e in parsed) == 1
    open_text: str | None = None
    open_tools: set[str] = set()
    ended_tools: set[str] = set()
    for e in parsed:
        if isinstance(e, TextMessageStart):
            assert open_text is None, "one text message at a time"
            open_text = e.messageId
        elif isinstance(e, TextMessageContent | TextMessageEnd):
            assert open_text == e.messageId, "content and end belong to the open message"
            if isinstance(e, TextMessageEnd):
                open_text = None
        elif isinstance(e, ToolCallStart):
            assert e.toolCallId not in open_tools | ended_tools
            open_tools.add(e.toolCallId)
        elif isinstance(e, ToolCallArgs | ToolCallEnd):
            assert e.toolCallId in open_tools
            if isinstance(e, ToolCallEnd):
                open_tools.discard(e.toolCallId)
                ended_tools.add(e.toolCallId)
        elif isinstance(e, ToolCallResult):
            assert e.toolCallId in ended_tools, "a result follows its call's end"
        elif isinstance(e, RunFinished | RunError):
            assert open_text is None and not open_tools, "everything is closed at the end"
    return parsed
