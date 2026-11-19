"""Generating facts and taking snapshots for an application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from evokai.compliance.facts import collect
from evokai.compliance.profile import load
from evokai.compliance.store import ComplianceRepository, new_snapshot_id

if TYPE_CHECKING:
    from evokai.db import models as m
    from evokai.services import Services


async def current_facts(svc: Services, app: m.Application) -> dict[str, Any]:
    """The facts of an application as of now, with its stored profile.

    Returns:
        The facts.
    """
    repo = ComplianceRepository(svc.db)
    profile = load(await repo.profile(app.id)).model_dump()
    return await collect(svc, app, profile)


async def take_snapshot(
    svc: Services, app: m.Application, *, label: str = "", by_kind: str = ""
) -> dict[str, Any]:
    """Store what is true now, so a reviewer can see it later.

    Returns:
        The snapshot (with its facts).
    """
    facts = await current_facts(svc, app)
    from evokai.compliance.classify import classify

    snapshot = {
        "id": new_snapshot_id(),
        "createdAt": facts["collectedAt"],
        "label": label,
        "byKind": by_kind,
        "classification": classify(facts).level,
        "agents": len(facts["agents"]),
        "generator": "evokai-compliance/1",
        "facts": facts,
    }
    await ComplianceRepository(svc.db).add_snapshot(app.id, snapshot, by_kind)
    return snapshot
