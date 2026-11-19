"""Durable workflow runs: starting, leasing due steps, running them, retrying, resuming.

Every run and every step's state is in the database; the process keeps nothing a restart would
lose. A step is **due** when it is ``pending`` (or ``waiting``) and its ``due_at`` has come, or
when it is ``running`` on a lease that expired (its worker stopped). A worker takes a due step with
one conditional ``UPDATE`` (the **lease**: owner and expiry), so two workers or two instances never
run the same step at once; the lease is renewed while the step runs. When the step ends, its new
state and the next step's row are written in one transaction, guarded by the lease: a worker that
lost its lease changes nothing.

Steps run **at least once**: a step whose worker died runs again on another worker, with the same
``idempotency_key`` (``{{ step.key }}`` in templates, ``Idempotency-Key`` on notify calls) so
the systems it calls can deduplicate. Agent, tool and notify steps are retried with exponential
backoff (``retry``); every step has a timeout. Cancelling a run stops its running steps (on this
instance at once, on others at their next lease renewal) and cancels the rest.

Schedules fire once per occurrence whatever the instances: a run's ``trigger_key`` is unique, so
the instances that see the same occurrence race on an insert only one of them wins.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
import sqlalchemy as sa
from jsonschema import Draft202012Validator
from sqlalchemy.exc import IntegrityError

from evokai.agents.definitions import AgentDef
from evokai.core.errors import AppError, BadRequest, Conflict, Forbidden, NotFound, Unauthorized
from evokai.core.util import TtlCache, new_id, utcnow
from evokai.db import models as m
from evokai.grants import NotGranted, grants_of
from evokai.guard import pii
from evokai.i18n import message
from evokai.identity.roles import at_least
from evokai.knowledge.fetch import FetchRefused
from evokai.llm.catalog import bare
from evokai.observability.tracing import Span, Trace, content_mode
from evokai.privacy import subject_key
from evokai.tools.labels import render_label
from evokai.workflows import expressions as ex
from evokai.workflows import webhooks
from evokai.workflows.cron import Cron, CronError, zone
from evokai.workflows.definitions import (
    END,
    AgentStep,
    ApprovalStep,
    ConditionStep,
    NotifyStep,
    ParallelStep,
    Retry,
    ToolStep,
    Trigger,
    WaitStep,
    WorkflowDef,
    containers,
)
from evokai.workflows.store import TERMINAL, WorkflowRepository

if TYPE_CHECKING:
    from evokai.services import Services

log = logging.getLogger("evokai.workflows")

DEFAULT_TIMEOUT = {"agent": 300, "tool": 120, "notify": 30}
"""Seconds a step of these kinds may take when its definition sets no timeout."""
RETRIED = frozenset({"agent", "tool", "notify"})
"""Kinds retried by default (three attempts); the others run once unless they set ``retry``."""
NEVER = timedelta(days=36_500)
SCHEDULE_CATCH_UP = timedelta(hours=24)
"""A missed occurrence (the service was down) still fires when it is at most this old."""


class StepFailed(Exception):
    """A step that did not succeed."""

    def __init__(self, text: str, *, code: str = "error", retry: bool = True) -> None:
        """Create the failure.

        Args:
            text: What happened, for the console.
            code: A stable code (``timeout``, ``limit``, ``approval_required``...).
            retry: Whether another attempt may succeed.
        """
        super().__init__(text)
        self.text = text[:280]
        self.code = code
        self.retry = retry


@dataclass
class Outcome:
    """How a step attempt ended."""

    status: str = "done"
    """``done``, ``skipped``, ``waiting`` (for a person or branches) or ``pending`` (until
    ``due_at``)."""
    output: Any = None
    input: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    goto: str | None = None
    due_at: datetime | None = None
    end: str | None = None
    """End the run with this status (``rejected``)."""
    cost: int = 0
    new_rows: list[m.WorkflowStep] = field(default_factory=list)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    v = _aware(value)
    return v.isoformat() if v else None


def now_context(now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    """What ``now`` means in templates, in the workflow's time zone.

    Returns:
        ``iso``, ``date``, ``time``, ``month`` (``2026-11``), ``previous_month``, ``year``,
        ``weekday`` (1 = Monday).
    """
    local = now.astimezone(tz)
    first = local.date().replace(day=1)
    prev = first - timedelta(days=1)
    return {
        "iso": local.isoformat(timespec="seconds"),
        "date": local.date().isoformat(),
        "time": local.strftime("%H:%M"),
        "month": local.strftime("%Y-%m"),
        "previous_month": prev.strftime("%Y-%m"),
        "year": local.year,
        "weekday": local.isoweekday(),
    }


def mask_value(value: Any) -> Any:
    """A JSON value with personal data masked in every string.

    Returns:
        The masked copy.
    """
    if isinstance(value, str):
        return pii.mask(value)
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value


def _json_or_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


class WorkflowService:
    """Workflows of every application: runs, workers, schedules, approvals."""

    def __init__(self, svc: Services) -> None:
        """Wire the service.

        Args:
            svc: The process services.
        """
        self.svc = svc
        self.settings = svc.settings
        self.db = svc.store.db
        self.repo: WorkflowRepository = svc.store.workflows
        self.owner = f"{socket.gethostname()[:40]}:{new_id()[:8]}"
        """This instance's name on the leases it holds."""
        self.now = utcnow
        self.notifier = webhooks.Notifier(svc.http, list(svc.settings.workflow_notify_allow))
        self.wake = asyncio.Event()
        self.tasks: list[asyncio.Task[None]] = []
        self.active: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        """Step rows running in this process: row id → (run id, task)."""
        self._stopped: set[str] = set()
        self._closing = False
        self._defs: TtlCache[tuple[str, str, str], WorkflowDef] = TtlCache(500, 3_600)
        svc.engine.run_listeners.append(self.on_agent_run)

    # lifecycle

    async def start(self) -> None:
        """Start the workers and the scheduler (when workflows run in this process)."""
        if not self.settings.workflows_enabled or self.tasks:
            return
        for i in range(self.settings.workflow_workers):
            self.tasks.append(asyncio.create_task(self._worker(i)))
        self.tasks.append(asyncio.create_task(self._scheduler()))

    async def close(self) -> None:
        """Stop the workers.

        Steps they were running are resumed by another instance (or this one after a restart) once
        their lease expires.
        """
        self._closing = True
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(BaseException):
                await t
        self.tasks.clear()

    async def _worker(self, n: int) -> None:
        poll = self.settings.workflow_poll_seconds
        while True:
            try:
                got = await self.run_due(limit=4)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(
                    "workflow_worker_failed", extra={"worker": n, "error": type(e).__name__}
                )
                got = 0
            if not got:
                self.wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), poll)

    async def _scheduler(self) -> None:
        while True:
            try:
                await self.schedule_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("workflow_schedule_failed", extra={"error": type(e).__name__})
            await asyncio.sleep(self.settings.workflow_schedule_seconds)

    async def drain(self, rounds: int = 200) -> int:
        """Run due steps until none is left (tests and the CLI).

        Returns:
            How many steps ran.
        """
        total = 0
        for _ in range(rounds):
            n = await self.run_due(limit=20)
            total += n
            if not n:
                break
        return total

    # definitions

    def definition(self, rec: m.WorkflowRecord) -> WorkflowDef:
        """The definition of a stored version (cached: a version never changes).

        Returns:
            The definition.
        """
        key = (rec.application_id, rec.name, rec.version)
        hit = self._defs.get(key)
        if hit is None:
            hit = WorkflowDef.model_validate({**rec.definition, "version": rec.version})
            self._defs.set(key, hit)
        return hit

    async def _version(self, run: m.WorkflowRun) -> WorkflowDef:
        rec = await self.repo.version(run.application_id, run.workflow_name, run.workflow_version)
        if rec is None:
            raise NotFound("The workflow version of this run is gone.", code="unknown_workflow")
        return self.definition(rec)

    def timezone(self, app: m.Application, defn: WorkflowDef) -> ZoneInfo:
        """The time zone a workflow's schedules and dates are read in.

        Returns:
            Its own, else the application's, else UTC.
        """
        try:
            return zone(defn.timezone or (app.defaults or {}).get("timezone") or "UTC")
        except CronError:
            return zone("UTC")

    def masked(self, app: m.Application) -> bool:
        """Whether runs keep personal data masked (the application enforces ``pii.history``).

        Returns:
            True when they do.
        """
        return self.svc.guard.policy(app).enforced("pii.history")

    # starting

    async def start_run(
        self,
        app: m.Application,
        name: str,
        *,
        input: Any,
        subject: str | None = None,
        tags: list[str] | None = None,
        trigger: str = "manual",
        trigger_key: str | None = None,
        context: dict[str, Any] | None = None,
        created_by: str | None = None,
        strict: bool = True,
    ) -> tuple[m.WorkflowRun, bool]:
        """Start a run of the active version of a workflow.

        Args:
            app: The application.
            name: The workflow.
            input: The run's input (checked against the input schema).
            subject: Whose data the run handles, if anyone's.
            tags: Usage tags of its agent steps.
            trigger: ``manual``, ``api``, ``schedule``, ``webhook`` or ``event``.
            trigger_key: Makes the start idempotent (the same key returns the same run).
            context: What started it (``trigger`` in templates).
            created_by: Who started it (audit).
            strict: Refuse an invalid input (an automatic trigger records a failed run instead).

        Returns:
            The run, and whether it was created now.

        Raises:
            NotGranted: Workflows are not granted to the application.
            NotFound: No such active workflow.
            BadRequest: The input does not match the schema (``invalid_input``).
            IntegrityError: A conflict other than the trigger key.
        """
        grants = await grants_of(self.svc.store, app)
        if not grants.workflow():
            raise NotGranted("workflow", "Workflows are not granted to this application.")
        rec = await self.repo.active(app.id, name)
        if rec is None:
            raise NotFound("Unknown workflow.", code="unknown_workflow")
        defn = self.definition(rec)
        input = {} if input is None else input
        problems = []
        if defn.input_schema is not None:
            problems = [
                f"{'/'.join(map(str, e.path)) or '(input)'}: {e.message}"
                for e in sorted(
                    Draft202012Validator(defn.input_schema).iter_errors(input),
                    key=lambda e: list(e.path),
                )
            ][:10]
        if problems and strict:
            raise BadRequest(
                f"The input does not match the workflow's input schema: {'; '.join(problems)}",
                code="invalid_input",
            )
        masked = self.masked(app)
        now = self.now()
        run = m.WorkflowRun(
            id=new_id(),
            application_id=app.id,
            workflow_name=defn.name,
            workflow_version=defn.version,
            status="failed" if problems else "running",
            reason="invalid_input" if problems else None,
            error="; ".join(problems)[:300] if problems else None,
            subject=(subject or None) and subject[:200],
            tags=list(tags or [])[:20],
            trigger=trigger,
            trigger_key=trigger_key,
            context=mask_value(context or {}) if masked else (context or {}),
            input=mask_value(input) if masked else input,
            masked=masked,
            retention_days=defn.retention_days,
            cost_micro_usd=0,
            created_by=(created_by or None) and created_by[:200],
            started_at=now,
            updated_at=now,
            finished_at=now if problems else None,
        )
        try:
            async with self.db.session() as s:
                s.add(run)
                if not problems:
                    s.add(self._new_row(run, defn.steps[0], "", now))
        except IntegrityError:
            if trigger_key is None:
                raise
            r = m.WorkflowRun
            async with self.db.session() as s:
                q = sa.select(r).where(
                    r.application_id == app.id,
                    r.workflow_name == defn.name,
                    r.trigger_key == trigger_key,
                )
                existing = (await s.execute(q)).scalar_one_or_none()
            if existing is None:
                raise
            return existing, False
        log.info(
            "workflow_run_started",
            extra={
                "runId": run.id,
                "workflow": defn.name,
                "trigger": trigger,
                "status": run.status,
            },
        )
        self.wake.set()
        await self.store_trace(run.id)
        return run, True

    def _new_row(self, run: m.WorkflowRun, step: Any, path: str, now: datetime) -> m.WorkflowStep:
        return m.WorkflowStep(
            id=new_id(),
            run_id=run.id,
            application_id=run.application_id,
            step_id=step.id,
            kind=step.kind,
            path=path,
            status="pending",
            attempt=0,
            due_at=now,
            idempotency_key=f"{run.id}:{step.id}",
            meta={},
            cost_micro_usd=0,
            created_at=now,
        )

    # triggers

    async def _fire(
        self,
        app: m.Application,
        defn: WorkflowDef,
        trig: Trigger,
        payload: dict[str, Any],
        *,
        kind: str,
        key: str,
        default_input: Any,
        strict: bool = False,
        now: datetime | None = None,
    ) -> tuple[m.WorkflowRun, bool]:
        tz = self.timezone(app, defn)
        ctx = {"trigger": payload, "event": payload, "now": now_context(now or self.now(), tz)}
        inp = ex.render(trig.input, ctx) if trig.input is not None else default_input
        subject = ex.render_text(trig.subject, ctx) if trig.subject else None
        return await self.start_run(
            app,
            defn.name,
            input=inp,
            subject=subject or None,
            trigger=kind,
            trigger_key=key[:200],
            context={"kind": kind, **payload},
            created_by=kind,
            strict=strict,
        )

    async def schedule_tick(self, now: datetime | None = None) -> int:
        """Start the runs of the schedules whose latest occurrence has come.

        Each occurrence starts at most one run, whatever the instances (its ``trigger_key``).

        Returns:
            How many runs were started.
        """
        now = now or self.now()
        started = 0
        apps: dict[str, m.Application | None] = {}
        for rec in await self.repo.active_all():
            try:
                defn = self.definition(rec)
            except ValueError:
                continue
            schedules = [(i, t) for i, t in enumerate(defn.triggers) if t.kind == "schedule"]
            if not schedules:
                continue
            if rec.application_id not in apps:
                apps[rec.application_id] = await self.svc.store.applications.get(rec.application_id)
            app = apps[rec.application_id]
            if app is None:
                continue
            tz = self.timezone(app, defn)
            since = max(_aware(rec.created_at) or now, now - SCHEDULE_CATCH_UP)
            for i, t in schedules:
                try:
                    at = Cron.parse(t.cron or "").previous(now, tz)
                except CronError:
                    continue
                if at is None or at < since:
                    continue
                payload = {"scheduledAt": at.isoformat(), "cron": t.cron}
                try:
                    _, created = await self._fire(
                        app,
                        defn,
                        t,
                        payload,
                        kind="schedule",
                        key=f"schedule:{i}:{at.strftime('%Y%m%dT%H%MZ')}",
                        default_input={},
                        now=now,
                    )
                except AppError as e:
                    log.warning(
                        "workflow_schedule_skipped", extra={"workflow": defn.name, "error": e.code}
                    )
                    continue
                started += int(created)
        return started

    async def on_agent_run(
        self,
        app: m.Application,
        agent: str,
        run_id: str,
        status: str,
        output: Any,
        subject: str | None,
    ) -> int:
        """The internal event «an agent run finished»: start the workflows that listen to it.

        Returns:
            How many runs were started.
        """
        started = 0
        for rec in await self.repo.active_all(app.id):
            defn = self.definition(rec)
            for t in defn.triggers:
                if t.kind != "event" or t.event != "agent_run.finished":
                    continue
                if t.agent and t.agent != agent:
                    continue
                if t.status != "any" and t.status != status:
                    continue
                payload = {
                    "event": "agent_run.finished",
                    "agent": agent,
                    "runId": run_id,
                    "status": status,
                    "output": output,
                    "subject": subject,
                }
                try:
                    _, created = await self._fire(
                        app,
                        defn,
                        t,
                        payload,
                        kind="event",
                        key=f"event:{run_id}",
                        default_input={"agent": agent, "runId": run_id, "output": output},
                    )
                    started += int(created)
                except AppError as e:
                    log.warning(
                        "workflow_event_skipped", extra={"workflow": defn.name, "error": e.code}
                    )
        return started

    async def hook(
        self, app: m.Application, name: str, body: bytes, headers: dict[str, str]
    ) -> tuple[m.WorkflowRun, bool]:
        """An inbound webhook call: verify its signature and start a run.

        Returns:
            The run, and whether it was created now (a repeated delivery returns the first).

        Raises:
            NotFound: No such workflow, or it has no webhook trigger.
            Forbidden: The webhook's secret is not configured in this deployment.
            Unauthorized: The signature is wrong or the timestamp too old (``signature``).
            BadRequest: The body is not JSON.
        """
        rec = await self.repo.active(app.id, name)
        defn = self.definition(rec) if rec is not None else None
        trig = next((t for t in defn.triggers if t.kind == "webhook"), None) if defn else None
        if defn is None or trig is None:
            raise NotFound("Unknown workflow webhook.", code="unknown_workflow")
        secret = os.environ.get(trig.secret_env or "", "")
        if not secret:
            raise Forbidden("This webhook's secret is not configured.", code="hook_secret")
        ts = headers.get("x-evokai-timestamp")
        if not webhooks.verify(
            secret,
            ts,
            headers.get("x-evokai-signature"),
            body,
            self.settings.workflow_hook_tolerance,
            time.time(),
        ):
            raise Unauthorized("Invalid webhook signature.", code="signature")
        try:
            data = json.loads(body) if body.strip() else {}
        except ValueError as e:
            raise BadRequest("The body is not JSON.", code="bad_json") from e
        delivery = (
            headers.get("x-evokai-delivery")
            or hashlib.sha256(f"{ts}.".encode() + body).hexdigest()[:40]
        )
        return await self._fire(
            app,
            defn,
            trig,
            {"body": data, "delivery": delivery[:100]},
            kind="webhook",
            key=f"webhook:{delivery[:100]}",
            default_input=data if isinstance(data, dict) else {"body": data},
            strict=True,
        )

    # workers

    def _due_filter(self, now: datetime) -> Any:
        st = m.WorkflowStep
        return sa.or_(
            sa.and_(st.status.in_(("pending", "waiting")), st.due_at <= now),
            sa.and_(st.status == "running", st.lease_until < now),
        )

    async def run_due(self, limit: int = 4) -> int:
        """Lease and run the steps that are due now, one after the other.

        Returns:
            How many steps this call ran.
        """
        now = self.now()
        st = m.WorkflowStep
        async with self.db.session() as s:
            q = sa.select(st.id).where(self._due_filter(now)).order_by(st.due_at).limit(limit)
            ids = list((await s.execute(q)).scalars().all())
        ran = 0
        for sid in ids:
            if await self._lease(sid):
                ran += 1
                await self._execute(sid)
        return ran

    async def _lease(self, row_id: str) -> bool:
        now = self.now()
        st = m.WorkflowStep
        async with self.db.session() as s:
            res = await s.execute(
                sa
                .update(st)
                .where(st.id == row_id, self._due_filter(now))
                .values(
                    status="running",
                    lease_owner=self.owner,
                    lease_until=now + timedelta(seconds=self.settings.workflow_lease_seconds),
                    attempt=st.attempt + 1,
                    started_at=sa.func.coalesce(st.started_at, now),
                )
            )
            return int(getattr(res, "rowcount", 0) or 0) == 1

    async def _heartbeat(self, row_id: str, run_id: str, task: asyncio.Task[Any]) -> None:
        lease = self.settings.workflow_lease_seconds
        every = max(0.5, min(lease / 3, 5.0))
        st, r = m.WorkflowStep, m.WorkflowRun
        while True:
            await asyncio.sleep(every)
            try:
                async with self.db.session() as s:
                    await s.execute(
                        sa
                        .update(st)
                        .where(
                            st.id == row_id, st.lease_owner == self.owner, st.status == "running"
                        )
                        .values(lease_until=self.now() + timedelta(seconds=lease))
                    )
                    status = (await s.execute(sa.select(r.status).where(r.id == run_id))).scalar()
            except Exception as e:
                log.warning("workflow_lease_not_renewed", extra={"error": type(e).__name__})
                continue
            if status is None or status in TERMINAL:
                self._stopped.add(row_id)
                task.cancel()
                return

    def _retry(self, sdef: Any) -> Retry:
        if sdef is not None and sdef.retry is not None:
            return sdef.retry
        if sdef is not None and sdef.kind in RETRIED:
            return Retry()
        return Retry(max_attempts=1, backoff_seconds=0)

    async def _execute(self, row_id: str) -> None:
        store = self.svc.store
        async with self.db.session() as s:
            row = await s.get(m.WorkflowStep, row_id)
            run = await s.get(m.WorkflowRun, row.run_id) if row is not None else None
        if row is None:
            return
        app = await store.applications.get(row.application_id) if run is not None else None
        if run is None or app is None or run.status in TERMINAL:
            await self._set(row, status="cancelled")
            return
        try:
            defn = await self._version(run)
        except NotFound:
            await self._failure(
                row, run, None, None, StepFailed("The workflow version is gone.", retry=False), {}
            )
            return
        sdef = defn.step(row.step_id)
        tz = self.timezone(app, defn)
        rows = await self.repo.steps(run.id)
        ctx = self._context(run, rows, tz, row)
        retry = self._retry(sdef)
        if sdef is None:
            await self._failure(row, run, defn, None, StepFailed("Unknown step.", retry=False), ctx)
            return
        if sdef.kind in RETRIED and row.attempt > retry.max_attempts:
            await self._failure(
                row,
                run,
                defn,
                sdef,
                StepFailed("The step stopped too many times.", code="attempts", retry=False),
                ctx,
            )
            return
        task = asyncio.current_task()
        assert task is not None  # ruff: ignore[assert] (always inside a task)
        self.active[row.id] = (run.id, task)
        beat = asyncio.create_task(self._heartbeat(row.id, run.id, task))
        timeout = (
            None
            if sdef.kind == "approval"
            else sdef.timeout_seconds or DEFAULT_TIMEOUT.get(sdef.kind)
        )
        failure: StepFailed | None = None
        outcome: Outcome | None = None
        try:
            coro = self._run_step(app, run, defn, sdef, row, ctx, tz)
            outcome = await (asyncio.wait_for(coro, timeout) if timeout else coro)
        except asyncio.CancelledError:
            if row.id in self._stopped and not self._closing:
                self._stopped.discard(row.id)
                uncancel = getattr(task, "uncancel", None)
                if uncancel is not None:
                    uncancel()
                return
            raise
        except TimeoutError:
            failure = StepFailed(f"The step took longer than {timeout} seconds.", code="timeout")
        except StepFailed as e:
            failure = e
        except ex.ExpressionError as e:
            failure = StepFailed(f"Template: {e}", code="template", retry=False)
        except Exception as e:
            log.warning(
                "workflow_step_error",
                extra={"runId": run.id, "step": row.step_id, "error": type(e).__name__},
            )
            failure = StepFailed(f"Unexpected error ({type(e).__name__}).", code="internal")
        finally:
            beat.cancel()
            self.active.pop(row.id, None)
        if failure is not None:
            await self._failure(row, run, defn, sdef, failure, ctx)
        elif outcome is not None:
            await self._complete(row, run, defn, sdef, outcome, ctx)

    def _context(
        self,
        run: m.WorkflowRun,
        rows: list[m.WorkflowStep],
        tz: ZoneInfo,
        row: m.WorkflowStep | None = None,
    ) -> dict[str, Any]:
        steps = {
            r.step_id: {
                "output": r.output,
                "status": r.status,
                "error": r.error,
                "attempt": r.attempt,
            }
            for r in rows
            if r.kind != "join"
        }
        ctx: dict[str, Any] = {
            "input": run.input if run.input is not None else {},
            "steps": steps,
            "run": {
                "id": run.id,
                "workflow": run.workflow_name,
                "version": run.workflow_version,
                "subject": run.subject,
                "trigger": run.trigger,
                "startedAt": _iso(run.started_at),
            },
            "trigger": run.context or {},
            "event": run.context or {},
            "now": now_context(self.now(), tz),
        }
        if row is not None:
            ctx["step"] = {"id": row.step_id, "key": row.idempotency_key, "attempt": row.attempt}
        return ctx

    def _stored(self, run: m.WorkflowRun, value: Any) -> Any:
        return mask_value(value) if run.masked else value

    async def _set(self, row: m.WorkflowStep, **values: Any) -> None:
        st = m.WorkflowStep
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(st)
                .where(st.id == row.id, st.lease_owner == self.owner)
                .values(lease_owner=None, lease_until=None, **values)
            )

    async def _complete(
        self,
        row: m.WorkflowStep,
        run: m.WorkflowRun,
        defn: WorkflowDef,
        sdef: Any,
        out: Outcome,
        ctx: dict[str, Any],
    ) -> None:
        now = self.now()
        st = m.WorkflowStep
        values: dict[str, Any] = {
            "lease_owner": None,
            "lease_until": None,
            "error": None,
            "meta": {**(row.meta or {}), **out.meta},
        }
        if out.input is not None:
            values["input"] = self._stored(run, out.input)
        if out.status in {"done", "skipped"}:
            values.update(
                status=out.status,
                output=self._stored(run, out.output),
                finished_at=now,
                cost_micro_usd=(row.cost_micro_usd or 0) + out.cost,
            )
        else:
            values.update(status=out.status, due_at=out.due_at or now)
        settle: list[tuple[str, int]] = []
        async with self.db.session() as s:
            res = await s.execute(
                sa
                .update(st)
                .where(st.id == row.id, st.lease_owner == self.owner, st.status == "running")
                .values(**values)
            )
            if int(getattr(res, "rowcount", 0) or 0) != 1:
                return
            run_row = await s.get(m.WorkflowRun, run.id, populate_existing=True)
            if run_row is None or run_row.status in TERMINAL:
                return
            run_row.cost_micro_usd = (run_row.cost_micro_usd or 0) + out.cost
            for new in out.new_rows:
                s.add(new)
            if out.status in {"done", "skipped"}:
                ctx["steps"][sdef.id] = {"output": out.output, "status": out.status}
                if out.end:
                    await self._end_run(s, run_row, out.end, reason=sdef.id, output=out.output)
                else:
                    settle = await self._advance(s, run_row, defn, sdef, out.goto, ctx, out.output)
            await self._run_status(s, run_row)
        await self._after(run.id, settle)

    async def _after(self, run_id: str, settle: list[tuple[str, int]]) -> None:
        for pid, n in settle:
            await self._settle(run_id, pid, n)
        self.wake.set()
        await self.store_trace(run_id)

    async def _run_status(self, s: Any, run: m.WorkflowRun) -> None:
        if run.status in TERMINAL:
            return
        st = m.WorkflowStep
        waiting = (
            await s.execute(
                sa.select(sa.func.count()).where(
                    st.run_id == run.id, st.kind == "approval", st.status == "waiting"
                )
            )
        ).scalar_one()
        run.status = "waiting" if waiting else "running"
        run.updated_at = self.now()

    async def _advance(
        self,
        s: Any,
        run: m.WorkflowRun,
        defn: WorkflowDef,
        sdef: Any,
        goto: str | None,
        ctx: dict[str, Any],
        last: Any,
    ) -> list[tuple[str, int]]:
        """Create the next step's row, or end the list the step was in.

        Returns:
            The parallel steps whose branches may now all be done (to settle after commit).
        """
        now = self.now()
        path, items = containers(defn)[sdef.id]
        target = goto if goto is not None else sdef.next
        nxt = None
        if target and target != END:
            nxt = defn.step(target)
        elif not target:
            ids = [x.id for x in items]
            i = ids.index(sdef.id)
            nxt = items[i + 1] if i + 1 < len(items) else None
        if nxt is not None:
            s.add(self._new_row(run, nxt, path, now))
            return []
        if not path:
            try:
                output = ex.render(defn.output, ctx) if defn.output is not None else last
            except ex.ExpressionError as e:
                await self._end_run(s, run, "failed", reason="output", error=f"output: {e}")
                return []
            await self._end_run(s, run, "done", output=output)
            return []
        pid, _, branch = path.partition("/")
        s.add(
            m.WorkflowStep(
                id=new_id(),
                run_id=run.id,
                application_id=run.application_id,
                step_id=f"{pid}#{branch}",
                kind="join",
                path=path,
                status="done",
                attempt=1,
                due_at=now,
                idempotency_key=f"{run.id}:{pid}#{branch}",
                output=self._stored(run, last),
                meta={},
                cost_micro_usd=0,
                created_at=now,
                finished_at=now,
            )
        )
        parallel = defn.step(pid)
        return [(pid, len(parallel.branches))]

    async def _settle(self, run_id: str, pid: str, branches: int) -> None:
        """Wake a parallel step once every branch has ended.

        Run after the joins committed, so branches that end at the same moment on two workers
        cannot both miss each other.
        """
        st = m.WorkflowStep
        async with self.db.session() as s:
            joined = (
                await s.execute(
                    sa.select(sa.func.count()).where(
                        st.run_id == run_id, st.kind == "join", st.step_id.like(f"{pid}#%")
                    )
                )
            ).scalar_one()
            if joined >= branches:
                await s.execute(
                    sa
                    .update(st)
                    .where(st.run_id == run_id, st.step_id == pid, st.status == "waiting")
                    .values(status="pending", due_at=self.now())
                )

    async def _end_run(
        self,
        s: Any,
        run: m.WorkflowRun,
        status: str,
        *,
        reason: str | None = None,
        output: Any = None,
        error: str | None = None,
    ) -> None:
        now = self.now()
        run.status = status
        run.reason = (reason or None) and reason[:60]
        run.output = self._stored(run, output)
        run.error = (error or None) and error[:300]
        run.finished_at = now
        run.updated_at = now
        st, a = m.WorkflowStep, m.WorkflowApproval
        await s.execute(
            sa
            .update(st)
            .where(st.run_id == run.id, st.status.in_(("pending", "waiting", "running")))
            .values(status="cancelled", lease_owner=None, lease_until=None, finished_at=now)
        )
        await s.execute(
            sa
            .update(a)
            .where(a.run_id == run.id, a.status == "pending")
            .values(status="cancelled", decided_at=now)
        )
        log.info("workflow_run_ended", extra={"runId": run.id, "status": status, "reason": reason})

    async def _failure(
        self,
        row: m.WorkflowStep,
        run: m.WorkflowRun,
        defn: WorkflowDef | None,
        sdef: Any,
        err: StepFailed,
        ctx: dict[str, Any],
    ) -> None:
        now = self.now()
        retry = self._retry(sdef)
        st = m.WorkflowStep
        if err.retry and row.attempt < retry.max_attempts:
            delay = min(
                retry.backoff_seconds * (2 ** max(0, row.attempt - 1)), retry.max_backoff_seconds
            )
            await self._set(
                row,
                status="pending",
                due_at=now + timedelta(seconds=delay),
                error=err.text,
            )
            log.info(
                "workflow_step_retry",
                extra={
                    "runId": run.id,
                    "step": row.step_id,
                    "attempt": row.attempt,
                    "code": err.code,
                },
            )
            await self.store_trace(run.id)
            return
        settle: list[tuple[str, int]] = []
        async with self.db.session() as s:
            res = await s.execute(
                sa
                .update(st)
                .where(st.id == row.id, st.lease_owner == self.owner, st.status == "running")
                .values(
                    status="failed",
                    error=err.text,
                    finished_at=now,
                    lease_owner=None,
                    lease_until=None,
                    meta={**(row.meta or {}), "code": err.code},
                )
            )
            if int(getattr(res, "rowcount", 0) or 0) != 1:
                return
            run_row = await s.get(m.WorkflowRun, run.id, populate_existing=True)
            if run_row is None or run_row.status in TERMINAL:
                return
            if sdef is not None and defn is not None and sdef.continue_on_error:
                ctx.setdefault("steps", {})[sdef.id] = {
                    "output": None,
                    "status": "failed",
                    "error": err.text,
                }
                settle = await self._advance(s, run_row, defn, sdef, None, ctx, None)
            else:
                await self._end_run(
                    s, run_row, "failed", reason=err.code, error=f"{row.step_id}: {err.text}"
                )
            await self._run_status(s, run_row)
        await self._after(run.id, settle)

    # step kinds

    async def _run_step(
        self,
        app: m.Application,
        run: m.WorkflowRun,
        defn: WorkflowDef,
        sdef: Any,
        row: m.WorkflowStep,
        ctx: dict[str, Any],
        tz: ZoneInfo,
    ) -> Outcome:
        if isinstance(sdef, AgentStep):
            return await self._agent(app, run, defn, sdef, ctx, row)
        if isinstance(sdef, ToolStep):
            return await self._tool(app, run, sdef, row, ctx)
        if isinstance(sdef, ApprovalStep):
            return await self._approval(app, run, defn, sdef, ctx)
        if isinstance(sdef, ConditionStep):
            value = ex.truthy(ex.evaluate_text(sdef.if_, ctx))
            goto = sdef.then if value else sdef.else_
            return Outcome(output={"value": value}, goto=goto, meta={"next": goto or "(next)"})
        if isinstance(sdef, WaitStep):
            return self._wait(sdef, row, ctx, tz)
        if isinstance(sdef, ParallelStep):
            return await self._parallel(run, sdef, row)
        if isinstance(sdef, NotifyStep):
            return await self._notify(run, sdef, row, ctx)
        raise StepFailed(f"Unknown step kind {sdef.kind}.", retry=False)

    async def _agent(
        self,
        app: m.Application,
        run: m.WorkflowRun,
        defn: WorkflowDef,
        sdef: AgentStep,
        ctx: dict[str, Any],
        row: m.WorkflowStep | None = None,
    ) -> Outcome:
        from evokai.sessions.runs import AgentRunner, Attachment, LimitReached

        try:
            agent = await self.svc.agents.active(app.id, sdef.agent)
        except NotFound as e:
            raise StepFailed(
                f"Unknown agent {sdef.agent}.", code="unknown_agent", retry=False
            ) from e
        if sdef.output_schema is not None:
            agent = agent.model_copy(update={"output_schema": sdef.output_schema})
        value = ex.render(sdef.input, ctx)
        text = value if isinstance(value, str) else None
        messages = None
        if text is None:
            if isinstance(value, list):
                messages = [
                    {
                        "role": str(x.get("role") or "user"),
                        "text": ex.render_text(x.get("text"), {}),
                    }
                    for x in value
                    if isinstance(x, dict)
                ]
            else:
                text = ex.render_text(value, {})
        attachments = [
            Attachment(
                str(a.get("name") or "attachment")[:200],
                str(a.get("mediaType") or "text/plain")[:100],
                ex.render_text(a.get("text"), {})[:200_000],
            )
            for a in ex.render(sdef.attachments, ctx)
            if isinstance(a, dict)
        ]
        if sdef.durable and row is not None:
            return await self._durable_agent(
                app, run, defn, sdef, row, value, text, messages, attachments
            )
        try:
            result = await AgentRunner(self.svc.engine).run(
                app,
                agent,
                text=text,
                messages=messages,
                attachments=attachments,
                subject=run.subject or f"system:workflow:{run.workflow_name}",
                tags=list(
                    dict.fromkeys([f"workflow:{run.workflow_name}", *defn.tags, *(run.tags or [])])
                )[:20],
                groups=sdef.groups,
                origin="workflow",
            )
        except LimitReached as e:
            raise StepFailed(e.message, code="limit", retry=False) from e
        except AppError as e:
            transient = e.status >= 500 or (e.status == 429 and e.code != "limit")
            raise StepFailed(
                e.message, code=e.code, retry=transient or e.code == "invalid_output"
            ) from e
        return Outcome(
            output=result.output,
            input=value,
            cost=result.cost_micro_usd,
            meta={
                "agent": agent.name,
                "agentVersion": agent.version,
                "runId": result.run_id,
                "model": bare(result.model),
                "inputTokens": result.input_tokens,
                "outputTokens": result.output_tokens,
                "toolCalls": result.tool_calls,
            },
        )

    async def _durable_agent(
        self,
        app: m.Application,
        run: m.WorkflowRun,
        defn: WorkflowDef,
        sdef: AgentStep,
        row: m.WorkflowStep,
        value: Any,
        text: str | None,
        messages: list[dict[str, Any]] | None,
        attachments: list[Any],
    ) -> Outcome:
        """An agent step as a pgtask task (``evokai.workflows.durable``).

        Returns:
            The step's outcome.

        Raises:
            StepFailed: Durable steps are off here, or the task did not succeed.
        """
        from evokai.workflows.durable import DurableFailed

        durable = getattr(self.svc, "durable", None)
        if durable is None or not durable.enabled:
            raise StepFailed(
                "Durable agent steps need Postgres and EVOKAI_WORKFLOW_DURABLE_AGENTS.",
                code="durable_unavailable",
                retry=False,
            )
        payload = {
            "applicationId": app.id,
            "agent": sdef.agent,
            "text": text,
            "messages": messages,
            "attachments": [
                {"name": a.name, "media_type": a.media_type, "text": a.text} for a in attachments
            ],
            "subject": run.subject or f"system:workflow:{run.workflow_name}",
            "tags": list(
                dict.fromkeys([f"workflow:{run.workflow_name}", *defn.tags, *(run.tags or [])])
            )[:20],
            "groups": sdef.groups,
            "outputSchema": sdef.output_schema,
        }
        timeout = float(sdef.timeout_seconds or 300)
        try:
            out = await durable.run(payload, key=row.idempotency_key, timeout=timeout)
        except DurableFailed as e:
            raise StepFailed(e.text, code="durable", retry=e.retry) from e
        return Outcome(
            output=out.get("output"),
            input=value,
            cost=int(out.get("costMicroUsd") or 0),
            meta=dict(out.get("meta") or {}),
        )

    async def _tool(
        self,
        app: m.Application,
        run: m.WorkflowRun,
        sdef: ToolStep,
        row: m.WorkflowStep,
        ctx: dict[str, Any],
    ) -> Outcome:
        store = self.svc.store
        grants = await grants_of(store, app)
        meta = {"server": sdef.server, "tool": sdef.tool}
        approval = (ctx["steps"].get(sdef.approval) or {}).get("output") if sdef.approval else None
        approved = isinstance(approval, dict) and approval.get("approved") is True
        if sdef.approval and not approved:
            return Outcome(status="skipped", meta={**meta, "skipped": "not approved"})
        servers = {x.name: x for x in await store.mcp_servers.all(app.id)}
        server = servers.get(sdef.server)
        if server is None or not grants.mcp(sdef.server):
            raise StepFailed(
                f"The MCP server {sdef.server} is unknown or not granted.",
                code="not_granted",
                retry=False,
            )
        if server.auth == "forward_user":
            raise StepFailed(
                f"The MCP server {sdef.server} acts as a signed-in user (forward_user); a workflow "
                "has none: use a server with auth secret or a connector.",
                code="forward_user",
                retry=False,
            )
        stub = AgentDef(name="workflow", model="none", instructions="-", mcp_servers=[sdef.server])
        box = await self.svc.mcp.toolbox(app.id, stub, None, grants)
        name = f"{server.tool_prefix}{sdef.tool}"
        tool = next((t for t in box.tools if t.name == name), None)
        if tool is None:
            if sdef.server in box.unavailable:
                raise StepFailed(
                    f"The MCP server {sdef.server} did not answer.", code="unavailable"
                )
            raise StepFailed(
                f"The tool {sdef.tool} is unknown or not granted.", code="not_granted", retry=False
            )
        guard = self.svc.guard.turn(
            app,
            None,
            subject=run.subject or f"system:workflow:{run.workflow_name}",
            session_id=None,
            turn_id=row.id,
            channel="server",
        )
        args = ex.render(sdef.arguments, ctx)
        # A tool an ask rule makes wait for a person needs an approval step, like one that
        # changes data.
        mutating = (
            tool.mutating or guard.forced_confirmation(name) or guard.asks(name, args) is not None
        )
        if mutating:
            by_timeout = isinstance(approval, dict) and approval.get("via") == "timeout"
            autonomous = grants.workflow("autonomous")
            allowed = (approved and not by_timeout) or (
                autonomous and (sdef.safe or (approved and by_timeout))
            )
            if not allowed:
                raise StepFailed(
                    f"The tool {sdef.tool} changes data: it needs an approval step decided by a "
                    "person (or safe: true and the workflows.autonomous grant).",
                    code="approval_required",
                    retry=False,
                )
        decision = await guard.check_call(name, args, tool.input_schema)
        if not decision.allowed:
            await guard.flush()
            raise StepFailed(
                f"Blocked by the security policy: {decision.reason}", code="blocked", retry=False
            )
        result = await box.call(name, guard.call_args(args))
        text = await guard.tool_result(name, result.text)
        await guard.flush()
        if not result.ok:
            raise StepFailed(f"The tool answered with an error: {text[:200]}", code="tool_error")
        return Outcome(output=_json_or_text(text), input=args, meta={**meta, "mutating": mutating})

    def _summary(
        self, app: m.Application, defn: WorkflowDef, sdef: ApprovalStep, ctx: dict[str, Any]
    ) -> list[str]:
        if sdef.summary is not None:
            items = sdef.summary if isinstance(sdef.summary, list) else [sdef.summary]
            return [ex.render_text(x, ctx)[:500] for x in items]
        out = []
        for t in defn.all_steps():
            if not isinstance(t, ToolStep) or t.approval != sdef.id:
                continue
            try:
                args = ex.render(t.arguments, ctx)
            except ex.ExpressionError:
                args = {}
            templates = [t.label] if isinstance(t.label, str) else t.label
            label = next(
                (
                    x
                    for x in (
                        render_label(tpl, args if isinstance(args, dict) else {}, t.label_values)
                        for tpl in templates
                    )
                    if x
                ),
                None,
            )
            out.append((label or message(app, "confirm.default", tool=t.tool))[:500])
        return out

    async def _approval(
        self,
        app: m.Application,
        run: m.WorkflowRun,
        defn: WorkflowDef,
        sdef: ApprovalStep,
        ctx: dict[str, Any],
    ) -> Outcome:
        now = self.now()
        a = m.WorkflowApproval
        async with self.db.session() as s:
            q = (
                sa
                .select(a)
                .where(a.run_id == run.id, a.step_id == sdef.id, a.status != "cancelled")
                .order_by(a.created_at.desc())
                .limit(1)
            )
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                question = ex.render_text(sdef.question, ctx)[:1000]
                summary = self._summary(app, defn, sdef, ctx)
                details = ex.render(sdef.details, ctx)
                expires = now + timedelta(seconds=sdef.timeout_seconds or 7 * 86_400)
                row = a(
                    id=new_id(),
                    application_id=app.id,
                    run_id=run.id,
                    step_id=sdef.id,
                    workflow_name=run.workflow_name,
                    subject=run.subject,
                    status="pending",
                    question=self._stored(run, question),
                    summary=self._stored(run, summary),
                    details=self._stored(run, details),
                    approvers=sdef.approvers.model_dump(),
                    on_timeout=sdef.on_timeout,
                    expires_at=expires,
                    created_at=now,
                )
                s.add(row)
                return Outcome(status="waiting", due_at=expires, meta={"approvalId": row.id})
            expires = _aware(row.expires_at) or now
            if row.status == "pending":
                if now < expires:
                    return Outcome(status="waiting", due_at=expires, meta={"approvalId": row.id})
                decided = "approved" if row.on_timeout == "approve" else "rejected"
                await s.execute(
                    sa
                    .update(a)
                    .where(a.id == row.id, a.status == "pending")
                    .values(
                        status=decided, decided_via="timeout", decided_at=now, decided_by="timeout"
                    )
                )
                row = await s.get(a, row.id, populate_existing=True)
                assert row is not None  # ruff: ignore[assert] (just updated)
        approved = row.status == "approved"
        output = {
            "approved": approved,
            "decision": row.status,
            "by": row.decided_by,
            "via": row.decided_via,
            "comment": row.comment,
            "at": _iso(row.decided_at),
        }
        meta = {"approvalId": row.id, "decision": row.status, "via": row.decided_via}
        if not approved and sdef.on_reject == "end":
            return Outcome(output=output, end="rejected", meta=meta)
        return Outcome(output=output, meta=meta)

    def _wait(
        self, sdef: WaitStep, row: m.WorkflowStep, ctx: dict[str, Any], tz: ZoneInfo
    ) -> Outcome:
        now = self.now()
        planned = (row.meta or {}).get("until")
        if planned:
            until = datetime.fromisoformat(planned)
        elif sdef.seconds is not None:
            until = now + timedelta(seconds=sdef.seconds)
        else:
            value = ex.render_text(sdef.until, ctx).strip()
            try:
                if len(value) == 10:
                    until = datetime.combine(
                        date.fromisoformat(value), datetime.min.time(), tzinfo=tz
                    )
                else:
                    until = datetime.fromisoformat(value)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=tz)
            except ValueError as e:
                raise StepFailed(
                    f"until is not an ISO date or time: {value[:60]}", code="template", retry=False
                ) from e
            if until - now > timedelta(seconds=366 * 86_400):
                raise StepFailed("A wait is at most a year.", code="too_long", retry=False)
        until = until.astimezone(UTC)
        if until <= now:
            return Outcome(output={"until": until.isoformat()}, meta={"until": until.isoformat()})
        return Outcome(status="pending", due_at=until, meta={"until": until.isoformat()})

    async def _parallel(
        self, run: m.WorkflowRun, sdef: ParallelStep, row: m.WorkflowStep
    ) -> Outcome:
        now = self.now()
        n = len(sdef.branches)
        meta = {"started": True, "branches": n}
        if not (row.meta or {}).get("started"):
            rows = [
                self._new_row(run, b.steps[0], f"{sdef.id}/{i}", now)
                for i, b in enumerate(sdef.branches)
            ]
            return Outcome(status="waiting", due_at=now + NEVER, meta=meta, new_rows=rows)
        st = m.WorkflowStep
        async with self.db.session() as s:
            joins = (
                (
                    await s.execute(
                        sa.select(st).where(
                            st.run_id == run.id, st.kind == "join", st.step_id.like(f"{sdef.id}#%")
                        )
                    )
                )
                .scalars()
                .all()
            )
        by_branch = {j.step_id.partition("#")[2]: j.output for j in joins}
        if len(by_branch) < n:
            return Outcome(status="waiting", due_at=now + NEVER, meta=meta)
        return Outcome(
            output={b.name: by_branch.get(str(i)) for i, b in enumerate(sdef.branches)}, meta=meta
        )

    async def _notify(
        self, run: m.WorkflowRun, sdef: NotifyStep, row: m.WorkflowStep, ctx: dict[str, Any]
    ) -> Outcome:
        url = ex.render_text(sdef.url, ctx).strip()
        if sdef.body is not None:
            body = ex.render(sdef.body, ctx)
        else:
            body = {
                "runId": run.id,
                "workflow": run.workflow_name,
                "steps": {k: v.get("output") for k, v in ctx["steps"].items()},
            }
        secret = None
        if sdef.secret_env:
            secret = os.environ.get(sdef.secret_env)
            if not secret:
                raise StepFailed(
                    f"The secret {sdef.secret_env} is not set.", code="secret", retry=False
                )
        try:
            sent = await self.notifier.send(
                url,
                body,
                secret=secret,
                idempotency_key=row.idempotency_key,
                headers={"X-Evokai-Workflow": run.workflow_name, "X-Evokai-Run": run.id},
            )
        except FetchRefused as e:
            raise StepFailed(e.message, code="refused", retry=False) from e
        except httpx.HTTPError as e:
            raise StepFailed(
                f"The address did not answer ({type(e).__name__}).", code="unreachable"
            ) from e
        host = httpx.URL(url).host
        if not sent.ok:
            raise StepFailed(
                f"The address answered {sent.status}.",
                code="http_status",
                retry=sent.status >= 500 or sent.status == 429,
            )
        return Outcome(output={"status": sent.status}, meta={"host": host, "status": sent.status})

    # control

    async def cancel(self, app_id: str, run_id: str, by: str | None = None) -> m.WorkflowRun:
        """Cancel a run: its open steps and approvals are cancelled, running steps stopped.

        Returns:
            The run.

        Raises:
            NotFound: No such run.
            Conflict: It already ended.
        """
        async with self.db.session() as s:
            run = await s.get(m.WorkflowRun, run_id)
            if run is None or run.application_id != app_id:
                raise NotFound("Unknown run.", code="unknown_run")
            if run.status in TERMINAL:
                raise Conflict("This run already ended.", code="ended")
            await self._end_run(s, run, "cancelled", reason="cancelled", error=(by and f"by {by}"))
        for row_id, (rid, task) in list(self.active.items()):
            if rid == run_id:
                self._stopped.add(row_id)
                task.cancel()
        await self.store_trace(run_id)
        return run

    async def retry(self, app_id: str, run_id: str) -> m.WorkflowRun:
        """Resume a failed or cancelled run from the steps that did not end well.

        Returns:
            The run.

        Raises:
            NotFound: No such run.
            Conflict: The run did not fail and was not cancelled.
        """
        now = self.now()
        st = m.WorkflowStep
        async with self.db.session() as s:
            run = await s.get(m.WorkflowRun, run_id)
            if run is None or run.application_id != app_id:
                raise NotFound("Unknown run.", code="unknown_run")
            if run.status not in {"failed", "cancelled"} or run.reason == "invalid_input":
                raise Conflict(
                    "Only a failed or cancelled run can be retried.", code="not_retryable"
                )
            res = await s.execute(
                sa
                .update(st)
                .where(st.run_id == run_id, st.status.in_(("failed", "cancelled")))
                .values(status="pending", attempt=0, due_at=now, error=None, finished_at=None)
            )
            if not int(getattr(res, "rowcount", 0) or 0):
                raise Conflict("Nothing to retry in this run.", code="not_retryable")
            run.status, run.reason, run.error, run.finished_at = "running", None, None, None
            run.updated_at = now
        self.wake.set()
        await self.store_trace(run_id)
        return run

    async def decide(
        self,
        app_id: str,
        approval_id: str,
        *,
        approve: bool,
        comment: str | None,
        by: str,
        via: str,
        role: str | None = None,
        groups: list[str] | None = None,
    ) -> m.WorkflowApproval:
        """Approve or reject: the run goes on from the approval step.

        Args:
            app_id: The application.
            approval_id: The approval.
            approve: The decision.
            comment: Why, for the record.
            by: Who decided (a console identity, or the application's user).
            via: ``console`` or ``api``.
            role: The console role of whoever decided (``console``).
            groups: The groups the application vouches the user is in (``api``).

        Returns:
            The decided approval.

        Raises:
            NotFound: No such approval.
            Conflict: It was already decided, or its run ended.
            Forbidden: Not one of the approvers (``not_approver``).
        """
        now = self.now()
        a, st = m.WorkflowApproval, m.WorkflowStep
        async with self.db.session() as s:
            row = await s.get(a, approval_id)
            if row is None or row.application_id != app_id:
                raise NotFound("Unknown approval.", code="unknown_approval")
            if row.status != "pending":
                raise Conflict("This approval was already decided.", code="decided")
            approvers = row.approvers or {}
            if via == "console":
                roles = approvers.get("roles") or ["operator"]
                if not any(at_least(role, r) for r in roles):
                    raise Forbidden(
                        "You are not one of the approvers of this step.", code="not_approver"
                    )
            else:
                need = set(approvers.get("groups") or [])
                if need and not need & set(groups or []):
                    raise Forbidden(
                        "The user is not in a group that may approve this step.",
                        code="not_approver",
                    )
            res = await s.execute(
                sa
                .update(a)
                .where(a.id == row.id, a.status == "pending")
                .values(
                    status="approved" if approve else "rejected",
                    decided_by=by[:200],
                    decided_via=via,
                    comment=(comment or None) and comment[:2000],
                    decided_at=now,
                )
            )
            if not int(getattr(res, "rowcount", 0) or 0):
                raise Conflict("This approval was already decided.", code="decided")
            await s.execute(
                sa
                .update(st)
                .where(st.run_id == row.run_id, st.step_id == row.step_id, st.status == "waiting")
                .values(status="pending", due_at=now)
            )
            run_id = row.run_id
        self.wake.set()
        async with self.db.session() as s:
            decided = await s.get(a, approval_id)
        assert decided is not None  # ruff: ignore[assert] (just updated)
        log.info(
            "workflow_approval_decided",
            extra={"runId": run_id, "decision": decided.status, "via": via},
        )
        return decided

    # traces

    async def store_trace(self, run_id: str) -> None:
        """Keep the run's trace current: a ``workflow`` root and one ``step`` span per step.

        Never raises. It goes to OpenTelemetry once, when the run ends.
        """
        try:
            async with self.db.session() as s:
                run = await s.get(m.WorkflowRun, run_id)
            if run is None:
                return
            app = await self.svc.store.applications.get(run.application_id)
            if app is None:
                return
            rows = [r for r in await self.repo.steps(run_id) if r.kind != "join"]
            await self.svc.store.traces.add(self.trace_row(app, run, rows))
        except Exception as e:
            log.warning("workflow_trace_failed", extra={"runId": run_id, "error": type(e).__name__})

    def trace_row(
        self, app: m.Application, run: m.WorkflowRun, rows: list[m.WorkflowStep]
    ) -> dict[str, Any]:
        """The trace of a run, built from its stored state.

        Returns:
            The ``evokai_traces`` row.
        """
        policy = self.svc.guard.policy(app)
        content = content_mode(
            app,
            storage="evokai",
            pii_masked=policy.enforced("pii.history") or policy.enforced("pii.model"),
        )
        subject = run.subject or ""
        tr = Trace(
            trace_id=run.id,
            kind="workflow",
            application_id=app.id,
            agent=run.workflow_name,
            version=run.workflow_version,
            subject=subject,
            subject_key=subject_key(self.svc.keyring.signing_key, app.id, subject)[:16],
            session_id=None,
            channel="server",
            content=content,
            known_secrets=self.svc.guard.known_secrets,
        )
        started = _aware(run.started_at) or self.now()
        tr.started_at = started
        tr._epoch_ns = int(started.timestamp() * 1e9)

        def off(t: datetime | None) -> float:
            return max(0.0, ((_aware(t) or started) - started).total_seconds() * 1000)

        end = _aware(run.finished_at) or self.now()
        root = tr.root
        root.name = f"workflow {run.workflow_name}"[:120]
        root.start, root.end = 0.0, off(end)
        root.set(
            workflow=run.workflow_name,
            version=run.workflow_version,
            trigger=run.trigger,
            steps=len(rows),
        )
        tr.put(root, input=run.input, output=run.output)
        tokens_in = tokens_out = 0
        for r in rows:
            meta = r.meta or {}
            span = Span(
                id=r.id.replace("-", "")[:16],
                parent=root.id,
                kind="step",
                name=f"step {r.step_id}",
                start=off(r.started_at or r.created_at),
                end=off(r.finished_at)
                if r.finished_at
                else (None if r.status == "running" else off(r.started_at or r.created_at)),
                status="error"
                if r.status == "failed"
                else "cancelled"
                if r.status == "cancelled"
                else "ok",
            )
            span.set(
                step=r.step_id,
                stepKind=r.kind,
                outcome=r.status,
                attempt=r.attempt,
                error=r.error,
                branch=r.path or None,
                **{
                    k: v
                    for k, v in meta.items()
                    if k
                    in {
                        "agent",
                        "runId",
                        "model",
                        "server",
                        "tool",
                        "approvalId",
                        "decision",
                        "via",
                        "next",
                        "until",
                        "host",
                        "code",
                        "toolCalls",
                    }
                },
            )
            tokens_in += int(meta.get("inputTokens") or 0)
            tokens_out += int(meta.get("outputTokens") or 0)
            tr.put(span, input=r.input, output=r.output)
            if len(tr.spans) < 400:
                tr.spans.append(span)
        status = {
            "done": "done",
            "failed": "failed",
            "cancelled": "cancelled",
            "rejected": "done",
        }.get(run.status, "running")
        terminal = run.status in TERMINAL
        tr.cost_micro_usd = run.cost_micro_usd or 0
        row = tr.finish(
            status=status if terminal else "running",
            reason=run.reason or (run.status if run.status == "rejected" else None),
            export=terminal,
        )
        row.update(
            status=run.status[:16],
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            model_calls=sum(1 for r in rows if r.kind == "agent" and r.status == "done"),
            tool_calls=sum(1 for r in rows if r.kind == "tool" and r.status in {"done", "failed"}),
            tool_errors=sum(1 for r in rows if r.kind == "tool" and r.status == "failed"),
            error=run.status == "failed" or any(r.status == "failed" for r in rows),
            duration_ms=round(root.ms),
        )
        return row
