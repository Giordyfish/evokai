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
from evokai.api.deps import admin_app, console_login, services
from evokai.api.server_routes import TokenRequest, mint
from evokai.assistant import activation
from evokai.assistant import bootstrap as shipped
from evokai.assistant.delegation import mint_delegation
from evokai.core.errors import BadRequest, Conflict, Forbidden, NotFound
from evokai.identity.console import ConsoleLogin
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


# ── the assistant: state, activation and tries ──────────────────────────────────────────────────


class Activate(BaseModel):
    """The model the assistant should answer with."""

    model: str = Field(min_length=3, max_length=200)
    acceptNotRecommended: bool = False
    """The owner read the second disclaimer: the model does not meet every requirement."""


class Try(BaseModel):
    """The model to run the assistant's eval suite on."""

    model: str = Field(min_length=3, max_length=200)


async def _owned_assistant(svc: Services, a: AppPrincipal) -> Any:
    """The assistant's system application, when the caller acts on it as a person.

    Returns:
        The application.

    Raises:
        Forbidden: The console assistant asked (``person``), or the caller's application is not
            the assistant's (``system_app``: select it in the console).
        NotFound: The assistant is off.
    """
    if a.via:
        raise Forbidden("A person activates the assistant in the console.", code="person")
    app = await activation.system_app(svc)
    if app is None:
        raise NotFound("The assistant is off in this deployment.", code="assistant_off")
    if a.application_id != app.id:
        raise Forbidden(
            "Activate the assistant from its own application, as its owner.", code="system_app"
        )
    return app


def _model_ok(svc: Services, model: str) -> str:
    """A model the deployment allows that calls tools.

    Returns:
        The model.

    Raises:
        BadRequest: Not allowed, or a model that calls no tools (400 ``model``).
    """
    if not svc.catalog.allowed(model) or not activation.supports_tools(model):
        raise BadRequest(f"Model not allowed for the assistant: {model}", code="model")
    return model


@router.get("/assistant")
async def assistant_state(svc: Svc, _a: Admin) -> dict[str, Any]:
    """The built-in assistant's state.

    Whether it is active, its model, the requirements a recommended model meets and the
    candidates with their measured numbers (shares, never amounts).

    Returns:
        available, active, model, applicationId, locked, updateAvailable, requirements,
        candidates, trial.
    """
    return await activation.state(svc)


@router.post("/assistant/activate")
async def activate_assistant(
    body: Activate, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Pick the assistant's model (owner of its application).

    Returns:
        ok and the model.

    Raises:
        BadRequest: A model that does not meet every requirement without ``acceptNotRecommended``
            (400 ``not_recommended_unconfirmed``).
    """
    app = await _owned_assistant(svc, a)
    model = _model_ok(svc, body.model)
    recommended = activation.meets(activation.measured(app, model), model)
    if not recommended and not body.acceptNotRecommended:
        raise BadRequest(
            "This model does not meet every requirement: confirm the second disclaimer.",
            code="not_recommended_unconfirmed",
        )
    before = (app.defaults or {}).get(shipped.MODEL_KEY)
    await activation.activate(svc, app, model)
    await audit.record(
        request,
        a,
        "assistant.activate",
        target=model,
        before={"model": before},
        after={"model": model, "recommended": recommended},
    )
    return {"ok": True, "model": model, "recommended": recommended}


@router.post("/assistant/try", status_code=202)
async def try_assistant_model(body: Try, svc: Svc, a: Admin) -> dict[str, Any]:
    """Run the assistant's eval suite on one model (owner), against the recorded admin MCP server.

    Returns:
        runId.

    Raises:
        Conflict: A try is going (409 ``try_running``).
    """
    app = await _owned_assistant(svc, a)
    model = _model_ok(svc, body.model)
    if await svc.store.evals.running(app.id, shipped.AGENT, activation.SUITE):
        raise Conflict("A try of the assistant is going.", code="try_running")
    run = await activation.start_trial(svc, app, model, a.key_id)
    return {"runId": run.id}


# ── the console's assistant session ─────────────────────────────────────────────────────────────

console = APIRouter(prefix="/v1/console", tags=["console"])


class SessionBody(BaseModel):
    """The conversation to continue (a token refresh); omitted = a new one."""

    conversationId: str | None = Field(default=None, max_length=64)


def _login_id(login: ConsoleLogin) -> str:
    return {"sso": login.user_id, "adm": login.key_id}.get(login.kind) or "operator"


@console.post("/assistant/session")
async def assistant_session(
    svc: Svc, a: Admin, request: Request, body: SessionBody | None = None
) -> dict[str, Any]:
    """A session token for the console assistant, acting on the selected application.

    It needs a console login (the cookie, its header and the application it acts on): its
    forwarded credential is a delegation token for that application with the login's role, which
    the admin MCP server checks against the live login on every call. The session belongs to the
    assistant's system application, for the subject ``console:<login>``, with browser speech.

    Returns:
        What minting a session token returns, and applicationId (the assistant's).

    Raises:
        Forbidden: A key or the assistant itself instead of a console login (``console_required``).
        NotFound: The assistant is off.
    """
    if not a.console or a.via:
        raise Forbidden("The assistant is opened from the console.", code="console_required")
    login = await console_login(request)
    system = await activation.system_app(svc)
    if system is None:
        raise NotFound("The assistant is off in this deployment.", code="assistant_off")
    credential = mint_delegation(
        svc.keyring,
        login,
        a.application_id,
        a.role,  # ty: ignore[invalid-argument-type]
        ttl=svc.settings.session_token_max_ttl,
    )
    token = TokenRequest(
        subject=f"console:{_login_id(login)}",
        agent=shipped.AGENT,
        conversationId=body.conversationId if body else None,
        forwardCredential=credential,
        voiceEngine="browser",
        ttlSeconds=svc.settings.session_token_max_ttl,
        tags=["evokai:console"],
    )
    who = AppPrincipal(
        application_id=system.id, kind="srv", key_id="console-assistant", actor=a.actor
    )
    minted = await mint(token, svc, who)
    return {**minted, "applicationId": system.id}
