"""Console logins: an admin or operator key, or single sign-on, exchanged for a short cookie.

Keys never reach browser storage. The cookie is a signed token (its own issuer, so it can never
pass as a session token), ``HttpOnly``, ``SameSite=Strict``, ``Secure`` over HTTPS, scoped to the
API path. Requests that use it must also send ``X-Evokai-Console: 1`` (a header a cross-site form
cannot set), and an operator picks the application with ``X-Evokai-Application``.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field
from typing import Any

import jwt

from evokai.core.errors import Unauthorized
from evokai.core.security import Keyring
from evokai.identity.roles import highest

COOKIE = "evokai_console"
HEADER = "x-evokai-console"
APPLICATION_HEADER = "x-evokai-application"
ISSUER = "evokai-console"


@dataclass(frozen=True)
class ConsoleLogin:
    """Who is logged in to the console."""

    kind: str
    """``op`` (the operator key: owner of every application), ``adm`` (an admin key: owner of
    its application) or ``sso`` (single sign-on: the roles its identity provider gave)."""
    application_id: str | None
    expires_at: int
    key_id: str | None = None
    """The admin key the login was made with (it ends with the key's revocation); for the
    operator key, its fingerprint (the login ends when the key is changed or removed)."""
    user_id: str | None = None
    """Single sign-on: the console user (a disabled user's login ends at once)."""
    email: str | None = None
    name: str | None = None
    roles: dict[str, str] = field(default_factory=dict)
    """Single sign-on: application slug, or ``*`` for every application, to its role."""

    def role_for(self, app: Any) -> str | None:
        """The role of this login on an application.

        Returns:
            owner, operator or viewer; None when it may not see the application.
        """
        if self.kind == "op":
            return "owner"
        if self.kind == "adm":
            return "owner" if self.application_id == app.id else None
        return highest(self.roles.get(app.slug), self.roles.get("*"))

    @property
    def deployment_owner(self) -> bool:
        """Whether the login owns the deployment (applications, sign-in, console users).

        Returns:
            True for the operator key and for single sign-on owners of ``*``.
        """
        return self.kind == "op" or (self.kind == "sso" and self.roles.get("*") == "owner")

    @property
    def actor(self) -> str:
        """Who this login is, for the audit log.

        Returns:
            ``sso:<e-mail>``, ``admin-key:<id>`` or ``operator-key``.
        """
        if self.kind == "sso":
            return f"sso:{self.email or self.user_id}"
        if self.kind == "adm":
            return f"admin-key:{(self.key_id or '')[:8]}"
        return "operator-key"


def is_operator_key(presented: str, configured: str | None) -> bool:
    """Whether a key is the deployment's operator key (constant time).

    Returns:
        True for the operator key.
    """
    return bool(configured) and hmac.compare_digest(presented.encode(), str(configured).encode())


def operator_fingerprint(keyring: Keyring, configured: str | None) -> str | None:
    """A keyed fingerprint of the operator key, held by an operator login (``key_id``).

    A login made with the operator key ends when the key is changed or removed: its fingerprint
    no longer matches. It is keyed with the signing key, so it says nothing about the key itself.

    Returns:
        32 hex characters, or None without an operator key.
    """
    if not configured:
        return None
    mac = hmac.new(keyring.signing_key.encode(), f"evokai-operator:{configured}".encode(), "sha256")
    return mac.hexdigest()[:32]


def operator_current(keyring: Keyring, configured: str | None, key_id: str | None) -> bool:
    """Whether an operator login was made with the operator key configured now.

    Returns:
        True when the login's fingerprint matches the current key's (constant time).
    """
    now = operator_fingerprint(keyring, configured)
    return bool(now and key_id) and hmac.compare_digest(str(key_id).encode(), str(now).encode())


def issue(keyring: Keyring, login: ConsoleLogin) -> str:
    """Sign a console login.

    Returns:
        The cookie value.
    """
    payload = {
        "iss": ISSUER,
        "knd": login.kind,
        "aud": login.application_id or "",
        "kid": login.key_id or "",
        "iat": int(time.time()),
        "exp": login.expires_at,
    }
    if login.kind == "sso":
        payload |= {"uid": login.user_id, "eml": login.email, "nam": login.name, "rol": login.roles}
    return jwt.encode(payload, keyring.signing_key, algorithm="HS256")


def read(keyring: Keyring, value: str) -> ConsoleLogin:
    """Verify a console cookie.

    Returns:
        The login.

    Raises:
        Unauthorized: Expired, forged or malformed.
    """
    try:
        p = jwt.decode(
            value,
            keyring.signing_key,
            algorithms=["HS256"],
            issuer=ISSUER,
            options={"require": ["exp", "knd"], "verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise Unauthorized("Console login expired.", code="console") from e
    kind = str(p["knd"])
    if kind not in {"op", "adm", "sso"}:
        raise Unauthorized("Invalid console login.", code="console")
    raw = p.get("rol")
    roles: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return ConsoleLogin(
        kind,
        str(p.get("aud") or "") or None,
        int(p["exp"]),
        str(p.get("kid") or "") or None,
        user_id=str(p["uid"]) if p.get("uid") else None,
        email=str(p["eml"]) if p.get("eml") else None,
        name=str(p["nam"]) if p.get("nam") else None,
        roles={str(k): str(v) for k, v in roles.items()},
    )
