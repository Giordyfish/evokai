"""Admin console: login with a key (exchanged for a cookie), operator and console-only views.

The console itself (the SPA in ``admin/``) is served at ``{base}/admin``; it calls these routes
and the ordinary server and admin routes with its cookie.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import sqlalchemy as sa
import yaml
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator

from evokai import audit
from evokai.agents.definitions import AgentDef
from evokai.agents.skills import SkillError, parse_skill_text
from evokai.api.deps import admin_app, console_login, operator, services
from evokai.api.grants_routes import grant_view
from evokai.api.trace_routes import note_view
from evokai.api.trace_routes import summary as trace_summary
from evokai.assistant import bootstrap as shipped
from evokai.assistant.privacy import lists_everyone, own_subject
from evokai.core.caps import LEGACY
from evokai.core.errors import AppError, BadRequest, Conflict, Forbidden, NotFound, Unauthorized
from evokai.core.security import generate_key
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.grants import YAML_SECTIONS
from evokai.identity import console
from evokai.identity.auth import bearer
from evokai.identity.models import AppPrincipal
from evokai.llm.suggestions import suggestions
from evokai.mcp.client import McpClient
from evokai.mcp.registry import status_of, tool_info
from evokai.services import Services
from evokai.sessions.limits import app_shares, caps_set, month_cap_usd, share_pct
from evokai.version import __version__

router = APIRouter(tags=["console"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]
ConsoleUser = Annotated[console.ConsoleLogin, Depends(console_login)]

MAX_FAILED_LOGINS = 10
LOGIN_CODE_TTL = 60
FAILURE_WINDOW = 300
"""Seconds a failed sign-in counts against its address."""
"""Seconds a one-time sign-in code stays valid."""


def _hashed(value: str) -> str:
    """A shared-state key part that does not reveal the value (an address, a code).

    Returns:
        A hex digest.
    """
    return hashlib.sha256(value.encode()).hexdigest()[:40]


def _code_key(code: str) -> str:
    return f"console:code:{_hashed(code)}"


def _failure_key(request: Request) -> str:
    return f"console:failures:{_hashed(_client_ip(request))}"


class TooMany(AppError):
    """Too many failed logins from this address."""

    def __init__(self) -> None:
        """Create the error."""
        super().__init__(
            "Too many attempts. Try again in a few minutes.", code="too_many_attempts", status=429
        )


class Login(BaseModel):
    """An admin or operator key, or a one-time sign-in code (exactly one)."""

    key: str | None = Field(default=None, min_length=8, max_length=200)
    code: str | None = Field(default=None, min_length=16, max_length=200)

    @model_validator(mode="after")
    def _one(self) -> Login:
        if (self.key is None) == (self.code is None):
            raise ValueError("send exactly one of key or code")
        return self


def _cookie_path(svc: Services) -> str:
    return svc.settings.base_path.rstrip("/") or "/"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def _throttle(request: Request, svc: Services) -> None:
    """Refuse an address with too many failed sign-ins (counted by every instance together).

    Raises:
        TooMany: Too many failed attempts from this address.
    """
    if await svc.shared.count(_failure_key(request)) >= MAX_FAILED_LOGINS:
        raise TooMany()


async def _failed(request: Request, svc: Services) -> None:
    await svc.shared.incr(_failure_key(request), FAILURE_WINDOW)


def _login_json(who: console.ConsoleLogin) -> str:
    return json.dumps(dataclasses.asdict(who))


def _login_of(raw: str | None) -> console.ConsoleLogin | None:
    if raw is None:
        return None
    try:
        return console.ConsoleLogin(**json.loads(raw))
    except (ValueError, TypeError):
        return None


async def _resolve(key: str, svc: Services) -> console.ConsoleLogin:
    """The console login an admin or operator key stands for.

    Returns:
        The login, expiring after the console session's lifetime.

    Raises:
        Unauthorized: Not an admin or operator key.
    """
    expires = int(time.time()) + svc.settings.console_session_ttl
    try:
        if console.is_operator_key(key, svc.settings.operator_key):
            return console.ConsoleLogin("op", None, expires)
        app = await svc.auth.application(key, admin=True)
        return console.ConsoleLogin("adm", app.application_id, expires, app.key_id)
    except AppError as e:
        raise Unauthorized("Invalid key.", code="key") from e


@router.post("/v1/console/login", status_code=204)
async def login(body: Login, request: Request, response: Response, svc: Svc) -> None:
    """Exchange a key, or a one-time code, for a short console cookie.

    The cookie is ``HttpOnly`` and ``SameSite=Strict``. A code comes from
    ``POST /v1/console/login-code``, is valid for a minute and works once. Too many failed
    attempts from one address answer 429.

    Raises:
        Unauthorized: Not an admin or operator key (``key``), or an unknown, expired or used
            code (``login_code``).
    """
    await _throttle(request, svc)
    if body.code is not None:
        held = _login_of(await svc.shared.take(_code_key(body.code)))
        alive = held is not None and (
            held.kind != "adm"
            or not held.key_id
            or await svc.auth.key_alive(held.key_id, held.application_id or "")
        )
        if held is None or not alive:
            await _failed(request, svc)
            await audit.record(request, None, "console.login_failed", target="code")
            raise Unauthorized("This sign-in link has expired.", code="login_code")
        expires = int(time.time()) + svc.settings.console_session_ttl
        who = dataclasses.replace(held, expires_at=expires)
    else:
        try:
            who = await _resolve(body.key or "", svc)
        except Unauthorized:
            await _failed(request, svc)
            await audit.record(request, None, "console.login_failed", target="key")
            raise
    await audit.record(
        request, who, "console.login", application_id=who.application_id, target=who.kind
    )
    response.set_cookie(
        console.COOKIE,
        console.issue(svc.keyring, who),
        max_age=svc.settings.console_session_ttl,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https" or svc.settings.production,
        path=_cookie_path(svc),
    )


@router.post("/v1/console/login-code")
async def login_code(request: Request, svc: Svc) -> dict[str, Any]:
    """A one-time sign-in code for the console, for a local script that opens the browser.

    Authenticated like the key login (``Authorization: Bearer`` with an admin or operator key,
    same throttling). The script then opens ``{base}/admin/#/login?code=...``; the console trades
    the code for its cookie. Codes live in the shared state (Redis when configured), so any
    instance can redeem them.

    Returns:
        code (single use) and expiresIn (seconds).

    Raises:
        Unauthorized: Not an admin or operator key.
    """
    await _throttle(request, svc)
    try:
        who = await _resolve(bearer(request.headers.get("authorization")) or "", svc)
    except Unauthorized:
        await _failed(request, svc)
        raise
    code = secrets.token_urlsafe(32)
    await svc.shared.set(_code_key(code), _login_json(who), LOGIN_CODE_TTL)
    await audit.record(request, who, "console.login_code", application_id=who.application_id)
    return {"code": code, "expiresIn": LOGIN_CODE_TTL}


@router.post("/v1/console/logout", status_code=204)
async def logout(response: Response, svc: Svc) -> None:
    """End the console login."""
    response.delete_cookie(console.COOKIE, path=_cookie_path(svc), samesite="strict")


@router.get("/v1/console/me")
async def me(request: Request, svc: Svc) -> dict[str, Any]:
    """Who is logged in.

    Returns:
        kind (``op``, ``adm`` or ``sso``), applicationId, expiresAt, keyId and keyPrefix (admin
        logins), email, name and roles (single sign-on), whether it owns the deployment, and the
        deployment's facts the console needs (model allow-list, voice enabled).

    Raises:
        Unauthorized: Not logged in, the login's key was revoked or its user disabled.
    """
    value = request.cookies.get(console.COOKIE)
    if not value:
        raise Unauthorized()
    who = console.read(svc.keyring, value)
    if who.kind == "sso" and not await svc.auth.user_active(who.user_id or ""):
        raise Unauthorized("This console user was disabled.", code="console")
    prefix = None
    if who.kind == "adm" and who.key_id:
        key = await svc.store.applications.key(who.key_id)
        if key is None or key.revoked_at is not None:
            raise Unauthorized("The key of this login was revoked.", code="console")
        prefix = key.prefix
    return {
        "kind": who.kind,
        "applicationId": who.application_id,
        "expiresAt": who.expires_at,
        "keyId": who.key_id,
        "keyPrefix": prefix,
        "email": who.email,
        "name": who.name,
        "roles": who.roles if who.kind == "sso" else {"*" if who.kind == "op" else "own": "owner"},
        "deploymentOwner": who.deployment_owner,
        "modelAllow": svc.settings.model_allow,
        "voiceEnabled": svc.settings.voice_enabled,
    }


# Operator: applications


class NewApplication(BaseModel):
    """A new application."""

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=120)
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    daily_cap_usd: float | None = Field(default=None, ge=0)
    allowed_origins: list[str] = Field(default_factory=list)


@router.get("/v1/operator/applications", dependencies=[Depends(operator)])
async def applications(svc: Svc) -> dict[str, Any]:
    """Every application.

    Returns:
        items.
    """
    rows = await svc.store.applications.all()
    return {
        "items": [
            {
                "id": a.id,
                "slug": a.slug,
                "name": a.name,
                "status": a.status,
                "storage_mode": a.storage_mode,
                "monthly_cap_usd": a.monthly_cap_usd,
                "daily_cap_usd": a.daily_cap_usd,
            }
            for a in rows
        ]
    }


@router.post("/v1/operator/applications", dependencies=[Depends(operator)], status_code=201)
async def create_application(body: NewApplication, svc: Svc, request: Request) -> dict[str, str]:
    """Create an application with a server key and an admin key (shown only now).

    Returns:
        id, serverKey, adminKey.

    Raises:
        Conflict: The slug is taken.
    """
    if await svc.store.applications.by_slug(body.slug):
        raise Conflict("An application with this slug already exists.", code="slug_taken")
    fields = body.model_dump(exclude={"slug", "name"}, exclude_none=True)
    actor = deployment_actor(request, svc)
    app = await svc.store.applications.create(body.slug, body.name, by=actor.actor, **fields)
    keys = {}
    for kind in ("srv", "adm"):
        key = generate_key(kind)
        await svc.store.applications.add_key(app.id, kind, key.prefix, key.secret_hash, "console")
        keys[kind] = key.value
    await audit.record(
        request,
        actor,
        "application.create",
        application_id=app.id,
        target=app.slug,
        after=body.model_dump(),
    )
    return {"id": app.id, "serverKey": keys["srv"], "adminKey": keys["adm"]}


def deployment_actor(request: Request, svc: Services) -> console.ConsoleLogin:
    """Who acts with the operator key or a deployment owner's console login (for the audit log).

    Returns:
        The login (the operator key stands for itself).
    """
    value = request.cookies.get(console.COOKIE)
    if value and not bearer(request.headers.get("authorization")):
        return console.read(svc.keyring, value)
    return console.ConsoleLogin("op", None, 0)


class McpProbe(BaseModel):
    """An MCP server to try before it is registered."""

    url: str = Field(pattern=r"^https?://", max_length=500)
    token: str | None = Field(default=None, max_length=8_000)
    """A bearer token to call it with (a service secret or a user's token); never stored."""


@router.post("/v1/operator/mcp-test", dependencies=[Depends(operator)])
async def probe_mcp(body: McpProbe, svc: Svc) -> dict[str, Any]:
    """Connect to an MCP server that is not registered yet and list its tools (setup wizard).

    Returns:
        ok, latencyMs, tools (name, description, readOnly, destructive) or error.
    """
    client = McpClient(
        name="probe",
        url=body.url,
        http=svc.http,
        headers={"Authorization": f"Bearer {body.token}"} if body.token else {},
        timeout=15,
    )
    started = time.perf_counter()
    try:
        tools = await client.list_tools()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    return {
        "ok": True,
        "latencyMs": round((time.perf_counter() - started) * 1000),
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "readOnly": t.read_only,
                "destructive": t.destructive,
            }
            for t in tools
        ],
    }


@router.get("/v1/console/models")
async def models(svc: Svc, _login: ConsoleUser) -> dict[str, Any]:
    """Models to offer in the agent editor and the setup wizard, within the deployment's allow-list.

    Returns:
        items (id, label, provider, ready, note) and allow (the allow-list, for typed-in models).
    """
    return {
        "items": await suggestions(svc.catalog, svc.http),
        "allow": svc.settings.model_allow,
    }


class SkillText(BaseModel):
    """The text of a SKILL.md."""

    text: str = Field(min_length=1, max_length=40_000)


@router.post("/v1/console/skills/parse")
async def parse_skill(body: SkillText, _login: ConsoleUser) -> dict[str, Any]:
    """Read a SKILL.md as the service would (front matter name and description, then the body).

    Returns:
        name, description, body, size.

    Raises:
        BadRequest: No front matter with a name and a description (``skill_format``), or a
            description or body over its limit (``skill_limit``).
    """
    try:
        skill = parse_skill_text(body.text)
    except SkillError as e:
        raise BadRequest(str(e), code="skill_limit") from None
    if skill is None:
        raise BadRequest(
            "A SKILL.md starts with front matter (---) holding a name and a description.",
            code="skill_format",
        )
    if not skill.body:
        raise BadRequest("The skill has no text after its front matter.", code="skill_format")
    return {
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,
        "size": len(skill.body.encode()),
    }


# Console views over one application


@router.get("/v1/admin/agents/{name}/versions")
async def agent_versions(name: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """Every version of an agent, newest first (for history and diffs).

    Returns:
        items: version, active, createdAt, createdBy (``config``, ``operator`` or an admin key's
        id), definition.

    Raises:
        NotFound: No such agent.
    """
    rows = await svc.store.agents.versions(a.application_id, name)
    if not rows:
        raise NotFound()
    return {
        "items": [
            {
                "version": r.version,
                "active": r.active,
                "createdAt": r.created_at.isoformat(),
                "createdBy": r.created_by,
                "definition": r.definition,
            }
            for r in rows
        ]
    }


class McpTest(BaseModel):
    """A test of an MCP server."""

    credential: str | None = Field(default=None, max_length=8_000)
    """For ``forward_user`` servers: a user's token to test with (not stored)."""


@router.post("/v1/admin/mcp-servers/{name}/test")
async def test_mcp(name: str, body: McpTest, svc: Svc, a: Admin) -> dict[str, Any]:
    """Connect to an MCP server and list its tools.

    The result is written down (status, latency and tools) for the console and the grants.

    Returns:
        ok, latencyMs, tools (name, description, readOnly, destructive) or error.

    Raises:
        NotFound: No such server.
    """
    server = next(
        (s for s in await svc.store.mcp_servers.all(a.application_id) if s.name == name), None
    )
    if server is None:
        raise NotFound()
    if server.auth == "forward_user" and not server.connector and not body.credential:
        return {"ok": False, "error": "A user token is needed to test it."}
    started = time.perf_counter()
    try:
        client, _ = svc.mcp.client_for(server, body.credential)
        tools = await client.list_tools()
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:200]}"
        await svc.store.mcp_servers.seen(server.id, status="down", error=error)
        return {"ok": False, "error": error}
    ms = round((time.perf_counter() - started) * 1000)
    await svc.store.mcp_servers.seen(
        server.id, status=status_of(ms), latency_ms=ms, tools=[tool_info(t) for t in tools]
    )
    svc.mcp.forget(a.application_id)
    return {
        "ok": True,
        "latencyMs": ms,
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "readOnly": t.read_only,
                "destructive": t.destructive,
            }
            for t in tools
        ],
    }


@router.get("/v1/admin/conversations")
async def conversations(
    svc: Svc,
    a: Admin,
    subject: str | None = None,
    agent: str | None = None,
    feedback: Annotated[str | None, Query(pattern="^(up|down|any)$")] = None,
    noted: bool = False,
    before: datetime | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Conversations kept in Evokai, newest first, with their counts (the console's review).

    ``feedback`` keeps those with a thumbs up, down or any; ``noted`` those with a reviewer's
    note. Pass ``next`` back as ``before``.

    Returns:
        storage, items (turns, thumbsUp, thumbsDown and notes of each; none with external
        storage: Evokai keeps no content) and next.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    if app.storage_mode != "evokai":
        return {"storage": app.storage_mode, "items": []}
    limit = max(1, min(limit, 200))
    # A system application: each person's own conversations; an owner lists everyone's, the
    # others without their content (``evokai.assistant.privacy``).
    mine = await own_subject(svc, a)
    everyone = mine is not None and lists_everyone(a)
    if mine is not None and not everyone:
        if subject and subject != mine:
            return {"storage": app.storage_mode, "items": [], "next": None}
        subject = mine
    items = await svc.store.sessions.review(
        a.application_id,
        subject=subject,
        agent=agent,
        feedback=feedback,
        noted=noted,
        before=before,
        limit=limit,
    )
    if mine is not None:
        items = [
            {**i, "own": True} if i["subject"] == mine else {**i, "title": None, "own": False}
            for i in items
        ]
    return {
        "storage": app.storage_mode,
        "items": items,
        "next": items[-1]["updatedAt"] if len(items) == limit else None,
    }


@router.get("/v1/admin/conversations/{conversation_id}")
async def conversation(conversation_id: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """One conversation with its messages, turns, their traces and the reviewers' notes.

    On a system application only the caller's own conversation opens; an owner opens another
    person's through an inspection (403 ``inspection_required``).

    Returns:
        The conversation: messages (with their turn), turns, traces (summaries by turn) and
        notes.

    Raises:
        NotFound: Unknown, or the application keeps conversations itself.
        Forbidden: Another person's conversation on a system application, for an owner.
    """
    mine = await own_subject(svc, a)
    subject = await conversation_subject(svc, a.application_id, conversation_id)
    if subject is None or (mine is not None and subject != mine and not lists_everyone(a)):
        raise NotFound()
    if mine is not None and subject != mine:
        raise Forbidden(
            "Another person's conversation opens only through an inspection.",
            code="inspection_required",
        )
    return await conversation_view(svc, a.application_id, conversation_id)


async def conversation_subject(
    svc: Services, application_id: str, conversation_id: str
) -> str | None:
    """The subject of a conversation of the application.

    Returns:
        The subject, or None when there is no such conversation.
    """
    async with svc.db.session() as s:
        return (
            await s.execute(
                sa.select(m.Session.subject).where(
                    m.Session.id == conversation_id, m.Session.application_id == application_id
                )
            )
        ).scalar_one_or_none()


async def conversation_view(
    svc: Services, application_id: str, conversation_id: str
) -> dict[str, Any]:
    """A conversation with its messages, turns, their traces and the reviewers' notes.

    Returns:
        The conversation.

    Raises:
        NotFound: No such conversation.
    """
    async with svc.db.session() as s:
        row = (
            await s.execute(
                sa.select(m.Session).where(
                    m.Session.id == conversation_id, m.Session.application_id == application_id
                )
            )
        ).scalar_one_or_none()
        turns = (
            (
                await s.execute(
                    sa
                    .select(m.Turn)
                    .where(m.Turn.session_id == conversation_id)
                    .order_by(m.Turn.started_at)
                )
            )
            .scalars()
            .all()
        )
    if row is None:
        raise NotFound()
    who = Who(application_id, row.subject)
    thread = await svc.store.sessions.thread(row.id)
    msgs = await svc.store.messages.recent(who, thread.id, 400)
    application = await svc.store.applications.get(application_id)
    traces = await svc.store.traces.for_session(application_id, row.id) if application else []
    notes = await svc.store.review_notes.for_session(application_id, row.id)
    return {
        "id": row.id,
        "subject": row.subject,
        "title": row.title,
        "agent": row.agent_name,
        "agentVersion": row.agent_version,
        "voiceEngine": row.voice_engine,
        "messages": [
            {
                "id": x.id,
                "turnId": x.turn_id,
                "role": x.role,
                "text": x.text,
                "events": x.events or [],
                "interrupted": x.interrupted,
                "feedback": x.feedback,
            }
            for x in msgs
        ],
        "turns": [
            {
                "id": t.id,
                "mode": t.mode,
                "status": t.status,
                "reason": t.reason,
                "timings": _no_amounts(t.timings or {}, application),
            }
            for t in turns
        ],
        "traces": [trace_summary(t, application) for t in traces if application],
        "notes": [note_view(n) for n in notes],
    }


@router.get("/v1/admin/health")
async def health(svc: Svc, _a: Admin) -> dict[str, Any]:
    """The service as the console sees it.

    Returns:
        database, voice, models.
    """
    try:
        async with svc.db.session() as s:
            await s.execute(sa.text("SELECT 1"))
        database = "ok"
    except Exception as e:
        database = type(e).__name__
    return {
        "database": database,
        "voiceEnabled": svc.settings.voice_enabled,
        "modelAllow": svc.settings.model_allow,
        "version": __version__,
    }


# Console views across applications (cookie login only)

DEFAULT_MCP_TIMEOUT = 60


async def visible_applications(svc: Services, login: console.ConsoleLogin) -> list[m.Application]:
    """The applications a login may see: every one for the operator, its own for an admin.

    Returns:
        Applications by name.
    """
    if login.kind == "op":
        return await svc.store.applications.all()
    if login.kind == "sso":
        return [a for a in await svc.store.applications.all() if login.role_for(a)]
    app = await svc.store.applications.get(login.application_id or "")
    return [app] if app else []


def _no_amounts(timings: dict[str, Any], app: m.Application | None) -> dict[str, Any]:
    """A turn's timings for the console: its cost as a share of the monthly cap, not an amount.

    Returns:
        The timings, with ``capPct`` in place of ``costMicroUsd``.
    """
    out = {k: v for k, v in timings.items() if k != "costMicroUsd"}
    if "costMicroUsd" in timings and app is not None:
        out["capPct"] = share_pct(timings["costMicroUsd"] / 1e6, month_cap_usd(app))
    return out


def _month(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _card(svc: Services, app: m.Application, now: datetime) -> dict[str, Any]:
    """An application as the console's list shows it.

    Returns:
        Settings, this month's usage as shares of the caps (never amounts), turns and subjects,
        counts, grant summary and MCP status.
    """
    servers = await svc.store.mcp_servers.all(app.id)
    keys = await svc.store.applications.keys(app.id)
    view = await grant_view(svc, app)
    turns = await svc.store.turns.stats(app.id, start=_month(now), end=now + timedelta(seconds=1))
    subjects, _ = await svc.store.usage.subjects_and_tags(
        app.id, start=_month(now), end=now + timedelta(seconds=1)
    )
    defaults = app.defaults or {}
    statuses = [s.status for s in servers if s.status]
    return {
        "id": app.id,
        "slug": app.slug,
        "name": app.name,
        "status": app.status,
        "storageMode": app.storage_mode,
        "voiceEngine": defaults.get("voice_engine", "browser"),
        "locale": defaults.get("locale", "en"),
        "timezone": defaults.get("timezone", "UTC"),
        "conversationTtlDays": app.conversation_ttl_days,
        "caps": caps_set(app),
        "usage": await app_shares(svc.store, app, now),
        "turnsMonth": turns["turns"],
        "subjectsMonth": subjects,
        "counts": {
            "agents": len(await svc.store.agents.active_all(app.id)),
            "mcpServers": len(servers),
            "skills": len(await svc.store.skills.all(app.id)),
            "keys": sum(1 for k in keys if k.revoked_at is None),
            "overrides": view["overrides"],
            "workflows": len(await svc.store.workflows.active_all(app.id)),
            "approvals": await svc.store.workflows.pending_count(app.id),
        },
        "mcpStatus": "down"
        if "down" in statuses
        else "slow"
        if "slow" in statuses
        else "ok"
        if statuses
        else None,
        "grants": view["summary"],
    }


def _assistant_off(app: m.Application) -> bool:
    """Whether this is the built-in assistant's application while the assistant is not on.

    The console lists it only while the assistant is active (``hidden`` otherwise); the
    Assistant page still manages it.

    Returns:
        True for the assistant's application without an active model.
    """
    return (
        bool(app.system)
        and app.slug == shipped.SLUG
        and not (app.defaults or {}).get(shipped.MODEL_KEY)
    )


@router.get("/v1/console/applications")
async def console_applications(svc: Svc, login: ConsoleUser) -> dict[str, Any]:
    """The applications of this login, with what the console's list and switcher show.

    Returns:
        items (``hidden``: the assistant's application while the assistant is off), and whether
        this login may create applications (operator).
    """
    now = utcnow()
    return {
        "items": [
            {**await _card(svc, a, now), "role": login.role_for(a), "hidden": _assistant_off(a)}
            for a in await visible_applications(svc, login)
        ],
        "canCreate": login.deployment_owner,
    }


def _migration_head() -> str | None:
    """The newest migration shipped with this build.

    Returns:
        Its revision, or None when the migrations are not next to the code.
    """
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        root = Path(__file__).resolve().parents[3]
        cfg = Config()
        cfg.set_main_option("script_location", str(root / "migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:
        return None


async def _database(svc: Services) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with svc.db.session() as s:
            await s.execute(sa.text("SELECT 1"))
            try:
                revision = (
                    await s.execute(sa.text("SELECT version_num FROM alembic_version"))
                ).scalar_one_or_none()
            except Exception:
                revision = None
        status, error = "ok", None
    except Exception as e:
        status, error, revision = "down", type(e).__name__, None
    head = _migration_head()
    return {
        "status": status,
        "error": error,
        "latencyMs": round((time.perf_counter() - started) * 1000),
        "dialect": svc.db.engine.dialect.name,
        "schema": svc.settings.database_schema,
        "revision": revision,
        "head": head,
    }


async def _test_server(svc: Services, server: m.McpServerRecord) -> None:
    """List a server's tools when that needs no user (secret or no auth) and write it down."""
    started = time.perf_counter()
    try:
        client, _ = svc.mcp.client_for(server, None)
        tools = await client.list_tools()
    except Exception as e:
        await svc.store.mcp_servers.seen(server.id, status="down", error=type(e).__name__)
        return
    ms = round((time.perf_counter() - started) * 1000)
    await svc.store.mcp_servers.seen(
        server.id, status=status_of(ms), latency_ms=ms, tools=[tool_info(t) for t in tools]
    )


@router.get("/v1/console/health")
async def console_health(svc: Svc, login: ConsoleUser, check: bool = False) -> dict[str, Any]:
    """The service's health as this login may see it.

    With ``check=true`` the database is timed and every MCP server that needs no user credential
    is listed now; servers called as the user show what was seen at their last use or test.

    Returns:
        database, models, mcp, voice, sharedState (memory or redis, answering), otel, purge, build.
    """
    apps = await visible_applications(svc, login)
    servers = []
    for app in apps:
        for server in await svc.store.mcp_servers.all(app.id):
            if check and server.auth != "forward_user":
                await _test_server(svc, server)
            servers.append((app, server))
    if check:
        servers = [
            (app, s) for app in apps for s in await svc.store.mcp_servers.all(app.id)
        ]  # fmt: skip
    voice = svc.voice
    return {
        "database": await _database(svc),
        "models": {
            "region": svc.settings.aws_region,
            "allow": svc.settings.model_allow,
            "items": svc.engine.model_health.snapshot(),
        },
        "mcp": [
            {
                "application": app.slug,
                "applicationName": app.name,
                "name": s.name,
                "auth": s.auth,
                "status": s.status,
                "latencyMs": s.latency_ms,
                "checkedAt": s.checked_at.isoformat() if s.checked_at else None,
                "tools": len(s.tools) if s.tools is not None else None,
                "error": s.last_error,
            }
            for app, s in servers
        ],
        "voice": {
            "enabled": svc.settings.voice_enabled,
            "stt": svc.settings.voice_stt,
            "tts": svc.settings.voice_tts,
            "smartTurn": svc.settings.voice_smart_turn,
            "connections": len(getattr(voice, "_owners", {}) or {}) if voice else 0,
        },
        "sharedState": {
            "kind": svc.shared.kind,
            "ok": await svc.shared.ping(),
            "error": getattr(svc.shared, "last_error", None),
        },
        "otel": svc.settings.otel_enabled,
        "purge": {
            **svc.housekeeping,
            "ttlApplications": sum(1 for a in apps if a.conversation_ttl_days),
        },
        "build": {
            "version": __version__,
            "commit": svc.settings.build_commit or None,
            "python": platform.python_version(),
        },
    }


# Console views over one application


@router.get("/v1/admin/activity")
async def activity(svc: Svc, a: Admin, limit: int = 20) -> dict[str, Any]:
    """What changed lately: agent versions, keys, grants.

    Returns:
        items (at, kind, name, detail, by), newest first.
    """
    items: list[dict[str, Any]] = [
        {
            "at": r.created_at.isoformat(),
            "kind": "agent",
            "name": r.name,
            "detail": r.version,
            "by": r.created_by,
        }
        for r in await svc.store.agents.history(a.application_id, limit)
    ]
    for k in await svc.store.applications.keys(a.application_id):
        items.append({
            "at": k.created_at.isoformat(),
            "kind": "key_created",
            "name": k.prefix,
            "detail": k.kind,
            "by": None,
        })
        if k.revoked_at:
            items.append({
                "at": k.revoked_at.isoformat(),
                "kind": "key_revoked",
                "name": k.prefix,
                "detail": k.kind,
                "by": None,
            })
    items += [
        {
            "at": r.updated_at.isoformat(),
            "kind": "grant",
            "name": f"{r.kind}:{r.name}",
            "detail": r.value,
            "by": r.updated_by or None,
        }
        for r in await svc.store.grants.all(a.application_id)
    ]
    items.sort(key=lambda x: x["at"], reverse=True)
    return {"items": items[: max(1, min(limit, 100))]}


@router.get("/v1/admin/usage/summary")
async def usage_summary(
    svc: Svc,
    a: Admin,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    tag: str | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Turns and subjects of a period (default: this month), for the console's usage page.

    Returns:
        turns, fallbackTurns, medianFirstTokenMs, subjects, tags (every tag seen), from, to.
    """
    now = utcnow()
    end = end or now + timedelta(seconds=1)
    start = start or _month(now)
    turns = await svc.store.turns.stats(
        a.application_id, start=start, end=end, tag=tag, agent=agent
    )
    subjects, tags = await svc.store.usage.subjects_and_tags(
        a.application_id, start=start, end=end, tag=tag, agent=agent
    )
    return {
        **turns,
        "subjects": subjects,
        "tags": tags,
        "from": start.isoformat(),
        "to": end.isoformat(),
    }


class _BlockDumper(yaml.SafeDumper):
    """Writes texts with several lines as YAML blocks (``|``), readable in the exported file."""


def _block_text(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockDumper.add_representer(str, _block_text)


@router.get("/v1/admin/export")
async def export(svc: Svc, a: Admin) -> dict[str, str]:
    """The application as a config file (secrets left out: ``secret_env`` names a variable).

    Returns:
        yaml, filename.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    d = app.defaults or {}
    spec: dict[str, Any] = {"slug": app.slug, "name": app.name}
    settings = (
        ("monthly_cap_usd", app.monthly_cap_usd),
        ("daily_cap_usd", app.daily_cap_usd),
        ("subject_monthly_usd", d.get("subject_monthly_usd")),
        ("subject_daily_usd", d.get("subject_daily_usd")),
        ("currency", app.currency if app.currency and app.currency != "USD" else None),
        ("exchange_rate", app.exchange_rate),
        ("caps", app.caps or None),
        ("allowed_origins", app.allowed_origins or None),
        ("allowed_models", app.allowed_models or None),
        ("storage_mode", app.storage_mode),
        ("conversation_ttl_days", app.conversation_ttl_days),
        ("security_event_ttl_days", app.security_event_ttl_days),
        ("security", app.security or None),
        ("residency", app.residency or None),
        ("voice_engine", d.get("voice_engine")),
        ("locale", d.get("locale")),
        ("timezone", d.get("timezone")),
        ("messages", d.get("messages") or None),
        ("markup", d.get("markup") or None),
        ("tracing", {"content": d["tracing_content"]} if d.get("tracing_content") else None),
    )
    # An old *_usd field whose cap ``caps`` sets does not apply: left out, or the file would set
    # the same cap twice.
    shadowed = {
        LEGACY[scope, c["period"]]
        for scope in ("application", "subject")
        for c in (app.caps or {}).get(scope) or []
        if (scope, c["period"]) in LEGACY
    }
    spec.update({k: v for k, v in settings if v is not None and k not in shadowed})
    servers = []
    for s in await svc.store.mcp_servers.all(app.id):
        if s.connector:
            servers.append({"name": s.name, "connector": s.connector, "config": s.config or {}})
            continue
        server: dict[str, Any] = {"name": s.name, "url": s.url, "auth": s.auth}
        if s.auth == "secret":
            server["secret_env"] = "EVOKAI_MCP_" + s.name.upper().replace("-", "_") + "_SECRET"
        if s.timeout_seconds != DEFAULT_MCP_TIMEOUT:
            server["timeout_seconds"] = s.timeout_seconds
        if s.tool_prefix:
            server["tool_prefix"] = s.tool_prefix
        servers.append(server)
    agents = [
        AgentDef.model_validate(r.definition).model_dump(mode="json", exclude_defaults=True)
        for r in await svc.store.agents.active_all(app.id)
    ]
    grants: dict[str, dict[str, Any]] = {}
    sections = {kind: section for section, kind in YAML_SECTIONS.items()}
    for r in await svc.store.grants.all(app.id):
        grants.setdefault(sections[r.kind], {})[r.name] = r.value
    doc: dict[str, Any] = {"application": spec, "mcp_servers": servers}
    skills = []
    for r in await svc.store.skills.all(app.id):
        skill: dict[str, Any] = {"name": r.name, "description": r.description, "body": r.body}
        if files := await svc.store.skills.files(app.id, r.name):
            skill["files"] = dict(files)
        skills.append(skill)
    if skills:
        doc["skills"] = skills
    doc["agents"] = agents
    if grants:
        doc["grants"] = grants
    text = yaml.dump(doc, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=100)
    header = (
        f"# {app.name}: exported from the Evokai console. Skills are inline, with their files\n"
        "# (folders with a SKILL.md under skills_dir work too); MCP secrets come from the\n"
        "# environment variables named here.\n"
    )
    return {"yaml": header + text, "filename": f"{app.slug}.yaml"}
