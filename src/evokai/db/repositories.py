"""Query layer: one repository per entity.

Every query on conversation data is scoped by application and subject; ids alone are never trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database

DEFAULT_TITLE = "Conversation"
"""Title of a conversation until its first message names it."""


@dataclass(frozen=True)
class Who:
    """The owner of conversation data: an application and its opaque subject."""

    application_id: str
    subject: str


def _rowcount(result: object) -> int:
    """Rows touched by an UPDATE or DELETE.

    Returns:
        The count (0 when the driver does not report it).
    """
    return int(getattr(result, "rowcount", 0) or 0)


def tags_key(tags: list[str]) -> str:
    """Encode tags for LIKE matching.

    Args:
        tags: Tags of a session.

    Returns:
        ",a,b," (sorted, deduplicated) or "" when there are none.
    """
    clean = sorted({t.strip().replace(",", " ") for t in tags if t and t.strip()})
    return f",{','.join(clean)}," if clean else ""


def _tags(key: str | None) -> list[str]:
    """Decode tags stored by ``tags_key``.

    Returns:
        The tags.
    """
    return [t for t in (key or "").split(",") if t]


@dataclass
class _Repo:
    db: Database


class ApplicationRepository(_Repo):
    """Applications and their keys."""

    async def all(self) -> list[m.Application]:
        """Every application (operator view).

        Returns:
            Applications by name.
        """
        async with self.db.session() as s:
            q = sa.select(m.Application).order_by(m.Application.name)
            return list((await s.execute(q)).scalars().all())

    async def by_slug(self, slug: str) -> m.Application | None:
        """Find an application by slug.

        Returns:
            The application or None.
        """
        async with self.db.session() as s:
            q = sa.select(m.Application).where(m.Application.slug == slug)
            return (await s.execute(q)).scalar_one_or_none()

    async def get(self, application_id: str) -> m.Application | None:
        """Find an application by id.

        Returns:
            The application or None.
        """
        async with self.db.session() as s:
            return await s.get(m.Application, application_id)

    async def create(self, slug: str, name: str, **fields: Any) -> m.Application:
        """Create an application.

        Returns:
            The new application.
        """
        async with self.db.session() as s:
            app = m.Application(id=new_id(), slug=slug, name=name, **fields)
            s.add(app)
            return app

    async def update(self, application_id: str, **fields: Any) -> None:
        """Change an application's settings (caps, origins, models)."""
        async with self.db.session() as s:
            q = sa.update(m.Application).where(m.Application.id == application_id)
            await s.execute(q.values(**fields))

    async def add_key(
        self, application_id: str, kind: str, prefix: str, secret_hash: str, label: str = ""
    ) -> str:
        """Store a key's hash.

        Returns:
            The key id.
        """
        async with self.db.session() as s:
            key = m.ApiKey(
                id=new_id(),
                application_id=application_id,
                kind=kind,
                prefix=prefix,
                secret_hash=secret_hash,
                label=label,
            )
            s.add(key)
            return key.id

    async def key_by_prefix(self, prefix: str) -> m.ApiKey | None:
        """Find a live key by its public prefix.

        Returns:
            The key row, or None if unknown or revoked.
        """
        async with self.db.session() as s:
            q = sa.select(m.ApiKey).where(m.ApiKey.prefix == prefix, m.ApiKey.revoked_at.is_(None))
            return (await s.execute(q)).scalar_one_or_none()

    async def touch_key(self, key_id: str) -> None:
        """Record a key's last use."""
        async with self.db.session() as s:
            q = sa.update(m.ApiKey).where(m.ApiKey.id == key_id)
            await s.execute(q.values(last_used_at=utcnow()))

    async def keys(self, application_id: str) -> list[m.ApiKey]:
        """List an application's keys (hashes never leave the service).

        Returns:
            Key rows, newest first.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.ApiKey)
                .where(m.ApiKey.application_id == application_id)
                .order_by(m.ApiKey.created_at.desc())
            )
            return list((await s.execute(q)).scalars().all())

    async def key(self, key_id: str) -> m.ApiKey | None:
        """A key by id, revoked or not.

        Returns:
            The key row or None.
        """
        async with self.db.session() as s:
            return await s.get(m.ApiKey, key_id)

    async def revoke_key(self, application_id: str, key_id: str) -> bool:
        """Revoke a key.

        Returns:
            Whether a live key was revoked.
        """
        async with self.db.session() as s:
            q = sa.update(m.ApiKey).where(
                m.ApiKey.id == key_id,
                m.ApiKey.application_id == application_id,
                m.ApiKey.revoked_at.is_(None),
            )
            return (_rowcount(await s.execute(q.values(revoked_at=utcnow())))) > 0


class AgentRepository(_Repo):
    """Versioned agent definitions of an application."""

    async def put(
        self,
        application_id: str,
        name: str,
        version: str,
        definition: dict,
        created_by: str | None = None,
        *,
        activate: bool = True,
    ) -> bool:
        """Store a definition version and make it the active one (no-op if unchanged).

        An older version saved again becomes the active one (a restore). With ``activate=False``
        a new version is stored as ``pending`` (the red team gate publishes it later) and the
        active version stays.

        Returns:
            Whether a new version was stored.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            same = (a.application_id == application_id, a.name == name)
            exists = await s.execute(sa.select(a.id).where(*same, a.version == version))
            if exists.scalar_one_or_none():
                if activate:
                    await s.execute(sa.update(a).where(*same).values(active=a.version == version))
                    await s.execute(
                        sa.update(a).where(*same, a.version == version).values(publish_state=None)
                    )
                return False
            if activate:
                await s.execute(sa.update(a).where(*same).values(active=False))
            s.add(
                a(
                    id=new_id(),
                    application_id=application_id,
                    name=name,
                    version=version,
                    definition=definition,
                    created_by=created_by,
                    active=activate,
                    publish_state=None if activate else "pending",
                )
            )
            return True

    async def publish(self, application_id: str, name: str, version: str) -> bool:
        """Make a stored version the active one (a version the red team gate let through).

        Returns:
            Whether the version exists.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            same = (a.application_id == application_id, a.name == name)
            exists = await s.execute(sa.select(a.id).where(*same, a.version == version))
            if exists.scalar_one_or_none() is None:
                return False
            await s.execute(sa.update(a).where(*same).values(active=a.version == version))
            await s.execute(
                sa.update(a).where(*same, a.version == version).values(publish_state=None)
            )
            return True

    async def set_publish_state(
        self, application_id: str, name: str, version: str, state: str | None
    ) -> None:
        """Mark a version ``pending``, ``blocked`` or published as usual (None)."""
        async with self.db.session() as s:
            a = m.AgentRecord
            await s.execute(
                sa
                .update(a)
                .where(a.application_id == application_id, a.name == name, a.version == version)
                .values(publish_state=state)
            )

    async def active(self, application_id: str, name: str) -> m.AgentRecord | None:
        """The active version of an agent.

        Returns:
            The record or None.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            q = sa.select(a).where(a.application_id == application_id, a.name == name, a.active)
            return (await s.execute(q)).scalar_one_or_none()

    async def version(self, application_id: str, name: str, version: str) -> m.AgentRecord | None:
        """A specific version (for conversations pinned to it).

        Returns:
            The record or None.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            q = sa.select(a).where(
                a.application_id == application_id, a.name == name, a.version == version
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def active_all(self, application_id: str) -> list[m.AgentRecord]:
        """Active agents of an application.

        Returns:
            Records.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            q = sa.select(a).where(a.application_id == application_id, a.active).order_by(a.name)
            return list((await s.execute(q)).scalars().all())

    async def versions(self, application_id: str, name: str) -> list[m.AgentRecord]:
        """Every version of an agent, newest first.

        Returns:
            Records.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            q = (
                sa
                .select(a)
                .where(a.application_id == application_id, a.name == name)
                .order_by(a.created_at.desc())
            )
            return list((await s.execute(q)).scalars().all())

    async def history(self, application_id: str, limit: int = 30) -> list[m.AgentRecord]:
        """The latest versions saved across the application's agents, newest first.

        Returns:
            Records.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            q = (
                sa
                .select(a)
                .where(a.application_id == application_id)
                .order_by(a.created_at.desc())
                .limit(limit)
            )
            return list((await s.execute(q)).scalars().all())

    async def deactivate(self, application_id: str, name: str) -> bool:
        """Retire an agent (its versions stay for pinned conversations).

        Returns:
            Whether an active agent was retired.
        """
        async with self.db.session() as s:
            a = m.AgentRecord
            q = sa.update(a).where(a.application_id == application_id, a.name == name, a.active)
            return (_rowcount(await s.execute(q.values(active=False)))) > 0


class McpServerRepository(_Repo):
    """MCP servers registered by an application."""

    async def put(self, application_id: str, name: str, **fields: Any) -> None:
        """Create or replace a server by name (what was last seen of it is kept)."""
        async with self.db.session() as s:
            r = m.McpServerRecord
            same = (r.application_id == application_id, r.name == name)
            row = (await s.execute(sa.select(r.id).where(*same))).scalar_one_or_none()
            if row is None:
                s.add(r(id=new_id(), application_id=application_id, name=name, **fields))
            else:
                await s.execute(sa.update(r).where(r.id == row).values(**fields))

    async def seen(
        self,
        server_id: str,
        *,
        status: str,
        latency_ms: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> None:
        """Record a listing or a test of a server: its status, latency and tools."""
        values: dict[str, Any] = {
            "status": status,
            "latency_ms": latency_ms,
            "checked_at": utcnow(),
            "last_error": (error or "")[:300] or None,
        }
        if tools is not None:
            values["tools"] = tools
        async with self.db.session() as s:
            r = m.McpServerRecord
            await s.execute(sa.update(r).where(r.id == server_id).values(**values))

    async def all(self, application_id: str) -> list[m.McpServerRecord]:
        """Servers of an application.

        Returns:
            Records ordered by name.
        """
        async with self.db.session() as s:
            r = m.McpServerRecord
            q = sa.select(r).where(r.application_id == application_id).order_by(r.name)
            return list((await s.execute(q)).scalars().all())

    async def delete(self, application_id: str, name: str) -> bool:
        """Remove a server.

        Returns:
            Whether it existed.
        """
        async with self.db.session() as s:
            r = m.McpServerRecord
            q = sa.delete(r).where(r.application_id == application_id, r.name == name)
            return (_rowcount(await s.execute(q))) > 0


class SkillRepository(_Repo):
    """Skills of an application, with their files (references, data, scripts)."""

    async def put(
        self,
        application_id: str,
        name: str,
        description: str,
        body: str,
        files: list[tuple[str, str]] | None = None,
    ) -> bool:
        """Create or replace a skill by name (nothing is written when it is unchanged).

        Args:
            application_id: The application.
            name: The skill.
            description: Its description (the caller checked the limits).
            body: The body of SKILL.md.
            files: (path, text) of all its files, replacing the stored ones; None keeps them.

        Returns:
            Whether anything changed.
        """
        changed = False
        async with self.db.session() as s:
            r = m.SkillRecord
            same = (r.application_id == application_id, r.name == name)
            row = (await s.execute(sa.select(r).where(*same))).scalar_one_or_none()
            if row is None or row.description != description or row.body != body:
                changed = True
                await s.execute(sa.delete(r).where(*same))
                s.add(
                    r(
                        id=new_id(),
                        application_id=application_id,
                        name=name,
                        description=description,
                        body=body,
                    )
                )
            if files is not None:
                f = m.SkillFileRecord
                mine = (f.application_id == application_id, f.skill_name == name)
                found = await s.execute(sa.select(f.path, f.content).where(*mine))
                old = dict(found.all())
                if old != dict(files):
                    changed = True
                    await s.execute(sa.delete(f).where(*mine))
                    s.add_all(_file_rows(application_id, name, files))
        return changed

    async def all(self, application_id: str) -> list[m.SkillRecord]:
        """Skills of an application.

        Returns:
            Records ordered by name.
        """
        async with self.db.session() as s:
            r = m.SkillRecord
            q = sa.select(r).where(r.application_id == application_id).order_by(r.name)
            return list((await s.execute(q)).scalars().all())

    async def file_index(self, application_id: str) -> dict[str, list[tuple[str, int, str]]]:
        """The files of every skill, without their texts.

        Returns:
            Skill name to (path, size, title), by path.
        """
        out: dict[str, list[tuple[str, int, str]]] = {}
        async with self.db.session() as s:
            f = m.SkillFileRecord
            q = (
                sa
                .select(f.skill_name, f.path, f.size, f.title)
                .where(f.application_id == application_id)
                .order_by(f.skill_name, f.path)
            )
            for skill, path, size, title in await s.execute(q):
                out.setdefault(skill, []).append((path, int(size), title or ""))
        return out

    async def files(self, application_id: str, name: str) -> list[tuple[str, str]]:
        """A skill's files with their texts.

        Returns:
            (path, text), by path.
        """
        async with self.db.session() as s:
            f = m.SkillFileRecord
            q = (
                sa
                .select(f.path, f.content)
                .where(f.application_id == application_id, f.skill_name == name)
                .order_by(f.path)
            )
            return [(p, c) for p, c in await s.execute(q)]

    async def file(self, application_id: str, name: str, path: str) -> str | None:
        """The text of one file of a skill.

        Returns:
            The text, or None.
        """
        async with self.db.session() as s:
            f = m.SkillFileRecord
            q = sa.select(f.content).where(
                f.application_id == application_id, f.skill_name == name, f.path == path
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def put_file(self, application_id: str, name: str, path: str, content: str) -> None:
        """Create or replace one file of a skill (the caller checked the limits)."""
        async with self.db.session() as s:
            f = m.SkillFileRecord
            await s.execute(
                sa.delete(f).where(
                    f.application_id == application_id, f.skill_name == name, f.path == path
                )
            )
            s.add_all(_file_rows(application_id, name, [(path, content)]))

    async def delete_file(self, application_id: str, name: str, path: str) -> bool:
        """Remove one file of a skill.

        Returns:
            Whether it existed.
        """
        async with self.db.session() as s:
            f = m.SkillFileRecord
            q = sa.delete(f).where(
                f.application_id == application_id, f.skill_name == name, f.path == path
            )
            return (_rowcount(await s.execute(q))) > 0

    async def delete(self, application_id: str, name: str) -> bool:
        """Remove a skill and its files.

        Returns:
            Whether it existed.
        """
        async with self.db.session() as s:
            r = m.SkillRecord
            f = m.SkillFileRecord
            await s.execute(
                sa.delete(f).where(f.application_id == application_id, f.skill_name == name)
            )
            q = sa.delete(r).where(r.application_id == application_id, r.name == name)
            return (_rowcount(await s.execute(q))) > 0


def _file_rows(
    application_id: str, name: str, files: list[tuple[str, str]]
) -> list[m.SkillFileRecord]:
    from evokai.agents.skills import title_of

    return [
        m.SkillFileRecord(
            id=new_id(),
            application_id=application_id,
            skill_name=name,
            path=path,
            content=content,
            size=len(content),
            title=title_of(path, content),
        )
        for path, content in files
    ]


class CredentialRepository(_Repo):
    """End-user credentials for forward_user MCP servers, encrypted, for a token's lifetime."""

    async def put(
        self, token_id: str, application_id: str, encrypted: str, expires_at: datetime
    ) -> None:
        """Store a credential."""
        async with self.db.session() as s:
            s.add(
                m.SessionCredential(
                    token_id=token_id,
                    application_id=application_id,
                    encrypted=encrypted,
                    expires_at=expires_at,
                )
            )

    async def get(self, token_id: str, application_id: str) -> str | None:
        """A live credential.

        Returns:
            The encrypted value, or None if missing or expired.
        """
        async with self.db.session() as s:
            c = m.SessionCredential
            q = sa.select(c.encrypted).where(
                c.token_id == token_id, c.application_id == application_id, c.expires_at > utcnow()
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def purge(self) -> int:
        """Delete expired credentials.

        Returns:
            How many were deleted.
        """
        async with self.db.session() as s:
            q = sa.delete(m.SessionCredential).where(m.SessionCredential.expires_at <= utcnow())
            return _rowcount(await s.execute(q))


class SessionRepository(_Repo):
    """Conversations and their threads."""

    async def get(self, w: Who, session_id: str) -> m.Session | None:
        """A conversation of this owner.

        Returns:
            The session or None.
        """
        async with self.db.session() as s:
            q = sa.select(m.Session).where(
                m.Session.id == session_id,
                m.Session.application_id == w.application_id,
                m.Session.subject == w.subject,
            )
            sess = (await s.execute(q)).scalar_one_or_none()
            if sess is None:
                return None
            cutoff, app_mode = await _cutoff(s, w.application_id)
            expired = (
                cutoff is not None
                and (sess.storage_mode or app_mode) == "evokai"
                and _aware(sess.updated_at) <= cutoff
            )
            return None if expired else sess

    async def exists(self, session_id: str) -> bool:
        """Whether a conversation row exists at all (expired ones included).

        Returns:
            True if the id is taken.
        """
        async with self.db.session() as s:
            q = sa.select(m.Session.id).where(m.Session.id == session_id)
            return (await s.execute(q)).scalar_one_or_none() is not None

    async def purge_expired(self, now: datetime | None = None) -> int:
        """Delete conversations past their application's time to live, for good.

        The time to live counts from the last message, and for a system application from the
        conversation's start.

        Messages, summaries, events, turns, pending actions, threads and the conversation go; usage
        records stay (budgets keep counting) without their link to the conversation or turn; bug
        reports lose the link too.

        Returns:
            How many conversations were deleted.
        """
        now = now or utcnow()
        deleted = 0
        async with self.db.session() as s:
            apps = (
                await s.execute(
                    sa.select(
                        m.Application.id,
                        m.Application.conversation_ttl_days,
                        m.Application.storage_mode,
                        m.Application.system,
                    ).where(m.Application.conversation_ttl_days.is_not(None))
                )
            ).all()
        for app_id, ttl, app_mode, system in apps:
            cutoff = now - timedelta(days=int(ttl or 0))
            # A system application's conversations (the console assistant's) go that many days
            # after they started, however long they are resumed: what was said is kept no longer.
            since = m.Session.created_at if system else m.Session.updated_at
            while True:
                async with self.db.session() as s:
                    ids = list(
                        (
                            await s.execute(
                                sa
                                .select(m.Session.id)
                                .where(
                                    m.Session.application_id == app_id,
                                    since <= cutoff,
                                    _stored_here(str(app_mode or "evokai")),
                                )
                                .limit(500)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not ids:
                        break
                    await _forget_sessions(s, ids)
                    deleted += len(ids)
        return deleted

    async def create(
        self,
        w: Who,
        agent_name: str,
        agent_version: str,
        title: str,
        tags: list[str],
        session_id: str | None = None,
        consent: dict[str, Any] | None = None,
        exit_code_hash: str | None = None,
        voice_engine: str = "browser",
        storage_mode: str | None = None,
        prompt_options: dict[str, Any] | None = None,
        client_tools: list[dict[str, Any]] | None = None,
    ) -> tuple[m.Session, m.Thread]:
        """Create a conversation and its thread.

        Returns:
            The session and its thread.
        """
        async with self.db.session() as s:
            sess = m.Session(
                id=session_id or new_id(),
                application_id=w.application_id,
                subject=w.subject,
                tags=tags,
                agent_name=agent_name,
                agent_version=agent_version,
                title=(title or DEFAULT_TITLE)[:120],
                consent=consent,
                exit_code_hash=exit_code_hash,
                voice_engine=voice_engine,
                storage_mode=storage_mode,
                prompt_options=prompt_options,
                client_tools=client_tools,
            )
            thread = m.Thread(id=new_id(), session_id=sess.id)
            s.add_all([sess, thread])
            return sess, thread

    async def set_prompt_options(self, session_id: str, options: dict[str, Any]) -> None:
        """Replace the prompt modules and locale chosen for the session."""
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.Session)
                .where(m.Session.id == session_id)
                .values(prompt_options=options)
            )

    async def set_client_tools(self, session_id: str, tools: list[dict[str, Any]] | None) -> None:
        """Replace the custom client tools the session token declared."""
        async with self.db.session() as s:
            await s.execute(
                sa.update(m.Session).where(m.Session.id == session_id).values(client_tools=tools)
            )

    async def set_voice_engine(self, session_id: str, engine: str) -> None:
        """Record who does speech for the session from now on."""
        async with self.db.session() as s:
            await s.execute(
                sa.update(m.Session).where(m.Session.id == session_id).values(voice_engine=engine)
            )

    async def set_agent(self, session_id: str, name: str, version: str) -> None:
        """Hand the conversation to another agent (a handoff): it answers from the next turn."""
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.Session)
                .where(m.Session.id == session_id)
                .values(agent_name=name, agent_version=version)
            )

    async def thread(self, session_id: str) -> m.Thread:
        """The thread of a session.

        Returns:
            The thread.
        """
        async with self.db.session() as s:
            q = sa.select(m.Thread).where(m.Thread.session_id == session_id)
            return (await s.execute(q)).scalar_one()

    async def recent(self, w: Who, limit: int = 30) -> list[dict[str, Any]]:
        """Recent conversations of a subject.

        Returns:
            id, title, agent and updatedAt of each.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.Session.id, m.Session.title, m.Session.agent_name, m.Session.updated_at)
                .where(m.Session.application_id == w.application_id, m.Session.subject == w.subject)
                .order_by(m.Session.updated_at.desc())
                .limit(limit)
            )
            q = q.where(_alive(*await _cutoff(s, w.application_id)))
            rows = (await s.execute(q)).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "agent": r.agent_name,
                "updatedAt": r.updated_at.isoformat(),
            }
            for r in rows
        ]

    async def across(
        self, application_id: str, subject: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Recent conversations of an application, of every subject or one (admin view).

        Returns:
            id, subject, title, agent, status, voiceEngine and updatedAt of each.
        """
        async with self.db.session() as s:
            q = sa.select(m.Session).where(m.Session.application_id == application_id)
            if subject:
                q = q.where(m.Session.subject == subject)
            q = q.where(_alive(*await _cutoff(s, application_id)))
            q = q.order_by(m.Session.updated_at.desc()).limit(limit)
            rows = (await s.execute(q)).scalars().all()
        return [
            {
                "id": r.id,
                "subject": r.subject,
                "title": r.title,
                "agent": r.agent_name,
                "agentVersion": r.agent_version,
                "status": r.status,
                "voiceEngine": r.voice_engine,
                "storageMode": r.storage_mode,
                "updatedAt": r.updated_at.isoformat(),
            }
            for r in rows
        ]

    async def review(
        self,
        application_id: str,
        *,
        subject: str | None = None,
        agent: str | None = None,
        feedback: str | None = None,
        noted: bool | None = None,
        before: datetime | None = None,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Conversations stored in Evokai, newest first, with their counts (console review).

        Args:
            application_id: The application.
            subject: Only this subject's.
            agent: Only this agent's.
            feedback: ``up`` or ``down``: only conversations with such a thumbs; ``any`` with one.
            noted: Only conversations with a reviewer's note.
            before: Last activity before this time (the next page).
            limit: At most this many.
            conversation_id: Only this conversation.

        Returns:
            id, subject, title, agent, agentVersion, status, voiceEngine, storageMode, updatedAt,
            turns, thumbsUp, thumbsDown, notes.
        """
        sess, msg = m.Session, m.Message
        async with self.db.session() as s:
            cutoff, app_mode = await _cutoff(s, application_id)
            q = sa.select(sess).where(
                sess.application_id == application_id,
                _stored_here(app_mode),
                _alive(cutoff, app_mode),
            )
            if subject:
                q = q.where(sess.subject == subject)
            if conversation_id:
                q = q.where(sess.id == conversation_id)
            if agent:
                q = q.where(sess.agent_name == agent)
            if before is not None:
                q = q.where(sess.updated_at < before)
            thumbs = {"up": msg.feedback > 0, "down": msg.feedback < 0}.get(
                feedback or "", msg.feedback.is_not(None) if feedback == "any" else None
            )
            if thumbs is not None:
                q = q.where(
                    sess.id.in_(
                        sa.select(msg.session_id).where(
                            msg.application_id == application_id, thumbs
                        )
                    )
                )
            if noted:
                q = q.where(
                    sess.id.in_(
                        sa.select(m.ReviewNote.session_id).where(
                            m.ReviewNote.application_id == application_id
                        )
                    )
                )
            rows = (
                (await s.execute(q.order_by(sess.updated_at.desc()).limit(limit))).scalars().all()
            )
            ids = [r.id for r in rows]
            counts: dict[str, dict[str, int]] = {i: {} for i in ids}
            if ids:
                for sid, up, down in (
                    await s.execute(
                        sa
                        .select(
                            msg.session_id,
                            sa.func.sum(sa.case((msg.feedback > 0, 1), else_=0)),
                            sa.func.sum(sa.case((msg.feedback < 0, 1), else_=0)),
                        )
                        .where(msg.session_id.in_(ids))
                        .group_by(msg.session_id)
                    )
                ).all():
                    counts[sid].update(thumbsUp=int(up or 0), thumbsDown=int(down or 0))
                for sid, n in (
                    await s.execute(
                        sa
                        .select(m.Turn.session_id, sa.func.count())
                        .where(m.Turn.session_id.in_(ids))
                        .group_by(m.Turn.session_id)
                    )
                ).all():
                    counts[sid]["turns"] = int(n)
                for sid, n in (
                    await s.execute(
                        sa
                        .select(m.ReviewNote.session_id, sa.func.count())
                        .where(m.ReviewNote.session_id.in_(ids))
                        .group_by(m.ReviewNote.session_id)
                    )
                ).all():
                    counts[sid]["notes"] = int(n)
        return [
            {
                "id": r.id,
                "subject": r.subject,
                "title": r.title,
                "agent": r.agent_name,
                "agentVersion": r.agent_version,
                "status": r.status,
                "voiceEngine": r.voice_engine,
                "storageMode": r.storage_mode,
                "updatedAt": r.updated_at.isoformat(),
                "turns": counts[r.id].get("turns", 0),
                "thumbsUp": counts[r.id].get("thumbsUp", 0),
                "thumbsDown": counts[r.id].get("thumbsDown", 0),
                "notes": counts[r.id].get("notes", 0),
            }
            for r in rows
        ]

    async def set_title(self, session_id: str, title: str) -> None:
        """Rename a conversation (its first message)."""
        async with self.db.session() as s:
            q = sa.update(m.Session).where(
                m.Session.id == session_id, m.Session.title == DEFAULT_TITLE
            )
            await s.execute(q.values(title=title[:120]))

    async def delete(self, w: Who, session_id: str) -> bool:
        """Delete a conversation and everything in it.

        Returns:
            Whether it existed for this owner.
        """
        async with self.db.session() as s:
            q = sa.select(m.Session.id).where(
                m.Session.id == session_id,
                m.Session.application_id == w.application_id,
                m.Session.subject == w.subject,
            )
            if not (await s.execute(q)).scalar_one_or_none():
                return False
            for model in (
                m.Event,
                m.PendingAction,
                m.Message,
                m.ConversationSummary,
                m.CapabilityRecord,
                m.Turn,
                m.Thread,
                m.Trace,
                m.ReviewNote,
            ):
                await s.execute(sa.delete(model).where(model.session_id == session_id))
            await s.execute(sa.delete(m.Session).where(m.Session.id == session_id))
            return True

    async def record_exit_attempt(self, session_id: str, *, close: bool) -> int:
        """Count an exit attempt, closing the session when the code was right.

        Returns:
            Attempts so far.
        """
        async with self.db.session() as s:
            sess = await s.get(m.Session, session_id)
            if sess is None:
                return 0
            sess.exit_attempts = (sess.exit_attempts or 0) + 1
            if close:
                sess.status = "closed"
            return sess.exit_attempts

    async def set_last_seq(self, session_id: str, seq: int) -> None:
        """Record the last event number sent."""
        async with self.db.session() as s:
            q = sa.update(m.Session).where(m.Session.id == session_id)
            await s.execute(q.values(last_seq=seq, updated_at=utcnow()))


class MessageRepository(_Repo):
    """Messages of a thread."""

    async def add(
        self,
        w: Who,
        session_id: str,
        thread_id: str,
        *,
        role: str,
        content: list[Any],
        text: str = "",
        events: list[Any] | None = None,
        turn_id: str | None = None,
        interrupted: bool = False,
    ) -> str:
        """Append a message.

        Returns:
            The message id.
        """
        async with self.db.session() as s:
            q = sa.select(sa.func.coalesce(sa.func.max(m.Message.position), 0)).where(
                m.Message.thread_id == thread_id
            )
            pos = (await s.execute(q)).scalar_one()
            msg = m.Message(
                id=new_id(),
                thread_id=thread_id,
                session_id=session_id,
                application_id=w.application_id,
                subject=w.subject,
                turn_id=turn_id,
                position=pos + 1,
                role=role,
                content=content,
                text=text,
                events=events or None,
                interrupted=interrupted,
            )
            s.add(msg)
            q2 = sa.update(m.Session).where(m.Session.id == session_id).values(updated_at=utcnow())
            await s.execute(q2)
            return msg.id

    async def recent(self, w: Who, thread_id: str, limit: int = 200) -> list[m.Message]:
        """The last messages of a thread, oldest first.

        Returns:
            Messages.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.Message)
                .where(
                    m.Message.thread_id == thread_id,
                    m.Message.application_id == w.application_id,
                    m.Message.subject == w.subject,
                )
                .order_by(m.Message.position.desc())
                .limit(limit)
            )
            rows = (await s.execute(q)).scalars().all()
        return list(reversed(rows))

    async def since(
        self, w: Who, thread_id: str, after_position: int, limit: int = 400
    ) -> list[m.Message]:
        """The messages after a position (the latest ``limit`` of them), oldest first.

        Returns:
            Messages.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.Message)
                .where(
                    m.Message.thread_id == thread_id,
                    m.Message.application_id == w.application_id,
                    m.Message.subject == w.subject,
                    m.Message.position > after_position,
                )
                .order_by(m.Message.position.desc())
                .limit(limit)
            )
            rows = (await s.execute(q)).scalars().all()
        return list(reversed(rows))

    async def set_feedback(self, w: Who, message_id: str, value: int | None) -> bool:
        """Thumbs up/down on a message.

        Returns:
            Whether the message exists for this owner.
        """
        async with self.db.session() as s:
            q = sa.update(m.Message).where(
                m.Message.id == message_id,
                m.Message.application_id == w.application_id,
                m.Message.subject == w.subject,
            )
            return (_rowcount(await s.execute(q.values(feedback=value)))) > 0


class SummaryRepository(_Repo):
    """The summaries that stand in for conversations' older messages (context compaction)."""

    async def get(self, w: Who, session_id: str) -> m.ConversationSummary | None:
        """A conversation's summary.

        Returns:
            The row, or None.
        """
        async with self.db.session() as s:
            q = sa.select(m.ConversationSummary).where(
                m.ConversationSummary.session_id == session_id,
                m.ConversationSummary.application_id == w.application_id,
                m.ConversationSummary.subject == w.subject,
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def save(
        self,
        w: Who,
        session_id: str,
        thread_id: str,
        *,
        up_to_position: int,
        up_to_message_id: str,
        summary: str,
        messages: int,
        model: str,
    ) -> bool:
        """Write or move forward a conversation's summary.

        A summary never goes back: when another compaction already covers this position (another
        instance, a background one), nothing changes.

        Args:
            w: The owner.
            session_id: The conversation.
            thread_id: Its thread.
            up_to_position: The position of the last message the summary covers.
            up_to_message_id: That message's id.
            summary: The text.
            messages: How many messages this compaction folded in (added to the total).
            model: The model that wrote it.

        Returns:
            Whether it was written.
        """
        now = utcnow()
        async with self.db.session() as s:
            q = sa.select(m.ConversationSummary).where(
                m.ConversationSummary.session_id == session_id
            )
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                s.add(
                    m.ConversationSummary(
                        session_id=session_id,
                        thread_id=thread_id,
                        application_id=w.application_id,
                        subject=w.subject,
                        up_to_position=up_to_position,
                        up_to_message_id=up_to_message_id,
                        summary=summary,
                        messages=messages,
                        compactions=1,
                        model=model[:200],
                        created_at=now,
                        updated_at=now,
                    )
                )
                return True
            if row.application_id != w.application_id or row.subject != w.subject:
                return False
            if row.up_to_position >= up_to_position:
                return False
            row.up_to_position = up_to_position
            row.up_to_message_id = up_to_message_id
            row.summary = summary
            row.messages = (row.messages or 0) + messages
            row.compactions = (row.compactions or 0) + 1
            row.model = model[:200]
            row.updated_at = now
            return True


class TurnRepository(_Repo):
    """Turns and their timings."""

    async def start(self, w: Who, session_id: str, mode: str, turn_id: str) -> None:
        """Record a turn start."""
        async with self.db.session() as s:
            s.add(
                m.Turn(
                    id=turn_id,
                    session_id=session_id,
                    application_id=w.application_id,
                    subject=w.subject,
                    mode=mode,
                )
            )

    async def count(self, session_id: str) -> int:
        """How many turns a conversation has had.

        Returns:
            The count.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(sa.func.count())
                .select_from(m.Turn)
                .where(m.Turn.session_id == session_id)
            )
            return int((await s.execute(q)).scalar_one())

    async def finish(self, turn_id: str, status: str, reason: str | None, timings: dict) -> None:
        """Record a turn's outcome."""
        async with self.db.session() as s:
            q = sa.update(m.Turn).where(m.Turn.id == turn_id)
            await s.execute(
                q.values(status=status, reason=reason, timings=timings, ended_at=utcnow())
            )

    async def stats(
        self,
        application_id: str,
        *,
        start: datetime,
        end: datetime,
        tag: str | None = None,
        agent: str | None = None,
        limit: int = 20_000,
    ) -> dict[str, Any]:
        """Turns of a period.

        How many, how many a fallback model answered, and the median time to the first word.

        Returns:
            turns, fallbackTurns, medianFirstTokenMs (None without data).
        """
        t, sess = m.Turn, m.Session
        q = (
            sa
            .select(t.timings, sess.tags)
            .join(sess, sess.id == t.session_id, isouter=True)
            .where(t.application_id == application_id, t.started_at >= start, t.started_at < end)
            .order_by(t.started_at.desc())
            .limit(limit)
        )
        if agent:
            q = q.where(sess.agent_name == agent)
        async with self.db.session() as s:
            rows = (await s.execute(q)).all()
        if tag:
            rows = [r for r in rows if tag in (r[1] or [])]
        firsts = sorted(
            int(r[0]["firstTokenMs"])
            for r in rows
            if isinstance((r[0] or {}).get("firstTokenMs"), int)
        )
        median = firsts[len(firsts) // 2] if firsts else None
        return {
            "turns": len(rows),
            "fallbackTurns": sum(1 for r in rows if (r[0] or {}).get("fallback")),
            "medianFirstTokenMs": median,
        }

    async def add_timing(self, turn_id: str, key: str, value: Any) -> None:
        """Add a timing reported later (e.g. first audio played by the client)."""
        async with self.db.session() as s:
            t = await s.get(m.Turn, turn_id)
            if t:
                t.timings = {**(t.timings or {}), key: value}


class EventRepository(_Repo):
    """Persisted events, for replay."""

    async def append(self, session_id: str, rows: list[dict[str, Any]]) -> None:
        """Store events."""
        if not rows:
            return
        async with self.db.session() as s:
            s.add_all([
                m.Event(
                    session_id=session_id,
                    turn_id=r.get("turnId"),
                    seq=r["seq"],
                    type=r["type"],
                    payload=r,
                )
                for r in rows
            ])

    async def after(self, session_id: str, seq: int, limit: int = 2000) -> list[dict[str, Any]]:
        """Events after a sequence number.

        Returns:
            Event payloads in order.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.Event.payload)
                .where(m.Event.session_id == session_id, m.Event.seq > seq)
                .order_by(m.Event.seq)
                .limit(limit)
            )
            return list((await s.execute(q)).scalars().all())

    async def max_seq(self, session_id: str) -> int:
        """The last event number of a session.

        Returns:
            0 when there are none.
        """
        async with self.db.session() as s:
            q = sa.select(sa.func.coalesce(sa.func.max(m.Event.seq), 0)).where(
                m.Event.session_id == session_id
            )
            return int((await s.execute(q)).scalar_one())


class PendingRepository(_Repo):
    """Confirmations waiting for the user."""

    async def open(self, w: Who, session_id: str) -> m.PendingAction | None:
        """The open confirmation of a conversation, if any.

        Returns:
            The pending action or None.
        """
        async with self.db.session() as s:
            p = m.PendingAction
            q = (
                sa
                .select(p)
                .where(
                    p.session_id == session_id,
                    p.application_id == w.application_id,
                    p.subject == w.subject,
                    p.status == "open",
                    sa.or_(p.expires_at.is_(None), p.expires_at > utcnow()),
                )
                .order_by(p.created_at.desc())
                .limit(1)
            )
            return (await s.execute(q)).scalar_one_or_none()

    async def create(
        self, w: Who, session_id: str, state: dict[str, Any], ttl_seconds: int | None = None
    ) -> str:
        """Open a confirmation (short-lived when the application keeps conversations itself).

        Returns:
            Its id (the action id the client confirms).
        """
        async with self.db.session() as s:
            p = m.PendingAction(
                id=new_id(),
                session_id=session_id,
                application_id=w.application_id,
                subject=w.subject,
                state=state,
                expires_at=utcnow() + timedelta(seconds=ttl_seconds) if ttl_seconds else None,
            )
            s.add(p)
            return p.id

    async def purge(self) -> int:
        """Delete expired or closed short-lived confirmations.

        Returns:
            How many were deleted.
        """
        async with self.db.session() as s:
            p = m.PendingAction
            q = sa.delete(p).where(
                p.expires_at.is_not(None), sa.or_(p.expires_at <= utcnow(), p.status != "open")
            )
            return _rowcount(await s.execute(q))

    async def close(self, w: Who, pending_id: str, status: str) -> None:
        """Close a confirmation (approved, rejected, superseded)."""
        async with self.db.session() as s:
            p = m.PendingAction
            q = sa.update(p).where(
                p.id == pending_id, p.application_id == w.application_id, p.subject == w.subject
            )
            await s.execute(q.values(status=status))


class UsageRepository(_Repo):
    """Usage records: what was spent, by whom, on what."""

    async def record(self, w: Who, tags: list[str], **fields: Any) -> bool:
        """Record a cost.

        With ``dedup_key`` in the fields, a record with the same key already written means this
        call was recorded before (a durable step's replay): nothing is written.

        Returns:
            Whether a record was written.
        """
        key = fields.get("dedup_key")
        async with self.db.session() as s:
            if key is not None:
                q = sa.select(m.UsageRecord.id).where(m.UsageRecord.dedup_key == key)
                if (await s.execute(q)).scalar_one_or_none() is not None:
                    return False
            s.add(
                m.UsageRecord(
                    id=new_id(),
                    application_id=w.application_id,
                    subject=w.subject,
                    tags=tags_key(tags),
                    **fields,
                )
            )
            return True

    async def spend(self, w: Who, now: datetime) -> dict[str, float]:
        """Spend in USD this UTC month and today, for the application and for the subject.

        Returns:
            appMonthUsd, appDayUsd, subjectMonthUsd, subjectDayUsd.
        """
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        u = m.UsageRecord
        mine = u.subject == w.subject
        today = u.created_at >= day
        zero = sa.literal(0)
        cols = (
            sa.func.coalesce(sa.func.sum(u.cost_micro_usd), 0),
            sa.func.coalesce(sa.func.sum(sa.case((today, u.cost_micro_usd), else_=zero)), 0),
            sa.func.coalesce(sa.func.sum(sa.case((mine, u.cost_micro_usd), else_=zero)), 0),
            sa.func.coalesce(
                sa.func.sum(sa.case((sa.and_(mine, today), u.cost_micro_usd), else_=zero)), 0
            ),
        )
        async with self.db.session() as s:
            # Evals are capped by the application's eval budget, never by the caps of its users.
            q = sa.select(*cols).where(
                u.application_id == w.application_id, u.created_at >= month, u.kind != "eval"
            )
            row = (await s.execute(q)).one()
        keys = ("appMonthUsd", "appDayUsd", "subjectMonthUsd", "subjectDayUsd")
        return {k: int(v) / 1e6 for k, v in zip(keys, row, strict=True)}

    async def window_spend(
        self, w: Who, windows: dict[str, tuple[datetime, bool]]
    ) -> dict[str, float]:
        """Spend in USD since each window's start, for the application or for the subject.

        Args:
            w: The application and the subject.
            windows: Name to (start, subject only). Evals are left out: they have their own
                budget.

        Returns:
            Name to USD.
        """
        if not windows:
            return {}
        u = m.UsageRecord
        mine = u.subject == w.subject
        zero = sa.literal(0)
        names = list(windows)
        cols = []
        for name in names:
            start, own = windows[name]
            cond = sa.and_(mine, u.created_at >= start) if own else u.created_at >= start
            cols.append(
                sa.func.coalesce(sa.func.sum(sa.case((cond, u.cost_micro_usd), else_=zero)), 0)
            )
        first = min(start for start, _ in windows.values())
        async with self.db.session() as s:
            q = sa.select(*cols).where(
                u.application_id == w.application_id, u.created_at >= first, u.kind != "eval"
            )
            row = (await s.execute(q)).one()
        return {k: int(v) / 1e6 for k, v in zip(names, row, strict=True)}

    async def top_subject_spend(
        self, application_id: str, start: datetime, exclude: tuple[str, ...] = ()
    ) -> float:
        """The most any one subject spent since ``start``, in USD (evals and prefixes left out).

        Returns:
            USD, 0 when nobody spent anything.
        """
        u = m.UsageRecord
        total = sa.func.sum(u.cost_micro_usd)
        q = sa.select(total).where(
            u.application_id == application_id, u.created_at >= start, u.kind != "eval"
        )
        for prefix in exclude:
            q = q.where(sa.not_(u.subject.startswith(prefix)))
        q = q.group_by(u.subject).order_by(total.desc()).limit(1)
        async with self.db.session() as s:
            value = (await s.execute(q)).scalar()
        return int(value or 0) / 1e6

    async def eval_spend(self, application_id: str, now: datetime) -> float:
        """What the application's evals spent this UTC month, in USD.

        Returns:
            The spend.
        """
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        u = m.UsageRecord
        async with self.db.session() as s:
            q = sa.select(sa.func.coalesce(sa.func.sum(u.cost_micro_usd), 0)).where(
                u.application_id == application_id, u.created_at >= month, u.kind == "eval"
            )
            return int((await s.execute(q)).scalar_one()) / 1e6

    async def report(
        self,
        application_id: str,
        *,
        start: datetime,
        end: datetime,
        group_by: list[str],
        subject: str | None = None,
        tag: str | None = None,
        agent: str | None = None,
    ) -> list[dict[str, Any]]:
        """Usage grouped by subject, agent, kind, model and/or day (tags are filters).

        Returns:
            One row per group: the group keys, costUsd, inputTokens (cached ones included),
            outputTokens, cacheReadTokens, cacheWriteTokens, characters, audioMs, textUnits (the
            injection classifier's billed text units, usage kind ``guard``), calls.
        """
        u = m.UsageRecord
        guard = u.kind == "guard"
        zero = sa.literal(0)
        keys: dict[str, Any] = {
            "subject": u.subject,
            "agent": u.agent_name,
            "kind": u.kind,
            "model": u.model,
            "day": sa.func.substring(sa.cast(u.created_at, sa.String(40)), 1, 10),
            "tags": u.tags,
        }
        groups = [(g, keys[g]) for g in group_by if g in keys]
        q = sa.select(
            *[col.label(g) for g, col in groups],
            sa.func.coalesce(sa.func.sum(u.cost_micro_usd), 0).label("cost"),
            # A guard record's input_tokens are text units, not tokens.
            sa.func.coalesce(sa.func.sum(sa.case((guard, zero), else_=u.input_tokens)), 0).label(
                "input_tokens"
            ),
            sa.func.coalesce(sa.func.sum(sa.case((guard, u.input_tokens), else_=zero)), 0).label(
                "text_units"
            ),
            sa.func.coalesce(sa.func.sum(u.output_tokens), 0).label("output_tokens"),
            sa.func.coalesce(sa.func.sum(u.cache_read_tokens), 0).label("cache_read_tokens"),
            sa.func.coalesce(sa.func.sum(u.cache_write_tokens), 0).label("cache_write_tokens"),
            sa.func.coalesce(sa.func.sum(u.characters), 0).label("characters"),
            sa.func.coalesce(sa.func.sum(u.audio_ms), 0).label("audio_ms"),
            sa.func.count().label("calls"),
        ).where(u.application_id == application_id, u.created_at >= start, u.created_at < end)
        if subject:
            q = q.where(u.subject == subject)
        if agent:
            q = q.where(u.agent_name == agent)
        if tag:
            q = q.where(u.tags.like(f"%,{tag.replace(',', ' ')},%"))
        if groups:
            q = q.group_by(*[col for _, col in groups]).order_by(*[col for _, col in groups])
        async with self.db.session() as s:
            rows = (await s.execute(q)).mappings().all()
        return [
            {
                **{g: _tags(r[g]) if g == "tags" else r[g] for g, _ in groups},
                "costUsd": int(r["cost"]) / 1e6,
                "inputTokens": int(r["input_tokens"]),
                "outputTokens": int(r["output_tokens"]),
                "cacheReadTokens": int(r["cache_read_tokens"]),
                "cacheWriteTokens": int(r["cache_write_tokens"]),
                "characters": int(r["characters"]),
                "audioMs": int(r["audio_ms"]),
                "textUnits": int(r["text_units"]),
                "calls": int(r["calls"]),
            }
            for r in rows
        ]

    async def subjects_and_tags(
        self,
        application_id: str,
        *,
        start: datetime,
        end: datetime,
        tag: str | None = None,
        agent: str | None = None,
    ) -> tuple[int, list[str]]:
        """How many subjects spent in a period, and every tag seen in it.

        Returns:
            (subjects, sorted tags).
        """
        u = m.UsageRecord
        where = [u.application_id == application_id, u.created_at >= start, u.created_at < end]
        if agent:
            where.append(u.agent_name == agent)
        tagged = [*where, u.tags.like(f"%,{tag.replace(',', ' ')},%")] if tag else where
        async with self.db.session() as s:
            subjects = (
                await s.execute(sa.select(sa.func.count(sa.distinct(u.subject))).where(*tagged))
            ).scalar_one()
            keys = (await s.execute(sa.select(u.tags).where(*where).distinct())).scalars().all()
        return int(subjects or 0), sorted({t for k in keys for t in _tags(k)})


class PromptRepository(_Repo):
    """Versioned prompts of an application."""

    async def put(
        self,
        application_id: str,
        name: str,
        text: str,
        *,
        parameters: dict[str, Any] | None = None,
        description: str = "",
        data: dict[str, Any] | None = None,
        template_syntax: str = "handlebars",
    ) -> int:
        """Store a new version of a prompt (no-op when nothing changed).

        Args:
            application_id: Owner application.
            name: Prompt name.
            text: The text (a template for a module).
            parameters: A module's parameter schema; None for a plain prompt.
            description: What the prompt is for.
            data: A module's static lookup table.
            template_syntax: The text's syntax (``legacy`` only for versions saved before
                Handlebars).

        Returns:
            The version number.
        """
        async with self.db.session() as s:
            p = m.PromptRecord
            q = (
                sa
                .select(p)
                .where(p.application_id == application_id, p.name == name)
                .order_by(p.version.desc())
                .limit(1)
            )
            last = (await s.execute(q)).scalar_one_or_none()
            if (
                last
                and last.text == text
                and last.parameters == parameters
                and (last.description or "") == description
                and last.data == data
                and last.template_syntax == template_syntax
            ):
                return last.version
            version = (last.version if last else 0) + 1
            s.add(
                p(
                    id=new_id(),
                    application_id=application_id,
                    name=name,
                    version=version,
                    text=text,
                    parameters=parameters,
                    description=description,
                    data=data,
                    template_syntax=template_syntax,
                )
            )
            return version

    async def get(
        self, application_id: str, name: str, version: int | None = None
    ) -> m.PromptRecord | None:
        """A prompt version (the latest when version is None).

        Returns:
            The record or None.
        """
        async with self.db.session() as s:
            p = m.PromptRecord
            q = sa.select(p).where(p.application_id == application_id, p.name == name)
            q = q.where(p.version == version) if version else q.order_by(p.version.desc()).limit(1)
            return (await s.execute(q)).scalar_one_or_none()

    async def all(self, application_id: str) -> list[m.PromptRecord]:
        """Every version of every prompt.

        Returns:
            Records by name and version.
        """
        async with self.db.session() as s:
            p = m.PromptRecord
            q = sa.select(p).where(p.application_id == application_id).order_by(p.name, p.version)
            return list((await s.execute(q)).scalars().all())

    async def delete(self, application_id: str, name: str) -> int:
        """Delete a prompt with every version (a system application's reset).

        Returns:
            How many versions were deleted.
        """
        async with self.db.session() as s:
            p = m.PromptRecord
            q = sa.delete(p).where(p.application_id == application_id, p.name == name)
            return _rowcount(await s.execute(q))


class VoiceRepository(_Repo):
    """The deployment's text-to-speech voice catalog."""

    async def put(self, provider: str, voice_id: str, **fields: Any) -> None:
        """Create or replace a voice."""
        async with self.db.session() as s:
            v = m.VoiceRecord
            await s.execute(sa.delete(v).where(v.provider == provider, v.voice_id == voice_id))
            s.add(v(id=new_id(), provider=provider, voice_id=voice_id, **fields))

    async def all(self, language: str | None = None) -> list[m.VoiceRecord]:
        """Voices, optionally for one language.

        Returns:
            Records by provider and name.
        """
        async with self.db.session() as s:
            v = m.VoiceRecord
            q = sa.select(v).order_by(v.provider, v.name)
            if language:
                q = q.where(v.language == language)
            return list((await s.execute(q)).scalars().all())


class BugReportRepository(_Repo):
    """Problems reported from sessions."""

    async def add(
        self, w: Who, session_id: str | None, description: str, context: dict[str, Any]
    ) -> str:
        """Store a report.

        Returns:
            Its id.
        """
        async with self.db.session() as s:
            r = m.BugReport(
                id=new_id(),
                application_id=w.application_id,
                subject=w.subject,
                session_id=session_id,
                description=description,
                context=context,
            )
            s.add(r)
            return r.id

    async def all(self, application_id: str, limit: int = 100) -> list[m.BugReport]:
        """Recent reports of an application.

        Returns:
            Records, newest first.
        """
        async with self.db.session() as s:
            r = m.BugReport
            q = (
                sa
                .select(r)
                .where(r.application_id == application_id)
                .order_by(r.created_at.desc())
                .limit(limit)
            )
            return list((await s.execute(q)).scalars().all())


class GrantRepository(_Repo):
    """Explicit grants and revocations of an application (everything else follows defaults)."""

    async def all(self, application_id: str) -> list[m.Grant]:
        """The application's overrides.

        Returns:
            Rows by kind and name.
        """
        async with self.db.session() as s:
            g = m.Grant
            q = sa.select(g).where(g.application_id == application_id).order_by(g.kind, g.name)
            return list((await s.execute(q)).scalars().all())

    async def change(
        self, application_id: str, changes: list[tuple[str, str, Any]], by: str = ""
    ) -> None:
        """Set overrides, or reset them to the default (value None), in one transaction."""
        async with self.db.session() as s:
            g = m.Grant
            for kind, name, value in changes:
                same = (g.application_id == application_id, g.kind == kind, g.name == name)
                await s.execute(sa.delete(g).where(*same))
                if value is not None:
                    s.add(
                        g(
                            id=new_id(),
                            application_id=application_id,
                            kind=kind,
                            name=name,
                            value=value,
                            updated_at=utcnow(),
                            updated_by=by[:80],
                        )
                    )
                await s.flush()

    async def replace(
        self, application_id: str, overrides: dict[tuple[str, str], Any], by: str = ""
    ) -> bool:
        """Make the overrides exactly these (the config file's ``grants:``).

        Returns:
            Whether anything changed.
        """
        current = {(r.kind, r.name): r.value for r in await self.all(application_id)}
        if current == overrides:
            return False
        changes = [(k, n, None) for (k, n) in current if (k, n) not in overrides]
        changes += [(k, n, v) for (k, n), v in overrides.items() if current.get((k, n)) != v]
        await self.change(application_id, changes, by)
        return True


class SecurityEventRepository(_Repo):
    """Security events of the guard (``evokai.guard``)."""

    async def add_many(self, rows: list[dict[str, Any]]) -> None:
        """Store events."""
        if not rows:
            return
        async with self.db.session() as s:
            s.add_all([m.SecurityEvent(**r) for r in rows])

    @staticmethod
    def _filters(
        application_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        control: str | None = None,
        rule: str | None = None,
        action: str | None = None,
        severity: str | None = None,
        agent: str | None = None,
        subject: str | None = None,
    ) -> list[Any]:
        e = m.SecurityEvent
        where: list[Any] = [e.application_id == application_id]
        if start is not None:
            where.append(e.created_at >= start)
        if end is not None:
            where.append(e.created_at < end)
        if control:
            where.append(
                e.control.like(f"{control}%") if control.endswith(".") else e.control == control
            )
        if rule:
            where.append(e.rule == rule)
        if action:
            where.append(e.action == action)
        if severity:
            where.append(e.severity == severity)
        if agent:
            where.append(e.agent_name == agent)
        if subject:
            where.append(e.subject == subject)
        return where

    async def query(
        self,
        application_id: str,
        *,
        limit: int = 100,
        before: datetime | None = None,
        **filters: Any,
    ) -> list[m.SecurityEvent]:
        """Events of an application, newest first.

        Returns:
            Events.
        """
        e = m.SecurityEvent
        where = self._filters(application_id, **filters)
        if before is not None:
            where.append(e.created_at < before)
        async with self.db.session() as s:
            q = sa.select(e).where(*where).order_by(e.created_at.desc(), e.id).limit(limit)
            return list((await s.execute(q)).scalars().all())

    async def summary(self, application_id: str, **filters: Any) -> dict[str, Any]:
        """Counts of a period by rule, control, action and severity.

        Returns:
            total, byRule (control, rule, count, blocked), byControl, byAction, bySeverity.
        """
        e = m.SecurityEvent
        where = self._filters(application_id, **filters)
        blocked = sa.func.sum(sa.case((e.action != "logged", 1), else_=0))
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    sa
                    .select(e.control, e.rule, e.action, e.severity, sa.func.count(), blocked)
                    .where(*where)
                    .group_by(e.control, e.rule, e.action, e.severity)
                )
            ).all()
        by_rule: dict[tuple[str, str], dict[str, Any]] = {}
        by_control: dict[str, int] = {}
        by_action: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        total = 0
        for control, rule, action, severity, n, acted in rows:
            n, acted = int(n), int(acted or 0)
            total += n
            item = by_rule.setdefault(
                (control, rule), {"control": control, "rule": rule, "count": 0, "acted": 0}
            )
            item["count"] += n
            item["acted"] += acted
            by_control[control] = by_control.get(control, 0) + n
            by_action[action] = by_action.get(action, 0) + n
            by_severity[severity] = by_severity.get(severity, 0) + n
        return {
            "total": total,
            "byRule": sorted(by_rule.values(), key=lambda x: -x["count"]),
            "byControl": by_control,
            "byAction": by_action,
            "bySeverity": by_severity,
        }

    async def purge(self, default_ttl_days: int | None, now: datetime | None = None) -> int:
        """Delete events past their application's time to live (or the deployment default).

        Returns:
            How many were deleted.
        """
        now = now or utcnow()
        e = m.SecurityEvent
        deleted = 0
        async with self.db.session() as s:
            apps = (
                await s.execute(sa.select(m.Application.id, m.Application.security_event_ttl_days))
            ).all()
            for app_id, ttl in apps:
                days = ttl or default_ttl_days
                if not days:
                    continue
                r = await s.execute(
                    sa.delete(e).where(
                        e.application_id == app_id, e.created_at < now - timedelta(days=int(days))
                    )
                )
                deleted += _rowcount(r)
        return deleted

    async def for_subject(self, w: Who) -> list[m.SecurityEvent]:
        """A subject's events (export).

        Returns:
            Events, oldest first.
        """
        e = m.SecurityEvent
        async with self.db.session() as s:
            q = sa.select(e).where(e.application_id == w.application_id, e.subject == w.subject)
            return list((await s.execute(q.order_by(e.created_at))).scalars().all())

    async def forget_subject(self, w: Who) -> int:
        """De-identify a subject's events (right to be forgotten): counts stay, the person goes.

        Returns:
            How many events were changed.
        """
        e = m.SecurityEvent
        async with self.db.session() as s:
            r = await s.execute(
                sa
                .update(e)
                .where(e.application_id == w.application_id, e.subject == w.subject)
                .values(subject=None, session_id=None, turn_id=None, snippet="")
            )
            return _rowcount(r)


class AuditRepository(_Repo):
    """The audit log of changes."""

    async def add(self, **fields: Any) -> None:
        """Write an entry."""
        async with self.db.session() as s:
            s.add(m.AuditEntry(id=new_id(), at=utcnow(), **fields))

    async def query(
        self,
        application_id: str | None,
        *,
        everything: bool = False,
        action: str | None = None,
        actor: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        before: datetime | None = None,
        limit: int = 100,
        target: str | None = None,
    ) -> list[m.AuditEntry]:
        """Entries, newest first: of one application, or every entry (``everything``).

        ``action`` ending in ``.`` is a prefix (``agent.``); ``actor`` and ``target`` match a part
        of it.

        Returns:
            Entries.
        """
        a = m.AuditEntry
        where: list[Any] = []
        if not everything:
            where.append(a.application_id == application_id)
        elif application_id:
            where.append(a.application_id == application_id)
        if action:
            where.append(
                a.action.like(f"{action}%") if action.endswith(".") else a.action == action
            )
        if actor:
            where.append(a.actor.like(f"%{actor}%"))
        if target:
            where.append(a.target.like(f"%{target}%"))
        if start is not None:
            where.append(a.at >= start)
        if end is not None:
            where.append(a.at < end)
        if before is not None:
            where.append(a.at < before)
        async with self.db.session() as s:
            q = sa.select(a).where(*where).order_by(a.at.desc(), a.id).limit(limit)
            return list((await s.execute(q)).scalars().all())

    async def purge(self, default_ttl_days: int | None, now: datetime | None = None) -> int:
        """Delete entries past their application's time to live (deployment entries: the default).

        Returns:
            How many were deleted.
        """
        now = now or utcnow()
        a = m.AuditEntry
        deleted = 0
        async with self.db.session() as s:
            apps = (
                await s.execute(sa.select(m.Application.id, m.Application.audit_ttl_days))
            ).all()
            for app_id, ttl in [*apps, (None, None)]:
                days = ttl or default_ttl_days
                if not days:
                    continue
                owner = a.application_id.is_(None) if app_id is None else a.application_id == app_id
                cutoff = now - timedelta(days=int(days))
                r = await s.execute(sa.delete(a).where(owner, a.at < cutoff))
                deleted += _rowcount(r)
        return deleted


class ConsoleUserRepository(_Repo):
    """People who signed in to the console with single sign-on."""

    async def signed_in(
        self, issuer: str, sub: str, email: str, name: str, roles: dict[str, str]
    ) -> m.ConsoleUser:
        """Record a sign-in (creating the user the first time).

        Returns:
            The user.
        """
        async with self.db.session() as s:
            q = sa.select(m.ConsoleUser).where(
                m.ConsoleUser.issuer == issuer, m.ConsoleUser.sub == sub
            )
            user = (await s.execute(q)).scalar_one_or_none()
            if user is None:
                user = m.ConsoleUser(id=new_id(), issuer=issuer, sub=sub, created_at=utcnow())
                s.add(user)
            user.email, user.name, user.roles = email[:320], name[:200], roles
            user.last_login_at = utcnow()
            await s.flush()
            s.expunge(user)
            return user

    async def get(self, user_id: str) -> m.ConsoleUser | None:
        """A user.

        Returns:
            The user or None.
        """
        async with self.db.session() as s:
            return await s.get(m.ConsoleUser, user_id)

    async def all(self) -> list[m.ConsoleUser]:
        """Every user, most recent sign-in first.

        Returns:
            Users.
        """
        async with self.db.session() as s:
            q = sa.select(m.ConsoleUser).order_by(m.ConsoleUser.last_login_at.desc())
            return list((await s.execute(q)).scalars().all())

    async def set_disabled(self, user_id: str, disabled: bool) -> bool:
        """Disable or enable a user.

        Returns:
            Whether the user exists.
        """
        async with self.db.session() as s:
            r = await s.execute(
                sa
                .update(m.ConsoleUser)
                .where(m.ConsoleUser.id == user_id)
                .values(disabled=disabled)
            )
            return _rowcount(r) > 0


class ConsoleSettingRepository(_Repo):
    """Deployment settings changed from the console."""

    async def get(self, key: str) -> dict[str, Any] | None:
        """A setting.

        Returns:
            Its value, or None when not set.
        """
        async with self.db.session() as s:
            row = await s.get(m.ConsoleSetting, key)
            return dict(row.value) if row else None

    async def put(self, key: str, value: dict[str, Any], by: str) -> None:
        """Set a setting."""
        async with self.db.session() as s:
            row = await s.get(m.ConsoleSetting, key)
            if row is None:
                s.add(
                    m.ConsoleSetting(key=key, value=value, updated_at=utcnow(), updated_by=by[:200])
                )
            else:
                row.value, row.updated_at, row.updated_by = value, utcnow(), by[:200]

    async def delete(self, key: str) -> bool:
        """Remove a setting.

        Returns:
            Whether it was set.
        """
        async with self.db.session() as s:
            r = await s.execute(sa.delete(m.ConsoleSetting).where(m.ConsoleSetting.key == key))
            return _rowcount(r) > 0


class ConsolePreferenceRepository(_Repo):
    """The console preferences of each console identity."""

    async def get(self, identity: str) -> m.ConsolePreferences | None:
        """The stored preferences of an identity.

        Returns:
            The row, or None when the identity never saved any.
        """
        async with self.db.session() as s:
            return await s.get(m.ConsolePreferences, identity)

    async def put(self, identity: str, value: dict[str, Any]) -> m.ConsolePreferences:
        """Store the preferences of an identity (replacing what it had).

        Returns:
            The row.
        """
        async with self.db.session() as s:
            row = await s.get(m.ConsolePreferences, identity)
            if row is None:
                row = m.ConsolePreferences(identity=identity, value=value, updated_at=utcnow())
                s.add(row)
            else:
                row.value, row.updated_at = value, utcnow()
            return row

    async def delete(self, identity: str) -> bool:
        """Forget the preferences of an identity.

        Returns:
            Whether it had any.
        """
        async with self.db.session() as s:
            r = await s.execute(
                sa.delete(m.ConsolePreferences).where(m.ConsolePreferences.identity == identity)
            )
            return _rowcount(r) > 0


class EvalRepository(_Repo):
    """Eval cases, runs and results of an application's agents."""

    async def cases(self, application_id: str, agent: str) -> list[m.EvalCase]:
        """The cases of an agent, by name.

        Returns:
            Cases.
        """
        async with self.db.session() as s:
            c = m.EvalCase
            q = (
                sa
                .select(c)
                .where(c.application_id == application_id, c.agent_name == agent)
                .order_by(c.name)
            )
            return list((await s.execute(q)).scalars().all())

    async def put_case(self, application_id: str, agent: str, name: str, **fields: Any) -> str:
        """Create or replace a case.

        Returns:
            Its id.
        """
        async with self.db.session() as s:
            c = m.EvalCase
            same = (c.application_id == application_id, c.agent_name == agent, c.name == name)
            row = (await s.execute(sa.select(c.id).where(*same))).scalar_one_or_none()
            if row:
                await s.execute(
                    sa.update(c).where(c.id == row).values(updated_at=utcnow(), **fields)
                )
                return row
            case_id = new_id()
            s.add(
                c(id=case_id, application_id=application_id, agent_name=agent, name=name, **fields)
            )
            return case_id

    async def delete_case(self, application_id: str, agent: str, name: str) -> bool:
        """Remove a case (its past results stay with their runs).

        Returns:
            Whether it existed.
        """
        async with self.db.session() as s:
            c = m.EvalCase
            res = await s.execute(
                sa.delete(c).where(
                    c.application_id == application_id, c.agent_name == agent, c.name == name
                )
            )
            return _rowcount(res) > 0

    async def start_run(self, application_id: str, agent: str, **fields: Any) -> m.EvalRun:
        """Record a run that starts now.

        Returns:
            The run.
        """
        run = m.EvalRun(
            id=new_id(), application_id=application_id, agent_name=agent, status="running", **fields
        )
        async with self.db.session() as s:
            s.add(run)
        return run

    async def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        """Close a run with its outcome."""
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(m.EvalRun)
                .where(m.EvalRun.id == run_id)
                .values(status=status, summary=summary, ended_at=utcnow())
            )

    async def running(
        self, application_id: str, agent: str, suite: str = "cases"
    ) -> m.EvalRun | None:
        """The agent's run of a suite in progress, if any.

        Returns:
            The run or None.
        """
        async with self.db.session() as s:
            r = m.EvalRun
            q = sa.select(r).where(
                r.application_id == application_id,
                r.agent_name == agent,
                r.status == "running",
                r.suite == suite,
            )
            return (await s.execute(q)).scalars().first()

    async def interrupt_stale(self, before: datetime) -> int:
        """Mark runs left running by a stopped process as interrupted.

        Returns:
            How many.
        """
        async with self.db.session() as s:
            r = m.EvalRun
            res = await s.execute(
                sa
                .update(r)
                .where(r.status == "running", r.started_at < before)
                .values(status="interrupted", ended_at=utcnow())
            )
            return _rowcount(res)

    async def runs(
        self, application_id: str, agent: str, limit: int = 30, suite: str = "cases"
    ) -> list[m.EvalRun]:
        """An agent's runs of a suite, newest first.

        Returns:
            Runs.
        """
        async with self.db.session() as s:
            r = m.EvalRun
            q = (
                sa
                .select(r)
                .where(r.application_id == application_id, r.agent_name == agent, r.suite == suite)
                .order_by(r.started_at.desc())
                .limit(limit)
            )
            return list((await s.execute(q)).scalars().all())

    async def run(self, application_id: str, run_id: str) -> m.EvalRun | None:
        """One run of the application.

        Returns:
            The run or None.
        """
        async with self.db.session() as s:
            r = m.EvalRun
            q = sa.select(r).where(r.id == run_id, r.application_id == application_id)
            return (await s.execute(q)).scalar_one_or_none()

    async def add_result(self, run_id: str, **fields: Any) -> None:
        """Store a case's result."""
        async with self.db.session() as s:
            s.add(m.EvalResult(id=new_id(), run_id=run_id, **fields))

    async def results(self, run_id: str) -> list[m.EvalResult]:
        """A run's results in the order they were made.

        Returns:
            Results.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.EvalResult)
                .where(m.EvalResult.run_id == run_id)
                .order_by(m.EvalResult.created_at, m.EvalResult.case_name)
            )
            return list((await s.execute(q)).scalars().all())


class TraceRepository(_Repo):
    """Traces of turns and server runs (``evokai.observability.tracing``)."""

    SUMMARY = (
        m.Trace.id,
        m.Trace.kind,
        m.Trace.session_id,
        m.Trace.subject_key,
        m.Trace.agent_name,
        m.Trace.agent_version,
        m.Trace.mode,
        m.Trace.status,
        m.Trace.reason,
        m.Trace.started_at,
        m.Trace.duration_ms,
        m.Trace.first_token_ms,
        m.Trace.model,
        m.Trace.fallback,
        m.Trace.input_tokens,
        m.Trace.output_tokens,
        m.Trace.cache_read_tokens,
        m.Trace.cache_write_tokens,
        m.Trace.model_calls,
        m.Trace.tool_calls,
        m.Trace.tool_errors,
        m.Trace.guard_events,
        m.Trace.error,
        m.Trace.feedback,
        m.Trace.cost_micro_usd,
        m.Trace.content,
    )
    """The columns of a list (everything but the spans)."""

    async def add(self, row: dict[str, Any]) -> None:
        """Store a finished trace (a feedback given meanwhile is kept)."""
        async with self.db.session() as s:
            existing = await s.get(m.Trace, row["id"])
            if existing is not None:
                for k, v in row.items():
                    if k != "feedback" or v is not None:
                        setattr(existing, k, v)
                return
            s.add(m.Trace(**row))

    async def get(self, application_id: str, trace_id: str) -> m.Trace | None:
        """A trace of the application.

        Returns:
            The trace, or None.
        """
        async with self.db.session() as s:
            q = sa.select(m.Trace).where(
                m.Trace.id == trace_id, m.Trace.application_id == application_id
            )
            return (await s.execute(q)).scalar_one_or_none()

    @staticmethod
    def _filters(
        application_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        agent: str | None = None,
        version: str | None = None,
        subject_key: str | None = None,
        outcome: str | None = None,
        kind: str | None = None,
        guard: bool | None = None,
        feedback: str | None = None,
        min_ms: int | None = None,
        errors: bool | None = None,
        session_id: str | None = None,
    ) -> list[Any]:
        t = m.Trace
        where: list[Any] = [t.application_id == application_id]
        if start is not None:
            where.append(t.started_at >= start)
        if end is not None:
            where.append(t.started_at < end)
        if agent:
            where.append(t.agent_name == agent)
        if version:
            where.append(t.agent_version == version)
        if subject_key:
            where.append(t.subject_key == subject_key[:16])
        if outcome:
            where.append(sa.or_(t.status == outcome, t.reason == outcome))
        if kind:
            where.append(t.kind == kind)
        if guard:
            where.append(t.guard_events > 0)
        if feedback == "down":
            where.append(t.feedback < 0)
        elif feedback == "up":
            where.append(t.feedback > 0)
        elif feedback == "any":
            where.append(t.feedback.is_not(None))
        if min_ms:
            where.append(t.duration_ms >= min_ms)
        if errors:
            where.append(t.error.is_(True))
        if session_id:
            where.append(t.session_id == session_id)
        return where

    async def query(
        self,
        application_id: str,
        *,
        limit: int = 50,
        before: datetime | None = None,
        **filters: Any,
    ) -> list[Any]:
        """Traces of an application, newest first, without their spans.

        Returns:
            Rows with the ``SUMMARY`` columns.
        """
        t = m.Trace
        where = self._filters(application_id, **filters)
        if before is not None:
            where.append(t.started_at < before)
        q = sa.select(*self.SUMMARY).where(*where).order_by(t.started_at.desc(), t.id).limit(limit)
        async with self.db.session() as s:
            return list((await s.execute(q)).all())

    async def for_session(self, application_id: str, session_id: str) -> list[Any]:
        """The traces of a conversation, oldest first, without their spans.

        Returns:
            Rows with the ``SUMMARY`` columns.
        """
        t = m.Trace
        q = (
            sa
            .select(*self.SUMMARY)
            .where(t.application_id == application_id, t.session_id == session_id)
            .order_by(t.started_at)
        )
        async with self.db.session() as s:
            return list((await s.execute(q)).all())

    async def for_versions(
        self, application_id: str, agent: str, start: datetime, end: datetime, limit: int = 50_000
    ) -> list[Any]:
        """What the version comparison reads of an agent's turns in a period.

        Returns:
            Rows: version, status, reason, duration, first token, tool calls and errors, guard
            events, feedback, cost (server side), error.
        """
        t = m.Trace
        q = (
            sa
            .select(
                t.agent_version,
                t.status,
                t.reason,
                t.duration_ms,
                t.first_token_ms,
                t.tool_calls,
                t.tool_errors,
                t.guard_events,
                t.feedback,
                t.cost_micro_usd,
                t.error,
                t.input_tokens,
                t.output_tokens,
            )
            .where(
                t.application_id == application_id,
                t.agent_name == agent,
                t.kind != "eval",
                t.started_at >= start,
                t.started_at < end,
            )
            .order_by(t.started_at.desc())
            .limit(limit)
        )
        async with self.db.session() as s:
            return list((await s.execute(q)).all())

    async def feedback(self, w: Who, message_id: str, value: int | None) -> None:
        """Put a thumbs given on a message on the trace of its turn."""
        async with self.db.session() as s:
            q = sa.select(m.Message.turn_id).where(
                m.Message.id == message_id,
                m.Message.application_id == w.application_id,
                m.Message.subject == w.subject,
            )
            turn_id = (await s.execute(q)).scalar_one_or_none()
            if turn_id:
                await s.execute(
                    sa
                    .update(m.Trace)
                    .where(m.Trace.id == turn_id, m.Trace.application_id == w.application_id)
                    .values(feedback=value)
                )

    async def for_subject(self, w: Who) -> list[m.Trace]:
        """Every trace of a subject (their data export).

        Returns:
            Traces, oldest first.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.Trace)
                .where(m.Trace.application_id == w.application_id, m.Trace.subject == w.subject)
                .order_by(m.Trace.started_at)
            )
            return list((await s.execute(q)).scalars().all())

    async def purge(self, now: datetime | None = None) -> int:
        """Delete traces older than their application's ``conversation_ttl_days``.

        Every trace follows it, server runs and external storage included (a trace is the
        record of a turn).

        Returns:
            How many were deleted.
        """
        now = now or utcnow()
        deleted = 0
        async with self.db.session() as s:
            apps = (
                await s.execute(
                    sa.select(m.Application.id, m.Application.conversation_ttl_days).where(
                        m.Application.conversation_ttl_days.is_not(None)
                    )
                )
            ).all()
            for app_id, ttl in apps:
                cutoff = now - timedelta(days=int(ttl or 0))
                r = await s.execute(
                    sa.delete(m.Trace).where(
                        m.Trace.application_id == app_id, m.Trace.started_at <= cutoff
                    )
                )
                deleted += _rowcount(r)
        return deleted


class ReviewNoteRepository(_Repo):
    """Reviewers' notes and labels on conversations."""

    async def add(self, **fields: Any) -> m.ReviewNote:
        """Store a note.

        Returns:
            The note.
        """
        row = m.ReviewNote(id=new_id(), created_at=utcnow(), **fields)
        async with self.db.session() as s:
            s.add(row)
        return row

    async def for_session(self, application_id: str, session_id: str) -> list[m.ReviewNote]:
        """The notes of a conversation, oldest first.

        Returns:
            Notes.
        """
        async with self.db.session() as s:
            q = (
                sa
                .select(m.ReviewNote)
                .where(
                    m.ReviewNote.application_id == application_id,
                    m.ReviewNote.session_id == session_id,
                )
                .order_by(m.ReviewNote.created_at)
            )
            return list((await s.execute(q)).scalars().all())

    async def delete(
        self, application_id: str, session_id: str, note_id: str
    ) -> m.ReviewNote | None:
        """Delete a note.

        Returns:
            The deleted note, or None when there was none.
        """
        async with self.db.session() as s:
            q = sa.select(m.ReviewNote).where(
                m.ReviewNote.id == note_id,
                m.ReviewNote.application_id == application_id,
                m.ReviewNote.session_id == session_id,
            )
            row = (await s.execute(q)).scalar_one_or_none()
            if row is not None:
                await s.delete(row)
            return row


class Store:
    """All repositories over one database."""

    def __init__(self, db: Database) -> None:
        """Bind the repositories to a database.

        Args:
            db: The database.
        """
        self.db = db
        self.applications = ApplicationRepository(db)
        self.agents = AgentRepository(db)
        self.mcp_servers = McpServerRepository(db)
        self.skills = SkillRepository(db)
        self.credentials = CredentialRepository(db)
        self.sessions = SessionRepository(db)
        self.messages = MessageRepository(db)
        self.summaries = SummaryRepository(db)
        self.turns = TurnRepository(db)
        self.events = EventRepository(db)
        self.pending = PendingRepository(db)
        from evokai.memory.store import MemoryRepository

        self.memories = MemoryRepository(db)
        self.usage = UsageRepository(db)
        self.prompts = PromptRepository(db)
        self.voices = VoiceRepository(db)
        self.bug_reports = BugReportRepository(db)
        self.grants = GrantRepository(db)
        self.security_events = SecurityEventRepository(db)
        self.audit = AuditRepository(db)
        self.console_users = ConsoleUserRepository(db)
        self.console_settings = ConsoleSettingRepository(db)
        self.console_preferences = ConsolePreferenceRepository(db)
        self.evals = EvalRepository(db)
        self.traces = TraceRepository(db)
        self.review_notes = ReviewNoteRepository(db)
        from evokai.knowledge.store import KnowledgeRepository

        self.knowledge = KnowledgeRepository(db)
        from evokai.workflows.store import WorkflowRepository

        self.workflows = WorkflowRepository(db)
        from evokai.connections.store import ConnectionRepository

        self.connections = ConnectionRepository(db)
        from evokai.channels.store import ChannelRepository

        self.channels = ChannelRepository(db)
        from evokai.capabilities.store import (
            CapabilityCredentialRepository,
            CapabilityRecordRepository,
        )

        self.capabilities = CapabilityRecordRepository(db)
        self.capability_credentials = CapabilityCredentialRepository(db)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored time is UTC.

    Returns:
        The time, aware.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _cutoff(s: Any, application_id: str) -> tuple[datetime | None, str]:
    """The oldest last-message time still alive for an application, if it has a time to live.

    The time to live applies to conversations stored in Evokai (a conversation's own storage mode,
    else the application's).

    Returns:
        The cutoff (None without a time to live) and the application's storage mode.
    """
    q = sa.select(m.Application.conversation_ttl_days, m.Application.storage_mode).where(
        m.Application.id == application_id
    )
    row = (await s.execute(q)).first()
    if row is None:
        return None, "evokai"
    mode = str(row[1] or "evokai")
    if not row[0]:
        return None, mode
    return utcnow() - timedelta(days=int(row[0])), mode


def _stored_here(app_mode: str) -> Any:
    """A condition: the conversation is stored in Evokai (its own mode, else the application's).

    Returns:
        The SQL expression.
    """
    return sa.func.coalesce(m.Session.storage_mode, app_mode) == "evokai"


def _alive(cutoff: datetime | None, app_mode: str) -> Any:
    """A condition: the conversation has not passed the time to live.

    Returns:
        The SQL expression (always true without a cutoff).
    """
    if cutoff is None:
        return sa.true()
    return sa.or_(m.Session.updated_at > cutoff, sa.not_(_stored_here(app_mode)))


async def _forget_sessions(s: Any, ids: list[str]) -> None:
    """Delete everything a conversation holds; keep usage and bug reports, unlinked."""
    await s.execute(
        sa
        .update(m.UsageRecord)
        .where(m.UsageRecord.session_id.in_(ids))
        .values(session_id=None, turn_id=None)
    )
    await s.execute(
        sa.update(m.BugReport).where(m.BugReport.session_id.in_(ids)).values(session_id=None)
    )
    for model in (
        m.Event,
        m.Message,
        m.ConversationSummary,
        m.CapabilityRecord,
        m.PendingAction,
        m.Turn,
        m.Thread,
        m.Trace,
        m.ReviewNote,
    ):
        await s.execute(sa.delete(model).where(model.session_id.in_(ids)))
    await s.execute(sa.delete(m.Session).where(m.Session.id.in_(ids)))
