"""Channels beyond the web panel: the endpoints their providers call, and their configuration.

| Method | Path | Who |
|---|---|---|
| POST | ``/v1/channels/{app}/email/{name}/inbound`` | a mail provider, signed (HMAC) |
| POST | ``/v1/channels/{app}/teams/{name}/messages`` | the Bot Framework (a JWT it signs) |
| POST | ``/v1/channels/{app}/slack/{name}/events`` | Slack's Events API (``X-Slack-Signature``) |
| POST | ``/v1/channels/{app}/slack/{name}/interactions`` | Slack's interactivity (signed) |
| GET | ``/v1/admin/channels`` | console: channels with endpoints, environment, status |
| PUT, DELETE | ``/v1/admin/channels/{kind}/{name}`` | console (operator): create, change, delete |
| POST | ``/v1/admin/channels/{kind}/{name}/test`` | console: validate without sending anything |

Every delivery is verified before anything else, answered at once and handled in the background.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.channels import slack
from evokai.channels.config import validate
from evokai.channels.teams import TeamsRefused
from evokai.connectors.base import secret
from evokai.core.errors import AppError, BadRequest, NotFound, Unauthorized
from evokai.identity.models import AppPrincipal
from evokai.services import Services
from evokai.workflows.webhooks import verify as verify_hook

public = APIRouter(prefix="/v1/channels", tags=["channels"])
admin = APIRouter(prefix="/v1/admin/channels", tags=["admin"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
Kind = Literal["email", "teams", "slack"]
MAX_MAIL = 30_000_000
"""Bytes of an inbound message (attachments included)."""
MAX_EVENT = 1_000_000


def base_url(request: Request, svc: Services) -> str:
    """The public base of the service (``EVOKAI_PUBLIC_URL``, else the request's own).

    Returns:
        The URL, with the base path.
    """
    if svc.settings.public_url:
        return svc.settings.public_url.rstrip("/")
    base = svc.settings.base_path.rstrip("/")
    return f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}{base}"


# the providers' endpoints


@public.post("/{application}/email/{name}/inbound", status_code=202)
async def email_inbound(application: str, name: str, request: Request, svc: Svc) -> JSONResponse:
    """An inbound message from a mail provider: the raw RFC 822 message as the body.

    Signed like workflow webhooks: ``X-Evokai-Timestamp`` and ``X-Evokai-Signature``
    (``v1=`` and the HMAC-SHA256 of ``<timestamp>.<body>`` with the channel's secret).

    Returns:
        202 ``{"accepted": true}`` (handled in the background).

    Raises:
        NotFound: A channel that is not a webhook mailbox (and unknown or disabled ones).
        Unauthorized: A missing or wrong signature.
        BadRequest: A message that is too large.
    """
    app, rec, cfg = await svc.channels.channel(application, "email", name)
    if cfg.inbound != "webhook":
        raise NotFound("Unknown channel.", code="unknown_channel")
    body = await request.body()
    if len(body) > MAX_MAIL:
        raise BadRequest("The message is too large.", code="too_large")
    try:
        key = secret(cfg.webhook_secret_env, "inbound mail webhook")
    except AppError as e:
        raise Unauthorized("The channel has no secret.", code="signature") from e
    h = request.headers
    if not verify_hook(
        key, h.get("x-evokai-timestamp"), h.get("x-evokai-signature"), body, 300, time.time()
    ):
        raise Unauthorized("Invalid signature.", code="signature")
    svc.channels.spawn(svc.channels.email(app, rec, cfg, body))
    return JSONResponse({"accepted": True}, status_code=202)


@public.post("/{application}/teams/{name}/messages")
async def teams_messages(application: str, name: str, request: Request, svc: Svc) -> JSONResponse:
    """A Bot Framework activity (the bot's messaging endpoint).

    Returns:
        200 (the reply is posted to the conversation afterwards).

    Raises:
        Unauthorized: The request's token does not check (an unknown channel is 404).
        BadRequest: Not an activity.
    """
    app, rec, cfg = await svc.channels.channel(application, "teams", name)
    body = await request.body()
    if len(body) > MAX_EVENT:
        raise BadRequest("The activity is too large.", code="too_large")
    try:
        activity = json.loads(body)
    except ValueError as e:
        raise BadRequest("Not an activity.", code="activity") from e
    if not isinstance(activity, dict):
        raise BadRequest("Not an activity.", code="activity")
    try:
        await svc.channels.teams.verify(cfg, request.headers.get("authorization"), activity)
    except TeamsRefused as e:
        raise Unauthorized("Invalid token.", code="token") from e
    svc.channels.spawn(svc.channels.teams_activity(app, rec, cfg, activity))
    return JSONResponse({}, status_code=200)


async def _slack_body(request: Request, cfg: Any) -> bytes:
    body = await request.body()
    if len(body) > MAX_EVENT:
        raise BadRequest("The event is too large.", code="too_large")
    try:
        key = secret(cfg.signing_secret_env, "Slack signing secret")
    except AppError as e:
        raise Unauthorized("The channel has no secret.", code="signature") from e
    h = request.headers
    if not slack.verify(key, h.get("x-slack-request-timestamp"), h.get("x-slack-signature"), body):
        raise Unauthorized("Invalid signature.", code="signature")
    return body


@public.post("/{application}/slack/{name}/events")
async def slack_events(application: str, name: str, request: Request, svc: Svc) -> JSONResponse:
    """Slack's Events API (and its URL verification).

    Returns:
        200; ``{"challenge"}`` for the URL verification.

    Raises:
        BadRequest: Not JSON (an unknown channel is 404, a wrong signature 401).
    """
    app, rec, cfg = await svc.channels.channel(application, "slack", name)
    body = await _slack_body(request, cfg)
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise BadRequest("Not an event.", code="event") from e
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": str(payload.get("challenge") or "")})
    svc.channels.spawn(svc.channels.slack_event(app, rec, cfg, payload))
    return JSONResponse({}, status_code=200)


@public.post("/{application}/slack/{name}/interactions")
async def slack_interactions(
    application: str, name: str, request: Request, svc: Svc
) -> JSONResponse:
    """Slack's interactivity: a button pressed (form-encoded ``payload``).

    Returns:
        200.

    Raises:
        BadRequest: No payload (an unknown channel is 404, a wrong signature 401).
    """
    app, rec, cfg = await svc.channels.channel(application, "slack", name)
    body = await _slack_body(request, cfg)
    try:
        payload = json.loads(parse_qs(body.decode())["payload"][0])
    except (KeyError, IndexError, ValueError, UnicodeDecodeError) as e:
        raise BadRequest("No payload.", code="event") from e
    svc.channels.spawn(svc.channels.slack_action(app, rec, cfg, payload))
    return JSONResponse({}, status_code=200)


# the console


class ChannelBody(BaseModel):
    """A channel's configuration."""

    agent: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    """Its settings (``docs/channels/README.md``): names of environment variables, never secrets."""


@admin.get("")
async def list_channels(svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """The application's channels: endpoints to copy, environment variables, status.

    Returns:
        channels, baseUrl, publicUrlSet.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    base = base_url(request, svc)
    return {
        "channels": await svc.channels.view(app, base),
        "baseUrl": base,
        "publicUrlSet": bool(svc.settings.public_url),
    }


@admin.put("/{kind}/{name}")
async def put_channel(
    kind: Kind, name: str, body: ChannelBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Create or change a channel (its configuration is checked first).

    Returns:
        The channel, as in the list.

    Raises:
        BadRequest: An invalid name or configuration (or a secret in clear).
        NotFound: The application is gone.
    """
    if not name.replace("-", "").replace("_", "").isalnum() or len(name) > 80:
        raise BadRequest("A channel name is letters, digits, - and _.", code="channel")
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    cfg = validate(kind, body.config)
    config = cfg.model_dump(exclude_defaults=True, mode="json")
    before, _ = await svc.store.channels.put(
        app.id, kind, name, agent=body.agent, enabled=body.enabled, config=config
    )
    await audit.record(
        request,
        a,
        "channel.put",
        target=f"{kind}/{name}",
        before={"agent": before.agent, "enabled": before.enabled, "config": before.config}
        if before
        else None,
        after={"agent": body.agent, "enabled": body.enabled, "config": config},
    )
    rows = await svc.channels.view(app, base_url(request, svc))
    return next(r for r in rows if r["kind"] == kind and r["name"] == name)


@admin.delete("/{kind}/{name}", status_code=204)
async def delete_channel(kind: Kind, name: str, svc: Svc, a: Admin, request: Request) -> None:
    """Delete a channel and its threads (the conversations stay).

    Raises:
        NotFound: No such channel.
    """
    if not await svc.store.channels.delete(a.application_id, kind, name):
        raise NotFound()
    await audit.record(request, a, "channel.delete", target=f"{kind}/{name}")


@admin.post("/{kind}/{name}/test")
async def test_channel(kind: Kind, name: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """Check a channel without sending anything.

    Configuration, secrets, grant, agent, and a login or token at its provider.

    Returns:
        ok and the checks.

    Raises:
        NotFound: No such channel.
    """
    app = await svc.store.applications.get(a.application_id)
    rec = await svc.store.channels.get(a.application_id, kind, name)
    if app is None or rec is None:
        raise NotFound()
    checks = await svc.channels.test(app, rec)
    return {"ok": all(c["ok"] for c in checks), "checks": checks}
