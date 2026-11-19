"""The memory service: consent, writing with consolidation, recall, episodes, expiry.

Embeddings reuse the knowledge path (``EmbeddingCatalog``: Titan Text Embeddings v2 in the
deployment's region, within ``EVOKAI_EMBEDDING_ALLOW`` and the data residency); each call is a
usage record of kind ``embed`` tagged ``evokai:memory``. Without an allowed embedding model, or when
Bedrock does not answer, memories are compared and recalled by their words.
"""

from __future__ import annotations

import contextlib
import fnmatch
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelRequest, SystemPromptPart, TextPart, UserPromptPart

from evokai.core.errors import AppError, BadRequest, NotFound
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.guard import pii, secrets
from evokai.knowledge.embeddings import pack, unpack
from evokai.llm.catalog import bare, normalize
from evokai.llm.prices import call_cost_micro_usd, embedding_cost_micro_usd, estimate_call_usd
from evokai.memory.consolidate import Candidate, decide, normalize_fact
from evokai.memory.recall import DEFAULT_K, Recallable, Recalled, rank
from evokai.privacy import residency_allows
from evokai.protection import protection
from evokai.sessions.limits import cap_state

if TYPE_CHECKING:
    from evokai.services import Services

log = logging.getLogger("evokai.memory")

MODES = ("off", "ask", "on")
KINDS = ("preference", "semantic", "episodic")
TAGS = ["evokai:memory"]
MAX_FACT = 300
"""Characters of a preference or fact (an episode may take up to ``MAX_EPISODE``)."""
MAX_EPISODE = 600
EPISODE_LOOKBACK = timedelta(days=7)
"""Conversations finished longer ago than this are not summarised any more."""
EPISODES_PER_PASS = 20

SUMMARY_PROMPT = (
    "You write the memory an assistant keeps of a finished conversation with one person at work. "
    "In one or two short sentences (at most 300 characters), in the language of the "
    "conversation, say what the person worked on and how it ended. Write about their work, never "
    "about their customers: no names of customers, no codes, identifiers, e-mail addresses, phone "
    "numbers or amounts. No instructions. If nothing worth remembering happened, answer NONE."
)


def memory_mode(app: m.Application) -> str:
    """The application's memory mode.

    Returns:
        ``off``, ``ask`` or ``on`` (the default, what Evokai did before consent existed).
    """
    mode = (app.defaults or {}).get("memory_mode", "on")
    return mode if mode in MODES else "on"


def ttl_days(app: m.Application) -> int | None:
    """How long a memory lives after it was written or confirmed.

    Returns:
        Days, or None to keep memories until they are deleted.
    """
    value = (app.defaults or {}).get("memory_ttl_days")
    return int(value) if value else None


@dataclass(frozen=True)
class MemoryState:
    """Whether agents may remember things about a person now."""

    mode: str
    consent: bool | None
    """The person's answer: True, False, or None when never asked."""

    @property
    def active(self) -> bool:
        """Agents may remember and recall (``on`` unless the person said no; ``ask`` after a yes).

        Returns:
            True when memory is on for this person.
        """
        if self.mode == "on":
            return self.consent is not False
        return self.mode == "ask" and self.consent is True

    @property
    def ask(self) -> bool:
        """The chat panel should ask the person once.

        Returns:
            True with ``ask`` and no answer yet.
        """
        return self.mode == "ask" and self.consent is None

    def view(self) -> dict[str, Any]:
        """For the session API and the chat panel.

        Returns:
            mode, consent (``granted``, ``declined`` or ``unknown``), active, ask.
        """
        consent = {True: "granted", False: "declined", None: "unknown"}[self.consent]
        return {"mode": self.mode, "consent": consent, "active": self.active, "ask": self.ask}


@dataclass(frozen=True)
class Remembered:
    """What happened to a memory the agent or the person wrote."""

    id: str
    action: str
    """``new``, ``duplicate`` (an existing memory was refreshed) or ``supersede``."""
    replaced: str | None = None


class MemoryService:
    """Memories of the application's people, as one service per process."""

    def __init__(self, svc: Services) -> None:
        """Bind to the process services."""
        self.svc = svc

    @property
    def repo(self) -> Any:
        """The memory repository.

        Returns:
            ``evokai.memory.store.MemoryRepository``.
        """
        return self.svc.store.memories

    # consent

    async def state(self, app: m.Application, who: Who) -> MemoryState:
        """The application's mode and the person's answer.

        Returns:
            The state.
        """
        mode = memory_mode(app)
        if mode == "off":
            return MemoryState(mode, None)
        row = await self.repo.consent(who)
        return MemoryState(mode, row.granted if row else None)

    async def set_consent(self, app: m.Application, who: Who, granted: bool, source: str) -> int:
        """Record the person's answer; a no (or a withdrawal) deletes what was remembered.

        Returns:
            How many memories were deleted.
        """
        await self.repo.set_consent(who, granted, source)
        deleted = 0 if granted else await self.repo.forget(who)
        log.info(
            "memory_consent",
            extra={"applicationId": app.id, "granted": granted, "source": source},
        )
        return deleted

    # embeddings

    def _embedding_model(self, app: m.Application) -> str | None:
        name = normalize(self.svc.settings.memory_embedding_model)
        catalog = getattr(self.svc.knowledge, "catalog", None)
        if catalog is None or not catalog.allowed(name, protection(app)):
            return None
        return name

    async def _embed(
        self,
        app: m.Application,
        who: Who,
        texts: list[str],
        kind: str,
        *,
        agent: str = "",
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> tuple[list[list[float]] | None, str | None]:
        """Embed texts for one person, as a usage record (kind ``embed``).

        Returns:
            The vectors and the model, or (None, None) without an embedding model or on failure.
        """
        model = self._embedding_model(app)
        if model is None or not texts:
            return None, None
        dims = self.svc.settings.memory_embedding_dimensions
        started = time.perf_counter()
        try:
            embedded = await self.svc.knowledge.catalog.get(model, dims).embed(texts, kind)
        except Exception as e:
            log.warning(
                "memory_embedding_failed",
                extra={"applicationId": app.id, "error_type": type(e).__name__},
            )
            return None, None
        with contextlib.suppress(Exception):
            await self.svc.store.usage.record(
                who,
                TAGS,
                kind="embed",
                model=bare(model),
                agent_name=agent,
                session_id=session_id,
                turn_id=turn_id,
                input_tokens=embedded.tokens,
                cost_micro_usd=embedding_cost_micro_usd(model, embedded.tokens),
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        return embedded.vectors, model

    # writing

    async def remember(
        self,
        app: m.Application,
        who: Who,
        text: str,
        *,
        agent: str = "",
        kind: str = "preference",
        source: str = "agent",
        session_id: str | None = None,
        replaces: str | None = None,
    ) -> Remembered:
        """Store a memory, merging it with a near-duplicate or superseding a contradicted one.

        The caller has already passed it through the guard (``memory.write``).

        Args:
            app: The application (TTL, residency).
            who: The person.
            text: What to remember.
            agent: The agent that writes it.
            kind: ``preference``, ``semantic`` or ``episodic``.
            source: ``agent``, ``summary`` or ``person``.
            session_id: The conversation it comes from.
            replaces: A memory the agent says this one replaces (it is superseded).

        Returns:
            What happened.

        Raises:
            BadRequest: An empty text or an unknown kind.
        """
        if kind not in KINDS:
            raise BadRequest(f"Unknown memory kind {kind}.", code="memory_kind")
        fact = normalize_fact(text)[: MAX_EPISODE if kind == "episodic" else MAX_FACT]
        if not fact:
            raise BadRequest("Nothing to remember.", code="memory_empty")
        vectors, model = await self._embed(
            app, who, [fact], "document", agent=agent, session_id=session_id
        )
        vector = vectors[0] if vectors else None
        now = utcnow()
        days = ttl_days(app)
        expires = now + timedelta(days=days) if days else None
        group = await self._group(who, agent, kind)
        target: m.Memory | None = None
        if replaces:
            target = next((x for x in group if x.id == replaces), None)
        decision = (
            None
            if target is not None
            else decide(
                fact,
                vector,
                [
                    Candidate(x.id, x.fact, unpack(x.embedding) if x.embedding else None)
                    for x in group
                ],
            )
        )
        if decision is not None and decision.action == "duplicate" and decision.target:
            fields: dict[str, Any] = {"fact": decision.text, "expires_at": expires}
            if decision.text == fact and vector is not None:
                fields.update(embedding=pack(vector), embedding_model=model)
            await self.repo.update(who, decision.target, **fields)
            return Remembered(decision.target, "duplicate")
        new = await self.repo.add(
            who,
            fact,
            kind=kind,
            agent_name=agent[:80],
            session_id=session_id,
            source=source,
            embedding=pack(vector) if vector is not None else None,
            embedding_model=model,
            expires_at=expires,
        )
        old = target.id if target is not None else None
        if decision is not None and decision.action == "supersede":
            old = decision.target
        if old:
            await self.repo.supersede(who, old, new)
            return Remembered(new, "supersede", old)
        return Remembered(new, "new")

    async def _group(self, who: Who, agent: str, kind: str) -> list[m.Memory]:
        """The active memories a new one is consolidated with.

        Returns:
            Preferences and facts together; an agent's episodes apart.
        """
        rows = await self.repo.recallable(who, agent)
        if kind == "episodic":
            return [x for x in rows if x.kind == "episodic"]
        return [x for x in rows if x.kind != "episodic"]

    async def edit(self, app: m.Application, who: Who, memory_id: str, text: str) -> m.Memory:
        """The person rewrites a memory (it keeps its kind; its source becomes ``person``).

        Returns:
            The updated memory.

        Raises:
            NotFound: Not one of the person's active memories.
            BadRequest: An empty text.
        """
        row = await self.repo.get(who, memory_id)
        if row is None or row.superseded_by:
            raise NotFound("Unknown memory.", code="unknown_memory")
        fact = normalize_fact(text)[: MAX_EPISODE if row.kind == "episodic" else MAX_FACT]
        if not fact:
            raise BadRequest("Nothing to remember.", code="memory_empty")
        vectors, model = await self._embed(app, who, [fact], "document", agent=row.agent_name)
        days = ttl_days(app)
        await self.repo.update(
            who,
            memory_id,
            fact=fact,
            source="person",
            embedding=pack(vectors[0]) if vectors else None,
            embedding_model=model,
            expires_at=utcnow() + timedelta(days=days) if days else None,
        )
        updated = await self.repo.get(who, memory_id)
        assert updated is not None  # ruff: ignore[assert] (just updated)
        return updated

    # recall

    async def recall(
        self,
        app: m.Application,
        who: Who,
        agent: str,
        query: str,
        *,
        k: int = DEFAULT_K,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[Recalled]:
        """The memories for one turn: preferences, then the most relevant facts and episodes.

        The query is embedded only when there are more candidates than ``k``.

        Returns:
            The memories, in prompt order.
        """
        rows = await self.repo.recallable(who, agent)
        if not rows:
            return []
        items = [
            Recallable(
                x.id,
                x.kind,
                x.fact,
                x.updated_at or x.created_at,
                unpack(x.embedding) if x.embedding else None,
            )
            for x in rows
        ]
        rest = [x for x in items if x.kind != "preference"]
        query_vector = None
        if len(rest) > k and query.strip() and any(x.vector for x in rest):
            vectors, _ = await self._embed(
                app,
                who,
                [query.strip()[:2_000]],
                "query",
                agent=agent,
                session_id=session_id,
                turn_id=turn_id,
            )
            query_vector = vectors[0] if vectors else None
        now = utcnow()
        picked = rank(items, query=query, query_vector=query_vector, now=now, k=k)
        with contextlib.suppress(Exception):
            await self.repo.touch(who, [x.id for x in picked], now)
        return picked

    # episodes

    def _summary_model(self, app: m.Application, agent_model: str) -> str | None:
        name = self.svc.settings.memory_summary_model or agent_model
        if not self.svc.catalog.allowed(name):
            return None
        if not residency_allows(protection(app), name, self.svc.settings.aws_region):
            return None
        allowed = app.allowed_models or []
        if allowed and not any(fnmatch.fnmatchcase(normalize(name), normalize(p)) for p in allowed):
            return None
        return name

    async def summarize(self, app: m.Application, session: m.Session) -> str | None:
        """Write the episode of a finished conversation, when its agent keeps episodes.

        Returns:
            The episode's id, or None (no episodes for this agent, no consent, nothing worth
            remembering, a cap reached, or the guard refused it).
        """
        who = Who(app.id, session.subject)
        if not (await self.state(app, who)).active:
            return None
        if await self.repo.episode_of(app.id, session.id):
            return None
        try:
            agent = await self.svc.agents.pinned(app.id, session.agent_name, session.agent_version)
        except AppError:
            return None
        if agent.memory is None or not agent.memory.episodes:
            return None
        thread = await self.svc.store.sessions.thread(session.id)
        msgs = await self.svc.store.messages.recent(who, thread.id, 60)
        lines = [f"{x.role}: {x.text.strip()}" for x in msgs if x.text and x.text.strip()]
        if sum(1 for x in msgs if x.role == "user" and x.text) < 2:
            return None
        model = self._summary_model(app, agent.model)
        if model is None:
            return None
        caps = await cap_state(self.svc.store, app, who, utcnow())
        if caps.hit(estimate_call_usd(model)):
            return None
        transcript = pii.mask("\n".join(lines)[-12_000:])
        started = time.perf_counter()
        response = await model_request(
            self.svc.catalog.get(model),
            [
                ModelRequest(
                    parts=[
                        SystemPromptPart(content=SUMMARY_PROMPT),
                        UserPromptPart(content=f"Conversation:\n{transcript}"),
                    ]
                )
            ],
            model_settings={"max_tokens": 200, "temperature": 0},
        )
        await self.svc.store.usage.record(
            who,
            TAGS,
            agent_name=agent.name,
            session_id=session.id,
            kind="llm",
            model=bare(model),
            input_tokens=response.usage.input_tokens or 0,
            output_tokens=response.usage.output_tokens or 0,
            cost_micro_usd=call_cost_micro_usd(model, response.usage),
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        text = "".join(p.content for p in response.parts if isinstance(p, TextPart)).strip()
        if not text or text.upper().startswith("NONE"):
            return None
        text = secrets.redact(pii.mask(text), known=self.svc.guard.known_secrets)
        guard = self.svc.guard.turn(
            app, agent, subject=who.subject, session_id=session.id, turn_id=None, channel="browser"
        )
        allowed = guard.memory(text)
        await guard.flush()
        if not allowed:
            return None
        done = await self.remember(
            app,
            who,
            text,
            agent=agent.name,
            kind="episodic",
            source="summary",
            session_id=session.id,
        )
        return done.id

    async def summarize_finished(self, now: datetime | None = None) -> int:
        """Write the episodes of conversations that finished (idle long enough) and have none.

        Returns:
            How many episodes were written.
        """
        now = now or utcnow()
        idle = now - timedelta(minutes=self.svc.settings.memory_episode_idle_minutes)
        s_, mem = m.Session, m.Memory
        has_episode = (
            sa.select(mem.id).where(mem.session_id == s_.id, mem.kind == "episodic").exists()
        )
        async with self.svc.db.session() as s:
            rows = (
                (
                    await s.execute(
                        sa
                        .select(s_)
                        .where(
                            s_.updated_at < idle,
                            s_.updated_at > now - EPISODE_LOOKBACK,
                            sa.func.coalesce(s_.storage_mode, "evokai") == "evokai",
                            ~has_episode,
                        )
                        .order_by(s_.updated_at)
                        .limit(EPISODES_PER_PASS * 5)
                    )
                )
                .scalars()
                .all()
            )
        written = 0
        apps: dict[str, m.Application | None] = {}
        for session in rows:
            if written >= EPISODES_PER_PASS:
                break
            if session.application_id not in apps:
                apps[session.application_id] = await self.svc.store.applications.get(
                    session.application_id
                )
            app = apps[session.application_id]
            if app is None or memory_mode(app) == "off":
                continue
            try:
                if await self.summarize(app, session):
                    written += 1
            except Exception as e:
                log.warning(
                    "memory_episode_failed",
                    extra={"sessionId": session.id, "error_type": type(e).__name__},
                )
        return written
