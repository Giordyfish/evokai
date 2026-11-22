"""Data protection requests about a subject: export, erase, revoke their sessions.

With the application's server key (its backend relays a person's request) or a console login (an
operator acting on one): the subject travels in the body, never in the address, so it stays out of
access logs. A console login gets no amounts in the export.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai import audit, privacy
from evokai.api.deps import server_app, services
from evokai.assistant.privacy import own_subject
from evokai.core.errors import Conflict, Forbidden
from evokai.db.repositories import Who
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(prefix="/v1/privacy", tags=["privacy"])
Svc = Annotated[Services, Depends(services)]
App = Annotated[AppPrincipal, Depends(server_app)]


class SubjectRequest(BaseModel):
    """A request about one subject of the application."""

    subject: str = Field(min_length=1, max_length=200)


@router.post("/export")
async def export(body: SubjectRequest, svc: Svc, app: App, request: Request) -> dict[str, Any]:
    """Everything Evokai keeps about a subject, as JSON (right of access and portability).

    Conversations with messages and turns, memories, usage, security events (masked), bug reports
    and consents. Amounts only with a server or admin key.

    On a system application a person exports only their own data (403 ``own_data``).

    Returns:
        The export.

    Raises:
        Forbidden: Another person's data on a system application.
    """
    mine = await own_subject(svc, app)
    if mine is not None and body.subject != mine:
        raise Forbidden("On this application you export only your own data.", code="own_data")
    data = await privacy.export_subject(
        svc.store, Who(app.application_id, body.subject), amounts=not app.console
    )
    await audit.record(request, app, "privacy.export", target=_subject(svc, app, body.subject))
    return data


def _subject(svc: Services, app: AppPrincipal, subject: str) -> str:
    """A subject as the audit log names it: a keyed hash, never the id itself.

    Returns:
        ``subject:<hash>``.
    """
    key = privacy.subject_key(svc.keyring.signing_key, app.application_id, subject)
    return f"subject:{key[:16]}"


@router.post("/erase")
async def erase(body: SubjectRequest, svc: Svc, app: App, request: Request) -> dict[str, Any]:
    """Erase a subject (right to be forgotten).

    Conversations, messages, replay events, turns, pending confirmations, memories, bug reports,
    workflow runs, OAuth connections (revoked at the provider when it can be) and channel threads
    are deleted; usage rows and security events are de-identified, so the
    application's caps keep counting; every session token of the subject stops working. Idempotent.

    On a system application only an owner erases, and never their own conversations: the
    console assistant's conversations stay until their time to live ends (403 ``role``, 409
    ``retention``).

    Returns:
        ok and how many rows of each kind were deleted or de-identified.

    Raises:
        Forbidden: Not an owner, on a system application.
        Conflict: Your own data on a system application.
    """
    mine = await own_subject(svc, app)
    if mine is not None and app.role != "owner":
        raise Forbidden("On this application an owner erases a person's data.", code="role")
    if mine is not None and body.subject == mine:
        raise Conflict(
            "Your conversations with the assistant are kept until their time to live ends.",
            code="retention",
        )
    who = Who(app.application_id, body.subject)
    # Connections first: Google's refresh tokens are revoked at Google before they are deleted.
    revoked = await svc.connections.forget_subject(app.application_id, body.subject)
    counts, conversations = await privacy.erase_subject(svc.store, who, svc.keyring.signing_key)
    counts["connections"] = counts.get("connections", 0) + revoked
    await svc.auth.forget_revocation(app.application_id, body.subject)
    for conversation_id in conversations:
        if running := svc.turns.running(conversation_id):
            running.cancel("erased")
    await audit.record(
        request, app, "privacy.erase", target=_subject(svc, app, body.subject), after=counts
    )
    return {"ok": True, **counts}


@router.post("/revoke-sessions")
async def revoke_sessions(
    body: SubjectRequest, svc: Svc, app: App, request: Request
) -> dict[str, bool]:
    """Refuse every session token of the subject minted until now (a sign-out everywhere).

    Returns:
        ok.
    """
    await privacy.revoke_sessions(
        svc.store, Who(app.application_id, body.subject), svc.keyring.signing_key
    )
    await svc.auth.forget_revocation(app.application_id, body.subject)
    await audit.record(
        request, app, "privacy.revoke_sessions", target=_subject(svc, app, body.subject)
    )
    return {"ok": True}
