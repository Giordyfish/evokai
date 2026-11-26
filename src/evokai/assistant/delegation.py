"""Console delegation tokens: the assistant acts with the rights of the person behind the console.

A delegation token is minted for one console login and one application, or for every application
the login can see (``ALL``: the console assistant, which names the application on each request with
the ``X-Evokai-Application`` header, by id or slug). It is a signed token with
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

from evokai.core.errors import BadRequest, Unauthorized
from evokai.core.security import Keyring
from evokai.db.repositories import APPLICATION
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
ALL = "*"
"""The ``app`` claim of a token valid on every application the login can see, one per request."""
RECORDED = "recorded"
"""The kind of the token of the assistant's evals: the recorded admin MCP server, no application."""


def login_subject(
    kind: str, user_id: str | None, key_id: str | None, application_id: str | None = None
) -> str:
    """The subject of a console login in the assistant: ``console:<login>``.

    A single sign-on login is its user id, an admin key's login its key id (or its application's
    id for a login without one), the operator key's login ``operator``.

    Returns:
        The subject.
    """
    if kind == "sso" and user_id:
        return f"console:{user_id}"
    if kind == "adm":
        return f"console:{key_id}" if key_id else f"console:admin-app:{application_id or ''}"
    return "console:operator"


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
        application_id: The only application the token is valid on, or ``ALL``.
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
        claims["home"] = login.application_id or ""
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


async def _named(svc: Services, name: str | None) -> Any:
    """The application a request of an ``ALL`` token names, by id or slug.

    Returns:
        The application.

    Raises:
        BadRequest: No application named (``application_required``).
        Unauthorized: An unknown or inactive application.
    """
    if not name:
        raise BadRequest(
            "Name the application: pass its slug as application (list_applications lists them).",
            code="application_required",
        )
    app = await svc.store.applications.get(name) or await svc.store.applications.by_slug(name)
    # A gateway is never an application the assistant acts on (evokai.gateway).
    if app is None or app.status != "active" or app.kind != APPLICATION:
        raise Unauthorized(f"No active application {name[:60]}.", code="application")
    return app


async def principal(svc: Services, token: str, application: str | None = None) -> AppPrincipal:
    """The principal of a delegation token, checked against the live login.

    Args:
        svc: Services.
        token: The delegation token.
        application: For a token valid on every application (``ALL``), the one this request acts
            on (id or slug); a token for one application ignores it.

    Returns:
        An admin principal acting as ``assistant``, with the lower of the claimed and live role.

    Raises:
        Unauthorized: Expired, forged, another audience, an inactive application, or the login
            behind it ended or lost the application.
    """
    claims = _claims(svc.keyring, token)
    if claims["app"] == ALL:
        app = await _named(svc, application)
    else:
        app = await svc.store.applications.get(str(claims["app"]))
    if app is None or app.status != "active" or app.kind != APPLICATION:
        raise Unauthorized("Application not active.", code="application")
    live = await _live_role(svc, claims, app)
    claimed = str(claims["role"])
    if live is None or claimed not in RANK:
        raise Unauthorized("The console login of this delegation ended.", code="delegation")
    role = min(live, claimed, key=RANK.__getitem__)
    home = str(claims.get("home") or (claims["app"] if claims["app"] != ALL else ""))
    return AppPrincipal(
        application_id=app.id,
        kind="adm",
        key_id=f"delegation:{str(claims['jti'])[:16]}",
        console=True,
        role=role,
        actor=f"assistant:{claims.get('act') or claims['kind']}"[:200],
        via="assistant",
        subject=login_subject(str(claims["kind"]), claims.get("uid"), claims.get("kid"), home),
    )


async def applications(svc: Services, token: str) -> list[dict[str, Any]]:
    """The applications a delegation token may act on now, with the role it acts with on each.

    Returns:
        ``[{slug, name, role}]`` by name; the assistant's own application is marked ``system``.
        An expired or forged token, or one whose login ended, is refused (``Unauthorized``).
    """
    claims = _claims(svc.keyring, token)
    if claims["app"] == ALL:
        rows = await svc.store.applications.all()
    else:
        one = await svc.store.applications.get(str(claims["app"]))
        rows = [one] if one else []
    claimed = str(claims["role"])
    out = []
    for app in rows:
        if app.status != "active":
            continue
        live = await _live_role(svc, claims, app)
        if live is None or claimed not in RANK:
            continue
        item: dict[str, Any] = {
            "slug": app.slug,
            "name": app.name,
            "role": min(live, claimed, key=RANK.__getitem__),
        }
        if app.system:
            item["system"] = True
        out.append(item)
    if not out and claims["kind"] != "op":
        await _alive(svc, claims)
    return sorted(out, key=lambda a: str(a["name"]).lower())


async def _alive(svc: Services, claims: dict[str, Any]) -> None:
    """Refuse a token whose login ended (when no application tells it).

    Raises:
        Unauthorized: The login ended.
    """
    kind = claims["kind"]
    if kind == "sso" and await svc.auth.user_active(str(claims.get("uid") or "")):
        return
    if kind == "adm" and await svc.auth.key_alive(
        str(claims.get("kid") or ""), str(claims.get("home") or "")
    ):
        return
    raise Unauthorized("The console login of this delegation ended.", code="delegation")


async def listing_role(svc: Services, token: str) -> str:
    """The highest role a delegation token acts with on any application (what ``tools/list`` shows).

    Every call is checked again on the application it names.

    Returns:
        The role.

    Raises:
        Unauthorized: Expired, forged, or the login behind it ended or sees no application.
    """
    roles = [a["role"] for a in await applications(svc, token)]
    if not roles:
        raise Unauthorized(
            "The console login of this delegation sees no application.", code="delegation"
        )
    return max(roles, key=RANK.__getitem__)
