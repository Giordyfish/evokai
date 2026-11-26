"""SQLite as a deployment's database: a read never waits for a write (write-ahead log)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sqlalchemy as sa

from evokai.core.config import Settings
from evokai.db.engine import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'wal.db'}"))


async def test_sqlite_runs_with_a_write_ahead_log(db: Database) -> None:
    async with db.engine.connect() as c:
        mode = (await c.execute(sa.text("PRAGMA journal_mode"))).scalar()
        busy = (await c.execute(sa.text("PRAGMA busy_timeout"))).scalar()
    await db.engine.dispose()
    assert mode == "wal"
    assert busy == 30_000


async def test_a_write_waits_for_a_long_one_instead_of_failing(db: Database) -> None:
    # SQLite's default wait is 5 s: a write behind one that holds the lock longer (slow disks,
    # many concurrent turns) failed with "database is locked". It now waits up to 30 s.
    async with db.engine.begin() as c:
        await c.execute(sa.text("CREATE TABLE t (x INTEGER)"))
    holder = await db.engine.connect()
    await holder.exec_driver_sql("BEGIN IMMEDIATE")
    await holder.exec_driver_sql("INSERT INTO t VALUES (1)")

    async def second() -> None:
        async with db.engine.begin() as w:
            await w.execute(sa.text("INSERT INTO t VALUES (2)"))

    task = asyncio.create_task(second())
    await asyncio.sleep(5.5)
    assert not task.done()
    await holder.exec_driver_sql("COMMIT")
    await holder.close()
    await asyncio.wait_for(task, timeout=5)
    async with db.engine.connect() as c:
        assert (await c.execute(sa.text("SELECT count(*) FROM t"))).scalar() == 2
    await db.engine.dispose()
