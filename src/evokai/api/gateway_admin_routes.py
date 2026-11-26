"""Gateways for coding tools, managed apart from applications (``/v1/admin/gateways``).

Callers: the operator key, or a console login. A deployment owner creates gateways; on a gateway,
the roles are those of an application with the same slug: viewers read, operators manage people
(their personal keys), owners change the gateway and its policy.

A gateway's policy (security and data protection) is a policy like an application's, with
versions, tests and copies: the console edits it through the application routes
(``/v1/admin/security``, ``/v1/admin/policy/...``) with ``X-Evokai-Application`` naming the
gateway, which are the only application routes a gateway answers to; this API's ``PATCH`` saves it
too, through the same path. See ``docs/gateway.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from evokai import audit, policies
from evokai.api.deps import console_login, services
from evokai.core.errors import BadRequest, Conflict, Forbidden, NotFound, Unauthorized
from evokai.core.security import GATEWAY_KIND, generate_gateway_key
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.db.repositories import GATEWAY
from evokai.gateway.checks import initial_policy
from evokai.guard import SecurityPolicy
from evokai.identity import console
from evokai.identity.auth import bearer
from evokai.identity.roles import authorize
from evokai.protection import DataProtection
from evokai.services import Services

router = APIRouter(prefix="/v1/admin/gateways", tags=["gateways"])
one = APIRouter(prefix="/v1/admin/gateways/{gateway_id}", tags=["gateways"])
"""The routes of one gateway (its id or slug in the path)."""
Svc = Annotated[Services, Depends(services)]
Provider = Literal["anthropic", "openai"]


async def gateway_login(request: Request) -> console.ConsoleLogin:
    """The caller of a gateway route: the operator key, or a console login.

    Returns:
        The login (the operator key stands for a deployment owner).

    Raises:
        Unauthorized: Another key (an application's keys never manage gateways), or no login.
    """
    svc = services(request)
    token = bearer(request.headers.get("authorization"))
    if token:
        if console.is_operator_key(token, svc.settings.operator_key):
            return console.ConsoleLogin("op", None, 0)
        raise Unauthorized(
            "Gateways are managed with the operator key or a console login.", code="operator_key"
        )
    return await console_login(request)


Login = Annotated[console.ConsoleLogin, Depends(gateway_login)]


def deployment_owner(login: Login) -> console.ConsoleLogin:
    """A deployment owner (who creates gateways), checked before the body is read.

    Returns:
        The login.

    Raises:
        Forbidden: Another role.
    """
    if not login.deployment_owner:
        raise Forbidden("Only the deployment's owners create gateways.", code="role")
    return login


Owner = Annotated[console.ConsoleLogin, Depends(deployment_owner)]


@dataclass(frozen=True)
class Access:
    """A gateway and the caller's role on it."""

    gateway: m.Application
    login: console.ConsoleLogin
    role: str


async def gateway_access(gateway_id: str, request: Request, login: Login) -> Access:
    """The gateway a route names (by id or slug), if the caller has a role that allows the route.

    Returns:
        The access.

    Raises:
        NotFound: No such gateway, or one the caller may not see.
    """
    svc = services(request)
    gw = await svc.store.applications.get(gateway_id)
    if gw is None or gw.kind != GATEWAY:
        gw = await svc.store.applications.by_slug(gateway_id, kind=GATEWAY)
    role = login.role_for(gw) if gw is not None else None
    if gw is None or role is None:
        raise NotFound("Unknown gateway.", code="unknown_gateway")
    authorize(request, role, svc.settings.base_path)
    return Access(gw, login, role)


Gw = Annotated[Access, Depends(gateway_access)]


def gateway_view(gw: m.Application, role: str | None = None, people: int | None = None) -> dict:
    """A gateway as the API shows it.

    Returns:
        id, slug, name, status, providers, allowedModels, tokensPerDay, tokensPerMonth,
        createdAt, and the caller's role and the count of active people when known.
    """
    g = gw.gateway or {}
    out: dict[str, Any] = {
        "id": gw.id,
        "slug": gw.slug,
        "name": gw.name,
        "status": gw.status,
        "providers": list(g.get("providers") or []),
        "allowedModels": list(gw.allowed_models or []),
        "tokensPerDay": g.get("tokens_per_day"),
        "tokensPerMonth": g.get("tokens_per_month"),
        "createdAt": gw.created_at.isoformat(),
    }
    if role is not None:
        out["role"] = role
    if people is not None:
        out["people"] = people
    return out


async def _people(svc: Services, gateway_id: str) -> list[m.ApiKey]:
    return [k for k in await svc.store.applications.keys(gateway_id) if k.kind == GATEWAY_KIND]


@router.get("")
async def list_gateways(svc: Svc, login: Login) -> dict[str, Any]:
    """The gateways the caller can see.

    Returns:
        items (with the caller's role and the count of active people), and whether the caller
        may create gateways (a deployment owner).
    """
    items = []
    for gw in await svc.store.applications.all(kind=GATEWAY):
        role = login.role_for(gw)
        if role is None:
            continue
        people = sum(1 for k in await _people(svc, gw.id) if k.revoked_at is None)
        items.append(gateway_view(gw, role, people))
    return {"items": items, "canCreate": login.deployment_owner}


class NewGateway(BaseModel):
    """A new gateway."""

    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=120)
    providers: list[Provider] = Field(min_length=1)
    """The providers its people may reach: ``anthropic`` (Claude Code), ``openai`` (Codex)."""
    allowed_models: list[str] = Field(default_factory=list, max_length=50)
    """Model names or globs (``claude-*``); none: every model of the enabled providers."""


@router.post("", status_code=201)
async def create_gateway(
    body: NewGateway, svc: Svc, login: Owner, request: Request
) -> dict[str, Any]:
    """Create a gateway (deployment owners). It starts with a policy fit for coding tools.

    Returns:
        The gateway.

    Raises:
        Conflict: The slug is taken (by a gateway or an application).
    """
    if await svc.store.applications.by_slug(body.slug, kind=None):
        raise Conflict("A gateway or an application with this slug exists.", code="slug_taken")
    gw = await svc.store.applications.create(
        body.slug,
        body.name,
        by=login.actor,
        kind=GATEWAY,
        gateway={"providers": sorted(set(body.providers))},
        allowed_models=body.allowed_models,
        security=initial_policy(),
    )
    await audit.record(
        request,
        login,
        "gateway.create",
        application_id=gw.id,
        target=gw.slug,
        after=body.model_dump(),
    )
    return gateway_view(gw, "owner", 0)


@one.get("")
async def get_gateway(svc: Svc, a: Gw) -> dict[str, Any]:
    """A gateway.

    Returns:
        The gateway, with its data protection profile.
    """
    people = sum(1 for k in await _people(svc, a.gateway.id) if k.revoked_at is None)
    return {
        **gateway_view(a.gateway, a.role, people),
        "dataProtection": a.gateway.data_protection,
    }


class GatewayChange(BaseModel):
    """A change of a gateway (unset fields stay as they are)."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    status: Literal["active", "disabled"] | None = None
    """``disabled``: every person's requests are refused (401) until it is active again."""
    providers: list[Provider] | None = Field(default=None, min_length=1)
    allowed_models: list[str] | None = Field(default=None, max_length=50)
    tokens_per_day: int | None = Field(default=None, ge=0)
    """Input and output tokens per person per day (UTC); 0 removes the cap."""
    tokens_per_month: int | None = Field(default=None, ge=0)
    """Input and output tokens per person per calendar month (UTC); 0 removes the cap."""
    security: SecurityPolicy | None = None
    """A new security policy (a new version of the gateway's policy, after its tests)."""
    data_protection: dict[str, Any] | None = None
    """A new data protection profile (``{}`` removes it)."""
    note: str | None = Field(default=None, max_length=500)
    """Kept with the new policy version."""
    force: bool = False
    """Save the policy even when its tests fail."""


@one.patch("")
async def patch_gateway(body: GatewayChange, svc: Svc, a: Gw, request: Request) -> dict[str, Any]:
    """Change a gateway (owners): its settings, and its policy as a new version.

    Returns:
        The gateway.

    Raises:
        BadRequest: An invalid data protection profile.
    """
    gw = a.gateway
    before = gateway_view(gw)
    fields: dict[str, Any] = {}
    settings = dict(gw.gateway or {})
    if body.name is not None:
        fields["name"] = body.name
    if body.status is not None:
        fields["status"] = body.status
    if body.allowed_models is not None:
        fields["allowed_models"] = body.allowed_models
    if body.providers is not None:
        settings["providers"] = sorted(set(body.providers))
    for name in ("tokens_per_day", "tokens_per_month"):
        value = getattr(body, name)
        if value is not None:
            settings[name] = value or None
    if settings != (gw.gateway or {}):
        fields["gateway"] = settings
    if fields:
        await svc.store.applications.update(gw.id, by=a.login.actor, **fields)
    parts = tuple(
        p
        for p, v in (("security", body.security), ("data_protection", body.data_protection))
        if v is not None
    )
    if parts:
        try:
            dp = (
                (DataProtection.from_stored(body.data_protection) if body.data_protection else None)
                if body.data_protection is not None
                else DataProtection.from_stored(gw.data_protection)
                if gw.data_protection
                else None
            )
        except ValueError as e:
            raise BadRequest(f"Invalid data protection profile: {e}", code="data_protection") from e
        content = policies.PolicyContent(
            security=body.security or (SecurityPolicy.model_validate(gw.security or {})),
            data_protection=dp,
        )
        await policies.save_application(
            svc, gw, content, by=a.login.actor, note=body.note, force=body.force, parts=parts
        )
    svc.gateway.forget(gw.id)
    if body.status is not None and body.status != gw.status:
        await svc.auth.forget_cached_keys()
    fresh = await svc.store.applications.get(gw.id)
    after = gateway_view(fresh) if fresh else None
    await audit.record(
        request,
        a.login,
        "gateway.update",
        application_id=gw.id,
        target=gw.slug,
        before=before,
        after={**(after or {}), **({"policy": list(parts)} if parts else {})},
    )
    return gateway_view(fresh or gw, a.role)


@one.get("/people")
async def list_people(svc: Svc, a: Gw) -> dict[str, Any]:
    """The people of a gateway: one personal key each (never its secret).

    Returns:
        items: id, label, prefix, createdAt, lastUsedAt, revoked.
    """
    return {
        "items": [
            {
                "id": k.id,
                "label": k.label,
                "prefix": f"evk_gw_{k.prefix}",
                "createdAt": k.created_at.isoformat(),
                "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
                "revoked": k.revoked_at is not None,
            }
            for k in await _people(svc, a.gateway.id)
        ]
    }


class NewPerson(BaseModel):
    """A person of a gateway."""

    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=120)
    """Their name or e-mail, as usage and events show it."""


@one.post("/people", status_code=201)
async def add_person(body: NewPerson, svc: Svc, a: Gw, request: Request) -> dict[str, str]:
    """Give a person their personal gateway key (operators). The key is shown only now.

    Returns:
        id, label, key.
    """
    key = generate_gateway_key()
    key_id = await svc.store.applications.add_key(
        a.gateway.id, key.kind, key.prefix, key.secret_hash, body.label.strip()
    )
    await audit.record(
        request,
        a.login,
        "gateway.key_create",
        application_id=a.gateway.id,
        target=f"evk_gw_{key.prefix}",
        after={"label": body.label.strip()},
    )
    return {"id": key_id, "label": body.label.strip(), "key": key.value}


@one.delete("/people/{key_id}")
async def revoke_person(key_id: str, svc: Svc, a: Gw, request: Request) -> dict[str, bool]:
    """Revoke a person's gateway key (operators): their requests are refused at once.

    Returns:
        ok.

    Raises:
        NotFound: Unknown or already revoked.
    """
    row = await svc.store.applications.key(key_id)
    if row is None or row.kind != GATEWAY_KIND or row.application_id != a.gateway.id:
        raise NotFound("Unknown key.", code="unknown_key")
    if not await svc.store.applications.revoke_key(a.gateway.id, key_id):
        raise NotFound("Unknown key.", code="unknown_key")
    await svc.auth.forget_cached_keys()
    await audit.record(
        request,
        a.login,
        "gateway.key_revoke",
        application_id=a.gateway.id,
        target=f"evk_gw_{row.prefix}",
        before={"label": row.label},
    )
    return {"ok": True}


@one.get("/usage")
async def gateway_usage(
    svc: Svc, a: Gw, days: Annotated[int, Query(ge=1, le=400)] = 30
) -> dict[str, Any]:
    """Tokens through a gateway, per person and per model, and its latest requests.

    Never an amount: the traffic is people's own subscriptions. Requests hold no content.

    Returns:
        days, people, models, outcomes, recent (timings, sizes, outcome per request).
    """
    since = utcnow() - timedelta(days=days)
    usage = await svc.store.gateway.usage(a.gateway.id, since)
    recent = [
        {
            "at": r.created_at.isoformat(),
            "person": r.person,
            "provider": r.provider,
            "model": r.model,
            "outcome": r.outcome,
            "control": r.control,
            "status": r.status,
            "inputTokens": r.input_tokens,
            "outputTokens": r.output_tokens,
            "requestBytes": r.request_bytes,
            "firstByteMs": r.first_byte_ms,
            "durationMs": r.duration_ms,
        }
        for r in await svc.store.gateway.recent(a.gateway.id)
    ]
    return {"days": days, **usage, "recent": recent}
