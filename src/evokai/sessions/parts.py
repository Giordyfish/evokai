"""What the person was shown of a turn, kept with its assistant messages in the order it happened.

Every event a turn sends goes through the turn's :class:`PartLog`. The events that draw something
in the conversation (its *parts*) are kept, in order: ``text`` (consecutive deltas joined into one),
``tool`` (one per call, holding its last status), ``client_tool`` (the call the page received and,
for a call that waited, whether the page answered: ``status``), ``chart``, ``diagram``, ``ask``,
``navigate``, ``connect``, ``confirm``, ``citation``, ``handoff`` and ``error``. Each stored
assistant message takes the parts shown since the previous one (``events``), so a client rebuilds a
reloaded conversation by applying them, in order, exactly as it applied the live events.

Nothing else is kept: never a tool's arguments or result (a browser never sees them), never what a
page answered to a client tool (the page has it, the model reads it in the message's content), never
usage, speech segments or stream bookkeeping. With ``pii.history`` enforced the stored copy is
masked like the stored text.

Messages stored before this format have their text and some of these events (charts, diagrams,
questions, navigation, connection cards, confirmations, citations) but no ``text`` event: a reader
shows their text first, then their events (``ordered``).
"""

from __future__ import annotations

from typing import Any

SHOWN = frozenset({
    "text",
    "tool",
    "client_tool",
    "chart",
    "diagram",
    "ask",
    "navigate",
    "connect",
    "confirm",
    "citation",
    "handoff",
    "error",
})
"""Event types that draw something in the conversation."""


def ordered(events: list[Any] | None) -> bool:
    """Whether a stored message's events are its whole display, in order (they hold its text).

    Returns:
        True for a message stored with its parts; False for an older message, whose text goes
        before its events.
    """
    return any(isinstance(e, dict) and e.get("type") == "text" for e in events or [])


class PartLog:
    """The parts a turn has shown and not yet stored with a message."""

    def __init__(self) -> None:
        """Start empty."""
        self._parts: list[dict[str, Any]] = []
        self._calls: dict[str, dict[str, Any]] = {}
        """Client tool calls by id, to record the page's answer on the part already taken."""

    @property
    def pending(self) -> bool:
        """Whether parts were shown since the last message took them."""
        return bool(self._parts)

    def add(self, event: dict[str, Any]) -> None:
        """Keep an event the person is shown (other events are ignored)."""
        kind = event.get("type")
        if kind not in SHOWN:
            return
        parts = self._parts
        if kind == "text":
            delta = str(event.get("delta") or "")
            if not delta:
                return
            if parts and parts[-1].get("type") == "text":
                parts[-1] = {"type": "text", "delta": parts[-1]["delta"] + delta}
            else:
                parts.append({"type": "text", "delta": delta})
            return
        if kind == "tool" and event.get("status") != "start":
            # The end of a call updates its card, as a client does.
            for p in reversed(parts):
                if (
                    p.get("type") == "tool"
                    and p.get("name") == event.get("name")
                    and p.get("status") == "start"
                ):
                    p["status"] = event.get("status")
                    return
        part = dict(event)
        parts.append(part)
        if kind == "client_tool" and isinstance(event.get("callId"), str):
            self._calls[event["callId"]] = part

    def settle(self, call_id: str, ok: bool) -> None:
        """A client tool that waited was answered (or not): its part says how it ended."""
        part = self._calls.get(call_id)
        if part is not None:
            part["status"] = "done" if ok else "error"

    def take(self, *, interrupted: bool = False) -> list[dict[str, Any]]:
        """The parts shown since the last message took them, for the message being stored.

        Args:
            interrupted: The message was cut off: calls still running are left out (their
                content is dropped too).

        Returns:
            The parts, in order.
        """
        out, self._parts = self._parts, []
        if interrupted:
            out = [p for p in out if not (p.get("type") == "tool" and p.get("status") == "start")]
        return out
