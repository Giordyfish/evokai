"""The signed-in person's console preferences: language, appearance and accessibility.

Kept per console identity (single sign-on user, admin key or the operator), so they follow the
person across browsers. Console cookie and ``X-Evokai-Console`` header, like every console route;
every change is in the audit log.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from evokai import audit
from evokai.api.deps import console_login, services
from evokai.identity import console
from evokai.identity.preferences import DEFAULTS, Preferences, identity_of, read
from evokai.services import Services

router = APIRouter(tags=["console"])
Svc = Annotated[Services, Depends(services)]
ConsoleUser = Annotated[console.ConsoleLogin, Depends(console_login)]


def _view(prefs: Preferences, updated_at: Any) -> dict[str, Any]:
    return {
        "preferences": prefs.document(),
        "updatedAt": updated_at.isoformat() if updated_at is not None else None,
    }


@router.get("/v1/console/me/preferences")
async def get_preferences(svc: Svc, login: ConsoleUser) -> dict[str, Any]:
    """The preferences of the signed-in console identity.

    Returns:
        preferences (the defaults when none were saved) and updatedAt (null when never saved).
    """
    row = await svc.store.console_preferences.get(identity_of(login))
    if row is None:
        return _view(DEFAULTS, None)
    return _view(read(row.value), row.updated_at)


@router.put("/v1/console/me/preferences")
async def put_preferences(
    body: Preferences, request: Request, svc: Svc, login: ConsoleUser
) -> dict[str, Any]:
    """Save the preferences of the signed-in console identity (the whole document).

    Unknown fields and values outside each field's choices are refused (400, ``bad_request``).

    Returns:
        preferences and updatedAt.
    """
    who = identity_of(login)
    row = await svc.store.console_preferences.get(who)
    before = read(row.value).document() if row is not None else None
    after = body.document()
    if row is not None and before == after:
        return _view(body, row.updated_at)
    saved = await svc.store.console_preferences.put(who, after)
    changed = {k: v for k, v in after.items() if before is None or before.get(k) != v}
    await audit.record(
        request,
        login,
        "preferences.update",
        target=login.actor,
        before={k: before[k] for k in changed} if before is not None else None,
        after=changed,
    )
    return _view(body, saved.updated_at)


@router.delete("/v1/console/me/preferences")
async def reset_preferences(request: Request, svc: Svc, login: ConsoleUser) -> dict[str, Any]:
    """Forget the saved preferences of the signed-in console identity.

    Returns:
        The defaults, with updatedAt null.
    """
    if await svc.store.console_preferences.delete(identity_of(login)):
        await audit.record(request, login, "preferences.reset", target=login.actor)
    return _view(DEFAULTS, None)
