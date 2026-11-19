"""Queries on what harness capabilities keep, always scoped by application and subject.

``evokai_capability_records`` holds a capability's items: memory files, plans, step-log records,
spilled tool results, spend counters (``evokai.capabilities.stores`` builds the harness stores on
it). Rows of a conversation carry its ``session_id`` and go with it; the others are the person's,
exported and erased with them and purged at ``expires_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database
from evokai.db.repositories import _rowcount

ANY = object()
"""``put`` without a version check."""


class CapabilityRecordRepository:
    """A capability's items for a person or a conversation."""

    def __init__(self, db: Database) -> None:
        """Bind to a database."""
        self.db = db

    @staticmethod
    def _where(app_id: str, subject: str, capability: str, scope: str) -> Any:
        r = m.CapabilityRecord
        return sa.and_(
            r.application_id == app_id,
            r.subject == subject,
            r.capability == capability,
            r.scope == scope,
        )

    async def items(
        self, app_id: str, subject: str, capability: str, scope: str, *, prefix: str = ""
    ) -> list[m.CapabilityRecord]:
        """Every live item of one capability scope, by key.

        Returns:
            Rows, sorted by key.
        """
        r = m.CapabilityRecord
        now = utcnow()
        q = sa.select(r).where(
            self._where(app_id, subject, capability, scope),
            sa.or_(r.expires_at.is_(None), r.expires_at > now),
        )
        if prefix:
            q = q.where(r.key.startswith(prefix, autoescape=True))
        async with self.db.session() as s:
            return list((await s.execute(q.order_by(r.key))).scalars().all())

    async def get(
        self, app_id: str, subject: str, capability: str, scope: str, key: str
    ) -> m.CapabilityRecord | None:
        """One live item.

        Returns:
            The row, or None.
        """
        r = m.CapabilityRecord
        now = utcnow()
        q = sa.select(r).where(
            self._where(app_id, subject, capability, scope),
            r.key == key,
            sa.or_(r.expires_at.is_(None), r.expires_at > now),
        )
        async with self.db.session() as s:
            return (await s.execute(q)).scalar_one_or_none()

    async def put(
        self,
        app_id: str,
        subject: str,
        capability: str,
        scope: str,
        key: str,
        value: str,
        *,
        session_id: str | None = None,
        expires_at: datetime | None = None,
        meta: dict[str, Any] | None = None,
        expected: Any = ANY,
    ) -> str | None:
        """Create or replace an item.

        Args:
            app_id: The application.
            subject: The person (empty for the application).
            capability: The capability's id.
            scope: Inside the capability (an agent, a conversation).
            key: The item.
            value: Its content.
            session_id: The conversation it belongs to (deleted with it).
            expires_at: When it is purged.
            meta: What the capability records about the write.
            expected: The version it must have (None: it must not exist); ``ANY`` skips the check.

        Returns:
            The new version, or None when the expected version did not match.
        """
        r = m.CapabilityRecord
        now = utcnow()
        version = new_id()
        async with self.db.session() as s:
            q = sa.select(r).where(self._where(app_id, subject, capability, scope), r.key == key)
            row = (await s.execute(q)).scalar_one_or_none()
            if row is not None and row.expires_at is not None and _aware(row.expires_at) <= now:
                await s.delete(row)
                await s.flush()
                row = None
            current = row.version if row is not None else None
            if expected is not ANY and expected != current:
                return None
            if row is None:
                s.add(
                    m.CapabilityRecord(
                        application_id=app_id,
                        subject=subject,
                        session_id=session_id,
                        capability=capability,
                        scope=scope,
                        key=key,
                        value=value,
                        version=version,
                        meta=meta,
                        created_at=now,
                        updated_at=now,
                        expires_at=expires_at,
                    )
                )
            else:
                row.value, row.version, row.meta = value, version, meta
                row.updated_at, row.expires_at = now, expires_at
                if session_id is not None:
                    row.session_id = session_id
            return version

    async def delete(
        self,
        app_id: str,
        subject: str,
        capability: str,
        scope: str,
        key: str,
        *,
        expected: Any = ANY,
    ) -> bool | None:
        """Delete an item.

        Returns:
            Whether it existed; None when the expected version did not match.
        """
        r = m.CapabilityRecord
        async with self.db.session() as s:
            q = sa.select(r).where(self._where(app_id, subject, capability, scope), r.key == key)
            row = (await s.execute(q)).scalar_one_or_none()
            current = row.version if row is not None else None
            if expected is not ANY and expected != current:
                return None
            if row is None:
                return False
            await s.delete(row)
            return True

    async def forget(self, app_id: str, subject: str, capability: str | None = None) -> int:
        """Delete a person's items (of one capability, or all).

        Returns:
            How many.
        """
        r = m.CapabilityRecord
        q = sa.delete(r).where(r.application_id == app_id, r.subject == subject)
        if capability is not None:
            q = q.where(r.capability == capability)
        async with self.db.session() as s:
            return _rowcount(await s.execute(q))

    async def for_subject(self, app_id: str, subject: str) -> list[m.CapabilityRecord]:
        """Everything kept for a person (an export).

        Returns:
            Rows, oldest first.
        """
        r = m.CapabilityRecord
        q = (
            sa
            .select(r)
            .where(r.application_id == app_id, r.subject == subject)
            .order_by(r.created_at, r.key)
        )
        async with self.db.session() as s:
            return list((await s.execute(q)).scalars().all())

    async def purge(self, now: datetime | None = None) -> int:
        """Delete expired items.

        Returns:
            How many.
        """
        r = m.CapabilityRecord
        now = now or utcnow()
        async with self.db.session() as s:
            q = sa.delete(r).where(r.expires_at.is_not(None), r.expires_at <= now)
            return _rowcount(await s.execute(q))


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


class CapabilityCredentialRepository:
    """An application's credentials per provider, encrypted with the deployment's key."""

    def __init__(self, db: Database) -> None:
        """Bind to a database."""
        self.db = db

    async def all(self, app_id: str) -> list[m.CapabilityCredential]:
        """Every provider the application has credentials for.

        Returns:
            Rows.
        """
        r = m.CapabilityCredential
        async with self.db.session() as s:
            q = sa.select(r).where(r.application_id == app_id).order_by(r.provider)
            return list((await s.execute(q)).scalars().all())

    async def get(self, app_id: str, provider: str) -> m.CapabilityCredential | None:
        """One provider's credentials.

        Returns:
            The row, or None.
        """
        r = m.CapabilityCredential
        async with self.db.session() as s:
            q = sa.select(r).where(r.application_id == app_id, r.provider == provider)
            return (await s.execute(q)).scalar_one_or_none()

    async def put(
        self,
        app_id: str,
        provider: str,
        *,
        values_encrypted: str,
        names: list[str],
        plain: dict[str, str] | None,
    ) -> None:
        """Replace one provider's credentials."""
        r = m.CapabilityCredential
        async with self.db.session() as s:
            q = sa.select(r).where(r.application_id == app_id, r.provider == provider)
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                s.add(
                    m.CapabilityCredential(
                        application_id=app_id,
                        provider=provider,
                        values_encrypted=values_encrypted,
                        names=names,
                        plain=plain,
                        updated_at=utcnow(),
                    )
                )
            else:
                row.values_encrypted, row.names, row.plain = values_encrypted, names, plain
                row.updated_at = utcnow()

    async def delete(self, app_id: str, provider: str) -> bool:
        """Remove one provider's credentials.

        Returns:
            Whether there were any.
        """
        r = m.CapabilityCredential
        async with self.db.session() as s:
            q = sa.delete(r).where(r.application_id == app_id, r.provider == provider)
            return _rowcount(await s.execute(q)) > 0
