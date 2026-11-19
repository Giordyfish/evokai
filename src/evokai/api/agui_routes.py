"""AG-UI (Agent User Interaction Protocol): a session's turns as AG-UI events.

``POST /v1/session/agui`` takes AG-UI's ``RunAgentInput`` and streams AG-UI events over SSE, so a
CopilotKit (or any AG-UI) frontend can talk to an Evokai agent. It is the session chat with another
event vocabulary: the same session token, origin check, rate limit, turn engine, grants, guard and
caps, and closing the stream cancels the turn.

Mapping (see ``docs/ag-ui.md``):

- the input is the last user message of ``messages`` (Evokai storage keeps the conversation; with
  external storage the earlier messages, or ``forwardedProps.evokai.history``, are the history);
  ``context`` becomes the page the user is looking at, unless ``forwardedProps.evokai.page`` gives
  one; client-defined ``tools`` and ``state`` are not used;
- ``text`` → ``TEXT_MESSAGE_START/CONTENT/END``; ``tool`` → ``TOOL_CALL_START/END/RESULT`` (the
  result carries the status, never the tool's output);
- ``confirm`` and ``ask`` end the run with an **interrupt** (``RUN_FINISHED`` with
  ``outcome.type = "interrupt"``): a confirmation is ``reason: "confirmation"`` with
  ``responseSchema {approved: boolean}``, a question ``reason: "input_required"`` with
  ``responseSchema {answer}``; the client resumes with ``resume: [{interruptId, status, payload}]``.
  A ``CUSTOM`` event (``evokai.confirm``, ``evokai.ask``) carries the same payload for frontends
  that render human-in-the-loop with their own components;
- ``chart``, ``diagram``, ``navigate``, ``say``, ``speech``, ``citation`` (a knowledge base passage
  the answer cites) → ``CUSTOM`` (``evokai.<type>``);
- ``usage`` → ``CUSTOM evokai.usage`` and a ``STATE_DELTA`` of ``/evokai/usage`` (shares of the
  caps, never amounts); the run starts with a ``STATE_SNAPSHOT`` of ``{evokai: {conversationId,
  agent, turnId}}``;
- ``error`` → ``RUN_ERROR`` (the run's last event); ``done`` → ``RUN_FINISHED`` with
  ``result: {reason, conversationId, messages?}``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evokai.agents.prompts import PageContext
from evokai.api.deps import services, session
from evokai.core.errors import BadRequest
from evokai.identity.models import SessionPrincipal
from evokai.services import Services
from evokai.sessions.threads import MAX_HISTORY_MESSAGES, HistoryMessage
from evokai.sessions.turns import TurnInput

router = APIRouter(prefix="/v1/session", tags=["session"])
Svc = Annotated[Services, Depends(services)]
Sess = Annotated[SessionPrincipal, Depends(session)]

CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {"approved": {"type": "boolean"}},
    "required": ["approved"],
}
CUSTOM_EVENTS = ("chart", "diagram", "navigate", "say", "speech", "citation")


class AguiMessage(BaseModel):
    """A message of AG-UI's ``RunAgentInput`` (only user and assistant text is read)."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    role: str
    content: str | list[dict[str, Any]] | None = None


class AguiContext(BaseModel):
    """A piece of context the frontend shares."""

    description: str = Field(max_length=500)
    value: str = Field(max_length=20_000)


class ResumeEntry(BaseModel):
    """The answer to one interrupt of the previous run."""

    model_config = ConfigDict(extra="allow")

    interruptId: str = Field(max_length=200)
    status: Literal["resolved", "cancelled"]
    payload: Any = None


class RunAgentInput(BaseModel):
    """AG-UI's run input."""

    model_config = ConfigDict(extra="allow")

    threadId: str = Field(max_length=200)
    runId: str = Field(max_length=200)
    parentRunId: str | None = None
    messages: list[AguiMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    context: list[AguiContext] = Field(default_factory=list, max_length=50)
    state: Any = None
    forwardedProps: Any = None
    resume: list[ResumeEntry] | None = None


def _text(msg: AguiMessage) -> str:
    """The text of a message (text parts only).

    Returns:
        The text.
    """
    if isinstance(msg.content, str):
        return msg.content
    return "\n".join(
        str(p.get("text") or "") for p in msg.content or [] if p.get("type") == "text"
    ).strip()


def _evokai_props(body: RunAgentInput) -> dict[str, Any]:
    props = body.forwardedProps if isinstance(body.forwardedProps, dict) else {}
    ev = props.get("evokai")
    return ev if isinstance(ev, dict) else {}


def turn_input(body: RunAgentInput, principal: SessionPrincipal) -> TurnInput:
    """The Evokai turn of an AG-UI run.

    Returns:
        The turn input.

    Raises:
        BadRequest: No user message and nothing to resume, or a malformed ``forwardedProps``.
    """
    props = _evokai_props(body)
    try:
        page = PageContext.model_validate(props["page"]) if props.get("page") else None
        history = (
            [HistoryMessage.model_validate(h) for h in props["history"]]
            if isinstance(props.get("history"), list)
            else None
        )
    except ValidationError as e:
        raise BadRequest("Invalid forwardedProps.evokai.", code="bad_request") from e
    if page is None and body.context:
        page = PageContext(
            path="(frontend context)",
            text="\n".join(f"{c.description}: {c.value}" for c in body.context),
        )
    convo = [
        (m.role, _text(m)) for m in body.messages if m.role in {"user", "assistant"} and _text(m)
    ]
    text: str | None = None
    confirm: dict[str, Any] | None = None
    for r in body.resume or []:
        if r.interruptId.startswith("ask_"):
            answer = r.payload.get("answer") if isinstance(r.payload, dict) else r.payload
            if r.status == "resolved" and answer:
                text = ", ".join(map(str, answer)) if isinstance(answer, list) else str(answer)
        else:
            approved = isinstance(r.payload, dict) and r.payload.get("approved") is True
            confirm = {"actionId": r.interruptId, "approve": r.status == "resolved" and approved}
    if confirm is None and text is None:
        if not convo or convo[-1][0] != "user":
            raise BadRequest("Send a user message or resume the open interrupt.", code="empty")
        text = convo[-1][1][:8_000]
        convo = convo[:-1]
    if history is None:
        history = [HistoryMessage(role=role, content=[{"text": t}], text=t) for role, t in convo]  # ty: ignore[invalid-argument-type]
    return TurnInput(
        principal=principal,
        text=text if confirm is None else None,
        confirm=confirm,
        page=page,
        mode="text",
        history=history[-MAX_HISTORY_MESSAGES:],
    )


class Translator:
    """Evokai events of one turn → AG-UI events."""

    def __init__(self, thread_id: str, run_id: str) -> None:
        """Start a run.

        Args:
            thread_id: The client's thread.
            run_id: The client's run.
        """
        self.thread_id, self.run_id = thread_id, run_id
        self.turn_id = ""
        self.text_id: str | None = None
        self.last_text_id: str | None = None
        self.messages = 0
        self.tools: dict[str, str] = {}
        """Open tool calls: name → toolCallId (the engine runs a turn's tools one by one)."""
        self.calls = 0
        self.interrupts: list[dict[str, Any]] = []
        self.ended = False

    def start(self, conversation_id: str, agent: str, turn_id: str) -> list[dict[str, Any]]:
        """The run's first events.

        Returns:
            RUN_STARTED and the state snapshot.
        """
        self.turn_id = turn_id
        return [
            {"type": "RUN_STARTED", "threadId": self.thread_id, "runId": self.run_id},
            {
                "type": "STATE_SNAPSHOT",
                "snapshot": {
                    "evokai": {"conversationId": conversation_id, "agent": agent, "turnId": turn_id}
                },
            },
        ]

    def _close_text(self) -> list[dict[str, Any]]:
        if self.text_id is None:
            return []
        out = [{"type": "TEXT_MESSAGE_END", "messageId": self.text_id}]
        self.text_id = None
        return out

    def event(self, e: dict[str, Any]) -> list[dict[str, Any]]:
        """Translate one Evokai event.

        Returns:
            The AG-UI events (none for events AG-UI has no use for).
        """
        if self.ended:
            return []
        kind = e.get("type")
        if kind == "text":
            delta = str(e.get("delta") or "")
            if not delta:
                return []
            out: list[dict[str, Any]] = []
            if self.text_id is None:
                self.messages += 1
                self.text_id = self.last_text_id = f"{self.turn_id}-m{self.messages}"
                out.append({
                    "type": "TEXT_MESSAGE_START",
                    "messageId": self.text_id,
                    "role": "assistant",
                })
            out.append({"type": "TEXT_MESSAGE_CONTENT", "messageId": self.text_id, "delta": delta})
            return out
        if kind == "tool":
            name = str(e.get("name") or "")
            if e.get("status") == "start":
                self.calls += 1
                call_id = self.tools[name] = f"{self.turn_id}-t{self.calls}"
                start: dict[str, Any] = {
                    "type": "TOOL_CALL_START",
                    "toolCallId": call_id,
                    "toolCallName": name,
                }
                if self.last_text_id:
                    start["parentMessageId"] = self.last_text_id
                return [
                    *self._close_text(),
                    start,
                    {"type": "TOOL_CALL_END", "toolCallId": call_id},
                ]
            call_id = self.tools.pop(name, None)
            if call_id is None:
                return []
            return [
                {
                    "type": "TOOL_CALL_RESULT",
                    "messageId": f"{call_id}-result",
                    "toolCallId": call_id,
                    "content": json.dumps({"status": e.get("status")}),
                    "role": "tool",
                }
            ]
        if kind == "confirm":
            items = [str(x) for x in e.get("items") or []]
            self.interrupts.append({
                "id": e["actionId"],
                "reason": "confirmation",
                "message": "; ".join(items),
                "responseSchema": CONFIRM_SCHEMA,
                # why[i]: why items[i] waits (a change, the policy, an ask rule with its label).
                "metadata": {"items": items, **({"why": e["why"]} if e.get("why") else {})},
            })
            return [
                *self._close_text(),
                {"type": "CUSTOM", "name": "evokai.confirm", "value": _payload(e)},
            ]
        if kind == "ask":
            options = [str(x) for x in e.get("options") or []]
            answer: dict[str, Any] = {"type": "string"}
            if options and not e.get("other"):
                answer["enum"] = options
            if e.get("multiple"):
                answer = {"type": "array", "items": answer}
            self.interrupts.append({
                "id": e["askId"],
                "reason": "input_required",
                "message": str(e.get("question") or ""),
                "responseSchema": {
                    "type": "object",
                    "properties": {"answer": answer},
                    "required": ["answer"],
                },
                "metadata": {
                    "options": options,
                    "multiple": bool(e.get("multiple")),
                    "other": bool(e.get("other")),
                },
            })
            return [
                *self._close_text(),
                {"type": "CUSTOM", "name": "evokai.ask", "value": _payload(e)},
            ]
        if kind == "usage":
            shares = _payload(e)
            return [
                {"type": "CUSTOM", "name": "evokai.usage", "value": shares},
                {
                    "type": "STATE_DELTA",
                    "delta": [{"op": "add", "path": "/evokai/usage", "value": shares}],
                },
            ]
        if kind in CUSTOM_EVENTS:
            return [{"type": "CUSTOM", "name": f"evokai.{kind}", "value": _payload(e)}]
        if kind == "error":
            self.ended = True
            return [
                *self._close_text(),
                {
                    "type": "RUN_ERROR",
                    "message": str(e.get("message") or "Something went wrong."),
                    "code": str(e.get("code") or "internal"),
                },
            ]
        if kind == "done":
            self.ended = True
            finished: dict[str, Any] = {
                "type": "RUN_FINISHED",
                "threadId": self.thread_id,
                "runId": self.run_id,
                "result": {
                    "reason": e.get("reason"),
                    "conversationId": e.get("conversationId"),
                    **({"messages": e["messages"]} if e.get("messages") is not None else {}),
                },
            }
            if self.interrupts and e.get("reason") in {"confirm", "ask"}:
                finished["outcome"] = {"type": "interrupt", "interrupts": self.interrupts}
            else:
                finished["outcome"] = {"type": "success"}
            return [*self._close_text(), finished]
        return []


def _payload(e: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if k not in {"type", "seq"}}


def frame(event: dict[str, Any]) -> str:
    """An AG-UI event as a server-sent event (AG-UI's SSE encoding: data lines only).

    Returns:
        The frame.
    """
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/agui", responses={200: {"content": {"text/event-stream": {}}}})
async def agui(body: RunAgentInput, svc: Svc, s: Sess) -> StreamingResponse:
    """Run a turn as an AG-UI run: ``RunAgentInput`` in, AG-UI events out (SSE).

    Returns:
        The event stream.
    """
    inp = turn_input(body, s)
    run = await svc.engine.open(inp)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_event(e: dict[str, Any]) -> None:
        if e.get("turnId") == run.turn_id:
            queue.put_nowait(e)

    stop_listening = run.channel.listen(on_event)
    task = asyncio.create_task(svc.engine.execute(run, inp))
    tr = Translator(body.threadId, body.runId)

    async def stream() -> AsyncIterator[str]:
        try:
            for x in tr.start(run.session.id, run.agent.name, run.turn_id):
                yield frame(x)
            while True:
                try:
                    e = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                for x in tr.event(e):
                    yield frame(x)
                if e["type"] == "done":
                    return
        finally:
            stop_listening()
            if not task.done():
                # The client went away: stop this turn only.
                run.handle.cancel("disconnected")
            svc.hub.drop_if_idle(run.session.id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Turn-Id": run.turn_id},
    )
