"""Console sign-in with single sign-on, the console's users, and the audit log.

The sign-in pages are reached by the browser itself (redirects), so they need no console header;
everything else is for deployment owners, except an application's own audit log, which every
console role of that application may read.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from evokai import audit
from evokai.api.console_routes import deployment_actor
from evokai.api.deps import admin_app, operator, services
from evokai.core.errors import BadRequest, NotFound
from evokai.db import models as m
from evokai.identity import console, sso
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(tags=["console"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
Owner = [Depends(operator)]
log = logging.getLogger("evokai.sso")

FLOW_COOKIE = "evokai_console_sso"
FLOW_TTL = 600


def _base(svc: Services) -> str:
    return svc.settings.base_path.rstrip("/")


def _redirect_uri(request: Request, svc: Services, cfg: sso.SsoConfig) -> str:
    if cfg.redirect_uri:
        return cfg.redirect_uri
    if svc.settings.public_url:
        return f"{svc.settings.public_url.rstrip('/')}/v1/console/sso/callback"
    return f"{request.url.scheme}://{request.url.netloc}{_base(svc)}/v1/console/sso/callback"


def _to_console(svc: Services, error: str | None = None) -> RedirectResponse:
    target = f"{_base(svc)}/admin/#/" + (f"login?sso_error={quote(error)}" if error else "apps")
    return RedirectResponse(target, status_code=302)


@router.get("/v1/console/sso")
async def sso_info(svc: Svc) -> dict[str, Any]:
    """Whether the console offers single sign-on (for the sign-in page; no login needed).

    Returns:
        enabled and the button's label.
    """
    cfg, _ = await sso.configured(svc.store, svc.settings, svc.keyring)
    return {"enabled": cfg is not None, "label": cfg.label if cfg else None}


@router.get("/v1/console/sso/start", include_in_schema=False)
async def sso_start(request: Request, svc: Svc) -> RedirectResponse:
    """Send the browser to the identity provider (authorization code with PKCE).

    Returns:
        A redirect to the provider, with the flow in a short encrypted cookie.
    """
    cfg, _ = await sso.configured(svc.store, svc.settings, svc.keyring)
    if cfg is None:
        return _to_console(svc, "not_configured")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = sso.pkce()
    redirect_uri = _redirect_uri(request, svc, cfg)
    try:
        url = await sso.authorization_url(svc.http, cfg, redirect_uri, state, nonce, challenge)
    except sso.SsoError as e:
        log.warning("sso_start_failed", extra={"code": e.code, "detail": e.detail})
        return _to_console(svc, e.code)
    flow = {"s": state, "n": nonce, "v": verifier, "r": redirect_uri, "t": int(time.time())}
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        FLOW_COOKIE,
        svc.keyring.encrypt(json.dumps(flow)),
        max_age=FLOW_TTL,
        httponly=True,
        samesite="lax",  # the provider's redirect back is a cross-site navigation
        secure=request.url.scheme == "https" or svc.settings.production,
        path=f"{_base(svc)}/v1/console/sso",
    )
    return response


@router.get("/v1/console/sso/callback", include_in_schema=False)
async def sso_callback(
    request: Request,
    svc: Svc,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish the sign-in: check the state, trade the code, verify the ID token, map the roles.

    Returns:
        A redirect to the console with its cookie, or to the sign-in page with the reason (every
        ``SsoError`` ends there).

    Raises:
        SsoError: Caught here: a refused sign-in never leaves this function.
    """
    raw = request.cookies.get(FLOW_COOKIE)
    flow = json.loads(svc.keyring.decrypt(raw) or "{}") if raw else {}
    try:
        if error:
            raise sso.SsoError("provider", error[:80])
        if not flow or not state or not secrets.compare_digest(str(flow.get("s")), state):
            raise sso.SsoError("state")
        if time.time() - int(flow.get("t", 0)) > FLOW_TTL or not code:
            raise sso.SsoError("state", "expired")
        cfg, _ = await sso.configured(svc.store, svc.settings, svc.keyring)
        if cfg is None:
            raise sso.SsoError("not_configured")
        id_token = await sso.exchange(svc.http, cfg, code, str(flow["v"]), str(flow["r"]))
        claims = await sso.verify(svc.http, cfg, id_token, str(flow["n"]))
        who = sso.identity(cfg, claims)
        user = await svc.store.console_users.signed_in(
            who.issuer, who.sub, who.email, who.name, who.roles
        )
        if user.disabled:
            raise sso.SsoError("disabled")
    except sso.SsoError as e:
        log.warning("sso_refused", extra={"code": e.code, "detail": e.detail})
        await audit.record(request, None, "console.login_failed", target=f"sso:{e.code}")
        response = _to_console(svc, e.code)
        response.delete_cookie(FLOW_COOKIE, path=f"{_base(svc)}/v1/console/sso")
        return response
    login = console.ConsoleLogin(
        "sso",
        None,
        int(time.time()) + svc.settings.console_session_ttl,
        user_id=user.id,
        email=who.email,
        name=who.name,
        roles=who.roles,
    )
    await svc.auth.forget_user(user.id)
    await audit.record(
        request, login, "console.login", target="sso", after={"roles": who.roles, "amr": who.amr}
    )
    response = _to_console(svc)
    response.delete_cookie(FLOW_COOKIE, path=f"{_base(svc)}/v1/console/sso")
    response.set_cookie(
        console.COOKIE,
        console.issue(svc.keyring, login),
        max_age=svc.settings.console_session_ttl,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https" or svc.settings.production,
        path=_base(svc) or "/",
    )
    return response


class SsoConfigBody(sso.SsoConfig):
    """The provider; leave ``client_secret`` out to keep the one already stored."""


@router.get("/v1/console/sso/config", dependencies=Owner)
async def get_sso_config(request: Request, svc: Svc) -> dict[str, Any]:
    """The sign-in provider (deployment owners), never its secret.

    Returns:
        source (``console``, ``environment`` or null), config, redirectUri.
    """
    cfg, source = await sso.configured(svc.store, svc.settings, svc.keyring)
    return {
        "source": source,
        "config": cfg.public() if cfg else None,
        "redirectUri": _redirect_uri(request, svc, cfg) if cfg else None,
    }


@router.put("/v1/console/sso/config", dependencies=Owner)
async def put_sso_config(body: SsoConfigBody, request: Request, svc: Svc) -> dict[str, Any]:
    """Set the sign-in provider from the console (it wins over ``EVOKAI_SSO``).

    Admin and operator keys keep working, so a wrong provider never locks the console.

    Returns:
        The new configuration, as ``GET`` shows it.
    """
    before, _ = await sso.configured(svc.store, svc.settings, svc.keyring)
    secret = body.client_secret or (before.client_secret if before else None)
    value = body.model_dump(mode="json", exclude={"client_secret"})
    if secret:
        value["client_secret_encrypted"] = svc.keyring.encrypt(secret)
    actor = deployment_actor(request, svc)
    await svc.store.console_settings.put(sso.SETTING, value, actor.actor)
    sso.forget_metadata()
    await audit.record(
        request,
        actor,
        "sso.config",
        target="console",
        before=before.public() if before else None,
        after=body.model_copy(update={"client_secret": secret}).public(),
    )
    return await get_sso_config(request, svc)


@router.delete("/v1/console/sso/config", dependencies=Owner)
async def delete_sso_config(request: Request, svc: Svc) -> dict[str, Any]:
    """Forget the console's provider: ``EVOKAI_SSO`` applies again, if set.

    Returns:
        The configuration that applies now.
    """
    before, _ = await sso.configured(svc.store, svc.settings, svc.keyring)
    await svc.store.console_settings.delete(sso.SETTING)
    sso.forget_metadata()
    await audit.record(
        request,
        deployment_actor(request, svc),
        "sso.config_reset",
        before=before.public() if before else None,
    )
    return await get_sso_config(request, svc)


def _user(u: m.ConsoleUser) -> dict[str, Any]:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "roles": u.roles,
        "disabled": u.disabled,
        "createdAt": u.created_at.isoformat(),
        "lastLoginAt": u.last_login_at.isoformat() if u.last_login_at else None,
    }


@router.get("/v1/console/users", dependencies=Owner)
async def users(svc: Svc) -> dict[str, Any]:
    """People who signed in with single sign-on, with the roles of their last sign-in.

    Returns:
        items.
    """
    return {"items": [_user(u) for u in await svc.store.console_users.all()]}


class UserPatch(BaseModel):
    """Disable or enable a console user."""

    disabled: bool


@router.patch("/v1/console/users/{user_id}", dependencies=Owner)
async def patch_user(user_id: str, body: UserPatch, request: Request, svc: Svc) -> dict[str, Any]:
    """Disable a user (their login ends at once) or enable them again.

    Returns:
        The user.

    Raises:
        NotFound: Unknown user.
    """
    before = await svc.store.console_users.get(user_id)
    if before is None or not await svc.store.console_users.set_disabled(user_id, body.disabled):
        raise NotFound()
    await svc.auth.forget_user(user_id)
    await audit.record(
        request,
        deployment_actor(request, svc),
        "console_user.disable" if body.disabled else "console_user.enable",
        target=before.email,
        before={"disabled": before.disabled},
        after={"disabled": body.disabled},
    )
    after = await svc.store.console_users.get(user_id)
    assert after is not None  # ruff: ignore[assert] (just updated)
    return _user(after)


def _entry(e: m.AuditEntry, slugs: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": e.id,
        "at": e.at.isoformat(),
        "application": slugs.get(e.application_id or "") if slugs is not None else None,
        "actor": e.actor,
        "actorKind": e.actor_kind,
        "role": e.role,
        "action": e.action,
        "target": e.target,
        "before": e.before,
        "after": e.after,
        "ip": e.ip,
        "correlationId": e.correlation_id,
    }


Start = Annotated[datetime | None, Query(alias="from")]
End = Annotated[datetime | None, Query(alias="to")]
Limit = Annotated[int, Query(ge=1, le=500)]


@router.get("/v1/admin/audit")
async def app_audit(
    svc: Svc,
    a: Admin,
    action: str | None = None,
    actor: str | None = None,
    start: Start = None,
    end: End = None,
    before: datetime | None = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """The application's audit log, newest first (``action`` ending in ``.`` is a prefix).

    Returns:
        items, and ``next`` (the ``before`` of the next page) when there may be more.
    """
    rows = await svc.store.audit.query(
        a.application_id,
        action=action,
        actor=actor,
        start=start,
        end=end,
        before=before,
        limit=limit,
    )
    return {
        "items": [_entry(e) for e in rows],
        "next": rows[-1].at.isoformat() if len(rows) == limit else None,
    }


@router.get("/v1/console/audit", dependencies=Owner)
async def deployment_audit(
    svc: Svc,
    application: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    start: Start = None,
    end: End = None,
    before: datetime | None = None,
    limit: Limit = 100,
) -> dict[str, Any]:
    """Every audit entry (deployment owners): the deployment's and every application's.

    Returns:
        items (with the application's slug), and ``next``.

    Raises:
        BadRequest: An unknown application slug.
    """
    apps = await svc.store.applications.all(kind=None)
    slugs = {x.id: x.slug for x in apps}
    app_id = None
    if application:
        app_id = next((x.id for x in apps if x.slug == application), None)
        if app_id is None:
            raise BadRequest("Unknown application.", code="unknown_application")
    rows = await svc.store.audit.query(
        app_id,
        everything=True,
        action=action,
        actor=actor,
        start=start,
        end=end,
        before=before,
        limit=limit,
    )
    return {
        "items": [_entry(e, slugs) for e in rows],
        "next": rows[-1].at.isoformat() if len(rows) == limit else None,
    }
