"""Postgres provisioning for ``evokai db-setup``: a runtime role with CRUD only, in its own schema.

The owner (the URL's user) owns the schema and the tables; the runtime role can read and write rows
but cannot change the schema. Every statement is idempotent.
"""

from __future__ import annotations

import logging
import re

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from evokai.core.config import Settings
from evokai.core.errors import ConfigError
from evokai.db.engine import Database
from evokai.knowledge.store import pgvector_schema

log = logging.getLogger("evokai.db")

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ConfigError(f"db-setup: {name!r} is not a plain lower-case identifier")
    return f'"{name}"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _owner(settings: Settings) -> Database:
    return Database(settings.model_copy(update={"database_schema": None}))


async def prepare(settings: Settings, *, role: bool = True) -> None:
    """Create or update the runtime role and create the schema.

    Args:
        settings: Owner URL, schema, runtime role and its password.
        role: Also create or update the runtime role (``False``: only the schema, for an install
            where the owner runs the service).

    Raises:
        ConfigError: The role does not exist and no password was given.
    """
    schema = _ident(settings.database_schema or "evokai")
    db = _owner(settings)
    try:
        async with db.engine.begin() as c:
            if not role:
                await c.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
                return
            q = sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r")
            exists = (await c.execute(q, {"r": settings.app_db_role})).first() is not None
            password = settings.app_db_password
            if not exists and not password:
                raise ConfigError("db-setup: EVOKAI_APP_DB_PASSWORD is required to create the role")
            if password:
                verb = "ALTER" if exists else "CREATE"
                name = _ident(settings.app_db_role)
                await c.execute(sa.text(f"{verb} ROLE {name} LOGIN PASSWORD {_literal(password)}"))
            await c.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await vector_extension(db)
    finally:
        await db.close()


async def vector_extension(db: Database) -> bool:
    """Install pgvector for the knowledge bases' search, when the database offers it.

    It runs as the owner (on Amazon RDS the owner has ``rds_superuser``). Where the extension is
    not available the knowledge bases use the portable search.

    Returns:
        Whether the extension is installed.
    """
    try:
        async with db.engine.begin() as c:
            await c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        return True
    except DBAPIError as e:
        log.warning("pgvector_unavailable", extra={"error_type": type(e).__name__})
        return False


async def grant(settings: Settings) -> None:
    """Grant the runtime role USAGE on the schema and CRUD on its tables (now and future)."""
    role, schema = _ident(settings.app_db_role), _ident(settings.database_schema or "evokai")
    db = _owner(settings)
    try:
        async with db.engine.begin() as c:
            for statement in (
                f"GRANT USAGE ON SCHEMA {schema} TO {role}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {schema} TO {role}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {role}",
            ):
                await c.execute(sa.text(statement))
            # The runtime role uses pgvector's type and operators, wherever the extension lives.
            ns = await pgvector_schema(c)
            if ns and ns != (settings.database_schema or "evokai"):
                await c.execute(sa.text(f"GRANT USAGE ON SCHEMA {_quoted(ns)} TO {role}"))
    finally:
        await db.close()


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
