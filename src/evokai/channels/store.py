"""Channels and their threads."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class ChannelRepository:
    """Queries of channels; every query is scoped by application but the pollers' own."""

    def __init__(self, db: Database) -> None:
        """Bind to a database.

        Args:
            db: The database.
        """
        self.db = db

    async def all(self, application_id: str) -> list[m.ChannelRecord]:
        """An application's channels.

        Returns:
            Channels by kind and name.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.ChannelRecord)
                .where(m.ChannelRecord.application_id == application_id)
                .order_by(m.ChannelRecord.kind, m.ChannelRecord.name)
            )
            return list((await s.execute(q)).scalars().all())

    async def get(self, application_id: str, kind: str, name: str) -> m.ChannelRecord | None:
        """One channel.

        Returns:
            The channel, or None.
        """
        async with self.db.session() as s:
            q = sa.select(m.ChannelRecord).where(
                m.ChannelRecord.application_id == application_id,
                m.ChannelRecord.kind == kind,
                m.ChannelRecord.name == name,
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def by_id(self, channel_id: str) -> m.ChannelRecord | None:
        """A channel by id (the pollers).

        Returns:
            The channel, or None.
        """
        async with self.db.session() as s:
            return await s.get(m.ChannelRecord, channel_id)

    async def enabled(self, kind: str) -> list[m.ChannelRecord]:
        """Every enabled channel of a kind, of every application (the pollers).

        Returns:
            Channels.
        """
        async with self.db.session() as s:
            q = sa.select(m.ChannelRecord).where(
                m.ChannelRecord.kind == kind, m.ChannelRecord.enabled.is_(True)
            )
            return list((await s.execute(q)).scalars().all())

    async def put(
        self,
        application_id: str,
        kind: str,
        name: str,
        *,
        agent: str,
        enabled: bool,
        config: dict[str, Any],
    ) -> tuple[m.ChannelRecord | None, m.ChannelRecord]:
        """Create or replace a channel.

        Returns:
            (a copy of the channel before, or None) and the channel after.
        """
        async with self.db.session() as s:
            q = sa.select(m.ChannelRecord).where(
                m.ChannelRecord.application_id == application_id,
                m.ChannelRecord.kind == kind,
                m.ChannelRecord.name == name,
            )
            row = (await s.execute(q)).scalar_one_or_none()
            before = None
            if row is None:
                row = m.ChannelRecord(
                    id=new_id(),
                    application_id=application_id,
                    kind=kind,
                    name=name,
                    state={},
                    created_at=utcnow(),
                )
                s.add(row)
            else:
                before = m.ChannelRecord(
                    id=row.id,
                    application_id=row.application_id,
                    kind=row.kind,
                    name=row.name,
                    agent=row.agent,
                    enabled=row.enabled,
                    config=dict(row.config or {}),
                )
                if row.config != config:
                    row.state = {}
                    row.status, row.last_error = None, None
            row.agent = agent
            row.enabled = enabled
            row.config = config
            row.updated_at = utcnow()
            await s.flush()
            s.expunge(row)
            return before, row

    async def delete(self, application_id: str, kind: str, name: str) -> bool:
        """Delete a channel and its threads.

        Returns:
            Whether it existed.
        """
        async with self.db.session() as s:
            row = (
                await s.execute(
                    sa.select(m.ChannelRecord).where(
                        m.ChannelRecord.application_id == application_id,
                        m.ChannelRecord.kind == kind,
                        m.ChannelRecord.name == name,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await s.execute(sa.delete(m.ChannelThread).where(m.ChannelThread.channel_id == row.id))
            await s.delete(row)
            return True

    async def set_state(self, channel_id: str, state: dict[str, Any]) -> None:
        """Keep what a channel needs between deliveries."""
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.ChannelRecord)
                .where(m.ChannelRecord.id == channel_id)
                .values(state=state)
            )

    async def seen(self, channel_id: str, *, status: str, error: str | None = None) -> None:
        """Write down a delivery's or a test's outcome."""
        values: dict[str, Any] = {"status": status, "last_error": (error or None) and error[:300]}
        if status == "ok":
            values["last_event_at"] = utcnow()
        async with self.db.session() as s:
            await s.execute(
                sa.update(m.ChannelRecord).where(m.ChannelRecord.id == channel_id).values(**values)
            )

    # threads

    async def thread(self, channel_id: str, keys: list[str]) -> m.ChannelThread | None:
        """The thread one of these keys belongs to.

        Returns:
            The thread, or None.
        """
        if not keys:
            return None
        async with self.db.session() as s:
            q = (
                sa
                .select(m.ChannelThread)
                .where(
                    m.ChannelThread.channel_id == channel_id,
                    m.ChannelThread.thread_key.in_(keys),
                )
                .order_by(m.ChannelThread.created_at)
                .limit(1)
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def link(
        self,
        channel: m.ChannelRecord,
        key: str,
        *,
        subject: str,
        conversation_id: str,
        reference: dict[str, Any] | None = None,
    ) -> None:
        """Bind a key to a conversation (a new thread, or one more message id of a thread)."""
        async with self.db.session() as s:
            q = sa.select(m.ChannelThread).where(
                m.ChannelThread.channel_id == channel.id, m.ChannelThread.thread_key == key
            )
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                s.add(
                    m.ChannelThread(
                        id=new_id(),
                        application_id=channel.application_id,
                        channel_id=channel.id,
                        thread_key=key,
                        subject=subject,
                        conversation_id=conversation_id,
                        reference=reference or {},
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            else:
                row.conversation_id = conversation_id
                if reference is not None:
                    row.reference = reference
                row.updated_at = utcnow()

    async def counts(self, application_id: str) -> dict[str, int]:
        """Conversations per channel.

        Returns:
            channel id → distinct conversations.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(
                    m.ChannelThread.channel_id,
                    sa.func.count(sa.distinct(m.ChannelThread.conversation_id)),
                )
                .where(m.ChannelThread.application_id == application_id)
                .group_by(m.ChannelThread.channel_id)
            )
            return {str(c): int(n) for c, n in (await s.execute(q)).all()}
