"""Rewrite the last question: the person edits their last message and sends it again.

The last turn, that is the person's last message and everything after it (the assistant's text, its
tool calls and results, a confirmation and what it ran), is replaced by the new message, and a new
turn runs on it. Limits:

- only the last question (the last message the person wrote; a confirmation is not a question);
- refused when anything after it changed data: a tool that changes data ran (its result is marked
  ``wrote``), or, for messages stored before that mark existed, a change was confirmed. Writes
  cannot be undone, so a turn that wrote cannot be taken back;
- refused while a turn of the conversation is running.

With Evokai storage the replaced messages and their replay events are deleted, an open confirmation
is closed (``rewritten``), the conversation summary is dropped when it covers them (it is written
again when needed), and what memory took from them is forgotten: the conversation's episode and the
memories its agent wrote since the replaced message (an older memory one of them had superseded
comes back). Usage stays recorded: it was spent. Traces are not changed (they follow their own
retention).

With external storage the application owns the history: it sends ``rewrite`` with the history as it
has it; Evokai checks and cuts it, runs the turn, and says from which index of the sent history the
new messages replace the old ones (``replacedFrom``).

Regenerating the last answer is a rewrite with the same question (``POST /v1/session/regenerate``):
the question runs again and its new answer replaces the last one. It is refused
(``not_regenerable``) whenever a rewrite would be, and also after a custom client tool ran (the
client acted on it, and running it again could act twice) or a provider built-in tool that can
change data was called (an MCP server the provider calls, its memory, its code execution). It is
refused too when the question could not be asked again as it was: its identifiers were masked when
it was stored (``pii.history`` enforced: the original is not kept anywhere), or, with external
storage, it carried a file the model read as it is (an image, a PDF), whose bytes lived only during
its turn. The replaced answer stays in its turn's trace.

On the event stream a rewrite is one ``rewritten`` event, the first of the new turn after
``conversation``: ``{type: "rewritten", turnId: <the new turn>, replaced: [<turn ids>], messageId,
replacedFrom?}``. A client drops what it shows of the replaced turns. A replay after a rewrite never
returns the replaced turns' events (they are deleted); a client that had them drops them on the
``rewritten`` event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from evokai.db import models as m
from evokai.db.repositories import Store, Who

NOTHING = "rewrite_nothing"
"""There is no question to rewrite (a new conversation, or only confirmations so far)."""
NOT_LAST = "rewrite_not_last"
"""The message the client names is not the last question any more."""
AFTER_WRITE = "rewrite_after_write"
"""The last turn changed data: it cannot be rewritten."""
NOT_REGENERABLE = "not_regenerable"
"""The last answer cannot be regenerated (nothing to regenerate, a write, a client tool, a turn
running)."""
MASKED = "masked"
"""The mark of a question stored with identifiers masked (``pii.history``): a block
``{"masked": true}`` beside its text."""
CLIENT_PREFIX = "client_"
"""The model-facing prefix of custom client tools (``evokai.tools.custom.PREFIX``)."""
RUNNING = "rewrite_running"
"""A turn of the conversation is running: stop it or wait for it first."""
MESSAGE_KEYS = {
    NOTHING: "turn.rewriteNothing",
    NOT_LAST: "turn.rewriteNotLast",
    AFTER_WRITE: "turn.rewriteAfterWrite",
    RUNNING: "turn.rewriteRunning",
    NOT_REGENERABLE: "turn.notRegenerable",
}
"""The user-facing message (``evokai.i18n``) of each refusal."""


def _get(x: Any, key: str) -> Any:
    return x.get(key) if isinstance(x, dict) else getattr(x, key, None)


def is_question(msg: Any) -> bool:
    """Whether a message is a question the person wrote (not a confirmation or tool results only).

    Returns:
        True for a user message with text (shown, or a text block beside no tool result).
    """
    if _get(msg, "role") != "user":
        return False
    if (_get(msg, "text") or "").strip():
        return True
    blocks = [b for b in _get(msg, "content") or [] if isinstance(b, dict)]
    return not any("toolResult" in b for b in blocks) and any(
        str(b.get("text") or "").strip() and not b.get("summary") and "file" not in b
        for b in blocks
    )


def last_question(messages: list[Any]) -> int | None:
    """The index of the last question.

    Returns:
        The index, or None when there is none.
    """
    for i in range(len(messages) - 1, -1, -1):
        if is_question(messages[i]):
            return i
    return None


def wrote(messages: list[Any]) -> bool:
    """Whether a tool that changes data ran in these messages.

    Returns:
        True when a result is marked ``wrote``, or a confirmation stored before that mark existed
        approved a change.
    """
    for msg in messages:
        for b in _get(msg, "content") or []:
            tr = b.get("toolResult") if isinstance(b, dict) else None
            if isinstance(tr, dict) and tr.get("wrote"):
                return True
        for e in _get(msg, "events") or []:
            if isinstance(e, dict) and e.get("type") == "confirmed" and e.get("approve"):
                if e.get("writes", True):
                    return True
    return False


def rewritable_index(messages: list[Any]) -> int | None:
    """The index of the question that can still be rewritten, if any.

    Returns:
        The last question's index when nothing after it changed data, else None.
    """
    i = last_question(messages)
    if i is None or wrote(messages[i:]):
        return None
    return i


def acted_on_client(messages: list[Any]) -> bool:
    """Whether a custom client tool ran in these messages (the client may have acted).

    Returns:
        True when a result of a ``client_<name>`` tool is there.
    """
    for msg in messages:
        for b in _get(msg, "content") or []:
            tr = b.get("toolResult") if isinstance(b, dict) else None
            if isinstance(tr, dict) and str(tr.get("name") or "").startswith(CLIENT_PREFIX):
                return True
    return False


def acted_at_provider(messages: list[Any]) -> bool:
    """Whether a provider built-in tool that can change data was called in these messages.

    Returns:
        True when an answer carries a ``builtinTool`` mark (``engine.messages.response_blocks``).
    """
    for msg in messages:
        for b in _get(msg, "content") or []:
            bt = b.get("builtinTool") if isinstance(b, dict) else None
            if isinstance(bt, dict) and bt.get("changes"):
                return True
    return False


def asked_again(question: Any, *, files_kept: bool) -> bool:
    """Whether a question can be asked again exactly as the person asked it.

    Args:
        question: The stored question.
        files_kept: Whether the bytes of its files are kept (Evokai storage).

    Returns:
        False when it was stored masked, or carries a file read as it is whose bytes are gone.
    """
    blocks = [b for b in _get(question, "content") or [] if isinstance(b, dict)]
    if any(b.get(MASKED) for b in blocks):
        return False
    return files_kept or not any("file" in b and not b.get("text") for b in blocks)


def regenerable_index(messages: list[Any], *, files_kept: bool = True) -> int | None:
    """The index of the question whose answer can be regenerated, if any.

    Args:
        messages: The conversation's latest messages, oldest first.
        files_kept: Whether the bytes of the question's files are kept (false with external
            storage, where they live only during their turn).

    Returns:
        The last question's index when nothing after it changed data, ran a client tool or called
        a provider built-in tool that can change data, and the question can be asked again as it
        was (``asked_again``).
    """
    i = rewritable_index(messages)
    if i is None or acted_on_client(messages[i:]) or acted_at_provider(messages[i:]):
        return None
    if not asked_again(messages[i], files_kept=files_kept):
        return None
    return i


def question_text(msg: Any) -> str:
    """The text the person wrote in a question.

    Returns:
        The shown text, else its text blocks.
    """
    text = (_get(msg, "text") or "").strip()
    if text:
        return text
    blocks = [b for b in _get(msg, "content") or [] if isinstance(b, dict)]
    return "\n".join(
        str(b.get("text") or "")
        for b in blocks
        if b.get("text") and not b.get("summary") and "file" not in b
    ).strip()


@dataclass
class Plan:
    """What a rewrite replaces (Evokai storage)."""

    position: int
    """The position of the replaced question in its thread (it and everything after go)."""
    message_id: str
    created_at: datetime
    turn_ids: list[str] = field(default_factory=list)
    """The turns of the replaced messages, oldest first."""


def check(messages: list[Any], message_id: str | None) -> tuple[int | None, str | None]:
    """Whether the last question can be rewritten.

    Args:
        messages: The conversation's latest messages, oldest first.
        message_id: The message the client edits (None: the last question).

    Returns:
        ``(index, None)`` when it can, else ``(None, code)``.
    """
    i = last_question(messages)
    if i is None:
        return None, NOTHING
    if message_id is not None and _get(messages[i], "id") not in (None, message_id):
        return None, NOT_LAST
    if wrote(messages[i:]):
        return None, AFTER_WRITE
    return i, None


def plan_of(rows: list[m.Message], index: int) -> Plan:
    """The plan of a rewrite from the stored rows.

    Returns:
        The plan.
    """
    q = rows[index]
    turns = list(dict.fromkeys(r.turn_id for r in rows[index:] if r.turn_id))
    return Plan(q.position, q.id, q.created_at, turns)


async def drop(store: Store, who: Who, session_id: str, thread_id: str, plan: Plan) -> None:
    """Delete what a rewrite replaces, and what was derived from it (Evokai storage).

    Messages from the question on, their replay events, an open confirmation (closed as
    ``rewritten``), a summary that covers them, the conversation's episode and the memories its
    agents wrote since the question (an older memory one of them superseded comes back).
    """
    msg, ev, pa, cs, mem = (
        m.Message,
        m.Event,
        m.PendingAction,
        m.ConversationSummary,
        m.Memory,
    )
    owner = (who.application_id, who.subject)
    async with store.db.session() as s:
        await s.execute(
            sa.delete(msg).where(
                msg.thread_id == thread_id,
                msg.application_id == owner[0],
                msg.subject == owner[1],
                msg.position >= plan.position,
            )
        )
        if plan.turn_ids:
            await s.execute(
                sa.delete(ev).where(ev.session_id == session_id, ev.turn_id.in_(plan.turn_ids))
            )
        await s.execute(
            sa
            .update(pa)
            .where(
                pa.session_id == session_id,
                pa.application_id == owner[0],
                pa.subject == owner[1],
                pa.status == "open",
            )
            .values(status="rewritten")
        )
        await s.execute(
            sa.delete(cs).where(
                cs.session_id == session_id,
                cs.application_id == owner[0],
                cs.subject == owner[1],
                cs.up_to_position >= plan.position,
            )
        )
        gone = (
            (
                await s.execute(
                    sa.select(mem.id).where(
                        mem.application_id == owner[0],
                        mem.subject == owner[1],
                        mem.session_id == session_id,
                        sa.or_(
                            mem.kind == "episodic",
                            sa.and_(mem.source == "agent", mem.created_at >= plan.created_at),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        if gone:
            await s.execute(
                sa
                .update(mem)
                .where(
                    mem.application_id == owner[0],
                    mem.subject == owner[1],
                    mem.superseded_by.in_(gone),
                )
                .values(superseded_by=None)
            )
            await s.execute(sa.delete(mem).where(mem.id.in_(gone)))
