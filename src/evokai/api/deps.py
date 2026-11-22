"""Request dependencies: services and callers."""

from __future__ import annotations

import json

from fastapi import Request

from evokai.assistant import delegation
from evokai.core.errors import AppError, BadRequest, Conflict, Forbidden, NotFound, Unauthorized
from evokai.identity import console
from evokai.identity.auth import bearer
from evokai.identity.models import AppPrincipal, SessionPrincipal
from evokai.identity.roles import authorize, required, route_of
from evokai.mcp_admin.tools import by_route
from evokai.services import Services

SESSION_REQUESTS_PER_MINUTE = 240
"""Requests per session token per minute on the browser API (a token lives at most 15 minutes)."""


def services(request: Request) -> Services:
    """The process services.

    Returns:
        Services.
    """
    return request.app.state.services


def origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """Whether a browser origin may use a session (no Origin header = not a browser).

    Returns:
        True if allowed.
    """
    if not origin:
        return True
    return origin.rstrip("/") in {a.rstrip("/") for a in allowed}


async def _console(request: Request) -> AppPrincipal | None:
    """The application of a console login, if the request uses the console cookie.

    Returns:
        An admin principal, or None without the cookie.

    Raises:
        Forbidden: The CSRF header is missing, no application was picked, or the login's role on
            the application does not allow the route (``role``).
        NotFound: An unknown application, or one the login may not see.
    """
    value = request.cookies.get(console.COOKIE)
    if not value:
        return None
    if request.headers.get(console.HEADER) != "1":
        raise Forbidden("Invalid console request.", code="console_header")
    svc = services(request)
    login = await console_login(request)
    app_id = login.application_id
    if login.kind in {"op", "sso"}:
        app_id = request.headers.get(console.APPLICATION_HEADER)
        if not app_id:
            raise Forbidden("Pick an application.", code="application_required")
    app = await svc.store.applications.get(app_id or "")
    role = login.role_for(app) if app is not None else None
    if app is None or role is None:
        raise NotFound("Unknown application.", code="unknown_application")
    authorize(request, role, svc.settings.base_path)
    key_id = {"op": "operator", "sso": f"sso:{login.user_id}"}.get(login.kind) or login.key_id
    return AppPrincipal(
        application_id=app.id,
        kind="adm",
        key_id=(key_id or "console")[:80],
        console=True,
        role=role,
        actor=login.actor,
        subject=delegation.login_subject(
            login.kind, login.user_id, login.key_id, login.application_id
        ),
    )


async def _delegated(request: Request, token: str) -> AppPrincipal:
    """The console assistant's delegation token: the live login's rights, checked like the console.

    An expired or forged token, or one whose login ended, is refused (401); a role that does not
    allow the route is refused (403 ``role``). Even used directly, it opens only the routes of the
    admin MCP server's allow-list (403 ``assistant_route``) and only the body fields a tool may send
    (403 ``assistant_field``): keys, secrets, sign-in, the audit log, personal data and the unlock
    of a system application stay out of its reach whoever holds it.

    Returns:
        The principal.

    Raises:
        Forbidden: A route or a field outside the admin MCP server's allow-list.
        BadRequest: A body that is not a JSON object.
    """
    svc = services(request)
    who = await delegation.principal(svc, token)
    authorize(request, who.role, svc.settings.base_path)
    spec = by_route().get((request.method.upper(), route_of(request, svc.settings.base_path)))
    if spec is None:
        raise Forbidden("The assistant cannot use this route.", code="assistant_route")
    if spec.fields is not None:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            raise BadRequest("Invalid JSON.") from None
        if not isinstance(body, dict):
            raise BadRequest("The body is a JSON object.")
        if set(body) - set(spec.fields):
            raise Forbidden("The assistant cannot change these fields.", code="assistant_field")
    return who


async def console_login(request: Request) -> console.ConsoleLogin:
    """The console login of a request (cookie and CSRF header), still backed by a live key.

    Returns:
        The login.

    Raises:
        Unauthorized: No cookie, an expired one, or its admin key was revoked.
        Forbidden: The CSRF header is missing.
    """
    value = request.cookies.get(console.COOKIE)
    if not value:
        raise Unauthorized()
    if request.headers.get(console.HEADER) != "1":
        raise Forbidden("Invalid console request.", code="console_header")
    svc = services(request)
    login = console.read(svc.keyring, value)
    if (
        login.kind == "adm"
        and login.key_id
        and not await svc.auth.key_alive(login.key_id, login.application_id or "")
    ):
        raise Unauthorized("The key of this login was revoked.", code="console")
    if login.kind == "sso" and not await svc.auth.user_active(login.user_id or ""):
        raise Unauthorized("This console user was disabled.", code="console")
    return login


async def server_app(request: Request) -> AppPrincipal:
    """An application authenticated with its server or admin key, or a console login.

    Returns:
        The application principal.

    Raises:
        Unauthorized: No key.
    """
    token = bearer(request.headers.get("authorization"))
    if not token:
        if (c := await _console(request)) is not None:
            return c
        raise Unauthorized()
    # The console assistant's delegation token is never a server key: the server API mints session
    # tokens and reaches a person's data (export, erase), which the assistant must never do.
    return await services(request).auth.application(token)


async def admin_app(request: Request) -> AppPrincipal:
    """An application authenticated with its admin key, or a console login.

    Every change on a locked system application is refused here (409 ``locked``), for every admin
    route, including routes added later; only ``LOCK_EXEMPT`` routes pass.

    Returns:
        The application principal.

    Raises:
        Unauthorized: No key.
    """
    token = bearer(request.headers.get("authorization"))
    if not token:
        if (c := await _console(request)) is None:
            raise Unauthorized()
        who = c
    elif delegation.is_delegation(token):
        who = await _delegated(request, token)
    else:
        who = await services(request).auth.application(token, admin=True)
    await _unlocked(request, who)
    return who


LOCK_EXEMPT: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/v1/admin/application/unlock"),
    ("POST", "/v1/admin/application/reset"),
    ("POST", "/v1/admin/assistant/activate"),
    ("POST", "/v1/admin/assistant/try"),
    ("POST", "/v1/admin/assistant/conversations/{conversation_id}/inspect"),
})
"""Changes a locked system application accepts: unlocking and resetting it, activating the
assistant's model and trying a model on it."""


async def _unlocked(request: Request, who: AppPrincipal) -> None:
    """Refuse a change on a locked system application (409 ``locked``).

    Raises:
        Conflict: The application is a locked system application and the route changes something.
    """
    svc = services(request)
    method, path = request.method.upper(), route_of(request, svc.settings.base_path)
    if required(method, path) == "viewer" or (method, path) in LOCK_EXEMPT:
        return
    app = await svc.store.applications.get(who.application_id)
    if app is not None and app.system and app.locked:
        raise Conflict(
            "This system application is locked: an owner unlocks it in the console first.",
            code="locked",
        )


async def operator(request: Request) -> None:
    """The deployment's owner: the operator key, or a console login that owns the deployment.

    A console request without the CSRF header is refused (403) by ``console_login``.

    Raises:
        Unauthorized: Neither.
    """
    svc = services(request)
    token = bearer(request.headers.get("authorization"))
    if token:
        if console.is_operator_key(token, svc.settings.operator_key):
            return
        raise Unauthorized("The operator key is required.", code="operator_key")
    if not request.cookies.get(console.COOKIE):
        raise Unauthorized()
    login = await console_login(request)
    if not login.deployment_owner:
        raise Unauthorized("An operator login is required.", code="operator_key")


def same_origin(request: Request, origin: str) -> bool:
    """Whether a browser origin is this service's own (the console's playground).

    Returns:
        True for the service's own scheme and host.
    """
    own = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    return origin.rstrip("/") == own


async def session(request: Request) -> SessionPrincipal:
    """A browser session (session token), from an allowed origin.

    Returns:
        The session principal.

    Raises:
        Unauthorized: No token.
        AppError: More than ``SESSION_REQUESTS_PER_MINUTE`` requests with this token (429).
        Forbidden: Origin not allowed for this application.
    """
    token = bearer(request.headers.get("authorization"))
    if not token:
        raise Unauthorized()
    auth = services(request).auth
    principal = await auth.session(token)
    if not await auth.count(f"token:{principal.claims.token_id}", SESSION_REQUESTS_PER_MINUTE, 60):
        raise AppError("Too many requests. Try again in a minute.", code="rate_limited", status=429)
    origin = request.headers.get("origin")
    if origin and same_origin(request, origin):
        return principal
    if not origin_allowed(origin, principal.origins):
        raise Forbidden("Origin not allowed.", code="origin")
    return principal
