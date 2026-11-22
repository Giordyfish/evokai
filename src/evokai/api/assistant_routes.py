"""The built-in assistant and system applications: unlock, reset (owner, a person in the console).

A system application is locked: every change is refused (``api.deps.admin_app``) until an owner
unlocks it here, typing its slug. A reset applies the shipped definition again and locks it.
Neither route is a tool of the admin MCP server, and the assistant's delegation token is refused.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from evokai import audit
from evokai.api.console_routes import conversation_subject, conversation_view
from evokai.api.deps import admin_app, console_login, services
from evokai.api.server_routes import TokenRequest, mint
from evokai.assistant import activation, privacy
from evokai.assistant import bootstrap as shipped
from evokai.assistant.delegation import mint_delegation
from evokai.core.errors import BadRequest, Conflict, Forbidden, NotFound
from evokai.core.util import utcnow
from evokai.identity.models import AppPrincipal
from evokai.identity.preferences import identity_of
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


MODEL_ID = r"^[A-Za-z0-9_.:/@-]+$"
"""A model id: letters, digits and ``_.:/@-`` only (never ``${...}``, nothing a template reads)."""


class Activate(BaseModel):
    """The model the assistant should answer with."""

    model: str = Field(min_length=3, max_length=200, pattern=MODEL_ID)
    acceptNotRecommended: bool = False
    """The owner read the second disclaimer: the model does not meet every requirement."""


class Try(BaseModel):
    """The model to run the assistant's eval suite on."""

    model: str = Field(min_length=3, max_length=200, pattern=MODEL_ID)


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
    await activation.activate(svc, app, model, by=a.key_id)
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


# ── inspection: an owner reads another person's conversation, with a reason ──────────────────────


class Inspect(BaseModel):
    """Why the owner opens another person's conversation (kept in the audit log, shown to them)."""

    reason: str = Field(min_length=20, max_length=500)

    @field_validator("reason")
    @classmethod
    def _written(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 20:
            raise ValueError("write a reason of at least 20 characters")
        return v


@router.post("/assistant/conversations/{conversation_id}/inspect")
async def inspect_conversation(
    conversation_id: str, body: Inspect, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Read another person's conversation with the assistant (owner of its application).

    Read only. The reason (20 to 500 characters) is written to the audit log with who inspected and
    the author's keyed subject, and the author's console lists it ("viewed by").

    Returns:
        The conversation, as the conversation detail returns it.

    Raises:
        NotFound: The assistant is off, or no such conversation.
    """
    app = await _owned_assistant(svc, a)
    subject = await conversation_subject(svc, app.id, conversation_id)
    if subject is None:
        raise NotFound()
    view = await conversation_view(svc, app.id, conversation_id)
    key = privacy.key_of(svc, app.id, subject)
    await audit.record(
        request,
        a,
        privacy.INSPECT,
        target=f"conversation:{conversation_id} subject:{key}",
        after={"reason": body.reason, "author": key},
    )
    return view


# ── the console's assistant session ─────────────────────────────────────────────────────────────

console = APIRouter(prefix="/v1/console", tags=["console"])


class SessionBody(BaseModel):
    """The conversation to continue (a token refresh); omitted = a new one."""

    conversationId: str | None = Field(default=None, max_length=64)


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
        Conflict: The assistant's MCP servers were changed (409 ``assistant_altered``).
    """
    if not a.console or a.via:
        raise Forbidden("The assistant is opened from the console.", code="console_required")
    login = await console_login(request)
    system = await activation.system_app(svc)
    if system is None:
        raise NotFound("The assistant is off in this deployment.", code="assistant_off")
    if not await shipped.forwards_only_to_admin_mcp(svc.store, svc.settings, system.id):
        raise Conflict(
            "The assistant's MCP servers were changed: an owner resets it in the console.",
            code="assistant_altered",
        )
    credential = mint_delegation(
        svc.keyring,
        login,
        a.application_id,
        a.role,  # ty: ignore[invalid-argument-type]
        ttl=svc.settings.session_token_max_ttl,
    )
    token = TokenRequest(
        subject=a.subject,
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


# ── what a person is told: the first-use notice, who viewed their conversations, the voice ──────


class NoticeAccept(BaseModel):
    """The version of the notice the person read."""

    version: int = Field(ge=0)


def _notice_key(login: Any) -> str:
    return f"assistant-notice:{identity_of(login)}"[:120]


@console.get("/assistant/privacy")
async def assistant_privacy(svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """What the console tells a person about the assistant, for the selected application.

    The first-use notice (its version and whether this login accepted it), how long conversations
    are kept, the voice in use (``online`` only when the selected application's and the
    assistant's data protection allow processors outside the EU, else ``device``) and every
    inspection of this person's conversations: who, when and why.

    Returns:
        noticeVersion, accepted, acceptedAt, ttlDays, voice, inspections, unseen.

    Raises:
        Forbidden: A key or the assistant itself instead of a console login.
        NotFound: The assistant is off.
    """
    if not a.console or a.via:
        raise Forbidden("The assistant is opened from the console.", code="console_required")
    login = await console_login(request)
    system = await activation.system_app(svc)
    if system is None:
        raise NotFound("The assistant is off in this deployment.", code="assistant_off")
    selected = await svc.store.applications.get(a.application_id)
    row = await svc.store.console_preferences.get(_notice_key(login))
    stored = (row.value if row else None) or {}
    key = privacy.key_of(svc, system.id, a.subject)
    entries = await svc.store.audit.query(
        system.id, action=privacy.INSPECT, target=f"subject:{key}", limit=50
    )
    seen = str(stored.get("seenAt") or "")
    inspections = [
        {
            "conversationId": e.target.split()[0].removeprefix("conversation:"),
            "by": e.actor.removeprefix("sso:"),
            "at": e.at.isoformat(),
            "reason": (e.after or {}).get("reason", ""),
        }
        for e in entries
        if (e.after or {}).get("author") == key
    ]
    voice = (
        "online"
        if privacy.online_voice_allowed(system) and privacy.online_voice_allowed(selected)
        else "device"
    )
    return {
        "noticeVersion": privacy.NOTICE_VERSION,
        "accepted": stored.get("version") == privacy.NOTICE_VERSION,
        "acceptedAt": stored.get("acceptedAt"),
        "ttlDays": system.conversation_ttl_days,
        "voice": voice,
        "inspections": inspections,
        "unseen": sum(1 for i in inspections if i["at"] > seen),
    }


@console.post("/assistant/privacy")
async def accept_notice(
    body: NoticeAccept, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Record that this login read the current first-use notice (and the inspections listed).

    Returns:
        ok.

    Raises:
        BadRequest: Not the current version of the notice (400 ``notice_version``).
        NotFound: The assistant is off.
        Forbidden: A key or the assistant itself instead of a console login.
    """
    if not a.console or a.via:
        raise Forbidden("The assistant is opened from the console.", code="console_required")
    if body.version != privacy.NOTICE_VERSION:
        raise BadRequest("This is not the current notice.", code="notice_version")
    login = await console_login(request)
    system = await activation.system_app(svc)
    if system is None:
        raise NotFound("The assistant is off in this deployment.", code="assistant_off")
    key = _notice_key(login)
    row = await svc.store.console_preferences.get(key)
    now = utcnow().isoformat()
    first = (row.value if row else None) or {}
    accepted = first.get("acceptedAt") if first.get("version") == body.version else None
    await svc.store.console_preferences.put(
        key, {"version": body.version, "acceptedAt": accepted or now, "seenAt": now}
    )
    if not accepted:
        await audit.record(
            request,
            login,
            privacy.NOTICE,
            application_id=system.id,
            target=login.actor,
            after={"version": body.version},
        )
    return {"ok": True}
