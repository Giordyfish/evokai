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

OpenAI (Codex, ChatGPT): every request carries the ChatGPT login's JWT, whose claims name the user,
their e-mail and the ChatGPT account (workspace) they act in. The claims are read, never verified
and never trusted for anything but attribution (a forged token is refused upstream anyway): the
key is the user, the organisation the ``chatgpt_account_id``, and the binding is written from the
claims the first time a key is seen (or when they change).

GitHub Copilot (best effort, not measured): chat and completion requests carry a short-lived
Copilot token (``tid=...;exp=...;...``) minted by ``GET api.github.com/copilot_internal/v2/token``
from the person's GitHub token. The GitHub token is bound to the account by ``GET
api.github.com/user`` or ``/copilot_internal/user`` (login, e-mail when public, organisations), and
the Copilot token's ``tid`` inherits that binding. A GitHub account without a public e-mail is
found by a gateway person whose label is the GitHub login.
"""

from __future__ import annotations

import base64
import binascii
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
class Claims:
    """What a request states about its own account (a ChatGPT JWT): read, never verified."""

    email: str | None
    account: str | None
    org: str | None


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


OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_PROFILE_CLAIM = "https://api.openai.com/profile"


def bearer_token(authorization: str) -> str:
    """The token of an ``Authorization`` header (``Bearer x`` or GitHub's ``token x``).

    Returns:
        The token, or an empty string.
    """
    scheme, _, token = authorization.strip().partition(" ")
    return token.strip() if scheme.lower() in ("bearer", "token") else ""


def jwt_claims(token: str) -> dict[str, Any] | None:
    """A JWT's claims, read without verifying the signature (attribution only).

    Returns:
        The claims, or None when the token is not a JWT.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, binascii.Error):
        return None
    return claims if isinstance(claims, dict) else None


def openai_identity(authorization: str) -> tuple[Key, Claims | None] | None:
    """The key and the stated account of a Codex or ChatGPT request.

    A ChatGPT login's JWT names the user (``chatgpt_account_user_id``, else ``chatgpt_user_id``,
    ``user_id``, ``sub``), their e-mail and the ChatGPT account (``chatgpt_account_id``, the
    organisation). An API key names nobody: its key is the key itself (never stored, only its
    digest), unidentified unless bound otherwise.

    Returns:
        (key, claims or None), or None when the request carries no token.
    """
    token = bearer_token(authorization)
    if not token:
        return None
    claims = jwt_claims(token)
    if claims is None:
        return Key("openai", "api_key", token), None
    auth = claims.get(OPENAI_AUTH_CLAIM)
    auth = auth if isinstance(auth, dict) else {}
    profile = claims.get(OPENAI_PROFILE_CLAIM)
    profile = profile if isinstance(profile, dict) else {}
    user = (
        _str(auth.get("chatgpt_account_user_id"))
        or _str(auth.get("chatgpt_user_id"))
        or _str(auth.get("user_id"))
        or _str(claims.get("sub"))
    )
    if not user:
        return None
    email = _str(profile.get("email")) or _str(claims.get("email"))
    org = _str(auth.get("chatgpt_account_id"))
    return (
        Key("openai", "user", user, org),
        Claims(email.lower() if email else None, user[:80], org[:80] if org else None),
    )


def copilot_key(authorization: str) -> Key | None:
    """The key of a Copilot chat or completion request: its Copilot token's ``tid``.

    The token is ``tid=<tracking id>;exp=...;sku=...;...:<mac>``; one without a ``tid`` is keyed
    on itself (only its digest is stored).

    Returns:
        The key, or None when the request carries no token.
    """
    token = bearer_token(authorization)
    if not token:
        return None
    tid = copilot_tid(token)
    return Key("github", "tid", tid) if tid else Key("github", "token", token)


def copilot_tid(token: str) -> str | None:
    """The ``tid`` field of a Copilot token.

    Returns:
        The tracking id, or None.
    """
    for field in token.split(":", 1)[0].split(";"):
        name, sep, value = field.partition("=")
        if sep and name.strip() == "tid" and value.strip():
            return value.strip()
    return None


def github_key(authorization: str) -> Key | None:
    """The key of a request to ``api.github.com``: the person's GitHub token.

    Returns:
        The key, or None.
    """
    token = bearer_token(authorization)
    return Key("github", "token", token) if token else None


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


def from_github_user(data: Any) -> tuple[str | None, str | None]:
    """The e-mail (when public) and login of ``GET api.github.com/user``.

    Returns:
        (email, login).
    """
    if not isinstance(data, dict):
        return None, None
    email = _str(data.get("email"))
    login = _str(data.get("login"))
    return (email.lower() if email else None), (login.lower() if login else None)


def from_copilot_user(data: Any) -> tuple[str | None, str | None, str | None, str | None]:
    """What ``GET api.github.com/copilot_internal/user`` names (best effort, not measured).

    Returns:
        (email, login, organisations joined by commas, the analytics tracking id).
    """
    if not isinstance(data, dict):
        return None, None, None, None
    email, login = from_github_user(data)
    names = [x for x in data.get("organization_login_list") or [] if isinstance(x, str)]
    if not names:
        names = [
            str(o.get("login"))
            for o in data.get("organization_list") or []
            if isinstance(o, dict) and o.get("login")
        ]
    orgs = ""
    for name in names:
        more = f"{orgs},{name}" if orgs else name
        if len(more) > 80:
            break
        orgs = more
    return email, login, orgs or None, _str(data.get("analytics_tracking_id"))


def from_copilot_token(data: Any) -> str | None:
    """The ``tid`` of the Copilot token ``GET api.github.com/copilot_internal/v2/token`` answers.

    Only the token's ``tid`` field is read; the token is never logged or kept.

    Returns:
        The tracking id, or None.
    """
    if not isinstance(data, dict):
        return None
    token = data.get("token")
    return copilot_tid(token) if isinstance(token, str) else None


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
