"""Running turns.

Each turn has its own cancellation; cancelling one stops its model stream, tools and speech, and
nothing else. A new turn in the same session supersedes the running one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class TurnHandle:
    """A running turn's identity and cancellation."""

    turn_id: str
    session_id: str
    user_id: str
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""
    answered: bool = False
    """The turn sent ``done``: its messages are stored and only its last events and its row are
    still being persisted (it is ``finished`` a moment later)."""

    def cancel(self, reason: str = "cancelled") -> None:
        """Ask the turn to stop (idempotent)."""
        if not self.cancelled.is_set():
            self.reason = reason
            self.cancelled.set()


class TurnRegistry:
    """Running turns of this process, by turn and by session."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._by_turn: dict[str, TurnHandle] = {}
        self._by_session: dict[str, TurnHandle] = {}

    async def begin(self, turn_id: str, session_id: str, user_id: str) -> TurnHandle:
        """Register a turn, superseding the session's running one (waits for it to stop).

        Returns:
            The new turn's handle.
        """
        previous = self._by_session.get(session_id)
        if previous and not previous.finished.is_set():
            previous.cancel("superseded")
            try:
                await asyncio.wait_for(previous.finished.wait(), timeout=5)
            except TimeoutError:
                pass
        h = TurnHandle(turn_id=turn_id, session_id=session_id, user_id=user_id)
        self._by_turn[turn_id] = h
        self._by_session[session_id] = h
        return h

    def end(self, h: TurnHandle) -> None:
        """Mark a turn finished."""
        h.finished.set()
        self._by_turn.pop(h.turn_id, None)
        if self._by_session.get(h.session_id) is h:
            self._by_session.pop(h.session_id, None)

    def cancel(
        self,
        turn_id: str,
        user_id: str,
        reason: str = "cancelled",
        *,
        session_id: str | None = None,
    ) -> bool:
        """Cancel a running turn of this subject (and of this conversation, when given).

        Returns:
            Whether a running turn was cancelled.
        """
        h = self._by_turn.get(turn_id)
        if not h or h.user_id != user_id or (session_id is not None and h.session_id != session_id):
            return False
        h.cancel(reason)
        return True

    async def drain(self, timeout: float = 10.0) -> None:
        """Cancel every running turn and wait for them to finish (shutdown)."""
        handles = list(self._by_turn.values())
        for h in handles:
            h.cancel("shutdown")
        if handles:
            waits = [asyncio.create_task(h.finished.wait()) for h in handles]
            _, pending = await asyncio.wait(waits, timeout=timeout)
            for w in pending:
                w.cancel()

    def running(self, session_id: str) -> TurnHandle | None:
        """The session's running turn (one that already sent ``done`` included, until finished).

        Returns:
            Its handle, or None.
        """
        h = self._by_session.get(session_id)
        return h if h and not h.finished.is_set() else None

    def answering(self, session_id: str) -> TurnHandle | None:
        """The session's turn that has not sent ``done`` yet.

        That is what a reader of the conversation calls running: a turn past its ``done`` has
        stored its messages.

        Returns:
            Its handle, or None.
        """
        h = self.running(session_id)
        return h if h and not h.answered else None
