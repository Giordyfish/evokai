"""Queries on memories and memory consents, always scoped by application and subject."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database
from evokai.db.repositories import Who, _rowcount

SUPERSEDED_KEPT = timedelta(days=30)
"""A superseded memory is purged this long after it was replaced (the person can still see it
was replaced in the meantime)."""


def _live(r: type[m.Memory], now: datetime) -> Any:
    return sa.and_(r.superseded_by.is_(None), sa.or_(r.expires_at.is_(None), r.expires_at > now))


def memory_view(row: m.Memory) -> dict[str, Any]:
    """A memory for the person and the APIs.

    Returns:
        id, text, kind, agent, source, createdAt, updatedAt, expiresAt, lastUsedAt, uses.
    """

    def iso(v: datetime | None) -> str | None:
        return v.isoformat() if v else None

    return {
        "id": row.id,
        "text": row.fact,
        "kind": row.kind,
        "agent": row.agent_name or None,
        "source": row.source,
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
        "expiresAt": iso(row.expires_at),
        "lastUsedAt": iso(row.last_used_at),
        "uses": row.uses,
    }


class MemoryRepository:
    """What agents remember about a subject, and the subject's consent."""

    def __init__(self, db: Database) -> None:
        """Bind to a database."""
        self.db = db

    # reading

    async def facts(self, w: Who) -> list[dict[str, str]]:
        """A subject's active memories (id and text), oldest first.

        Returns:
            id and fact of each.
        """
        return [{"id": x.id, "fact": x.fact} for x in await self.items(w)]

    async def items(self, w: Who, *, limit: int = 200) -> list[m.Memory]:
        """A subject's active memories, oldest first (superseded and expired ones left out).

        Returns:
            Rows.
        """
        async with self.db.session() as s:
            r = m.Memory
            q = (
                sa
                .select(r)
                .where(
                    r.application_id == w.application_id,
                    r.subject == w.subject,
                    _live(r, utcnow()),
                )
                .order_by(r.created_at)
                .limit(limit)
            )
            return list((await s.execute(q)).scalars().all())

    async def recallable(self, w: Who, agent: str) -> list[m.Memory]:
        """Memories one agent may recall: every preference and fact, and its own episodes.

        Returns:
            Rows.
        """
        async with self.db.session() as s:
            r = m.Memory
            q = (
                sa
                .select(r)
                .where(
                    r.application_id == w.application_id,
                    r.subject == w.subject,
                    _live(r, utcnow()),
                    sa.or_(r.kind != "episodic", r.agent_name == agent),
                )
                .order_by(r.created_at.desc())
                .limit(500)
            )
            return list((await s.execute(q)).scalars().all())

    async def get(self, w: Who, memory_id: str) -> m.Memory | None:
        """One of the subject's memories.

        Returns:
            The row or None.
        """
        async with self.db.session() as s:
            r = m.Memory
            q = sa.select(r).where(
                r.id == memory_id, r.application_id == w.application_id, r.subject == w.subject
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def episode_of(self, application_id: str, session_id: str) -> bool:
        """Whether a conversation already has its episode.

        Returns:
            True when summarised.
        """
        async with self.db.session() as s:
            r = m.Memory
            q = sa.select(r.id).where(
                r.application_id == application_id,
                r.session_id == session_id,
                r.kind == "episodic",
            )
            return (await s.execute(q)).first() is not None

    # writing

    async def add(self, w: Who, fact: str, **fields: Any) -> str:
        """Store a memory.

        Returns:
            Its id.
        """
        mem = m.Memory(
            id=new_id(),
            application_id=w.application_id,
            subject=w.subject,
            fact=fact[:1000],
            updated_at=utcnow(),
            **fields,
        )
        async with self.db.session() as s:
            s.add(mem)
        return mem.id

    async def remember(self, w: Who, fact: str) -> str:
        """Store a preference as it is (no consolidation; ``MemoryService.remember`` does that).

        Returns:
            Its id.
        """
        return await self.add(w, fact)

    async def update(self, w: Who, memory_id: str, **fields: Any) -> bool:
        """Change a memory.

        Returns:
            Whether it exists.
        """
        async with self.db.session() as s:
            r = m.Memory
            q = sa.update(r).where(
                r.id == memory_id, r.application_id == w.application_id, r.subject == w.subject
            )
            return _rowcount(await s.execute(q.values(updated_at=utcnow(), **fields))) > 0

    async def supersede(self, w: Who, old_id: str, new_id_: str) -> None:
        """Mark a memory as replaced by a newer one."""
        await self.update(w, old_id, superseded_by=new_id_)

    async def touch(self, w: Who, ids: list[str], now: datetime) -> None:
        """Note that memories were recalled into a prompt."""
        if not ids:
            return
        async with self.db.session() as s:
            r = m.Memory
            await s.execute(
                sa
                .update(r)
                .where(r.application_id == w.application_id, r.subject == w.subject, r.id.in_(ids))
                .values(last_used_at=now, uses=r.uses + 1)
            )

    async def forget(self, w: Who, memory_id: str | None = None) -> int:
        """Delete one memory, or all of a subject's.

        Everything goes with all of them: the memory files of the ``memory`` capability too (a
        withdrawn consent, the person's «forget everything», an erasure).

        Returns:
            How many were deleted.
        """
        async with self.db.session() as s:
            r = m.Memory
            q = sa.delete(r).where(r.application_id == w.application_id, r.subject == w.subject)
            if memory_id:
                q = q.where(r.id == memory_id)
            deleted = _rowcount(await s.execute(q))
            if not memory_id:
                c = m.CapabilityRecord
                await s.execute(
                    sa.delete(c).where(
                        c.application_id == w.application_id,
                        c.subject == w.subject,
                        c.capability == "memory",
                    )
                )
            return deleted

    async def purge(self, now: datetime | None = None) -> int:
        """Delete expired memories and those superseded more than 30 days ago.

        Returns:
            How many.
        """
        now = now or utcnow()
        async with self.db.session() as s:
            r = m.Memory
            q = sa.delete(r).where(
                sa.or_(
                    sa.and_(r.expires_at.is_not(None), r.expires_at <= now),
                    sa.and_(r.superseded_by.is_not(None), r.updated_at < now - SUPERSEDED_KEPT),
                )
            )
            return _rowcount(await s.execute(q))

    # consent

    async def consent(self, w: Who) -> m.MemoryConsent | None:
        """The subject's recorded answer, if any.

        Returns:
            The row or None.
        """
        async with self.db.session() as s:
            c = m.MemoryConsent
            q = sa.select(c).where(c.application_id == w.application_id, c.subject == w.subject)
            return (await s.execute(q)).scalar_one_or_none()

    async def set_consent(self, w: Who, granted: bool, source: str) -> None:
        """Record the subject's answer (the latest one counts)."""
        async with self.db.session() as s:
            c = m.MemoryConsent
            same = (c.application_id == w.application_id, c.subject == w.subject)
            res = await s.execute(
                sa
                .update(c)
                .where(*same)
                .values(granted=granted, source=source, decided_at=utcnow())
            )
            if _rowcount(res) == 0:
                s.add(
                    c(
                        id=new_id(),
                        application_id=w.application_id,
                        subject=w.subject,
                        granted=granted,
                        source=source,
                        decided_at=utcnow(),
                    )
                )

    async def forget_consent(self, w: Who) -> int:
        """Delete the subject's consent record (erase).

        Returns:
            How many rows.
        """
        async with self.db.session() as s:
            c = m.MemoryConsent
            return _rowcount(
                await s.execute(
                    sa.delete(c).where(c.application_id == w.application_id, c.subject == w.subject)
                )
            )
