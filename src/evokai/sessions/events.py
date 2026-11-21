"""Events of a session: numbered, persisted for replay, fanned out to the live connections.

Every event carries `turnId` and a per-session `seq`. Persistence is batched (text deltas are
frequent) and flushed on every non-text event and at the end of a turn, so a client that reconnects
can replay exactly what it missed. When the application keeps conversations itself, nothing is
persisted: replay comes from a short in-memory buffer instead.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from typing import Any

from evokai.db.repositories import Store

log = logging.getLogger("evokai.events")

Listener = Callable[[dict[str, Any]], None]


class SessionChannel:
    """One per active session in this process.

    Listeners are the SSE response or the voice socket.
    """

    def __init__(
        self, store: Store, session_id: str, last_seq: int, *, persist: bool = True
    ) -> None:
        """Open a channel.

        Args:
            store: Repositories.
            session_id: The session.
            last_seq: The last event number already sent.
            persist: Store events for replay (off when the application keeps conversations).
        """
        self.store = store
        self.persist = persist
        self._recent: deque[dict[str, Any]] = deque(maxlen=1_000)
        self.session_id = session_id
        self.seq = last_seq
        self._pending: list[dict[str, Any]] = []
        self._listeners: set[Listener] = set()
        self._lock = asyncio.Lock()
        self.persist_filter: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        """What the stored copy of an event becomes (the guard masks personal data in it)."""

    def has_listeners(self) -> bool:
        """Whether a client is connected to this session.

        Returns:
            True if at least one listener is registered.
        """
        return bool(self._listeners)

    def listen(self, fn: Listener) -> Callable[[], None]:
        """Add a listener.

        Returns:
            A function that removes it.
        """
        self._listeners.add(fn)
        return lambda: self._listeners.discard(fn)

    def publish(self, event: dict[str, Any], turn_id: str | None) -> dict[str, Any]:
        """Number, keep and fan out an event.

        Returns:
            The event as sent (with turnId and seq).
        """
        self.seq += 1
        e = {**event, "turnId": turn_id, "seq": self.seq}
        self._recent.append(e)
        if self.persist:
            self._pending.append(self.persist_filter(e) if self.persist_filter else e)
        for fn in list(self._listeners):
            try:
                fn(e)
            except Exception:
                log.exception("listener_failed")
        return e

    async def flush(self) -> None:
        """Persist the events published so far."""
        async with self._lock:
            rows, self._pending = self._pending, []
            if not rows:
                return
            await self.store.events.append(self.session_id, rows)
            await self.store.sessions.set_last_seq(self.session_id, rows[-1]["seq"])

    async def replay(self, after: int) -> list[dict[str, Any]]:
        """Events after a number, persisted first.

        Returns:
            The events in order.
        """
        if not self.persist:
            return [e for e in self._recent if e["seq"] > after]
        await self.flush()
        return await self.store.events.after(self.session_id, after)


class ChannelHub:
    """The channels of the sessions active in this process."""

    def __init__(self, store: Store) -> None:
        """Create the hub.

        Args:
            store: Repositories.
        """
        self.store = store
        self._channels: dict[str, SessionChannel] = {}

    async def get(self, session_id: str, *, persist: bool = True) -> SessionChannel:
        """The channel of a session, opened on first use.

        Args:
            session_id: The session.
            persist: Whether its events are stored.

        Returns:
            The channel.
        """
        ch = self._channels.get(session_id)
        if ch is None:
            last = await self.store.events.max_seq(session_id) if persist else 0
            ch = SessionChannel(self.store, session_id, last, persist=persist)
            self._channels[session_id] = ch
        ch.persist = persist
        return ch

    def forget(self, session_id: str) -> None:
        """Drop a session's channel (a throw-away conversation, such as an eval case)."""
        self._channels.pop(session_id, None)

    def drop_if_idle(self, session_id: str) -> None:
        """Forget a channel nobody listens to and with nothing left to persist."""
        ch = self._channels.get(session_id)
        # A flush in progress has already taken its events off ``_pending``: until they are stored,
        # a channel reopened from the database would number new events from a stale last number.
        if ch and ch.persist and not ch._listeners and not ch._pending and not ch._lock.locked():
            self._channels.pop(session_id, None)
