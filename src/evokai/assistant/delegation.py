"""Console delegation tokens: the assistant acts with the rights of the person behind the console.

A delegation token is minted for one console login and one application. It is a signed token with
its own issuer and audience (``evokai-admin-mcp``), so it never passes as a session token, a worker
token or a console cookie, and it carries the ``evk_cdt_`` prefix so a log reader knows what it is.

It is checked again on every use, against the live login behind it:

- an admin key login ends with the key's revocation;
- a single sign-on login ends when the user is disabled, and its role follows the user's roles of
  the latest sign-in;
- the role used is the lower of the token's claim and the login's role now, never above.
"""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Any

import jwt

from evokai.core.errors import Unauthorized
from evokai.core.security import Keyring
from evokai.identity.console import ConsoleLogin
from evokai.identity.models import AppPrincipal
from evokai.identity.roles import RANK, Role, highest

if TYPE_CHECKING:
    from evokai.services import Services

PREFIX = "evk_cdt_"
ISSUER = "evokai-delegation"
AUDIENCE = "evokai-admin-mcp"
DEFAULT_TTL = 900
"""Seconds a delegation token lives (a console assistant session)."""
RECORDED = "recorded"
"""The kind of the token of the assistant's evals: the recorded admin MCP server, no application."""


def is_delegation(token: str) -> bool:
    """Whether a bearer value is a delegation token.

    Returns:
        True for the ``evk_cdt_`` prefix.
    """
    return token.startswith(PREFIX)


def mint_delegation(
    keyring: Keyring, login: ConsoleLogin, application_id: str, role: Role, ttl: int = DEFAULT_TTL
) -> str:
    """Sign a delegation token for a console login on one application.

    Args:
        keyring: Signing keys.
        login: The console login the assistant acts for.
        application_id: The only application the token is valid on.
        role: The login's role on it now (the role used is re-checked on every request).
        ttl: Lifetime in seconds.

    Returns:
        The token, with its prefix.
    """
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "app": application_id,
        "role": role,
        "kind": login.kind,
        "act": login.actor,
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + ttl,
    }
    if login.kind == "sso":
        claims["uid"] = login.user_id or ""
    elif login.kind == "adm":
        claims["kid"] = login.key_id or ""
    return PREFIX + jwt.encode(claims, keyring.signing_key, algorithm="HS256")


def mint_recorded(keyring: Keyring, ttl: int = 7_200) -> str:
    """Sign a token for the recorded admin MCP server (the assistant's evals).

    It opens no application: the admin API refuses it (its kind has no live login), and the admin
    MCP server answers it from recorded responses only.

    Returns:
        The token, with its prefix.
    """
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "app": "",
        "role": "viewer",
        "kind": RECORDED,
        "jti": secrets.token_hex(16),
        "iat": now,
        "exp": now + ttl,
    }
    return PREFIX + jwt.encode(claims, keyring.signing_key, algorithm="HS256")


def is_recorded(keyring: Keyring, token: str) -> bool:
    """Whether a bearer value is a valid token for the recorded admin MCP server.

    Returns:
        True for a delegation token of kind ``recorded``.
    """
    if not is_delegation(token):
        return False
    try:
        return _claims(keyring, token).get("kind") == RECORDED
    except Unauthorized:
        return False


def _claims(keyring: Keyring, token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token[len(PREFIX) :],
            keyring.signing_key,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["exp", "iat", "jti", "app", "role", "kind"]},
        )
    except jwt.PyJWTError as e:
        raise Unauthorized("Delegation expired or invalid.", code="delegation") from e


async def _live_role(svc: Services, claims: dict[str, Any], app: Any) -> str | None:
    """The role of the login behind a token on its application, now.

    Returns:
        The role, or None when the login ended or may no longer see the application.
    """
    kind = claims["kind"]
    if kind == "op":
        return "owner"
    if kind == "adm":
        kid = str(claims.get("kid") or "")
        return "owner" if kid and await svc.auth.key_alive(kid, app.id) else None
    if kind == "sso":
        uid = str(claims.get("uid") or "")
        if not uid or not await svc.auth.user_active(uid):
            return None
        user = await svc.store.console_users.get(uid)
        roles = dict(user.roles or {}) if user else {}
        return highest(roles.get(app.slug), roles.get("*"))
    return None


async def principal(svc: Services, token: str) -> AppPrincipal:
    """The principal of a delegation token, checked against the live login.

    Returns:
        An admin principal acting as ``assistant``, with the lower of the claimed and live role.

    Raises:
        Unauthorized: Expired, forged, another audience, an inactive application, or the login
            behind it ended or lost the application.
    """
    claims = _claims(svc.keyring, token)
    app = await svc.store.applications.get(str(claims["app"]))
    if app is None or app.status != "active":
        raise Unauthorized("Application not active.", code="application")
    live = await _live_role(svc, claims, app)
    claimed = str(claims["role"])
    if live is None or claimed not in RANK:
        raise Unauthorized("The console login of this delegation ended.", code="delegation")
    role = min(live, claimed, key=RANK.__getitem__)
    return AppPrincipal(
        application_id=app.id,
        kind="adm",
        key_id=f"delegation:{str(claims['jti'])[:16]}",
        console=True,
        role=role,
        actor=f"assistant:{claims.get('act') or claims['kind']}"[:200],
        via="assistant",
    )
