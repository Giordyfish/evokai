"""Settings, Modules (deployment owners): which modules are on (``evokai.core.modules``).

``EVOKAI_MODULES`` pins them; then the console shows them read-only and a change is refused (409
``modules_pinned``). Otherwise the owner switches them here, with an explicit confirmation and an
audit row; the change applies at once on this instance (routes, console sections, background
jobs) and within seconds on the others, without a restart. Data of a module turned off stays.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from evokai import audit, module_jobs
from evokai.api.console_routes import deployment_actor
from evokai.api.deps import operator, services
from evokai.core import modules
from evokai.core.errors import BadRequest, Conflict
from evokai.services import Services

router = APIRouter(tags=["modules"])
Svc = Annotated[Services, Depends(services)]
Owner = [Depends(operator)]
PATH = "/v1/console/modules"


class ModulesChoice(BaseModel):
    """The modules to turn on."""

    model_config = ConfigDict(extra="forbid")
    enabled: list[str] = Field(max_length=len(modules.MODULES))
    """``applications`` and/or ``gateway``; at least one."""
    confirm: bool = False
    """Must be true: the console asks the owner first (a module turned off hides its routes and
    stops its jobs for everyone)."""


async def _view(svc: Services) -> dict[str, Any]:
    on = await modules.enabled(svc)
    return {
        "modules": list(modules.MODULES),
        "enabled": sorted(on),
        "pinned": modules.pinned(svc.settings) is not None,
    }


@router.get(PATH, dependencies=Owner)
async def get_modules(svc: Svc) -> dict[str, Any]:
    """The modules and which are on (deployment owners).

    Returns:
        modules (every module), enabled (those on), pinned (set by ``EVOKAI_MODULES``).
    """
    return await _view(svc)


@router.put(PATH, dependencies=Owner)
async def put_modules(body: ModulesChoice, svc: Svc, request: Request) -> dict[str, Any]:
    """Switch the modules (deployment owners), with an audit row.

    Returns:
        The modules, as ``GET`` shows them.

    Raises:
        Conflict: ``EVOKAI_MODULES`` pins them (``modules_pinned``).
        BadRequest: No confirmation (``confirm_required``), no module on (``no_module``) or an
            unknown one (``unknown_module``).
    """
    if modules.pinned(svc.settings) is not None:
        raise Conflict(
            "The modules are set by the configuration (EVOKAI_MODULES).", code="modules_pinned"
        )
    if not body.confirm:
        raise BadRequest("Confirm the change of modules.", code="confirm_required")
    try:
        wanted = modules.parse(body.enabled)
    except modules.ModulesError as e:
        code = "no_module" if str(e) == modules.NONE_ON else "unknown_module"
        raise BadRequest(str(e), code=code) from None
    before = await modules.enabled(svc)
    actor = deployment_actor(request, svc)
    on = await modules.save(svc.store, wanted, actor.actor)
    if on != before:
        await audit.record(
            request,
            actor,
            "deployment.modules",
            target="modules",
            before={"enabled": sorted(before)},
            after={"enabled": sorted(on)},
        )
    await module_jobs.apply(svc, on)
    return await _view(svc)
