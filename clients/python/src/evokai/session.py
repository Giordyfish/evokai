"""A session: one subject, one agent, one conversation, authorised by a session token.

Chats stream as server-sent events parsed into typed events. A stream that breaks before the
turn's ``done`` is completed from the replay endpoint (``GET /v1/session/events?after=seq``).
Closing a stream early (leaving the ``with`` block, ``break``, ``close()``) closes the connection,
and the service cancels that turn.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Generator,
    Iterator,
    Mapping,
    Sequence,
)
from types import TracebackType
from typing import Any, Literal, NoReturn

import httpx
from typing_extensions import Self

from evokai import _calls as calls
from evokai._http import AsyncTransport, SyncTransport
from evokai.errors import EvokaiConnectionError, EvokaiError
from evokai.events import (
    ChatResult,
    ClientToolEvent,
    DoneEvent,
    Event,
    SessionConversation,
    TextEvent,
    parse_event,
)
from evokai.models import (
    Connection,
    ConnectionStarted,
    HistoryMessage,
    Memory,
    MemoryConsentResult,
    MemoryList,
    PageContext,
    SessionInfo,
    SessionToken,
)
from evokai.sse import SseMessage, SseParser

__all__ = ["AsyncChatStream", "AsyncEvokaiSession", "ChatMode", "ChatStream", "EvokaiSession"]

ChatMode = Literal["text", "voice"]
CHAT_PATH = "/v1/session/chat"
REGENERATE_PATH = "/v1/session/regenerate"


def chat_body(
    *,
    text: str | None = None,
    confirm: tuple[str, bool] | None = None,
    page: PageContext | Mapping[str, Any] | None = None,
    mode: ChatMode = "text",
    history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    end_of_speech_at: float | None = None,
    rewrite: bool | str = False,
) -> dict[str, Any]:
    """The body of ``POST /v1/session/chat``.

    ``rewrite``: replace the last question with ``text`` (True), or name the stored message being
    edited (its id; the service refuses it when it is no longer the last question).

    Returns:
        The JSON body.
    """
    out: dict[str, Any] = {"mode": mode}
    if confirm is not None:
        out["confirm"] = {"actionId": confirm[0], "approve": confirm[1]}
    elif text is not None:
        out["text"] = text
    if page is not None:
        p = page if isinstance(page, PageContext) else PageContext.model_validate(page)
        out["page"] = p.model_dump(mode="json", exclude_none=True)
    if history is not None:
        out["history"] = [calls.body(HistoryMessage, m) for m in history]
    if end_of_speech_at is not None:
        out["endOfSpeechAt"] = end_of_speech_at
    if rewrite and confirm is None:
        out["rewrite"] = {"messageId": rewrite} if isinstance(rewrite, str) else {}
    return out


def _event_of(msg: SseMessage) -> Event | None:
    if not msg.data:
        return None
    try:
        data = json.loads(msg.data)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return parse_event(data, default_type=None if msg.event == "message" else msg.event)


def _incomplete(turn_id: str | None) -> NoReturn:
    """Raise the error of a turn whose stream ended before its ``done`` event."""
    raise EvokaiConnectionError(
        0,
        "incomplete_turn",
        f"The stream of turn {turn_id or '(unknown)'} ended before its done event.",
    )


class _StreamState:
    """What a stream has seen so far (shared by the sync and async streams)."""

    def __init__(self, after: int) -> None:
        self.turn_id: str | None = None
        self.done: DoneEvent | None = None
        self.events: list[Event] = []
        self.max_seq = after
        self._text: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._text)

    def accept(self, event: Event, *, replay: bool) -> bool:
        """Record an event; False when it is a duplicate or (in a replay) of another turn.

        Returns:
            Whether to yield the event.
        """
        if event.seq is not None:
            if replay and event.seq <= self.max_seq:
                return False
            self.max_seq = max(self.max_seq, event.seq)
        if self.turn_id is None and event.turn_id:
            self.turn_id = event.turn_id
        if replay and event.turn_id != self.turn_id:
            return False
        self.events.append(event)
        if isinstance(event, TextEvent):
            self._text.append(event.delta)
        if isinstance(event, DoneEvent) and event.turn_id in {None, self.turn_id}:
            self.done = event
        return True


_OPENED = object()


class ChatStream:
    """The events of one turn, as an iterator; use it as a context manager to close it early.

    Attributes:
        turn_id: The turn's id (from the ``X-Turn-Id`` header, known once the stream is open).
        done: The turn's ``done`` event, once it arrived.
        events: Every event yielded so far.
    """

    def __init__(self, session: EvokaiSession, body: dict[str, Any], path: str = CHAT_PATH) -> None:
        self._session = session
        self._body = body
        self._path = path
        self._state = _StreamState(session.last_seq)
        self._gen: Generator[Any, None, None] | None = None

    @property
    def turn_id(self) -> str | None:
        """The turn's id, once known."""
        return self._state.turn_id

    @property
    def done(self) -> DoneEvent | None:
        """The turn's ``done`` event, once it arrived."""
        return self._state.done

    @property
    def events(self) -> list[Event]:
        """Every event yielded so far."""
        return self._state.events

    @property
    def text(self) -> str:
        """The answer's text so far (the ``text`` deltas joined)."""
        return self._state.text

    def open(self) -> Self:
        """Send the request (done on entering the ``with`` block or on the first event).

        Returns:
            The stream.
        """
        self._generator()
        return self

    def _generator(self) -> Generator[Any, None, None]:
        if self._gen is None:
            self._gen = self._run()
            next(self._gen)
        return self._gen

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __iter__(self) -> Iterator[Event]:
        return self

    def __next__(self) -> Event:
        return next(self._generator())

    def until_done(self) -> DoneEvent:
        """Consume the rest of the turn.

        Returns:
            The turn's ``done`` event.
        """
        for _ in self:
            pass
        if self._state.done is None:
            _incomplete(self._state.turn_id)
        return self._state.done

    def close(self) -> None:
        """Close the connection (the service cancels the turn if it is still running)."""
        if self._gen is not None:
            self._gen.close()

    def _run(self) -> Generator[Any, None, None]:
        s = self._session
        state = self._state
        with s._transport.stream(self._path, self._body, s.token) as response:
            state.turn_id = response.headers.get("x-turn-id") or None
            yield _OPENED
            parser = SseParser()
            try:
                for chunk in response.iter_bytes():
                    for msg in parser.feed(chunk):
                        event = _event_of(msg)
                        if event is None or not state.accept(event, replay=False):
                            continue
                        s._seen(event)
                        if isinstance(event, ClientToolEvent):
                            s._run_tool(event)
                        yield event
                        if state.done is not None:
                            return
            except httpx.TransportError:
                pass  # The stream broke mid-turn: fall through to the replay.
        if state.turn_id is None:
            _incomplete(None)
        for attempt in range(s.replay_attempts):
            if attempt:
                time.sleep(s.replay_delay * attempt)
            try:
                replayed = s.events(after=state.max_seq)
            except EvokaiError:
                continue
            for event in replayed:
                if state.accept(event, replay=True):
                    yield event
                    if state.done is not None:
                        return
        _incomplete(state.turn_id)


class AsyncChatStream:
    """The events of one turn, as an async iterator; ``async with`` closes it early.

    Attributes:
        turn_id: The turn's id (from the ``X-Turn-Id`` header, known once the stream is open).
        done: The turn's ``done`` event, once it arrived.
        events: Every event yielded so far.
    """

    def __init__(
        self, session: AsyncEvokaiSession, body: dict[str, Any], path: str = CHAT_PATH
    ) -> None:
        self._session = session
        self._body = body
        self._path = path
        self._state = _StreamState(session.last_seq)
        self._gen: AsyncGenerator[Any, None] | None = None

    @property
    def turn_id(self) -> str | None:
        """The turn's id, once known."""
        return self._state.turn_id

    @property
    def done(self) -> DoneEvent | None:
        """The turn's ``done`` event, once it arrived."""
        return self._state.done

    @property
    def events(self) -> list[Event]:
        """Every event yielded so far."""
        return self._state.events

    @property
    def text(self) -> str:
        """The answer's text so far (the ``text`` deltas joined)."""
        return self._state.text

    async def open(self) -> Self:
        """Send the request (done on entering ``async with`` or on the first event).

        Returns:
            The stream.
        """
        await self._generator()
        return self

    async def _generator(self) -> AsyncGenerator[Any, None]:
        if self._gen is None:
            self._gen = self._run()
            await anext(self._gen)
        return self._gen

    async def __aenter__(self) -> Self:
        return await self.open()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    def __aiter__(self) -> AsyncIterator[Event]:
        return self

    async def __anext__(self) -> Event:
        return await anext(await self._generator())

    async def until_done(self) -> DoneEvent:
        """Consume the rest of the turn.

        Returns:
            The turn's ``done`` event.
        """
        async for _ in self:
            pass
        if self._state.done is None:
            _incomplete(self._state.turn_id)
        return self._state.done

    async def aclose(self) -> None:
        """Close the connection (the service cancels the turn if it is still running)."""
        if self._gen is not None:
            await self._gen.aclose()

    async def _run(self) -> AsyncGenerator[Any, None]:
        s = self._session
        state = self._state
        async with s._transport.stream(self._path, self._body, s.token) as response:
            state.turn_id = response.headers.get("x-turn-id") or None
            yield _OPENED
            parser = SseParser()
            try:
                async for chunk in response.aiter_bytes():
                    for msg in parser.feed(chunk):
                        event = _event_of(msg)
                        if event is None or not state.accept(event, replay=False):
                            continue
                        s._seen(event)
                        if isinstance(event, ClientToolEvent):
                            await s._run_tool(event)
                        yield event
                        if state.done is not None:
                            return
            except httpx.TransportError:
                pass  # The stream broke mid-turn: fall through to the replay.
        if state.turn_id is None:
            _incomplete(None)
        for attempt in range(s.replay_attempts):
            if attempt:
                await asyncio.sleep(s.replay_delay * attempt)
            try:
                replayed = await s.events(after=state.max_seq)
            except EvokaiError:
                continue
            for event in replayed:
                if state.accept(event, replay=True):
                    yield event
                    if state.done is not None:
                        return
        _incomplete(state.turn_id)


def _token(token: str | SessionToken) -> tuple[str, str | None]:
    if isinstance(token, SessionToken):
        return token.token, token.conversation_id
    if not token:
        raise ValueError("token is required")
    return token, None


def _answer_text(option: str | Sequence[str]) -> str:
    return option if isinstance(option, str) else ", ".join(option)


class _SessionBase:
    """State shared by the sync and async sessions."""

    token: str
    conversation_id: str | None
    last_seq: int
    replay_attempts: int
    replay_delay: float

    def _setup(self, token: str | SessionToken, replay_attempts: int, replay_delay: float) -> None:
        self.token, self.conversation_id = _token(token)
        self.last_seq = 0
        self.replay_attempts = replay_attempts
        self.replay_delay = replay_delay
        self._tools: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def on_tool(self, name: str, fn: Callable[[dict[str, Any]], Any]) -> None:
        """Run a custom client tool when the agent calls it (``docs/client-tools.md``).

        ``fn`` gets the call's arguments; for a tool that waits, what it returns (or the message
        of what it raises) is posted as the result while the stream goes on. With the async
        session ``fn`` may be a coroutine function.
        """
        self._tools[name] = fn

    def use_token(self, token: str | SessionToken) -> None:
        """Replace the session token (a refreshed one, for the same conversation)."""
        self.token, conversation_id = _token(token)
        self.conversation_id = conversation_id or self.conversation_id

    def _seen(self, event: Event) -> None:
        if event.seq is not None and event.seq > self.last_seq:
            self.last_seq = event.seq


class EvokaiSession(_SessionBase):
    """A session over HTTP with a session token (sync).

    Args:
        base_url: Where the service is mounted, base path included
            (``https://agents.example.com/agent``).
        token: A session token, or the ``SessionToken`` your backend minted.
        http_client: An ``httpx.Client`` to use (not closed by the session).
        timeout: Timeout of each request when the session creates its own client.
        replay_attempts: How many times a broken stream polls the replay endpoint.
        replay_delay: Seconds between replay polls (grows linearly).
    """

    def __init__(
        self,
        base_url: str,
        token: str | SessionToken,
        *,
        http_client: httpx.Client | None = None,
        timeout: float | httpx.Timeout | None = None,
        replay_attempts: int = 8,
        replay_delay: float = 0.5,
    ) -> None:
        self._setup(token, replay_attempts, replay_delay)
        self._transport = SyncTransport(base_url, http_client=http_client, timeout=timeout)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client, if the session created it."""
        self._transport.close()

    def info(self) -> SessionInfo:
        """The session: conversation, agent, usage shares, memories and starter questions.

        Returns:
            The session info.
        """
        info = self._transport.send(calls.session_info(), self.token)
        self.conversation_id = self.conversation_id or info.conversation_id
        return info

    def conversation(self) -> SessionConversation:
        """The session's conversation (empty until the first message).

        Returns:
            Messages, the open confirmation, the last sequence number and the running turn.
        """
        conv = self._transport.send(calls.session_conversation(), self.token)
        self.last_seq = max(self.last_seq, conv.last_seq)
        return conv

    def events(self, after: int | None = None) -> list[Event]:
        """Replay: the events with ``seq`` greater than ``after`` (default: the last one seen).

        Returns:
            The events, in order.
        """
        start = self.last_seq if after is None else after
        events = self._transport.send(calls.session_events(start), self.token)
        for e in events:
            self._seen(e)
        return events

    def chat(
        self,
        text: str,
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
        end_of_speech_at: float | None = None,
    ) -> ChatStream:
        """Send a message and stream the turn.

        Args:
            text: The user's message.
            page: The page the user is looking at.
            mode: ``voice`` for a transcript the answer of which is read aloud.
            history: External storage: the conversation so far, oldest first.
            end_of_speech_at: Seconds since the epoch when the user stopped speaking.

        Returns:
            The stream of the turn's events (iterate it, or use it in a ``with`` block).
        """
        body = chat_body(
            text=text, page=page, mode=mode, history=history, end_of_speech_at=end_of_speech_at
        )
        return ChatStream(self, body)

    def rewrite(
        self,
        text: str,
        *,
        message_id: str | None = None,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> ChatStream:
        """Edit the last question and send it again: the last turn is replaced.

        The question, the answer, its tool calls and results go, and a new turn runs. The stream
        starts with ``conversation`` and ``rewritten``. Refused with :class:`ConflictError`
        (``rewrite_after_write`` when that turn changed data, ``rewrite_not_last``,
        ``rewrite_nothing``, ``rewrite_running``). With external storage, drop your history from
        ``done.replaced_from`` before appending ``done.messages``.

        Args:
            text: The new question.
            message_id: The stored question being edited (Evokai storage; default the last one).
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first (with the question
                being replaced).

        Returns:
            The stream of the new turn's events.
        """
        body = chat_body(
            text=text, page=page, mode=mode, history=history, rewrite=message_id or True
        )
        return ChatStream(self, body)

    def regenerate(
        self,
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> ChatStream:
        """Regenerate the last answer: the last question runs again and replaces the last turn.

        The stream starts with ``conversation`` and ``rewritten``. Refused with
        :class:`ConflictError` ``not_regenerable`` when there is nothing to regenerate, the last
        turn changed data or ran a client tool, or a turn is running (``done.regenerable`` and
        ``regenerable`` on a stored message say when it is allowed).

        Args:
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first (with the last
                question and the answer to replace).

        Returns:
            The stream of the new turn's events.
        """
        body = chat_body(page=page, mode=mode, history=history)
        return ChatStream(self, body, REGENERATE_PATH)

    def confirm(
        self,
        action_id: str,
        approve: bool,
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> ChatStream:
        """Approve or reject an open confirmation and stream the turn that resumes.

        Args:
            action_id: The ``action_id`` of the ``confirm`` event.
            approve: The user's decision.
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first.

        Returns:
            The stream of the turn's events.
        """
        body = chat_body(confirm=(action_id, approve), page=page, mode=mode, history=history)
        return ChatStream(self, body)

    def answer(
        self,
        option: str | Sequence[str],
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> ChatStream:
        """Answer an open question with one of its options (or several, or free text).

        Args:
            option: The chosen option, or options (sent joined with ``, ``).
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first.

        Returns:
            The stream of the turn's events.
        """
        return self.chat(_answer_text(option), page=page, mode=mode, history=history)

    def chat_json(
        self,
        text: str | None = None,
        *,
        confirm: tuple[str, bool] | None = None,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
        rewrite: bool | str = False,
    ) -> ChatResult:
        """Run one turn and wait for its end (``Accept: application/json``).

        Args:
            text: The user's message.
            confirm: ``(action_id, approve)`` to decide on an open confirmation instead.
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first.
            rewrite: Replace the last question with ``text`` (True, or the edited message's id);
                with external storage drop your history from ``result.replaced_from``.

        Returns:
            The whole turn: its text, events, reason and (external storage) new messages.
        """
        body = chat_body(
            text=text, confirm=confirm, page=page, mode=mode, history=history, rewrite=rewrite
        )
        result = self._transport.send(calls.session_chat_json(body), self.token)
        for e in result.events:
            self._seen(e)
        return result

    def cancel(self, turn_id: str) -> bool:
        """Stop a turn (barge-in or a stop button); its stream still ends with ``done``.

        Returns:
            Whether a running turn was cancelled.
        """
        return self._transport.send(calls.session_cancel(turn_id), self.token)

    def feedback(self, message_id: str, value: Literal[1, -1] | None) -> None:
        """Thumbs up (1), down (-1) or none (None) on a stored assistant message."""
        self._transport.send(calls.session_feedback(message_id, value), self.token)

    def tool_result(self, call_id: str, result: Any = None, *, error: str | None = None) -> None:
        """Answer a waiting custom client tool call (once): a JSON result, or an error."""
        self._transport.send(calls.session_tool_result(call_id, result, error), self.token)

    def _run_tool(self, event: ClientToolEvent) -> None:
        fn = self._tools.get(event.name)
        if fn is None:
            return
        try:
            result, error = fn(event.args), None
        except Exception as e:
            result, error = None, str(e) or type(e).__name__
        if event.wait:
            self.tool_result(event.call_id, result, error=error)

    def exit(self, code: str) -> None:
        """Kiosk sessions: close the session with its exit code."""
        self._transport.send(calls.session_exit(code), self.token)

    def memories(self) -> MemoryList:
        """What the agents remember about the person, and whether memory is on for them.

        Returns:
            The memory state (mode, consent, active, ask) and the memories.
        """
        return self._transport.send(calls.session_memories(), self.token)

    def memory_consent(self, granted: bool) -> MemoryConsentResult:
        """Record the person's answer to «may the assistant remember things about you?».

        A no (or withdrawing) deletes what was remembered. When memory is off for the application
        the service answers 409 ``memory_off`` (:class:`EvokaiConflictError`).

        Returns:
            The new memory state and how many memories were deleted.
        """
        return self._transport.send(calls.session_memory_consent(granted), self.token)

    def edit_memory(self, memory_id: str, text: str) -> Memory:
        """Rewrite one memory (its source becomes ``person``).

        The guard refuses instructions, secrets and personal data: 400 ``memory_refused``
        (:class:`EvokaiValidationError`). Memory must be on for the person (403
        ``memory_inactive``).

        Returns:
            The memory as stored.
        """
        return self._transport.send(calls.session_memory_edit(memory_id, text), self.token)

    def forget_memory(self, memory_id: str | None = None) -> int:
        """Delete one memory, or everything remembered about the person (no id).

        Returns:
            How many memories were deleted.
        """
        return self._transport.send(calls.session_memory_forget(memory_id), self.token)

    def report_bug(self, description: str, context: Mapping[str, Any] | None = None) -> str:
        """Report a problem with this session.

        Returns:
            The report's id.
        """
        return self._transport.send(calls.session_bug_report(description, context), self.token)

    def connections(self) -> list[Connection]:
        """The person's connections, one per provider the application configured and granted.

        Returns:
            Connections.
        """
        return self._transport.send(calls.session_connections(), self.token)

    def start_connection(self, provider: str, return_to: str | None = None) -> ConnectionStarted:
        """Start connecting a provider: open ``authorize_url`` in the person's browser.

        Returns:
            Where to send the person (single use, valid ten minutes).
        """
        return self._transport.send(calls.session_connection_start(provider, return_to), self.token)

    def disconnect(self, provider: str) -> None:
        """Disconnect a provider: the person's tokens are deleted."""
        self._transport.send(calls.session_disconnect(provider), self.token)


class AsyncEvokaiSession(_SessionBase):
    """A session over HTTP with a session token (async); see :class:`EvokaiSession`.

    Args:
        base_url: Where the service is mounted, base path included.
        token: A session token, or the ``SessionToken`` your backend minted.
        http_client: An ``httpx.AsyncClient`` to use (not closed by the session).
        timeout: Timeout of each request when the session creates its own client.
        replay_attempts: How many times a broken stream polls the replay endpoint.
        replay_delay: Seconds between replay polls (grows linearly).
    """

    def __init__(
        self,
        base_url: str,
        token: str | SessionToken,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout | None = None,
        replay_attempts: int = 8,
        replay_delay: float = 0.5,
    ) -> None:
        self._setup(token, replay_attempts, replay_delay)
        self._transport = AsyncTransport(base_url, http_client=http_client, timeout=timeout)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client, if the session created it."""
        await self._transport.aclose()

    async def info(self) -> SessionInfo:
        """The session: conversation, agent, usage shares, memories and starter questions.

        Returns:
            The session info.
        """
        info = await self._transport.send(calls.session_info(), self.token)
        self.conversation_id = self.conversation_id or info.conversation_id
        return info

    async def conversation(self) -> SessionConversation:
        """The session's conversation (empty until the first message).

        Returns:
            Messages, the open confirmation, the last sequence number and the running turn.
        """
        conv = await self._transport.send(calls.session_conversation(), self.token)
        self.last_seq = max(self.last_seq, conv.last_seq)
        return conv

    async def events(self, after: int | None = None) -> list[Event]:
        """Replay: the events with ``seq`` greater than ``after`` (default: the last one seen).

        Returns:
            The events, in order.
        """
        start = self.last_seq if after is None else after
        events = await self._transport.send(calls.session_events(start), self.token)
        for e in events:
            self._seen(e)
        return events

    def chat(
        self,
        text: str,
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
        end_of_speech_at: float | None = None,
    ) -> AsyncChatStream:
        """Send a message and stream the turn (``async for`` or ``async with``).

        Args:
            text: The user's message.
            page: The page the user is looking at.
            mode: ``voice`` for a transcript the answer of which is read aloud.
            history: External storage: the conversation so far, oldest first.
            end_of_speech_at: Seconds since the epoch when the user stopped speaking.

        Returns:
            The stream of the turn's events.
        """
        body = chat_body(
            text=text, page=page, mode=mode, history=history, end_of_speech_at=end_of_speech_at
        )
        return AsyncChatStream(self, body)

    def rewrite(
        self,
        text: str,
        *,
        message_id: str | None = None,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> AsyncChatStream:
        """Edit the last question and send it again: the last turn is replaced.

        The question, the answer, its tool calls and results go, and a new turn runs. The stream
        starts with ``conversation`` and ``rewritten``. Refused with :class:`ConflictError`
        (``rewrite_after_write`` when that turn changed data, ``rewrite_not_last``,
        ``rewrite_nothing``, ``rewrite_running``). With external storage, drop your history from
        ``done.replaced_from`` before appending ``done.messages``.

        Args:
            text: The new question.
            message_id: The stored question being edited (Evokai storage; default the last one).
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first (with the question
                being replaced).

        Returns:
            The stream of the new turn's events.
        """
        body = chat_body(
            text=text, page=page, mode=mode, history=history, rewrite=message_id or True
        )
        return AsyncChatStream(self, body)

    def regenerate(
        self,
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> AsyncChatStream:
        """Regenerate the last answer: the last question runs again and replaces the last turn.

        The stream starts with ``conversation`` and ``rewritten``. Refused with
        :class:`ConflictError` ``not_regenerable`` when there is nothing to regenerate, the last
        turn changed data or ran a client tool, or a turn is running (``done.regenerable`` and
        ``regenerable`` on a stored message say when it is allowed).

        Args:
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first (with the last
                question and the answer to replace).

        Returns:
            The stream of the new turn's events.
        """
        body = chat_body(page=page, mode=mode, history=history)
        return AsyncChatStream(self, body, REGENERATE_PATH)

    def confirm(
        self,
        action_id: str,
        approve: bool,
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> AsyncChatStream:
        """Approve or reject an open confirmation and stream the turn that resumes.

        Args:
            action_id: The ``action_id`` of the ``confirm`` event.
            approve: The user's decision.
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first.

        Returns:
            The stream of the turn's events.
        """
        body = chat_body(confirm=(action_id, approve), page=page, mode=mode, history=history)
        return AsyncChatStream(self, body)

    def answer(
        self,
        option: str | Sequence[str],
        *,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
    ) -> AsyncChatStream:
        """Answer an open question with one of its options (or several, or free text).

        Args:
            option: The chosen option, or options (sent joined with ``, ``).
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first.

        Returns:
            The stream of the turn's events.
        """
        return self.chat(_answer_text(option), page=page, mode=mode, history=history)

    async def chat_json(
        self,
        text: str | None = None,
        *,
        confirm: tuple[str, bool] | None = None,
        page: PageContext | Mapping[str, Any] | None = None,
        mode: ChatMode = "text",
        history: Sequence[HistoryMessage | Mapping[str, Any]] | None = None,
        rewrite: bool | str = False,
    ) -> ChatResult:
        """Run one turn and wait for its end (``Accept: application/json``).

        Args:
            text: The user's message.
            confirm: ``(action_id, approve)`` to decide on an open confirmation instead.
            page: The page the user is looking at.
            mode: ``text`` or ``voice``.
            history: External storage: the conversation so far, oldest first.
            rewrite: Replace the last question with ``text`` (True, or the edited message's id);
                with external storage drop your history from ``result.replaced_from``.

        Returns:
            The whole turn: its text, events, reason and (external storage) new messages.
        """
        body = chat_body(
            text=text, confirm=confirm, page=page, mode=mode, history=history, rewrite=rewrite
        )
        result = await self._transport.send(calls.session_chat_json(body), self.token)
        for e in result.events:
            self._seen(e)
        return result

    async def cancel(self, turn_id: str) -> bool:
        """Stop a turn (barge-in or a stop button); its stream still ends with ``done``.

        Returns:
            Whether a running turn was cancelled.
        """
        return await self._transport.send(calls.session_cancel(turn_id), self.token)

    async def feedback(self, message_id: str, value: Literal[1, -1] | None) -> None:
        """Thumbs up (1), down (-1) or none (None) on a stored assistant message."""
        await self._transport.send(calls.session_feedback(message_id, value), self.token)

    async def tool_result(
        self, call_id: str, result: Any = None, *, error: str | None = None
    ) -> None:
        """Answer a waiting custom client tool call (once): a JSON result, or an error."""
        await self._transport.send(calls.session_tool_result(call_id, result, error), self.token)

    async def _run_tool(self, event: ClientToolEvent) -> None:
        fn = self._tools.get(event.name)
        if fn is None:
            return
        try:
            result, error = fn(event.args), None
            if inspect.isawaitable(result):
                result = await result
        except Exception as e:
            result, error = None, str(e) or type(e).__name__
        if event.wait:
            await self.tool_result(event.call_id, result, error=error)

    async def exit(self, code: str) -> None:
        """Kiosk sessions: close the session with its exit code."""
        await self._transport.send(calls.session_exit(code), self.token)

    async def memories(self) -> MemoryList:
        """What the agents remember about the person, and whether memory is on for them.

        Returns:
            The memory state (mode, consent, active, ask) and the memories.
        """
        return await self._transport.send(calls.session_memories(), self.token)

    async def memory_consent(self, granted: bool) -> MemoryConsentResult:
        """Record the person's answer to «may the assistant remember things about you?».

        A no (or withdrawing) deletes what was remembered. When memory is off for the application
        the service answers 409 ``memory_off`` (:class:`EvokaiConflictError`).

        Returns:
            The new memory state and how many memories were deleted.
        """
        return await self._transport.send(calls.session_memory_consent(granted), self.token)

    async def edit_memory(self, memory_id: str, text: str) -> Memory:
        """Rewrite one memory (its source becomes ``person``).

        The guard refuses instructions, secrets and personal data: 400 ``memory_refused``
        (:class:`EvokaiValidationError`). Memory must be on for the person (403
        ``memory_inactive``).

        Returns:
            The memory as stored.
        """
        return await self._transport.send(calls.session_memory_edit(memory_id, text), self.token)

    async def forget_memory(self, memory_id: str | None = None) -> int:
        """Delete one memory, or everything remembered about the person (no id).

        Returns:
            How many memories were deleted.
        """
        return await self._transport.send(calls.session_memory_forget(memory_id), self.token)

    async def report_bug(self, description: str, context: Mapping[str, Any] | None = None) -> str:
        """Report a problem with this session.

        Returns:
            The report's id.
        """
        return await self._transport.send(
            calls.session_bug_report(description, context), self.token
        )

    async def connections(self) -> list[Connection]:
        """The person's connections, one per provider the application configured and granted.

        Returns:
            Connections.
        """
        return await self._transport.send(calls.session_connections(), self.token)

    async def start_connection(
        self, provider: str, return_to: str | None = None
    ) -> ConnectionStarted:
        """Start connecting a provider: open ``authorize_url`` in the person's browser.

        Returns:
            Where to send the person (single use, valid ten minutes).
        """
        return await self._transport.send(
            calls.session_connection_start(provider, return_to), self.token
        )

    async def disconnect(self, provider: str) -> None:
        """Disconnect a provider: the person's tokens are deleted."""
        await self._transport.send(calls.session_disconnect(provider), self.token)
