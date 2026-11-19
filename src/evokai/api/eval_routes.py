"""Evals of an application's agents: test cases, runs on demand, history and comparisons.

Every figure is a share or a count: a run's spend is a share of the application's eval budget,
token use of the version under test is a share of the baseline's. No amount leaves the service.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError, field_validator

from evokai.agents.prompts import PageContext
from evokai.api.deps import admin_app, services
from evokai.core.errors import BadRequest, Conflict, NotFound
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.evals.checks import parse_checks
from evokai.evals.runner import MAX_CASES, shares, subject_of
from evokai.identity.models import AppPrincipal
from evokai.services import Services
from evokai.sessions.limits import eval_cap_usd

router = APIRouter(prefix="/v1/admin", tags=["evals"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


class CaseBody(BaseModel):
    """A test case."""

    input: str = Field(min_length=1, max_length=8_000)
    page: PageContext | None = None
    """What the user would be looking at (path, title, visible text)."""
    checks: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    """contains, not_contains, regex, json_schema, tool_called, tool_not_called, rubric."""
    enabled: bool = True

    @field_validator("checks")
    @classmethod
    def _checks(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            parse_checks(v)
        except (ValidationError, ValueError) as e:
            raise ValueError(f"invalid checks: {str(e).splitlines()[0][:300]}") from e
        return v


def _case(c: m.EvalCase) -> dict[str, Any]:
    return {
        "name": c.name,
        "input": c.input,
        "page": c.page,
        "checks": c.checks or [],
        "enabled": c.enabled,
        "updatedAt": c.updated_at.isoformat(),
    }


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


async def _agent_exists(svc: Services, a: AppPrincipal, agent: str) -> m.AgentRecord:
    row = await svc.store.agents.active(a.application_id, agent)
    if row is None:
        raise NotFound("Unknown agent.", code="unknown_agent")
    return row


@router.get("/agents/{agent}/evals/cases")
async def list_cases(agent: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """An agent's test cases.

    Returns:
        items.
    """
    return {"items": [_case(c) for c in await svc.store.evals.cases(a.application_id, agent)]}


@router.put("/agents/{agent}/evals/cases/{name}")
async def put_case(agent: str, name: str, body: CaseBody, svc: Svc, a: Admin) -> dict[str, Any]:
    """Create or replace a test case.

    Returns:
        ok.

    Raises:
        BadRequest: A bad name, or too many cases.
    """
    await _agent_exists(svc, a, agent)
    if not name.strip() or len(name) > 120:
        raise BadRequest("A case name has 1 to 120 characters.", code="case_name")
    cases = await svc.store.evals.cases(a.application_id, agent)
    if name not in {c.name for c in cases} and len(cases) >= MAX_CASES * 2:
        raise BadRequest(f"An agent has at most {MAX_CASES * 2} cases.", code="too_many_cases")
    page = body.page
    if page is not None and page.text and not page.path:
        # The prompt shows a page by its path: a page given by its text alone is the root.
        page = page.model_copy(update={"path": "/"})
    await svc.store.evals.put_case(
        a.application_id,
        agent,
        name,
        input=body.input,
        page=page.model_dump(exclude_none=True) if page else None,
        checks=body.checks,
        enabled=body.enabled,
    )
    return {"ok": True}


@router.delete("/agents/{agent}/evals/cases/{name}")
async def delete_case(agent: str, name: str, svc: Svc, a: Admin) -> dict[str, bool]:
    """Remove a test case (its past results stay with their runs).

    Returns:
        ok.

    Raises:
        NotFound: Unknown case.
    """
    if not await svc.store.evals.delete_case(a.application_id, agent, name):
        raise NotFound()
    return {"ok": True}


class RunBody(BaseModel):
    """What to compare."""

    version: str | None = None
    """The version under test (default: the active one)."""
    baseline: str | None = None
    """The version to compare with (default: none)."""


@router.post("/agents/{agent}/evals/runs", status_code=202)
async def start_run(agent: str, body: RunBody, svc: Svc, a: Admin) -> dict[str, Any]:
    """Run the agent's enabled cases now, in the background (poll the run).

    Returns:
        runId.

    Raises:
        BadRequest: No enabled case, or an unknown version.
        Conflict: A run of this agent is going.
    """
    app = await _app(svc, a)
    active = await _agent_exists(svc, a, agent)
    if not any(c.enabled for c in await svc.store.evals.cases(app.id, agent)):
        raise BadRequest("Add an enabled test case first.", code="no_cases")
    if await svc.store.evals.running(app.id, agent):
        raise Conflict("A run of this agent is going: wait for it to end.", code="eval_running")
    version = body.version or active.version
    for v in (version, body.baseline):
        if v and await svc.store.agents.version(app.id, agent, v) is None:
            raise BadRequest(f"Unknown version {v}.", code="unknown_version")
    run = await svc.evals.start(
        app, agent, version, body.baseline, trigger="manual", created_by=a.key_id
    )
    return {"runId": run.id}


def _run_view(run: m.EvalRun, app: m.Application) -> dict[str, Any]:
    versions = (run.summary or {}).get("versions") or {}
    new = versions.get(run.version) or {}
    base = versions.get(run.baseline or "") or {}
    out: dict[str, Any] = {
        "id": run.id,
        "agent": run.agent_name,
        "version": run.version,
        "baseline": run.baseline,
        "trigger": run.trigger,
        "status": run.status,
        "startedAt": run.started_at.isoformat(),
        "endedAt": run.ended_at.isoformat() if run.ended_at else None,
        "cases": (run.summary or {}).get("cases"),
        "summary": {v: shares(s, app) for v, s in versions.items()},
    }
    if new and base:
        tokens = new.get("inputTokens", 0) + new.get("outputTokens", 0)
        before = base.get("inputTokens", 0) + base.get("outputTokens", 0)
        out["comparison"] = {
            "passRateDelta": None
            if new.get("passRate") is None or base.get("passRate") is None
            else round(new["passRate"] - base["passRate"], 1),
            "latencyDeltaMs": None
            if new.get("latencyMs") is None or base.get("latencyMs") is None
            else new["latencyMs"] - base["latencyMs"],
            "tokensVsBaselinePct": round(tokens / before * 100, 1) if before else None,
        }
    return out


@router.get("/agents/{agent}/evals/runs")
async def list_runs(agent: str, svc: Svc, a: Admin, limit: int = 30) -> dict[str, Any]:
    """An agent's runs, newest first, with their figures as shares and counts.

    Returns:
        items, and the eval budget used this month (share).
    """
    app = await _app(svc, a)
    runs = await svc.store.evals.runs(app.id, agent, max(1, min(limit, 100)))
    spent = await svc.store.usage.eval_spend(app.id, utcnow())
    cap = eval_cap_usd(app)
    return {
        "items": [_run_view(r, app) for r in runs],
        "budget": {
            "usedPct": 100.0 if cap <= 0 else round(spent / cap * 100, 2),
            "judge": bool((app.defaults or {}).get("eval_judge_model")),
            "onSave": (app.defaults or {}).get("eval_on_save", True),
            "subject": subject_of(agent),
        },
    }


@router.get("/evals/runs/{run_id}")
async def get_run(run_id: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """A run with every case on both versions: checks, answers, tools, latency and tokens.

    Returns:
        The run, and ``cases``: name, ``results`` per version, and whether the outcome changed.

    Raises:
        NotFound: Unknown run.
    """
    app = await _app(svc, a)
    run = await svc.store.evals.run(app.id, run_id)
    if run is None:
        raise NotFound()
    by_case: dict[str, dict[str, Any]] = {}
    for r in await svc.store.evals.results(run.id):
        entry = by_case.setdefault(r.case_name, {"name": r.case_name, "results": {}})
        entry["results"][r.version] = {
            "passed": r.passed,
            "checks": r.checks,
            "answer": r.answer,
            "tools": r.tools,
            "reason": r.reason,
            "latencyMs": r.latency_ms,
            "inputTokens": r.input_tokens,
            "outputTokens": r.output_tokens,
            "error": r.error,
        }
    cases = list(by_case.values())
    for c in cases:
        new = c["results"].get(run.version)
        base = c["results"].get(run.baseline or "")
        c["changed"] = bool(new and base and new["passed"] != base["passed"])
        c["sameAnswer"] = bool(new and base and new["answer"].strip() == base["answer"].strip())
    return {**_run_view(run, app), "cases": cases}
