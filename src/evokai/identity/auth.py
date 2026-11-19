"""Authentication of applications (API keys) and sessions (tokens)."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC

from evokai.core.errors import Forbidden, Unauthorized
from evokai.core.security import Keyring, parse_key, secret_matches
from evokai.core.shared import MemoryState, SharedState
from evokai.core.util import TtlCache
from evokai.db.repositories import Store
from evokai.identity.models import AppPrincipal, SessionPrincipal
from evokai.privacy import revoked_at, subject_key

EPOCH_KEY = "auth:epoch"
"""A counter every instance reads: it moves when a key is revoked or a console user changes, and
each instance then drops its cache of verified keys and users."""
EPOCH_CHECK_EVERY = 1.0
"""Seconds an instance trusts its last reading of the epoch."""


class Authenticator:
    """Verifies API keys (with a short cache of verified keys) and session tokens.

    The caches are per instance; what must be the same everywhere goes through the shared state:
    request counters, a subject's sign-out (read on every session request) and the epoch that
    tells every instance to forget its verified keys.
    """

    def __init__(
        self,
        store: Store,
        keyring: Keyring,
        shared: SharedState | None = None,
        *,
        revocation_ttl: int = 1_200,
    ) -> None:
        """Create the authenticator.

        Args:
            store: Repositories.
            keyring: Signing keys.
            shared: State shared by the instances (this process's memory by default).
            revocation_ttl: How long a sign-out is kept in the shared state (the longest token
                life plus a margin).
        """
        self.store = store
        self.keyring = keyring
        self.shared: SharedState = shared or MemoryState()
        self.revocation_ttl = revocation_ttl
        self._keys: TtlCache[str, AppPrincipal] = TtlCache(max_items=1_000, ttl_seconds=30)
        self._alive: TtlCache[str, bool] = TtlCache(max_items=1_000, ttl_seconds=30)
        self._revoked: TtlCache[str, float] = TtlCache(max_items=10_000, ttl_seconds=5)
        self._epoch: str | None = None
        self._epoch_read = 0.0

    async def _sync(self) -> None:
        """Drop the caches of verified keys and users when another instance revoked something."""
        now = time.monotonic()
        if now - self._epoch_read < EPOCH_CHECK_EVERY:
            return
        self._epoch_read = now
        epoch = await self.shared.get(EPOCH_KEY)
        if epoch != self._epoch:
            self._epoch = epoch
            self._keys.clear()
            self._alive.clear()

    async def _bump(self) -> None:
        """Tell every instance to re-read keys and users."""
        await self.shared.incr(EPOCH_KEY, 86_400 * 30)
        self._epoch_read = 0.0

    async def application(self, presented: str, *, admin: bool = False) -> AppPrincipal:
        """Authenticate an application key.

        Args:
            presented: The bearer value.
            admin: Require an admin key.

        Returns:
            The application principal.

        Raises:
            Unauthorized: Unknown, revoked or malformed key.
            Forbidden: A server key used where an admin key is required.
        """
        await self._sync()
        hit = self._keys.get(presented)
        if hit is None:
            parsed = parse_key(presented)
            if not parsed:
                raise Unauthorized("Invalid key.", code="key")
            kind, prefix, secret = parsed
            row = await self.store.applications.key_by_prefix(prefix)
            if not row or row.kind != kind or not secret_matches(secret, row.secret_hash):
                raise Unauthorized("Invalid key.", code="key")
            app = await self.store.applications.get(row.application_id)
            if not app or app.status != "active":
                raise Unauthorized("Application not active.", code="application")
            hit = AppPrincipal(
                application_id=row.application_id,
                kind=row.kind,
                key_id=row.id,
                actor=f"key:{row.prefix}",
            )
            self._keys.set(presented, hit)
            await self.store.applications.touch_key(row.id)
        if admin and hit.kind != "adm":
            raise Forbidden("An admin key is required.", code="admin_key")
        return hit

    async def _revoked_at(self, application_id: str, subject: str) -> float:
        """When the subject's sessions were last revoked (cached for a few seconds).

        Returns:
            Epoch seconds, 0 when never.
        """
        key = subject_key(self.keyring.signing_key, application_id, subject)
        shared = await self.shared.get(f"auth:revoked:{key[:40]}")
        hit = self._revoked.get(key)
        if hit is None:
            at = await revoked_at(self.store, application_id, key)
            hit = (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp() if at else 0.0
            self._revoked.set(key, hit)
        return max(hit, float(shared or 0))

    async def user_active(self, user_id: str) -> bool:
        """Whether a single sign-on console user exists and is not disabled (cached briefly).

        Returns:
            True for an active user.
        """
        await self._sync()
        hit = self._alive.get(f"user:{user_id}")
        if hit is None:
            user = await self.store.console_users.get(user_id) if user_id else None
            hit = bool(user and not user.disabled)
            self._alive.set(f"user:{user_id}", hit)
        return hit

    async def forget_user(self, user_id: str) -> None:
        """Re-read a console user's state at once, on every instance (disabled or signed in)."""
        self._alive.pop(f"user:{user_id}")
        await self._bump()

    async def forget_revocation(self, application_id: str, subject: str) -> None:
        """A subject's sessions were revoked now: every instance refuses their tokens at once."""
        key = subject_key(self.keyring.signing_key, application_id, subject)
        self._revoked.pop(key)
        await self.shared.set(
            f"auth:revoked:{key[:40]}", str(time.time()), float(self.revocation_ttl)
        )

    async def count(self, key: str, limit: int, window_seconds: int) -> bool:
        """Count a request in a fixed window (every instance together); whether it is within.

        Returns:
            True while the window's count is at most ``limit``.
        """
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        bucket = f"auth:rate:{digest}:{int(time.time() // window_seconds)}"
        n = await self.shared.incr(bucket, window_seconds * 2)
        return n <= limit

    async def forget_cached_keys(self) -> None:
        """Drop the verified-key cache, on every instance (after a revocation)."""
        self._keys.clear()
        self._alive.clear()
        await self._bump()

    async def key_alive(self, key_id: str, application_id: str) -> bool:
        """Whether a key (by id) is still valid for its application (cached for a few seconds).

        Returns:
            True when the key exists, is not revoked and belongs to the application.
        """
        await self._sync()
        hit = self._alive.get(key_id)
        if hit is None:
            row = await self.store.applications.key(key_id)
            hit = bool(row and row.revoked_at is None and row.application_id == application_id)
            self._alive.set(key_id, hit)
        return hit

    async def session(self, token: str) -> SessionPrincipal:
        """Authenticate a session token.

        Args:
            token: The bearer JWT.

        Returns:
            The session principal (with the application's allowed origins).

        Raises:
            Unauthorized: Invalid token, inactive application, or the subject's sessions were
                revoked after the token was minted (an erase, a logout everywhere).
        """
        claims = self.keyring.verify(token)
        app = await self.store.applications.get(claims.application_id)
        if not app or app.status != "active":
            raise Unauthorized("Application not active.", code="application")
        if claims.issued_at <= await self._revoked_at(claims.application_id, claims.subject):
            raise Unauthorized("Session revoked.", code="token")
        return SessionPrincipal(claims=claims, origins=list(app.allowed_origins or []))


def bearer(authorization: str | None) -> str | None:
    """Extract a bearer value.

    Args:
        authorization: The Authorization header.

    Returns:
        The token, or None.
    """
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return None
