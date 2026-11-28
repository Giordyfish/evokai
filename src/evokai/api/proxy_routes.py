"""The gateway proxy's public files, served by the API (the proxy itself runs as ``evokai proxy``).

- ``GET {base}/proxy.pac``: the PAC file PCs are configured with: the AI hosts of the providers
  enabled on active gateways (and served by this build's adapters) and the check host go to
  ``EVOKAI_PROXY_PUBLIC_ADDRESS``, everything else direct. 404 until that address is set.
- ``GET {base}/proxy/ca.crt``: the root to distribute (``?format=der`` for DER), read from
  ``EVOKAI_PROXY_CA_CERT``. The API never reads the CA's key.

Both are public: they hold nothing secret. They belong to the gateway module (mounted with its
``require_module`` dependency: 404 ``module_disabled`` while it is off). Not in the OpenAPI
document: their contract is the PAC format and X.509.
"""

from __future__ import annotations

import time
from typing import Any

from cryptography.hazmat.primitives import serialization
from fastapi import APIRouter, Request
from starlette.responses import Response

from evokai.core.errors import NotFound
from evokai.db.kinds import GATEWAY
from evokai.gateway.proxy import ca, catalogue
from evokai.gateway.proxy.adapters import providers_with_adapters

router = APIRouter(include_in_schema=False)

PAC_TTL = 30
"""Seconds the API keeps the list of enabled providers."""
_cache: dict[str, Any] = {}


async def _enabled(svc: Any) -> list[str]:
    """The catalogue providers served (cached for ``PAC_TTL`` seconds).

    Returns:
        The providers.
    """
    hit = _cache.get("providers")
    if hit is not None and hit[0] > time.monotonic():
        return hit[1]
    enabled: set[str] = set()
    for app in await svc.store.applications.all(GATEWAY):
        if app.status == "active":
            enabled.update((app.gateway or {}).get("providers") or [])
    served = catalogue.served(enabled, providers_with_adapters())
    _cache["providers"] = (time.monotonic() + PAC_TTL, served)
    return served


@router.get("/proxy.pac")
async def proxy_pac(request: Request) -> Response:
    """The PAC file.

    Returns:
        ``application/x-ns-proxy-autoconfig``.

    Raises:
        NotFound: No public address of the proxy is configured.
    """
    svc = request.app.state.services
    address = (svc.settings.proxy_public_address or "").strip()
    if not address:
        raise NotFound("The gateway proxy is not configured.", code="proxy_not_configured")
    hosts = catalogue.intercepted(await _enabled(svc))
    text = catalogue.pac(hosts, address, fallback=svc.settings.proxy_pac_fallback)
    return Response(
        text,
        media_type="application/x-ns-proxy-autoconfig",
        headers={"cache-control": "max-age=300"},
    )


@router.get("/proxy/ca.crt")
async def proxy_ca(request: Request, format: str = "pem") -> Response:
    """The root certificate to distribute.

    Returns:
        PEM (``application/x-pem-file``) or DER (``application/pkix-cert``).

    Raises:
        NotFound: No CA certificate is configured.
    """
    svc = request.app.state.services
    if not svc.settings.proxy_ca_cert:
        raise NotFound("The gateway proxy is not configured.", code="proxy_not_configured")
    try:
        root = ca.root_of(ca.material(svc.settings.proxy_ca_cert, "EVOKAI_PROXY_CA_CERT"))
    except ca.CaError as e:
        raise NotFound(
            "The gateway proxy's certificate cannot be read.", code="proxy_not_configured"
        ) from e
    if format == "der":
        return Response(
            root.public_bytes(serialization.Encoding.DER),
            media_type="application/pkix-cert",
            headers={"content-disposition": 'attachment; filename="evokai-proxy-root.cer"'},
        )
    return Response(
        root.public_bytes(serialization.Encoding.PEM),
        media_type="application/x-pem-file",
        headers={"content-disposition": 'attachment; filename="evokai-proxy-root.crt"'},
    )
