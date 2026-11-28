"""One user turn.

A confirmation, an answer or a new message, then model ↔ tools until the model answers, asks the
user something, needs a confirmation, a cap stops it, or the turn is cancelled.

Robustness rules:
- every await that can take long (model stream, tool call) races the turn's own cancellation;
- a cancelled or failed turn keeps what was already said, marked as interrupted, and always ends
with `done`;
- the user's message is stored before the model runs, and tool calls never stay without a result
(history repair);
- the same step moves to the next model on a capacity error, but only if nothing reached the user
yet.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC
from typing import Any, Literal, cast
from urllib.parse import unquote_plus

from pydantic_ai import Agent
from pydantic_ai.exceptions import CallDeferred
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import DeferredToolResults, ToolDenied
from pydantic_ai.usage import UsageLimits

from evokai import attachments as files_
from evokai.agents.definitions import AgentDef
from evokai.agents.modules import protected, resolve_modules
from evokai.agents.prompts import (
    Instructions,
    PageContext,
    memory_block,
    split_skills,
    system_prompt,
    system_text,
    volatile_instructions,
)
from evokai.agents.registry import AgentRegistry
from evokai.agents.skills import Skill, SkillFile
from evokai.agents.skills import load as load_skill
from evokai.audio.speech.markup import Markup, markup_of
from evokai.audio.speech.numbers import spell_numbers
from evokai.audio.speech.replies import spoken_decision, spoken_option
from evokai.audio.speech.speakable import SentenceSplitter, speakable
from evokai.core.config import Settings
from evokai.core.errors import (
    BadRequest,
    ConfigError,
    Conflict,
    Forbidden,
    NotFound,
    Unauthorized,
)
from evokai.core.util import clip, new_id, utcnow
from evokai.db import models as m
from evokai.db.repositories import DEFAULT_TITLE, Store, Who
from evokai.engine import builtin_tools
from evokai.engine.agent import build_agent
from evokai.engine.capability import finish_attempt
from evokai.engine.client_calls import ClientCalls, ClientToolWait
from evokai.engine.code_mode import EvokaiCodeMode
from evokai.engine.compaction import Compactor, EvokaiCompaction, estimate_tokens, over
from evokai.engine.messages import (
    keep_reasoning,
    reasoning_block,
    response_blocks,
    result_blocks,
    resume_reasoning,
    to_stored,
)
from evokai.engine.models import USAGE_KIND as CAP_KIND
from evokai.engine.models import RecordedModel
from evokai.engine.state import RunState, Scope, StopRun
from evokai.engine.subagents import EvokaiSubAgents
from evokai.engine.tools import EvokaiToolset, GuardedToolset, ToolSpec
from evokai.engine.workspace import NoLocalWorkspace
from evokai.grants import Grants, NotGranted, grants_of
from evokai.guard import Guard, TurnGuard, identifiers
from evokai.guard.ask import REASON_WRITES
from evokai.i18n import (
    SpeechRules,
    locale_of,
    message,
    session_locale,
    speech_redaction,
    speech_rules,
)
from evokai.identity.models import SessionPrincipal
from evokai.knowledge.tool import CITE_NOTE, format_results, record_found, retrieval_span
from evokai.knowledge.tool import TOOL_NAME as KNOWLEDGE_TOOL
from evokai.knowledge.tool import tool_def as knowledge_tool
from evokai.llm.catalog import ModelCatalog, bare, normalize
from evokai.llm.health import ModelHealth
from evokai.llm.policy import agent_settings, output_cap, request_settings
from evokai.llm.prices import call_cost_micro_usd, estimate_call_usd
from evokai.mcp.client import ToolResult
from evokai.mcp.registry import McpRegistry, Toolbox
from evokai.memory.recall import DEFAULT_K as DEFAULT_RECALL
from evokai.observability import telemetry
from evokai.observability.logging import error_class
from evokai.observability.tracing import Span, Trace, content_mode
from evokai.privacy import residency_allows, subject_key
from evokai.protection import protection
from evokai.sandbox.skill_scripts import TOOL_NAME as SKILL_RUN
from evokai.sandbox.skill_scripts import run_script as sandbox_skill_script
from evokai.sandbox.skill_scripts import tool_def as skill_run_tool
from evokai.sandbox.tool import NOTE as CODE_NOTE
from evokai.sandbox.tool import TOOL_NAME as CODE_TOOL
from evokai.sandbox.tool import USAGE_KIND as CODE_KIND
from evokai.sandbox.tool import USAGE_MODEL as CODE_MODEL
from evokai.sandbox.tool import cost_micro_usd as code_cost
from evokai.sandbox.tool import run_code
from evokai.sandbox.tool import tool_def as code_tool
from evokai.sessions import rewrite
from evokai.sessions.cancel import TurnHandle, TurnRegistry
from evokai.sessions.events import ChannelHub, SessionChannel
from evokai.sessions.history import (
    Stored,
    flatten_tools,
    open_ask,
    open_question,
    repair,
    to_model_messages,
    window,
)
from evokai.sessions.limits import EVAL_LIMIT, cap_state, eval_cap_usd, limit_message
from evokai.sessions.live import CARD_KINDS, LiveStep
from evokai.sessions.parts import PartLog
from evokai.sessions.rewrite import Plan
from evokai.sessions.threads import (
    EvokaiThreads,
    ExternalThreads,
    HistoryMessage,
    ThreadMessage,
    ThreadStore,
)
from evokai.tools import custom, visuals
from evokai.tools.client_tools import HANDOFF, handoff_tool, local_tools
from evokai.tools.labels import confirm_label

log = logging.getLogger("evokai.turns")

# Model-facing texts (tool results and framing) are English; user-facing ones come from the locale.
SUBAGENT_KIND = "subagent"
"""The usage kind of a subagent's model calls (counted against the caps like any call)."""
REJECTED_TEXT = (
    "CANCELLED by the user: the change was NOT made and nothing changed. Say so in one sentence, "
    "without saying it was done."
)
SUPERSEDED_TEXT = "Not run: the user wrote a new message instead of confirming."
ANSWERED_WAIT = 10.0
"""Seconds a rewrite waits for a turn that already sent ``done`` to persist its last events."""


@dataclass
class TurnInput:
    """One user input in a session."""

    principal: SessionPrincipal
    text: str | None = None
    confirm: dict[str, Any] | None = None
    page: PageContext | None = None
    mode: Literal["text", "voice"] = "text"
    history: list[HistoryMessage] | None = None
    """External storage: the conversation so far, sent by the application."""
    end_of_speech_at: float | None = None
    """Client epoch ms when the user stopped speaking (voice): for the latency metric."""
    evaluation: bool = False
    """An eval case (``evokai.evals``): priced as usage kind ``eval`` against the application's
    eval budget, never against the caps of the application's users."""
    model_override: str | None = None
    """Evaluation only: the model that answers instead of the agent's chain (a ``models`` run that
    tries each allowed model). Still within the allow-lists, the residency and the grants."""
    channel: str = "browser"
    """How the person wrote: ``browser`` (a session token), or ``teams`` and ``slack``
    (``evokai.channels``), whose principal the channel builds for the mapped subject."""
    rewrite: dict[str, Any] | None = None
    """Rewrite the last question with ``text`` (``{messageId?}``): the last turn is replaced
    (``evokai.sessions.rewrite``)."""
    client_tools: bool = True
    """Offer custom client tools: only where the client receives ``client_tool`` events (the
    session stream; not AG-UI or the messaging channels)."""
    client_wait: bool = True
    """Offer the custom client tools that wait (not in a JSON turn: it cannot answer them)."""
    regenerate: bool = False
    """Regenerate the last answer: the last question (its stored or sent text) runs again as a
    rewrite (409 ``not_regenerable`` when it cannot)."""
    files: list[files_.Upload] = field(default_factory=list)
    """Attachments of the message (``evokai.attachments``), checked when the turn opens."""
    kept_files: list[dict[str, Any]] = field(default_factory=list)
    """A rewrite or a regeneration: the replaced question's attachment blocks, which the new
    question keeps."""


@dataclass
class TurnRun:
    """A turn being run: its session, agent, channel and outcome."""

    turn_id: str
    session: m.Session
    thread_id: str
    agent: AgentDef
    channel: SessionChannel
    handle: TurnHandle
    who: Who
    app: m.Application
    created: bool
    threads: ThreadStore
    grants: Grants = field(default_factory=Grants)
    """What the application may use (the agent is already restricted to it)."""
    storage: str = "evokai"
    """Where this conversation lives: ``evokai`` or ``external``."""
    rules: SpeechRules = field(default_factory=speech_rules)
    """Speech rules of the application's language."""
    markup: Markup | None = None
    """The inline markup of the answer (cues, speech tags), when the application or agent has it."""
    guard: TurnGuard | None = None
    """The security layer of this turn."""
    status: str = "done"
    reason: str = "end_turn"
    cost_micro_usd: int = 0
    timings: dict[str, Any] = field(default_factory=dict)
    citations: dict[str, int] = field(default_factory=dict)
    """Knowledge passages cited in this turn (chunk id to its number)."""
    trace: Trace | None = None
    """The turn's trace (spans of model calls, tools, guard decisions, speech)."""
    answer: str = ""
    """What the user was shown of the answer (for the trace, as its content allows)."""
    rewrite: Plan | None = None
    """What this turn replaces (a rewrite of the last question, Evokai storage)."""
    replaced_from: int | None = None
    """A rewrite with external storage: the index of the sent history the new messages replace
    from."""
    files: list[files_.Prepared] = field(default_factory=list)
    """The message's attachments, checked and prepared (``evokai.attachments``)."""
    dropped: bool = False
    """A rewrite deleted the question it replaces (Evokai storage)."""
    parts: PartLog = field(default_factory=PartLog)
    """What the person was shown and no stored message holds yet (``evokai.sessions.parts``)."""
    last_answer: tuple[ThreadMessage, str | None] | None = None
    """The turn's last stored assistant message and its id: what is shown after it goes there."""


class _Cancelled(Exception):
    pass


class TurnEngine:
    """Runs turns. One instance per process."""

    def __init__(
        self,
        *,
        store: Store,
        hub: ChannelHub,
        turns: TurnRegistry,
        agents: AgentRegistry,
        mcp: McpRegistry,
        catalog: ModelCatalog,
        settings: Settings,
        keyring: Any,
        now: Any = None,
        guard: Guard | None = None,
    ) -> None:
        """Wire the engine.

        Args:
            store: Repositories.
            hub: Session channels.
            turns: Running turns.
            agents: Agent registry.
            mcp: MCP registry.
            catalog: Models.
            settings: Deployment settings.
            keyring: To decrypt forwarded credentials.
            now: Clock (tests).
            guard: The security layer (one is built when not given).
        """
        self.keyring = keyring
        self.guard = guard or Guard(store, settings, keyring)
        self.store, self.hub, self.turns, self.agents, self.mcp, self.catalog, self.settings = (
            store,
            hub,
            turns,
            agents,
            mcp,
            catalog,
            settings,
        )
        self.now = now or utcnow
        self.knowledge: Any = None
        """Knowledge bases (``evokai.knowledge.service.KnowledgeService``), set by the services."""
        self.memory: Any = None
        """Long-term memory (``evokai.memory.service.MemoryService``), set by the services."""
        self.code: Any = None
        """Isolated code execution (``evokai.sandbox.tool.CodeService``), set by the services."""
        self.model_health = ModelHealth()
        """The last model calls of this process (the console's health page)."""
        self.compactor = Compactor(self)
        """Context compaction of conversations stored by Evokai (``evokai.engine.compaction``)."""
        self.client_calls = ClientCalls()
        """Custom client tool calls waiting for the client (``evokai.engine.client_calls``)."""
        self.run_listeners: list[Any] = []
        """Called when a server run ends (``evokai.workflows`` event triggers):
        ``(app, agent, run_id, status, output, subject)``."""

    # opening

    async def open(self, inp: TurnInput) -> TurnRun:
        """Resolve the token's conversation (creating it on first use) and register the turn.

        Args:
            inp: The input.

        Returns:
            The run to execute.

        Raises:
            Unauthorized: The application is gone.
            Conflict: The session is closed.
            NotFound: The conversation has expired.
            NotGranted: Browser sessions, the conversation's storage mode or (for a voice turn)
                its voice engine are no longer granted.
            Forbidden: A channel's turn for an agent that does not allow that channel.

        An abuse limit of the security policy refuses the turn with 429 (``GuardRefused``).
        """
        c = inp.principal.claims
        who = inp.principal.who
        app = await self.store.applications.get(c.application_id)
        if app is None:
            raise Unauthorized()
        grants = await grants_of(self.store, app)
        if not grants.channel(inp.channel):
            what = (
                "Browser sessions are"
                if inp.channel == "browser"
                else f"The {inp.channel} channel is"
            )
            raise NotGranted("channel", f"{what} not granted to this application.")
        if inp.mode == "voice" and not grants.voice(c.voice_engine):
            raise NotGranted("voice_engine", f"{c.voice_engine} speech is not granted.")
        session = await self.store.sessions.get(who, c.conversation_id)
        if session is None and await self.store.sessions.exists(c.conversation_id):
            raise NotFound("This conversation has expired.", code="expired_conversation")
        created = session is None
        storage = (
            (session.storage_mode if session else c.storage_mode) or app.storage_mode or "evokai"
        )
        # An eval case runs in a throw-away conversation that keeps nothing (external storage).
        throwaway = inp.evaluation and storage == "external"
        if not throwaway and not grants.storage(storage):
            raise NotGranted("storage_mode", f"The {storage} storage mode is not granted.")
        keeps_content = storage == "evokai"
        if session is None:
            agent = await self.agents.active(app.id, c.agent)
        else:
            if session.status == "closed":
                raise Conflict("This session is closed.", code="closed")
            # Every turn runs on the agent's active version: a conversation left open across a
            # release gets the new instructions, skills and settings (prompt cache included) at
            # its next turn instead of staying on the version it started with. Each trace records
            # the version it ran on. An agent no longer active keeps its last version.
            try:
                agent = await self.agents.active(app.id, session.agent_name)
            except NotFound:
                agent = await self.agents.pinned(app.id, session.agent_name, session.agent_version)
            if agent.version != session.agent_version:
                await self.store.sessions.set_agent(session.id, agent.name, agent.version)
                session.agent_version = agent.version
        if inp.channel != "browser" and inp.channel not in agent.channels:
            raise Forbidden(f"This agent is not reached over {inp.channel}.", code="channel")
        plan, replaced_from = await self._rewrite_check(inp, app, who, session, storage)
        attached = await self._attachments(inp, app, grants, agent, session, storage)
        turn_id = new_id()
        guard = self.guard.turn(
            app,
            agent,
            subject=c.subject,
            session_id=c.conversation_id,
            turn_id=turn_id,
            channel="browser",
            tags=c.tags,
        )
        guard.trace = self.new_trace(
            trace_id=turn_id,
            kind="eval" if inp.evaluation else "turn",
            app=app,
            agent=agent,
            subject=c.subject,
            session_id=c.conversation_id,
            mode=inp.mode,
            channel=inp.channel,
            storage=storage,
            guard=guard,
        )
        turns_so_far = 0 if session is None else await self.store.turns.count(session.id)
        await self.guard.admit(guard, turns_so_far=turns_so_far)
        first_text = (inp.text or "").strip()[:80]
        if guard.policy.enforced("pii.history"):
            first_text = guard.for_storage([], first_text)[1]
        if session is None:
            title = (first_text if keeps_content else "") or DEFAULT_TITLE
            session, thread = await self.store.sessions.create(
                who,
                agent.name,
                agent.version,
                title,
                c.tags,
                session_id=c.conversation_id,
                storage_mode=storage,
            )
            thread_id = thread.id
        else:
            thread_id = (await self.store.sessions.thread(session.id)).id
            if keeps_content and session.title == DEFAULT_TITLE and first_text:
                await self.store.sessions.set_title(session.id, first_text)
        channel = await self.hub.get(session.id, persist=keeps_content)
        handle = await self.turns.begin(turn_id, session.id, c.subject)
        await self.store.turns.start(who, session.id, inp.mode, turn_id)
        threads = self._threads(storage, who, session.id, thread_id, inp)
        await self.guard.started(guard)
        markup = markup_of((app.defaults or {}).get("markup"), agent.markup, session)
        guard.neutralise = markup.neutralise if markup is not None else None
        return TurnRun(
            rewrite=plan,
            replaced_from=replaced_from,
            files=attached,
            guard=guard,
            trace=guard.trace,
            rules=speech_rules(
                locale_of(app, session), identifiers.of_app(app), redact=speech_redaction(app)
            ),
            markup=markup,
            threads=threads,
            grants=grants,
            storage=storage,
            turn_id=turn_id,
            session=session,
            thread_id=thread_id,
            agent=grants.restrict(agent),
            channel=channel,
            handle=handle,
            who=who,
            app=app,
            created=created,
        )

    async def _rewrite_check(
        self,
        inp: TurnInput,
        app: m.Application,
        who: Who,
        session: m.Session | None,
        storage: str,
    ) -> tuple[Plan | None, int | None]:
        """Whether the last question can be rewritten, before the turn starts.

        With external storage the sent history is cut at the replaced question.

        Returns:
            The plan (Evokai storage) and the index the new messages replace from (external).

        Raises:
            Conflict: Nothing to rewrite, not the last question, the last turn changed data, or a
                turn is running (409, ``rewrite_*`` codes, with the application's message).
        """
        if inp.regenerate:
            inp.rewrite, inp.confirm = inp.rewrite or {}, None
        elif inp.rewrite is None or inp.confirm is not None or not (inp.text or "").strip():
            return None, None  # a decision is not a rewrite; an empty message is refused later
        rows: list[Any] = []
        index: int | None = None
        if storage == "external":
            sent = list(inp.history or [])
            dumped = [h.model_dump() for h in sent]
            index, code = rewrite.check(dumped, None)
            if index is not None and inp.regenerate:
                index, inp.text = self._regenerated(dumped, index, files_kept=False)
            if index is not None:
                inp.kept_files = _attached_blocks(dumped[index].get("content"))
                inp.history = sent[:index]
        elif session is None:
            code = rewrite.NOTHING
        elif not await self._idle(session.id):
            code = rewrite.RUNNING
        else:
            thread = await self.store.sessions.thread(session.id)
            rows = await self.store.messages.recent(who, thread.id, 400)
            index, code = rewrite.check(rows, inp.rewrite.get("messageId"))
            if index is not None and inp.regenerate:
                index, inp.text = self._regenerated(rows, index)
            if index is not None:
                inp.kept_files = _attached_blocks(rows[index].content)
        if code is not None or index is None:
            code = rewrite.NOT_REGENERABLE if inp.regenerate else code or rewrite.NOTHING
            raise Conflict(message(app, rewrite.MESSAGE_KEYS[code], session=session), code=code)
        if storage == "external":
            return None, index
        return rewrite.plan_of(rows, index), None

    async def _attachments(
        self,
        inp: TurnInput,
        app: m.Application,
        grants: Grants,
        agent: AgentDef,
        session: m.Session | None,
        storage: str,
    ) -> list[files_.Prepared]:
        """The message's attachments, checked before anything is kept (``evokai.attachments``).

        Returns:
            The prepared files.

        Raises:
            BadRequest: Attachments sent with a decision (``attachment_invalid``).
        """
        if not inp.files:
            return []
        if inp.confirm is not None:
            raise BadRequest("A decision carries no attachments.", code="attachment_invalid")
        restricted = grants.restrict(agent)
        chain = self._candidates(restricted, app, inp.principal.claims.model, grants)
        used = None
        if storage == "evokai":
            used = (
                0
                if session is None
                else await self.store.attachments.total(inp.principal.who, session.id)
            )
        # Stripping and reading a file takes a while: off the event loop, so other streams go on.
        return await asyncio.to_thread(
            files_.check,
            inp.files,
            granted=grants.attachments(),
            config=restricted.attachments,
            models=[self.catalog.get(n) for n in chain],
            carried=len(inp.kept_files),
            used_bytes=used,
        )

    def models_for(
        self, agent: AgentDef, app: m.Application, requested: str | None, grants: Grants
    ) -> list[Any]:
        """The model objects of the chain a turn of this agent would try.

        Returns:
            pydantic-ai models, in order.
        """
        chain = self._candidates(grants.restrict(agent), app, requested, grants)
        return [self.catalog.get(n) for n in chain]

    async def _file_blocks(
        self, run: TurnRun, inp: TurnInput, guard: TurnGuard
    ) -> list[dict[str, Any]]:
        """The question's attachment blocks: kept ones first, then the new ones.

        A document's text is scanned for hidden instructions (``untrusted.injection``); a PDF
        whose scan removed something goes as its cleaned text instead of the file.

        Returns:
            The blocks.
        """
        blocks = list(inp.kept_files)
        for p in run.files:
            meta = p.meta()
            if p.text is not None:
                cleaned = files_.clip(await guard.untrusted(p.text, f"attachment:{p.name}"))
                # A PDF goes as its text when the scan removed something, or when it holds
                # personal data the policy protects towards the model (the file cannot be masked).
                protects = guard.policy.enforced("pii.model") and guard.has_pii(cleaned)
                if not p.native or cleaned != p.text or protects:
                    p.native = False
                    blocks.append({"file": meta, "text": cleaned})
                    continue
            blocks.append({"file": meta})
        if blocks:
            self._trace(run, inp).root.set(
                attachments=[{**b["file"], "native": "text" not in b} for b in blocks]
            )
        return blocks

    async def _keep_files(self, run: TurnRun, inp: TurnInput, message_id: str | None) -> None:
        """Keep the new attachments with their message (Evokai storage only)."""
        if run.storage != "evokai":
            return
        await self.store.attachments.add(run.who, run.session.id, message_id, run.files)
        kept = [str(b["file"]["id"]) for b in inp.kept_files]
        await self.store.attachments.relink(run.who, run.session.id, kept, message_id)
        inp.kept_files = []

    async def _drop_unkept(self, run: TurnRun, inp: TurnInput) -> None:
        """Drop the attachments of a replaced question that no new question kept (never raises).

        A rewrite whose new question was never stored (refused, cancelled) deleted the question:
        its attachments go with it.
        """
        if run.storage != "evokai" or not run.dropped or not inp.kept_files:
            return
        ids = [str(b["file"]["id"]) for b in inp.kept_files]
        try:
            await self.store.attachments.delete(run.who, run.session.id, ids)
        except Exception as e:
            log.warning(
                "attachments_not_dropped", extra={"turnId": run.turn_id, "error": error_class(e)}
            )

    async def _file_bytes(self, run: TurnRun, stored: list[Any], models: list[Any]) -> Any:
        """The files of the history the models read as they are, by id (``history.file_part``).

        Returns:
            id to ``BinaryContent``.
        """
        if not run.grants.attachments():
            return {}  # revoked: earlier files are no longer sent (their text stays in the history)
        images, pdf = files_.readable(models)
        wanted = files_.native_files(stored, images=images, pdf=pdf)
        data = {p.id: p.data for p in run.files}
        missing = [str(f["id"]) for f in wanted if str(f["id"]) not in data]
        if missing and run.storage == "evokai":
            data.update(await self.store.attachments.data_of(run.who, run.session.id, missing))
        return files_.binaries(wanted, data)

    @staticmethod
    def _regenerated(
        messages: list[Any], index: int, *, files_kept: bool = True
    ) -> tuple[int | None, str | None]:
        """The question a regeneration runs again, when its answer may be regenerated.

        Returns:
            Its index and text, or ``(None, None)``.
        """
        if rewrite.regenerable_index(messages, files_kept=files_kept) != index:
            return None, None
        text = rewrite.question_text(messages[index])
        return (index, text) if text else (None, None)

    async def _idle(self, session_id: str, wait: float = 2.0) -> bool:
        """Whether no turn of the conversation runs.

        A turn that is still running is waited for briefly (``wait``). One that already sent
        ``done`` is always waited for until it has persisted its last events (up to
        ``ANSWERED_WAIT``): a rewrite deletes the replaced turn's events, and must not run while
        that turn is still writing them.

        Returns:
            True when idle.
        """
        running = self.turns.running(session_id)
        if running is None:
            return True
        timeout = max(wait, ANSWERED_WAIT) if running.answered else wait
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(running.finished.wait(), timeout=timeout)
        return self.turns.running(session_id) is None

    async def _rewritable(self, run: TurnRun) -> tuple[bool, bool]:
        """Whether the last question can be rewritten, and the last answer regenerated.

        For the ``done`` event.

        Returns:
            Both answers.
        """
        external = isinstance(run.threads, ExternalThreads)
        try:
            if isinstance(run.threads, ExternalThreads):
                msgs: list[Any] = [*run.threads.history, *run.threads.added]
            else:
                msgs = await self.store.messages.recent(run.who, run.thread_id, 100)
        except Exception as e:
            log.warning(
                "rewritable_unknown", extra={"turnId": run.turn_id, "error": error_class(e)}
            )
            return False, False
        return (
            rewrite.rewritable_index(msgs) is not None,
            rewrite.regenerable_index(msgs, files_kept=not external) is not None,
        )

    def new_trace(
        self,
        *,
        trace_id: str,
        kind: str,
        app: m.Application,
        agent: AgentDef,
        subject: str | None,
        session_id: str | None,
        mode: str,
        channel: str,
        storage: str,
        guard: TurnGuard,
    ) -> Trace:
        """The trace of a turn or a server run, with the content the application allows.

        Returns:
            The trace (its root span is open).
        """
        p = guard.policy
        return Trace(
            trace_id=trace_id,
            kind=kind,
            application_id=app.id,
            agent=agent.name,
            version=agent.version,
            subject=subject or "",
            subject_key=subject_key(self.keyring.signing_key, app.id, subject or "")[:16],
            session_id=session_id,
            mode=mode,
            channel=channel,
            content=content_mode(
                app,
                storage=storage,
                pii_masked=p.enforced("pii.history") or p.enforced("pii.model"),
            ),
            known_secrets=self.guard.known_secrets,
        )

    async def store_trace(self, row: dict[str, Any] | None) -> None:
        """Keep a finished trace (never raises: a trace must not fail a turn)."""
        if row is None:
            return
        try:
            await self.store.traces.add(row)
        except Exception as e:
            log.warning(
                "trace_not_stored", extra={"traceId": row.get("id"), "error": error_class(e)}
            )

    def expired_callback(
        self, application_id: str, subject: str
    ) -> Callable[[str], Awaitable[None]]:
        """What a run calls when a service refused the person's own token.

        Returns:
            A callback marking the person's connection to that provider for reconnecting.
        """

        async def expired(provider: str) -> None:
            service = getattr(self.mcp, "connections", None)
            if service is not None:
                await service.expired(application_id, subject, provider)

        return expired

    def _trace(self, run: TurnRun, inp: TurnInput) -> Trace:
        """The run's trace (built for runs opened without one, e.g. by tests).

        Returns:
            The trace.
        """
        if run.trace is None:
            guard = self._guard(run)
            run.trace = guard.trace = self.new_trace(
                trace_id=run.turn_id,
                kind="eval" if inp.evaluation else "turn",
                app=run.app,
                agent=run.agent,
                subject=run.who.subject,
                session_id=run.session.id,
                mode=inp.mode,
                channel="browser",
                storage=run.storage,
                guard=guard,
            )
        return run.trace

    def _finish_trace(self, run: TurnRun, inp: TurnInput) -> dict[str, Any] | None:
        """Close the turn's trace and export it (never raises).

        Returns:
            The row to store, or None when it could not be built.
        """
        try:
            tr = self._trace(run, inp)
            t = run.timings
            tr.root.set(
                mode=inp.mode,
                voiceEngine=inp.principal.claims.voice_engine if inp.mode == "voice" else None,
                firstTokenMs=t.get("firstTokenMs"),
                firstSayMs=t.get("firstSayMs"),
                model=t.get("model"),
                fallback=t.get("fallback"),
                storage=run.storage,
            )
            if inp.confirm is not None:
                tr.root.set(confirm="approved" if inp.confirm.get("approve") else "rejected")
            tr.put(tr.root, input=inp.text, output=run.answer or None)
            tr.cost_micro_usd = run.cost_micro_usd
            return tr.finish(status=run.status, reason=run.reason)
        except Exception as e:
            log.warning("trace_failed", extra={"turnId": run.turn_id, "error": error_class(e)})
            return None

    # ── running ───────────────────────────────────────────────────────────────────────────────────
    # ────────────

    async def execute(self, run: TurnRun, inp: TurnInput) -> None:
        """Run the turn to its end. Always publishes `done`, flushes events and records the turn.

        Args:
            run: The opened run.
            inp: The input.
        """
        t0 = time.perf_counter()
        run.timings["startedAt"] = self.now().isoformat()
        if inp.end_of_speech_at:
            run.timings["endOfSpeechAt"] = inp.end_of_speech_at
        voice = inp.mode == "voice"
        splitter = SentenceSplitter(
            run.agent.voice.first_clause_min_chars if voice else 0, run.rules, markup=run.markup
        )
        said = {"n": 0, "dropped": 0}
        carried: list[dict[str, Any]] = []
        carried_raw: list[str] = []
        trace = self._trace(run, inp)
        voice_span: dict[str, Span | None] = {"span": None}

        def emit(event: dict[str, Any]) -> dict[str, Any]:
            run.parts.add(event)
            return run.channel.publish(event, run.turn_id)

        def say(text: str, *, filler: bool = False, last: bool = False) -> None:
            if not voice or not (text or last):
                return
            shown, spoken = text, None
            extra: dict[str, Any] = {}
            # With markup the segments come as written (Markdown, markup, identifiers).
            raw = " ".join([*carried_raw, text]) if run.markup is not None else None
            if run.markup is not None:
                # Cues become marks; a segment of cues alone hands them to the next one.
                f = run.markup.forms(text, run.rules)
                said["dropped"] += f.dropped
                marks = [{**x, "at": 0, **({"spokenAt": 0} if "spokenAt" in x else {})}
                         for x in carried] + f.marks  # fmt: skip
                carried.clear()
                if not f.text and not last:
                    carried.extend(marks)
                    carried_raw[:] = [raw] if raw else []
                    return
                carried_raw.clear()
                shown, spoken = f.text, f.spoken
                extra = {"marks": marks} if marks else {}
                if f.tagged:
                    extra["tagged"] = f.tagged
            if said["n"] == 0:
                run.timings["firstSayMs"] = round((time.perf_counter() - t0) * 1000)
                voice_span["span"] = trace.event(
                    "voice",
                    "speech",
                    ms=run.timings["firstSayMs"],
                    engine=inp.principal.claims.voice_engine,
                    firstSayMs=run.timings["firstSayMs"],
                )
            # What is shown keeps its digits; the speech engine gets the numbers in words.
            if spoken is None:
                spoken = spell_numbers(shown, run.rules.locale)
            # The segment as the model wrote it (Markdown, markup, identifiers), for a client that
            # shows the answer as written and speaks the segments.
            if raw is not None and raw.strip() and raw.strip() != shown:
                extra["raw"] = raw.strip()
            emit({
                "type": "say",
                "index": said["n"],
                "text": shown,
                **({"spoken": spoken} if spoken != shown else {}),
                **extra,
                **({"filler": True} if filler else {}),
            })
            said["n"] += 1

        guard = self._guard(run)
        if guard.policy.enforced("pii.history"):
            run.channel.persist_filter = guard.persisted
        emit({"type": "conversation", "id": run.session.id, "turnStarted": True})
        try:
            await self._run(run, inp, emit, say, splitter, t0)
            if carried:
                say("", last=True)  # the answer ended on cues
        except _Cancelled:
            run.status, run.reason = "interrupted", run.handle.reason or "cancelled"
        except Exception as e:
            log.exception("turn_failed", extra={"turnId": run.turn_id, "error": error_class(e)})
            run.status, run.reason = "failed", "internal"
            emit({
                "type": "error",
                "code": "internal",
                "message": message(run.app, "turn.failed", session=run.session),
            })
        finally:
            await self._drop_unkept(run, inp)
            try:
                await self._keep_shown(run)
            except Exception as e:
                log.warning(
                    "shown_not_kept", extra={"turnId": run.turn_id, "error": error_class(e)}
                )
            # The turn is over: its capability workers' call tokens stop working.
            await self.mcp.end_run(run.turn_id)
            run.timings["totalMs"] = round((time.perf_counter() - t0) * 1000)
            if run.cost_micro_usd:
                run.timings["costMicroUsd"] = run.cost_micro_usd  # server-side record only
            done: dict[str, Any] = {
                "type": "done",
                "conversationId": run.session.id,
                "reason": run.reason,
            }
            if isinstance(run.threads, ExternalThreads):
                # External storage: the application appends these to its copy of the conversation
                # (after dropping, on a rewrite, what it sent from ``replacedFrom`` on).
                done["messages"] = [x.as_json() for x in run.threads.added]
                if run.replaced_from is not None:
                    done["replacedFrom"] = run.replaced_from
            done["rewritable"], done["regenerable"] = await self._rewritable(run)
            # Security events are stored before the end is announced: whoever reads them next
            # (the console, a test) finds them.
            await guard.flush()
            if voice_span["span"] is not None:
                voice_span["span"].set(segments=said["n"])
                if said["dropped"]:
                    voice_span["span"].set(markupDropped=said["dropped"])
            # The trace too, so the console can open it as soon as the turn is over.
            await self.store_trace(self._finish_trace(run, inp))
            # From here the turn has answered: its messages are stored, what is left is persisting
            # its last events and its row. Readers of the conversation treat it as over.
            run.handle.answered = True
            emit(done)
            try:
                await run.channel.flush()
                await self.store.turns.finish(run.turn_id, run.status, run.reason, run.timings)
            finally:
                run.channel.persist_filter = None
                await self.guard.finished(guard)
                self.turns.end(run.handle)
                telemetry.record(
                    "turn_latency", run.timings["totalMs"], mode=inp.mode, status=run.status
                )
                log.info(
                    "turn",
                    extra={
                        "turnId": run.turn_id,
                        "conversationId": run.session.id,
                        "subject": run.who.subject,
                        "mode": inp.mode,
                        "voiceEngine": inp.principal.claims.voice_engine,
                        "status": run.status,
                        "reason": run.reason,
                        **run.timings,
                    },
                )

    async def _race(self, run: TurnRun, aw: Any) -> Any:
        """Await `aw` unless the turn is cancelled first.

        Returns:
            What `aw` returned.

        Raises:
            _Cancelled: The turn was cancelled; `aw` has been cancelled too.
        """
        task = asyncio.ensure_future(aw)
        stop = asyncio.ensure_future(run.handle.cancelled.wait())
        try:
            done, _ = await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
        if task in done:
            return task.result()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise _Cancelled()

    async def _run(
        self,
        run: TurnRun,
        inp: TurnInput,
        emit: Any,
        say: Any,
        splitter: SentenceSplitter,
        t0: float,
    ) -> None:
        store, who, agent = self.store, run.who, run.agent
        settings = self.settings
        claims = inp.principal.claims
        guard = self._guard(run)
        credential = await self._credential(claims)

        # 0. A rewrite of the last question: what it replaces goes, and clients are told.
        if run.rewrite is not None or run.replaced_from is not None:
            await self._replace(run, emit)

        # 1. The user's message: a confirmation, or text (which supersedes a confirmation left
        # open).
        pending = None if run.created else await store.pending.open(who, run.session.id)
        content: list[dict[str, Any]] = []
        user_text: str | None = None
        resume: dict[str, Any] | None = None
        """A confirmation being carried out: the pending state, the decision and the wait."""
        recent = [] if run.created else await run.threads.recent(6)
        shown = [{"role": x.role, "text": x.text, "events": x.events} for x in recent]
        if inp.mode == "voice" and inp.confirm is None and inp.text:
            inp = self._spoken(inp, pending, shown, run.rules)

        if inp.confirm is not None:
            approve = bool(inp.confirm.get("approve"))
            if not pending or pending.id != inp.confirm.get("actionId"):
                emit({
                    "type": "error",
                    "code": "expired",
                    "message": message(run.app, "turn.confirmExpired", session=run.session),
                })
                run.reason = "expired"
                return
            state = pending.state
            created = pending.created_at
            waited = round(
                (
                    self.now() - (created if created.tzinfo else created.replace(tzinfo=UTC))
                ).total_seconds()
                * 1000
            )
            if not approve:
                tr = self._trace(run, inp)
                for c in state["calls"]:
                    with tr.span(
                        "tool",
                        f"execute_tool {c['name']}",
                        tool=c["name"],
                        callId=c["toolUseId"],
                        confirmed=False,
                        waitedMs=max(0, waited),
                    ) as ts:
                        tr.put(ts, args=c["input"])
                        ts.set(outcome="rejected")
            if not approve and not state["ready"]:
                # A refusal needs no model call: the answer is fixed (a small model once reported a
                # cancelled change as done).
                content += [
                    _result(c["toolUseId"], c["name"], False, REJECTED_TEXT) for c in state["calls"]
                ]
                await store.pending.close(who, pending.id, "rejected")
                await self._add(
                    run,
                    role="user",
                    content=content,
                    events=[{"type": "confirmed", "approve": approve, "writes": False}],
                )
                refusal = message(run.app, "turn.refusal", session=run.session)
                emit({"type": "text", "delta": refusal})
                say(refusal)
                await self._add(run, role="assistant", content=[{"text": refusal}], text=refusal)
                return
            await store.pending.close(who, pending.id, "approved" if approve else "rejected")
            resume = {"state": state, "approve": approve, "waitedMs": max(0, waited)}
        else:
            text = (inp.text or "").strip()
            if not text:
                emit({
                    "type": "error",
                    "code": "empty",
                    "message": message(run.app, "turn.empty", session=run.session),
                })
                run.reason = "empty"
                return
            tr = self._trace(run, inp)
            with tr.span("guard", "guard input", control="input") as gs:
                decision = await guard.check_input(text)
                # A file's name is the person's input too: the same checks, before the model.
                for p in run.files:
                    if decision.blocked:
                        break
                    named = await guard.check_input(p.name)
                    if named.blocked:
                        decision = named
                gs.set(decision="blocked" if decision.blocked else "allowed")
            if decision.blocked:
                if decision.refusal:
                    emit({"type": "text", "delta": decision.refusal})
                    say(speakable(decision.refusal, run.rules))
                else:
                    refused = message(run.app, "turn.guard", session=run.session)
                    emit({"type": "error", "code": "guard", "message": refused})
                run.status, run.reason = "blocked", "guard"
                return
            text = user_text = decision.text
            if run.markup is not None:
                # A person cannot write cues for the agent: their markup reaches it inert.
                text = user_text = run.markup.neutralise(text)
            if pending:
                content += [
                    _result(
                        r["toolUseId"], r["name"], r["ok"], r["text"], wrote=bool(r.get("wrote"))
                    )
                    for r in pending.state["ready"]
                ]
                content += [
                    _result(c["toolUseId"], c["name"], False, SUPERSEDED_TEXT)
                    for c in pending.state["calls"]
                ]
                await store.pending.close(who, pending.id, "superseded")
            question = open_question(shown)
            content += await self._file_blocks(run, inp, guard)
            content.append({
                "text": clip(
                    f'Answer to your question "{question}": {text}' if question else text, 8_000
                )
            })
            message_id = await self._add(run, role="user", content=content, text=text)
            await self._keep_files(run, inp, message_id)

        # 2. Tools: the app's (as the user) and our own. If an app server does not answer, the agent
        # goes on without it.
        toolbox = self._guarded_toolbox(
            guard,
            await self.mcp.toolbox(
                run.app.id,
                agent,
                credential,
                run.grants,
                subject=who.subject,
                conversation=run.session.id,
                tags=claims.tags,
                run_id=run.turn_id,
                worker_calls=settings.worker_model_calls,
            ),
        )
        skills = await self._skills(run.app.id, agent, run.grants)
        preloaded, on_demand = split_skills(agent, skills)
        instructions = await self._instructions(run.app.id, agent)
        guard.instructions = instructions.text if instructions else agent.instructions
        modules = await resolve_modules(
            self.store, run.app.id, agent, run.session.prompt_options, session=run.session
        )
        modules = protected(modules, guard.protect_text)
        guard.add_prompt_text(*(m.text for m in modules))  # the leak check covers them too
        if modules and guard.trace is not None:
            # Which modules at which versions; never their parameters.
            guard.trace.root.set(**{"prompt.modules": [f"{x.name}@{x.version}" for x in modules]})
        # Memory: only with Evokai storage, the application's mode and the person's consent.
        memory_on = False
        if run.storage == "evokai" and self.memory is not None:
            memory_on = (await self.memory.state(run.app, who)).active
        kbs = await self._knowledge_bases(run)
        targets = await self._handoff_targets(run) if agent.handoff_to else {}
        rs = RunState(
            guard=guard,
            trace=self._trace(run, inp),
            settings=settings,
            run_id=run.turn_id,
            emit=emit,
            say=lambda sentence: say(speakable(sentence, run.rules)),
            label=lambda name, args: confirm_label(agent, name, args, run.app),
            health=self.model_health,
            operation="stream",
            max_tokens=output_cap(run.app, settings.max_output_tokens, agent),
            op_tags={"turnId": run.turn_id},
            prompt_cache=agent.prompt_cache,
            locale=locale_of(run.app, run.session),
            expired=self.expired_callback(run.app.id, run.who.subject),
        )
        if resume is not None:
            rs.confirm = {"approve": resume["approve"], "waitedMs": resume["waitedMs"]}
        tools = self._turn_tools(
            run, inp, rs, toolbox, skills, kbs, targets, memory_on, on_demand, preloaded, say
        )
        rs.tools = {t.name: t for t in tools}
        names = set(rs.tools)
        if agent.show_tool_args:
            # The calls that show a card show their arguments as they are written.
            rs.extra["argsOf"] = frozenset(t.name for t in tools if t.kind in CARD_KINDS)
        kbs = kbs if KNOWLEDGE_TOOL in names else []
        code_on = CODE_TOOL in names
        page = await self._guarded_page(guard, inp.page)
        notes = guard.system_notes()
        if kbs:
            notes = f"{notes}\n\n{CITE_NOTE}" if notes else CITE_NOTE
        if code_on:
            notes = f"{notes}\n\n{CODE_NOTE}" if notes else CODE_NOTE

        candidates = self._candidates(agent, run.app, claims.model, run.grants)
        if inp.evaluation and inp.model_override:
            override = inp.model_override
            ok = self._allowed(run.app, override) and run.grants.chain([override])
            candidates = [override] if ok else []
        if not candidates:
            log.warning("no_granted_model", extra={"turnId": run.turn_id, "agent": agent.name})
            emit({
                "type": "error",
                "code": "model",
                "message": message(run.app, "turn.modelUnavailable", session=run.session),
            })
            run.status, run.reason = "failed", "model"
            return
        rs.models = [(n, self.catalog.get(n)) for n in candidates]

        # Automatic retrieval: the knowledge bases are searched with the message before the model
        # answers, as if the agent had called knowledge_search itself.
        if kbs and user_text and agent.knowledge is not None and agent.knowledge.auto:
            await self._auto_knowledge(run, inp, kbs, user_text, guard, emit)

        # Recall: preferences and the facts and episodes relevant to this message, once per turn,
        # spotlighted as untrusted data (they were written from conversations).
        memory_text = await self._recall(run, guard, user_text, shown) if memory_on else None

        prompt = system_prompt(
            agent,
            now=self.now(),
            app_name=run.app.name,
            page=page,
            memories=[],
            memory_text=memory_text,
            skills=on_demand,
            page_text_chars=settings.page_text_chars,
            voice=inp.mode == "voice",
            instructions=instructions,
            timezone=str((run.app.defaults or {}).get("timezone") or "UTC"),
            preloaded=preloaded,
            scripts_runnable=SKILL_RUN in names,
            modules=modules,
            locale=session_locale(run.app, run.session),
        )
        if notes:
            prompt = replace(prompt, stable=f"{prompt.stable}\n\n{notes}")
        system = system_text(agent, prompt)
        volatile = volatile_instructions(agent, prompt)

        # 3. Model and tools: pydantic-ai's Agent runs the loop, Evokai's capability and toolset
        # apply caps, guard, confirmations, traces and usage.
        files: dict[str, Any] = {}
        """Attachments the models read as they are, by id (filled once the history is known)."""

        def view(messages: list[Any]) -> list[Any]:
            fold = rs.extra.get("fold")
            if fold is not None:
                # Compacted after a too-long error: the summary and what is stored after it, then
                # what this turn added since.
                done, base = fold
                stored = base + to_stored(messages[done:], rs.args, rs.ok, rs.renamed)
            else:
                stored = to_stored(messages, rs.args, rs.ok, rs.renamed)
            history = window(stored, settings.history_messages, settings.history_chars)
            model_history = guard.for_model(history)
            if not names:
                # An agent without tools (after a handoff, say) gets earlier tool calls as text:
                # providers refuse tool calls in a history sent without tools.
                model_history = flatten_tools(model_history, set())
            rs.extra["traceInput"] = _new_messages(model_history)
            rebuilt = to_model_messages(system, model_history, files)
            # The reasoning of this turn's steps that called tools goes back with their calls
            # (the providers require it); no other reasoning is ever sent again.
            return keep_reasoning(messages, rebuilt) if names else rebuilt

        rs.view = view
        rs.before_call = lambda: self._before_call(run, inp, rs, candidates, emit, say)
        rs.on_response = lambda name, response, latency: self._on_response(
            run, inp, rs, name, response, latency, emit
        )
        scope = self._scope(run, inp, credential)
        capabilities = await self.capabilities_for(agent, rs, scope)
        waits = self._client_wait(run, rs)
        if waits is not None:
            capabilities.append(waits)
        compaction = next((c for c in capabilities if isinstance(c, EvokaiCompaction)), None)
        plan = (
            self.compactor.plan(run, candidates, compaction.policy(settings))
            if compaction is not None and resume is None
            else None
        )
        if plan is not None:
            stored = await self._compacted(run, inp, rs, plan, system, tools)
        else:
            stored = [Stored(role=x.role, content=x.content) for x in await self._history(run)]
        files.update(await self._file_bytes(run, stored, [x for _, x in rs.models]))
        history = to_model_messages(system, stored if resume else repair(stored))
        results = None
        if resume is not None:
            # The calls that waited go back with the reasoning that wrote them, if it was kept.
            history = resume_reasoning(history, stored)
            for r in resume["state"]["ready"]:
                rs.ok[r["toolUseId"]] = r["ok"]
            for c in resume["state"]["calls"]:
                rs.args[c["toolUseId"]] = c["input"]
            if resume["state"]["ready"]:
                # What ran before the confirmation: already answered, pydantic-ai skips them.
                history.append(
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name=r["name"],
                                content=r["text"] or ("ok" if r["ok"] else "errore"),
                                tool_call_id=r["toolUseId"],
                            )
                            for r in resume["state"]["ready"]
                        ]
                    )
                )
            results = DeferredToolResults(
                approvals={
                    c["toolUseId"]: True if resume["approve"] else ToolDenied(REJECTED_TEXT)
                    for c in resume["state"]["calls"]
                }
            )
        model = build_agent(
            rs,
            toolset=GuardedToolset(EvokaiToolset(tools)),
            instructions=volatile[0].content if volatile else None,
            capabilities=capabilities,
        )
        await self._drive(
            run,
            rs,
            model,
            history,
            results,
            resume,
            emit,
            say,
            splitter,
            t0,
            max_calls=guard.max_calls(agent.max_tool_calls_per_turn),
            model_settings=request_settings(
                agent_settings(agent), output_cap(run.app, settings.max_output_tokens, agent)
            ),
        )

    async def _replace(self, run: TurnRun, emit: Any) -> None:
        """Replace the last question.

        What it replaces is deleted (Evokai storage) and the event stream says so (``rewritten``),
        persisted at once so a replay finds it in place.
        """
        event: dict[str, Any] = {"type": "rewritten", "replaced": []}
        plan = run.rewrite
        if plan is not None:
            await rewrite.drop(self.store, run.who, run.session.id, run.thread_id, plan)
            run.dropped = True
            event.update(replaced=plan.turn_ids, messageId=plan.message_id)
        if run.replaced_from is not None:
            event["replacedFrom"] = run.replaced_from
        if run.trace is not None:
            run.trace.root.set(rewrite=True, replacedTurns=len(event["replaced"]) or None)
        emit(event)
        await run.channel.flush()

    def _scope(self, run: TurnRun, inp: TurnInput, credential: str | None = None) -> Scope:
        """Who the turn is for, as capabilities that make their own calls need it.

        Returns:
            The scope.
        """
        claims = inp.principal.claims

        def add(cost: int) -> None:
            run.cost_micro_usd += cost

        return Scope(
            app=run.app,
            grants=run.grants,
            who=run.who,
            tags=claims.tags,
            run_id=run.turn_id,
            session_id=run.session.id,
            credential=credential,
            groups=list(claims.groups),
            evaluation=inp.evaluation,
            on_cost=add,
            locale=session_locale(run.app, run.session),
            storage=run.storage,
        )

    async def _compacted(
        self,
        run: TurnRun,
        inp: TurnInput,
        rs: RunState,
        plan: Any,
        system: str,
        tools: list[ToolSpec],
    ) -> list[Stored]:
        """The conversation from its summary, compacted first when it no longer fits.

        Also arms the one recovery from a too-long error (``RunState.compact``).

        Returns:
            The stored history the turn starts from.
        """
        claims = inp.principal.claims
        plan.fixed_tokens = estimate_tokens(
            len(system) + sum(len(json.dumps(t.schema)) + len(t.description) for t in tools)
        )
        state = await self.compactor.load(run)

        async def fold(trigger: str) -> Any:
            out = await self.compactor.compact(
                run,
                plan,
                state,
                guard=rs.guard,
                tags=claims.tags,
                limits=claims.limits,
                trigger=trigger,
                trace=rs.trace,
                evaluation=inp.evaluation,
            )
            if out is not None:
                run.cost_micro_usd += out.cost_micro_usd
            return out

        if over(plan, state):
            out = await fold("threshold")
            if out is not None:
                state = out.state

        async def recover(messages: list[Any]) -> bool:
            nonlocal state
            state = await self.compactor.load(run)
            out = await fold("overflow")
            if out is None:
                return False
            state = out.state
            rs.extra["fold"] = (len(messages), self.compactor.history(plan, state))
            return True

        rs.compact = recover
        return self.compactor.history(plan, state)

    async def _history(self, run: TurnRun) -> list[ThreadMessage]:
        """The stored conversation the turn starts from (the window is applied per request).

        Returns:
            The latest messages.
        """
        return await run.threads.recent(self.settings.history_messages * 2)

    async def capabilities_for(self, agent: AgentDef, rs: RunState, scope: Scope) -> list[Any]:
        """The capabilities a turn or run adds to the agent, after Evokai's own.

        A capability is added when the deployment offers it, the application is granted it and the
        agent turns it on; one that cannot be built (not installed, missing credentials, a model
        the application may not use, host access) is left out with a warning, and the turn goes
        on without it.

        Returns:
            Capabilities, in the catalog's order.
        """
        from evokai import capabilities as caps
        from evokai.capabilities.catalog import ENTRIES, HOST_WORKSPACE

        builtin = builtin_tools.capabilities(
            agent.builtin_tools,
            scope.grants,
            protection(scope.app),
            _egress_allow(rs.guard),
            gate=rs.guard.provider_tool_refusal,
            mcp_allow=_egress_allow(rs.guard, strict=True),
        )
        if builtin:
            rs.trace.root.set(builtinTools=[d.kind for d in agent.builtin_tools])
        uses = {
            name: use
            for name, use in agent.capabilities.items()
            if use.enabled
            and caps.offered(name, self.settings.capabilities)
            and scope.grants.capability(name)
        }
        if not uses:
            return builtin
        enabled = set(uses)

        async def record(name: str, response: ModelResponse) -> None:
            cost = await self.record_call(
                scope,
                agent_name=agent.name,
                kind="eval" if scope.evaluation else CAP_KIND,
                model=name,
                response=response,
                latency_ms=0,
            )
            rs.trace.cost_micro_usd += cost
            if scope.on_cost is not None:
                scope.on_cost(cost)

        def model_of(name: str) -> Any:
            # A capability's own model: one the application may use, its calls recorded as usage
            # and free of provider-side state.
            if name in ("", "test", "agent"):
                name = rs.models[0][0]
            elif not (self._allowed(scope.app, name) and scope.grants.chain([name])):
                raise caps.CapabilityError(f"model {name} is not allowed to this application")
            return RecordedModel(self.catalog.get(name), name, record)

        factories: dict[str, Any] = {
            "code_mode": lambda c: EvokaiCodeMode(
                timeout_s=min(
                    float(c.get("timeout_seconds") or self.settings.code_mode_timeout_seconds),
                    self.settings.code_mode_timeout_seconds,
                ),
                memory_mb=min(
                    int(c.get("memory_mb") or self.settings.code_mode_memory_mb),
                    self.settings.code_mode_memory_mb,
                ),
                tool_calls=min(
                    int(c.get("tool_calls") or self.settings.code_mode_tool_calls),
                    self.settings.code_mode_tool_calls,
                ),
            ),
            "compaction": lambda c: EvokaiCompaction(
                keep_tokens=c.get("keep_tokens"), buffer=c.get("buffer"), model=c.get("model")
            ),
        }
        # Evokai's builders of the harness capabilities that need its objects (an answerer, guard
        # functions, stores, the application's credentials).
        from evokai.capabilities import harness

        factories = {
            **harness.factories(await harness.context(self, agent, rs, scope, set(uses))),
            **factories,
        }
        out: list[Any] = []
        for entry in ENTRIES:
            use = uses.get(entry.id)
            if use is None:
                continue
            try:
                if entry.id == "subagents":
                    cap = await self._subagents(agent, rs, scope)
                    if cap is None:
                        continue
                else:
                    cap = caps.build(
                        entry.id,
                        use.config,
                        model_of=model_of,
                        factories=factories,
                        enabled=enabled,
                    )
            except Exception as e:
                log.warning(
                    "capability_unavailable",
                    extra={
                        "turnId": rs.run_id,
                        "agent": agent.name,
                        "capability": entry.id,
                        "error": clip(str(e), 300),
                    },
                )
                continue
            out.append(cap)
            rs.trace.root.set(capabilities=[*rs.trace.root.attrs.get("capabilities", []), entry.id])
        if any(caps.get(n).host == HOST_WORKSPACE for n in enabled):
            out.append(NoLocalWorkspace())
        if any(caps.get(n).group != "evokai" for n in enabled):
            # The tools capabilities bring get Evokai's tool rules, and what they do is traced.
            out += [harness.CapabilityTools(), harness.CapabilityEvents()]
        return out + builtin

    async def _subagents(
        self, agent: AgentDef, rs: RunState, scope: Scope
    ) -> EvokaiSubAgents | None:
        """The ``subagents`` capability of a turn or run: the agents it may call, as children.

        Each must be an active agent of the application, other than the caller, with a granted
        model. A child never gets subagents of its own (depth 1).

        Returns:
            The capability, or None when no listed agent can be called.
        """
        children: list[tuple[str, str, Any, Any]] = []
        for name in agent.subagents:
            if name == agent.name:
                continue
            try:
                target = scope.grants.restrict(await self.agents.active(scope.app.id, name))
            except NotFound:
                continue
            candidates = self._candidates(target, scope.app, None, scope.grants)
            if not candidates:
                continue
            template = build_agent(
                RunState(
                    guard=rs.guard,
                    trace=rs.trace,
                    settings=self.settings,
                    run_id=rs.run_id,
                    models=[(candidates[0], self.catalog.get(candidates[0]))],
                ),
                toolset=None,
            )
            children.append((
                name,
                clip(target.title or target.name, 200),
                self._child_factory(target, candidates, scope),
                template,
            ))
        if not children:
            return None
        return EvokaiSubAgents(
            children,
            calls=self.settings.subagent_calls,
            timeout_seconds=self.settings.subagent_timeout_seconds,
        )

    def _child_factory(self, target: AgentDef, candidates: list[str], scope: Scope) -> Any:
        """Builds a subagent for one call, as the same person, with read-only tools.

        Returns:
            The factory (``evokai.engine.subagents.ChildFactory``).
        """
        from evokai.sessions.runs import AgentRunner, RunResult

        async def make(parent: RunState) -> tuple[Agent[RunState, Any], RunState]:
            guard = parent.guard
            toolbox = await self.mcp.toolbox(
                scope.app.id,
                target,
                scope.credential,
                scope.grants,
                subject=scope.who.subject,
                conversation=scope.session_id or scope.run_id,
                tags=scope.tags,
                run_id=scope.run_id,
                worker_calls=self.settings.worker_model_calls,
            )
            # Nobody can confirm inside a delegation: a tool an ask rule may make wait for the
            # person (the caller's rules or the child's own) is not read-only for the child.
            own = guard.ask.with_agent(target)

            def asks(name: str) -> bool:
                return guard.askable(name) or own.askable(name)

            read_only = [
                t
                for t in toolbox.tools
                if not t.mutating
                and guard.offered(t.name)
                and not guard.forced_confirmation(t.name)
                and not asks(t.name)
            ]
            skills = await self._skills(scope.app.id, target, scope.grants)
            preloaded, on_demand = split_skills(target, skills)
            kbs = []
            k = target.knowledge
            if self.knowledge is not None and k and k.bases and scope.grants.knowledge():
                rows = {b.name: b for b in await self.store.knowledge.bases(scope.app.id)}
                kbs = [rows[n] for n in k.bases if n in rows and guard.offered(KNOWLEDGE_TOOL)]
            child = RunState(
                guard=guard,
                trace=parent.trace,
                settings=self.settings,
                run_id=parent.run_id,
                models=[(n, self.catalog.get(n)) for n in candidates],
                prompt_cache=target.prompt_cache,
                before_call=parent.before_call,
                health=self.model_health,
                operation=parent.operation,
                max_tokens=output_cap(scope.app, self.settings.max_output_tokens, target),
                op_tags={**parent.op_tags, "subagent": target.name},
            )
            result = RunResult(run_id=parent.run_id, output=None, text="", model=candidates[0])

            async def on_response(name: str, response: ModelResponse, latency: int) -> None:
                cost = await self.record_call(
                    scope,
                    agent_name=target.name,
                    kind="eval" if scope.evaluation else SUBAGENT_KIND,
                    model=name,
                    response=response,
                    latency_ms=latency,
                )
                parent.trace.cost_micro_usd += cost
                if scope.on_cost is not None:
                    scope.on_cost(cost)

            child.on_response = on_response
            specs = AgentRunner(self)._tools(
                scope.app,
                target,
                guard,
                toolbox,
                read_only,
                skills,
                kbs,
                False,
                [],
                scope.who,
                scope.tags,
                scope.groups,
                result,
                loadable=bool(on_demand) or any(s.files for s in preloaded),
            )
            specs = [s for s in specs if not asks(s.name)]
            child.tools = {s.name: s for s in specs}
            instructions = await self._instructions(scope.app.id, target)
            # A subagent renders its own modules at their defaults (no session choice, no pin).
            modules = await resolve_modules(self.store, scope.app.id, target, None)
            prompt = system_prompt(
                target,
                now=self.now(),
                app_name=scope.app.name,
                page=None,
                memories=[],
                skills=on_demand,
                page_text_chars=0,
                voice=False,
                instructions=instructions,
                timezone=str((scope.app.defaults or {}).get("timezone") or "UTC"),
                preloaded=preloaded,
                scripts_runnable=False,
                modules=modules,
                locale=scope.locale,
            )
            notes = guard.system_notes()
            if kbs:
                notes = f"{notes}\n\n{CITE_NOTE}" if notes else CITE_NOTE
            text = prompt.text()
            agent = build_agent(
                child,
                toolset=GuardedToolset(EvokaiToolset(specs)) if specs else None,
                system_prompt=f"{text}\n\n{notes}" if notes else text,
                output_type=str,
            )
            return agent, child

        return make

    async def record_call(
        self,
        scope: Scope,
        *,
        agent_name: str,
        kind: str,
        model: str,
        response: ModelResponse,
        latency_ms: int,
    ) -> int:
        """Record one answered model call as usage, with the model that answered.

        Returns:
            Its cost in micro-dollars (never shown to people).
        """
        u = response.usage
        cost = call_cost_micro_usd(model, u)
        await self.store.usage.record(
            scope.who,
            scope.tags,
            agent_name=agent_name,
            session_id=scope.session_id,
            turn_id=scope.run_id,
            kind=kind,
            model=bare(model),
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=u.cache_read_tokens or 0,
            cache_write_tokens=u.cache_write_tokens or 0,
            cost_micro_usd=cost,
            tool_calls=sum(1 for p in response.parts if isinstance(p, ToolCallPart)),
            latency_ms=latency_ms,
        )
        telemetry.record("model_cost", cost, model=bare(model))
        return cost

    async def _before_call(
        self,
        run: TurnRun,
        inp: TurnInput,
        rs: RunState,
        candidates: list[str],
        emit: Any,
        say: Any,
    ) -> None:
        """Before every model call: cancellation, the token budget and the caps.

        Raises:
            _Cancelled: The turn was cancelled.
            StopRun: The budget or a cap stops the turn (what the user sees is already sent).
        """
        if run.handle.cancelled.is_set():
            raise _Cancelled()
        guard = self._guard(run)
        if guard.budget(0):
            emit({
                "type": "text",
                "delta": "\n\n" + message(run.app, "turn.maxSteps", session=run.session),
            })
            raise StopRun("guard")
        claims = inp.principal.claims
        caps = await cap_state(self.store, run.app, run.who, self.now(), claims.limits)
        rs.extra["caps"] = caps
        if inp.evaluation:
            used = await self.store.usage.eval_spend(run.app.id, self.now())
            if used + estimate_call_usd(candidates[0]) > eval_cap_usd(run.app):
                emit({"type": "error", "code": "limit", "limit": "eval", "message": EVAL_LIMIT})
                raise StopRun("limit")
            return
        hit = caps.hit(estimate_call_usd(candidates[0]))
        if hit:
            emit({
                "type": "error",
                "code": "limit",
                "limit": hit.kind,
                "message": limit_message(run.app, hit, session=run.session),
                "resetsAt": hit.end.isoformat(),
            })
            # In voice the person may not be looking at the panel: the cap is said aloud too,
            # or the assistant just seems not to answer.
            say(speakable(limit_message(run.app, hit, session=run.session), run.rules))
            raise StopRun("limit")

    async def _on_response(
        self,
        run: TurnRun,
        inp: TurnInput,
        rs: RunState,
        name: str,
        response: ModelResponse,
        latency_ms: int,
        emit: Any,
    ) -> None:
        """After every answered model call: the budget, usage with the model that answered."""
        u = response.usage
        self._guard(run).budget((u.input_tokens or 0) + (u.output_tokens or 0))
        cost = await self.record_call(
            self._scope(run, inp),
            agent_name=run.agent.name,
            kind="eval" if inp.evaluation else "llm",
            model=name,
            response=response,
            latency_ms=latency_ms,
        )
        run.cost_micro_usd += cost
        rs.trace.cost_micro_usd += cost
        if rs.fallback:
            run.timings["fallback"] = True
        run.timings["model"] = bare(name)
        caps = rs.extra.get("caps")
        if caps is not None:
            # Browsers see shares only; amounts stay on the server (turn record, usage API).
            emit({"type": "usage", **caps.plus(cost / 1e6).view()})

    async def _drive(
        self,
        run: TurnRun,
        rs: RunState,
        model: Agent[RunState, Any],
        history: list[Any],
        results: DeferredToolResults | None,
        resume: dict[str, Any] | None,
        emit: Any,
        say: Any,
        splitter: SentenceSplitter,
        t0: float,
        *,
        max_calls: int,
        model_settings: dict[str, Any],
    ) -> None:
        """Run the agent node by node: stream the answer, store each step, stop where Evokai does.

        A turn ends on an answer without tool calls, a confirmation, a question, the tool-call cap,
        the output filter, a cap, a model failure or the turn's cancellation.

        Raises:
            _Cancelled: The turn was cancelled (what was said is kept, marked as interrupted).
        """
        guard = self._guard(run)

        def show(text: str) -> None:
            if not text:
                return
            rs.shown = True
            rs.text += text
            self._show(text, emit, say, splitter, run, t0)

        async def go() -> None:
            first_tools = resume is not None
            with ToolManager.parallel_execution_mode("sequential"):
                async with model.iter(
                    None,
                    message_history=history,
                    deps=rs,
                    deferred_tool_results=results,
                    model_settings=cast(ModelSettings, model_settings),
                    usage_limits=UsageLimits(request_limit=max_calls + 2),
                ) as ar:
                    async for node in ar:
                        if Agent.is_model_request_node(node):
                            filt = guard.output_filter(
                                clean=run.markup.clean if run.markup else None
                            )
                            live = LiveStep(
                                guard,
                                emit,
                                reasoning=run.agent.show_reasoning,
                                args_of=rs.extra.get("argsOf", frozenset()),
                                clean=run.markup.clean if run.markup else None,
                            )
                            started = time.perf_counter()
                            rs.extra["streaming"] = True
                            async with node.stream(ar.ctx) as stream:
                                async for ev in stream:
                                    if rs.first_ms is None:
                                        rs.first_ms = round((time.perf_counter() - started) * 1000)
                                    live.event(ev)
                                    delta = None
                                    if isinstance(ev, PartStartEvent) and isinstance(
                                        ev.part, TextPart
                                    ):
                                        delta = ev.part.content
                                    elif isinstance(ev, PartDeltaEvent) and isinstance(
                                        ev.delta, TextPartDelta
                                    ):
                                        delta = ev.delta.content_delta
                                    if isinstance(ev, PartStartEvent) and isinstance(
                                        ev.part, NativeToolCallPart | NativeToolReturnPart
                                    ):
                                        self._native(run, rs, ev.part, emit)
                                    if delta:
                                        show(filt.push(delta))
                                        if filt.blocked:
                                            break
                                show(filt.flush())
                                for seg in splitter.flush():
                                    say(seg)
                                guard.after_output(filt)
                                rs.extra["argsShown"] = live.finish()
                                response = stream.response
                                if filt.blocked and rs.attempt is not None:
                                    # Stopped early: pydantic-ai will not report this call.
                                    a, rs.attempt = rs.attempt, None
                                    await finish_attempt(rs, a, response)
                            rs.extra["streaming"] = False
                            if filt.blocked:
                                self._settle_args(run, rs, emit)
                                stop = message(run.app, "turn.guardStopped", session=run.session)
                                emit({"type": "text", "delta": ("\n\n" if rs.text else "") + stop})
                                say(stop)
                                answer = f"{rs.text}\n\n{stop}" if rs.text else stop
                                await self._add(
                                    run,
                                    role="assistant",
                                    content=[{"text": answer}, *_acted(rs)],
                                    text=answer,
                                )
                                run.reason = "guard"
                                return
                            if not any(isinstance(p, ToolCallPart) for p in response.parts):
                                blocks = response_blocks(response)
                                if blocks:
                                    await self._add(
                                        run,
                                        role="assistant",
                                        content=blocks,
                                        text=rs.text,
                                    )
                                run.reason = "end_turn"
                                return
                        elif Agent.is_call_tools_node(node):
                            response = node.model_response
                            done: list[Any] = []
                            async with node.stream(ar.ctx) as stream:
                                done += [
                                    ev.part
                                    async for ev in stream
                                    if isinstance(ev, FunctionToolResultEvent)
                                ]
                            self._settle_args(run, rs, emit)
                            got = result_blocks(
                                ModelRequest(parts=done), rs.ok, rs.renamed, rs.wrote
                            )
                            if first_tools:
                                # The user's decision: what ran before it and what it ran now, in
                                # one message.
                                first_tools = False
                                ready = [
                                    _result(
                                        r["toolUseId"],
                                        r["name"],
                                        r["ok"],
                                        r["text"],
                                        wrote=bool(r.get("wrote")),
                                    )
                                    for r in (resume or {})["state"]["ready"]
                                ]
                                wrote = any(b["toolResult"].get("wrote") for b in got)
                                await self._add(
                                    run,
                                    role="user",
                                    content=ready + got,
                                    events=[
                                        {
                                            "type": "confirmed",
                                            "approve": rs.confirm["approve"],
                                            "writes": wrote,
                                        }
                                    ]
                                    if rs.confirm
                                    else None,
                                )
                                continue
                            assistant = response_blocks(response, rs.args, rs.renamed)
                            if rs.waiting:
                                kept = (
                                    reasoning_block(response) if run.storage == "evokai" else None
                                )
                                if kept is not None:
                                    # Sent back with the calls when they are carried out (the
                                    # provider requires it); never shown, never sent otherwise.
                                    assistant.append(kept)
                                ready = [
                                    {
                                        "toolUseId": b["toolResult"]["id"],
                                        "name": b["toolResult"]["name"],
                                        "ok": b["toolResult"]["ok"],
                                        "text": b["toolResult"]["text"],
                                        "wrote": bool(b["toolResult"].get("wrote")),
                                    }
                                    for b in got
                                ]
                                await self._confirm(run, rs, ready, assistant, emit, say)
                                return
                            if assistant:
                                await self._add(
                                    run,
                                    role="assistant",
                                    content=assistant,
                                    text=rs.text,
                                )
                            await self._add(run, role="user", content=got)
                            # A question shown to the user ends the turn, like a confirmation: the
                            # next model call waits for the answer.
                            if any(e["type"] == "ask" for e in rs.events):
                                run.reason = "ask"
                                return
                            if rs.tool_calls >= max_calls:
                                emit({
                                    "type": "text",
                                    "delta": "\n\n"
                                    + message(run.app, "turn.maxSteps", session=run.session),
                                })
                                run.reason = "max_steps"
                                return
                            log.debug("step_done", extra={"turnId": run.turn_id, "step": rs.step})
            run.reason = "end_turn"

        try:
            await self._race(run, go())
        except StopRun as stop:
            run.reason = stop.reason
            if isinstance(stop.detail, dict) and stop.detail.get("store"):
                # A capability ended the turn on something it showed (ask_user's next question):
                # it is kept as the turn's answer, so the next message answers it.
                await self._add(run, role="assistant", content=stop.detail["store"])
        except _Cancelled:
            if rs.attempt is not None and rs.attempt.span is not None:
                rs.trace.end(rs.attempt.span, status="cancelled", outputTokens=None)
            if rs.extra.get("streaming"):
                await self._keep_partial(run, rs.text, splitter, _acted(rs))
            raise
        except Exception as e:
            if rs.extra.get("model_error") is None and not rs.extra.get("streaming"):
                raise
            if rs.attempt is not None and rs.attempt.span is not None:
                rs.trace.end(rs.attempt.span, status="error", errorType=error_class(e))
            log.debug("turn_model_failed", extra={"turnId": run.turn_id, "error": error_class(e)})
            emit({
                "type": "error",
                "code": "model",
                "message": message(run.app, "turn.modelUnavailable", session=run.session),
            })
            if rs.text or _acted(rs):
                await self._keep_partial(run, rs.text, splitter, _acted(rs))
            run.status, run.reason = "failed", "model"
        finally:
            if rs.fallback:
                run.timings["fallback"] = True

    async def _confirm(
        self,
        run: TurnRun,
        rs: RunState,
        ready: list[dict[str, Any]],
        assistant: list[dict[str, Any]],
        emit: Any,
        say: Any,
    ) -> None:
        """End the turn on calls that change data: keep them waiting, ask the user."""
        action_id = await self.store.pending.create(
            run.who,
            run.session.id,
            {"ready": ready, "calls": rs.waiting},
            ttl_seconds=None if run.storage == "evokai" else 3600,
        )
        confirm = {
            "type": "confirm",
            "actionId": action_id,
            "items": [w["label"] for w in rs.waiting],
            "why": [w.get("why") or {"key": REASON_WRITES} for w in rs.waiting],
        }
        rs.events.append(confirm)
        emit(confirm)
        items = "; ".join(w["label"] for w in rs.waiting)
        say(
            speakable(
                message(run.app, "turn.confirmPrompt", session=run.session, items=items), run.rules
            )
        )
        if assistant:
            await self._add(run, role="assistant", content=assistant, text=rs.text)
        run.reason = "confirm"

    def _turn_tools(
        self,
        run: TurnRun,
        inp: TurnInput,
        rs: RunState,
        toolbox: Toolbox,
        skills: dict[str, Skill],
        kbs: list[m.KnowledgeBase],
        targets: dict[str, AgentDef],
        memory_on: bool,
        on_demand: list[Skill],
        preloaded: list[Skill],
        say: Any,
    ) -> list[ToolSpec]:
        """The tools of a turn, each with its handler.

        The application's tools (as the user, those that change data wait for a confirmation),
        then Evokai's own: client tools, memory, skills, handoff, knowledge, code, skill scripts.

        Returns:
            The tool specs, in the order they are offered.
        """
        agent, guard, who = run.agent, rs.guard, run.who
        settings = self.settings
        claims = inp.principal.claims
        emit = rs.emit
        assert emit is not None  # ruff: ignore[assert] (a turn always emits)
        app_names = toolbox.names()
        figures: set[float] = rs.extra.setdefault("toolFigures", set())
        mutating = toolbox.mutating() | {n for n in app_names if guard.forced_confirmation(n)}

        def app_tool(name: str) -> Any:
            async def handle(args: dict[str, Any], cid: str, span: Any) -> ToolResult:
                emit({"type": "tool", "name": name, "status": "start", **_call(cid)})
                filler = (
                    self._filler(agent, name, say)
                    if inp.mode == "voice" and rs.confirm is None
                    else None
                )
                try:
                    r = await toolbox.call(name, args)
                finally:
                    if filler:
                        filler.cancel()
                emit({
                    "type": "tool",
                    "name": name,
                    "status": "done" if r.ok else "error",
                    **_call(cid),
                })
                if r.connect and not any(
                    e["type"] == "connect" and e.get("provider") == r.connect["provider"]
                    for e in rs.events
                ):
                    # The person has no connection to this provider: a card to connect.
                    rs.show({"type": "connect", **r.connect})
                text_out = await guard.tool_result(name, r.text)
                if span is not None:
                    span.set(outcome="ok" if r.ok else "error", resultChars=len(text_out))
                if r.ok:
                    # What a chart may show (``client_tools.chart_from_tools``).
                    figures.update(visuals.numbers_in(r.text))
                return ToolResult(ok=r.ok, text=clip(text_out, settings.tool_result_chars))

            return handle

        out = [
            ToolSpec(
                name=t.name,
                description=t.description,
                schema=t.input_schema,
                run=app_tool(t.name),
                kind="app",
                server=toolbox.server_of(t.name),
                mutating=t.name in mutating,
                read_only=t.name not in mutating,
                return_schema=t.output_schema,
            )
            for t in toolbox.tools
        ]
        scripts = (
            self.skill_run_offered(agent, run.grants, guard, skills)
            if SKILL_RUN not in app_names
            else []
        )
        loadable = bool(on_demand) or any(s.files for s in preloaded)

        def local(name: str) -> Any:
            async def handle(args: dict[str, Any], cid: str, span: Any) -> ToolResult:
                if name == "ui_chart" and agent.client_tools.chart_from_tools:
                    chart = visuals.to_chart(args)
                    loose = visuals.ungrounded(chart, figures) if chart else []
                    if loose:
                        if span is not None:
                            span.set(outcome="error", type="client", ungrounded=len(loose))
                        shown = ", ".join(str(v) for v in loose[:6])
                        return ToolResult(
                            False,
                            f"Not shown: no tool returned {shown} in this turn. Read the data "
                            "again with the tools now, then chart their values as they are "
                            "(a duration in ms with unit ms).",
                        )
                r = await self._local(
                    name,
                    args,
                    cid,
                    who,
                    agent,
                    skills,
                    emit,
                    say,
                    rs.events,
                    run.rules,
                    run.app,
                    guard,
                    session_id=run.session.id,
                    scripts_runnable=bool(scripts),
                )
                if span is not None:
                    span.set(outcome="ok" if r.ok else "error", type="client")
                return r

            return handle

        for t in local_tools(agent, loadable, memory=memory_on):
            if t["name"] in app_names:
                continue
            out.append(
                ToolSpec(
                    name=t["name"],
                    description=t["description"],
                    schema=t["schema"],
                    run=local(t["name"]),
                    read_only=t["name"] == "skill_load",
                )
            )
        if inp.client_tools and inp.channel == "browser":
            out += self._client_tools(run, rs, app_names, wait=inp.client_wait)
        if targets and guard.offered(HANDOFF) and HANDOFF not in app_names:
            handoff = handoff_tool([(n, t.title) for n, t in targets.items()])

            async def hand(args: dict[str, Any], _cid: str, span: Any) -> ToolResult:
                r = await self._handoff(run, args, targets, rs.show)
                if span is not None:
                    span.set(outcome="ok" if r.ok else "error", type="handoff")
                return r

            out.append(
                ToolSpec(
                    name=HANDOFF,
                    description=handoff["description"],
                    schema=handoff["schema"],
                    run=hand,
                    kind="handoff",
                )
            )
        if kbs and guard.offered(KNOWLEDGE_TOOL) and KNOWLEDGE_TOOL not in app_names:
            kd = knowledge_tool(kbs)

            async def search(args: dict[str, Any], cid: str, span: Any) -> ToolResult:
                r = await self._knowledge_search(
                    run, inp, kbs, args, guard, emit, rs.show, call_id=cid
                )
                if span is not None:
                    span.set(outcome="ok" if r.ok else "error", type="knowledge")
                return r

            out.append(
                ToolSpec(
                    name=KNOWLEDGE_TOOL,
                    description=kd["description"],
                    schema=kd["schema"],
                    run=search,
                    kind="knowledge",
                )
            )

        def sandboxed(name: str, runner: Any) -> Any:
            async def handle(args: dict[str, Any], cid: str, span: Any) -> ToolResult:
                emit({"type": "tool", "name": name, "status": "start", **_call(cid)})
                result = await runner(args)
                for chart in result.charts:
                    rs.show({"type": "chart", "chart": chart})
                emit({
                    "type": "tool",
                    "name": name,
                    "status": "done" if result.ok else "error",
                    **_call(cid),
                })
                text_out = await guard.tool_result(name, result.text)
                if span is not None:
                    span.set(**result.attrs)
                return ToolResult(ok=result.ok, text=clip(text_out, settings.tool_result_chars))

            return handle

        if self.code_offered(agent, run.grants, guard) and CODE_TOOL not in app_names:
            cd = code_tool()
            out.append(
                ToolSpec(
                    name=CODE_TOOL,
                    description=cd["description"],
                    schema=cd["schema"],
                    run=sandboxed(
                        CODE_TOOL,
                        lambda args: self.run_code(
                            args,
                            guard,
                            who,
                            claims.tags,
                            agent=agent.name,
                            session_id=run.session.id,
                            turn_id=run.turn_id,
                            evaluation=inp.evaluation,
                        ),
                    ),
                    kind="code",
                )
            )
        if scripts:
            sd = skill_run_tool(scripts)
            out.append(
                ToolSpec(
                    name=SKILL_RUN,
                    description=sd["description"],
                    schema=sd["schema"],
                    run=sandboxed(
                        SKILL_RUN,
                        lambda args: self.run_skill_script(
                            run.app.id,
                            args,
                            skills,
                            guard,
                            who,
                            claims.tags,
                            agent=agent.name,
                            session_id=run.session.id,
                            turn_id=run.turn_id,
                            evaluation=inp.evaluation,
                        ),
                    ),
                    kind="skill_run",
                )
            )
        # Without the confirm client tool nobody can be asked: a tool an ask rule may make wait for
        # the person is left out, like a tool that changes data (``McpRegistry.toolbox``).
        asks = run.grants.client("confirm")
        return [t for t in out if guard.offered(t.name) and (asks or not guard.askable(t.name))]

    @staticmethod
    def _settle_args(run: TurnRun, rs: RunState, emit: Any) -> None:
        """Calls whose arguments were shown and that did not start: they wait or did not run.

        A card that showed a call being written never stays as if it might still run: the call
        waits for the person's confirmation (``waiting``, the confirmation card follows), or it
        was refused, cut off or left out (``skipped``).
        """
        shown: dict[str, str] = rs.extra.pop("argsShown", None) or {}
        waiting = {w["toolUseId"] for w in rs.waiting}
        for cid, name in shown.items():
            if run.parts.status(cid) == "args":
                status = "waiting" if cid in waiting else "skipped"
                emit({"type": "tool", "name": name, "id": cid, "status": status})

    @staticmethod
    def _native(run: TurnRun, rs: RunState, part: Any, emit: Any) -> None:
        """A provider built-in tool's call or result, as events.

        A ``tool`` event, and a ``citation`` per web source (numbered with the turn's other
        citations); traced as an event.
        """
        if isinstance(part, NativeToolCallPart):
            emit({
                "type": "tool",
                "name": part.tool_name,
                "status": "start",
                "builtin": True,
                **_call(part.tool_call_id),
            })
            if builtin_tools.kind_of(part.tool_name) in builtin_tools.CHANGING:
                # Kept with an answer that ends before its response is stored (``_acted``).
                rs.extra.setdefault("acted", []).append({
                    "builtinTool": {
                        "id": part.tool_call_id,
                        "name": part.tool_name,
                        "changes": True,
                    }
                })
            return
        failed = isinstance(part.content, dict) and part.content.get("error_code")
        status = "error" if failed else "done"
        emit({
            "type": "tool",
            "name": part.tool_name,
            "status": status,
            "builtin": True,
            **_call(part.tool_call_id),
        })
        # A source shown as a link follows the link policy, like a link in the answer.
        found = [x for x in builtin_tools.sources(part.content) if rs.guard.link_allowed(x["url"])]
        for src in found:
            n = run.citations.get(src["url"])
            if n is None:
                n = run.citations[src["url"]] = len(run.citations) + 1
                rs.show({
                    "type": "citation",
                    "index": n,
                    "title": src["title"],
                    "url": src["url"],
                    "source": part.tool_name,
                })
        rs.trace.event(
            "tool",
            f"builtin_tool {part.tool_name}",
            tool=part.tool_name,
            callId=part.tool_call_id,
            type="builtin",
            outcome="error" if failed else "ok",
            sources=len(found),
        )

    def _client_tools(
        self, run: TurnRun, rs: RunState, app_names: set[str], *, wait: bool = True
    ) -> list[ToolSpec]:
        """The custom client tools of a turn: the agent's, then the session token's.

        Each sends a ``client_tool`` event; one that waits defers its call until the client
        answers (``ClientToolWait``). An application tool with the same name wins.

        Returns:
            Their tool specs.
        """
        session_tools = [
            t
            for t in custom.merged([], run.session.client_tools)
            if run.grants.client_tool(t.name, static=False)
        ]
        tools = custom.merged(run.agent.client_tools.custom, session_tools)
        if not wait:
            tools = [t for t in tools if not t.wait]
        emit = rs.emit
        assert emit is not None  # ruff: ignore[assert] (a turn always emits)
        waits: dict[str, float] = rs.extra.setdefault("clientWaits", {})
        names: dict[str, str] = rs.extra.setdefault("clientNames", {})
        announce: dict[str, dict[str, Any]] = rs.extra.setdefault("clientAnnounce", {})

        def handler(tool: Any) -> Any:
            async def handle(  # ruff: ignore[unused-async] (a handler is awaited)
                args: dict[str, Any], cid: str, span: Any
            ) -> ToolResult:
                event = {
                    "type": "client_tool",
                    "callId": cid,
                    "name": tool.name,
                    "args": args,
                    "wait": tool.wait,
                }
                if span is not None:
                    span.set(type="client", wait=tool.wait)
                if not tool.wait:
                    emit(event)
                    return ToolResult(True, custom.OK_TEXT)
                # Sent when the wait opens: a step that ends on a confirmation never sends it.
                announce[cid] = event
                waits[cid] = time.perf_counter()
                names[cid] = custom.model_name(tool.name)
                if span is not None:
                    span.set(outcome="waiting")
                raise CallDeferred()

            return handle

        out = []
        for t in tools:
            name = custom.model_name(t.name)
            if name in app_names:
                log.warning("client_tool_shadowed", extra={"turnId": run.turn_id, "tool": name})
                continue
            out.append(
                ToolSpec(
                    name=name,
                    description=t.description,
                    schema=t.parameters,
                    run=handler(t),
                    kind="client",
                    metadata={"evokai_client_wait": t.wait},
                )
            )
        return out

    def _client_wait(self, run: TurnRun, rs: RunState) -> ClientToolWait | None:
        """The capability that waits for the client's results, when a client tool waits.

        Returns:
            It, or None.
        """
        names = frozenset(
            n
            for n, t in rs.tools.items()
            if t.kind == "client" and t.metadata.get("evokai_client_wait")
        )
        if not names:
            return None
        guard, trace = rs.guard, rs.trace
        waits: dict[str, float] = rs.extra.setdefault("clientWaits", {})
        called: dict[str, str] = rs.extra.setdefault("clientNames", {})
        limit = self.settings.tool_result_chars

        def waited(cid: str) -> int:
            started = waits.pop(cid, None)
            return round((time.perf_counter() - started) * 1000) if started else 0

        async def accept(cid: str, result: Any, error: str | None) -> str:
            name = called.get(cid, "client")
            ok = error is None
            if ok:
                text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            else:
                text = f"Error from the client application: {clip(str(error), 2_000)}"
            # The result comes from the browser: data the person controls, never instructions.
            text = clip(await guard.tool_result(name, text), limit)
            rs.ok[cid] = ok
            run.parts.settle(cid, ok)
            span = trace.event(
                "tool",
                f"client_tool_result {name}",
                tool=name,
                callId=cid,
                type="client",
                waitedMs=waited(cid),
                outcome="ok" if ok else "error",
                resultChars=len(text),
            )
            trace.put(span, result=text)
            return text

        announce: dict[str, dict[str, Any]] = rs.extra.setdefault("clientAnnounce", {})

        def announced(cids: list[str]) -> None:
            # Each call names the calls the turn now waits for together: a client can run them
            # all before it answers.
            batch = [cid for cid in cids if cid in announce]
            for cid in cids:
                event = announce.pop(cid, None)
                if event is not None and rs.emit is not None:
                    rs.emit({**event, "batch": batch})

        def timed_out(cids: list[str]) -> None:
            for cid in cids:
                rs.ok[cid] = False
                run.parts.settle(cid, ok=False)
                name = called.get(cid, "client")
                trace.event(
                    "tool",
                    f"client_tool_result {name}",
                    tool=name,
                    callId=cid,
                    type="client",
                    waitedMs=waited(cid),
                    outcome="timeout",
                )

        return ClientToolWait(
            calls=self.client_calls,
            session_id=run.session.id,
            turn_id=run.turn_id,
            names=names,
            accept=accept,
            announce=announced,
            timed_out=timed_out,
            timeout_seconds=self.settings.client_tool_timeout_seconds,
        )

    @staticmethod
    def _spoken(
        inp: TurnInput,
        pending: m.PendingAction | None,
        shown: list[dict[str, Any]],
        rules: SpeechRules,
    ) -> TurnInput:
        """Read a spoken reply by rules, not by a model.

        A yes or no answers an open confirmation; an ordinal ("the second one") or an option's
        words answer an open question. The words come from the application's locale.

        Returns:
            The input, as a confirmation or with the option's text when the reply matched.
        """
        text = inp.text or ""
        if pending is not None and (approve := spoken_decision(text, rules)) is not None:
            return replace(inp, text=None, confirm={"actionId": pending.id, "approve": approve})
        ask = open_ask(shown)
        options = [str(o) for o in (ask or {}).get("options") or []]
        if options and (option := spoken_option(text, options, rules)):
            return replace(inp, text=option)
        return inp

    def _allowed(self, app: m.Application, model: str) -> bool:
        if not self.catalog.allowed(model):
            return False
        if not residency_allows(protection(app), model, self.settings.aws_region):
            return False
        app_models = app.allowed_models or []
        return not app_models or any(
            fnmatch.fnmatchcase(normalize(model), normalize(p)) for p in app_models
        )

    def _candidates(
        self,
        agent: AgentDef,
        app: m.Application,
        requested: str | None,
        grants: Grants | None = None,
    ) -> list[str]:
        """Primary model (the token's choice if the agent offers it) and the fallbacks.

        With grants, models that are off are skipped and a fallback-only model never answers
        first; when nothing is granted the list is empty.

        Returns:
            Model names to try, in order.
        """
        primary = agent.model
        if requested and bare(requested) in {bare(x) for x in (agent.model, *agent.models)}:
            primary = requested
        out = [primary] + [f for f in agent.fallbacks if bare(f) != bare(primary)]
        allowed = [c for c in out if self._allowed(app, c)]
        if grants is not None:
            return grants.chain(allowed)
        return allowed or [primary]

    async def _recall(
        self,
        run: TurnRun,
        guard: TurnGuard,
        user_text: str | None,
        shown: list[dict[str, Any]],
    ) -> str | None:
        """The memories of this turn, as the marked block the prompt carries.

        Returns:
            The block, or None when nothing is remembered (or recall failed: the turn goes on).
        """
        query = user_text or next(
            (str(x["text"]) for x in reversed(shown) if x["role"] == "user" and x.get("text")), ""
        )
        k = run.agent.memory.recall if run.agent.memory is not None else DEFAULT_RECALL
        try:
            recalled = await self.memory.recall(
                run.app,
                run.who,
                run.agent.name,
                query,
                k=k,
                session_id=run.session.id,
                turn_id=run.turn_id,
            )
        except Exception as e:
            log.warning(
                "memory_recall_failed",
                extra={"turnId": run.turn_id, "error_type": type(e).__name__},
            )
            return None
        if run.trace is not None:
            run.trace.event(
                "memory",
                "memory recall",
                recalled=len(recalled),
                kinds=sorted({r.kind for r in recalled}),
            )
        if not recalled:
            return None
        block = memory_block([{"id": r.id, "fact": r.text, "kind": r.kind} for r in recalled])
        return guard.mark(guard.protect_text(block), "memory")

    def code_allowed(self, agent: AgentDef, grants: Grants) -> bool:
        """Whether an agent may run code at all (``code_run``, skill scripts with ``skill_run``).

        The deployment has a sandbox, the application is granted ``code`` and the agent asks for
        it.

        Returns:
            True when all three hold.
        """
        return bool(
            self.code is not None
            and self.code.available
            and agent.code is not None
            and agent.code.enabled
            and grants.code()
        )

    def code_offered(self, agent: AgentDef, grants: Grants, guard: TurnGuard) -> bool:
        """Whether an agent gets ``code_run``: code is allowed and the tool policy offers it.

        Returns:
            True when both hold.
        """
        return (
            self.code_allowed(agent, grants)
            and self.code_protected(guard)
            and guard.offered(CODE_TOOL)
        )

    def code_protected(self, guard: TurnGuard) -> bool:
        """Whether the application's data protection profile lets data reach the code sandbox.

        Returns:
            True without a profile, or when the sandbox (AgentCore, Azure sessions) in its region
            meets it.
        """
        if not guard.protection or self.code is None:
            return True
        from evokai.protection import processor_allows

        return processor_allows(guard.protection, self.code.processor, region=self.code.region)

    def skill_run_offered(
        self, agent: AgentDef, grants: Grants, guard: TurnGuard, skills: dict[str, Skill]
    ) -> list[Skill]:
        """The skills whose scripts an agent may run with ``skill_run``.

        Code is allowed (deployment, grant, agent), the tool policy offers ``skill_run`` and a
        skill has scripts.

        Returns:
            The skills with scripts (empty: no ``skill_run`` tool).
        """
        if (
            not self.code_allowed(agent, grants)
            or not self.code_protected(guard)
            or not guard.offered(SKILL_RUN)
        ):
            return []
        return [s for s in skills.values() if s.scripts]

    async def run_code(
        self,
        args: dict[str, Any],
        guard: TurnGuard,
        who: Who,
        tags: list[str],
        *,
        agent: str,
        session_id: str | None,
        turn_id: str | None,
        evaluation: bool = False,
    ) -> Any:
        """Run ``code_run`` in the sandbox and record its session time as usage.

        Returns:
            The outcome (``evokai.sandbox.tool.CodeOutcome``).
        """
        out = await run_code(self.code.interpreter, args, guard, timeout=self.code.timeout)
        await self._code_usage(
            out,
            who,
            tags,
            agent=agent,
            session_id=session_id,
            turn_id=turn_id,
            evaluation=evaluation,
        )
        return out

    async def run_skill_script(
        self,
        application_id: str,
        args: dict[str, Any],
        skills: dict[str, Skill],
        guard: TurnGuard,
        who: Who,
        tags: list[str],
        *,
        agent: str,
        session_id: str | None,
        turn_id: str | None,
        evaluation: bool = False,
    ) -> Any:
        """Run ``skill_run`` (a skill's script) in the sandbox and record its usage like code.

        Returns:
            The outcome (``evokai.sandbox.tool.CodeOutcome``).
        """

        async def files_of(name: str) -> list[tuple[str, str]]:
            return await self.store.skills.files(application_id, name)

        out = await sandbox_skill_script(
            self.code.interpreter, args, skills, files_of, guard, timeout=self.code.timeout
        )
        await self._code_usage(
            out,
            who,
            tags,
            agent=agent,
            session_id=session_id,
            turn_id=turn_id,
            evaluation=evaluation,
        )
        return out

    async def _code_usage(
        self,
        out: Any,
        who: Who,
        tags: list[str],
        *,
        agent: str,
        session_id: str | None,
        turn_id: str | None,
        evaluation: bool,
    ) -> None:
        """Record a sandbox session as usage of kind ``code`` (its cost estimated, never shown)."""
        if out.ran:
            cost = code_cost(out.session_ms, self.code.usd_per_second)
            await self.store.usage.record(
                who,
                tags,
                agent_name=agent,
                session_id=session_id,
                turn_id=turn_id,
                kind="eval" if evaluation else CODE_KIND,
                model=getattr(self.code, "usage_model", CODE_MODEL),
                characters=out.code_chars,
                latency_ms=out.session_ms,
                cost_micro_usd=cost,
                tool_calls=1,
            )
            telemetry.record(
                "model_cost", cost, model=getattr(self.code, "usage_model", CODE_MODEL)
            )

    async def _knowledge_bases(self, run: TurnRun) -> list[m.KnowledgeBase]:
        """The knowledge bases the turn's agent may search (existing and granted).

        Returns:
            Bases, in the agent's order.
        """
        k = run.agent.knowledge
        if self.knowledge is None or k is None or not k.bases or not run.grants.knowledge():
            return []
        rows = {b.name: b for b in await self.store.knowledge.bases(run.app.id)}
        return [rows[n] for n in k.bases if n in rows]

    async def _knowledge_search(
        self,
        run: TurnRun,
        inp: TurnInput,
        kbs: list[m.KnowledgeBase],
        args: dict[str, Any],
        guard: TurnGuard,
        emit: Any,
        show: Any,
        call_id: str | None = None,
    ) -> ToolResult:
        """Run ``knowledge_search`` for the session's subject and groups; cite what it finds.

        Every passage is scanned as untrusted content; a ``citation`` event goes out for each
        passage that gets a new number in this turn.

        Returns:
            Numbered passages for the model.
        """
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(False, "Give a query.")
        name = args.get("kb")
        chosen = [b for b in kbs if b.name == name] if name else kbs
        if not chosen:
            return ToolResult(False, f"Unknown knowledge base: {name}")
        filters = args.get("filters") if isinstance(args.get("filters"), dict) else None
        claims = inp.principal.claims
        emit({"type": "tool", "name": KNOWLEDGE_TOOL, "status": "start", **_call(call_id)})
        try:
            with retrieval_span(guard.trace, chosen, query) as span:
                found = await self.knowledge.search(
                    run.app,
                    chosen,
                    query,
                    groups=set(claims.groups),
                    filters=filters,
                    top_k=run.agent.knowledge.top_k if run.agent.knowledge else None,
                    subject=run.who.subject,
                    tags=claims.tags,
                    agent=run.agent.name,
                    session_id=run.session.id,
                    turn_id=run.turn_id,
                )
                record_found(guard.trace, span, found)
        except Exception as e:
            log.warning(
                "knowledge_search_failed", extra={"turnId": run.turn_id, "error": error_class(e)}
            )
            emit({"type": "tool", "name": KNOWLEDGE_TOOL, "status": "error", **_call(call_id)})
            return ToolResult(False, "The knowledge search is not available right now.")
        items = []
        for f in found:
            n = run.citations.get(f.chunk.id)
            if n is None:
                n = run.citations[f.chunk.id] = len(run.citations) + 1
                show(self.knowledge.citation(f, n))
            items.append((n, f, await guard.untrusted(f.chunk.text, f.source, tool=KNOWLEDGE_TOOL)))
        emit({"type": "tool", "name": KNOWLEDGE_TOOL, "status": "done", **_call(call_id)})
        return ToolResult(True, format_results(query, items))

    async def _auto_knowledge(
        self,
        run: TurnRun,
        inp: TurnInput,
        kbs: list[m.KnowledgeBase],
        text: str,
        guard: TurnGuard,
        emit: Any,
    ) -> None:
        """Search before the first model step and keep it in the conversation as a tool call."""
        call_id = f"kb_{run.turn_id.replace('-', '')[:16]}"
        args = {"query": text[:2_000]}
        r = await self._race(run, self._knowledge_search(run, inp, kbs, args, guard, emit, emit))
        use = {"toolUse": {"id": call_id, "name": KNOWLEDGE_TOOL, "input": args}}
        await self._add(run, role="assistant", content=[use])
        await self._add(run, role="user", content=[_result(call_id, KNOWLEDGE_TOOL, r.ok, r.text)])

    async def _handoff_targets(self, run: TurnRun) -> dict[str, AgentDef]:
        """The agents this turn's agent may hand the conversation to.

        Each one must be active, take browser sessions and have a granted model.

        Returns:
            Name → definition.
        """
        out: dict[str, AgentDef] = {}
        for name in run.agent.handoff_to:
            if name == run.agent.name:
                continue
            try:
                target = await self.agents.active(run.app.id, name)
            except NotFound:
                continue
            if "browser" in target.channels and self._candidates(target, run.app, None, run.grants):
                out[name] = target
        return out

    async def _handoff(
        self, run: TurnRun, args: dict[str, Any], targets: dict[str, AgentDef], show: Any
    ) -> ToolResult:
        """Hand the conversation to another agent.

        From the next turn it answers, with the history so far, its own tools and policy, within
        the application's grants.

        Returns:
            What the model reads back.
        """
        target = targets.get(str(args.get("agent") or ""))
        if target is None:
            return ToolResult(False, f"Unknown agent. Choose one of: {', '.join(targets)}.")
        await self.store.sessions.set_agent(run.session.id, target.name, target.version)
        run.session.agent_name, run.session.agent_version = target.name, target.version
        title = target.title or target.name
        show({
            "type": "handoff",
            "from": run.agent.name,
            "to": target.name,
            "title": title,
            "reason": clip(str(args.get("reason") or ""), 200),
        })
        run.timings["handoff"] = target.name
        if run.trace is not None:
            run.trace.root.set(handoff=target.name)
        log.info(
            "handoff",
            extra={"turnId": run.turn_id, "from": run.agent.name, "to": target.name},
        )
        return ToolResult(
            True,
            f"Done: {title} takes over from the user's next message, with this conversation. "
            "Tell the user so in one short sentence; do not answer the request yourself.",
        )

    async def _credential(self, claims: Any) -> str | None:
        if not claims.has_credential:
            return None
        enc = await self.store.credentials.get(claims.token_id, claims.application_id)
        return self.keyring.decrypt(enc) if enc else None

    async def _instructions(self, application_id: str, agent: AgentDef) -> Instructions | None:
        """The agent's versioned prompt, if it references one.

        Returns:
            The prompt's text and syntax, or None to use the inline instructions.

        Raises:
            ConfigError: The referenced prompt does not exist.
        """
        if not agent.prompt:
            return None
        row = await self.store.prompts.get(application_id, agent.prompt.name, agent.prompt.version)
        if row is None:
            raise ConfigError(f"prompt not found: {agent.prompt.name}")
        # An empty prompt leaves the inline instructions, as it always did.
        return Instructions(row.text, row.template_syntax) if row.text else None

    async def _skills(
        self, application_id: str, agent: AgentDef, grants: Grants | None = None
    ) -> dict[str, Skill]:
        rows = await self.store.skills.all(application_id)
        wanted = set(agent.skills) if agent.skills is not None else None
        rows = [
            r
            for r in rows
            if (wanted is None or r.name in wanted) and (grants is None or grants.skill(r.name))
        ]
        index = await self.store.skills.file_index(application_id) if rows else {}
        return {
            r.name: Skill(
                r.name,
                r.description,
                r.body,
                tuple(SkillFile(p, None, n, t) for p, n, t in index.get(r.name, [])),
            )
            for r in rows
        }

    @staticmethod
    def _show(
        text: str,
        emit: Any,
        say: Any,
        splitter: SentenceSplitter,
        run: TurnRun,
        t0: float,
    ) -> None:
        """Send text the output filter let through (to the stream and speech)."""
        if not text:
            return
        if "firstTokenMs" not in run.timings:
            run.timings["firstTokenMs"] = round((time.perf_counter() - t0) * 1000)
        run.answer += text
        emit({"type": "text", "delta": text})
        for seg in splitter.push(text):
            say(seg)

    def _guard(self, run: TurnRun) -> TurnGuard:
        """The run's guard (built for runs opened without one, e.g. by tests).

        Returns:
            The turn guard.
        """
        if run.guard is None:
            run.guard = self.guard.turn(
                run.app,
                run.agent,
                subject=run.who.subject,
                session_id=run.session.id,
                turn_id=run.turn_id,
                channel="browser",
            )
        return run.guard

    @staticmethod
    def _guarded_toolbox(guard: TurnGuard, toolbox: Toolbox) -> Toolbox:
        """The toolbox without the tools the policy does not allow.

        Returns:
            The same toolbox, filtered.
        """
        toolbox.tools = [t for t in toolbox.tools if guard.offered(t.name)]
        return toolbox

    @staticmethod
    async def _guarded_page(guard: TurnGuard, page: PageContext | None) -> PageContext | None:
        """The page the user is on: text scanned, protected and marked as data; path protected.

        Returns:
            The page context for the prompt.
        """
        if page is None:
            return page
        update: dict[str, str] = {}
        if page.path:
            # The query carries what the user typed in a search box (a name, a tax code):
            # decoded, then protected like the page text, and kept short.
            update["path"] = guard.protect_text(unquote_plus(page.path)[:300])
        if page.text:
            update["text"] = guard.mark(
                guard.protect_text(await guard.untrusted(page.text, "page")), "page"
            )
        return page.model_copy(update=update)

    async def _add(
        self,
        run: TurnRun,
        *,
        role: str,
        content: list[dict[str, Any]],
        text: str = "",
        events: list[Any] | None = None,
        interrupted: bool = False,
    ) -> str | None:
        """Append a message to the conversation, wherever the application keeps it.

        An assistant message keeps what the person was shown since the previous one, in order
        (``evokai.sessions.parts``); ``events`` is for a user message (the decision on a
        confirmation).

        Returns:
            The message id, where the store assigns one.
        """
        assert run.threads is not None  # ruff: ignore[assert] (set by open())
        if role == "assistant":
            events = run.parts.take(interrupted=interrupted) or None
        if run.storage == "evokai":
            stored, kept = self._guard(run).for_storage(content, text)
            if role == "user" and (stored != content or kept != text):
                # Identifiers were masked: the question cannot be asked again as it was.
                stored = [*stored, {rewrite.MASKED: True}]
            content, text = stored, kept
            events = self._stored_parts(run, events)
        msg = ThreadMessage(role, content, text, events, interrupted=interrupted)
        msg_id = await run.threads.add(msg, run.turn_id)
        if role == "assistant":
            run.last_answer = (msg, msg_id)
        return msg_id

    def _stored_parts(self, run: TurnRun, events: list[Any] | None) -> list[Any] | None:
        """What is stored of a message's parts.

        Masked like its text when ``pii.history`` is enforced.

        Returns:
            The parts to store.
        """
        guard = self._guard(run)
        if not events or not guard.policy.enforced("pii.history"):
            return events
        return [guard.persisted(e) for e in events]

    async def _keep_shown(self, run: TurnRun) -> None:
        """Keep what the turn showed after its last stored answer with that answer.

        An error, the tool-call cap's sentence: a turn that stored no answer keeps nothing of it.
        """
        if not run.parts.pending or run.last_answer is None:
            return
        msg, msg_id = run.last_answer
        # The turn is over: a call still marked as running was cut off.
        more = run.parts.take(interrupted=True)
        if run.storage == "evokai":
            more = self._stored_parts(run, more) or []
            if msg_id is None:
                return
            await self.store.messages.append_events(run.who, msg_id, more)
        # External storage: the message is returned to the application with them.
        msg.events = [*(msg.events or []), *more]

    def _threads(
        self, storage: str, who: Who, session_id: str, thread_id: str, inp: TurnInput
    ) -> ThreadStore:
        """The conversation's message store.

        Returns:
            Evokai's database, or the history the application sent.
        """
        if storage == "external":
            return ExternalThreads.from_request(inp.history)
        return EvokaiThreads(self.store.messages, who, session_id, thread_id)

    async def _keep_partial(
        self,
        run: TurnRun,
        text: str,
        splitter: SentenceSplitter,
        acted: list[dict[str, Any]] | None = None,
    ) -> None:
        """Keep an interrupted answer in the conversation, marked as such.

        Tool calls it had started are dropped; the provider's built-in tools that can change data
        and were called stay marked (``acted``), so the answer is not regenerated.
        """
        splitter.reset()
        if text or acted:
            await self._add(
                run,
                role="assistant",
                content=[*([{"text": text}] if text else []), *(acted or [])],
                text=text,
                interrupted=True,
            )

    def _filler(self, agent: AgentDef, tool: str, say: Any) -> asyncio.Task | None:
        text = agent.voice.fillers.get(tool) or agent.voice.fillers.get("*")
        if not text:
            return None

        async def later() -> None:
            await asyncio.sleep(self.settings.filler_after_ms / 1000)
            say(text, filler=True)

        return asyncio.create_task(later())

    async def _local(
        self,
        name: str,
        args: dict[str, Any],
        call_id: str,
        who: Who,
        agent: AgentDef,
        skills: dict,
        emit: Any,
        say: Any,
        events: list[dict[str, Any]],
        rules: SpeechRules,
        app: m.Application,
        guard: TurnGuard | None = None,
        *,
        session_id: str | None = None,
        scripts_runnable: bool = False,
    ) -> ToolResult:
        def show(e: dict[str, Any]) -> None:
            events.append(e)
            emit(e)

        if name == "ui_navigate":
            path = args.get("path")
            # A hash route ("#/apps/x") is the same page as its path.
            if isinstance(path, str):
                path = path.strip().removeprefix("#")
            if not isinstance(path, str) or not re.match(
                agent.client_tools.navigate.path_pattern, path
            ):
                return ToolResult(
                    False,
                    "Path not allowed: give a path that starts with / and matches "
                    f"{agent.client_tools.navigate.path_pattern} (no address, no spaces).",
                )
            show({"type": "navigate", "path": path})
            return ToolResult(True, f"Opened {path}")
        if name == "ui_chart":
            chart = visuals.to_chart(args, message(app, "chart.other"))
            if not chart:
                return ToolResult(
                    False,
                    (
                        "Invalid chart: points need a label and a numeric value (donut and "
                        "stacked at least 2 positive entries, kpi at least one item)."
                    ),
                )
            show({"type": "chart", "chart": chart})
            return ToolResult(True, visuals.chart_summary(chart))
        if name == "ui_diagram":
            diagram = visuals.to_diagram(args)
            if not diagram:
                return ToolResult(
                    False,
                    (
                        "Invalid diagram: steps needs 2-7 steps, tree a root and at least one "
                        "child, compare at least one row."
                    ),
                )
            show({"type": "diagram", "diagram": diagram})
            return ToolResult(True, "Diagram shown.")
        if name == "ui_ask":
            ask = visuals.to_ask(args)
            if not ask:
                return ToolResult(
                    False, "Invalid question: it needs a question and 2-5 short, different answers."
                )
            show({"type": "ask", "askId": f"ask_{call_id}", **ask})
            say(speakable(ask["question"], rules))
            return ToolResult(
                True,
                (
                    "Question shown. The user's answer will come as their next message: do not "
                    "answer for them."
                ),
            )
        if name == "memory_remember":
            fact = str(args.get("fact") or "").strip()
            if not fact:
                return ToolResult(False, "Nothing to remember.")
            if guard is not None and not guard.memory(fact):
                return ToolResult(
                    False,
                    "Not remembered: memories may not hold instructions, secrets or personal data.",
                )
            if self.memory is None:
                return ToolResult(
                    True, f"Remembered ({await self.store.memories.remember(who, fact)})."
                )
            kind = args.get("kind") if args.get("kind") in ("preference", "semantic") else None
            done = await self.memory.remember(
                app,
                who,
                fact,
                agent=agent.name,
                kind=kind or "preference",
                session_id=session_id,
                replaces=str(args.get("replaces") or "") or None,
            )
            if done.action == "duplicate":
                return ToolResult(True, f"Already remembered ({done.id}): kept and refreshed.")
            if done.replaced:
                return ToolResult(True, f"Remembered ({done.id}); it replaces {done.replaced}.")
            return ToolResult(True, f"Remembered ({done.id}).")
        if name == "memory_forget":
            n = (
                await self.store.memories.forget(who, str(args.get("id") or ""))
                if args.get("id")
                else 0
            )
            return ToolResult(n > 0, "Forgotten." if n else "No memory with this id.")
        if name == "skill_load":

            async def read(skill: str, path: str) -> str | None:
                return await self.store.skills.file(app.id, skill, path)

            ok, text = await load_skill(skills, args, read, runnable=scripts_runnable)
            return ToolResult(ok, text)
        return ToolResult(False, f"Unknown tool: {name}")


def _call(call_id: str | None) -> dict[str, Any]:
    """The call a ``tool`` event is about (its arguments, start and end update one card).

    Returns:
        ``{"id": call_id}``, or nothing without one.
    """
    return {"id": call_id} if call_id else {}


def _acted(rs: RunState) -> list[dict[str, Any]]:
    """The marks of the built-in tools that can change data the turn's model called.

    A response stored whole carries them already (``response_blocks``); an answer stopped by the
    guard, cancelled or cut by a model failure is stored from its text, so it takes them from here.

    Returns:
        ``builtinTool`` blocks.
    """
    return list(rs.extra.get("acted") or [])


def _egress_allow(guard: TurnGuard, *, strict: bool = False) -> list[str] | None:
    """The egress allow-list a built-in tool follows (only while ``tools.egress`` is enforced).

    Args:
        guard: The turn's guard.
        strict: Any allow-list while ``tools.egress`` is not off, also in monitor (an MCP server
            the provider calls: its calls cannot be checked one by one).

    Returns:
        Domains (an empty list allows none), or None when there is no check.
    """
    tools = guard.policy.settings.tools
    on = guard.policy.on if strict else guard.policy.enforced
    if tools is None or tools.egress_allow is None or not on("tools.egress"):
        return None
    return list(tools.egress_allow)


def _new_messages(history: list[Any]) -> list[dict[str, Any]]:
    """The messages a model call saw for the first time: those after the last answer.

    Returns:
        role and content blocks of each.
    """
    tail: list[Any] = []
    for x in reversed(history):
        if x.role == "assistant":
            break
        tail.append(x)
    # An attachment's text never goes in a trace: its name, type, size and hash do.
    return [
        {"role": x.role, "content": [files_.traced(b) for b in x.content]} for x in reversed(tail)
    ]


def _attached_blocks(content: Any) -> list[dict[str, Any]]:
    """The attachment blocks of a message's content.

    Returns:
        The blocks.
    """
    return [b for b in content or [] if isinstance(b, dict) and "file" in b]


def _result(
    tool_use_id: str, name: str, ok: bool, text: str, *, wrote: bool = False
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "toolResult": {
            "id": tool_use_id,
            "name": name,
            "text": text or ("ok" if ok else "errore"),
            "ok": ok,
        }
    }
    if wrote:
        out["toolResult"]["wrote"] = True
    return out
