"""The security layer of an application: its policy, the effective controls and the events."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.core.errors import NotFound
from evokai.db import models as m
from evokai.guard import SecurityPolicy, catalogue
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(prefix="/v1/admin/security", tags=["security"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


async def security_view(svc: Services, app: m.Application) -> dict[str, Any]:
    """The application's policy as the console shows it.

    Returns:
        enabled, policy (as set), effective (modes and settings), controls (the catalogue), agents
        (each agent's own policy and effective modes), eventTtlDays, defaultEventTtlDays,
        classifiers (each injection classifier and whether this deployment can use it) and
        defaultClassifier (what ``default`` means here, or None).
    """
    agents = []
    for row in await svc.store.agents.active_all(app.id):
        own = row.definition.get("security")
        agents.append({
            "name": row.name,
            "security": own,
            "modes": svc.guard.policy(app, _Agent(own)).modes,
        })
    return {
        "enabled": svc.guard.enabled,
        "policy": app.security or {},
        "effective": svc.guard.policy(app).view(),
        "controls": catalogue(),
        "agents": agents,
        "eventTtlDays": app.security_event_ttl_days,
        "defaultEventTtlDays": svc.settings.security_event_ttl_days,
        "classifiers": svc.guard.classifiers.view(),
        "defaultClassifier": svc.guard.classifiers.default_id(),
        "moderationClassifiers": svc.guard.classifiers.moderation_view(),
    }


class _Agent:
    """Just the security policy of an agent definition (for resolving its modes)."""

    def __init__(self, security: dict[str, Any] | None) -> None:
        """Hold the policy."""
        self.security = security
        self.name = ""
        self.instructions = ""


@router.get("")
async def get_security(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's security policy, what applies, and each agent's own policy.

    Returns:
        The security view.
    """
    return await security_view(svc, await _app(svc, a))


@router.put("")
async def put_security(
    body: SecurityPolicy, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Replace the application's security policy (unset fields follow the defaults).

    Agents may override it in their own definition (``security``). Effective from the next turn.

    Returns:
        The security view.
    """
    app = await _app(svc, a)
    await svc.store.applications.update(app.id, security=body.stored())
    await audit.record(request, a, "security.policy", before=app.security, after=body.stored())
    return await security_view(svc, await _app(svc, a))


def _event(e: m.SecurityEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "at": e.created_at.isoformat(),
        "control": e.control,
        "rule": e.rule,
        "action": e.action,
        "mode": e.mode,
        "severity": e.severity,
        "direction": e.direction,
        "agent": e.agent_name,
        "subject": e.subject,
        "conversationId": e.session_id,
        "turnId": e.turn_id,
        "tool": e.tool,
        "snippet": e.snippet,
        "details": e.details or {},
    }


Start = Annotated[datetime | None, Query(alias="from")]
End = Annotated[datetime | None, Query(alias="to")]


@router.get("/events")
async def events(
    svc: Svc,
    a: Admin,
    start: Start = None,
    end: End = None,
    control: str | None = None,
    rule: str | None = None,
    action: str | None = None,
    severity: str | None = None,
    agent: str | None = None,
    subject: str | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """Security events, newest first, with filters (``control`` ending in ``.`` is a prefix).

    Snippets are masked and clipped when recorded: no secret or raw personal data is ever stored.

    Returns:
        items, and ``next`` (the ``before`` of the next page) when there may be more.
    """
    rows = await svc.store.security_events.query(
        a.application_id,
        limit=limit,
        before=before,
        start=start,
        end=end,
        control=control,
        rule=rule,
        action=action,
        severity=severity,
        agent=agent,
        subject=subject,
    )
    nxt = rows[-1].created_at.isoformat() if len(rows) == limit else None
    return {"items": [_event(e) for e in rows], "next": nxt}


@router.get("/summary")
async def summary(
    svc: Svc,
    a: Admin,
    start: Start = None,
    end: End = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Counts of a period (default: everything kept) by rule, control, action and severity.

    Returns:
        total, byRule, byControl, byAction, bySeverity.
    """
    return await svc.store.security_events.summary(
        a.application_id, start=start, end=end, agent=agent
    )
