"""The harness stores, on Evokai's database (``evokai_capability_records``).

Every store is scoped to one application and one person, like everything else Evokai keeps:

- ``memory``: the agent's memory files, per person and agent, only while the person's memory is
  active (Evokai storage, the application's memory mode and the person's consent, as Evokai's own
  memory); every write passes the guard's ``memory.write`` control; ``memory_ttl_days`` expires
  them; a withdrawn consent or an erasure deletes them.
- ``planning`` and ``step_persistence``: per conversation, kept with it (Evokai storage only:
  they are conversation content); with external storage they live only for the turn.
- ``tool_output_limits``: a spilled tool result, kept with its conversation (Evokai storage), for
  a week at most; with external storage only for the turn.
- ``spend_limits``: counters per window, priced with Evokai's prices; never shown to people.
- ``conversation_search``: no store of its own: it reads the person's conversations kept by
  Evokai.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import TypeAdapter

from evokai.capabilities.store import ANY, CapabilityRecordRepository
from evokai.core.util import utcnow

OVERFLOW_KEPT = timedelta(days=7)
"""A spilled tool result is kept this long at most (and never longer than its conversation)."""

SNAPSHOTS_KEPT = 20
"""Step-log snapshots kept per run (the newest)."""


@dataclass
class Owner:
    """Whose records a store reads and writes."""

    repo: CapabilityRecordRepository
    app_id: str
    subject: str
    session_id: str | None = None


# memory files


class DbMemoryStore:
    """The harness ``MemoryStore`` (and its search and metadata extensions) on Evokai's database.

    One per person and agent. ``allow`` is the guard's ``memory.write`` control: a write it refuses
    raises, and the model reads why.
    """

    def __init__(
        self,
        owner: Owner,
        agent: str,
        *,
        ttl_days: int | None = None,
        allow: Any = None,
    ) -> None:
        """Bind to a person and an agent.

        Args:
            owner: The person.
            agent: The agent whose files these are.
            ttl_days: Days a file is kept after it was written (the application's memory retention).
            allow: ``(content) -> bool``: whether the content may be remembered.
        """
        self.owner, self.agent, self.ttl_days, self.allow = owner, agent, ttl_days, allow

    def _expires(self) -> datetime | None:
        return utcnow() + timedelta(days=self.ttl_days) if self.ttl_days else None

    async def read(self, path: str, *, max_chars: int) -> Any:
        """A file.

        Returns:
            ``MemoryFile``, or None.

        Raises:
            ValueError: A bad path or size.
        """
        from pydantic_ai_harness.memory._store import (
            MemoryFile,
            decode_metadata,
            validate_store_path,
        )

        validate_store_path(path)
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        o = self.owner
        row = await o.repo.get(o.app_id, o.subject, "memory", self.agent, path)
        if row is None:
            return None
        meta = row.meta or {}
        return MemoryFile(
            content=row.value[:max_chars],
            version=row.version,
            operation_id=meta.get("operation"),
            truncated=len(row.value) > max_chars,
            metadata=decode_metadata(meta.get("metadata")),
        )

    async def get_operation(self, operation: Any) -> Any:
        """A prior result of an operation (not kept: writes are not replayed here)."""
        del operation

    async def write(
        self,
        path: str,
        content: str,
        *,
        expected_version: str | None,
        operation: Any = None,
    ) -> Any:
        """Create or replace a file if its version is the expected one.

        Returns:
            ``MemoryMutation``.
        """
        return await self.write_with_metadata(
            path, content, expected_version=expected_version, metadata=None, operation=operation
        )

    async def write_with_metadata(
        self,
        path: str,
        content: str,
        *,
        expected_version: str | None,
        metadata: Mapping[str, str] | None,
        operation: Any = None,
    ) -> Any:
        """Create or replace a file, recording what the write says about itself.

        Returns:
            ``MemoryMutation``.

        Raises:
            ValueError: The guard refuses the content.
            MemoryConflictError: The file changed before it could be written.
        """
        from pydantic_ai_harness.memory._store import (
            MemoryConflictError,
            MemoryMutation,
            encode_metadata,
            validate_store_path,
        )

        validate_store_path(path)
        if self.allow is not None and not self.allow(content):
            raise ValueError(
                "Not remembered: memories may not hold instructions, secrets or personal data."
            )
        o = self.owner
        existed = await o.repo.get(o.app_id, o.subject, "memory", self.agent, path) is not None
        version = await o.repo.put(
            o.app_id,
            o.subject,
            "memory",
            self.agent,
            path,
            content,
            expires_at=self._expires(),
            meta={
                "metadata": encode_metadata(metadata),
                "operation": operation.id if operation is not None else None,
            },
            expected=expected_version,
        )
        if version is None:
            raise MemoryConflictError(f"memory path {path!r} changed before it could be written")
        return MemoryMutation(version=version, replayed=False, existed=existed)

    async def delete(
        self, path: str, *, expected_version: str | None, operation: Any = None
    ) -> Any:
        """Delete a file if its version is the expected one.

        Returns:
            ``MemoryMutation``.

        Raises:
            MemoryConflictError: The file changed before it could be deleted.
        """
        from pydantic_ai_harness.memory._store import (
            MemoryConflictError,
            MemoryMutation,
            validate_store_path,
        )

        del operation
        validate_store_path(path)
        o = self.owner
        existed = await o.repo.delete(
            o.app_id, o.subject, "memory", self.agent, path, expected=expected_version
        )
        if existed is None:
            raise MemoryConflictError(f"memory path {path!r} changed before it could be deleted")
        return MemoryMutation(version=None, replayed=False, existed=existed)

    async def list_paths(self, prefix: str = "", *, limit: int) -> list[str]:
        """The stored paths starting with a prefix, sorted.

        Returns:
            Paths.

        Raises:
            ValueError: A bad prefix or limit.
        """
        from pydantic_ai_harness.memory._store import validate_store_prefix

        validate_store_prefix(prefix)
        if limit <= 0:
            raise ValueError("limit must be positive")
        o = self.owner
        rows = await o.repo.items(o.app_id, o.subject, "memory", self.agent, prefix=prefix)
        return [r.key for r in rows][:limit]

    async def search(
        self,
        prefix: str,
        query: str,
        *,
        limit: int,
        max_files: int,
        max_chars: int,
        max_file_chars: int,
    ) -> Any:
        """Search the files directly below a prefix.

        Returns:
            ``MemorySearchResult``.
        """
        from pydantic_ai_harness.memory._store import (
            MemorySearchResult,
            is_direct_child,
            lexical_search,
            validate_store_prefix,
        )

        validate_store_prefix(prefix)
        if not query.split() or min(limit, max_files, max_chars, max_file_chars) <= 0:
            return MemorySearchResult(matches=[], scanned=0, truncated=False)
        o = self.owner
        rows = await o.repo.items(o.app_id, o.subject, "memory", self.agent, prefix=prefix)
        selected = [r for r in rows if is_direct_child(prefix, r.key)]
        scanned = selected[:max_files]
        files = [(r.key, r.value[:max_file_chars]) for r in scanned]
        result = await lexical_search(
            files, query, limit=limit, max_files=max_files, max_chars=max_chars, score_prefix=prefix
        )
        return MemorySearchResult(
            matches=result.matches,
            scanned=result.scanned,
            truncated=result.truncated
            or len(selected) > max_files
            or any(len(r.value) > max_file_chars for r in scanned),
        )


# plans


def plan_store(owner: Owner | None, agent: str, items: list[Any] | None) -> Any:
    """The plan of a conversation: kept with it (Evokai storage), or only for the turn.

    Args:
        owner: The conversation's owner (None: nothing at rest).
        agent: The agent whose plan it is.
        items: The plan as loaded (``load_plan``).

    Returns:
        A harness ``PlanStore``.
    """
    from pydantic_ai_harness.planning import InMemoryPlanStore

    class DbPlanStore(InMemoryPlanStore):
        async def _save(self) -> None:
            if owner is None or owner.session_id is None:
                return
            value = json.dumps([i.model_dump(mode="json") for i in await self.get_items()])
            await owner.repo.put(
                owner.app_id,
                owner.subject,
                "planning",
                owner.session_id,
                agent,
                value,
                session_id=owner.session_id,
            )

        async def set_items(self, items: list[Any]) -> None:
            await super().set_items(items)
            await self._save()

        async def add_item(self, item: Any) -> Any:
            out = await super().add_item(item)
            await self._save()
            return out

        async def update_item(self, item_id: str, **changes: Any) -> Any:
            out = await super().update_item(item_id, **changes)
            await self._save()
            return out

        async def remove_item(self, item_id: str) -> bool:
            out = await super().remove_item(item_id)
            await self._save()
            return out

    store = DbPlanStore()
    store._items = list(items or [])
    return store


async def load_plan(owner: Owner, agent: str) -> list[Any]:
    """A conversation's plan as last saved.

    Returns:
        ``PlanItem`` list (empty without one).
    """
    from pydantic_ai_harness.planning import PlanItem

    if owner.session_id is None:
        return []
    row = await owner.repo.get(owner.app_id, owner.subject, "planning", owner.session_id, agent)
    if row is None:
        return []
    return [PlanItem.model_validate(x) for x in json.loads(row.value)]


# step persistence


async def step_store(owner: Owner | None) -> Any:
    """The step log of a conversation: kept with it (Evokai storage), or only for the turn.

    Records are written through as the capability appends them; the conversation's earlier ones
    are loaded first, so a later turn continues the same log.

    Returns:
        A harness ``StepStore``.
    """
    from pydantic_ai_harness.step_persistence import (
        ContinuableSnapshot,
        InMemoryStepStore,
        RunRecord,
        StepEvent,
        ToolEffectRecord,
    )

    run_ta = TypeAdapter(RunRecord)
    event_ta = TypeAdapter(StepEvent)
    snaps_ta = TypeAdapter(list[ContinuableSnapshot])
    effect_ta = TypeAdapter(ToolEffectRecord)

    class DbStepStore(InMemoryStepStore):
        async def _put(self, key: str, value: bytes) -> None:
            if owner is None or owner.session_id is None:
                return
            await owner.repo.put(
                owner.app_id,
                owner.subject,
                "step_persistence",
                owner.session_id,
                key,
                value.decode(),
                session_id=owner.session_id,
            )

        async def register_run(self, record: Any) -> None:
            await super().register_run(record)
            await self._put(f"run/{record.run_id}", run_ta.dump_json(record))

        async def append_event(self, event: Any) -> None:
            before = len(await self.list_events(run_id=event.run_id))
            await super().append_event(event)
            events = await self.list_events(run_id=event.run_id)
            if len(events) > before:
                await self._put(f"event/{event.run_id}/{before:06d}", event_ta.dump_json(event))

        async def save_snapshot(self, snapshot: Any) -> None:
            await super().save_snapshot(snapshot)
            kept = await self.list_snapshots(run_id=snapshot.run_id, include_interrupted=True)
            await self._put(f"snapshots/{snapshot.run_id}", snaps_ta.dump_json(kept))

        async def record_tool_effect(self, record: Any) -> None:
            await super().record_tool_effect(record)
            await self._put(
                f"effect/{record.run_id}/{record.tool_call_id}", effect_ta.dump_json(record)
            )

    store = DbStepStore(max_snapshots_per_run=SNAPSHOTS_KEPT)
    if owner is None or owner.session_id is None:
        return store
    rows = await owner.repo.items(owner.app_id, owner.subject, "step_persistence", owner.session_id)
    for r in rows:
        kind = r.key.split("/", 1)[0]
        if kind == "run":
            await InMemoryStepStore.register_run(store, run_ta.validate_json(r.value))
        elif kind == "event":
            await InMemoryStepStore.append_event(store, event_ta.validate_json(r.value))
        elif kind == "snapshots":
            for snap in snaps_ta.validate_json(r.value):
                await InMemoryStepStore.save_snapshot(store, snap)
        elif kind == "effect":
            await InMemoryStepStore.record_tool_effect(store, effect_ta.validate_json(r.value))
    return store


# spilled tool results


class DbOverflowStore:
    """The harness ``OverflowStore``: spilled tool results kept with their conversation."""

    def __init__(self, owner: Owner) -> None:
        """Bind to a conversation.

        Args:
            owner: The conversation's owner (``session_id`` set).
        """
        self.owner = owner

    async def write(self, key: str, data: bytes) -> str:
        """Keep a result.

        Returns:
            Its handle (the key).
        """
        o = self.owner
        await o.repo.put(
            o.app_id,
            o.subject,
            "tool_output_limits",
            o.session_id or "",
            key,
            base64.b64encode(data).decode(),
            session_id=o.session_id,
            expires_at=utcnow() + OVERFLOW_KEPT,
        )
        return key

    async def read(self, handle: str) -> bytes:
        """A result kept before.

        Returns:
            Its bytes.

        Raises:
            KeyError: Unknown or expired handle.
        """
        o = self.owner
        row = await o.repo.get(
            o.app_id, o.subject, "tool_output_limits", o.session_id or "", handle
        )
        if row is None:
            raise KeyError(handle)
        return base64.b64decode(row.value)


# spend


class DbSpendStore:
    """The harness ``BatchSpendStore``: spend counters per window in Evokai's database.

    A counter's key is the harness's (budget, scope, window bucket); its amounts are never shown to
    people (the console shows usage as a share of a cap).
    """

    def __init__(self, repo: CapabilityRecordRepository, app_id: str) -> None:
        """Bind to an application.

        Args:
            repo: The records.
            app_id: The application.
        """
        self.repo, self.app_id = repo, app_id

    async def _get(self, key: str) -> Any:
        from pydantic_ai_harness.spend_limits import Spent

        row = await self.repo.get(self.app_id, "", "spend_limits", "spend", key)
        if row is None:
            return Spent()
        v = json.loads(row.value)
        return Spent(
            usd=Decimal(v["usd"]),
            tokens=int(v["tokens"]),
            requests=int(v["requests"]),
            unpriced_requests=int(v.get("unpriced", 0)),
        )

    async def get_many(self, keys: Sequence[str]) -> Mapping[str, Any]:
        """What each key has accumulated.

        Returns:
            Key to ``Spent``.
        """
        return {k: await self._get(k) for k in keys}

    async def add_many(self, entries: Sequence[Any]) -> Mapping[str, Any]:
        """Apply every entry.

        Returns:
            Key to its new ``Spent``.
        """
        from pydantic_ai_harness.spend_limits import Spent

        out: dict[str, Any] = {}
        for e in entries:
            cur = out.get(e.key) or await self._get(e.key)
            new = Spent(
                usd=cur.usd + e.usd,
                tokens=cur.tokens + e.tokens,
                requests=cur.requests + e.requests,
                unpriced_requests=cur.unpriced_requests + e.unpriced,
            )
            value = json.dumps({
                "usd": str(new.usd),
                "tokens": new.tokens,
                "requests": new.requests,
                "unpriced": new.unpriced_requests,
            })
            await self.repo.put(
                self.app_id,
                "",
                "spend_limits",
                "spend",
                e.key,
                value,
                expires_at=utcnow() + e.ttl if e.ttl is not None else None,
                expected=ANY,
            )
            out[e.key] = new
        return out


# conversation search


class EvokaiHistorySource:
    """The harness ``HistorySource`` over the person's conversations kept by Evokai."""

    def __init__(self, store: Any, app: Any, who: Any, *, limit: int = 50) -> None:
        """Bind to a person.

        Args:
            store: Evokai's repositories.
            app: The application.
            who: The person.
            limit: The most recent conversations searched.
        """
        self.store, self.app, self.who, self.limit = store, app, who, limit

    async def list_runs(self) -> list[Any]:
        """The person's conversations, oldest first.

        Returns:
            ``RunRecord`` list (one per conversation).
        """
        import sqlalchemy as sa
        from pydantic_ai_harness.step_persistence import RunRecord

        from evokai.db import models as m

        async with self.store.db.session() as s:
            q = (
                sa
                .select(m.Session)
                .where(
                    m.Session.application_id == self.who.application_id,
                    m.Session.subject == self.who.subject,
                    sa.or_(m.Session.storage_mode.is_(None), m.Session.storage_mode == "evokai"),
                )
                .order_by(m.Session.updated_at.desc())
                .limit(self.limit)
            )
            rows = list((await s.execute(q)).scalars().all())
        return [
            RunRecord(
                run_id=x.id,
                conversation_id=x.id,
                agent_name=x.agent_name,
                started_at=_aware(x.created_at),
            )
            for x in sorted(rows, key=lambda x: _aware(x.created_at))
        ]

    async def run_history(self, *, run_id: str) -> list[Any]:
        """One conversation's messages, as the model saw them (text only).

        Returns:
            Messages (none for a conversation that is not the person's).
        """
        import sqlalchemy as sa
        from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

        from evokai.db import models as m

        async with self.store.db.session() as s:
            owned = (
                await s.execute(
                    sa.select(m.Session.id).where(
                        m.Session.id == run_id,
                        m.Session.application_id == self.who.application_id,
                        m.Session.subject == self.who.subject,
                    )
                )
            ).scalar_one_or_none()
            if owned is None:
                return []
            q = (
                sa
                .select(m.Message)
                .where(m.Message.session_id == run_id)
                .order_by(m.Message.position)
            )
            rows = list((await s.execute(q)).scalars().all())
        out: list[Any] = []
        for x in rows:
            if not x.text:
                continue
            if x.role == "user":
                out.append(ModelRequest(parts=[UserPromptPart(content=x.text)]))
            else:
                out.append(ModelResponse(parts=[TextPart(content=x.text)]))
        return out


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
