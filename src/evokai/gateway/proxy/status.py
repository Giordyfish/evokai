"""The proxy's status for the console: what the running process reports, the last day's figures.

``evokai proxy`` and the API are separate processes. Each proxy instance writes a heartbeat every
``HEARTBEAT_SECONDS`` into the console settings table (key ``proxy.status:<instance>``): when it
wrote, the providers it serves, its port and how many PCs (network addresses with a recognised
person) it saw in the last 24 hours. The API reads the heartbeats and counts the rest from the
records the proxy writes anyway (usage rows with a ``surface``, ``proxy.*`` security events,
identity bindings). Nothing here holds content.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from evokai.core.util import utcnow
from evokai.db import models as m

log = logging.getLogger("evokai.gateway.proxy")

HEARTBEAT_KEY = "proxy.status:"
"""The prefix of each instance's heartbeat in the console settings table."""
HEARTBEAT_SECONDS = 60
STALE_SECONDS = 180
"""A heartbeat older than this means the instance is not running."""
DAY = 24 * 3600
MAX_ADDRESSES = 100_000

FORMAT_UNKNOWN = "proxy.format_unknown"
ORG_REFUSED = "proxy.org_refused"
UNIDENTIFIED = "proxy.unidentified"
UNIDENTIFIED_PREFIX = "unidentified "
"""How a request recorded in ``record`` mode names its person (``service.ProxyService``)."""


class Addresses:
    """The PCs' addresses seen with a recognised person, over the last day (in memory)."""

    def __init__(self) -> None:
        """Start empty."""
        self._seen: dict[str, float] = {}

    def note(self, address: str, now: float | None = None) -> None:
        """Note a PC's address."""
        if not address:
            return
        self._seen[address] = time.time() if now is None else now
        if len(self._seen) > MAX_ADDRESSES:
            self.prune()
            while len(self._seen) > MAX_ADDRESSES:
                self._seen.pop(next(iter(self._seen)))

    def prune(self, now: float | None = None) -> None:
        """Forget addresses not seen for a day."""
        cut = (time.time() if now is None else now) - DAY
        self._seen = {a: t for a, t in self._seen.items() if t >= cut}

    def count(self, now: float | None = None) -> int:
        """How many addresses were seen in the last day.

        Returns:
            The count.
        """
        self.prune(now)
        return len(self._seen)


def instance_name() -> str:
    """This instance's name in its heartbeat key (the host name: a container's own).

    Returns:
        The name, at most 60 characters.
    """
    return (socket.gethostname() or "proxy")[:60]


async def beat(svc: Any, proxy: Any, port: int, instance: str | None = None) -> None:
    """Write one heartbeat of this instance."""
    value = {
        "at": utcnow().isoformat(),
        "providers": list(proxy.providers),
        "port": int(port),
        "pcs24h": proxy.book.addresses.count(),
        "commit": getattr(svc.settings, "build_commit", "") or "",
    }
    await svc.store.console_settings.put(
        HEARTBEAT_KEY + (instance or instance_name()), value, "proxy"
    )


async def run_heartbeat(svc: Any, proxy: Any, port: int) -> None:
    """Write a heartbeat now and every ``HEARTBEAT_SECONDS`` (a background task)."""
    instance = instance_name()
    while True:
        try:
            await beat(svc, proxy, port, instance)
        except Exception as e:
            log.warning("proxy_heartbeat_failed", extra={"error_type": type(e).__name__})
        await asyncio.sleep(HEARTBEAT_SECONDS)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)


async def heartbeats(db: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """The instances' heartbeats, newest first, each with whether it is fresh.

    Returns:
        instance, at, providers, port, pcs24h, running.
    """
    now = now or utcnow()
    c = m.ConsoleSetting
    async with db.session() as s:
        rows = (
            (await s.execute(sa.select(c).where(c.key.like(f"{HEARTBEAT_KEY}%")))).scalars().all()
        )
    out = []
    for row in rows:
        v = dict(row.value or {})
        try:
            at = _aware(datetime.fromisoformat(str(v.get("at"))))
        except ValueError:
            continue
        if now - at > timedelta(days=7):
            continue
        out.append({
            "instance": row.key[len(HEARTBEAT_KEY) :],
            "at": at.isoformat(),
            "providers": [str(p) for p in v.get("providers") or []],
            "port": v.get("port"),
            "pcs24h": int(v.get("pcs24h") or 0),
            "running": (now - at).total_seconds() <= STALE_SECONDS,
        })
    return sorted(out, key=lambda h: h["at"], reverse=True)


async def last_day(db: Any, gateway_names: dict[str, str], now: datetime | None = None) -> dict:
    """The proxy's figures of the last 24 hours, and its alerts of the last 7 days.

    Args:
        db: The database.
        gateway_names: Gateway id to name (events are a gateway's).
        now: The time to count from.

    Returns:
        requests, people (identified people with requests), accounts (accounts seen),
        unidentifiedRecorded, unidentifiedRefused, surfaces, formatAlerts, refusedOrgs.
    """
    now = now or utcnow()
    since = now - timedelta(days=1)
    week = now - timedelta(days=7)
    r = m.GatewayRequest
    e = m.SecurityEvent
    i = m.ProxyIdentity
    async with db.session() as s:
        proxied = sa.and_(r.surface.is_not(None), r.created_at >= since)
        requests = (await s.execute(sa.select(sa.func.count()).where(proxied))).scalar() or 0
        unidentified = sa.and_(proxied, r.person.like(f"{UNIDENTIFIED_PREFIX}%"))
        recorded = (await s.execute(sa.select(sa.func.count()).where(unidentified))).scalar() or 0
        people = (
            await s.execute(
                sa.select(sa.func.count(sa.distinct(r.key_id))).where(
                    proxied,
                    r.key_id.is_not(None),
                    sa.not_(r.person.like(f"{UNIDENTIFIED_PREFIX}%")),
                )
            )
        ).scalar() or 0
        surfaces = {
            str(name): int(n)
            for name, n in (
                await s.execute(
                    sa.select(r.surface, sa.func.count()).where(proxied).group_by(r.surface)
                )
            ).all()
        }
        accounts = (
            await s.execute(
                sa.select(sa.func.count(sa.distinct(i.email))).where(
                    i.last_seen >= since, i.email.is_not(None)
                )
            )
        ).scalar() or 0
        refused = (
            await s.execute(
                sa.select(sa.func.count()).where(e.control == UNIDENTIFIED, e.created_at >= since)
            )
        ).scalar() or 0
        alerts = (
            (
                await s.execute(
                    sa
                    .select(e)
                    .where(e.control == FORMAT_UNKNOWN, e.created_at >= week)
                    .order_by(e.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        orgs = (
            (
                await s.execute(
                    sa
                    .select(e)
                    .where(e.control == ORG_REFUSED, e.created_at >= week)
                    .order_by(e.created_at.desc())
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in orgs:
        org = str((ev.details or {}).get("org") or "")
        g = grouped.setdefault(
            (ev.rule, org),
            {"provider": ev.rule, "org": org, "events": 0, "people": set(), "lastAt": None},
        )
        g["events"] += 1
        if ev.subject:
            g["people"].add(ev.subject)
        at = _aware(ev.created_at).isoformat()
        g["lastAt"] = max(g["lastAt"] or at, at)
    return {
        "requests": int(requests),
        "people": int(people),
        "accounts": int(accounts),
        "unidentifiedRecorded": int(recorded),
        "unidentifiedRefused": int(refused),
        "surfaces": surfaces,
        "formatAlerts": [
            {
                "at": _aware(ev.created_at).isoformat(),
                "gateway": gateway_names.get(ev.application_id, ""),
                "adapter": ev.rule,
                "why": str((ev.details or {}).get("why") or "")[:200],
                "host": str((ev.details or {}).get("host") or "")[:120],
            }
            for ev in alerts
        ],
        "refusedOrgs": [
            {**g, "people": len(g["people"])}
            for g in sorted(grouped.values(), key=lambda g: g["lastAt"] or "", reverse=True)
        ],
    }
