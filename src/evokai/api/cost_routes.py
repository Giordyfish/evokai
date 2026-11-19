"""Cost governance of an application: forecasts, alerts, model recommendations, cache hints.

Shares only: usage as a share of the caps, a model's cost as a percentage of the current model's.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai.agents.definitions import AgentDef
from evokai.api import admin_routes
from evokai.api.deps import admin_app, services
from evokai.core.errors import BadRequest, Conflict, NotFound
from evokai.costs.recommend import DEFAULT_TOLERANCE
from evokai.db import models as m
from evokai.identity.models import AppPrincipal
from evokai.llm.catalog import bare, normalize
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["costs"])
WINDOW = re.compile(r"\d{4}-\d{2}(-\d{2}[mwd])?")
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


@router.get("/costs/forecast")
async def forecast(svc: Svc, a: Admin) -> dict[str, Any]:
    """The forecast of every cap at its window's end, its people's, and the current alerts.

    Checking the forecast also fires the alerts it crosses for the first time in a window.

    Returns:
        period, application (the monthly cap: usedPct, forecastPct, lowPct, highPct, trend,
        daysLeft, resetsAt, daily), caps (every application cap), subjects, thresholds, alerts,
        webhook (whether one is set and signed).
    """
    app = await _app(svc, a)
    view = await svc.costs.forecast(app)
    await svc.costs.evaluate(app, view)
    d = app.defaults or {}
    return {
        **view,
        "alerts": await svc.costs.alerts(app),
        "webhook": {
            "set": bool(d.get("cost_alert_webhook")),
            "signed": bool(d.get("cost_alert_secret")),
        },
    }


@router.get("/costs/alerts")
async def alerts(
    svc: Svc, a: Admin, period: str | None = None, open: bool = False
) -> dict[str, Any]:
    """The application's cost alerts of a cap's window (the current windows by default).

    A window is ``YYYY-MM`` for a month from the 1st, else its first day and period letter
    (``2026-11-14w``, ``2026-11-15m``, ``2026-11-16d``).

    Returns:
        items, newest first.

    Raises:
        BadRequest: A period that is not a window.
    """
    if period is not None and not WINDOW.fullmatch(period):
        raise BadRequest("A period is YYYY-MM or YYYY-MM-DD followed by m, w or d.", code="period")
    app = await _app(svc, a)
    return {"items": await svc.costs.alerts(app, period, open_only=open)}


@router.post("/costs/alerts/{alert_id}/ack")
async def acknowledge(alert_id: str, svc: Svc, a: Admin) -> dict[str, bool]:
    """Mark an alert as seen: the console's banner goes.

    Returns:
        ok.

    Raises:
        NotFound: Unknown alert.
    """
    app = await _app(svc, a)
    if not await svc.costs.acknowledge(app, alert_id, a.actor or a.key_id):
        raise NotFound()
    return {"ok": True}


@router.get("/costs/cache-suggestions")
async def cache_suggestions(svc: Svc, a: Admin) -> dict[str, Any]:
    """Agents whose prompt is long enough to cache while their prompt cache is off.

    Returns:
        items (agent, model, stableTokens, minTokens, inputSharePct, suggested).
    """
    return {"items": await svc.costs.cache_suggestions(await _app(svc, a))}


class CompareBody(BaseModel):
    """How much pass rate a cheaper model may lose and still pass."""

    tolerance: float = Field(default=DEFAULT_TOLERANCE, ge=0, le=50)


@router.post("/agents/{agent}/model-recommendations", status_code=202)
async def start_comparison(agent: str, body: CompareBody, svc: Svc, a: Admin) -> dict[str, Any]:
    """Run the agent's eval cases on its model and on the cheaper allowed models.

    Returns:
        runId, current, candidates.

    Raises:
        NotFound: Unknown agent.
        BadRequest: No enabled case, or no cheaper allowed model.
        Conflict: A comparison of this agent is going.
    """
    app = await _app(svc, a)
    record = await svc.store.agents.active(app.id, agent)
    if record is None:
        raise NotFound("Unknown agent.", code="unknown_agent")
    if not any(c.enabled for c in await svc.store.evals.cases(app.id, agent)):
        raise BadRequest("Add an enabled test case first.", code="no_cases")
    definition = AgentDef.model_validate({**record.definition, "version": record.version})
    if not await svc.costs.candidates(app, definition):
        raise BadRequest(
            "No allowed model is cheaper than the agent's current one.", code="no_cheaper_model"
        )
    if await svc.store.evals.running(app.id, agent, "models"):
        raise Conflict("A comparison of this agent is going.", code="comparison_running")
    run = await svc.costs.start_comparison(
        app, agent, created_by=a.key_id, tolerance=body.tolerance
    )
    s = run.summary or {}
    return {"runId": run.id, "current": s.get("current"), "candidates": s.get("candidates")}


@router.get("/agents/{agent}/model-recommendations")
async def comparisons(agent: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """The agent's latest model comparison and the models it could try now.

    Returns:
        latest (the comparison: models with passRate, passRateDelta, relativeCostPct, latencyMs,
        passes, recommended; or None), candidates (cheaper models allowed now), history.
    """
    app = await _app(svc, a)
    runs = await svc.store.evals.runs(app.id, agent, 10, "models")
    record = await svc.store.agents.active(app.id, agent)
    candidates: list[str] = []
    current = None
    if record is not None:
        definition = AgentDef.model_validate({**record.definition, "version": record.version})
        candidates = await svc.costs.candidates(app, definition)
        current = normalize(definition.model)
    return {
        "latest": svc.costs.comparison_view(runs[0]) if runs else None,
        "current": current,
        "candidates": candidates,
        "history": [
            {"id": r.id, "status": r.status, "startedAt": r.started_at.isoformat()} for r in runs
        ],
    }


class SwitchBody(BaseModel):
    """The model the agent should answer with."""

    model: str = Field(min_length=3, max_length=200)


@router.post("/agents/{agent}/switch-model")
async def switch_model(
    agent: str, body: SwitchBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Save a new version of the agent with another primary model.

    It is saved like any version: allow-lists, residency, grants, evals, red team and its gate.

    Returns:
        What saving the agent returns (version, created, published...).

    Raises:
        NotFound: Unknown agent.
    """
    record = await svc.store.agents.active(a.application_id, agent)
    if record is None:
        raise NotFound("Unknown agent.", code="unknown_agent")
    current = AgentDef.model_validate(record.definition)
    model = normalize(body.model)
    data = current.model_dump()
    data["model"] = model
    data["fallbacks"] = [f for f in current.fallbacks if bare(f) != bare(model)]
    data["models"] = [x for x in current.models if bare(x) != bare(model)]
    data.pop("version", None)
    return await admin_routes.put_agent(agent, AgentDef.model_validate(data), svc, a, request)
