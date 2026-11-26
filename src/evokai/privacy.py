"""Data protection: a subject's data exported or erased, sessions revoked, data residency enforced.

- **Export** (right of access, portability): everything Evokai keeps about one subject of one
  application as JSON: conversations with their messages and turns, memories, usage, traces,
  security events (already masked), bug reports, workflow runs, the subject's connections to
  third-party providers (never their tokens) and the consent each conversation started under.
- **Erase** (right to be forgotten): conversations, messages, replay events, turns, pending
  confirmations, memories, bug reports, traces, reviewers' notes, workflow runs (with their
  steps and approvals), OAuth connections (their tokens) and channel threads are deleted; usage
  rows lose
  the subject (a random ``erased:`` pseudonym takes its place) so the application's caps keep
  counting what was spent; security events lose the subject, the conversation and their snippet;
  every session token of the subject stops working at once.
- **Residency**: an application declares where its data lives and which inference geographies its
  models may run in (``model_regions: [eu]`` allows only ``eu.*`` Bedrock profiles, or in-region
  models when the deployment's region is in the EU); a model outside it is never called.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.repositories import Store, Who, _forget_sessions
from evokai.memory.store import memory_view

GEOGRAPHIES = ("eu", "us", "apac", "us-gov", "ca", "jp", "au", "global")
"""Bedrock cross-region inference profile prefixes."""


class Residency(BaseModel):
    """Where an application's data lives and where its models may run."""

    model_config = ConfigDict(extra="forbid")
    region: str | None = Field(default=None, max_length=40)
    """Where the data is stored (a declaration, e.g. ``eu-north-1``)."""
    model_regions: list[str] | None = None
    """Inference geographies allowed: ``eu``, ``us``, ``apac``, ``global``... (``eu`` allows
    ``eu.*`` Bedrock profiles and in-region models of an EU deployment)."""
    models: list[str] | None = None
    """Further model patterns allowed (``provider:model`` globs), e.g. a self-hosted model."""

    @field_validator("model_regions")
    @classmethod
    def _regions(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            v = [x.lower() for x in v]
            if bad := [x for x in v if x not in GEOGRAPHIES]:
                raise ValueError(f"unknown model regions: {bad}; use {', '.join(GEOGRAPHIES)}")
        return v

    def stored(self) -> dict[str, Any] | None:
        """The declaration as stored.

        Returns:
            A JSON-able dict, or None when empty.
        """
        return self.model_dump(exclude_none=True) or None


def subject_key(signing_key: str, application_id: str, subject: str) -> str:
    """A keyed hash of a subject (revocations and audit entries never hold the subject itself).

    Returns:
        Hex digest.
    """
    msg = f"{application_id}\x00{subject}".encode()
    return hmac.new(f"evokai-subject:{signing_key}".encode(), msg, hashlib.sha256).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def export_subject(store: Store, who: Who, *, amounts: bool) -> dict[str, Any]:
    """Everything Evokai keeps about a subject.

    Args:
        store: Repositories.
        who: Application and subject.
        amounts: Include costs (server to server); a console login gets tokens only.

    Returns:
        The export.
    """
    async with store.db.session() as s:
        sessions = list(
            (
                await s.execute(
                    sa
                    .select(m.Session)
                    .where(
                        m.Session.application_id == who.application_id,
                        m.Session.subject == who.subject,
                    )
                    .order_by(m.Session.created_at)
                )
            )
            .scalars()
            .all()
        )
        ids = [x.id for x in sessions]
        messages = (
            list(
                (
                    await s.execute(
                        sa
                        .select(m.Message)
                        .where(m.Message.session_id.in_(ids))
                        .order_by(m.Message.session_id, m.Message.position)
                    )
                )
                .scalars()
                .all()
            )
            if ids
            else []
        )
        summaries = (
            list(
                (
                    await s.execute(
                        sa.select(m.ConversationSummary).where(
                            m.ConversationSummary.session_id.in_(ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            if ids
            else []
        )
        files = (
            list(
                (
                    await s.execute(
                        sa
                        .select(m.Attachment)
                        .where(
                            m.Attachment.session_id.in_(ids),
                            m.Attachment.application_id == who.application_id,
                            m.Attachment.subject == who.subject,
                        )
                        .order_by(m.Attachment.created_at)
                    )
                )
                .scalars()
                .all()
            )
            if ids
            else []
        )
        turns = (
            list(
                (
                    await s.execute(
                        sa
                        .select(m.Turn)
                        .where(m.Turn.session_id.in_(ids))
                        .order_by(m.Turn.started_at)
                    )
                )
                .scalars()
                .all()
            )
            if ids
            else []
        )
        usage = list(
            (
                await s.execute(
                    sa
                    .select(m.UsageRecord)
                    .where(
                        m.UsageRecord.application_id == who.application_id,
                        m.UsageRecord.subject == who.subject,
                    )
                    .order_by(m.UsageRecord.created_at)
                )
            )
            .scalars()
            .all()
        )
        reports = list(
            (
                await s.execute(
                    sa.select(m.BugReport).where(
                        m.BugReport.application_id == who.application_id,
                        m.BugReport.subject == who.subject,
                    )
                )
            )
            .scalars()
            .all()
        )
    by_session: dict[str, list[dict[str, Any]]] = {}
    for x in messages:
        by_session.setdefault(x.session_id, []).append({
            "role": x.role,
            "text": x.text,
            "events": x.events or [],
            "interrupted": x.interrupted,
            "feedback": x.feedback,
            "createdAt": _iso(x.created_at),
        })
    summary_of = {
        x.session_id: {
            "text": x.summary,
            "messages": x.messages,
            "updatedAt": _iso(x.updated_at),
        }
        for x in summaries
    }
    # The files the person attached, as they are kept (images without their metadata).
    files_of: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        files_of.setdefault(f.session_id, []).append({
            "id": f.id,
            "messageId": f.message_id,
            "name": f.name,
            "mediaType": f.media_type,
            "size": f.size,
            "sha256": f.sha256,
            "dataBase64": base64.b64encode(f.data).decode(),
            "createdAt": _iso(f.created_at),
        })
    turns_of: dict[str, list[dict[str, Any]]] = {}
    for t in turns:
        turns_of.setdefault(t.session_id, []).append({
            "id": t.id,
            "mode": t.mode,
            "status": t.status,
            "reason": t.reason,
            "startedAt": _iso(t.started_at),
            "endedAt": _iso(t.ended_at),
        })

    def usage_row(u: m.UsageRecord) -> dict[str, Any]:
        row = {
            "at": _iso(u.created_at),
            "agent": u.agent_name,
            "kind": u.kind,
            "model": u.model,
            "inputTokens": u.input_tokens,
            "outputTokens": u.output_tokens,
            "toolCalls": u.tool_calls,
        }
        if amounts:
            row["costUsd"] = u.cost_micro_usd / 1e6
        return row

    events = await store.security_events.for_subject(who)
    traces = await store.traces.for_subject(who)
    kept = await store.capabilities.for_subject(who.application_id, who.subject)
    workflow_runs = await store.workflows.for_subject(who.application_id, who.subject)
    connections = await store.connections.of_subject(who.application_id, who.subject)
    return {
        "subject": who.subject,
        "exportedAt": utcnow().isoformat(),
        "conversations": [
            {
                "id": x.id,
                "title": x.title,
                "agent": x.agent_name,
                "agentVersion": x.agent_version,
                "storageMode": x.storage_mode,
                "status": x.status,
                "consent": x.consent,
                # The prompt modules and locale chosen at mint, free-text values included (a
                # name, a preference): the application may have written anything about the person.
                "promptOptions": x.prompt_options,
                "createdAt": _iso(x.created_at),
                "updatedAt": _iso(x.updated_at),
                "messages": by_session.get(x.id, []),
                "attachments": files_of.get(x.id, []),
                "summary": summary_of.get(x.id),
                "turns": turns_of.get(x.id, []),
            }
            for x in sessions
        ],
        "memories": [
            {"fact": x.fact, **memory_view(x), "supersededBy": x.superseded_by}
            for x in await _all_memories(store, who)
        ],
        "memoryConsent": _consent(await store.memories.consent(who)),
        "capabilityRecords": [
            {
                "capability": x.capability,
                "scope": x.scope,
                "key": x.key,
                "conversationId": x.session_id,
                "content": x.value,
                "createdAt": _iso(x.created_at),
                "updatedAt": _iso(x.updated_at),
                "expiresAt": _iso(x.expires_at),
            }
            for x in kept
        ],
        "usage": [usage_row(u) for u in usage],
        "traces": [
            {
                "id": x.id,
                "kind": x.kind,
                "conversationId": x.session_id,
                "agent": x.agent_name,
                "agentVersion": x.agent_version,
                "status": x.status,
                "reason": x.reason,
                "startedAt": _iso(x.started_at),
                "durationMs": x.duration_ms,
                "spans": x.spans or [],
            }
            for x in traces
        ],
        "workflowRuns": [
            {
                "id": x.id,
                "workflow": x.workflow_name,
                "version": x.workflow_version,
                "status": x.status,
                "trigger": x.trigger,
                "input": x.input,
                "output": x.output,
                "startedAt": _iso(x.started_at),
                "finishedAt": _iso(x.finished_at),
            }
            for x in workflow_runs
        ],
        "securityEvents": [
            {
                "at": _iso(e.created_at),
                "control": e.control,
                "rule": e.rule,
                "action": e.action,
                "snippet": e.snippet,
            }
            for e in events
        ],
        "bugReports": [
            {"description": r.description, "context": r.context, "createdAt": _iso(r.created_at)}
            for r in reports
        ],
        "connections": [
            {
                "provider": c.provider,
                "account": c.account,
                "scopes": c.scopes or [],
                "status": c.status,
                "connectedAt": _iso(c.created_at),
                "lastUsedAt": _iso(c.last_used_at),
            }
            for c in connections
        ],
    }


async def _all_memories(store: Store, who: Who) -> list[m.Memory]:
    """Every memory of a subject, superseded and expired ones included (an export is complete).

    Returns:
        Rows, oldest first.
    """
    async with store.db.session() as s:
        r = m.Memory
        q = (
            sa
            .select(r)
            .where(r.application_id == who.application_id, r.subject == who.subject)
            .order_by(r.created_at)
        )
        return list((await s.execute(q)).scalars().all())


def _consent(row: m.MemoryConsent | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"granted": row.granted, "source": row.source, "decidedAt": _iso(row.decided_at)}


async def erase_subject(
    store: Store, who: Who, signing_key: str
) -> tuple[dict[str, int], list[str]]:
    """Erase a subject: delete their content, de-identify what must keep counting, revoke tokens.

    Returns:
        How many rows of each kind were deleted or de-identified, and the conversations erased.
    """
    out: dict[str, int] = {}
    async with store.db.session() as s:
        ids = list(
            (
                await s.execute(
                    sa.select(m.Session.id).where(
                        m.Session.application_id == who.application_id,
                        m.Session.subject == who.subject,
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await _forget_sessions(s, ids)
        out["conversations"] = len(ids)
        r = await s.execute(
            sa.delete(m.Memory).where(
                m.Memory.application_id == who.application_id, m.Memory.subject == who.subject
            )
        )
        out["memories"] = int(getattr(r, "rowcount", 0) or 0)
        await s.execute(
            sa.delete(m.Attachment).where(
                m.Attachment.application_id == who.application_id,
                m.Attachment.subject == who.subject,
            )
        )
        r = await s.execute(
            sa.delete(m.CapabilityRecord).where(
                m.CapabilityRecord.application_id == who.application_id,
                m.CapabilityRecord.subject == who.subject,
            )
        )
        out["capabilityRecords"] = int(getattr(r, "rowcount", 0) or 0)
        await s.execute(
            sa.delete(m.MemoryConsent).where(
                m.MemoryConsent.application_id == who.application_id,
                m.MemoryConsent.subject == who.subject,
            )
        )
        r = await s.execute(
            sa.delete(m.BugReport).where(
                m.BugReport.application_id == who.application_id,
                m.BugReport.subject == who.subject,
            )
        )
        out["bugReports"] = int(getattr(r, "rowcount", 0) or 0)
        r = await s.execute(
            sa
            .update(m.UsageRecord)
            .where(
                m.UsageRecord.application_id == who.application_id,
                m.UsageRecord.subject == who.subject,
            )
            .values(subject=f"erased:{new_id()[:8]}", session_id=None, turn_id=None)
        )
        out["usageDeidentified"] = int(getattr(r, "rowcount", 0) or 0)
        await s.execute(
            sa.delete(m.Turn).where(
                m.Turn.application_id == who.application_id, m.Turn.subject == who.subject
            )
        )
        await s.execute(
            sa.delete(m.PendingAction).where(
                m.PendingAction.application_id == who.application_id,
                m.PendingAction.subject == who.subject,
            )
        )
        r = await s.execute(
            sa.delete(m.Trace).where(
                m.Trace.application_id == who.application_id, m.Trace.subject == who.subject
            )
        )
        out["traces"] = int(getattr(r, "rowcount", 0) or 0)
        r = await s.execute(
            sa.delete(m.ReviewNote).where(
                m.ReviewNote.application_id == who.application_id,
                m.ReviewNote.subject == who.subject,
            )
        )
        out["reviewNotes"] = int(getattr(r, "rowcount", 0) or 0)
        out["workflowRuns"] = await store.workflows.forget_subject(
            s, who.application_id, who.subject
        )
        r = await s.execute(
            sa.delete(m.OAuthConnection).where(
                m.OAuthConnection.application_id == who.application_id,
                m.OAuthConnection.subject == who.subject,
            )
        )
        out["connections"] = int(getattr(r, "rowcount", 0) or 0)
        r = await s.execute(
            sa.delete(m.ChannelThread).where(
                m.ChannelThread.application_id == who.application_id,
                m.ChannelThread.subject == who.subject,
            )
        )
        out["channelThreads"] = int(getattr(r, "rowcount", 0) or 0)
        s.add(
            m.SubjectRevocation(
                id=new_id(),
                application_id=who.application_id,
                subject_key=subject_key(signing_key, who.application_id, who.subject),
                revoked_at=utcnow(),
            )
        )
    out["securityEventsDeidentified"] = await store.security_events.forget_subject(who)
    return out, ids


async def revoke_sessions(store: Store, who: Who, signing_key: str) -> None:
    """Make every session token of a subject issued until now invalid."""
    async with store.db.session() as s:
        s.add(
            m.SubjectRevocation(
                id=new_id(),
                application_id=who.application_id,
                subject_key=subject_key(signing_key, who.application_id, who.subject),
                revoked_at=utcnow(),
            )
        )


async def revoked_at(store: Store, application_id: str, key: str) -> datetime | None:
    """When a subject's sessions were last revoked.

    Returns:
        The time, or None.
    """
    async with store.db.session() as s:
        q = sa.select(sa.func.max(m.SubjectRevocation.revoked_at)).where(
            m.SubjectRevocation.application_id == application_id,
            m.SubjectRevocation.subject_key == key,
        )
        return (await s.execute(q)).scalar_one_or_none()


async def purge_revocations(store: Store, older_than: datetime) -> int:
    """Forget revocations older than the longest token life (they cannot matter any more).

    Returns:
        How many were deleted.
    """
    async with store.db.session() as s:
        r = await s.execute(
            sa.delete(m.SubjectRevocation).where(m.SubjectRevocation.revoked_at < older_than)
        )
        return int(getattr(r, "rowcount", 0) or 0)


def model_geography(model: str, aws_region: str) -> str | None:
    """Where a model runs: the provider catalog, else Bedrock's id (``evokai.protection``).

    Returns:
        ``eu``, ``us``, ``apac``, ``global``..., or None when nobody states it.
    """
    from evokai import protection

    return protection.model_geography(model, aws_region)


def residency_allows(residency: Any, model: str, aws_region: str) -> bool:
    """Whether the application's data residency and profile let a model answer.

    Args:
        residency: ``evokai.protection.protection(app)`` (or an older bare residency dict).
        model: ``provider:model``.
        aws_region: The deployment's region.

    Returns:
        True when every rule holds (``evokai.protection.residency_allows``).
    """
    from evokai import protection

    return protection.residency_allows(residency, model, aws_region)
