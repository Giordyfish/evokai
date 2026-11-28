"""The gateway proxy in the console (``/v1/admin/proxy``): setup values, status and settings.

- ``GET /setup`` (any gateway login): what the Connect page's Company network tab shows, the
  proxy's public address, the PAC and root certificate URLs, the providers it serves, the egress
  addresses for the providers' IP allowlists and the check page. Nothing secret.
- ``GET`` (the operator key, or a single sign-on role of operator or owner on ``*``): the status
  page, whether the proxy runs (its instances' heartbeats), the last day's PCs, people and requests,
  format alerts, refused organisations, unidentified requests, and the ``proxy`` settings.
- ``PUT /settings`` and ``DELETE /settings`` (deployment owners): the console setting ``proxy``
  (``allowed_orgs`` per provider, ``unidentified``, ``default_gateway``), which overrides the
  environment; each change writes an audit row. The proxy reads it within 30 seconds.

The proxy itself is never switched from here: ``EVOKAI_PROXY=on`` on its own process. Gateway
module routes (404 ``module_disabled`` while it is off). See ``docs/gateway-proxy.md``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from evokai import audit
from evokai.api.channel_routes import base_url
from evokai.api.deps import services
from evokai.api.gateway_admin_routes import Login, gateway_login
from evokai.core.errors import BadRequest, Forbidden
from evokai.db.kinds import GATEWAY
from evokai.gateway.proxy import catalogue, status
from evokai.gateway.proxy.service import SETTING
from evokai.identity import console
from evokai.identity.roles import at_least, authorize, highest
from evokai.services import Services

router = APIRouter(prefix="/v1/admin/proxy", tags=["gateway proxy"])
Svc = Annotated[Services, Depends(services)]

CHECK_URL = f"https://{catalogue.CHECK_HOST}"


def deployment_role(login: console.ConsoleLogin) -> str | None:
    """The caller's role on the whole deployment (the proxy is not one gateway's).

    Returns:
        ``owner`` for the operator key, the single sign-on role on ``*``, else None.
    """
    if login.kind == "op":
        return "owner"
    if login.kind == "sso":
        return highest(login.roles.get("*"))
    return None


def _egress(svc: Services) -> list[str]:
    return [x.strip() for x in (svc.settings.proxy_egress_ips or "").split(",") if x.strip()]


@router.get("/setup", dependencies=[Depends(gateway_login)])
async def proxy_setup(request: Request, svc: Svc) -> dict[str, Any]:
    """What a PC needs to go through the proxy (the Connect page's Company network tab).

    Returns:
        configured, publicAddress, pacUrl, caUrl, caDerUrl, pacFallback, providers (served),
        egressIps, checkUrl.
    """
    from evokai.api.proxy_routes import _enabled

    s = svc.settings
    base = base_url(request, svc)
    address = (s.proxy_public_address or "").strip() or None
    has_ca = bool(s.proxy_ca_cert)
    return {
        "configured": bool(address and has_ca),
        "publicAddress": address,
        "pacUrl": f"{base}/proxy.pac" if address else None,
        "caUrl": f"{base}/proxy/ca.crt" if has_ca else None,
        "caDerUrl": f"{base}/proxy/ca.crt?format=der" if has_ca else None,
        "pacFallback": s.proxy_pac_fallback,
        "providers": await _enabled(svc),
        "egressIps": _egress(svc),
        "checkUrl": CHECK_URL,
    }


async def _settings_view(svc: Services) -> dict[str, Any]:
    s = svc.settings
    environment = {
        "allowed_orgs": {k: list(v) for k, v in (s.proxy_allowed_orgs or {}).items()},
        "unidentified": s.proxy_unidentified,
        "default_gateway": s.proxy_default_gateway,
    }
    stored = await svc.store.console_settings.get(SETTING)
    effective = dict(environment)
    if isinstance(stored, dict):
        if isinstance(stored.get("allowed_orgs"), dict):
            effective["allowed_orgs"] = stored["allowed_orgs"]
        if stored.get("unidentified") in ("block", "record"):
            effective["unidentified"] = stored["unidentified"]
        if isinstance(stored.get("default_gateway"), str):
            effective["default_gateway"] = stored["default_gateway"] or None
    return {"value": effective, "environment": environment, "stored": stored is not None}


@router.get("")
async def proxy_status(svc: Svc, login: Login) -> dict[str, Any]:
    """The proxy's status page.

    Returns:
        switch (``EVOKAI_PROXY`` as this API process reads it), configured, running, instances
        (heartbeats), providers, pacFallback, lastDay (requests, people, accounts, pcs,
        unidentified, surfaces, formatAlerts, refusedOrgs), settings, gateways, catalogue, canEdit.

    Raises:
        Forbidden: Not an operator or owner of the deployment.
    """
    from evokai.api.proxy_routes import _enabled

    role = deployment_role(login)
    if not at_least(role, "operator"):
        raise Forbidden("The proxy's status needs the operator role on every gateway.", code="role")
    gws = list(await svc.store.applications.all(GATEWAY))
    names = {g.id: g.name for g in gws}
    beats = await status.heartbeats(svc.db)
    day = await status.last_day(svc.db, names)
    day["pcs"] = sum(b["pcs24h"] for b in beats if b["running"])
    s = svc.settings
    return {
        "switch": s.proxy,
        "configured": bool((s.proxy_public_address or "").strip() and s.proxy_ca_cert),
        "publicAddress": (s.proxy_public_address or "").strip() or None,
        "running": any(b["running"] for b in beats),
        "instances": beats,
        "providers": await _enabled(svc),
        "pacFallback": s.proxy_pac_fallback,
        "lastDay": day,
        "settings": await _settings_view(svc),
        "gateways": [{"id": g.id, "slug": g.slug, "name": g.name} for g in gws],
        "catalogue": list(catalogue.INTERCEPTED),
        "canEdit": login.deployment_owner,
    }


ORG = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,80}$")]


class ProxySettings(BaseModel):
    """The ``proxy`` console setting (it overrides ``EVOKAI_PROXY_*`` of the same names)."""

    model_config = ConfigDict(extra="forbid")
    allowed_orgs: dict[str, list[ORG]] = Field(default_factory=dict)
    """Per catalogue provider (``anthropic``, ``openai``, ``github``), the organisations whose
    accounts may pass; a provider not listed, or listed empty, is not checked."""
    unidentified: Literal["block", "record"] = "block"
    """``block`` (refuse with a message) or ``record`` (let through on the default gateway)."""
    default_gateway: str | None = Field(default=None, max_length=80)
    """The gateway (slug or id) of unidentified requests in ``record``."""


@router.put("/settings")
async def put_proxy_settings(
    body: ProxySettings, request: Request, svc: Svc, login: Login
) -> dict[str, Any]:
    """Set the ``proxy`` console setting (deployment owners), with an audit row.

    Returns:
        The settings, as the status page shows them.

    Raises:
        BadRequest: An unknown provider, too many organisations, or an unknown default gateway.
    """
    authorize(request, deployment_role(login), svc.settings.base_path)
    orgs: dict[str, list[str]] = {}
    for provider, values in body.allowed_orgs.items():
        if provider not in catalogue.INTERCEPTED:
            raise BadRequest(f"Unknown provider: {provider}.", code="proxy_provider")
        if len(values) > 50:
            raise BadRequest("At most 50 organisations per provider.", code="proxy_orgs")
        clean = sorted({v.strip() for v in values if v.strip()})
        if clean:
            orgs[provider] = clean
    default = (body.default_gateway or "").strip() or None
    if default is not None:
        gw = await svc.store.applications.one(default, GATEWAY)
        if gw is None:
            raise BadRequest("Unknown gateway.", code="unknown_gateway")
        default = gw.slug
    before = await _settings_view(svc)
    # An empty string, not null: the proxy reads a string as the console's choice (none here),
    # where a missing value would let EVOKAI_PROXY_DEFAULT_GATEWAY apply again.
    value = {
        "allowed_orgs": orgs,
        "unidentified": body.unidentified,
        "default_gateway": default or "",
    }
    await svc.store.console_settings.put(SETTING, value, login.actor)
    await audit.record(
        request, login, "proxy.settings", target="proxy", before=before["value"], after=value
    )
    return await _settings_view(svc)


@router.delete("/settings")
async def delete_proxy_settings(request: Request, svc: Svc, login: Login) -> dict[str, Any]:
    """Forget the console setting (deployment owners): the environment applies again.

    Returns:
        The settings that apply now.
    """
    authorize(request, deployment_role(login), svc.settings.base_path)
    before = await _settings_view(svc)
    await svc.store.console_settings.delete(SETTING)
    await audit.record(
        request, login, "proxy.settings_reset", target="proxy", before=before["value"]
    )
    return await _settings_view(svc)
