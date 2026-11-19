"""Durable agent steps on pgtask: checkpointed model calls in Evokai's Postgres (Postgres only)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from evokai.db import models as m
from evokai.workflows.durable import DurableAgents, dsn
from tests.conftest import Env
from tests.test_workflows import WRITER, _put, _run, _setup, _start, _steps

PG = os.environ.get("EVOKAI_TEST_DATABASE_URL", "")

DURABLE = {
    "name": "durable-reply",
    "steps": [
        {
            "id": "draft",
            "kind": "agent",
            "agent": "writer",
            "input": "Reply to: {{ input.text }}",
            "durable": True,
        }
    ],
}


def test_the_dsn_is_evokai_s_own_postgres() -> None:
    assert dsn("postgresql+asyncpg://u:p@db:5432/evokai") == "postgresql://u:p@db:5432/evokai"
    assert dsn("sqlite+aiosqlite:///x.db") is None


async def test_without_postgres_a_durable_step_fails_clearly(env: Env) -> None:
    if PG:
        pytest.skip("SQLite only")
    await _setup(env, WRITER)
    await _put(env, DURABLE)
    started = await _start(env, "durable-reply", input={"text": "Order arrived twice"})
    await env.svc.workflows.drain()
    run = await _run(env, started["id"])
    assert run["status"] == "failed"
    assert "need Postgres" in str(_steps(run)["draft"])


@pytest.mark.skipif(not PG.startswith("postgresql"), reason="durable agent steps need Postgres")
async def test_a_durable_step_runs_as_a_pgtask_task_with_its_calls_checkpointed(env: Env) -> None:
    from pgtask import Client

    env.svc.settings.workflow_durable_agents = True
    durable = DurableAgents(env.svc)
    assert durable.enabled
    client = await Client.connect(durable.url or "")
    await client.migrate()
    env.svc.durable = durable
    await durable.start()
    try:
        await _setup(env, WRITER)
        await _put(env, DURABLE)
        env.script.add("function:writer", ["Dear customer, fixed."])
        started = await _start(env, "durable-reply", input={"text": "Order arrived twice"})
        await env.svc.workflows.drain()
        run = await _run(env, started["id"])
        assert run["status"] == "done", run
        draft = _steps(run)["draft"]
        assert draft["output"] == "Dear customer, fixed."
        async with env.svc.db.engine.connect() as c:
            names = (
                (
                    await c.execute(
                        sa.text(
                            "select step_name from pgtask.checkpoints c join pgtask.tasks t "
                            "on t.id = c.task_id where t.idempotency_key like :k"
                        ),
                        {"k": "%draft%"},
                    )
                )
                .scalars()
                .all()
            )
        assert "evokai__model.request" in names
    finally:
        await durable.close()


async def test_a_resumed_durable_step_records_its_usage_once(env: Env) -> None:
    await _setup(env, WRITER)
    durable = DurableAgents(env.svc)
    payload = {
        "applicationId": env.app_id,
        "agent": "writer",
        "text": "Reply to: order",
        "subject": "cust-7",
        "tags": [],
    }
    task = SimpleNamespace(id="task-1")
    env.script.add("function:writer", ["Dear customer."], ["Dear customer."])
    first = await durable._handle(task, payload)
    # The task runs again after a crash: the same calls in the same order.
    again = await durable._handle(task, payload)
    assert first["output"] == again["output"] == "Dear customer."
    async with env.svc.store.db.session() as s:
        rows = (
            (await s.execute(sa.select(m.UsageRecord).where(m.UsageRecord.agent_name == "writer")))
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].dedup_key == "pgtask:task-1:model:1"
    # Another task is another call.
    await durable._handle(SimpleNamespace(id="task-2"), payload)
    async with env.svc.store.db.session() as s:
        n = (
            await s.execute(
                sa
                .select(sa.func.count())
                .select_from(m.UsageRecord)
                .where(m.UsageRecord.agent_name == "writer")
            )
        ).scalar_one()
    assert n == 2
