"""Async engine and sessions.

Short transactions per repository call; no global locks, no process-wide caches of rows.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from evokai.core.config import Settings

_ASYNC_DRIVERS = {
    "postgres": "postgresql+asyncpg",
    "postgresql": "postgresql+asyncpg",
    "mysql": "mysql+aiomysql",
    "mariadb": "mysql+aiomysql",
    "sqlite": "sqlite+aiosqlite",
    "mssql": "mssql+aioodbc",
}


def async_url(url: str) -> str:
    """Pick the async driver for a URL (plain postgres://… URLs are accepted too).

    Returns:
        The URL with an async driver.
    """
    scheme, sep, rest = url.partition("://")
    if not sep or "+" in scheme:
        return url
    return f"{_ASYNC_DRIVERS.get(scheme, scheme)}://{rest}"


_TLS_PARAMS = ("sslmode", "sslrootcert", "ssl")


def postgres_tls(url: str, settings: Settings) -> tuple[str, ssl.SSLContext | bool | str | None]:
    """TLS for asyncpg from a libpq-style URL (``?sslmode=verify-full``) and the CA file setting.

    ``verify-full`` checks the chain and the host name, ``verify-ca`` the chain only, ``require``
    encrypts without checking, ``prefer``/``allow`` are passed to the driver, ``disable`` turns TLS
    off. The CA comes from ``sslrootcert`` in the
    URL or ``EVOKAI_DB_CA_FILE`` (else the system store).

    Returns:
        The URL without the libpq parameters asyncpg does not take, and the ``ssl`` argument
        (None = the driver's default).
    """
    if not url.startswith("postgresql+asyncpg"):
        return url, None
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    mode = query.get("sslmode") or ("verify-full" if settings.database_ssl else None)
    ca = query.get("sslrootcert") or settings.db_ca_file
    clean = urlunsplit(parts._replace(query=urlencode({
        k: v for k, v in query.items() if k not in _TLS_PARAMS
    })))  # fmt: skip
    if mode is None:
        return clean, None
    if mode == "disable":
        return clean, False
    if mode in {"prefer", "allow"}:
        return clean, mode
    ctx = ssl.create_default_context(cafile=ca)
    if mode == "require":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        ctx.check_hostname = False
    return clean, ctx


class Database:
    """The async engine and session factory."""

    def __init__(self, settings: Settings) -> None:
        """Create the engine.

        Args:
            settings: Deployment settings (URL, schema, TLS).
        """
        url, tls = postgres_tls(async_url(settings.database_url), settings)
        connect_args: dict = {}
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("postgresql+asyncpg"):
            if tls is not None:
                connect_args["ssl"] = tls
            # Every session speaks UTC, whatever the server's or the role's time zone: the usage
            # series groups by the text of a timestamp and reads it as UTC (costs.service.daily).
            server_settings = {"timezone": "UTC"}
            if settings.database_schema:
                server_settings["search_path"] = settings.database_schema
            connect_args["server_settings"] = server_settings
        if url.startswith("sqlite"):
            kwargs = {}
        if settings.database_schema:
            kwargs["execution_options"] = {"schema_translate_map": {None: settings.database_schema}}
        self.engine: AsyncEngine = create_async_engine(url, connect_args=connect_args, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        """A session in a transaction, committed on exit.

        Yields:
            The session.
        """
        async with self.sessions() as s, s.begin():
            yield s

    async def create_all(self) -> None:
        """For tests and SQLite dev; production uses the Alembic migrations."""
        from evokai.db.models import Base

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """Dispose of the pool."""
        await self.engine.dispose()
