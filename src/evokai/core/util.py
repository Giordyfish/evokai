from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime


def new_id() -> str:
    """A new random id.

    Returns:
        A UUID4 string.
    """
    return str(uuid.uuid4())


def utcnow() -> datetime:
    """Now, timezone-aware, in UTC.

    Returns:
        The current time.
    """
    return datetime.now(UTC)


def clip(text: str, limit: int) -> str:
    """Cut a text to a length, marking the cut.

    Returns:
        The text, cut if longer than the limit.
    """
    return f"{text[:limit]}… [troncato]" if len(text) > limit else text


class TtlCache[K, V]:
    """Bounded LRU with a time-to-live.

    Per process and lock-free: one event loop, no awaits inside.
    """

    def __init__(self, max_items: int, ttl_seconds: float) -> None:
        """Create the cache.

        Args:
            max_items: Bound on entries (least recently used go first).
            ttl_seconds: Lifetime of an entry.
        """
        self.max_items = max_items
        self.ttl = ttl_seconds
        self._items: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        """Read an entry.

        Returns:
            The value, or None if missing or expired.
        """
        hit = self._items.get(key)
        if not hit:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: K, value: V) -> None:
        """Write an entry."""
        self._items[key] = (time.monotonic() + self.ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def pop(self, key: K) -> None:
        """Remove an entry if present."""
        self._items.pop(key, None)

    def clear(self) -> None:
        """Drop every entry."""
        self._items.clear()
