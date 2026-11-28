"""The gateway's request records: one row per request, never its content."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database
from evokai.db.kinds import gateways_only


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class GatewayRepository:
    """Requests through the gateways: usage per person and model, outcomes, timings."""

    def __init__(self, db: Database) -> None:
        """Hold the database."""
        self.db = db

    async def add(self, **fields: Any) -> None:
        """Record one request."""
        async with self.db.session() as s:
            s.add(m.GatewayRequest(id=new_id(), created_at=utcnow(), **fields))

    async def tokens_since(self, key_id: str, since: datetime) -> int:
        """Input and output tokens of a person's key since a moment (their token caps).

        Returns:
            The sum.
        """
        r = m.GatewayRequest
        async with self.db.session() as s:
            q = sa.select(sa.func.coalesce(sa.func.sum(r.input_tokens + r.output_tokens), 0)).where(
                r.key_id == key_id, r.created_at >= since
            )
            return int((await s.execute(q)).scalar_one())

    async def usage(self, gateway_id: str, since: datetime) -> dict[str, Any]:
        """A gateway's usage since a moment, per person and per model, and by outcome.

        Returns:
            ``people``, ``models`` (requests and tokens each) and ``outcomes`` (outcome to count).
        """
        r = m.GatewayRequest
        tokens = [
            sa.func.count().label("requests"),
            sa.func.coalesce(sa.func.sum(r.input_tokens), 0).label("input"),
            sa.func.coalesce(sa.func.sum(r.output_tokens), 0).label("output"),
            sa.func.coalesce(sa.func.sum(r.cache_read_tokens), 0).label("cache_read"),
            sa.func.coalesce(sa.func.sum(r.cache_write_tokens), 0).label("cache_write"),
            sa.func.sum(sa.case((r.outcome == "blocked", 1), else_=0)).label("blocked"),
        ]
        where = (r.application_id == gateway_id, r.created_at >= since)

        def rows(res: Any, *keys: str) -> list[dict[str, Any]]:
            return [
                {
                    **{k: row[i] for i, k in enumerate(keys)},
                    "requests": int(row.requests),
                    "inputTokens": int(row.input),
                    "outputTokens": int(row.output),
                    "cacheReadTokens": int(row.cache_read),
                    "cacheWriteTokens": int(row.cache_write),
                    "blocked": int(row.blocked or 0),
                }
                for row in res
            ]

        async with self.db.session() as s:
            people = await s.execute(
                sa
                .select(r.key_id, sa.func.max(r.person).label("person"), *tokens)
                .where(*where)
                .group_by(r.key_id)
                .order_by(sa.desc("output"))
            )
            models = await s.execute(
                sa
                .select(r.provider, r.model, *tokens)
                .where(*where)
                .group_by(r.provider, r.model)
                .order_by(sa.desc("output"))
            )
            outcomes = await s.execute(
                sa.select(r.outcome, sa.func.count()).where(*where).group_by(r.outcome)
            )
            return {
                "people": rows(people, "keyId", "person"),
                "models": rows(models, "provider", "model"),
                "outcomes": {o: int(n) for o, n in outcomes},
            }

    async def recent(self, gateway_id: str, limit: int = 50) -> list[m.GatewayRequest]:
        """A gateway's latest requests.

        Returns:
            Rows, newest first.
        """
        r = m.GatewayRequest
        async with self.db.session() as s:
            q = (
                sa
                .select(r)
                .where(r.application_id == gateway_id)
                .order_by(r.created_at.desc())
                .limit(limit)
            )
            return list((await s.execute(q)).scalars().all())

    async def purge(self, ttl_days: int | None, now: datetime | None = None) -> int:
        """Delete records past their gateway's time to live.

        A gateway keeps its requests as long as its security events (its own setting, else the
        deployment's).

        Returns:
            How many were deleted.
        """
        now = now or utcnow()
        deleted = 0
        async with self.db.session() as s:
            gateways = (
                await s.execute(
                    # Retention runs while the gateway module is off too.
                    gateways_only(
                        sa.select(m.Application.id, m.Application.security_event_ttl_days),
                        even_off=True,
                    )
                )
            ).all()
            for gateway_id, ttl in gateways:
                days = ttl or ttl_days
                if not days:
                    continue
                q = sa.delete(m.GatewayRequest).where(
                    m.GatewayRequest.application_id == gateway_id,
                    m.GatewayRequest.created_at < now - timedelta(days=int(days)),
                )
                deleted += _rowcount(await s.execute(q))
        return deleted
