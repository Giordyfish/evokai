"""Who an app session belongs to, learned passively from answers that pass anyway.

The proxy never calls a provider with a person's credentials. It reads a key from each request
and binds it to the account a provider's own answer named:

- claude.ai (Chat, Cowork, titles): the ``sessionKey`` cookie (read from every ``cookie``
  header: HTTP/2 splits them), learned from ``GET claude.ai/api/account``;
- Claude Code (Desktop's Code tab, the CLI): the account uuid in ``metadata.user_id`` of every
  ``/v1/messages`` body.

The person is bound to the **Anthropic account uuid**, learned from any answer that names it with
its e-mail: ``GET claude.ai/api/account`` (Desktop calls it at start), ``GET
api.anthropic.com/api/oauth/profile`` and Claude Code's OAuth token answer (``POST
platform.claude.com/v1/oauth/token``, read for its ``account`` and ``organization`` only). It is
one value space: claude.ai's account ``uuid``, the profile's ``account.uuid`` and the token
answer's ``account.uuid`` are what Claude Code stores as ``oauthAccount.accountUuid`` and sends
as ``account_uuid`` in ``metadata.user_id`` (read in the Claude Code 2.1.332 bundle; the probe saw
the same uuid in both). A person seen once through Desktop is so recognised on the CLI too, and a
signed-in CLI (which does not call the profile again) is recognised at its next token refresh.

Only a digest of the key is stored (``evokai_gateway_proxy_identities``), with the e-mail, the
account and the organisation, and an expiry that moves with use. The e-mail maps to a gateway
person (``email`` of their key, else a label that is that e-mail).

The organisation of a claude.ai request is its ``x-organization-uuid`` header; of an Anthropic API
request, the one its account's profile named.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa

from evokai.core.util import TtlCache, utcnow
from evokai.db import models as m

log = logging.getLogger("evokai.gateway.proxy")

BINDING_DAYS = 30
"""A binding expires this many days after it was last used."""
TOUCH_SECONDS = 600
"""A binding's ``last_seen`` is written at most this often."""
RECENT_SECONDS = 900
"""How long an identity seen from a PC's address stands for it where no key is carried (the
Cowork bridge, the check page)."""
MISS_SECONDS = 5
"""How long an unknown key is not looked up again."""

SESSION_COOKIES = ("sessionKey", "sessionKeyV3")
"""claude.ai's session cookies, in the order the key is taken from."""
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ACCOUNT_IN_ID = re.compile(r"_account_(" + _UUID.pattern + r")")


@dataclass(frozen=True)
class Key:
    """What a request carries that names its session, and the organisation it states."""

    provider: str
    kind: str
    """``cookie`` (claude.ai) or ``account`` (an account uuid)."""
    value: str
    org: str | None = None

    @property
    def digest(self) -> str:
        """The digest stored instead of the value.

        Returns:
            Hex.
        """
        raw = f"evokai-proxy\x00{self.provider}\x00{self.kind}\x00{self.value}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Binding:
    """A key's account, as a provider's answer named it."""

    key_hash: str
    provider: str
    email: str | None
    account_id: str | None
    org_id: str | None


# Reading keys from requests


def cookies(values: list[str]) -> dict[str, str]:
    """Cookies from every ``cookie`` header (HTTP/2 sends one header per cookie, or several).

    Returns:
        Name to value (the first of a repeated name).
    """
    out: dict[str, str] = {}
    for header in values:
        for part in header.split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name and name not in out:
                out[name] = value.strip()
    return out


def claude_ai_key(cookie_headers: list[str], org: str | None) -> Key | None:
    """The key of a claude.ai request: its session cookie.

    Returns:
        The key, or None when it carries no session.
    """
    jar = cookies(cookie_headers)
    for name in SESSION_COOKIES:
        if value := jar.get(name):
            return Key("anthropic", "cookie", value, (org or "").strip() or None)
    return None


def account_from_user_id(body: Any) -> str | None:
    """The account uuid in a Messages request's ``metadata.user_id``.

    Claude Code sends a JSON string (``{"device_id": ..., "account_uuid": ..., "session_id":
    ...}``); older versions sent ``user_<hash>_account_<uuid>_session_<uuid>``.

    Returns:
        The uuid (lower case), or None.
    """
    meta = body.get("metadata") if isinstance(body, dict) else None
    raw = meta.get("user_id") if isinstance(meta, dict) else None
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        value = parsed.get("account_uuid") or parsed.get("accountUuid")
        if isinstance(value, str) and _UUID.fullmatch(value):
            return value.lower()
        return None
    if found := _ACCOUNT_IN_ID.search(raw):
        return found.group(1).lower()
    return None


# Reading accounts from answers


def _str(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def from_claude_account(data: Any) -> tuple[str | None, str | None, str | None]:
    """The e-mail, account uuid and organisation of ``GET claude.ai/api/account``.

    Returns:
        (email, account, org); the org only when the account has exactly one.
    """
    if not isinstance(data, dict):
        return None, None, None
    acct = data.get("account") if isinstance(data.get("account"), dict) else data
    email = _str(acct.get("email_address")) or _str(acct.get("email"))
    uuid = _str(acct.get("uuid"))
    orgs = [
        _str(mm.get("organization", {}).get("uuid"))
        for mm in acct.get("memberships") or []
        if isinstance(mm, dict) and isinstance(mm.get("organization"), dict)
    ]
    orgs = [o for o in orgs if o]
    uuid = uuid.lower() if uuid else None
    return (email.lower() if email else None), uuid, (orgs[0] if len(orgs) == 1 else None)


def from_oauth_profile(data: Any) -> tuple[str | None, str | None, str | None]:
    """The e-mail, account uuid and organisation of ``GET api.anthropic.com/api/oauth/profile``.

    Returns:
        (email, account, org).
    """
    if not isinstance(data, dict):
        return None, None, None
    acct = data.get("account") if isinstance(data.get("account"), dict) else {}
    org = data.get("organization") if isinstance(data.get("organization"), dict) else {}
    email = _str(acct.get("email")) or _str(acct.get("email_address"))
    uuid = _str(acct.get("uuid"))
    return (
        (email.lower() if email else None),
        (uuid.lower() if uuid else None),
        _str(org.get("uuid")),
    )


def from_token_response(data: Any) -> tuple[str | None, str | None, str | None]:
    """The e-mail, account uuid and organisation of Claude Code's OAuth token answer.

    Only ``account`` and ``organization`` are read; the tokens beside them are never touched.

    Returns:
        (email, account, org).
    """
    if not isinstance(data, dict):
        return None, None, None
    acct = data.get("account") if isinstance(data.get("account"), dict) else {}
    org = data.get("organization") if isinstance(data.get("organization"), dict) else {}
    email = _str(acct.get("email_address")) or _str(acct.get("email"))
    uuid = _str(acct.get("uuid"))
    return (
        (email.lower() if email else None),
        (uuid.lower() if uuid else None),
        _str(org.get("uuid")),
    )


def mask_email(email: str | None) -> str:
    """An e-mail as the check page shows it (``a***@example.com``).

    Returns:
        The masked e-mail, or an empty string.
    """
    if not email or "@" not in email:
        return ""
    user, _, domain = email.partition("@")
    return f"{user[:1]}***@{domain}"


# The bindings


class IdentityBook:
    """Bindings in the database, with short caches in front (every proxy instance shares them)."""

    def __init__(self, db: Any) -> None:
        """Hold the database."""
        self.db = db
        self._hits: TtlCache[str, Binding] = TtlCache(max_items=50_000, ttl_seconds=300)
        self._misses: TtlCache[str, bool] = TtlCache(max_items=50_000, ttl_seconds=MISS_SECONDS)
        self._touched: TtlCache[str, bool] = TtlCache(max_items=50_000, ttl_seconds=TOUCH_SECONDS)
        self._recent: TtlCache[str, list[tuple[float, Binding]]] = TtlCache(
            max_items=20_000, ttl_seconds=RECENT_SECONDS
        )

    async def bind(
        self, key: Key, email: str | None, account_id: str | None, org_id: str | None
    ) -> Binding:
        """Store what an answer said about a key's account.

        Returns:
            The binding.
        """
        now = utcnow()
        digest = key.digest
        b = Binding(digest, key.provider, email, account_id, org_id)
        async with self.db.session() as s:
            row = await s.get(m.ProxyIdentity, digest)
            if row is None:
                s.add(
                    m.ProxyIdentity(
                        key_hash=digest,
                        provider=key.provider,
                        email=email,
                        account_id=account_id,
                        org_id=org_id,
                        first_seen=now,
                        last_seen=now,
                        expires_at=now + timedelta(days=BINDING_DAYS),
                    )
                )
            else:
                row.email = email or row.email
                row.account_id = account_id or row.account_id
                row.org_id = org_id or row.org_id
                row.last_seen = now
                row.expires_at = now + timedelta(days=BINDING_DAYS)
                b = Binding(digest, key.provider, row.email, row.account_id, row.org_id)
        self._hits.set(digest, b)
        self._misses.pop(digest)
        self._touched.set(digest, True)
        return b

    async def lookup(self, key: Key) -> Binding | None:
        """A key's binding, if one was learned and has not expired.

        Returns:
            The binding, or None.
        """
        digest = key.digest
        if hit := self._hits.get(digest):
            await self._touch(digest)
            return hit
        if self._misses.get(digest):
            return None
        async with self.db.session() as s:
            row = await s.get(m.ProxyIdentity, digest)
            found = None
            if row is not None and _aware(row.expires_at) > utcnow():
                found = Binding(digest, row.provider, row.email, row.account_id, row.org_id)
        if found is None:
            self._misses.set(digest, True)
            return None
        self._hits.set(digest, found)
        await self._touch(digest)
        return found

    async def _touch(self, digest: str) -> None:
        if self._touched.get(digest):
            return
        self._touched.set(digest, True)
        now = utcnow()
        try:
            async with self.db.session() as s:
                await s.execute(
                    sa
                    .update(m.ProxyIdentity)
                    .where(m.ProxyIdentity.key_hash == digest)
                    .values(last_seen=now, expires_at=now + timedelta(days=BINDING_DAYS))
                )
        except Exception as e:
            log.warning("proxy_identity_not_touched", extra={"error_type": type(e).__name__})

    def seen(self, address: str, b: Binding, now: float) -> None:
        """Note an identity seen from a PC's address."""
        if not address:
            return
        items = [(t, x) for t, x in self._recent.get(address) or [] if x.key_hash != b.key_hash]
        items = [(t, x) for t, x in items if now - t < RECENT_SECONDS][-7:]
        self._recent.set(address, [*items, (now, b)])

    def recent(self, address: str, now: float) -> list[Binding]:
        """The identities seen from a PC's address lately, newest first.

        Returns:
            The bindings.
        """
        items = self._recent.get(address) or []
        return [b for t, b in reversed(items) if now - t < RECENT_SECONDS]

    async def purge(self, now: datetime | None = None) -> int:
        """Delete expired bindings.

        Returns:
            How many.
        """
        async with self.db.session() as s:
            r = await s.execute(
                sa.delete(m.ProxyIdentity).where(m.ProxyIdentity.expires_at < (now or utcnow()))
            )
            return int(getattr(r, "rowcount", 0) or 0)


def _aware(t: datetime) -> datetime:
    from datetime import UTC

    return t if t.tzinfo else t.replace(tzinfo=UTC)
