"""Server API, with an application's server key: session tokens, conversations, usage, memories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, model_validator

from evokai import audit, privacy
from evokai.api.deps import server_app, services
from evokai.core.caps import CapSpec, caps_of, currency_of, rate_of, zone_of
from evokai.core.caps import window as cap_window
from evokai.core.errors import BadRequest, Conflict, Forbidden, NotFound
from evokai.core.security import hash_code
from evokai.core.util import utcnow
from evokai.db.repositories import DEFAULT_TITLE, Who
from evokai.grants import Grants, NotGranted, grants_of
from evokai.identity.models import AppPrincipal
from evokai.memory.store import memory_view as view
from evokai.privacy import residency_allows
from evokai.protection import protection
from evokai.services import Services
from evokai.sessions.limits import app_shares, cap_state, caps_set, report_shares, windows_of

router = APIRouter(prefix="/v1", tags=["server"])
Svc = Annotated[Services, Depends(services)]
App = Annotated[AppPrincipal, Depends(server_app)]
SubjectQ = Annotated[str, Query(min_length=1, max_length=200)]


class SubjectLimits(BaseModel):
    """Optional per-subject caps the application decides; Evokai enforces them for the session.

    Each replaces the application's default subject cap of its period.
    """

    subject_daily_usd: float | None = Field(default=None, ge=0)
    """The subject's daily cap in USD (the old form)."""
    subject_monthly_usd: float | None = Field(default=None, ge=0)
    """The subject's monthly cap in USD (the old form)."""
    caps: list[CapSpec] | None = Field(default=None, max_length=3)
    """The subject's caps in the application's currency, ``[{period: week, amount: 5}]``; a
    cap without ``resets`` resets when the application's default subject cap of its period does
    (else Monday, or the 1st)."""

    @model_validator(mode="after")
    def _one_form(self) -> SubjectLimits:
        periods = [c.period for c in self.caps or []]
        if len(periods) != len(set(periods)):
            raise ValueError("limits.caps: one cap per period")
        for period, old in (("month", self.subject_monthly_usd), ("day", self.subject_daily_usd)):
            if old is not None and period in periods:
                raise ValueError(f"limits: the {period} cap is set twice (old field and caps)")
        return self


class Consent(BaseModel):
    """A consent record: which notice, which version, when."""

    version: str = Field(min_length=1, max_length=40)
    acceptedAt: datetime
    noticeHash: str | None = Field(default=None, max_length=128)


class TokenRequest(BaseModel):
    """What a session token grants."""

    subject: str = Field(min_length=1, max_length=200)
    """The application's own id for the end user (opaque to Evokai)."""
    agent: str
    conversationId: str | None = None
    """Continue a conversation of this subject; omitted = a new one."""
    tags: list[str] = Field(default_factory=list, max_length=20)
    groups: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    """The subject's groups or roles (``finance``, ``role:agent``...), as the application knows
    them. Knowledge documents that carry access groups are found only by sessions with one of
    them; documents without access groups are found by everyone. Signed into the token."""
    ttlSeconds: int | None = Field(default=None, ge=30)
    forwardCredential: str | None = Field(default=None, max_length=8_000)
    """The end user's credential for forward_user MCP servers.

    Kept encrypted for the token's life.
    """
    limits: SubjectLimits | None = None
    model: str | None = None
    """Primary model, among those the agent offers."""
    voice: str | None = Field(default=None, max_length=120)
    """Voice for this session (overrides the agent's and the application's default)."""
    voiceEngine: Literal["browser", "server"] | None = None
    """Who does speech: the browser (on-device) or the server (Pipecat over WebRTC).

    Omitted = the application's default, else ``browser`` (or whichever engine is granted).
    Requesting an engine the application is not granted is refused (403
    ``voice_engine_not_granted``).
    """
    storageMode: Literal["evokai", "external"] | None = None
    """Where a new conversation lives: in Evokai, or kept by the application (it sends the
    history with each request). Omitted = the application's storage mode. Fixed for the
    conversation once created; a mode the application is not granted is refused (403
    ``storage_mode_not_granted``)."""
    actor: Literal["agent"] = "agent"
    """Who answers. Only agents today; avatars fronting an agent later."""
    consent: Consent | None = None
    """The consent the end user gave for this session (recorded with it)."""
    exitCode: str | None = Field(default=None, min_length=4, max_length=32)
    """Kiosk sessions: a code that closes the session (stored hashed)."""
    memoryConsent: bool | None = None
    """The person's answer to long-term memory, when the application asked it in its own
    interface (``memory_mode: ask``): recorded for the subject, the latest answer counts. False
    deletes what was remembered. Omitted = unchanged (the chat panel may ask)."""


@router.post("/session-tokens")
async def mint(body: TokenRequest, svc: Svc, app: App) -> dict[str, Any]:
    """Mint a short-lived session token for the browser.

    Returns:
        token, expiresAt, conversationId.

    Raises:
        NotFound: Unknown agent, conversation or application.
        Conflict: The conversation is closed, or kept in another storage mode.
        Forbidden: The agent runs on the server only.
        NotGranted: A requested model, voice engine, voice or storage mode, or browser sessions,
            are not granted to the application (403 ``*_not_granted``).
    """
    agent = await svc.agents.active(app.application_id, body.agent)
    if "browser" not in agent.channels:
        raise Forbidden("This agent runs on the server only.", code="channel")
    who = Who(app.application_id, body.subject)
    application = await svc.store.applications.get(app.application_id)
    if application is None:
        raise NotFound()
    grants = await grants_of(svc.store, application)
    if not grants.channel("browser"):
        raise NotGranted("channel", "Browser sessions are not granted to this application.")
    if body.model and (grants.model(body.model) != "any" or not svc.catalog.allowed(body.model)):
        raise NotGranted("model", f"The model {body.model} is not granted as primary.")
    if body.model and not residency_allows(
        protection(application), body.model, svc.settings.aws_region
    ):
        raise Forbidden(
            f"The model {body.model} is outside the data residency.", code="model_residency"
        )
    engine = _voice_engine(body, application, grants, svc.settings.voice_stt)
    if body.voice and not grants.tts(engine, body.voice):
        raise NotGranted("voice", f"The voice {body.voice} is not granted for {engine} speech.")
    mode = _storage_mode(body.storageMode, application.storage_mode, grants)
    if body.conversationId:
        existing = await svc.store.sessions.get(who, body.conversationId)
        if not existing:
            raise NotFound("Unknown conversation.", code="unknown_conversation")
        if existing.status == "closed":
            raise Conflict("This session is closed.", code="closed")
        kept = existing.storage_mode or application.storage_mode
        if body.storageMode and body.storageMode != kept:
            raise Conflict(f"This conversation is stored in {kept} mode.", code="storage_mode")
        if not grants.storage(kept):
            raise NotGranted("storage_mode", f"The {kept} storage mode is not granted.")
        mode = kept
        conversation_id = body.conversationId
        if existing.voice_engine != engine:
            await svc.store.sessions.set_voice_engine(existing.id, engine)
    else:
        sess, _ = await svc.store.sessions.create(
            who,
            agent.name,
            agent.version,
            DEFAULT_TITLE,
            body.tags,
            consent=body.consent.model_dump(mode="json") if body.consent else None,
            exit_code_hash=hash_code(body.exitCode) if body.exitCode else None,
            voice_engine=engine,
            storage_mode=mode,
        )
        conversation_id = sess.id
    claims: dict[str, Any] = {
        "aud": app.application_id,
        "sub": body.subject,
        "agt": body.agent,
        "cid": conversation_id,
        "tags": body.tags,
        "ven": engine,
        "stm": mode,
    }
    if body.limits:
        claims["lim"] = body.limits.model_dump(exclude_none=True)
    if body.model:
        claims["mdl"] = body.model
    if body.voice:
        claims["voc"] = body.voice
    if body.forwardCredential:
        claims["crd"] = True
    if body.groups:
        claims["grp"] = sorted(set(body.groups))
    token, exp, jti = svc.keyring.mint(claims, body.ttlSeconds)
    if body.forwardCredential:
        await svc.store.credentials.put(
            jti,
            app.application_id,
            svc.keyring.encrypt(body.forwardCredential),
            datetime.fromtimestamp(exp, UTC),
        )
    if body.memoryConsent is not None and svc.memory is not None:
        await svc.memory.set_consent(application, who, body.memoryConsent, "token")
    caps = await cap_state(
        svc.store,
        application,
        who,
        utcnow(),
        body.limits.model_dump(exclude_none=True) if body.limits else None,
    )
    return {
        "memory": (await svc.memory.state(application, who)).view(),
        "token": token,
        "expiresAt": exp,
        "conversationId": conversation_id,
        "voiceEngine": engine,
        "storageMode": mode,
        "usage": caps.view(),
        "suggestions": agent.suggestions,
    }


def _voice_engine(body: TokenRequest, app: Any, grants: Grants, stt: str) -> str:
    """The session's voice engine: the requested one, else the application's default.

    Without a request, an engine that is not granted gives way to one that is; a session with no
    granted engine is recorded as ``browser`` and its voice turns are refused.

    Returns:
        ``browser`` or ``server``.

    Raises:
        NotGranted: The requested engine, or server voice's speech-to-text engine, is not granted.
    """
    if body.voiceEngine:
        if not grants.voice(body.voiceEngine):
            raise NotGranted("voice_engine", f"{body.voiceEngine} speech is not granted.")
        if body.voiceEngine == "server" and not grants.stt(stt):
            raise NotGranted("stt", f"The speech-to-text engine {stt} is not granted.")
        return body.voiceEngine
    default = str((app.defaults or {}).get("voice_engine") or "browser")
    if grants.voice(default):
        return default
    return next((e for e in ("browser", "server") if grants.voice(e)), "browser")


def _storage_mode(requested: str | None, app_mode: str, grants: Grants) -> str:
    """The storage mode of a new conversation.

    Returns:
        ``evokai`` or ``external``.

    Raises:
        NotGranted: The requested mode, or every mode, is not granted.
    """
    if requested:
        if not grants.storage(requested):
            raise NotGranted("storage_mode", f"The {requested} storage mode is not granted.")
        return requested
    if grants.storage(app_mode):
        return app_mode
    other = next((x for x in ("evokai", "external") if grants.storage(x)), None)
    if other is None:
        raise NotGranted("storage_mode", "No storage mode is granted to this application.")
    return other


@router.get("/conversations")
async def conversations(
    svc: Svc, app: App, subject: SubjectQ, limit: Annotated[int, Query(ge=1, le=100)] = 30
) -> dict[str, Any]:
    """A subject's recent conversations.

    Returns:
        items.
    """
    return {"items": await svc.store.sessions.recent(Who(app.application_id, subject), limit)}


@router.get("/conversations/{conversation_id}")
async def conversation(
    conversation_id: str, svc: Svc, app: App, subject: SubjectQ
) -> dict[str, Any]:
    """A subject's conversation, with its messages.

    Returns:
        Messages and the open confirmation.

    Raises:
        NotFound: Not a conversation of this subject.
    """
    who = Who(app.application_id, subject)
    sess = await svc.store.sessions.get(who, conversation_id)
    if not sess:
        raise NotFound()
    thread = await svc.store.sessions.thread(sess.id)
    msgs = await svc.store.messages.recent(who, thread.id, 400)
    pending = await svc.store.pending.open(who, sess.id)
    return {
        "id": sess.id,
        "title": sess.title,
        "agent": sess.agent_name,
        "messages": [
            {
                "id": x.id,
                "role": x.role,
                "text": x.text,
                "events": x.events or [],
                "feedback": x.feedback,
            }
            for x in msgs
            if x.text or x.events
        ],
        "pending": {"actionId": pending.id, "items": [c["label"] for c in pending.state["calls"]]}
        if pending
        else None,
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, svc: Svc, app: App, subject: SubjectQ, request: Request
) -> dict[str, bool]:
    """Delete a subject's conversation.

    Returns:
        ok.

    Raises:
        NotFound: Not a conversation of this subject.
    """
    if not await svc.store.sessions.delete(Who(app.application_id, subject), conversation_id):
        raise NotFound()
    key = privacy.subject_key(svc.keyring.signing_key, app.application_id, subject)[:16]
    await audit.record(
        request, app, "conversation.delete", target=f"{conversation_id} subject:{key}"
    )
    return {"ok": True}


@router.get("/usage")
async def usage(
    svc: Svc,
    app: App,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    group_by: Annotated[str, Query(alias="groupBy")] = "subject",
    subject: str | None = None,
    tag: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Usage of the application, grouped by subject, agent, kind, model, day and/or tags.

    ``tags`` groups by the set of tags a session carried (a list per row). With the server or admin
    key the rows carry amounts (server to server); a console login gets shares of the caps instead.

    Returns:
        rows, and the application's caps and the spend of their current windows. With a key the
        application also gets its ``currency`` and the ``exchangeRate`` every amount in its
        currency was converted with (rows keep ``costUsd`` and add ``cost``), and ``caps``: each
        cap with its window, its maximum (``amount`` in its currency, ``capUsd``) and, for the
        application's own caps, what the window spent (``spentUsd``, ``spent``).

    Raises:
        BadRequest: Unknown grouping or inverted period.
        NotFound: The application is gone.
    """
    now = utcnow()
    end = end or now + timedelta(seconds=1)
    start = start or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    groups = [g.strip() for g in group_by.split(",") if g.strip()]
    known = {"subject", "agent", "kind", "model", "day", "tags"}
    if any(g not in known for g in groups) or start >= end:
        raise BadRequest("groupBy: subject, agent, kind, model, day, tags; from < to.")
    rows = await svc.store.usage.report(
        app.application_id,
        start=start,
        end=end,
        group_by=groups,
        subject=subject,
        tag=tag,
        agent=agent,
    )
    application = await svc.store.applications.get(app.application_id)
    if application is None:
        raise NotFound()
    if app.console:
        # A person's browser (the console): shares of the caps, never amounts.
        today = None
        if "subject" in groups:
            day = await svc.store.usage.report(
                app.application_id,
                start=cap_window("day", None, now, zone_of(application))[0],
                end=now + timedelta(seconds=1),
                group_by=["subject"],
                tag=tag,
                agent=agent,
            )
            today = {str(r["subject"]): float(r["costUsd"]) for r in day}
        return {
            "rows": report_shares(application, rows, today),
            "application": await app_shares(svc.store, application, now),
            "caps": caps_set(application),
        }
    rate = rate_of(application)
    spans = windows_of(caps_of(application), now, application)
    spend = await svc.store.usage.window_spend(
        Who(app.application_id, ""),
        {c.kind: (s, False) for c, s, _ in spans if c.scope == "application"},
    )

    def local(usd: float) -> float | None:
        return round(usd * rate, 6) if rate else None

    caps = []
    for c, s, e in spans:
        item: dict[str, Any] = {
            **c.view(),
            "capUsd": round(c.usd, 6),
            "windowStart": s.isoformat(),
            "resetsAt": e.isoformat(),
        }
        if c.scope == "application":
            item["spentUsd"] = spend.get(c.kind, 0.0)
            item["spent"] = local(item["spentUsd"])
        caps.append(item)
    by = {c["kind"]: c for c in caps}
    return {
        "rows": [{**r, "cost": local(float(r.get("costUsd") or 0))} for r in rows],
        "application": {
            "currency": currency_of(application),
            "exchangeRate": rate,
            "monthlyCapUsd": by["app_month"]["capUsd"] if "app_month" in by else None,
            "dailyCapUsd": by["app_day"]["capUsd"] if "app_day" in by else None,
            "monthUsd": by["app_month"]["spentUsd"] if "app_month" in by else 0.0,
            "dayUsd": by["app_day"]["spentUsd"] if "app_day" in by else 0.0,
            "caps": caps,
        },
    }


@router.get("/memories")
async def memories(svc: Svc, app: App, subject: SubjectQ) -> dict[str, Any]:
    """What the agents remember about a subject, and their consent.

    Returns:
        items (id, fact, text, kind, agent, source, dates, uses) and memory (mode, consent).

    Raises:
        NotFound: The application is gone.
    """
    who = Who(app.application_id, subject)
    application = await svc.store.applications.get(app.application_id)
    if application is None:
        raise NotFound()
    return {
        "items": [{"fact": x.fact, **view(x)} for x in await svc.store.memories.items(who)],
        "memory": (await svc.memory.state(application, who)).view(),
    }


class ConsentBody(BaseModel):
    """A subject's answer to long-term memory, relayed by the application's backend."""

    subject: str = Field(min_length=1, max_length=200)
    granted: bool


@router.post("/memories/consent")
async def memory_consent(body: ConsentBody, svc: Svc, app: App, request: Request) -> dict[str, Any]:
    """Record a subject's answer (False deletes what was remembered).

    Returns:
        memory (the new state) and deleted.

    Raises:
        NotFound: The application is gone.
    """
    who = Who(app.application_id, body.subject)
    application = await svc.store.applications.get(app.application_id)
    if application is None:
        raise NotFound()
    deleted = await svc.memory.set_consent(application, who, body.granted, "server")
    key = privacy.subject_key(svc.keyring.signing_key, app.application_id, body.subject)[:16]
    await audit.record(
        request,
        app,
        "memory.consent",
        target=f"subject:{key}",
        after={"granted": body.granted, "deleted": deleted},
    )
    return {"memory": (await svc.memory.state(application, who)).view(), "deleted": deleted}


@router.delete("/memories")
async def forget(
    svc: Svc,
    app: App,
    subject: SubjectQ,
    request: Request,
    memory_id: Annotated[str | None, Query(alias="id")] = None,
) -> dict[str, int]:
    """Forget one fact, or everything about a subject.

    Returns:
        How many facts were deleted.
    """
    deleted = await svc.store.memories.forget(Who(app.application_id, subject), memory_id)
    key = privacy.subject_key(svc.keyring.signing_key, app.application_id, subject)[:16]
    await audit.record(
        request, app, "memory.forget", target=f"subject:{key}", after={"deleted": deleted}
    )
    return {"deleted": deleted}


@router.get("/agents")
async def agents(svc: Svc, app: App) -> dict[str, Any]:
    """The application's active agents.

    Returns:
        items: name, version, title.
    """
    rows = await svc.store.agents.active_all(app.application_id)
    return {
        "items": [
            {"name": r.name, "version": r.version, "title": r.definition.get("title", "")}
            for r in rows
        ]
    }


@router.get("/voices")
async def voices(svc: Svc, _app: App, language: str | None = None) -> dict[str, Any]:
    """The text-to-speech voices of this deployment.

    Returns:
        items: provider, voiceId, language, name, gender, region.
    """
    rows = await svc.store.voices.all(language)
    return {
        "items": [
            {
                "provider": v.provider,
                "voiceId": v.voice_id,
                "language": v.language,
                "name": v.name,
                "gender": v.gender,
                "region": v.region,
            }
            for v in rows
        ]
    }
