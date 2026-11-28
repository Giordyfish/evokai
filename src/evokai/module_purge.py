"""``evokai module purge <applications|gateway>``: delete a module's data for good.

Only while the module is off (``evokai.core.modules``). The plan counts what would go (the dry
run); the export writes those rows to a JSON file (key hashes and encrypted secrets left out);
the purge revokes the module's keys first, then deletes its rows in one transaction and writes one
audit row. The audit log is core: its entries about the module stay until its own retention.

A gateway or an application is an application row (``evokai.db.kinds``): the module's rows are
its application rows of that kind and every row that carries one of their ids
(``application_id``, ``owner_id`` for policy versions and tests), plus the rows hanging off those
(events and threads of its sessions, results of its eval runs, its compliance settings).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from evokai.core import modules
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.db.kinds import applications_only, gateways_only
from evokai.db.repositories import Store

KEEP = frozenset({"evokai_audit_log"})
"""Tables of the core that keep their rows (the audit log has its own retention)."""
SECRET_COLUMNS = ("secret", "hash", "token", "password", "encrypted", "ciphertext")
"""Columns left out of the export (parts of the name)."""

OUTSIDE: dict[str, list[str]] = {
    modules.GATEWAY: [
        "the proxy's CA key (EVOKAI_PROXY_CA_KEY, for example a Key Vault secret)",
        "the MDM profiles that install the CA certificate and the proxy settings on the PCs",
        "the providers' IP allowlists that name Evokai's egress address",
        "the coding tools' settings that point at the gateway's base URL",
    ],
    modules.APPLICATIONS: [
        "files kept by external stores the deployment configured (blob containers, search "
        "indexes, vector stores)",
        "the client applications' own copies of conversations (external storage mode)",
        "keys and webhooks registered with channel providers (Slack, Teams, mail)",
    ],
}
"""What lives outside Evokai and stays after a purge, per module."""


class PurgeRefused(Exception):
    """The module is on, or the configuration pins it on."""


@dataclass
class Plan:
    """What a purge would delete."""

    module: str
    ids: list[str]
    slugs: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    """Rows per table (only tables with rows)."""
    keys: int = 0
    """Live keys that will be revoked."""

    @property
    def total(self) -> int:
        """Every row the purge deletes."""
        return sum(self.counts.values())


def _of_module(module: str, q: Any) -> Any:
    """The module's application rows, whatever the switch says (the module is off).

    Returns:
        The query.
    """
    if module == modules.GATEWAY:
        return gateways_only(q, even_off=True)
    return applications_only(q, even_off=True)


def _statements(ids: list[str]) -> list[tuple[str, Any]]:
    """Each table's rows of these application ids, children before their parents.

    Returns:
        (table name, where clause) pairs.
    """
    sessions = sa.select(m.Session.id).where(m.Session.application_id.in_(ids))
    runs = sa.select(m.EvalRun.id).where(m.EvalRun.application_id.in_(ids))
    out: list[tuple[str, Any]] = [
        (m.Event.__tablename__, m.Event.session_id.in_(sessions)),
        (m.Thread.__tablename__, m.Thread.session_id.in_(sessions)),
        (m.EvalResult.__tablename__, m.EvalResult.run_id.in_(runs)),
        (m.PolicyTest.__tablename__, m.PolicyTest.owner_id.in_(ids)),
        (m.PolicyVersion.__tablename__, m.PolicyVersion.owner_id.in_(ids)),
    ]
    from evokai.compliance.store import PROFILE, SNAPSHOT

    keys = [f"{PROFILE}{i}" for i in ids]
    snaps = [m.ConsoleSetting.key.like(f"{SNAPSHOT}{i}:%") for i in ids]
    out.append((m.ConsoleSetting.__tablename__, sa.or_(m.ConsoleSetting.key.in_(keys), *snaps)))
    for table in reversed(m.Base.metadata.sorted_tables):
        if table.name in KEEP or table.name == m.Application.__tablename__:
            continue
        if "application_id" in table.c:
            out.append((table.name, table.c.application_id.in_(ids)))
    return out


def _table(name: str) -> sa.Table:
    return m.Base.metadata.tables[name]


async def plan(store: Store, module: str) -> Plan:
    """Count what a purge of a module would delete.

    Returns:
        The plan.

    Raises:
        PurgeRefused: The module is on.
        ValueError: An unknown module.
    """
    if module not in modules.ALL:
        raise ValueError(f"unknown module {module!r}: {', '.join(modules.MODULES)}")
    pinned = modules.pinned()
    if pinned is not None and module in pinned:
        raise PurgeRefused(f"The {module} module is on (EVOKAI_MODULES): turn it off first.")
    if module in await modules.enabled(store):
        raise PurgeRefused(f"The {module} module is on: turn it off in the console first.")
    async with store.db.session() as s:
        q = sa.select(m.Application.id, m.Application.slug).order_by(m.Application.slug)
        if module == modules.GATEWAY:
            q = gateways_only(q, even_off=True)
        else:
            q = applications_only(q, even_off=True)
        rows = (await s.execute(q)).all()
        ids = [r[0] for r in rows]
        out = Plan(module, ids, [r[1] for r in rows])
        if not ids:
            return out
        for name, where in _statements(ids):
            q = sa.select(sa.func.count()).select_from(_table(name)).where(where)
            if n := int((await s.execute(q)).scalar_one()):
                out.counts[name] = out.counts.get(name, 0) + n
        out.counts[m.Application.__tablename__] = len(ids)
        live = sa.select(sa.func.count()).where(
            m.ApiKey.application_id.in_(ids), m.ApiKey.revoked_at.is_(None)
        )
        out.keys = int((await s.execute(live)).scalar_one())
    return out


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    return value


async def export(store: Store, p: Plan, path: Path) -> int:
    """Write the rows a purge would delete to a JSON file (no key hashes or secrets).

    Returns:
        How many rows were written.
    """
    doc: dict[str, Any] = {"module": p.module, "exportedAt": utcnow().isoformat(), "tables": {}}
    written = 0
    async with store.db.session() as s:
        parts = [*_statements(p.ids), (m.Application.__tablename__, m.Application.id.in_(p.ids))]
        for name, where in parts if p.ids else []:
            table = _table(name)
            cols = [c for c in table.c if not any(x in c.name for x in SECRET_COLUMNS)]
            rows = (await s.execute(sa.select(*cols).where(where))).mappings().all()
            if rows:
                items = [{k: _plain(v) for k, v in r.items()} for r in rows]
                doc["tables"].setdefault(name, []).extend(items)
                written += len(rows)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return written


async def purge(store: Store, p: Plan, by: str) -> dict[str, int]:
    """Revoke the module's keys, delete its rows and write one audit row.

    The module must still be off: the plan is checked again first.

    Returns:
        Rows deleted per table.
    """
    again = await plan(store, p.module)
    if not again.ids:
        return {}
    ids = again.ids
    now = utcnow()
    async with store.db.session() as s:
        live = sa.update(m.ApiKey).where(
            m.ApiKey.application_id.in_(ids), m.ApiKey.revoked_at.is_(None)
        )
        await s.execute(live.values(revoked_at=now))
    deleted: dict[str, int] = {}
    async with store.db.session() as s:
        for name, where in _statements(ids):
            r = await s.execute(sa.delete(_table(name)).where(where))
            if n := int(getattr(r, "rowcount", 0) or 0):
                deleted[name] = deleted.get(name, 0) + n
        q = _of_module(p.module, sa.delete(m.Application).where(m.Application.id.in_(ids)))
        r = await s.execute(q)
        deleted[m.Application.__tablename__] = int(getattr(r, "rowcount", 0) or 0)
    await store.audit.add(
        application_id=None,
        actor=by[:200],
        actor_kind="cli",
        role=None,
        action="module.purge",
        target=p.module,
        before={"slugs": again.slugs[:200], "rows": deleted, "keysRevoked": again.keys},
        after=None,
        ip="",
        correlation_id="",
    )
    return deleted
