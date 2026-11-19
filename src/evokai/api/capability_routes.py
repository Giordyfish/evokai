"""An application's capability credentials: the keys of web search, integrations and sandboxes.

Secrets are write-only: the view says which values are set (by the application, or by the
deployment's environment as a fallback), never a secret itself. Every change is audited without
its values.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.core.errors import AppError, NotFound
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


class CredentialValues(BaseModel):
    """A provider's values: a non-empty value replaces, an empty one keeps, null clears."""

    values: dict[str, str | None] = Field(default_factory=dict, max_length=10)


@router.get("/capability-credentials")
async def list_credentials(svc: Svc, a: Admin) -> dict[str, Any]:
    """Every provider, with what is set (never a secret).

    Returns:
        ``items``: provider, the capabilities that use it, its fields and their state.
    """
    from evokai.capabilities import credentials

    return {"items": await credentials.view(svc.store, a.application_id)}


@router.put("/capability-credentials/{provider}")
async def put_credentials(
    provider: str, body: CredentialValues, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Set a provider's values for the application.

    Returns:
        The provider's item, as listed.

    Raises:
        NotFound: Unknown provider.
        AppError: A field the provider does not have (400).
    """
    from evokai.capabilities import credentials

    if provider not in credentials.PROVIDERS:
        raise NotFound()
    try:
        names = await credentials.save(
            svc.store, svc.keyring, a.application_id, provider, body.values
        )
    except ValueError as e:
        raise AppError(str(e), code="bad_request", status=400) from e
    await audit.record(
        request,
        a,
        "capability_credentials.set",
        target=provider,
        after={"set": names, "changed": sorted(body.values)},
    )
    items = await credentials.view(svc.store, a.application_id)
    return next(x for x in items if x["provider"] == provider)


@router.delete("/capability-credentials/{provider}")
async def delete_credentials(
    provider: str, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Remove a provider's values for the application (the deployment's fallback stays).

    Returns:
        Whether there were any.

    Raises:
        NotFound: Unknown provider.
    """
    from evokai.capabilities import credentials

    if provider not in credentials.PROVIDERS:
        raise NotFound()
    deleted = await svc.store.capability_credentials.delete(a.application_id, provider)
    await audit.record(request, a, "capability_credentials.delete", target=provider)
    return {"deleted": deleted}
