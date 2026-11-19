"""pgtask: the task queue of durable agent steps (Postgres only)

pgtask keeps its tables in its own schema (``pgtask``) and tracks its own migrations
(``_sqlx_migrations``): this migration runs pgtask's migrator (the version pinned in uv.lock) so
both stay in step, instead of copying its SQL. On SQLite nothing is created: durable agent steps
need Postgres.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17 22:00:00
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def _dsn() -> str | None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return None
    url = bind.engine.url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def _in_thread(fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(fn())
        except BaseException as e:  # re-raised below, in the caller's thread
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    if errors:
        raise errors[0]


def upgrade() -> None:
    url = _dsn()
    if url is None:
        return
    try:
        from pgtask import Client
    except ImportError:
        # An image without the harness extra: durable agent steps are not available there.
        return

    async def migrate() -> None:
        client = await Client.connect(url)
        await client.migrate()

    _in_thread(migrate)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(sa.text("DROP SCHEMA IF EXISTS pgtask CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS public._sqlx_migrations"))
