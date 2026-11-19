"""Red team of an application's agents: runs, the score trend, failures, the publish gate.

Every figure is a score, a count or a share: a run's spend is a share of the eval budget. Answers
are stored masked (identifiers and secrets), as ``pii.history`` masks them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.core.errors import BadRequest, Conflict, Forbidden, NotFound
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.evals.redteam.attacks import CATEGORIES, LANGUAGES, SUITE, by_id
from evokai.evals.redteam.graders import mask
from evokai.evals.redteam.service import gate_of, outcome_of, subject_of
from evokai.identity.models import AppPrincipal
from evokai.services import Services
from evokai.sessions.limits import eval_cap_usd

router = APIRouter(prefix="/v1/admin", tags=["redteam"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


@router.get("/redteam/suite")
async def suite(_a: Admin) -> dict[str, Any]:
    """The built-in attacks: categories, languages and each attack with its graders.

    Returns:
        categories, languages, items.
    """
    return {
        "categories": list(CATEGORIES),
        "languages": list(LANGUAGES),
        "items": [_masked(a.public()) for a in SUITE],
    }


class RedTeamBody(BaseModel):
    """What to attack."""

    version: str | None = None
    """The version under test (default: the active one)."""
    categories: list[str] | None = Field(default=None, max_length=len(CATEGORIES))
    languages: list[str] | None = Field(default=None, max_length=len(LANGUAGES))


@router.post("/agents/{agent}/redteam/runs", status_code=202)
async def start(agent: str, body: RedTeamBody, svc: Svc, a: Admin) -> dict[str, Any]:
    """Run the attack suite on a version now, in the background (poll the run).

    Returns:
        runId.

    Raises:
        NotFound: Unknown agent.
        BadRequest: An unknown version, category or language.
        Conflict: A red team run of this agent is going.
    """
    app = await _app(svc, a)
    active = await svc.store.agents.active(app.id, agent)
    versions = await svc.store.agents.versions(app.id, agent)
    if active is None and not versions:
        raise NotFound("Unknown agent.", code="unknown_agent")
    version = body.version or (active.version if active else versions[0].version)
    if not any(v.version == version for v in versions):
        raise BadRequest(f"Unknown version {version}.", code="unknown_version")
    bad = [c for c in body.categories or [] if c not in CATEGORIES]
    bad += [x for x in body.languages or [] if x not in LANGUAGES]
    if bad:
        raise BadRequest(f"Unknown: {', '.join(bad)}.", code="unknown_filter")
    if await svc.store.evals.running(app.id, agent, "redteam"):
        raise Conflict("A red team run of this agent is going.", code="redteam_running")
    previous = _previous(versions, version)
    run = await svc.redteam.start(
        app,
        agent,
        version,
        trigger="manual",
        created_by=a.key_id,
        baseline=previous,
        categories=body.categories,
        languages=body.languages,
    )
    return {"runId": run.id}


def _masked(public: dict[str, Any]) -> dict[str, Any]:
    """An attack as the console shows it: its planted identifiers and secrets masked too.

    Returns:
        The attack.
    """
    for key in ("text", "injected"):
        if public.get(key):
            public[key] = mask(public[key])
    return public


def _previous(versions: list[m.AgentRecord], version: str) -> str | None:
    """The version saved before this one.

    Returns:
        Its version, or None.
    """
    names = [v.version for v in versions]  # newest first
    i = names.index(version) if version in names else -1
    return names[i + 1] if 0 <= i < len(names) - 1 else None


def _summary(run: m.EvalRun) -> dict[str, Any]:
    s = ((run.summary or {}).get("versions") or {}).get(run.version) or {}
    return {k: v for k, v in s.items() if k != "costMicroUsd"}


def _view(run: m.EvalRun, app: m.Application) -> dict[str, Any]:
    s = ((run.summary or {}).get("versions") or {}).get(run.version) or {}
    cap = eval_cap_usd(app)
    spent = (s.get("costMicroUsd") or 0) / 1e6
    gate, minimum = gate_of(app)
    value = s.get("score")
    return {
        "id": run.id,
        "agent": run.agent_name,
        "version": run.version,
        "previous": run.baseline,
        "trigger": run.trigger,
        "status": run.status,
        "startedAt": run.started_at.isoformat(),
        "endedAt": run.ended_at.isoformat() if run.ended_at else None,
        "attacks": (run.summary or {}).get("attacks"),
        "summary": _summary(run),
        "score": value,
        "budgetPct": 100.0 if cap <= 0 else round(spent / cap * 100, 2),
        "belowGate": gate != "off" and value is not None and value < minimum,
    }


@router.get("/agents/{agent}/redteam/runs")
async def runs(agent: str, svc: Svc, a: Admin, limit: int = 30) -> dict[str, Any]:
    """An agent's red team runs, newest first, the score per version and the gate.

    Returns:
        items, trend (oldest first: version, score, at), versions (publish state and latest
        score of each), gate (mode, minScore, onSave), budget (share used this month).
    """
    app = await _app(svc, a)
    items = await svc.store.evals.runs(app.id, agent, max(1, min(limit, 100)), "redteam")
    views = [_view(r, app) for r in items]
    latest: dict[str, dict[str, Any]] = {}
    for v in views:
        if v["status"] == "done" and v["version"] not in latest:
            latest[v["version"]] = v
    gate, minimum = gate_of(app)
    spent = await svc.store.usage.eval_spend(app.id, utcnow())
    cap = eval_cap_usd(app)
    versions = await svc.store.agents.versions(app.id, agent)
    return {
        "items": views,
        "trend": [
            {"version": v["version"], "score": v["score"], "at": v["startedAt"]}
            for v in reversed(views)
            if v["status"] == "done" and v["score"] is not None
        ],
        "versions": [
            {
                "version": r.version,
                "active": r.active,
                "publishState": r.publish_state,
                "createdAt": r.created_at.isoformat(),
                "score": (latest.get(r.version) or {}).get("score"),
            }
            for r in versions[:20]
        ],
        "gate": {
            "mode": gate,
            "minScore": minimum,
            "onSave": (app.defaults or {}).get("redteam_on_save", True),
            "judge": svc.redteam.judge_model(app) is not None,
        },
        "budget": {
            "usedPct": 100.0 if cap <= 0 else round(spent / cap * 100, 2),
            "subject": subject_of(agent),
        },
    }


@router.get("/redteam/runs/{run_id}")
async def get_run(run_id: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """A red team run: every attack with its outcome, graders and masked answer.

    It is compared with the latest finished run of the previous version.

    Returns:
        The run, ``results`` (each attack: id, category, language, severity, text, injected,
        outcome, checks, answer, tools, reason, error) and ``comparison`` (previous version, its
        score, the score delta, attacks newly failing and attacks fixed).

    Raises:
        NotFound: Unknown run.
    """
    app = await _app(svc, a)
    run = await svc.store.evals.run(app.id, run_id)
    if run is None or run.suite != "redteam":
        raise NotFound()
    catalog = by_id()
    results = await svc.store.evals.results(run.id)
    attacks = []
    outcomes: dict[str, str] = {}
    for r in results:
        attack = catalog.get(r.case_id)
        row = {"checks": r.checks, "error": r.error}
        outcome = outcome_of(row)
        outcomes[r.case_id] = outcome
        attacks.append({
            **(_masked(attack.public()) if attack else {"id": r.case_id}),
            "outcome": outcome,
            "checks": r.checks,
            "answer": r.answer,
            "tools": r.tools,
            "reason": r.reason,
            "error": r.error,
            "latencyMs": r.latency_ms,
        })
    order = {a.id: i for i, a in enumerate(SUITE)}
    attacks.sort(key=lambda x: (x["outcome"] != "failed", order.get(x["id"], 999)))
    out = {**_view(run, app), "results": attacks, "comparison": None}
    if run.baseline:
        before = next(
            (
                r
                for r in await svc.store.evals.runs(app.id, run.agent_name, 100, "redteam")
                if r.version == run.baseline and r.status == "done"
            ),
            None,
        )
        if before is not None:
            old = {
                r.case_id: outcome_of({"checks": r.checks, "error": r.error})
                for r in await svc.store.evals.results(before.id)
            }
            prev_score = _summary(before).get("score")
            score = out["score"]
            out["comparison"] = {
                "version": before.version,
                "runId": before.id,
                "score": prev_score,
                "scoreDelta": None
                if score is None or prev_score is None
                else round(score - prev_score, 1),
                "newlyFailing": sorted(
                    k for k, v in outcomes.items() if v == "failed" and old.get(k) == "passed"
                ),
                "fixed": sorted(
                    k for k, v in outcomes.items() if v == "passed" and old.get(k) == "failed"
                ),
            }
    return out


class PublishBody(BaseModel):
    """Publish a version the red team gate holds."""

    force: bool = False
    """Publish a version whose score is below the gate (owner only, audited)."""


@router.post("/agents/{agent}/versions/{version}/publish")
async def publish(
    agent: str, version: str, body: PublishBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Make a saved version the active one (a version the gate holds, or an older one).

    Returns:
        version, published.

    Raises:
        NotFound: Unknown version.
        Forbidden: Not an owner.
        Conflict: The gate holds the version and ``force`` was not given.
    """
    app = await _app(svc, a)
    if a.role != "owner":
        raise Forbidden("Publishing a version needs the owner role.", code="role")
    record = await svc.store.agents.version(app.id, agent, version)
    if record is None:
        raise NotFound("Unknown version.", code="unknown_version")
    gate, _ = gate_of(app)
    if gate == "enforce" and record.publish_state and not body.force:
        raise Conflict(
            "The red team gate holds this version: publish it with force to override.",
            code="redteam_gate",
        )
    current = await svc.store.agents.active(app.id, agent)
    await svc.store.agents.publish(app.id, agent, version)
    svc.agents.invalidate(app.id, agent)
    await audit.record(
        request,
        a,
        "agent.publish",
        target=agent,
        before={"version": current.version if current else None},
        after={"version": version, "force": body.force, "publishState": record.publish_state},
    )
    return {"version": version, "published": True}
