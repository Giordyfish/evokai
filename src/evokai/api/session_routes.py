"""Browser API, with a session token: one subject, one agent, one conversation.

Event names and payloads are the clients' contract.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from evokai.agents.prompts import PageContext
from evokai.api.deps import services, session
from evokai.core.errors import AppError, Conflict, Forbidden, NotFound
from evokai.core.security import code_matches
from evokai.core.util import utcnow
from evokai.guard.ask import REASON_WRITES
from evokai.identity.models import SessionPrincipal
from evokai.memory.store import memory_view as view
from evokai.services import Services
from evokai.sessions.limits import cap_state
from evokai.sessions.rewrite import regenerable_index, rewritable_index
from evokai.sessions.threads import MAX_HISTORY_MESSAGES, HistoryMessage
from evokai.sessions.turns import TurnInput
from evokai.tools.custom import MAX_RESULT_CHARS

router = APIRouter(prefix="/v1/session", tags=["session"])
Svc = Annotated[Services, Depends(services)]
Sess = Annotated[SessionPrincipal, Depends(session)]


@router.get("")
async def info(svc: Svc, s: Sess) -> dict[str, Any]:
    """The session: conversation, agent, the subject's memories and usage shares.

    Returns:
        Session info.
    """
    c = s.claims
    app = await svc.store.applications.get(c.application_id)
    # After a handoff the conversation goes on with another agent than the token's.
    conv = await svc.store.sessions.get(s.who, c.conversation_id)
    agent = conv.agent_name if conv is not None else c.agent
    return {
        "conversationId": c.conversation_id,
        "agent": agent,
        "expiresAt": c.expires_at,
        "usage": (await cap_state(svc.store, app, s.who, utcnow(), c.limits)).view()
        if app
        else None,
        "memories": await svc.store.memories.facts(s.who),
        "memory": (await svc.memory.state(app, s.who)).view() if app else None,
        "suggestions": await _suggestions(svc, c.application_id, agent),
    }


async def _suggestions(svc: Services, application_id: str, agent: str) -> list[str]:
    """The agent's starter questions, or none when the agent is gone.

    Returns:
        The suggestions.
    """
    try:
        return (await svc.agents.active(application_id, agent)).suggestions
    except AppError:
        return []


@router.get("/conversation")
async def conversation(svc: Svc, s: Sess) -> dict[str, Any]:
    """The session's conversation (empty until the first message).

    Returns:
        Messages, the open confirmation, the last event number and the running turn.
    """
    sess = await svc.store.sessions.get(s.who, s.claims.conversation_id)
    if not sess:
        return {"messages": [], "pending": None, "lastSeq": 0, "runningTurnId": None}
    thread = await svc.store.sessions.thread(sess.id)
    msgs = await svc.store.messages.recent(s.who, thread.id, 400)
    pending = await svc.store.pending.open(s.who, sess.id)
    # A turn that already sent done is over for readers: its messages are stored (it may still be
    # persisting its last events for a moment).
    running = svc.turns.answering(sess.id)
    # The last question can be rewritten while nothing after it changed data and no turn runs.
    editable = None if running else rewritable_index(msgs)
    # The last answer can be regenerated under the same rule, unless a client tool acted.
    again = None if running else regenerable_index(msgs)
    last_answer = max((i for i, x in enumerate(msgs) if x.role == "assistant"), default=None)
    return {
        "messages": [
            {
                "id": x.id,
                "role": x.role,
                "text": x.text,
                "events": x.events or [],
                "feedback": x.feedback,
                "interrupted": x.interrupted,
                **({"rewritable": i == editable} if x.role == "user" else {}),
                **(
                    {"regenerable": True}
                    if again is not None and i == last_answer and i > again
                    else {}
                ),
            }
            for i, x in enumerate(msgs)
            if x.text or x.events
        ],
        "pending": {
            "actionId": pending.id,
            "items": [c["label"] for c in pending.state["calls"]],
            "why": [c.get("why") or {"key": REASON_WRITES} for c in pending.state["calls"]],
        }
        if pending
        else None,
        "lastSeq": sess.last_seq,
        "runningTurnId": running.turn_id if running else None,
    }


@router.get("/events")
async def events(svc: Svc, s: Sess, after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    """Replay after a reconnect: every event with seq > after, in order.

    Returns:
        Events.
    """
    sess = await svc.store.sessions.get(s.who, s.claims.conversation_id)
    if not sess:
        return {"events": []}
    app = await svc.store.applications.get(s.claims.application_id)
    storage = sess.storage_mode or (app.storage_mode if app else "evokai")
    channel = await svc.hub.get(sess.id, persist=storage == "evokai")
    return {"events": await channel.replay(after)}


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: str, svc: Svc, s: Sess) -> dict[str, bool]:
    """Barge-in or "stop": cancels that turn only. Idempotent.

    Returns:
        Whether a running turn was cancelled.
    """
    # A session token is bound to one conversation: it cancels that conversation's turns only.
    cancelled = svc.turns.cancel(
        turn_id, s.claims.subject, "cancelled", session_id=s.claims.conversation_id
    )
    return {"ok": True, "cancelled": cancelled}


class ToolResultBody(BaseModel):
    """The client's answer to a ``client_tool`` call that waits."""

    callId: str = Field(min_length=1, max_length=200)
    result: Any = None
    """Any JSON value (at most 32 KB as JSON). It is data the browser controls: scanned like a
    tool's output and marked as data for the model, never an instruction."""
    error: str | None = Field(default=None, max_length=2_000)
    """Instead of a result: why the client could not do it (the model reads it)."""


@router.post("/tool-results", responses={404: {}, 413: {}})
async def tool_result(body: ToolResultBody, svc: Svc, s: Sess) -> dict[str, bool]:
    """Answer a custom client tool call the running turn waits for (once).

    Returns:
        ok.

    Raises:
        NotFound: No call of this session waits under that id here (answered, timed out, or the
            turn runs on another task) (``unknown_call``).
        AppError: The result is larger than 32 KB (413 ``too_large``).
    """
    if body.error is None and len(json.dumps(body.result, default=str)) > MAX_RESULT_CHARS:
        raise AppError("The result is larger than 32 KB.", code="too_large", status=413)
    answered = await svc.engine.client_calls.answer(
        s.claims.conversation_id, body.callId, body.result, body.error
    )
    if not answered:
        raise NotFound("No client call waits under this id.", code="unknown_call")
    return {"ok": True}


class Feedback(BaseModel):
    """Thumbs on a message."""

    value: int | None = None


@router.post("/messages/{message_id}/feedback")
async def feedback(message_id: str, body: Feedback, svc: Svc, s: Sess) -> dict[str, bool]:
    """Thumbs up (1), down (-1) or none.

    Returns:
        ok.

    Raises:
        NotFound: Not a message of this subject.
    """
    value = body.value if body.value in {1, -1} else None
    if not await svc.store.messages.set_feedback(s.who, message_id, value):
        raise NotFound()
    await svc.store.traces.feedback(s.who, message_id, value)
    return {"ok": True}


class Confirm(BaseModel):
    """The user's decision on a confirmation."""

    actionId: str
    approve: bool


class Rewrite(BaseModel):
    """Rewrite the last question: ``text`` replaces it and everything after it."""

    messageId: str | None = None
    """The message being edited (Evokai storage): refused when it is no longer the last question.
    Left out: the last question. External storage has no ids: the last question of ``history``."""


class ChatBody(BaseModel):
    """A user input."""

    text: str | None = Field(default=None, max_length=8_000)
    confirm: Confirm | None = None
    rewrite: Rewrite | None = None
    """With ``text``: replace the last question (409 ``rewrite_after_write`` when its turn changed
    data, ``rewrite_not_last``, ``rewrite_nothing``, ``rewrite_running``)."""
    page: PageContext | None = None
    mode: Literal["text", "voice"] = "text"
    history: list[HistoryMessage] | None = Field(default=None, max_length=MAX_HISTORY_MESSAGES)
    """External storage only: the conversation so far (oldest first)."""
    endOfSpeechAt: float | None = None


class ChatResult(BaseModel):
    """A whole turn, for clients that ask for JSON instead of a stream."""

    conversationId: str
    turnId: str
    reason: str
    """end_turn, confirm, ask, limit, max_steps, cancelled, empty, expired, model, internal."""
    text: str
    """The assistant's text of this turn."""
    events: list[dict[str, Any]]
    """Every event of the turn, in order (same payloads as the stream)."""
    messages: list[HistoryMessage] | None = None
    """External storage: the new messages, to append to the application's copy."""
    replacedFrom: int | None = None
    """External storage, a rewrite: drop the sent history from this index before appending
    ``messages``."""
    rewritable: bool | None = None
    """Whether the conversation's last question can still be rewritten."""
    regenerable: bool | None = None
    """Whether the conversation's last answer can still be regenerated."""


def sse(event: dict[str, Any]) -> str:
    """Format an event for server-sent events.

    Returns:
        The SSE frame.
    """
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post(
    "/chat",
    response_model=ChatResult,
    responses={200: {"content": {"text/event-stream": {}, "application/json": {}}}},
)
async def chat(
    body: ChatBody, svc: Svc, s: Sess, request: Request
) -> StreamingResponse | ChatResult:
    """Run a turn.

    Streams its events (text/event-stream, the default; closing the stream cancels the turn), or,
    with ``Accept: application/json``, waits for the end and returns the whole turn.

    Returns:
        The event stream, or the turn as JSON.
    """
    inp = TurnInput(
        principal=s,
        text=body.text,
        confirm=body.confirm.model_dump() if body.confirm else None,
        page=body.page,
        mode=body.mode,
        history=body.history,
        end_of_speech_at=body.endOfSpeechAt,
        rewrite=body.rewrite.model_dump() if body.rewrite else None,
    )
    return await _run_turn(svc, inp, request)


async def _run_turn(
    svc: Services, inp: TurnInput, request: Request
) -> StreamingResponse | ChatResult:
    """Open and run a turn: its event stream, or the whole turn as JSON.

    Returns:
        The response.
    """
    accept = request.headers.get("accept", "")
    as_json = "application/json" in accept and "text/event-stream" not in accept
    if as_json:
        # The client sees the events only at the end: it cannot answer a client tool that waits.
        inp.client_wait = False
    run = await svc.engine.open(inp)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_event(e: dict[str, Any]) -> None:
        if e.get("turnId") == run.turn_id:
            queue.put_nowait(e)

    stop_listening = run.channel.listen(on_event)
    task = asyncio.create_task(svc.engine.execute(run, inp))

    if as_json:
        try:
            await task
        finally:
            stop_listening()
        events: list[dict[str, Any]] = []
        while not queue.empty():
            events.append(queue.get_nowait())
        done = next((e for e in reversed(events) if e["type"] == "done"), {})
        return ChatResult(
            conversationId=run.session.id,
            turnId=run.turn_id,
            reason=str(done.get("reason", "")),
            text="".join(e.get("delta", "") for e in events if e["type"] == "text"),
            events=events,
            messages=done.get("messages"),
            replacedFrom=done.get("replacedFrom"),
            rewritable=done.get("rewritable"),
            regenerable=done.get("regenerable"),
        )

    async def stream() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    e = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield sse(e)
                if e["type"] == "done":
                    return
        finally:
            stop_listening()
            if not task.done():
                # The client went away (closed the panel, pressed stop): stop this turn only.
                run.handle.cancel("disconnected")
            svc.hub.drop_if_idle(run.session.id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Turn-Id": run.turn_id},
    )


class RegenerateBody(BaseModel):
    """Regenerate the last answer (the question is the conversation's own)."""

    page: PageContext | None = None
    mode: Literal["text", "voice"] = "text"
    history: list[HistoryMessage] | None = Field(default=None, max_length=MAX_HISTORY_MESSAGES)
    """External storage only: the conversation so far (oldest first), with the last question and
    the answer to replace."""
    endOfSpeechAt: float | None = None


@router.post(
    "/regenerate",
    response_model=ChatResult,
    responses={200: {"content": {"text/event-stream": {}, "application/json": {}}}, 409: {}},
)
async def regenerate(
    body: RegenerateBody, svc: Svc, s: Sess, request: Request
) -> StreamingResponse | ChatResult:
    """Regenerate the last answer: the last question runs again and replaces the last turn.

    Like a rewrite with the same question (a ``rewritten`` event, then the new answer), with the
    same caps, guard and traces; the replaced answer stays in its turn's trace. Refused with 409
    ``not_regenerable`` when there is nothing to regenerate, the last turn changed data or ran a
    custom client tool, or a turn is running.

    Returns:
        The event stream, or the turn as JSON (``Accept: application/json``).
    """
    inp = TurnInput(
        principal=s,
        page=body.page,
        mode=body.mode,
        history=body.history,
        end_of_speech_at=body.endOfSpeechAt,
        regenerate=True,
    )
    return await _run_turn(svc, inp, request)


class ExitBody(BaseModel):
    """The code that closes a kiosk session."""

    code: str = Field(min_length=1, max_length=64)


MAX_EXIT_ATTEMPTS = 5
BUG_REPORTS_PER_HOUR = 20


@router.post("/exit", status_code=204, responses={403: {}, 404: {}, 409: {}, 429: {}})
async def exit_session(body: ExitBody, svc: Svc, s: Sess) -> None:
    """Close a kiosk session with its exit code.

    Raises:
        NotFound: The session has no exit code.
        Conflict: Already closed.
        Forbidden: Wrong code.
        AppError: Too many attempts (429).
    """
    sess = await svc.store.sessions.get(s.who, s.claims.conversation_id)
    if not sess or not sess.exit_code_hash:
        raise NotFound("This session has no exit code.", code="no_exit_code")
    if sess.status == "closed":
        raise Conflict("This session is already closed.", code="closed")
    if sess.exit_attempts >= MAX_EXIT_ATTEMPTS:
        raise AppError("Too many attempts.", code="too_many_attempts", status=429)
    ok = code_matches(body.code, sess.exit_code_hash)
    await svc.store.sessions.record_exit_attempt(sess.id, close=ok)
    if not ok:
        raise Forbidden("Codice errato.", code="wrong_code")
    running = svc.turns.running(sess.id)
    if running:
        running.cancel("closed")


class BugReportBody(BaseModel):
    """A problem the user reports, with the context the client chooses to send."""

    description: str = Field(min_length=3, max_length=4_000)
    context: dict[str, Any] = Field(default_factory=dict)


@router.post("/bug-reports", status_code=201)
async def report_bug(body: BugReportBody, svc: Svc, s: Sess) -> dict[str, str]:
    """Report a problem with this session.

    Returns:
        The report id.

    Raises:
        AppError: Too many reports from this user in the last hour (429).
    """
    if not await svc.auth.count(
        f"bug:{s.claims.application_id}:{s.claims.subject}", BUG_REPORTS_PER_HOUR, 3600
    ):
        raise AppError("Too many reports. Try again later.", code="rate_limited", status=429)
    context = (
        body.context
        if len(json.dumps(body.context, default=str)) <= 20_000
        else {"truncated": True}
    )
    report_id = await svc.store.bug_reports.add(
        s.who, s.claims.conversation_id, body.description, context
    )
    return {"id": report_id}


# ── memory: what the agents remember about the person, and their consent ─────────────────────────


async def _memory_app(svc: Services, s: SessionPrincipal) -> Any:
    app = await svc.store.applications.get(s.claims.application_id)
    if app is None:
        raise NotFound()
    return app


@router.get("/memories")
async def list_memories(svc: Svc, s: Sess) -> dict[str, Any]:
    """What the agents remember about the person, and whether memory is on for them.

    Returns:
        ``memory`` (mode, consent, active, ask) and ``items`` (id, text, kind, agent, source,
        dates, uses), oldest first.
    """
    app = await _memory_app(svc, s)
    state = await svc.memory.state(app, s.who)
    return {
        "memory": state.view(),
        "items": [view(x) for x in await svc.store.memories.items(s.who)],
    }


class MemoryConsentBody(BaseModel):
    """The person's answer to «may the assistant remember things about you?»."""

    granted: bool


@router.post("/memory-consent")
async def memory_consent(body: MemoryConsentBody, svc: Svc, s: Sess) -> dict[str, Any]:
    """Record the person's answer. A no, or withdrawing a yes, deletes what was remembered.

    Returns:
        ``memory`` (the new state) and ``deleted`` (memories deleted).

    Raises:
        Conflict: Memory is off for this application.
    """
    app = await _memory_app(svc, s)
    if (await svc.memory.state(app, s.who)).mode == "off":
        raise Conflict("Memory is off for this application.", code="memory_off")
    deleted = await svc.memory.set_consent(app, s.who, body.granted, "person")
    return {"memory": (await svc.memory.state(app, s.who)).view(), "deleted": deleted}


class MemoryEdit(BaseModel):
    """A memory rewritten by the person."""

    text: str = Field(min_length=1, max_length=600)


@router.patch("/memories/{memory_id}")
async def edit_memory(memory_id: str, body: MemoryEdit, svc: Svc, s: Sess) -> dict[str, Any]:
    """Rewrite one memory. The guard's ``memory.write`` applies as to the agent's own writes.

    Returns:
        The memory.

    Raises:
        Forbidden: Memory is not on for the person.
        AppError: The guard refused the text (400 ``memory_refused``).
    """
    app = await _memory_app(svc, s)
    if not (await svc.memory.state(app, s.who)).active:
        raise Forbidden("Memory is not on for this person.", code="memory_inactive")
    agent = await svc.agents.active(app.id, s.claims.agent)
    guard = svc.guard.turn(
        app,
        agent,
        subject=s.claims.subject,
        session_id=s.claims.conversation_id,
        turn_id=None,
        channel="browser",
    )
    allowed = guard.memory(body.text)
    await guard.flush()
    if not allowed:
        raise AppError(
            "A memory may not hold instructions, secrets or personal data.",
            code="memory_refused",
            status=400,
        )
    return view(await svc.memory.edit(app, s.who, memory_id, body.text))


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str, svc: Svc, s: Sess) -> dict[str, int]:
    """Delete one memory.

    Returns:
        deleted.

    Raises:
        NotFound: Not one of the person's memories.
    """
    deleted = await svc.store.memories.forget(s.who, memory_id)
    if not deleted:
        raise NotFound("Unknown memory.", code="unknown_memory")
    return {"deleted": deleted}


@router.delete("/memories")
async def delete_memories(svc: Svc, s: Sess) -> dict[str, int]:
    """Delete everything the agents remember about the person (consent stays as it is).

    Returns:
        deleted.
    """
    return {"deleted": await svc.store.memories.forget(s.who)}
