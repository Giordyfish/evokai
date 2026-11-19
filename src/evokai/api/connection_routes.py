"""Per-user OAuth connections: the person's own (session), the callback, the application's view.

| Method | Path | Who |
|---|---|---|
| GET | ``/v1/session/connections`` | session: the person's connections, one per granted provider |
| POST | ``/v1/session/connections/{provider}/start`` | session: the authorize URL to open |
| DELETE | ``/v1/session/connections/{provider}`` | session: disconnect (tokens deleted) |
| GET | ``/v1/oauth/callback`` | the provider's redirect (the state binds it to the person) |
| GET, DELETE | ``/v1/connections[/{provider}]?subject=`` | server key: a subject's connections |
| GET, DELETE | ``/v1/admin/connections[/{provider}]`` | console: providers and counts, revoke all |
| PUT, DELETE | ``/v1/admin/connections/{provider}/app`` | console: an OAuth app (GitHub, ...) |
"""

from __future__ import annotations

import html
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, server_app, services, session
from evokai.connections.providers import PROVIDERS
from evokai.connections.service import CALLBACK_PATH
from evokai.core.errors import AppError, NotFound
from evokai.identity.models import AppPrincipal, SessionPrincipal
from evokai.services import Services

router = APIRouter(tags=["connections"])
Svc = Annotated[Services, Depends(services)]
Sess = Annotated[SessionPrincipal, Depends(session)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
Server = Annotated[AppPrincipal, Depends(server_app)]
Provider = Literal["microsoft365", "google", "github", "linear", "notion", "slack"]


def _fallback_base(request: Request, svc: Services) -> str:
    base = svc.settings.base_path.rstrip("/")
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}{base}"


class Connection(BaseModel):
    """A person's connection to a provider."""

    provider: str
    title: str
    status: Literal["connected", "expired", "not_connected"]
    account: str | None = None
    scopes: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    """The tool families the agent may use with it (``mail``, ``calendar``, ``files``)."""
    connectedAt: str | None = None
    lastUsedAt: str | None = None


class Connections(BaseModel):
    """The person's connections."""

    connections: list[Connection]


class ConnectionStart(BaseModel):
    """Start connecting a provider."""

    returnTo: str | None = Field(default=None, max_length=1000)
    """Where the browser goes after the provider (one of the application's origins); without it,
    a page that says the window can be closed."""


class ConnectionStarted(BaseModel):
    """Where to send the person."""

    provider: str
    authorizeUrl: str
    expiresAt: int
    """Epoch seconds: after this the link no longer works."""


@router.get("/v1/session/connections", response_model=Connections)
async def my_connections(svc: Svc, s: Sess) -> dict[str, Any]:
    """The person's connections, one per provider the application configured and granted.

    Returns:
        connections.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(s.claims.application_id)
    if app is None:
        raise NotFound()
    return {"connections": await svc.connections.view(app, s.claims.subject)}


@router.post("/v1/session/connections/{provider}/start", response_model=ConnectionStarted)
async def start_connection(
    provider: Provider, body: ConnectionStart, svc: Svc, s: Sess, request: Request
) -> dict[str, Any]:
    """Start connecting a provider: open ``authorizeUrl`` (a popup or a new tab).

    The link is single use and valid ten minutes, bound to this person and this conversation.

    Returns:
        provider, authorizeUrl, expiresAt.

    Raises:
        NotFound: The application is gone or has no such provider.
    """
    app = await svc.store.applications.get(s.claims.application_id)
    if app is None:
        raise NotFound()
    return await svc.connections.start(
        app,
        s.claims.subject,
        provider,
        redirect_uri=svc.connections.redirect_uri(_fallback_base(request, svc)),
        conversation_id=s.claims.conversation_id,
        return_to=body.returnTo,
    )


@router.delete("/v1/session/connections/{provider}", status_code=204)
async def disconnect(provider: Provider, svc: Svc, s: Sess) -> Response:
    """Disconnect a provider: its tokens are deleted (and revoked at the provider when it can).

    Returns:
        204.
    """
    await svc.connections.disconnect(s.claims.application_id, s.claims.subject, provider)
    return Response(status_code=204)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;margin:0;display:grid;place-items:center;
min-height:100vh;background:#f6f5f2;color:#1d1d1b}}main{{max-width:28rem;padding:2rem;
text-align:center}}h1{{font-size:1.25rem;margin:0 0 .5rem}}p{{margin:0;color:#55534e}}</style>
</head><body><main><h1>{title}</h1><p>{text}</p></main></body></html>"""
PAGE_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"


def _page(title: str, text: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        PAGE.format(title=html.escape(title), text=html.escape(text)),
        status_code=status,
        headers={"Content-Security-Policy": PAGE_CSP, "Cache-Control": "no-store"},
    )


@router.get(CALLBACK_PATH, include_in_schema=False)
async def callback(
    svc: Svc,
    state: Annotated[str, Query(max_length=200)] = "",
    code: Annotated[str | None, Query(max_length=4000)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> Response:
    """Where the provider sends the person back.

    Returns:
        A redirect to ``returnTo``, or a page saying the window can be closed (in English and
        Italian, since the person's language is not known here).
    """
    try:
        done = await svc.connections.callback(state, code, error)
    except AppError as e:
        return _page(
            "Not connected / Non collegato",
            f"{e.message} Close this window and try again. / Chiudi questa finestra e riprova.",
            400,
        )
    if done.get("returnTo"):
        return RedirectResponse(str(done["returnTo"]), status_code=303)
    return _page(
        f"{done['title']}: connected / collegato",
        "You can close this window and go back to the assistant. / "
        "Puoi chiudere questa finestra e tornare all’assistente.",
    )


@router.get("/v1/connections", response_model=Connections)
async def subject_connections(
    svc: Svc, a: Server, subject: Annotated[str, Query(min_length=1, max_length=200)]
) -> dict[str, Any]:
    """A subject's connections (the application's backend).

    Returns:
        connections.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return {"connections": await svc.connections.view(app, subject)}


@router.delete("/v1/connections/{provider}", status_code=204)
async def subject_disconnect(
    provider: Provider,
    svc: Svc,
    a: Server,
    request: Request,
    subject: Annotated[str, Query(min_length=1, max_length=200)],
) -> Response:
    """Disconnect a subject from a provider (the application's backend).

    Returns:
        204.
    """
    if await svc.connections.disconnect(a.application_id, subject, provider):
        await audit.record(request, a, "connection.disconnect", target=provider)
    return Response(status_code=204)


@router.get("/v1/admin/connections")
async def admin_connections(svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """The application's providers: configuration, status, connected people (counts only).

    Returns:
        providers, redirectUri.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    try:
        redirect = svc.connections.redirect_uri(_fallback_base(request, svc))
    except AppError:
        redirect = None
    view = await svc.connections.admin_view(app, redirect)
    view["publicUrlSet"] = bool(svc.settings.public_url)
    return view


@router.delete("/v1/admin/connections/{provider}")
async def revoke_all(provider: Provider, svc: Svc, a: Admin, request: Request) -> dict[str, int]:
    """Revoke every person's connection to a provider (each must connect again).

    Returns:
        revoked: how many connections were deleted.

    Raises:
        NotFound: An unknown provider.
    """
    if provider not in PROVIDERS:
        raise NotFound()
    n = await svc.connections.revoke_all(a.application_id, provider)
    await audit.record(request, a, "connection.revoke_all", target=provider, after={"revoked": n})
    return {"revoked": n}


class OAuthAppIn(BaseModel):
    """The application's OAuth app at a provider no connector configures.

    The secret is write-only: it is encrypted, never returned, and kept when omitted.
    """

    clientId: str = Field(min_length=1, max_length=300)
    clientSecret: str | None = Field(default=None, max_length=4000)
    """Required the first time; omitted or empty keeps the stored one."""
    writes: bool = False
    """Ask the provider's write scopes too (the integration may then change data, each change
    confirmed by the person)."""


@router.put("/v1/admin/connections/{provider}/app", status_code=204)
async def put_oauth_app(
    provider: Provider, body: OAuthAppIn, svc: Svc, a: Admin, request: Request
) -> Response:
    """Set the application's OAuth app at GitHub, Linear, Notion or Slack.

    Returns:
        204.
    """
    await svc.connections.put_app(
        a.application_id,
        provider,
        client_id=body.clientId,
        client_secret=body.clientSecret or None,
        writes=body.writes,
    )
    await audit.record(
        request,
        a,
        "connection.app_set",
        target=provider,
        after={"clientId": body.clientId, "writes": body.writes, "secret": bool(body.clientSecret)},
    )
    return Response(status_code=204)


@router.delete("/v1/admin/connections/{provider}/app", status_code=204)
async def delete_oauth_app(provider: Provider, svc: Svc, a: Admin, request: Request) -> Response:
    """Remove the application's OAuth app at a provider (people connect again once it is set).

    Returns:
        204.
    """
    if await svc.connections.delete_app(a.application_id, provider):
        await audit.record(request, a, "connection.app_delete", target=provider)
    return Response(status_code=204)
