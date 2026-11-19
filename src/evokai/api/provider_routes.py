"""The provider catalog as the console reads it: providers, endpoints, processors and their sources.

No prices: amounts never reach a browser. A model the catalog cannot price is only flagged.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from evokai.api.deps import admin_app, services
from evokai.core.errors import NotFound
from evokai.identity.models import AppPrincipal
from evokai.llm import providers as pc
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["providers"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


def _entity(e: pc.Entity) -> dict[str, Any]:
    return {
        "name": e.name,
        "country": e.country,
        "controlledFrom": e.controlled_from,
        "euContractingEntity": e.eu_contracting_entity,
        "sources": e.sources,
    }


def _terms(t: pc.Terms) -> dict[str, Any]:
    return {
        "training": t.training,
        "retention": t.retention,
        "retentionDays": t.retention_days,
        "zeroRetention": t.zero_retention,
        "notes": t.notes,
        "sources": t.sources,
    }


def _endpoint(cat: pc.ProviderCatalog, e: pc.Endpoint) -> dict[str, Any]:
    geo, countries, regions = cat._place(e.place, e.region)
    return {
        "id": e.id,
        "kind": e.kind,
        "howToSelect": e.how_to_select,
        "models": e.models,
        "offered": e.offered,
        "geography": geo,
        "countries": countries,
        "regions": regions,
        "fromDeploymentRegion": e.place.from_deployment_region,
        "dataAtRest": e.data_at_rest,
        "entity": _entity(e.entity) if e.entity else None,
        "terms": _terms(e.terms) if e.terms else None,
        "declaredBy": e.declared_by,
        "connected": e.api is not None,
        "notes": e.notes,
        "sources": e.sources,
        "checked": e.checked.isoformat() if e.checked else None,
    }


def catalog_view(cat: pc.ProviderCatalog) -> dict[str, Any]:
    """The whole catalog, without prices.

    Returns:
        version, checked, coverage, providers, processors.
    """
    return {
        "version": cat.data.version,
        "checked": cat.data.checked.isoformat() if cat.data.checked else None,
        "coverage": cat.coverage(),
        "providers": [
            {
                "id": p.id,
                "title": p.title,
                "aliases": p.aliases,
                "selfHosted": p.self_hosted,
                "entity": _entity(p.entity),
                "terms": _terms(p.terms),
                "caching": {
                    "style": p.caching.style,
                    "reportsUsage": p.caching.reports_usage,
                    "notes": p.caching.notes,
                    "sources": p.caching.sources,
                },
                "rated": bool(p.prices) or p.genai_prices is not None or p.self_hosted,
                "endpoints": [_endpoint(cat, e) for e in p.endpoints],
                "notes": p.notes,
                "sources": p.sources,
                "checked": p.checked.isoformat() if p.checked else None,
                "unknowns": p.unknowns,
            }
            for p in cat.data.providers
        ],
        "processors": [
            {
                "id": x.id,
                "kind": x.kind,
                "title": x.title,
                "provider": x.provider,
                "detects": x.detects,
                "facts": _processor_facts(cat, x.id),
                "notes": x.notes,
                "unknowns": x.unknowns,
            }
            for x in cat.data.processors
        ],
    }


def _processor_facts(cat: pc.ProviderCatalog, pid: str) -> dict[str, Any] | None:
    facts = cat.processor_facts(pid)
    return facts.view() if facts is not None else None


@router.get("/providers")
async def provider_catalog(svc: Svc, a: Admin) -> dict[str, Any]:  # ruff: ignore[unused-function-argument] (auth)
    """The provider catalog: who processes what, where, under whose control, and the sources.

    Returns:
        version, checked, coverage (facts known and unknown), providers (entity, terms, caching,
        endpoints), processors (classifiers, sandbox, speech, connections, exporters).
    """
    return catalog_view(svc.catalog.providers)


@router.get("/data-protection")
async def data_protection(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's data protection profile and every external processor under it.

    Returns:
        profile, residency, empty, processors (name, kind, allowed, reasons, facts), browser (as
        declared).

    Raises:
        NotFound: The application is gone.
    """
    from evokai.protection import report

    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return await report(svc, app)
