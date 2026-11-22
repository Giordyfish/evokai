"""The built-in assistant and system applications: unlock, reset (owner, a person in the console).

A system application is locked: every change is refused (``api.deps.admin_app``) until an owner
unlocks it here, typing its slug. A reset applies the shipped definition again and locks it.
Neither route is a tool of the admin MCP server, and the assistant's delegation token is refused.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.assistant import bootstrap as shipped
from evokai.core.errors import BadRequest, Conflict, Forbidden, NotFound
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


class Unlock(BaseModel):
    """The application's slug, typed by the person who unlocks it."""

    confirm: str = Field(max_length=60)


async def _system_app(svc: Services, a: AppPrincipal) -> Any:
    """The caller's application, when it is a system application changed by a person.

    Returns:
        The application.

    Raises:
        Forbidden: The console assistant asked (a person does this in the console).
        NotFound: The application is gone.
        Conflict: Not a system application (409 ``not_system``).
    """
    if a.via:
        raise Forbidden(
            "A person unlocks or resets a system application in the console.", code="person"
        )
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    if not app.system:
        raise Conflict("This is not a system application.", code="not_system")
    return app


@router.post("/application/unlock")
async def unlock_application(body: Unlock, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Unlock a system application (owner): its changes are then the owner's to make.

    Shipped updates are no longer applied while it is unlocked (they are recorded instead).

    Returns:
        ok.

    Raises:
        BadRequest: The typed slug is not the application's (400 ``confirm``).
    """
    app = await _system_app(svc, a)
    if body.confirm != app.slug:
        raise BadRequest("Type the application's slug to confirm.", code="confirm")
    await svc.store.applications.update(app.id, locked=False)
    await audit.record(
        request, a, "application.unlock", target=app.slug, before={"locked": app.locked}
    )
    return {"ok": True}


@router.post("/application/reset")
async def reset_application(svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Apply a system application's shipped definition again, and lock it (owner).

    Returns:
        ok.

    Raises:
        Conflict: A system application this version of Evokai does not ship.
    """
    app = await _system_app(svc, a)
    if app.slug != shipped.SLUG:
        raise Conflict("This version of Evokai does not ship this application.", code="not_system")
    before = await svc.store.agents.active_all(app.id)
    model = (app.defaults or {}).get(shipped.MODEL_KEY)
    await shipped.apply_shipped(svc.store, svc.keyring, svc.settings, model=model)
    for agent in before:
        svc.agents.invalidate(app.id, agent.name)
    svc.mcp.forget(app.id)
    await audit.record(request, a, "application.reset", target=app.slug)
    return {"ok": True}
