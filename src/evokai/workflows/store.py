"""Where workflows live: versioned definitions, runs, step states and approvals."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database

TERMINAL = frozenset({"done", "failed", "rejected", "cancelled"})
"""Run statuses that never change again (but a retry)."""
OPEN_STEPS = ("pending", "waiting", "running")


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class WorkflowRepository:
    """Queries of workflows. Every query is scoped by application, but the workers' own."""

    def __init__(self, db: Database) -> None:
        """Bind to a database.

        Args:
            db: The database.
        """
        self.db = db

    # definitions

    async def put(
        self,
        application_id: str,
        name: str,
        version: str,
        definition: dict[str, Any],
        created_by: str | None = None,
    ) -> bool:
        """Store a definition version and make it the active one (no-op if unchanged).

        Returns:
            Whether a new version was stored.
        """
        w = m.WorkflowRecord
        async with self.db.session() as s:
            same = (w.application_id == application_id, w.name == name)
            exists = await s.execute(sa.select(w.id).where(*same, w.version == version))
            if exists.scalar_one_or_none():
                await s.execute(sa.update(w).where(*same).values(active=w.version == version))
                return False
            await s.execute(sa.update(w).where(*same).values(active=False))
            s.add(
                w(
                    id=new_id(),
                    application_id=application_id,
                    name=name,
                    version=version,
                    definition=definition,
                    created_by=created_by,
                    created_at=utcnow(),
                )
            )
            return True

    async def active(self, application_id: str, name: str) -> m.WorkflowRecord | None:
        """The active version of a workflow.

        Returns:
            The record or None.
        """
        w = m.WorkflowRecord
        async with self.db.session() as s:
            q = sa.select(w).where(w.application_id == application_id, w.name == name, w.active)
            return (await s.execute(q)).scalar_one_or_none()

    async def version(
        self, application_id: str, name: str, version: str
    ) -> m.WorkflowRecord | None:
        """A version of a workflow.

        Returns:
            The record or None.
        """
        w = m.WorkflowRecord
        async with self.db.session() as s:
            q = sa.select(w).where(
                w.application_id == application_id, w.name == name, w.version == version
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def active_all(self, application_id: str | None = None) -> list[m.WorkflowRecord]:
        """The active workflows of an application (of every one, for the scheduler).

        Returns:
            Records, by name.
        """
        w = m.WorkflowRecord
        async with self.db.session() as s:
            q = sa.select(w).where(w.active)
            if application_id is not None:
                q = q.where(w.application_id == application_id)
            return list((await s.execute(q.order_by(w.name))).scalars().all())

    async def versions(self, application_id: str, name: str) -> list[m.WorkflowRecord]:
        """Every version of a workflow, newest first.

        Returns:
            Records.
        """
        w = m.WorkflowRecord
        async with self.db.session() as s:
            q = (
                sa
                .select(w)
                .where(w.application_id == application_id, w.name == name)
                .order_by(w.created_at.desc())
            )
            return list((await s.execute(q)).scalars().all())

    async def deactivate(self, application_id: str, name: str) -> bool:
        """Retire a workflow (its versions stay for the runs that use them).

        Returns:
            Whether an active workflow was retired.
        """
        w = m.WorkflowRecord
        async with self.db.session() as s:
            q = sa.update(w).where(w.application_id == application_id, w.name == name, w.active)
            return _rowcount(await s.execute(q.values(active=False))) > 0

    # runs

    async def run(self, application_id: str, run_id: str) -> m.WorkflowRun | None:
        """A run of the application.

        Returns:
            The run or None.
        """
        r = m.WorkflowRun
        async with self.db.session() as s:
            q = sa.select(r).where(r.id == run_id, r.application_id == application_id)
            return (await s.execute(q)).scalar_one_or_none()

    async def runs(
        self,
        application_id: str,
        *,
        workflow: str | None = None,
        status: str | None = None,
        subject: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> list[m.WorkflowRun]:
        """Runs, newest first.

        Returns:
            Runs.
        """
        r = m.WorkflowRun
        q = sa.select(r).where(r.application_id == application_id)
        if workflow:
            q = q.where(r.workflow_name == workflow)
        if status == "active":
            q = q.where(r.status.in_(("running", "waiting")))
        elif status:
            q = q.where(r.status == status)
        if subject:
            q = q.where(r.subject == subject)
        if before is not None:
            q = q.where(r.started_at < before)
        async with self.db.session() as s:
            res = await s.execute(q.order_by(r.started_at.desc()).limit(limit))
            return list(res.scalars().all())

    async def counts(self, application_id: str, since: datetime) -> dict[str, dict[str, int]]:
        """Runs per workflow and status since a time.

        Returns:
            Workflow → status → count.
        """
        r = m.WorkflowRun
        q = (
            sa
            .select(r.workflow_name, r.status, sa.func.count())
            .where(r.application_id == application_id, r.started_at >= since)
            .group_by(r.workflow_name, r.status)
        )
        out: dict[str, dict[str, int]] = {}
        async with self.db.session() as s:
            for name, status, n in (await s.execute(q)).all():
                out.setdefault(name, {})[status] = int(n)
        return out

    async def last_runs(self, application_id: str) -> dict[str, datetime]:
        """When each workflow last started a run.

        Returns:
            Workflow → time.
        """
        r = m.WorkflowRun
        q = (
            sa
            .select(r.workflow_name, sa.func.max(r.started_at))
            .where(r.application_id == application_id)
            .group_by(r.workflow_name)
        )
        async with self.db.session() as s:
            return {name: at for name, at in (await s.execute(q)).all() if at is not None}

    async def steps(self, run_id: str) -> list[m.WorkflowStep]:
        """The step states of a run, in the order they were created.

        Returns:
            Steps.
        """
        st = m.WorkflowStep
        async with self.db.session() as s:
            q = sa.select(st).where(st.run_id == run_id).order_by(st.created_at, st.step_id)
            return list((await s.execute(q)).scalars().all())

    # approvals

    async def approvals(
        self,
        application_id: str,
        *,
        status: str | None = "pending",
        workflow: str | None = None,
        subject: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> list[m.WorkflowApproval]:
        """Approvals, newest first.

        Returns:
            Approvals.
        """
        a = m.WorkflowApproval
        q = sa.select(a).where(a.application_id == application_id)
        if status:
            q = q.where(a.status == status)
        if workflow:
            q = q.where(a.workflow_name == workflow)
        if subject:
            q = q.where(a.subject == subject)
        if run_id:
            q = q.where(a.run_id == run_id)
        async with self.db.session() as s:
            res = await s.execute(q.order_by(a.created_at.desc()).limit(limit))
            return list(res.scalars().all())

    async def approval(self, application_id: str, approval_id: str) -> m.WorkflowApproval | None:
        """One approval of the application.

        Returns:
            The approval or None.
        """
        a = m.WorkflowApproval
        async with self.db.session() as s:
            q = sa.select(a).where(a.id == approval_id, a.application_id == application_id)
            return (await s.execute(q)).scalar_one_or_none()

    async def pending_count(self, application_id: str) -> int:
        """Approvals waiting for a decision.

        Returns:
            The count.
        """
        a = m.WorkflowApproval
        async with self.db.session() as s:
            q = sa.select(sa.func.count()).where(
                a.application_id == application_id, a.status == "pending"
            )
            return int((await s.execute(q)).scalar_one())

    # privacy and retention

    async def for_subject(self, application_id: str, subject: str) -> list[m.WorkflowRun]:
        """Every run of a subject (their data export).

        Returns:
            Runs, oldest first.
        """
        r = m.WorkflowRun
        async with self.db.session() as s:
            q = (
                sa
                .select(r)
                .where(r.application_id == application_id, r.subject == subject)
                .order_by(r.started_at)
            )
            return list((await s.execute(q)).scalars().all())

    async def forget_subject(self, s: Any, application_id: str, subject: str) -> int:
        """Delete the runs of a subject, their steps, approvals and traces (in a session).

        Returns:
            How many runs were deleted.
        """
        r = m.WorkflowRun
        ids = list(
            (
                await s.execute(
                    sa.select(r.id).where(r.application_id == application_id, r.subject == subject)
                )
            )
            .scalars()
            .all()
        )
        await _delete_runs(s, ids)
        return len(ids)

    async def purge(self, now: datetime | None = None) -> int:
        """Delete finished runs past their workflow's retention.

        Returns:
            How many runs were deleted.
        """
        now = now or utcnow()
        r = m.WorkflowRun
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    sa.select(r.id, r.started_at, r.retention_days).where(r.status.in_(TERMINAL))
                )
            ).all()
            ids = [
                i
                for i, started, days in rows
                if _aware(started) <= now - timedelta(days=int(days or 90))
            ]
            for chunk in range(0, len(ids), 500):
                await _delete_runs(s, ids[chunk : chunk + 500])
            return len(ids)


async def _delete_runs(s: Any, ids: list[str]) -> None:
    if not ids:
        return
    await s.execute(sa.delete(m.WorkflowStep).where(m.WorkflowStep.run_id.in_(ids)))
    await s.execute(sa.delete(m.WorkflowApproval).where(m.WorkflowApproval.run_id.in_(ids)))
    await s.execute(sa.delete(m.Trace).where(m.Trace.id.in_(ids)))
    await s.execute(sa.delete(m.WorkflowRun).where(m.WorkflowRun.id.in_(ids)))


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)
