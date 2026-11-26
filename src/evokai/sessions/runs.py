"""Server runs: an agent called by an application's backend, with no user in the loop.

For back-office automation (triage, classification, extraction). The agent must allow the
``server`` channel. A run:

- takes text or messages, plus optional text attachments;
- uses the agent's read-only MCP tools (servers authenticated with a secret; there is no user
  credential to forward) and its skills; tools that change data are not offered, since nobody
  could confirm them;
- ends with a structured answer when the agent has an ``output_schema``: the model must call the
  output tool, the arguments are validated against the schema, and one invalid answer gets one
  retry with the validation errors;
- is priced and recorded like any turn (the application's caps apply), and stores no conversation
  unless asked.

The same runner serves the open standards (``evokai.api.openai_routes`` and ``a2a_routes``): an
A2A call is a run on the ``a2a`` channel (the agent must be marked ``a2a`` and the application
granted it), and an OpenAI-compatible call made with a session token is a run on the ``browser``
channel, as the session's subject, with its forwarded credential and its caps.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from pydantic_ai import ModelRetry, StructuredDict, ToolOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tool_manager import ToolManager
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
from pydantic_ai.usage import UsageLimits

from evokai.agents.definitions import AgentDef, ClientToolDef
from evokai.agents.modules import protected, resolve_modules
from evokai.agents.prompts import Instructions, split_skills, system_prompt
from evokai.agents.skills import load as load_skill
from evokai.core.errors import AppError, BadRequest, Forbidden
from evokai.core.util import clip, new_id
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.engine.agent import build_agent
from evokai.engine.state import RunState, Scope
from evokai.engine.tools import EvokaiToolset, GuardedToolset, ToolSpec
from evokai.grants import NotGranted, grants_of
from evokai.guard import TurnGuard
from evokai.guard.output import SentenceGate
from evokai.i18n import locale_of, message
from evokai.knowledge.tool import (
    CITE_NOTE,
    format_results,
    mark_sources,
    record_found,
    retrieval_span,
)
from evokai.knowledge.tool import TOOL_NAME as KNOWLEDGE_TOOL
from evokai.knowledge.tool import tool_def as knowledge_tool
from evokai.llm.catalog import bare
from evokai.llm.policy import agent_settings, request_settings
from evokai.llm.prices import call_cost_micro_usd, estimate_call_usd
from evokai.mcp.client import ToolResult
from evokai.sandbox.skill_scripts import TOOL_NAME as SKILL_RUN
from evokai.sandbox.skill_scripts import tool_def as skill_run_tool
from evokai.sandbox.tool import NOTE as CODE_NOTE
from evokai.sandbox.tool import TOOL_NAME as CODE_TOOL
from evokai.sandbox.tool import tool_def as code_tool
from evokai.sessions.limits import cap_state, limit_message
from evokai.sessions.live import LiveStep
from evokai.tools import custom
from evokai.tools.client_tools import SKILL_LOAD
from evokai.tools.labels import confirm_label

log = logging.getLogger("evokai.runs")

OUTPUT_TOOL = "final_result"
NO_CHARTS = "describe the results in words."
MAX_SCHEMA_RETRIES = 1
STEP_SEPARATOR = "\n\n"
"""Between the texts of two steps of a run (text, a tool call, more text): the same in a streamed
and a whole answer, and in the stored conversation."""

RunChannel = Literal["server", "a2a", "browser", "email"]
"""How a run was reached: a server run, an A2A call, an OpenAI-compatible call with a session
token (the session's subject, credential and caps), or an e-mail to an agent's mailbox
(``evokai.channels.email``)."""

OnEvent = Callable[[dict[str, Any]], None]
"""Progress of a run, for the streaming standards: ``{"type": "tool", "name", "status"}``, and
for an agent with an ``output_schema`` its answer as it is written: ``{"type": "output", "runId",
"partial"}`` (the object so far, through the output checks, not validated yet)."""


class LimitReached(AppError):
    """The application's cap stops the run."""

    status, code = 429, "limit"


class GuardStopped(AppError):
    """The output guard stopped a streamed answer (it repeated the agent's instructions)."""

    status, code = 400, "guard"


class InvalidOutput(AppError):
    """The model did not produce an answer that fits the schema."""

    status, code = 502, "invalid_output"


@dataclass
class Attachment:
    """A text attachment given to the run."""

    name: str
    media_type: str
    text: str


@dataclass
class Resume:
    """A confirmation being carried out in a run (the OpenAI-compatible API)."""

    calls: list[dict[str, Any]]
    """The waiting calls, with their stored arguments (never regenerated)."""
    ready: list[dict[str, Any]]
    """What ran in the same step before the confirmation (id, name, ok, text)."""
    approve: bool
    waited_ms: int = 0
    channel: str = "openai"


@dataclass
class RunResult:
    """What a run returns."""

    run_id: str
    output: Any
    text: str
    model: str
    input_tokens: int = 0
    """Every input token, cached ones included."""
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_micro_usd: int = 0
    tool_calls: int = 0
    conversation_id: str | None = None
    timings: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    """The knowledge passages found, as ``citation`` events (numbered as the answer cites them)."""
    waiting: list[dict[str, Any]] = field(default_factory=list)
    """Calls that change data and wait for the person's yes (``confirm`` runs): id, name, input,
    label. The run ended on them; ``ready`` holds what ran in the same step."""
    ready: list[dict[str, Any]] = field(default_factory=list)
    client_calls: list[dict[str, Any]] = field(default_factory=list)
    """Custom client tool calls (``callId``, ``name``, ``args``), for the caller to carry out."""


class AgentRunner:
    """Runs agents for application backends."""

    def __init__(self, engine: Any) -> None:
        """Share the turn engine's services (store, registries, catalog, settings).

        Args:
            engine: The turn engine.
        """
        self.engine = engine

    async def run(
        self,
        app: m.Application,
        agent: AgentDef,
        *,
        text: str | None,
        messages: list[dict[str, Any]] | None,
        attachments: list[Attachment],
        subject: str,
        tags: list[str],
        store_conversation: bool = False,
        channel: RunChannel = "server",
        credential: str | None = None,
        limits: dict[str, Any] | None = None,
        on_event: OnEvent | None = None,
        model: str | None = None,
        groups: list[str] | None = None,
        origin: str | None = None,
        conversation_id: str | None = None,
        extra_capabilities: Sequence[Any] = (),
        usage_key: str | None = None,
        prompt_options: dict[str, Any] | None = None,
        client_tools: Sequence[ClientToolDef] = (),
        on_text: Callable[[str], None] | None = None,
        confirm: bool = False,
        resume: Resume | None = None,
        offer_client_tools: bool = False,
    ) -> RunResult:
        """Run the agent to its answer.

        Args:
            app: The application.
            agent: The agent (its active version).
            text: The input as one text.
            messages: Or the input as a conversation (``role``, ``text``).
            attachments: Text attachments.
            subject: Who the run is for.
            tags: Usage tags.
            store_conversation: Keep the run as a conversation of the subject.
            channel: ``server`` (a server run), ``a2a`` (an A2A call) or ``browser`` (an
                OpenAI-compatible call with a session token).
            credential: The session's forwarded credential (``browser`` only), for MCP servers
                with ``forward_user``.
            limits: The session token's caps (``browser`` only); the subject's caps apply only
                with it (None for a server run: the application's caps alone).
            on_event: Called with the run's progress (tool calls) and, with an ``output_schema``,
                the structured answer as it is written (``output`` events).
            model: The primary model a session token chose, if the agent offers it.
            groups: The subject's groups, for knowledge bases restricted to some groups.
            origin: ``workflow`` for a workflow's agent step: it does not fire the «agent run
                finished» event (a workflow could otherwise start itself in a loop).
            conversation_id: With ``store_conversation``, the subject's conversation the run
                goes on (an e-mail thread), instead of a new one.
            extra_capabilities: Capabilities the caller adds after the agent's (a durable step's
                ``PGTaskDurability``).
            usage_key: Makes the run's usage records idempotent: each model call's record is keyed
                by it and the call's number (a durable step: its pgtask task).
            prompt_options: The prompt modules and locale the caller chose (already checked,
                ``evokai.agents.modules``); None renders the agent's defaults.
            client_tools: Custom client tools the caller declares for this run (already checked
                and granted; none waits): their calls come back in ``client_calls``.
            offer_client_tools: Offer custom client tools at all (a server run returns their
                calls; the other callers have nobody to deliver them to).
            confirm: Offer the tools that change data: a call ends the run waiting for the
                person (``RunResult.waiting``), as over the OpenAI-compatible API with
                ``openai:confirm``. An agent with an ``output_schema`` never waits.
            resume: Carry out a confirmation: the stored calls run (approved) or are cancelled
                without a model call.
            on_text: Streams the answer as it is written (an answer without ``output_schema``):
                called with each piece once the output guard let it through, token by token when
                no output control rewrites text, else sentence by sentence (``SentenceGate``).

        Returns:
            The result.

        Raises:
            NotGranted: The channel, or every model of the agent, are not granted (and
                ``Forbidden`` when the agent does not allow the channel).
            BadRequest: No input.
            LimitReached: The application's cap stopped the run.
            AppError: ``InvalidOutput`` (no schema-valid answer after the retry), ``GuardRefused``
                (an abuse limit, 429) or the token budget.

        Whatever the end, the run's trace is stored.
        """
        eng = self.engine
        grants = await grants_of(eng.store, app)
        check_channel(agent, grants, channel)
        if not (text or messages):
            raise BadRequest("Give an input.", code="empty")
        candidates = eng._candidates(agent, app, model, grants)
        if not candidates:
            raise NotGranted("model", "None of the agent's models is granted.")
        agent = grants.restrict(agent)
        t0 = time.perf_counter()
        who = Who(app.id, subject)
        run_id = new_id()
        instructions = await eng._instructions(app.id, agent)
        # An A2A call or an e-mail is a server run for the security policy (its input comes
        # from outside).
        guard_channel = "browser" if channel == "browser" else "server"
        guard = eng.guard.turn(
            app,
            agent,
            subject=subject,
            session_id=None,
            turn_id=run_id,
            channel=guard_channel,
            instructions=instructions.text if instructions else agent.instructions,
            tags=tags,
        )
        trace = guard.trace = eng.new_trace(
            trace_id=run_id,
            kind="run",
            app=app,
            agent=agent,
            subject=subject,
            session_id=None,
            mode="text",
            channel=channel,
            storage=app.storage_mode or "evokai",
            guard=guard,
        )
        await eng.guard.admit(guard)
        status, reason, result = "done", "end_turn", None
        modules = protected(
            await resolve_modules(eng.store, app.id, agent, prompt_options), guard.protect_text
        )
        if modules:
            # Which modules at which versions; never their parameters.
            trace.root.set(**{"prompt.modules": [f"{x.name}@{x.version}" for x in modules]})
        try:
            result = await self._run(
                app,
                agent,
                guard,
                run_id,
                instructions,
                candidates,
                grants,
                who,
                t0,
                text=text,
                messages=messages,
                attachments=attachments,
                subject=subject,
                tags=tags,
                store_conversation=store_conversation,
                credential=credential,
                limits=limits,
                on_event=on_event,
                groups=groups or [],
                conversation_id=conversation_id,
                extra_capabilities=extra_capabilities,
                usage_key=usage_key,
                modules=modules,
                client_tools=client_tools,
                on_text=on_text,
                confirm=confirm,
                resume=resume,
                offer_client_tools=offer_client_tools,
            )
            return result
        except LimitReached:
            status, reason = "blocked", "limit"
            raise
        except AppError as e:
            status, reason = "failed", e.code
            raise
        except Exception:
            status, reason = "failed", "internal"
            raise
        finally:
            await eng.mcp.end_run(run_id)
            await guard.flush()
            row = None
            try:
                if result is not None:
                    trace.session_id = result.conversation_id
                    out = result.output
                    trace.put(trace.root, output=out if isinstance(out, str) else out or None)
                trace.put(trace.root, input=text)
                trace.root.set(model=bare(result.model) if result else None)
                row = trace.finish(status=status, reason=reason)
                if result is not None:
                    row["cost_micro_usd"] = result.cost_micro_usd
            except Exception as e:
                log.warning("trace_failed", extra={"runId": run_id, "error": type(e).__name__})
            await eng.store_trace(row)
            if origin is None:
                await self._announce(app, agent, run_id, status, result, subject)

    async def _announce(
        self,
        app: m.Application,
        agent: AgentDef,
        run_id: str,
        status: str,
        result: RunResult | None,
        subject: str,
    ) -> None:
        """Tell the listeners (workflow event triggers) that a run ended; never raises."""
        for listener in self.engine.run_listeners:
            try:
                await listener(
                    app, agent.name, run_id, status, result.output if result else None, subject
                )
            except Exception as e:
                log.warning(
                    "run_listener_failed", extra={"runId": run_id, "error": type(e).__name__}
                )

    async def _run(
        self,
        app: m.Application,
        agent: AgentDef,
        guard: TurnGuard,
        run_id: str,
        instructions: Instructions | None,
        candidates: list[str],
        grants: Any,
        who: Who,
        t0: float,
        *,
        text: str | None,
        messages: list[dict[str, Any]] | None,
        attachments: list[Attachment],
        subject: str,
        tags: list[str],
        store_conversation: bool,
        credential: str | None = None,
        limits: dict[str, Any] | None = None,
        on_event: OnEvent | None = None,
        groups: list[str],
        conversation_id: str | None = None,
        extra_capabilities: Sequence[Any] = (),
        usage_key: str | None = None,
        modules: list[Any] | None = None,
        client_tools: Sequence[ClientToolDef] = (),
        on_text: Callable[[str], None] | None = None,
        confirm: bool = False,
        resume: Resume | None = None,
        offer_client_tools: bool = False,
    ) -> RunResult:
        """The run itself, under its guard, on the same engine as the turns.

        Returns:
            The result.

        The application's cap (``LimitReached``) and the token budget (``AppError``, 429) stop
        it from the hooks of each model call.

        Raises:
            InvalidOutput: No schema-valid answer after the retry, or no answer in the step limit.
            UnexpectedModelBehavior: The model kept answering what could not be used (no schema).
        """
        eng = self.engine
        confirm = (confirm or resume is not None) and not agent.output_schema
        if resume is not None and not resume.approve:
            return self._cancelled(app, guard, run_id, candidates[0], resume)
        self._check_size(guard, text, messages, attachments)
        toolbox = await eng.mcp.toolbox(
            app.id,
            agent,
            credential,
            grants,
            subject=subject,
            conversation=run_id,
            tags=tags,
            run_id=run_id,
            worker_calls=eng.settings.worker_model_calls,
        )
        # Nobody can confirm in a server run: tools that change data, and tools an ask rule may
        # make wait for the person, are not offered.
        mutating = {
            t.name for t in toolbox.tools if t.mutating or guard.forced_confirmation(t.name)
        }
        read_only = [
            t
            for t in toolbox.tools
            if guard.offered(t.name)
            and (confirm or (t.name not in mutating and not guard.askable(t.name)))
        ]
        skills = await eng._skills(app.id, agent, grants)
        preloaded, on_demand = split_skills(agent, skills)
        names = {t.name for t in read_only}
        kbs: list[m.KnowledgeBase] = []
        k = agent.knowledge
        if eng.knowledge is not None and k and k.bases and grants.knowledge():
            rows = {b.name: b for b in await eng.store.knowledge.bases(app.id)}
            kbs = [rows[n] for n in k.bases if n in rows]
        if not (kbs and guard.offered(KNOWLEDGE_TOOL) and KNOWLEDGE_TOOL not in names):
            kbs = []
        code_on = eng.code_offered(agent, grants, guard) and CODE_TOOL not in names
        script_skills = (
            eng.skill_run_offered(agent, grants, guard, skills) if SKILL_RUN not in names else []
        )
        result = RunResult(run_id=run_id, output=None, text="", model=candidates[0])
        settings = request_settings(agent_settings(agent), eng.settings.max_output_tokens)
        rs = RunState(
            guard=guard,
            trace=guard.trace,
            settings=eng.settings,
            run_id=run_id,
            health=eng.model_health,
            operation="request",
            max_tokens=settings.get("max_tokens"),
            op_tags={"runId": run_id},
            prompt_cache=agent.prompt_cache,
            models=[(n, eng.catalog.get(n)) for n in candidates],
            expired=eng.expired_callback(app.id, subject),
        )
        specs = self._tools(
            app,
            agent,
            guard,
            toolbox,
            read_only,
            skills,
            kbs,
            code_on,
            script_skills,
            who,
            tags,
            groups,
            result,
            loadable=bool(on_demand) or any(s.files for s in preloaded),
            mutating=mutating if confirm else set(),
        )
        names = {s.name for s in specs}
        specs += [
            s
            for s in (self._client_tools(agent, client_tools, result) if offer_client_tools else [])
            if s.name not in names and guard.offered(s.name)
        ]
        if not confirm:
            specs = [s for s in specs if not guard.askable(s.name)]
        rs.tools = {s.name: s for s in specs}
        if confirm:
            rs.label = lambda name, args: confirm_label(agent, name, args, app)
            rs.locale = locale_of(app)
        if on_event is not None:
            rs.on_tool = lambda name, status: on_event({
                "type": "tool",
                "name": name,
                "status": status,
            })
        system = system_prompt(
            agent,
            now=eng.now(),
            app_name=app.name,
            page=None,
            memories=[],
            skills=on_demand,
            page_text_chars=0,
            voice=False,
            instructions=instructions,
            timezone=str((app.defaults or {}).get("timezone") or "UTC"),
            preloaded=preloaded,
            scripts_runnable=bool(script_skills),
            modules=modules,
        )
        notes = guard.system_notes()
        if kbs:
            notes = f"{notes}\n\n{CITE_NOTE}" if notes else CITE_NOTE
        if code_on:
            notes = f"{notes}\n\n{CODE_NOTE}" if notes else CODE_NOTE
        system_text = f"{system.text()}\n\n{notes}" if notes else system.text()
        # Nothing per request in a server run's system prompt: all of it is the cacheable prefix,
        # and the input follows it as the user's message.
        history: list[ModelMessage] = [
            ModelRequest(
                parts=[
                    SystemPromptPart(content=system_text),
                    *await self._input(guard, text, messages, attachments),
                ]
            )
        ]

        async def before_call() -> None:
            caps = await cap_state(
                eng.store, app, who, eng.now(), limits, subject=limits is not None
            )
            hit = caps.hit(estimate_call_usd(candidates[0]))
            if hit:
                raise LimitReached(limit_message(app, hit))

        calls = 0

        async def on_response(name: str, response: ModelResponse, _latency: int) -> None:
            nonlocal calls
            calls += 1
            # A durable step's calls are numbered in the order they happen, the same after a
            # resume: a replayed call is recorded once.
            key = f"{usage_key}:model:{calls}" if usage_key else None
            await self._record(who, tags, agent, name, response, result, dedup_key=key)
            result.tool_calls += sum(
                1
                for p in response.parts
                if isinstance(p, ToolCallPart) and p.tool_name != OUTPUT_TOOL
            )
            u = response.usage
            if guard.budget((u.input_tokens or 0) + (u.output_tokens or 0)):
                raise AppError("The run went over its token budget.", code="budget", status=429)

        rs.before_call = before_call
        rs.on_response = on_response
        schema = agent.output_schema
        output_type: Any = [str, DeferredToolRequests] if confirm else str
        if schema:
            output_type = ToolOutput(
                StructuredDict(schema),
                name=OUTPUT_TOOL,
                description="Give the final answer, as the required structure.",
                max_retries=MAX_SCHEMA_RETRIES,
            )

        def add(cost: int) -> None:
            result.cost_micro_usd += cost

        scope = Scope(
            app=app,
            grants=grants,
            who=who,
            tags=tags,
            run_id=run_id,
            credential=credential,
            groups=groups,
            on_cost=add,
        )
        runner = build_agent(
            rs,
            toolset=GuardedToolset(EvokaiToolset(specs)) if specs else None,
            capabilities=[*await eng.capabilities_for(agent, rs, scope), *extra_capabilities],
            output_type=output_type,
            output_retries=MAX_SCHEMA_RETRIES,
        )
        if schema:

            @runner.output_validator
            def valid(output: Any) -> Any:
                errors = sorted(
                    Draft202012Validator(schema).iter_errors(output), key=lambda e: list(e.path)
                )
                if errors:
                    feedback = "; ".join(
                        f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
                        for e in errors[:10]
                    )
                    raise ModelRetry(f"Invalid answer, fix it: {feedback}")
                return output

        gate = SentenceGate(guard.output_filter()) if on_text is not None and not schema else None
        stopped = GuardStopped(message(app, "turn.guardStopped"))
        deferred = None
        if resume is not None:
            history.extend(self._resumed(resume, rs))
            deferred = DeferredToolResults(approvals={c["toolUseId"]: True for c in resume.calls})

        steps = StepText()

        async def stream(_ctx: Any, events: Any) -> None:
            # Called once per step (each model request's events).
            assert gate is not None and on_text is not None  # ruff: ignore[assert] (set together)
            steps.step()
            async for ev in events:
                delta = steps.text(ev)
                if not delta:
                    continue
                for piece in gate.push(delta):
                    on_text(piece)
                if gate.blocked:
                    guard.after_output(gate.filter)
                    raise stopped

        def partial(e: dict[str, Any]) -> None:
            assert on_event is not None  # ruff: ignore[assert] (set with the handler)
            on_event({"type": "output", "runId": run_id, "partial": e["args"]})

        async def structured(_ctx: Any, events: Any) -> None:
            # The structured answer as the model writes it, through the output checks: an object
            # replaced by the next one, not validated yet (the final answer is).
            step = LiveStep(guard, partial, reasoning=False, args_of=frozenset({OUTPUT_TOOL}))
            async for ev in events:
                step.event(ev)
            step.finish()

        handler = stream if gate is not None else None
        if schema and on_event is not None:
            handler = structured
        try:
            with ToolManager.parallel_execution_mode("sequential"):
                answer = await runner.run(
                    None,
                    message_history=history,
                    deps=rs,
                    model_settings=cast(ModelSettings, settings),
                    usage_limits=UsageLimits(
                        request_limit=agent.max_tool_calls_per_turn + MAX_SCHEMA_RETRIES + 1
                    ),
                    event_stream_handler=handler,
                    deferred_tool_results=deferred,
                )
        except UsageLimitExceeded as e:
            raise InvalidOutput("The agent did not finish within its step limit.") from e
        except UnexpectedModelBehavior as e:
            if schema:
                raise InvalidOutput("The answer did not match the output schema.") from e
            raise
        if gate is not None and on_text is not None:
            # Streamed: every piece already passed the output filter on its way out.
            for piece in gate.flush():
                on_text(piece)
            guard.after_output(gate.filter)
            if gate.blocked:
                raise stopped
        if isinstance(answer.output, DeferredToolRequests):
            # A change waits for the person: the caller asks and keeps the calls.
            result.waiting = list(rs.waiting)
            result.ready = _ready(answer.all_messages(), rs.ok)
            result.timings["totalMs"] = round((time.perf_counter() - t0) * 1000)
            guard.trace.root.set(confirm="asked")
            return result
        if gate is not None:
            result.text = result.output = gate.filter.shown
        elif schema:
            result.output = guard.check_final(dict(answer.output))
        else:
            result.text = guard.check_final(_answer_text(answer.new_messages(), str(answer.output)))
            result.output = result.text

        result.timings["totalMs"] = round((time.perf_counter() - t0) * 1000)
        if store_conversation:
            result.conversation_id = await self._store(
                who, agent, tags, text, messages, result, guard, conversation_id
            )
        log.info(
            "server_run",
            extra={
                "runId": result.run_id,
                "agent": agent.name,
                "subject": subject,
                "model": bare(result.model),
                "toolCalls": result.tool_calls,
                **result.timings,
            },
        )
        return result

    def _tools(
        self,
        app: m.Application,
        agent: AgentDef,
        guard: TurnGuard,
        toolbox: Any,
        read_only: list[Any],
        skills: dict[str, Any],
        kbs: list[m.KnowledgeBase],
        code_on: bool,
        script_skills: list[Any],
        who: Who,
        tags: list[str],
        groups: list[str],
        result: RunResult,
        *,
        loadable: bool,
        mutating: set[str] | None = None,
    ) -> list[ToolSpec]:
        """The tools of a run: read-only MCP tools, skills, knowledge, code, skill scripts.

        With ``mutating`` (a run that may ask for confirmations) those MCP tools are offered too
        and wait for the person.

        Every result goes back to the model marked as data, its identifiers protected.

        Returns:
            The tool specs.
        """
        eng = self.engine
        chars = eng.settings.tool_result_chars

        def app_tool(name: str) -> Any:
            async def handle(args: dict[str, Any], _cid: str, span: Any) -> ToolResult:
                r = await toolbox.call(name, args)
                text_out = clip(await guard.tool_result(name, r.text), chars)
                if span is not None:
                    span.set(outcome="ok" if r.ok else "error", resultChars=len(text_out))
                return ToolResult(r.ok, guard.mark(guard.protect_text(text_out), f"tool:{name}"))

            return handle

        specs = [
            ToolSpec(
                name=t.name,
                description=t.description,
                schema=t.input_schema,
                run=app_tool(t.name),
                kind="app",
                server=toolbox.server_of(t.name),
                mutating=t.name in (mutating or set()),
                read_only=t.name not in (mutating or set()),
                return_schema=t.output_schema,
            )
            for t in read_only
        ]
        if loadable:

            async def load(args: dict[str, Any], _cid: str, span: Any) -> ToolResult:
                async def read(skill: str, path: str) -> str | None:
                    return await eng.store.skills.file(app.id, skill, path)

                ok, text = await load_skill(skills, args, read, runnable=bool(script_skills))
                if span is not None:
                    span.set(outcome="ok" if ok else "error", type="skill")
                return ToolResult(ok, text)

            specs.append(
                ToolSpec(
                    name=str(SKILL_LOAD["name"]),
                    description=str(SKILL_LOAD["description"]),
                    schema=cast(dict[str, Any], SKILL_LOAD["schema"]),
                    run=load,
                    read_only=True,
                )
            )
        if kbs:
            kd = knowledge_tool(kbs)
            cited: dict[str, int] = {}

            async def search(args: dict[str, Any], _cid: str, span: Any) -> ToolResult:
                text = await self._knowledge(
                    app, agent, kbs, args, guard, who, tags, groups, cited, result
                )
                if span is not None:
                    span.set(outcome="ok", type="knowledge")
                return ToolResult(True, text)

            specs.append(
                ToolSpec(
                    name=KNOWLEDGE_TOOL,
                    description=kd["description"],
                    schema=kd["schema"],
                    run=search,
                    kind="knowledge",
                )
            )

        def sandboxed(name: str, runner: Any) -> Any:
            async def handle(args: dict[str, Any], _cid: str, span: Any) -> ToolResult:
                out = await runner(args)
                text = out.text
                if out.charts:
                    text += "\n\nCharts are not delivered in server runs: " + NO_CHARTS
                text_out = clip(await guard.tool_result(name, text), chars)
                if span is not None:
                    span.set(**out.attrs)
                return ToolResult(out.ok, guard.mark(guard.protect_text(text_out), f"tool:{name}"))

            return handle

        if code_on:
            cd = code_tool()
            specs.append(
                ToolSpec(
                    name=CODE_TOOL,
                    description=cd["description"],
                    schema=cd["schema"],
                    run=sandboxed(
                        CODE_TOOL,
                        lambda args: eng.run_code(
                            args,
                            guard,
                            who,
                            tags,
                            agent=agent.name,
                            session_id=None,
                            turn_id=result.run_id,
                        ),
                    ),
                    kind="code",
                )
            )
        if script_skills:
            sd = skill_run_tool(script_skills)
            specs.append(
                ToolSpec(
                    name=SKILL_RUN,
                    description=sd["description"],
                    schema=sd["schema"],
                    run=sandboxed(
                        SKILL_RUN,
                        lambda args: eng.run_skill_script(
                            app.id,
                            args,
                            skills,
                            guard,
                            who,
                            tags,
                            agent=agent.name,
                            session_id=None,
                            turn_id=result.run_id,
                        ),
                    ),
                    kind="skill_run",
                )
            )
        return specs

    @staticmethod
    def _resumed(resume: Resume, rs: RunState) -> list[ModelMessage]:
        """The step a confirmation resumes.

        The model's calls with their stored arguments, then what had already run.

        Returns:
            Messages to append to the run's history.
        """
        rs.confirm = {"approve": True, "waitedMs": resume.waited_ms, "channel": resume.channel}
        for c in resume.calls:
            rs.args[c["toolUseId"]] = c["input"]
        for r in resume.ready:
            rs.ok[r["toolUseId"]] = r["ok"]
        out: list[ModelMessage] = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=x["name"], args=x.get("input") or {}, tool_call_id=x["toolUseId"]
                    )
                    for x in [*resume.ready, *resume.calls]
                ]
            )
        ]
        if resume.ready:
            out.append(
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name=r["name"],
                            content=r["text"] or ("ok" if r["ok"] else "error"),
                            tool_call_id=r["toolUseId"],
                        )
                        for r in resume.ready
                    ]
                )
            )
        return out

    def _cancelled(
        self, app: m.Application, guard: TurnGuard, run_id: str, model: str, resume: Resume
    ) -> RunResult:
        """A confirmation the person did not give: nothing runs and no model is called.

        Returns:
            The fixed answer.
        """
        tr = guard.trace
        for c in resume.calls:
            with tr.span(
                "tool",
                f"execute_tool {c['name']}",
                tool=c["name"],
                callId=c["toolUseId"],
                confirmed=False,
                confirmChannel=resume.channel,
                waitedMs=resume.waited_ms,
            ) as ts:
                tr.put(ts, args=c["input"])
                ts.set(outcome="rejected")
        tr.root.set(confirm="rejected", confirmChannel=resume.channel)
        text = message(app, "turn.refusal")
        return RunResult(run_id=run_id, output=text, text=text, model=model)

    @staticmethod
    def _client_tools(
        agent: AgentDef, extra: Sequence[ClientToolDef], result: RunResult
    ) -> list[ToolSpec]:
        """Custom client tools of a run: the agent's that do not wait, then the caller's.

        Nobody can answer in the middle of a run: a call is recorded for the caller (the
        response's ``clientToolCalls``) and the model reads ``ok``.

        Returns:
            Their tool specs.
        """

        def handler(name: str) -> Any:
            async def handle(  # ruff: ignore[unused-async] (a handler is awaited)
                args: dict[str, Any], cid: str, span: Any
            ) -> ToolResult:
                result.client_calls.append({"callId": cid, "name": name, "args": args})
                if span is not None:
                    span.set(outcome="ok", type="client")
                return ToolResult(True, custom.OK_TEXT)

            return handle

        own = [t for t in agent.client_tools.custom if not t.wait]
        return [
            ToolSpec(
                name=custom.model_name(t.name),
                description=t.description,
                schema=t.parameters,
                run=handler(t.name),
                kind="client",
            )
            for t in custom.merged(own, list(extra))
        ]

    async def _knowledge(
        self,
        app: m.Application,
        agent: AgentDef,
        kbs: list[m.KnowledgeBase],
        args: dict[str, Any],
        guard: TurnGuard,
        who: Who,
        tags: list[str],
        groups: list[str],
        cited: dict[str, int],
        result: RunResult,
    ) -> str:
        """``knowledge_search`` in a server run, with the run's groups.

        Returns:
            The passages, spotlighted one by one.
        """
        eng = self.engine
        query = str(args.get("query") or "").strip()
        name = args.get("kb")
        chosen = [b for b in kbs if b.name == name] if name else kbs
        if not query or not chosen:
            return "Give a query (and, optionally, one of the agent's knowledge bases)."
        filters = args.get("filters") if isinstance(args.get("filters"), dict) else None
        with retrieval_span(guard.trace, chosen, query) as span:
            found = await eng.knowledge.search(
                app,
                chosen,
                query,
                groups=set(groups),
                filters=filters,
                top_k=agent.knowledge.top_k if agent.knowledge else None,
                subject=who.subject,
                tags=tags,
                agent=agent.name,
                turn_id=result.run_id,
            )
            record_found(guard.trace, span, found)
        items = []
        for f in found:
            n = cited.get(f.chunk.id)
            if n is None:
                n = cited[f.chunk.id] = len(cited) + 1
                result.citations.append(eng.knowledge.citation(f, n))
            items.append((n, f, await guard.untrusted(f.chunk.text, f.source, tool=KNOWLEDGE_TOOL)))
        return mark_sources(guard.protect_text(format_results(query, items)), guard.mark)

    @staticmethod
    def _check_size(
        guard: TurnGuard,
        text: str | None,
        messages: list[dict[str, Any]] | None,
        attachments: list[Attachment],
    ) -> None:
        """The input size limit of a run (``input.size``).

        Raises:
            AppError: Over the limit while the control is enforced (413).
        """
        p = guard.policy
        limit = p.settings.input.max_run_chars if p.settings.input else None
        size = len(text or "") + sum(len(str(x.get("text") or "")) for x in messages or [])
        size += sum(len(a.text) for a in attachments)
        if not limit or size <= limit or not p.on("input.size"):
            return
        enforce = p.enforced("input.size")
        guard.record(
            "input.size",
            "too_long",
            "blocked" if enforce else "logged",
            details={"chars": size, "limit": limit},
            direction="input",
        )
        if enforce:
            raise AppError("The input is too large.", code="too_large", status=413)

    @staticmethod
    async def _input(
        guard: TurnGuard,
        text: str | None,
        messages: list[dict[str, Any]] | None,
        attachments: list[Attachment],
    ) -> list[UserPromptPart]:
        """The run's input as the model's first user message.

        The input of a server run usually comes from outside (an e-mail, a ticket): unless the
        policy says it is trusted, it is scanned for hidden instructions, its identifiers are
        protected and it is marked as data. Attachments always are.

        Returns:
            The user prompt part.
        """
        untrusted = guard.untrusted_input()

        async def data(body: str, source: str, *, always: bool = False) -> str:
            if not (untrusted or always):
                return guard.protect_text(body)
            body = await guard.untrusted(body, source)
            return guard.mark(guard.protect_text(body), source)

        parts: list[str] = [
            f"{x.get('role', 'user')}: {await data(str(x.get('text') or ''), 'input')}"
            for x in messages or []
        ]
        if text:
            parts.append(await data(text, "input"))
        for a in attachments:
            body = await data(a.text, f"attachment:{a.name}", always=True)
            parts.append(f"Attachment {a.name} ({a.media_type}):\n{body}")
        return [UserPromptPart(content="\n\n".join(parts))]

    async def _record(
        self,
        who: Who,
        tags: list[str],
        agent: AgentDef,
        name: str,
        response: ModelResponse,
        result: RunResult,
        dedup_key: str | None = None,
    ) -> None:
        """Record one answered call with the model that answered it (once per ``dedup_key``)."""
        result.model = name
        u = response.usage
        inp, out = u.input_tokens or 0, u.output_tokens or 0
        read, write = u.cache_read_tokens or 0, u.cache_write_tokens or 0
        cost = call_cost_micro_usd(name, u)
        result.input_tokens += inp
        result.output_tokens += out
        result.cache_read_tokens += read
        result.cache_write_tokens += write
        result.cost_micro_usd += cost
        await self.engine.store.usage.record(
            who,
            tags,
            dedup_key=dedup_key,
            agent_name=agent.name,
            kind="llm",
            model=bare(result.model),
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=read,
            cache_write_tokens=write,
            cost_micro_usd=cost,
            tool_calls=sum(1 for p in response.parts if isinstance(p, ToolCallPart)),
        )

    async def _store(
        self,
        who: Who,
        agent: AgentDef,
        tags: list[str],
        text: str | None,
        messages: list[dict[str, Any]] | None,
        result: RunResult,
        guard: TurnGuard | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """Keep the run as a conversation of the subject (a new one, or one it goes on).

        Returns:
            The conversation id.
        """
        store = self.engine.store
        title = (text or "Server run")[:80]
        sess = await store.sessions.get(who, conversation_id) if conversation_id else None
        if sess is not None:
            thread = await store.sessions.thread(sess.id)
        else:
            sess, thread = await store.sessions.create(who, agent.name, agent.version, title, tags)
        user_text = text or "\n".join(str(x.get("text") or "") for x in messages or [])
        answer = (
            json.dumps(result.output, ensure_ascii=False)
            if not isinstance(result.output, str)
            else result.output
        )
        for role, body in (("user", user_text), ("assistant", answer)):
            if guard is not None:
                body = guard.for_storage([], body)[1]
            await store.messages.add(
                who,
                sess.id,
                thread.id,
                role=role,
                content=[{"text": body}],
                text=body,
                events=None,
                turn_id=result.run_id,
                interrupted=False,
            )
        return sess.id


class StepText:
    """The text a run streams, set apart as the whole answer is (``_answer_text``).

    Each step's text follows the one before it after ``STEP_SEPARATOR``; within a step, a text
    part that starts after another kind of part (a built-in tool's call) is set apart the same
    way, as ``ModelResponse.text`` joins them.
    """

    def __init__(self) -> None:
        """Before the first step."""
        self.said = False
        """Some text was streamed already."""
        self.first = True
        """No text yet in this step."""
        self.gap = False
        """Another kind of part started after this step's text."""
        self.pending = False
        """The next text starts a new part after a gap."""

    def step(self) -> None:
        """A new step (model request) begins."""
        self.first, self.gap, self.pending = True, False, False

    def text(self, ev: Any) -> str | None:
        """The text an event adds, with its separator.

        Returns:
            The text, or None for an event without any.
        """
        if isinstance(ev, PartStartEvent):
            if not isinstance(ev.part, TextPart):
                self.gap = self.gap or not self.first
                return None
            self.pending, self.gap = self.pending or self.gap, False
            delta = ev.part.content
        elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
            delta = ev.delta.content_delta
        else:
            return None
        if not delta:
            return None
        if (self.first and self.said) or self.pending:
            delta = STEP_SEPARATOR + delta
        self.first, self.pending, self.said = False, False, True
        return delta


def _answer_text(messages: list[ModelMessage], output: str) -> str:
    """A run's whole answer: what each step said before its tool calls, then its output.

    A stream sends the text of each step as it is written (``STEP_SEPARATOR`` between steps), so
    a whole answer says the same, not only the last step's text. A response without tool calls
    that the run did not end on (a draft a capability refined) is not part of it.

    Returns:
        The text.
    """
    steps = [
        m.text or ""
        for m in messages
        if isinstance(m, ModelResponse)
        and any(isinstance(p, ToolCallPart) and p.tool_name != OUTPUT_TOOL for p in m.parts)
    ]
    return STEP_SEPARATOR.join([t for t in (*steps, output) if t])


def _ready(messages: list[ModelMessage], ok: dict[str, bool]) -> list[dict[str, Any]]:
    """What ran in the step that ended waiting for a confirmation.

    Returns:
        ``toolUseId``, ``name``, ``input``, ``ok`` and ``text`` of each call that has a result.
    """
    last = max((i for i, x in enumerate(messages) if isinstance(x, ModelResponse)), default=-1)
    if last < 0:
        return []
    step = messages[last]
    assert isinstance(step, ModelResponse)  # ruff: ignore[assert] (found above)
    calls = {p.tool_call_id: p for p in step.parts if isinstance(p, ToolCallPart)}
    out = []
    returns = [
        p
        for msg in messages[last + 1 :]
        if isinstance(msg, ModelRequest)
        for p in msg.parts
        if isinstance(p, ToolReturnPart) and p.tool_call_id in calls
    ]
    for p in returns:
        call = calls[p.tool_call_id]
        out.append({
            "toolUseId": p.tool_call_id,
            "name": p.tool_name,
            "input": call.args_as_dict(),
            "ok": ok.get(p.tool_call_id, True),
            "text": str(p.content),
        })
    return out


def check_channel(agent: AgentDef, grants: Any, channel: str) -> None:
    """Whether the agent may be reached this way.

    Raises:
        Forbidden: The agent does not allow the channel (``channel``) or is not published to A2A
            (``a2a``).
        NotGranted: The application is not granted the channel.
    """
    if channel == "a2a":
        if not agent.a2a:
            raise Forbidden("This agent is not published to A2A.", code="a2a")
        if not grants.channel("a2a"):
            raise NotGranted("channel", "A2A is not granted to this application.")
        return
    if channel not in agent.channels:
        what = {"server": "from the server", "browser": "in a browser session"}.get(
            channel, f"over {channel}"
        )
        raise Forbidden(f"This agent cannot be run {what}.", code="channel")
    if not grants.channel(channel):
        what = {"server": "Server runs are", "browser": "Browser sessions are"}.get(
            channel, f"The {channel} channel is"
        )
        raise NotGranted("channel", f"{what} not granted to this application.")
