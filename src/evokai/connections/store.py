"""The token vault: one connection per application, subject and provider.

Tokens are stored encrypted (``Keyring.encrypt``, the deployment's Fernet key); this repository
only moves the encrypted values, ``evokai.connections.service`` encrypts and decrypts them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database


def _rowcount(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


class ConnectionRepository:
    """Queries of OAuth connections, always scoped by application."""

    def __init__(self, db: Database) -> None:
        """Bind to a database.

        Args:
            db: The database.
        """
        self.db = db

    async def get(
        self, application_id: str, subject: str, provider: str
    ) -> m.OAuthConnection | None:
        """A subject's connection to a provider.

        Returns:
            The connection, or None.
        """
        async with self.db.session() as s:
            q = sa.select(m.OAuthConnection).where(
                m.OAuthConnection.application_id == application_id,
                m.OAuthConnection.subject == subject,
                m.OAuthConnection.provider == provider,
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def of_subject(self, application_id: str, subject: str) -> list[m.OAuthConnection]:
        """Every connection of a subject.

        Returns:
            Connections by provider.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.OAuthConnection)
                .where(
                    m.OAuthConnection.application_id == application_id,
                    m.OAuthConnection.subject == subject,
                )
                .order_by(m.OAuthConnection.provider)
            )
            return list((await s.execute(q)).scalars().all())

    async def put(
        self,
        application_id: str,
        subject: str,
        provider: str,
        *,
        account: str | None,
        scopes: list[str],
        access: str,
        refresh: str | None,
        expires_at: datetime | None,
    ) -> None:
        """Create or replace a connection (encrypted tokens)."""
        now = utcnow()
        async with self.db.session() as s:
            q = sa.select(m.OAuthConnection).where(
                m.OAuthConnection.application_id == application_id,
                m.OAuthConnection.subject == subject,
                m.OAuthConnection.provider == provider,
            )
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                row = m.OAuthConnection(
                    id=new_id(),
                    application_id=application_id,
                    subject=subject,
                    provider=provider,
                    created_at=now,
                )
                s.add(row)
            row.account = (account or "")[:320] or None
            row.scopes = scopes
            row.access_token_encrypted = access
            row.refresh_token_encrypted = refresh
            row.expires_at = expires_at
            row.status = "active"
            row.updated_at = now

    async def refreshed(
        self,
        connection_id: str,
        *,
        access: str,
        refresh: str | None,
        expires_at: datetime | None,
        scopes: list[str] | None,
    ) -> None:
        """Keep the tokens of a refresh (a provider may rotate the refresh token)."""
        values: dict[str, Any] = {
            "access_token_encrypted": access,
            "expires_at": expires_at,
            "status": "active",
            "updated_at": utcnow(),
            "last_used_at": utcnow(),
        }
        if refresh:
            values["refresh_token_encrypted"] = refresh
        if scopes:
            values["scopes"] = scopes
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.OAuthConnection)
                .where(m.OAuthConnection.id == connection_id)
                .values(**values)
            )

    async def mark(self, connection_id: str, status: str) -> None:
        """Set a connection's status (``expired`` when the provider refused a refresh)."""
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.OAuthConnection)
                .where(m.OAuthConnection.id == connection_id)
                .values(status=status, updated_at=utcnow())
            )

    async def used(self, connection_id: str) -> None:
        """Write down that a connection was used."""
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.OAuthConnection)
                .where(m.OAuthConnection.id == connection_id)
                .values(last_used_at=utcnow())
            )

    async def delete(self, application_id: str, subject: str, provider: str | None = None) -> int:
        """Delete a subject's connection to a provider (or all of them).

        Returns:
            How many were deleted.
        """
        q = sa.delete(m.OAuthConnection).where(
            m.OAuthConnection.application_id == application_id,
            m.OAuthConnection.subject == subject,
        )
        if provider is not None:
            q = q.where(m.OAuthConnection.provider == provider)
        async with self.db.session() as s:
            return _rowcount(await s.execute(q))

    async def all_of_provider(self, application_id: str, provider: str) -> list[m.OAuthConnection]:
        """Every connection of an application to a provider (to revoke them all).

        Returns:
            Connections.
        """
        async with self.db.session() as s:
            q = sa.select(m.OAuthConnection).where(
                m.OAuthConnection.application_id == application_id,
                m.OAuthConnection.provider == provider,
            )
            return list((await s.execute(q)).scalars().all())

    async def delete_provider(self, application_id: str, provider: str) -> int:
        """Delete every connection of an application to a provider.

        Returns:
            How many were deleted.
        """
        async with self.db.session() as s:
            r = await s.execute(
                sa.delete(m.OAuthConnection).where(
                    m.OAuthConnection.application_id == application_id,
                    m.OAuthConnection.provider == provider,
                )
            )
            return _rowcount(r)

    # the application's OAuth apps (providers no connector configures)

    async def apps(self, application_id: str) -> list[m.OAuthApp]:
        """The application's OAuth apps.

        Returns:
            Apps by provider.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.OAuthApp)
                .where(m.OAuthApp.application_id == application_id)
                .order_by(m.OAuthApp.provider)
            )
            return list((await s.execute(q)).scalars().all())

    async def put_app(
        self,
        application_id: str,
        provider: str,
        *,
        client_id: str,
        secret: str | None,
        writes: bool,
    ) -> m.OAuthApp:
        """Create or change an OAuth app (``secret`` None keeps the stored one).

        Returns:
            The app.

        Raises:
            ValueError: A new app without a secret.
        """
        async with self.db.session() as s:
            q = sa.select(m.OAuthApp).where(
                m.OAuthApp.application_id == application_id, m.OAuthApp.provider == provider
            )
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                if not secret:
                    raise ValueError("a new OAuth app needs its client secret")
                row = m.OAuthApp(id=new_id(), application_id=application_id, provider=provider)
                s.add(row)
            row.client_id = client_id
            if secret:
                row.client_secret_encrypted = secret
            row.writes = writes
            row.updated_at = utcnow()
            return row

    async def delete_app(self, application_id: str, provider: str) -> int:
        """Delete an OAuth app.

        Returns:
            How many were deleted.
        """
        async with self.db.session() as s:
            r = await s.execute(
                sa.delete(m.OAuthApp).where(
                    m.OAuthApp.application_id == application_id, m.OAuthApp.provider == provider
                )
            )
            return _rowcount(r)

    async def counts(self, application_id: str) -> dict[str, dict[str, int]]:
        """Connected people per provider, by status.

        Returns:
            provider → {status: count}.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.OAuthConnection.provider, m.OAuthConnection.status, sa.func.count())
                .where(m.OAuthConnection.application_id == application_id)
                .group_by(m.OAuthConnection.provider, m.OAuthConnection.status)
            )
            out: dict[str, dict[str, int]] = {}
            for provider, status, n in (await s.execute(q)).all():
                out.setdefault(str(provider), {})[str(status)] = int(n)
            return out
