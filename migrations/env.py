"""Alembic environment: the same async engine and schema mapping as the service."""

from __future__ import annotations

import asyncio

import sqlalchemy as sa
from alembic import context
from sqlalchemy.engine import Connection

from evokai.core.config import Settings, get_settings
from evokai.db.engine import Database
from evokai.db.models import Base

target_metadata = Base.metadata


def _include(obj: object, name: str | None, kind: str, reflected: bool, compare_to: object) -> bool:  # ruff: ignore[unused-function-argument]
    """Compare our tables only (the version table lives in the same schema).

    The pgvector columns and indexes of the knowledge passages exist on Postgres only (migration
    0016, ``evokai.knowledge.pgvector``); the models do not map them.
    """
    if kind == "column" and name in {"embedding_v", "tsv"}:
        return False
    if (
        kind == "index"
        and name
        and name.startswith(("ix_evokai_knowledge_chunks_hnsw_", "ix_evokai_knowledge_chunks_tsv"))
    ):
        return False
    return not (kind == "table" and name == "alembic_version")


def _run(connection: Connection, schema: str | None) -> None:
    if schema and connection.dialect.name in {"postgresql", "mssql"}:
        connection.execute(
            sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            if connection.dialect.name == "postgresql"
            else sa.text(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA [{schema}]')")
        )
        connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        include_object=_include,
    )
    with context.begin_transaction():
        context.run_migrations()


def _settings() -> Settings:
    """The settings passed by ``evokai migrate``/``db-setup``, else the environment's."""
    return context.config.attributes.get("settings") or get_settings()


async def _online() -> None:
    settings = _settings()
    db = Database(settings)
    async with db.engine.connect() as conn:
        await conn.run_sync(_run, settings.database_schema)
    await db.close()


if context.is_offline_mode():
    context.configure(
        url=_settings().database_url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_online())
