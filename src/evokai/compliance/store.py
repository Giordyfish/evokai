"""Where compliance profiles and snapshots are kept.

In the console settings table (``evokai_console_settings``, a key and a JSON value), so the
feature needs no migration (keys stay within its 60 characters): ``cmp.p:<application id>``
holds the profile and ``cmp.s:<application id>:<snapshot id>`` each snapshot. Snapshots are
immutable: created, listed, read, never changed. They hold facts about the configuration,
never personal data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.db.engine import Database

PROFILE = "cmp.p:"
SNAPSHOT = "cmp.s:"


def _snap_key(application_id: str, snapshot_id: str) -> str:
    return f"{SNAPSHOT}{application_id}:{snapshot_id}"


def new_snapshot_id(now: datetime | None = None) -> str:
    """A snapshot id that sorts by time: ``YYYYMMDDHHMMSS`` and the hundredths of a second.

    Returns:
        The id (16 digits).
    """
    now = (now or utcnow()).astimezone(UTC)
    return f"{now:%Y%m%d%H%M%S}{now.microsecond // 10_000:02d}"


class ComplianceRepository:
    """Profiles and snapshots of the applications."""

    def __init__(self, db: Database) -> None:
        """Bind to a database.

        Args:
            db: The database.
        """
        self.db = db

    async def profile(self, application_id: str) -> dict[str, Any] | None:
        """An application's stored profile.

        Returns:
            The profile, or None.
        """
        async with self.db.session() as s:
            row = await s.get(m.ConsoleSetting, f"{PROFILE}{application_id}")
            return dict(row.value) if row else None

    async def put_profile(self, application_id: str, value: dict[str, Any], by: str) -> None:
        """Store an application's profile."""
        key = f"{PROFILE}{application_id}"
        async with self.db.session() as s:
            row = await s.get(m.ConsoleSetting, key)
            if row is None:
                s.add(m.ConsoleSetting(key=key, value=value, updated_at=utcnow(), updated_by=by))
            else:
                row.value, row.updated_at, row.updated_by = value, utcnow(), by[:200]

    async def add_snapshot(self, application_id: str, snapshot: dict[str, Any], by: str) -> None:
        """Store a snapshot (its ``id`` is in it)."""
        async with self.db.session() as s:
            s.add(
                m.ConsoleSetting(
                    key=_snap_key(application_id, snapshot["id"]),
                    value=snapshot,
                    updated_at=utcnow(),
                    updated_by=by[:200],
                )
            )

    async def snapshot(self, application_id: str, snapshot_id: str) -> dict[str, Any] | None:
        """One snapshot.

        Returns:
            The snapshot, or None.
        """
        if not snapshot_id.isalnum():
            return None
        async with self.db.session() as s:
            row = await s.get(m.ConsoleSetting, _snap_key(application_id, snapshot_id))
            return dict(row.value) if row else None

    async def snapshots(self, application_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """An application's snapshots, newest first, without their facts.

        Returns:
            id, createdAt, label, by, classification, packs.
        """
        c = m.ConsoleSetting
        prefix = f"{SNAPSHOT}{application_id}:"
        async with self.db.session() as s:
            q = (
                sa
                .select(c.value)
                .where(c.key.like(f"{prefix}%"))
                .order_by(c.key.desc())
                .limit(limit)
            )
            rows = (await s.execute(q)).scalars().all()
        return [{k: v for k, v in dict(r).items() if k != "facts"} for r in rows]

    async def forget(self, application_id: str) -> int:
        """Delete an application's profile and snapshots (when the application goes).

        Returns:
            Rows deleted.
        """
        c = m.ConsoleSetting
        async with self.db.session() as s:
            r = await s.execute(
                sa.delete(c).where(
                    sa.or_(
                        c.key == f"{PROFILE}{application_id}",
                        c.key.like(f"{SNAPSHOT}{application_id}:%"),
                    )
                )
            )
            return int(getattr(r, "rowcount", 0) or 0)
