"""Where a conversation's messages live, chosen per application.

- ``evokai`` (default): in Evokai's database.
- ``external``: in the application. It sends the history with each request (the contract below);
  Evokai validates and windows it, runs the turn and returns the new messages (in the ``done``
  event, or in the JSON response) for the application to append. Evokai keeps no message content
  at rest in this mode.

The history format is a stable contract (the future TypeScript client is generated from it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

from evokai.db.repositories import MessageRepository, Who

STORAGE_MODES = ("evokai", "external")
MAX_HISTORY_MESSAGES = 200
MAX_HISTORY_CHARS = 400_000


class TextBlock(BaseModel):
    """Plain text."""

    text: str = Field(max_length=100_000)


class ToolUse(BaseModel):
    """A tool call made by the assistant."""

    id: str = Field(max_length=128)
    name: str = Field(max_length=128)
    input: dict[str, Any] = Field(default_factory=dict)


class ToolUseBlock(BaseModel):
    """Assistant block: a tool call."""

    toolUse: ToolUse


class ToolResult(BaseModel):
    """The result of a tool call, as text for the model."""

    id: str = Field(max_length=128)
    name: str = Field(max_length=128)
    text: str = Field(default="", max_length=100_000)
    ok: bool = True
    wrote: bool | None = None
    """True when the tool changes data and ran: a question whose turn wrote cannot be rewritten.
    Keep it as Evokai returns it."""


class ToolResultBlock(BaseModel):
    """User block: a tool result."""

    toolResult: ToolResult


class FileRef(BaseModel):
    """An attachment as a message keeps it (``evokai.attachments``): never its bytes."""

    id: str = Field(max_length=64)
    name: str = Field(max_length=200)
    mediaType: str = Field(max_length=100)
    size: int = Field(default=0, ge=0)
    sha256: str = Field(default="", max_length=64)


class FileBlock(BaseModel):
    """User block: an attachment, with the text the model read of it (a document).

    Without text the model read the file itself; with external storage that file is not kept, and
    a later turn tells the model it is no longer available.
    """

    file: FileRef
    text: str | None = Field(default=None, max_length=60_000)


Block = Annotated[
    FileBlock | TextBlock | ToolUseBlock | ToolResultBlock, Field(union_mode="left_to_right")
]


class HistoryMessage(BaseModel):
    """One message of a conversation, as the application stores it and sends it back.

    ``content`` is what the model sees; ``text`` and ``events`` are what the user saw (the text,
    and the charts, schemas, questions and confirmations shown with it).
    """

    role: Literal["user", "assistant"]
    content: list[Block] = Field(default_factory=list, max_length=200)
    text: str = Field(default="", max_length=100_000)
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    interrupted: bool = False


@dataclass
class ThreadMessage:
    """A message inside Evokai (provider-neutral blocks as plain dicts)."""

    role: str
    content: list[dict[str, Any]]
    text: str = ""
    events: list[Any] | None = None
    id: str | None = None
    interrupted: bool = False
    position: int | None = None
    """Its place in the thread (Evokai storage only)."""

    def as_json(self) -> dict[str, Any]:
        """The wire form (HistoryMessage).

        Returns:
            A JSON-able dict.
        """
        return {
            "role": self.role,
            "content": self.content,
            "text": self.text,
            "events": self.events or [],
            "interrupted": self.interrupted,
        }

    @classmethod
    def from_history(cls, h: HistoryMessage) -> ThreadMessage:
        """From the wire form.

        Returns:
            The message; content defaults to its text.
        """
        content = [b.model_dump(exclude_none=True) for b in h.content] or (
            [{"text": h.text}] if h.text else []
        )
        return cls(h.role, content, h.text, list(h.events), interrupted=h.interrupted)


class ThreadStore(Protocol):
    """Read and append the messages of one conversation."""

    async def recent(self, limit: int) -> list[ThreadMessage]:
        """The last messages, oldest first."""
        ...

    async def add(self, message: ThreadMessage, turn_id: str) -> str | None:
        """Append a message; returns its id where the store has ids."""
        ...


@dataclass
class EvokaiThreads:
    """Messages in Evokai's database."""

    repo: MessageRepository
    who: Who
    session_id: str
    thread_id: str

    async def recent(self, limit: int) -> list[ThreadMessage]:
        """The last messages.

        Returns:
            Messages, oldest first.
        """
        rows = await self.repo.recent(self.who, self.thread_id, limit)
        return [_message(r) for r in rows]

    async def since(self, position: int, limit: int) -> list[ThreadMessage]:
        """The messages after a position (a summary's checkpoint), at most the latest ``limit``.

        Returns:
            Messages, oldest first.
        """
        rows = await self.repo.since(self.who, self.thread_id, position, limit)
        return [_message(r) for r in rows]

    async def add(self, message: ThreadMessage, turn_id: str) -> str | None:
        """Store a message.

        Returns:
            Its id.
        """
        return await self.repo.add(
            self.who,
            self.session_id,
            self.thread_id,
            role=message.role,
            content=message.content,
            text=message.text,
            events=message.events,
            turn_id=turn_id,
            interrupted=message.interrupted,
        )


def _message(r: Any) -> ThreadMessage:
    return ThreadMessage(r.role, r.content, r.text, r.events, r.id, r.interrupted, r.position)


@dataclass
class ExternalThreads:
    """History sent by the application; this turn's new messages are returned to it."""

    history: list[ThreadMessage]
    added: list[ThreadMessage] = field(default_factory=list)

    @classmethod
    def from_request(cls, history: list[HistoryMessage] | None) -> ExternalThreads:
        """Validate and window the history the application sent.

        Returns:
            The thread, with at most the last MAX_HISTORY_MESSAGES within MAX_HISTORY_CHARS.
        """
        msgs = [ThreadMessage.from_history(h) for h in (history or [])][-MAX_HISTORY_MESSAGES:]
        total, kept = 0, []
        for m in reversed(msgs):
            total += len(m.text) + sum(len(str(b)) for b in m.content)
            if kept and total > MAX_HISTORY_CHARS:
                break
            kept.insert(0, m)
        return cls(kept)

    async def recent(self, limit: int) -> list[ThreadMessage]:
        """The provided history plus this turn's messages.

        Returns:
            Messages, oldest first.
        """
        return [*self.history, *self.added][-limit:]

    async def add(self, message: ThreadMessage, turn_id: str) -> str | None:  # ruff: ignore[unused-method-argument]
        """Keep a message to return to the application.

        Returns:
            None (the application assigns ids).
        """
        self.added.append(message)
        return None
