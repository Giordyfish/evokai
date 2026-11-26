"""What a conversation's server speech said lately, so the next synthesis can follow it.

A client that speaks an answer segment by segment asks for one synthesis per segment; spoken alone,
each would start and end like a whole utterance. An engine that can follow earlier syntheses (by a
reference of its own, or by the text spoken before) keeps the prosody natural across them. This
memory keeps, per conversation, the speech of the last two hours: its id (``speechId``), the
engine's reference, what made it (engine, voice, model: a reference serves only the same) and the
text spoken. It lives in this process only and is never stored: a missing entry only means the
engine is given the text before instead of a reference.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

Key = tuple[str, str, str]
"""Application, subject, conversation."""


@dataclass
class Spoken:
    """One synthesis of a conversation."""

    ref: str | None
    """The engine's reference for it (None for engines without one)."""
    text: str
    """What was spoken."""
    at: float = 0.0
    made_by: str = ""
    """The engine, voice and model that spoke it (its reference serves only the same)."""


@dataclass
class _Conversation:
    items: OrderedDict[str, Spoken] = field(default_factory=OrderedDict)
    touched: float = 0.0


class SpeechMemory:
    """The recent speech of each conversation, bounded in time and size."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 2 * 3600,
        conversations: int = 10_000,
        per_conversation: int = 64,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Start empty.

        Args:
            ttl_seconds: How long a synthesis is remembered.
            conversations: The most conversations kept (the least recently used go first).
            per_conversation: The most syntheses kept per conversation (the oldest go first).
            clock: Seconds, monotonic (replaced in tests).
        """
        self._ttl = ttl_seconds
        self._max = conversations
        self._per = per_conversation
        self._clock = clock
        self._data: OrderedDict[Key, _Conversation] = OrderedDict()

    def _expire(self, now: float) -> None:
        while self._data:
            key, conv = next(iter(self._data.items()))
            if now - conv.touched <= self._ttl and len(self._data) <= self._max:
                break
            del self._data[key]

    def remember(
        self, key: Key, speech_id: str, ref: str | None, text: str, made_by: str = ""
    ) -> None:
        """Keep a synthesis."""
        now = self._clock()
        conv = self._data.pop(key, None) or _Conversation()
        conv.items.pop(speech_id, None)
        conv.items[speech_id] = Spoken(ref, text, now, made_by)
        while len(conv.items) > self._per:
            conv.items.popitem(last=False)
        conv.touched = now
        self._data[key] = conv
        self._expire(now)

    def get(self, key: Key, speech_id: str) -> Spoken | None:
        """A synthesis of the conversation, while it is remembered.

        Returns:
            It, or None.
        """
        now = self._clock()
        self._expire(now)
        conv = self._data.get(key)
        item = conv.items.get(speech_id) if conv else None
        if item is None or now - item.at > self._ttl:
            return None
        return item

    def __len__(self) -> int:
        """The conversations remembered.

        Returns:
            Their number.
        """
        return len(self._data)


def say_speech_id(turn_id: str, index: int) -> str:
    """The ``speechId`` of a ``say`` segment.

    Returns:
        The id.
    """
    return f"{turn_id}.{index}"
