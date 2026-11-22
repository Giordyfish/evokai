"""Traces, conversation review and version comparison, for the console and the admin API.

Reads need the viewer role, notes the operator role (``evokai.identity.roles``). Nothing here
carries an amount: a trace's spend is a share of the application's monthly cap (``capPct``) and a
version's usage is its share of the agent's usage in the period.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.assistant.privacy import NOBODY, key_of
from evokai.core.errors import BadRequest, NotFound
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.identity.models import AppPrincipal
from evokai.privacy import subject_key
from evokai.services import Services
from evokai.sessions.limits import month_cap_usd, share_pct

router = APIRouter(prefix="/v1/admin", tags=["traces"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]

SUBJECT_KEY = re.compile(r"^[0-9a-f]{16}$")
LABEL = r"^[a-z0-9][a-z0-9_.-]{0,39}$"


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


def _key(svc: Services, application_id: str, subject: str | None) -> str | None:
    """The subject key to search: given as a key (16 hex characters), or computed from a subject.

    Returns:
        The key, or None for no subject filter.
    """
    if not subject:
        return None
    subject = subject.strip()
    if SUBJECT_KEY.match(subject):
        return subject
    return subject_key(svc.keyring.signing_key, application_id, subject)[:16]


def summary(row: Any, app: m.Application) -> dict[str, Any]:
    """A trace without its spans, as the console lists it (no amounts).

    Returns:
        The summary.
    """
    return {
        "id": row.id,
        "kind": row.kind,
        "conversationId": row.session_id,
        "subjectKey": row.subject_key,
        "agent": row.agent_name,
        "version": row.agent_version,
        "mode": row.mode,
        "status": row.status,
        "reason": row.reason,
        "startedAt": row.started_at.isoformat(),
        "durationMs": row.duration_ms,
        "firstTokenMs": row.first_token_ms,
        "model": row.model,
        "fallback": row.fallback,
        "inputTokens": row.input_tokens,
        "outputTokens": row.output_tokens,
        "cacheReadTokens": row.cache_read_tokens,
        "cacheWriteTokens": row.cache_write_tokens,
        "modelCalls": row.model_calls,
        "toolCalls": row.tool_calls,
        "toolErrors": row.tool_errors,
        "guardEvents": row.guard_events,
        "error": row.error,
        "feedback": row.feedback,
        "content": row.content,
        "capPct": share_pct(row.cost_micro_usd / 1e6, month_cap_usd(app))
        if row.cost_micro_usd
        else 0,
    }


def note_view(n: m.ReviewNote) -> dict[str, Any]:
    """A reviewer's note as the console shows it.

    Returns:
        id, turnId, label, note, author, createdAt.
    """
    return {
        "id": n.id,
        "turnId": n.turn_id,
        "label": n.label,
        "note": n.note,
        "author": n.author,
        "createdAt": n.created_at.isoformat(),
    }


@router.get("/traces")
async def traces(
    svc: Svc,
    a: Admin,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
    agent: str | None = None,
    version: str | None = None,
    subject: str | None = None,
    outcome: str | None = None,
    kind: str | None = None,
    guard: bool = False,
    feedback: Annotated[str | None, Query(pattern="^(up|down|any)$")] = None,
    minMs: Annotated[int | None, Query(ge=0)] = None,
    errors: bool = False,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Traces of turns and server runs, newest first, with filters.

    ``subject`` is a subject or its key (as the list shows it); ``outcome`` matches a status
    (``done``, ``failed``, ``blocked``, ``interrupted``) or a reason (``end_turn``, ``confirm``,
    ``limit``, ``guard``...); ``minMs`` keeps the slow ones. Pass ``next`` back as ``before``.

    Returns:
        items (summaries, no spans) and next.
    """
    app = await _app(svc, a)
    wanted = _key(svc, app.id, subject)
    if app.system:
        # Each person's own traces only (``evokai.assistant.privacy``).
        mine = key_of(svc, app.id, a.subject or NOBODY)
        if wanted and wanted != mine:
            return {"items": [], "next": None, "storage": app.storage_mode}
        wanted = mine
    rows = await svc.store.traces.query(
        app.id,
        limit=limit,
        before=before,
        start=start,
        end=end,
        agent=agent,
        version=version,
        subject_key=wanted,
        outcome=outcome,
        kind=kind,
        guard=guard,
        feedback=feedback,
        min_ms=minMs,
        errors=errors,
    )
    items = [summary(r, app) for r in rows]
    return {
        "items": items,
        "next": items[-1]["startedAt"] if len(items) == limit else None,
        "storage": app.storage_mode,
    }


@router.get("/traces/{trace_id}")
async def trace(trace_id: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """One trace with its spans (the waterfall), and the conversation it belongs to.

    Returns:
        The summary, ``spans`` (id, parent, kind, name, start and ms from the trace's start,
        status, attrs, content), ``conversation`` (whether it can be opened) and its notes.

    Raises:
        NotFound: Not a trace of this application.
    """
    app = await _app(svc, a)
    row = await svc.store.traces.get(app.id, trace_id)
    if row is None or (app.system and row.subject != (a.subject or NOBODY)):
        raise NotFound()
    readable = False
    notes: list[dict[str, Any]] = []
    if row.session_id:
        session = await svc.store.sessions.get(
            # The conversation is opened only where it is kept in Evokai and still alive.
            Who(app.id, row.subject),
            row.session_id,
        )
        readable = session is not None and (session.storage_mode or app.storage_mode) == "evokai"
        notes = [
            note_view(n)
            for n in await svc.store.review_notes.for_session(app.id, row.session_id)
            if n.turn_id == row.id
        ]
    return {
        **summary(row, app),
        "spans": [_no_cost(s) for s in row.spans or []],
        "conversation": {"id": row.session_id, "readable": readable} if row.session_id else None,
        "notes": notes,
    }


def _no_cost(span: dict[str, Any]) -> dict[str, Any]:
    """A span as the console sees it (spans hold no amount; this keeps it so).

    Returns:
        The span.
    """
    attrs = span.get("attrs")
    if isinstance(attrs, dict) and any("cost" in k.lower() for k in attrs):
        span = {**span, "attrs": {k: v for k, v in attrs.items() if "cost" not in k.lower()}}
    return span


def _pct(values: list[int], q: float) -> int | None:
    """A percentile by nearest rank.

    Returns:
        The value, or None without values.
    """
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q * len(s) + 0.5) - 1))]


def _rate(part: float, whole: float) -> float | None:
    return round(part * 100 / whole, 1) if whole else None


@router.get("/agents/{name}/version-stats")
async def version_stats(
    name: str,
    svc: Svc,
    a: Admin,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
) -> dict[str, Any]:
    """Each version of an agent side by side, over a period (default: the last 30 days).

    Eval cases are left out (they are not people's turns). Shares only: ``usageShare`` is the
    version's part of the agent's usage in the period, in percent.

    Returns:
        items per version (newest first): version, active, createdAt, turns, medianMs, p95Ms,
        medianFirstTokenMs, errorRate, toolCalls, toolErrorRate, guardTurns, guardRate,
        thumbsUp, thumbsDown, feedbackRatio, usageShare, avgInputTokens, avgOutputTokens,
        outcomes; and from, to.

    Raises:
        NotFound: No such agent.
    """
    app = await _app(svc, a)
    versions = await svc.store.agents.versions(app.id, name)
    if not versions:
        raise NotFound()
    end = end or utcnow() + timedelta(seconds=1)
    start = start or end - timedelta(days=30)
    rows = await svc.store.traces.for_versions(app.id, name, start, end)
    total_cost = sum(r.cost_micro_usd for r in rows) or 0
    by: dict[str, list[Any]] = {}
    for r in rows:
        by.setdefault(r.agent_version, []).append(r)
    known = {v.version: v for v in versions}
    order = [v.version for v in versions] + sorted(set(by) - set(known), reverse=True)
    items = []
    for version in order:
        rs = by.get(version, [])
        n = len(rs)
        tool_calls = sum(r.tool_calls for r in rs)
        up = sum(1 for r in rs if (r.feedback or 0) > 0)
        down = sum(1 for r in rs if (r.feedback or 0) < 0)
        outcomes: dict[str, int] = {}
        for r in rs:
            key = r.reason or r.status
            outcomes[key] = outcomes.get(key, 0) + 1
        rec = known.get(version)
        items.append({
            "version": version,
            "active": bool(rec and rec.active),
            "createdAt": rec.created_at.isoformat() if rec else None,
            "turns": n,
            "medianMs": _pct([r.duration_ms for r in rs], 0.5),
            "p95Ms": _pct([r.duration_ms for r in rs], 0.95),
            "medianFirstTokenMs": _pct(
                [r.first_token_ms for r in rs if r.first_token_ms is not None], 0.5
            ),
            "errorRate": _rate(sum(1 for r in rs if r.error), n),
            "toolCalls": tool_calls,
            "toolErrorRate": _rate(sum(r.tool_errors for r in rs), tool_calls),
            "guardTurns": sum(1 for r in rs if r.guard_events),
            "guardRate": _rate(sum(1 for r in rs if r.guard_events), n),
            "thumbsUp": up,
            "thumbsDown": down,
            "feedbackRatio": _rate(up, up + down),
            "usageShare": _rate(sum(r.cost_micro_usd for r in rs), total_cost),
            "avgInputTokens": round(sum(r.input_tokens for r in rs) / n) if n else None,
            "avgOutputTokens": round(sum(r.output_tokens for r in rs) / n) if n else None,
            "outcomes": outcomes,
        })
    return {"agent": name, "items": items, "from": start.isoformat(), "to": end.isoformat()}


class NoteBody(BaseModel):
    """A reviewer's note on a conversation or one of its turns."""

    turnId: str | None = Field(default=None, max_length=36)
    label: str | None = Field(default=None, pattern=LABEL)
    """A short label (``good``, ``wrong_answer``, ``wrong_tool``, ``unsafe``, ``slow``...)."""
    note: str = Field(default="", max_length=4_000)


async def _conversation(
    svc: Services, app: m.Application, conversation_id: str, a: AppPrincipal
) -> m.Session:
    """A conversation of the application kept in Evokai (reviewable).

    Returns:
        The conversation.

    Raises:
        NotFound: Unknown, expired, kept by the application itself, or another person's on a
            system application.
    """
    rows = await svc.store.sessions.review(app.id, limit=1, conversation_id=conversation_id)
    if not rows or (app.system and rows[0]["subject"] != (a.subject or NOBODY)):
        raise NotFound()
    session = await svc.store.sessions.get(Who(app.id, rows[0]["subject"]), conversation_id)
    if session is None:
        raise NotFound()
    return session


@router.post("/conversations/{conversation_id}/notes", status_code=201)
async def add_note(
    conversation_id: str, body: NoteBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Add a reviewer's note or label (operator). Audited with its label and length, not its text.

    Returns:
        The note.

    Raises:
        BadRequest: Neither a label nor a note.
    """
    if not body.label and not body.note.strip():
        raise BadRequest("Give a label or a note.", code="empty")
    app = await _app(svc, a)
    session = await _conversation(svc, app, conversation_id, a)
    row = await svc.store.review_notes.add(
        application_id=app.id,
        session_id=session.id,
        turn_id=body.turnId,
        subject=session.subject,
        label=body.label,
        note=body.note.strip(),
        author=(a.actor or a.key_id)[:200],
    )
    await audit.record(
        request,
        a,
        "review.note",
        target=f"conversation:{session.id}",
        after={"id": row.id, "turnId": body.turnId, "label": body.label, "chars": len(row.note)},
    )
    return note_view(row)


@router.delete("/conversations/{conversation_id}/notes/{note_id}")
async def delete_note(
    conversation_id: str, note_id: str, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Delete a reviewer's note (operator, audited).

    Returns:
        ok.

    Raises:
        NotFound: No such note on this conversation.
    """
    app = await _app(svc, a)
    if app.system:
        await _conversation(svc, app, conversation_id, a)
    row = await svc.store.review_notes.delete(app.id, conversation_id, note_id)
    if row is None:
        raise NotFound()
    await audit.record(
        request,
        a,
        "review.note_delete",
        target=f"conversation:{conversation_id}",
        before={"id": row.id, "turnId": row.turn_id, "label": row.label, "chars": len(row.note)},
    )
    return {"ok": True}
