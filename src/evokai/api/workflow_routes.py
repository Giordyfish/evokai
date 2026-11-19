"""Workflows: definitions, runs, approvals and the inbound webhook.

- ``/v1/admin/workflows``: the console and admin key (definitions with versions, a validation that
  points at the line of each problem, runs with their steps, retry, cancel, a manual start, the
  approvals queue).
- ``/v1/workflows``: the application's backend with its server key (start a run, read it, cancel
  it, list approvals and decide them for its own users, whom it vouches for with their groups).
- ``/v1/workflow-hooks/{application}/{workflow}``: an outside system starts a run with a body
  signed with the webhook's secret (``X-Evokai-Timestamp``, ``X-Evokai-Signature``).

People never see amounts: a run's usage is a share of the application's monthly cap (``capPct``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, server_app, services
from evokai.core.errors import BadRequest, NotFound
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.identity.models import AppPrincipal
from evokai.observability.tracing import CONTENT_CHARS, content_mode
from evokai.privacy import subject_key
from evokai.services import Services
from evokai.sessions.limits import month_cap_usd
from evokai.workflows import definitions as wd
from evokai.workflows.cron import Cron, CronError
from evokai.workflows.engine import mask_value

admin = APIRouter(prefix="/v1/admin/workflows", tags=["workflows"])
server = APIRouter(prefix="/v1/workflows", tags=["workflows"])
hooks = APIRouter(prefix="/v1/workflow-hooks", tags=["workflows"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
App = Annotated[AppPrincipal, Depends(server_app)]

RunStatus = Literal["running", "waiting", "done", "failed", "rejected", "cancelled", "active"]


class ValidateBody(BaseModel):
    """A definition to check, as text (what the console's editor holds) or as an object."""

    text: str | None = Field(default=None, max_length=200_000)
    format: Literal["yaml", "json"] = "yaml"
    definition: dict[str, Any] | None = None


class StartBody(BaseModel):
    """A run to start."""

    input: dict[str, Any] = Field(default_factory=dict)
    subject: str | None = Field(default=None, max_length=200)
    """Whose data the run handles (erased with them), when it has one."""
    tags: list[str] = Field(default_factory=list, max_length=20)
    idempotencyKey: str | None = Field(default=None, max_length=150)
    """The same key starts at most one run (the second call returns the first run)."""


class DecisionBody(BaseModel):
    """A console decision."""

    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=2_000)


class Decider(BaseModel):
    """The application's user who decided, as the application vouches for them."""

    name: str = Field(min_length=1, max_length=200)
    """How they appear in the record (a name or the application's own id)."""
    groups: list[Annotated[str, Field(max_length=100)]] = Field(default_factory=list, max_length=50)
    """Their groups or roles: approval steps that name groups need one of them."""


class ServerDecisionBody(DecisionBody):
    """A decision taken in the application's own interface."""

    by: Decider


class InvalidWorkflow(BadRequest):
    """A definition with problems (each one with its path)."""

    def __init__(self, problems: list[str]) -> None:
        """Create the error.

        Args:
            problems: ``path: message`` each.
        """
        super().__init__("The workflow definition has problems.", code="invalid_workflow")
        self.extra = {"problems": problems}


# views


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    from datetime import UTC

    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _cap_pct(app: m.Application, micro: int) -> float:
    cap = float(month_cap_usd(app) or 0)
    return round(micro / (cap * 1e6) * 100, 3) if cap > 0 else 0.0


def _content(svc: Services, app: m.Application, value: Any, *, console: bool) -> Any:
    """A run's input or output as the caller may see it.

    A console login follows the application's ``tracing_content`` (``none`` hides it, ``masked``
    masks personal data); the application's own key sees what is stored (masked when the
    application enforces ``pii.history``).

    Returns:
        The value, masked or None.
    """
    if value is None or not console:
        return value
    policy = svc.guard.policy(app)
    mode = content_mode(
        app,
        storage="evokai",
        pii_masked=policy.enforced("pii.history") or policy.enforced("pii.model"),
    )
    if mode == "none":
        return None
    out = mask_value(value) if mode == "masked" else value
    if isinstance(out, str) and len(out) > CONTENT_CHARS * 3:
        return f"{out[: CONTENT_CHARS * 3]}…"
    return out


def _who(svc: Services, app: m.Application, subject: str | None, console: bool) -> dict[str, Any]:
    if not subject:
        return {}
    if console:
        return {"subjectKey": subject_key(svc.keyring.signing_key, app.id, subject)[:16]}
    return {"subject": subject}


def run_view(
    svc: Services, app: m.Application, r: m.WorkflowRun, *, console: bool
) -> dict[str, Any]:
    """A run's summary.

    Returns:
        The view.
    """
    end = r.finished_at or utcnow()
    start = r.started_at
    if start.tzinfo is None and end.tzinfo is not None:
        end = end.replace(tzinfo=None)
    return {
        "id": r.id,
        "workflow": r.workflow_name,
        "version": r.workflow_version,
        "status": r.status,
        "reason": r.reason,
        "error": r.error,
        "trigger": r.trigger,
        "startedAt": _iso(r.started_at),
        "finishedAt": _iso(r.finished_at),
        "durationMs": max(0, round((end - start).total_seconds() * 1000)),
        "capPct": _cap_pct(app, r.cost_micro_usd or 0),
        "masked": r.masked,
        **_who(svc, app, r.subject, console),
    }


def approval_view(
    svc: Services, app: m.Application, a: m.WorkflowApproval, *, console: bool
) -> dict[str, Any]:
    """An approval as the queue shows it.

    Returns:
        The view.
    """
    return {
        "id": a.id,
        "runId": a.run_id,
        "workflow": a.workflow_name,
        "step": a.step_id,
        "status": a.status,
        "question": a.question,
        "summary": a.summary or [],
        "details": a.details or {},
        "approvers": a.approvers or {},
        "onTimeout": a.on_timeout,
        "expiresAt": _iso(a.expires_at),
        "createdAt": _iso(a.created_at),
        "decidedBy": a.decided_by,
        "decidedVia": a.decided_via,
        "comment": a.comment,
        "decidedAt": _iso(a.decided_at),
        **_who(svc, app, a.subject, console),
    }


async def run_detail(
    svc: Services, app: m.Application, r: m.WorkflowRun, *, console: bool
) -> dict[str, Any]:
    """A run with its steps, approvals and the steps of its definition.

    Returns:
        The view.
    """
    steps = await svc.store.workflows.steps(r.id)
    approvals = await svc.store.workflows.approvals(app.id, status=None, run_id=r.id)
    rec = await svc.store.workflows.version(app.id, r.workflow_name, r.workflow_version)
    defn = svc.workflows.definition(rec) if rec is not None else None
    order = {s.id: i for i, s in enumerate(defn.all_steps())} if defn else {}

    def step_view(s: m.WorkflowStep) -> dict[str, Any]:
        meta = dict(s.meta or {})
        return {
            "id": s.step_id,
            "kind": s.kind,
            "branch": s.path or None,
            "status": s.status,
            "attempt": s.attempt,
            "dueAt": _iso(s.due_at) if s.status in {"pending", "waiting"} else None,
            "startedAt": _iso(s.started_at),
            "finishedAt": _iso(s.finished_at),
            "error": s.error,
            "input": _content(svc, app, s.input, console=console),
            "output": _content(svc, app, s.output, console=console),
            "meta": {k: v for k, v in meta.items() if k not in {"inputTokens", "outputTokens"}},
            "capPct": _cap_pct(app, s.cost_micro_usd or 0),
        }

    visible = sorted(
        (s for s in steps if s.kind != "join"),
        key=lambda s: (order.get(s.step_id, 999), s.created_at),
    )
    return {
        **run_view(svc, app, r, console=console),
        "input": _content(svc, app, r.input, console=console),
        "output": _content(svc, app, r.output, console=console),
        "context": _content(svc, app, r.context, console=console),
        "steps": [step_view(s) for s in visible],
        "approvals": [approval_view(svc, app, a, console=console) for a in approvals],
        "outline": wd.outline(defn),
        "traceId": r.id,
    }


def _triggers(defn: wd.WorkflowDef, tz: Any, now: datetime) -> list[dict[str, Any]]:
    out = []
    for t in defn.triggers:
        item: dict[str, Any] = {"kind": t.kind}
        if t.kind == "schedule":
            item["cron"] = t.cron
            try:
                nxt = Cron.parse(t.cron or "").next(now, tz)
                item["nextAt"] = nxt.isoformat() if nxt else None
            except CronError:
                item["nextAt"] = None
        elif t.kind == "event":
            item.update(event=t.event, agent=t.agent, status=t.status)
        elif t.kind == "webhook":
            item["secretEnv"] = t.secret_env
        out.append(item)
    return out


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


async def _references(svc: Services, app: m.Application, defn: wd.WorkflowDef) -> list[wd.Problem]:
    """Agents and MCP servers a definition names that the application lacks.

    Returns:
        The problems.
    """
    agents = {r.name: r for r in await svc.store.agents.active_all(app.id)}
    servers = {s.name for s in await svc.store.mcp_servers.all(app.id)}
    out = []
    for s in defn.all_steps():
        path = wd._step_path(defn, s.id)
        if isinstance(s, wd.AgentStep):
            a = agents.get(s.agent)
            if a is None:
                out.append(wd.Problem(path=f"{path}.agent", message=f"unknown agent {s.agent}"))
            elif "server" not in (a.definition.get("channels") or ["browser"]):
                out.append(
                    wd.Problem(
                        path=f"{path}.agent",
                        message=f"agent {s.agent} does not allow server runs (channels)",
                    )
                )
        elif isinstance(s, wd.ToolStep) and s.server not in servers:
            out.append(wd.Problem(path=f"{path}.server", message=f"unknown MCP server {s.server}"))
    for i, t in enumerate(defn.triggers):
        if t.kind == "event" and t.agent and t.agent not in agents:
            out.append(wd.Problem(path=f"triggers[{i}].agent", message=f"unknown agent {t.agent}"))
    return out


def summary_view(
    svc: Services,
    app: m.Application,
    rec: m.WorkflowRecord,
    counts: dict[str, int],
    last: datetime | None,
) -> dict[str, Any]:
    """A workflow in the list.

    Returns:
        The view.
    """
    defn = svc.workflows.definition(rec)
    tz = svc.workflows.timezone(app, defn)
    triggers = _triggers(defn, tz, utcnow())
    nexts = [t["nextAt"] for t in triggers if t.get("nextAt")]
    return {
        "name": rec.name,
        "title": defn.title,
        "description": defn.description,
        "version": rec.version,
        "steps": len(defn.all_steps()),
        "kinds": sorted({s.kind for s in defn.all_steps()}),
        "triggers": triggers,
        "nextRunAt": min(nexts) if nexts else None,
        "lastRunAt": _iso(last),
        "runs": counts,
        "timezone": str(tz.key),
        "retentionDays": defn.retention_days,
        "createdAt": _iso(rec.created_at),
        "createdBy": rec.created_by,
    }


# admin


@admin.get("")
async def list_workflows(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's workflows, with their runs of the last 30 days per status.

    Returns:
        ``items``, ``pendingApprovals``, ``notifyAllowed`` (whether notify steps can run here).
    """
    app = await _app(svc, a)
    repo = svc.store.workflows
    counts = await repo.counts(app.id, utcnow() - timedelta(days=30))
    last = await repo.last_runs(app.id)
    return {
        "items": [
            summary_view(svc, app, r, counts.get(r.name, {}), last.get(r.name))
            for r in await repo.active_all(app.id)
        ],
        "pendingApprovals": await repo.pending_count(app.id),
        "notifyAllowed": bool(svc.settings.workflow_notify_allow),
    }


@admin.post("/validate")
async def validate(body: ValidateBody, svc: Svc, a: Admin) -> dict[str, Any]:
    """Check a definition without saving it.

    Problems come with their path and line, and the steps as a flow to draw.

    Returns:
        ``ok``, ``problems``, ``definition`` (when ok), ``outline``, ``version``.

    Raises:
        BadRequest: Neither a text nor a definition.
    """
    app = await _app(svc, a)
    if body.text is not None:
        defn, problems, raw = wd.parse_text(body.text, body.format)
    elif body.definition is not None:
        raw = body.definition
        defn, problems = wd.parse(raw)
    else:
        raise BadRequest("Give a text or a definition.", code="empty")
    if defn is not None:
        problems = await _references(svc, app, defn)
        if problems and body.text is not None and body.format == "yaml":
            for p in problems:
                p.line = wd.line_of(body.text, p.path)
    return {
        "ok": not problems,
        "problems": [p.model_dump() for p in problems],
        "definition": defn.content() if defn is not None else None,
        "version": defn.version if defn is not None else None,
        "outline": wd.outline(defn, raw),
    }


@admin.get("/runs")
async def list_runs(
    svc: Svc,
    a: Admin,
    workflow: Annotated[str | None, Query(max_length=80)] = None,
    status: RunStatus | None = None,
    subject: Annotated[str | None, Query(max_length=200)] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Runs, newest first; pass ``next`` back as ``before`` for the following page.

    Returns:
        ``items`` and ``next``.
    """
    app = await _app(svc, a)
    rows = await svc.store.workflows.runs(
        app.id, workflow=workflow, status=status, subject=subject, before=before, limit=limit
    )
    return {
        "items": [run_view(svc, app, r, console=a.console) for r in rows],
        "next": _iso(rows[-1].started_at) if len(rows) == limit else None,
    }


@admin.get("/runs/{run_id}")
async def get_run(run_id: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """A run with its step timeline, approvals and definition outline.

    Content is shown as the application's tracing settings allow.

    Returns:
        The run.

    Raises:
        NotFound: No such run.
    """
    app = await _app(svc, a)
    r = await svc.store.workflows.run(app.id, run_id)
    if r is None:
        raise NotFound("Unknown run.", code="unknown_run")
    return await run_detail(svc, app, r, console=a.console)


@admin.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Cancel a run.

    Returns:
        The run.
    """
    app = await _app(svc, a)
    r = await svc.workflows.cancel(app.id, run_id, by=a.actor or a.key_id)
    await audit.record(request, a, "workflow.run.cancel", target=f"{r.workflow_name}/{r.id}")
    return run_view(svc, app, r, console=a.console)


@admin.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Resume a failed or cancelled run from the steps that did not end well.

    Returns:
        The run.
    """
    app = await _app(svc, a)
    r = await svc.workflows.retry(app.id, run_id)
    await audit.record(request, a, "workflow.run.retry", target=f"{r.workflow_name}/{r.id}")
    return run_view(svc, app, r, console=a.console)


@admin.get("/approvals")
async def list_approvals(
    svc: Svc,
    a: Admin,
    status: Literal["pending", "approved", "rejected", "cancelled", "all"] = "pending",
    workflow: Annotated[str | None, Query(max_length=80)] = None,
) -> dict[str, Any]:
    """The approvals queue (pending by default), newest first.

    Returns:
        ``items``.
    """
    app = await _app(svc, a)
    rows = await svc.store.workflows.approvals(
        app.id, status=None if status == "all" else status, workflow=workflow
    )
    return {"items": [approval_view(svc, app, x, console=a.console) for x in rows]}


@admin.post("/approvals/{approval_id}")
async def decide(
    approval_id: str, body: DecisionBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Approve or reject in the console; the run goes on.

    The approval step names the console roles that may decide.

    Returns:
        The decided approval.
    """
    app = await _app(svc, a)
    x = await svc.workflows.decide(
        app.id,
        approval_id,
        approve=body.decision == "approve",
        comment=body.comment,
        by=a.actor or f"key:{a.key_id[:8]}",
        via="console",
        role=a.role,
    )
    await audit.record(
        request,
        a,
        "workflow.approval.decide",
        target=f"{x.workflow_name}/{x.run_id}/{x.step_id}",
        after={"decision": x.status, "comment": bool(x.comment)},
    )
    return approval_view(svc, app, x, console=a.console)


@admin.get("/{name}")
async def get_workflow(name: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """A workflow's active definition, its versions and its steps as a flow.

    Returns:
        The workflow.

    Raises:
        NotFound: No such workflow.
    """
    app = await _app(svc, a)
    repo = svc.store.workflows
    rec = await repo.active(app.id, name)
    if rec is None:
        raise NotFound("Unknown workflow.", code="unknown_workflow")
    defn = svc.workflows.definition(rec)
    counts = await repo.counts(app.id, utcnow() - timedelta(days=30))
    last = await repo.last_runs(app.id)
    return {
        **summary_view(svc, app, rec, counts.get(name, {}), last.get(name)),
        "definition": defn.content(),
        "outline": wd.outline(defn),
        "versions": [
            {
                "version": v.version,
                "active": v.active,
                "createdAt": _iso(v.created_at),
                "createdBy": v.created_by,
            }
            for v in await repo.versions(app.id, name)
        ],
        "hookPath": f"/v1/workflow-hooks/{app.slug}/{name}"
        if any(t.kind == "webhook" for t in defn.triggers)
        else None,
    }


@admin.put("/{name}")
async def put_workflow(
    name: str, body: dict[str, Any], svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Create or change a workflow.

    A changed definition becomes a new active version; runs keep the version they started with.

    Returns:
        ``version`` and ``created``.

    Raises:
        InvalidWorkflow: The definition has problems (``problems``: path and message each).
    """
    app = await _app(svc, a)
    raw = {**body, "name": name}
    raw.pop("version", None)
    defn, problems = wd.parse(raw)
    if defn is not None:
        problems = await _references(svc, app, defn)
    if defn is None or problems:
        raise InvalidWorkflow([f"{p.path}: {p.message}" if p.path else p.message for p in problems])
    before = await svc.store.workflows.active(app.id, name)
    created = await svc.store.workflows.put(
        app.id, name, defn.version, defn.content(), created_by=a.actor or a.key_id
    )
    await audit.record(
        request,
        a,
        "workflow.put",
        target=name,
        before=before.definition if before else None,
        after=defn.content(),
    )
    return {"version": defn.version, "created": created}


@admin.delete("/{name}")
async def delete_workflow(name: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Retire a workflow: no new runs; runs going on finish with their version.

    Returns:
        ``deleted``.

    Raises:
        NotFound: No such workflow.
    """
    app = await _app(svc, a)
    before = await svc.store.workflows.active(app.id, name)
    if before is None or not await svc.store.workflows.deactivate(app.id, name):
        raise NotFound("Unknown workflow.", code="unknown_workflow")
    await audit.record(request, a, "workflow.delete", target=name, before=before.definition)
    return {"deleted": True}


@admin.post("/{name}/runs")
async def start_from_console(
    name: str, body: StartBody, svc: Svc, a: Admin, request: Request
) -> JSONResponse:
    """Start a run now (the console's «run now»).

    Returns:
        202 with the run.
    """
    app = await _app(svc, a)
    r, created = await svc.workflows.start_run(
        app,
        name,
        input=body.input,
        subject=body.subject,
        tags=body.tags,
        trigger="manual",
        trigger_key=f"manual:{body.idempotencyKey}" if body.idempotencyKey else None,
        created_by=a.actor or a.key_id,
    )
    if created:
        await audit.record(request, a, "workflow.run.start", target=f"{name}/{r.id}")
    return JSONResponse(
        {**run_view(svc, app, r, console=a.console), "created": created}, status_code=202
    )


# server


@server.post("/{name}/runs")
async def start_from_server(
    name: str, body: StartBody, svc: Svc, a: App, request: Request
) -> JSONResponse:
    """Start a run from the application's backend.

    Returns:
        202 with the run (``created`` false when the idempotency key was seen before).
    """
    app = await _app(svc, a)
    r, created = await svc.workflows.start_run(
        app,
        name,
        input=body.input,
        subject=body.subject,
        tags=body.tags,
        trigger="api",
        trigger_key=f"api:{body.idempotencyKey}" if body.idempotencyKey else None,
        created_by=a.actor or f"key:{a.key_id[:8]}",
    )
    if created:
        await audit.record(request, a, "workflow.run.start", target=f"{name}/{r.id}")
    return JSONResponse(
        {**run_view(svc, app, r, console=a.console), "created": created}, status_code=202
    )


@server.get("/runs")
async def server_runs(
    svc: Svc,
    a: App,
    workflow: Annotated[str | None, Query(max_length=80)] = None,
    status: RunStatus | None = None,
    subject: Annotated[str | None, Query(max_length=200)] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Runs, newest first.

    Returns:
        ``items`` and ``next``.
    """
    app = await _app(svc, a)
    rows = await svc.store.workflows.runs(
        app.id, workflow=workflow, status=status, subject=subject, before=before, limit=limit
    )
    return {
        "items": [run_view(svc, app, r, console=a.console) for r in rows],
        "next": _iso(rows[-1].started_at) if len(rows) == limit else None,
    }


@server.get("/runs/{run_id}")
async def server_run(run_id: str, svc: Svc, a: App) -> dict[str, Any]:
    """A run with its steps and approvals.

    Returns:
        The run.

    Raises:
        NotFound: No such run.
    """
    app = await _app(svc, a)
    r = await svc.store.workflows.run(app.id, run_id)
    if r is None:
        raise NotFound("Unknown run.", code="unknown_run")
    return await run_detail(svc, app, r, console=a.console)


@server.post("/runs/{run_id}/cancel")
async def server_cancel(run_id: str, svc: Svc, a: App, request: Request) -> dict[str, Any]:
    """Cancel a run.

    Returns:
        The run.
    """
    app = await _app(svc, a)
    r = await svc.workflows.cancel(app.id, run_id, by=a.actor or f"key:{a.key_id[:8]}")
    await audit.record(request, a, "workflow.run.cancel", target=f"{r.workflow_name}/{r.id}")
    return run_view(svc, app, r, console=a.console)


@server.get("/approvals")
async def server_approvals(
    svc: Svc,
    a: App,
    status: Literal["pending", "approved", "rejected", "cancelled", "all"] = "pending",
    workflow: Annotated[str | None, Query(max_length=80)] = None,
    subject: Annotated[str | None, Query(max_length=200)] = None,
    group: Annotated[list[str] | None, Query(max_length=50)] = None,
) -> dict[str, Any]:
    """Approvals for the application's own interface.

    With ``group`` (repeatable), only those a user in one of these groups may decide.

    Returns:
        ``items``.
    """
    app = await _app(svc, a)
    rows = await svc.store.workflows.approvals(
        app.id, status=None if status == "all" else status, workflow=workflow, subject=subject
    )
    if group:
        mine = set(group)
        rows = [
            x
            for x in rows
            if not (x.approvers or {}).get("groups") or mine & set(x.approvers["groups"])
        ]
    return {"items": [approval_view(svc, app, x, console=a.console) for x in rows]}


@server.post("/approvals/{approval_id}")
async def server_decide(
    approval_id: str, body: ServerDecisionBody, svc: Svc, a: App, request: Request
) -> dict[str, Any]:
    """Approve or reject for one of the application's users; the run goes on.

    The application vouches for the user's groups.

    Returns:
        The decided approval.
    """
    app = await _app(svc, a)
    x = await svc.workflows.decide(
        app.id,
        approval_id,
        approve=body.decision == "approve",
        comment=body.comment,
        by=body.by.name,
        via="api",
        groups=body.by.groups,
    )
    await audit.record(
        request,
        a,
        "workflow.approval.decide",
        target=f"{x.workflow_name}/{x.run_id}/{x.step_id}",
        after={"decision": x.status, "comment": bool(x.comment), "via": "api"},
    )
    return approval_view(svc, app, x, console=a.console)


# inbound webhook


@hooks.post("/{application}/{name}")
async def inbound(application: str, name: str, request: Request, svc: Svc) -> JSONResponse:
    """Start a run from an outside system: the body is signed with the webhook trigger's secret.

    Headers: ``X-Evokai-Timestamp`` (Unix seconds), ``X-Evokai-Signature`` (``v1=`` and the
    HMAC-SHA256 of ``<timestamp>.<body>``), optionally ``X-Evokai-Delivery`` (a repeated delivery
    starts no second run).

    Returns:
        202 with ``runId`` and ``created``.

    Raises:
        NotFound: Unknown application or workflow.
        BadRequest: The body is too large.
    """
    app = await svc.store.applications.by_slug(application)
    if app is None or app.status != "active":
        raise NotFound("Unknown workflow webhook.", code="unknown_workflow")
    body = await request.body()
    if len(body) > 512_000:
        raise BadRequest("The body is too large.", code="too_large")
    headers = {k.lower(): v for k, v in request.headers.items()}
    r, created = await svc.workflows.hook(app, name, body, headers)
    return JSONResponse({"runId": r.id, "status": r.status, "created": created}, status_code=202)
