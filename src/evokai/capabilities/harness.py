"""Evokai's builders for the harness capabilities that need Evokai's own objects.

A harness capability that takes a function or a store (an answerer, a guard function, reminders,
a loop handler, a store) is built here with Evokai's: the person answers a question the way they
answer ``ui_ask``, the guardrails are Evokai's guard, a refused or looping call is a guard event,
what a capability does is in the trace. The stores are in ``evokai.capabilities.stores``.

Imported only when a turn or run builds an enabled capability (listing the catalog imports
nothing): ``factories`` returns the builders the engine passes to ``evokai.capabilities.build``.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapperCapability
from pydantic_ai.toolsets import AbstractToolset

from evokai import capabilities as caps
from evokai.engine.capability_tools import CapabilityTools
from evokai.engine.state import RunState, Scope, StopRun
from evokai.guard import tools as tp

__all__ = ["CapabilityEvents", "CapabilityTools", "Context", "context", "factories"]

log = logging.getLogger("evokai.capabilities")

MAX_REFINE_ROUNDS = 3
"""The most review rounds ``refine_output`` may ask for in one run (each one is a model call)."""


@dataclass
class Context:
    """What a builder may use: the engine, the agent, the run and who it is for."""

    engine: Any
    agent: Any
    rs: RunState
    scope: Scope
    owner: Any = None
    """``evokai.capabilities.stores.Owner`` of the person (None without a database)."""
    conversation: Any = None
    """The ``Owner`` of the conversation, when it is kept by Evokai (None: nothing at rest)."""
    memory_active: bool = False
    """Whether the person's memory is active (Evokai storage, mode and consent)."""
    loaded: dict[str, Any] = field(default_factory=dict)
    """What ``context`` loaded for a capability (a plan, a step log)."""
    secrets: dict[str, dict[str, str]] = field(default_factory=dict)
    """The application's credentials, by capability (decrypted, never logged)."""

    @property
    def turn(self) -> bool:
        """Whether a person is on the other side (a turn), not a server run.

        Returns:
            True in a turn.
        """
        return self.rs.emit is not None


def trace_event(rs: RunState, capability: str, what: str, **attrs: Any) -> None:
    """A capability's decision in the turn's trace (no content, only what happened)."""
    rs.trace.event("capability", f"{capability} {what}", capability=capability, **attrs)


# ask_user


def ask_user(ctx: Context, config: dict[str, Any]) -> Any:
    """``AskUser`` mapped onto Evokai's ``ask`` turn end (``evokai.capabilities.ask``).

    Returns:
        The capability.
    """
    from datetime import timedelta

    from evokai.capabilities.ask import EvokaiAskUser, Queue
    from evokai.capabilities.stores import Owner

    can_ask = ctx.turn and bool(ctx.scope.grants.client("ask"))
    queue = None
    if can_ask and ctx.owner is not None and ctx.scope.session_id:
        # The questions after the first wait with the conversation: a day with Evokai storage,
        # an hour (like a pending confirmation) with external storage.
        queue = Queue(
            Owner(ctx.owner.repo, ctx.scope.app.id, ctx.scope.who.subject, ctx.scope.session_id),
            ttl=timedelta(days=1) if ctx.scope.storage == "evokai" else timedelta(hours=1),
        )
    return caps.construct(
        "ask_user",
        config,
        model_of=_no_model,
        provided={"answerer": None, "can_ask": can_ask, "queue": queue},
        cls=EvokaiAskUser,
    )


# guardrails


def input_guardrail(_ctx: Context, config: dict[str, Any]) -> Any:
    """``InputGuardrail`` backed by Evokai's guard (size, hidden text, injection, scope).

    A prompt the turn or run already checked is not checked twice; any other prompt that reaches
    the model in the run is.

    Returns:
        The capability.
    """
    from pydantic_ai_harness.guardrails import GuardrailResult

    async def guard(run: RunContext[RunState], prompt: str) -> GuardrailResult:
        g = run.deps.guard
        if any(c and c in prompt for c in g.checked):
            return GuardrailResult.allow()
        decision = await g.check_input(prompt)
        if decision.blocked:
            trace_event(run.deps, "input_guardrail", "blocked", control=decision.blocked)
            return GuardrailResult.block(decision.refusal or "Blocked by the security policy.")
        if decision.text != prompt:
            return GuardrailResult.replace(decision.text)
        return GuardrailResult.allow()

    return caps.construct("input_guardrail", config, model_of=_no_model, provided={"guard": guard})


def output_guardrail(_ctx: Context, config: dict[str, Any]) -> Any:
    """``OutputGuardrail`` backed by Evokai's output rules (secrets, links, personal data, leaks).

    A turn's answer is filtered as it streams; this applies the same filter to a server run's
    final answer before it is returned (a text answer; a structured one keeps the final check).

    Returns:
        The capability.
    """
    from pydantic_ai_harness.guardrails import GuardrailResult

    def guard(run: RunContext[RunState], output: Any) -> GuardrailResult:
        if not isinstance(output, str) or run.deps.emit is not None:
            return GuardrailResult.allow()
        g = run.deps.guard
        f = g.output_filter()
        text = f.push(output) + f.flush()
        g.after_output(f)
        if f.blocked:
            trace_event(run.deps, "output_guardrail", "blocked")
            return GuardrailResult.block("The answer was withheld by the security policy.")
        if text != output:
            return GuardrailResult.replace(text)
        return GuardrailResult.allow()

    return caps.construct("output_guardrail", config, model_of=_no_model, provided={"guard": guard})


def tool_guardrail(_ctx: Context, config: dict[str, Any]) -> Any:
    """``ToolGuardrail`` backed by Evokai's argument and egress checks, on every tool call.

    Evokai's own tools and the capabilities' tools are already checked once; this adds the
    argument (``tools.args``) and egress (``tools.egress``) checks on every call, whatever brought
    the tool, and a forced confirmation from the tool policy.

    Returns:
        The capability.
    """
    from pydantic_ai_harness.guardrails import GuardrailResult

    def guard(run: RunContext[RunState], call: Any) -> GuardrailResult:
        g = run.deps.guard
        tools = g.policy.settings.tools
        if tools is None:
            return GuardrailResult.allow()
        spec = run.deps.tools.get(call.name)
        checks = []
        if g.policy.on("tools.args"):
            checks.append((
                "tools.args",
                tp.check_args(dict(call.args), spec.schema if spec else None),
            ))
        if g.policy.on("tools.egress"):
            checks.append(("tools.egress", tp.check_egress(call.name, dict(call.args), tools)))
        for control, d in checks:
            if d.allowed:
                continue
            enforce = g.policy.enforced(control)
            g.record(
                control,
                d.rule,
                "blocked" if enforce else "logged",
                details={"reason": d.reason[:300]},
                tool=call.name,
                direction="tool",
            )
            if enforce:
                return GuardrailResult.block(f"Blocked by the security policy: {d.reason}.")
        return GuardrailResult.allow()

    return caps.construct("tool_guardrail", config, model_of=_no_model, provided={"guard": guard})


# reminders, budgets, loops, output limits


def system_reminders(_ctx: Context, config: dict[str, Any]) -> Any:
    """``SystemReminders`` from the agent's reminder texts (written by an admin, trusted).

    Returns:
        The capability.

    Raises:
        CapabilityError: A reminder without content.
    """
    from pydantic_ai_harness.system_reminders import Reminder

    reminders = []
    for r in config.get("reminders") or []:
        if not isinstance(r, dict) or not str(r.get("content") or "").strip():
            raise caps.CapabilityError("system_reminders: every reminder needs a content")
        reminders.append(
            Reminder[RunState](
                content=str(r["content"])[:2000],
                interval=int(r.get("interval") or 1),
                first_after=r.get("first_after"),
                max_fires=r.get("max_fires"),
                tag=None,
                scope=r.get("scope") or "request",
                skip_unchanged=bool(r.get("skip_unchanged")),
            )
        )
    rest = {k: v for k, v in config.items() if k != "reminders"}
    return caps.construct(
        "system_reminders",
        rest,
        model_of=_no_model,
        provided={"reminders": reminders},
        own=("reminders",),
    )


def tool_budgets(ctx: Context, config: dict[str, Any]) -> Any:
    """``ToolBudgets`` within Evokai's tool-call cap, a refused call recorded as a guard event.

    The run's total is never above the turn's cap (the agent's ``max_tool_calls_per_turn``,
    lowered by the policy); a refusal answers the model and is a ``turn.budget`` event in the
    security log and the trace.

    Returns:
        The capability.
    """
    from pydantic_ai_harness.tool_budgets import ToolBudgets

    ceiling = ctx.rs.guard.max_calls(ctx.agent.max_tool_calls_per_turn)
    total = config.get("total")
    total = min(int(total), ceiling) if total else ceiling

    class EvokaiToolBudgets(ToolBudgets[RunState]):
        def _refuse(self, ctx: RunContext[RunState], call: Any, reason: Any, message: str) -> Any:
            g = ctx.deps.guard
            g.record(
                "turn.budget",
                f"tool_budget_{reason}",
                "blocked",
                details={"reason": str(reason)},
                tool=call.tool_name,
                direction="tool",
            )
            return super()._refuse(ctx, call, reason, message)

    args: dict[str, Any] = {**config, "total": total}
    repeats = config.get("refuse_repeats")
    if repeats is not None:
        args["refuse_repeats"] = frozenset(str(x) for x in repeats)
    return caps.construct("tool_budgets", args, model_of=_no_model, cls=EvokaiToolBudgets)


def detect_loops(ctx: Context, config: dict[str, Any]) -> Any:
    """``DetectLoops`` with Evokai's events: a loop is a ``tools.loop`` guard event.

    ``action: nudge`` tells the model and goes on; ``action: stop`` ends a turn as ``max_steps``
    (the person reads the usual message) and fails a server run. In a turn, text the person
    already read is never taken back: only thinking and tool arguments are checked as they stream.

    Returns:
        The capability.

    Raises:
        CapabilityError: An unknown action.
    """
    from evokai.i18n import message

    action = str(config.get("action") or "nudge")
    if action not in ("nudge", "stop"):
        raise caps.CapabilityError("detect_loops: action is nudge or stop")
    rs, scope = ctx.rs, ctx.scope

    def on_loop(run: RunContext[RunState], loop: Any) -> None:
        st = run.deps
        st.guard.record(
            "tools.loop",
            f"loop_{loop.pattern}",
            "blocked" if action == "stop" else "logged",
            details={"count": loop.count, "window": loop.window},
            tool=(loop.tool_names or [None])[0],
            direction="tool",
        )
        if action == "stop":
            if st.emit is not None:
                st.emit({"type": "text", "delta": "\n\n" + message(scope.app, "turn.maxSteps")})
                raise StopRun("max_steps")
            from evokai.sessions.runs import InvalidOutput

            raise InvalidOutput("The agent repeated itself without making progress.")
        from pydantic_ai.messages import SystemPromptPart

        run.enqueue(SystemPromptPart(loop.message))

    from pydantic_ai_harness.detect_loops import DetectLoops

    class EvokaiDetectLoops(DetectLoops[RunState]):
        async def wrap_model_request(
            self, ctx: RunContext[RunState], *, request_context: Any, handler: Any
        ) -> Any:
            # The stream is watched in a task group: what ends the run (a cap, the loop's own
            # stop) comes out of it as itself, so the turn ends with its own reason.
            try:
                return await super().wrap_model_request(
                    ctx, request_context=request_context, handler=handler
                )
            except BaseExceptionGroup as group:
                inner = _single(group)
                if inner is None:
                    raise
                raise inner from None

    args = {k: v for k, v in config.items() if k != "action"}
    if rs.emit is not None:
        args.setdefault("stream_channels", ["thinking", "tool_args"])
        args["stream_channels"] = [c for c in args["stream_channels"] if c != "text"]
    return caps.construct(
        "detect_loops",
        args,
        model_of=_no_model,
        provided={"action": on_loop},
        own=("action",),
        cls=EvokaiDetectLoops,
    )


def _single(group: BaseExceptionGroup) -> BaseException | None:
    """The one exception inside nested groups, or None when there are several.

    Returns:
        The exception.
    """
    inner: BaseException = group
    while isinstance(inner, BaseExceptionGroup):
        if len(inner.exceptions) != 1:
            return None
        inner = inner.exceptions[0]
    return inner


def tool_output_limits(ctx: Context, config: dict[str, Any]) -> Any:
    """``ToolOutputLimits`` with the overflow in Evokai's database, never in local files.

    ``over`` (characters, the turn's tool-result limit by default) and ``action`` (``spill``: kept
    and readable on demand; ``truncate``). A spilled result is kept with the conversation (Evokai
    storage) and goes with it; with external storage it lives only for the turn.

    Returns:
        The capability.

    Raises:
        CapabilityError: An unknown action.
    """
    from pydantic_ai_harness.tool_output_limits import Band, Spill, Truncate

    over = int(config.get("over") or ctx.rs.settings.tool_result_chars)
    action = str(config.get("action") or "spill")
    if action not in ("spill", "truncate"):
        raise caps.CapabilityError("tool_output_limits: action is spill or truncate")
    band = Band(over=over, action=Spill(then=Truncate()) if action == "spill" else Truncate())
    args = {k: v for k, v in config.items() if k not in ("over", "action")}
    store = _overflow_store(ctx)
    return caps.construct(
        "tool_output_limits",
        args,
        model_of=_no_model,
        provided={"bands": [band], "store": store},
        own=("over", "action"),
    )


def _overflow_store(ctx: Context) -> Any:
    if ctx.conversation is not None:
        from evokai.capabilities.stores import DbOverflowStore

        return DbOverflowStore(ctx.conversation)
    return MemoryOverflow()


# stores


def memory(ctx: Context, config: dict[str, Any]) -> Any:
    """``Memory`` (the agent's memory files) per person and agent, in Evokai's database.

    Only while the person's memory is active, as Evokai's own memory: Evokai storage, the
    application's memory mode and the person's consent. Every write passes the guard's
    ``memory.write`` control; files expire with the application's memory retention; a withdrawn
    consent or an erasure deletes them. A separate store from Evokai's memories (facts with kinds,
    consolidation and recall): the harness tools read and edit whole files with versions.

    Returns:
        The capability.

    Raises:
        CapabilityError: The person's memory is not active.
    """
    from evokai.capabilities.stores import DbMemoryStore
    from evokai.memory.service import ttl_days

    if not ctx.memory_active or ctx.owner is None:
        raise caps.CapabilityError(
            "memory needs Evokai storage and the person's memory turned on (mode and consent)"
        )
    guard = ctx.rs.guard
    store = DbMemoryStore(
        ctx.owner,
        ctx.agent.name,
        ttl_days=ttl_days(ctx.scope.app),
        allow=guard.memory,
    )
    return caps.construct(
        "memory",
        config,
        model_of=_no_model,
        provided={"store": store, "agent_name": ctx.agent.name},
    )


def planning(ctx: Context, config: dict[str, Any]) -> Any:
    """``Planning`` with the plan kept per conversation (Evokai storage; else for the turn).

    Returns:
        The capability.
    """
    from evokai.capabilities.stores import plan_store

    store = plan_store(ctx.conversation, ctx.agent.name, ctx.loaded.get("planning"))
    return caps.construct("planning", config, model_of=_no_model, provided={"store": store})


def step_persistence(ctx: Context, config: dict[str, Any]) -> Any:
    """``StepPersistence`` with the step log kept per conversation (Evokai storage; else the turn).

    Returns:
        The capability.
    """
    from pydantic_ai_harness.step_persistence import InMemoryStepStore

    store = ctx.loaded.get("step_persistence") or InMemoryStepStore()
    return caps.construct(
        "step_persistence",
        config,
        model_of=_no_model,
        provided={
            "store": store,
            "agent_name": ctx.agent.name,
            "metadata": {"application": ctx.scope.app.id},
        },
    )


def conversation_search(ctx: Context, config: dict[str, Any]) -> Any:
    """``ConversationSearch`` over the person's own conversations kept by Evokai.

    The source holds only this person's conversations of this application, so a search reaches
    nothing else (``scope: all`` over an isolated source); its results are untrusted content.

    Returns:
        The capability.

    Raises:
        CapabilityError: The conversations are not kept by Evokai.
    """
    from evokai.capabilities.stores import EvokaiHistorySource

    storage = ctx.scope.storage or getattr(ctx.scope.app, "storage_mode", None)
    if storage != "evokai" or ctx.engine is None:
        raise caps.CapabilityError("conversation_search needs conversations kept by Evokai")
    source = EvokaiHistorySource(ctx.engine.store, ctx.scope.app, ctx.scope.who)
    return caps.construct(
        "conversation_search",
        {k: v for k, v in config.items() if k != "scope"},
        model_of=_no_model,
        provided={"source": source, "scope": "all"},
    )


SPEND_SCOPES = ("person", "application", "conversation")
"""Whom a ``spend_limits`` budget counts for."""

SPEND_WINDOWS = ("day", "month", "run", "conversation", "total")
"""The periods a ``spend_limits`` budget counts over."""


def spend_limits(ctx: Context, config: dict[str, Any]) -> Any:
    """``SpendLimits`` with budgets set as shares of Evokai's caps or in tokens, never in money.

    ``budgets: [{name, window, scope, share, tokens, warn_at}]``: ``window`` ``day`` or ``month``
    (``share`` is a fraction of the application's cap of that scope and period: a person's
    ``subject`` cap, or the application's), or ``run``, ``conversation``, ``total`` (tokens only);
    ``scope`` ``person`` (default), ``application`` or ``conversation``. The spend is priced with
    Evokai's prices and kept in its database; Evokai's own caps still apply before every call, and
    no amount is ever shown to a person (no spend tools for the model either).

    Returns:
        The capability.

    Raises:
        CapabilityError: A budget in money, or a share of a cap the application does not have.
    """
    from pydantic_ai_harness.spend_limits import Budget, SpendLimits

    from evokai.capabilities.stores import DbSpendStore
    from evokai.core.caps import cap_usd
    from evokai.i18n import message
    from evokai.llm.prices import call_cost_micro_usd

    if ctx.engine is None:
        raise caps.CapabilityError("spend_limits needs Evokai's database")
    app, who, rs = ctx.scope.app, ctx.scope.who, ctx.rs
    budgets = []
    for b in config.get("budgets") or []:
        if not isinstance(b, dict) or "usd" in b:
            raise caps.CapabilityError(
                "spend_limits: a budget is a share of a cap or tokens, never an amount"
            )
        window = str(b.get("window") or "day")
        if window not in SPEND_WINDOWS:
            raise caps.CapabilityError(f"spend_limits: window is one of {', '.join(SPEND_WINDOWS)}")
        scope = str(b.get("scope") or "person")
        if scope not in SPEND_SCOPES:
            raise caps.CapabilityError(f"spend_limits: scope is one of {', '.join(SPEND_SCOPES)}")
        usd = None
        if b.get("share") is not None:
            cap = (
                cap_usd(app, "application" if scope == "application" else "subject", window)
                if window in ("day", "month")
                else None
            )
            if cap is None:
                raise caps.CapabilityError(
                    f"spend_limits: no {window} cap for the {scope} to take a share of"
                )
            usd = Decimal(str(round(cap * min(max(float(b["share"]), 0.0), 1.0), 6)))
        key = {
            "person": f"{app.id}:{who.subject}",
            "application": str(app.id),
            "conversation": str(ctx.scope.session_id or rs.run_id),
        }[scope]
        budgets.append(
            Budget[RunState](
                usd=usd,
                tokens=b.get("tokens"),
                window=cast(Any, window),
                scope=lambda _ctx, key=key: key,
                warn_at=b.get("warn_at"),
                name=str(b.get("name") or f"{scope}-{window}"),
            )
        )

    class EvokaiSpendLimits(SpendLimits[RunState]):
        def _refuse(self, budget: Any, ctx: RunContext[RunState], detail: str) -> None:
            # A spent budget ends the turn like a cap: the person reads why, never an amount.
            st = ctx.deps
            st.guard.record(
                "turn.budget", f"spend_{budget.window}", "blocked", details={"budget": budget.name}
            )
            text = message(app, "limit.agentBudget")
            if st.emit is not None:
                st.emit({"type": "error", "code": "limit", "limit": "agent", "message": text})
                if st.say is not None:
                    st.say(text)
                raise StopRun("limit")
            from evokai.sessions.runs import LimitReached

            del detail
            raise LimitReached(text)

    def price(response: Any) -> Decimal | None:
        model = rs.answered or (rs.models[0][0] if rs.models else "")
        return Decimal(call_cost_micro_usd(model, response.usage)) / Decimal(1_000_000)

    return caps.construct(
        "spend_limits",
        {k: v for k, v in config.items() if k not in ("budgets", "expose_tools")},
        model_of=_no_model,
        provided={
            "budgets": budgets,
            "store": DbSpendStore(ctx.engine.store.capabilities, app.id),
            "price": price,
            "expose_tools": False,
        },
        own=("budgets",),
        cls=EvokaiSpendLimits,
    )


@dataclass
class MemoryOverflow:
    """Spilled tool results kept only for the turn or run (nothing at rest)."""

    data: dict[str, bytes] = field(default_factory=dict)

    async def write(self, key: str, data: bytes) -> str:
        """Keep a result.

        Returns:
            Its handle.
        """
        self.data[key] = data
        return key

    async def read(self, handle: str) -> bytes:
        """A result kept before.

        Returns:
            Its bytes (an unknown handle raises ``KeyError``).
        """
        return self.data[handle]


# output review, argument repair, cache busts


def refine_output(ctx: Context, config: dict[str, Any]) -> Any:
    """``RefineOutput`` for server runs, at most three rounds (each one a model call, capped).

    A turn's answer reaches the person while it is written, so it cannot be revised after it was
    shown: in a turn the capability is left out.

    Returns:
        The capability.

    Raises:
        CapabilityError: In a turn.
    """
    if ctx.turn:
        raise caps.CapabilityError(
            "refine_output revises a server run's answer; a turn's answer is shown as it is written"
        )
    rounds = min(int(config.get("rounds") or 1), MAX_REFINE_ROUNDS)
    return caps.construct("refine_output", {**config, "rounds": rounds}, model_of=_no_model)


def repair_tool_arguments(_ctx: Context, config: dict[str, Any]) -> Any:
    """``RepairToolArguments``, with each repair in the trace.

    Returns:
        The capability.
    """
    from pydantic_ai_harness.repair_tool_arguments import RepairToolArguments

    class EvokaiRepairToolArguments(RepairToolArguments[RunState]):
        async def before_tool_validate(
            self, ctx: RunContext[RunState], *, call: Any, tool_def: Any, args: Any
        ) -> Any:
            out = await super().before_tool_validate(ctx, call=call, tool_def=tool_def, args=args)
            if out is not args:
                trace_event(ctx.deps, "repair_tool_arguments", "repaired", tool=call.tool_name)
            return out

    return caps.construct(
        "repair_tool_arguments", config, model_of=_no_model, cls=EvokaiRepairToolArguments
    )


_CACHE_MARKS: Any = None
"""The cache marks of every conversation in this process (``WarnOnCacheBusts`` keeps them on its
instance, and Evokai builds one per turn)."""


def warn_on_cache_busts(ctx: Context, config: dict[str, Any]) -> Any:
    """``WarnOnCacheBusts`` judged across the turns of a conversation, its warnings in the trace.

    A collapse of the prompt cache between requests (or a long request with caching off) is a
    ``capability`` event of the turn's trace and a log line; nothing is shown to the person.

    Returns:
        The capability.
    """
    from pydantic_ai_harness.warn_on_cache_busts import CacheBustWarning, WarnOnCacheBusts
    from pydantic_ai_harness.warn_on_cache_busts._capability import CacheNotEnabledWarning

    global _CACHE_MARKS
    conversation = ctx.scope.session_id

    class EvokaiWarnOnCacheBusts(WarnOnCacheBusts[RunState]):
        async def for_run(self, ctx: RunContext[RunState]) -> Any:
            bound: Any = await super().for_run(ctx)
            if conversation is not None:
                bound._state = type(bound._state)(
                    bound._detector(conversation_id=conversation, run_id=ctx.run_id)
                )
            return bound

        async def after_model_request(
            self, ctx: RunContext[RunState], *, request_context: Any, response: Any
        ) -> Any:
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                out = await super().after_model_request(
                    ctx, request_context=request_context, response=response
                )
            for w in seen:
                if isinstance(w.message, CacheBustWarning):
                    m = w.message
                    trace_event(
                        ctx.deps,
                        "warn_on_cache_busts",
                        "cache bust",
                        reason=m.reason,
                        establishedTokens=m.established_tokens,
                        cacheReadTokens=m.cache_read_tokens,
                        missedTokens=m.missed_tokens,
                    )
                    log.warning(
                        "prompt_cache_bust",
                        extra={"runId": ctx.deps.run_id, "missedTokens": m.missed_tokens},
                    )
                elif isinstance(w.message, CacheNotEnabledWarning):
                    trace_event(ctx.deps, "warn_on_cache_busts", "cache not enabled")
            return out

    cap: Any = caps.construct(
        "warn_on_cache_busts", config, model_of=_no_model, cls=EvokaiWarnOnCacheBusts
    )
    if _CACHE_MARKS is None:
        _CACHE_MARKS = cap._store
    cap._store = _CACHE_MARKS
    return cap


@dataclass
class CapabilityEvents(AbstractCapability[RunState]):
    """Every event a harness capability emits, as a ``capability`` event of the trace.

    A reminder fired, a plan changed, spend recorded: its name and numbers, never its text.
    """

    def listens_to(self, event: Any) -> bool:
        """Only capability events.

        Returns:
            Whether it is one.
        """
        from pydantic_ai import CapabilityEvent

        return isinstance(event, CapabilityEvent)

    async def on_event(self, ctx: RunContext[RunState], *, event: Any) -> None:
        """Record the event."""
        from pydantic_ai import CapabilityEvent

        if not isinstance(event, CapabilityEvent):
            return
        attrs: dict[str, Any] = {
            k: v
            for k, v in vars(event).items()
            if isinstance(v, (bool, int, float)) and not k.startswith("_")
        }
        if event.capability_id:
            attrs["capability"] = str(event.capability_id)
        ctx.deps.trace.event("capability", str(event.kind).replace(".", " "), **attrs)


async def context(
    engine: Any, agent: Any, rs: RunState, scope: Scope, enabled: set[str]
) -> Context:
    """What the builders of a turn or run need, loaded once (only for what is enabled).

    Returns:
        The context.
    """
    from evokai.capabilities import credentials
    from evokai.capabilities.stores import Owner, load_plan, step_store

    ctx = Context(engine=engine, agent=agent, rs=rs, scope=scope)
    repo = getattr(getattr(engine, "store", None), "capabilities", None)
    if repo is None:
        return ctx
    ctx.owner = Owner(repo, scope.app.id, scope.who.subject)
    if scope.storage == "evokai" and scope.session_id:
        ctx.conversation = Owner(repo, scope.app.id, scope.who.subject, scope.session_id)
    if "memory" in enabled and engine.memory is not None:
        storage = scope.storage or getattr(scope.app, "storage_mode", None)
        if storage == "evokai":
            ctx.memory_active = (await engine.memory.state(scope.app, scope.who)).active
    if "planning" in enabled and ctx.conversation is not None:
        ctx.loaded["planning"] = await load_plan(ctx.conversation, agent.name)
    if "step_persistence" in enabled:
        ctx.loaded["step_persistence"] = await step_store(ctx.conversation)
    for c in enabled:
        provider = credentials.PERSONAL.get(c)
        if provider and f"connection:{provider}" not in ctx.loaded:
            ctx.loaded[f"connection:{provider}"] = await _connection(engine, scope, provider)
    if any(credentials.provider_of(c) for c in enabled) and engine.keyring is not None:
        kept = await credentials.stored(engine.store, engine.keyring, scope.app.id)
        for c in enabled:
            provider = credentials.provider_of(c)
            if provider is not None:
                ctx.secrets[provider] = credentials.resolve(provider, kept.get(provider))
    return ctx


async def _connection(engine: Any, scope: Scope, provider: str) -> dict[str, Any] | None:
    """The signed-in person's connection to a provider, for an integration acting as them.

    Returns:
        ``{connected, token, title, server}``, or None when the application has no such
        connection configured and granted, or nobody is signed in.
    """
    service = getattr(getattr(engine, "mcp", None), "connections", None)
    subject = getattr(scope.who, "subject", None)
    if service is None or not subject or not scope.grants.connection(provider):
        return None
    return await service.person(scope.app, subject, provider)


def _no_model(name: str) -> Any:
    raise caps.CapabilityError(f"no model setting here: {name}")


BUILDERS: dict[str, Callable[[Context, dict[str, Any]], Any]] = {
    "ask_user": ask_user,
    "input_guardrail": input_guardrail,
    "output_guardrail": output_guardrail,
    "tool_guardrail": tool_guardrail,
    "system_reminders": system_reminders,
    "tool_budgets": tool_budgets,
    "detect_loops": detect_loops,
    "tool_output_limits": tool_output_limits,
    "refine_output": refine_output,
    "repair_tool_arguments": repair_tool_arguments,
    "warn_on_cache_busts": warn_on_cache_busts,
    "memory": memory,
    "planning": planning,
    "step_persistence": step_persistence,
    "conversation_search": conversation_search,
    "spend_limits": spend_limits,
}
"""Evokai's builder of each harness capability that needs Evokai's objects."""


def with_credentials(ctx: Context, capability: str, config: dict[str, Any]) -> Any:
    """A capability that needs a provider's keys, built with the application's credentials.

    The application's own values come first, the deployment's environment variables after; a
    missing one leaves the capability out of the run (``capability_unavailable``), and the catalog
    shows it as missing.

    Returns:
        The capability.

    Raises:
        CapabilityError: A credential is missing or the provider's client refused it.
    """
    from evokai.capabilities import credentials

    try:
        who = credentials.acts_as(capability, config)
    except ValueError as e:
        raise caps.CapabilityError(str(e)) from e
    config = {k: v for k, v in config.items() if k != "acts_as"}
    if who == "person":
        return _as_person(ctx, capability, config)
    provider = credentials.provider_of(capability)
    values = ctx.secrets.get(provider or "") or (
        credentials.resolve(provider, None) if provider else {}
    )
    try:
        provided = credentials.kwargs(capability, values)
    except Exception as e:
        raise caps.CapabilityError(f"{capability}: {type(e).__name__}: {e}") from e
    cap = caps.construct(
        capability,
        {k: v for k, v in config.items() if k not in provided},
        model_of=_no_model,
        provided=provided,
    )
    if who == "application":
        # A shared account: every tool call is traced as such and every write waits for the
        # person (no capability may approve it).
        if ctx.rs is not None:
            root = ctx.rs.trace.root
            root.set(actsAsApplication=[*root.attrs.get("actsAsApplication", []), capability])
        return Tagged(wrapped=cap, tags={"evokai_capability": capability, "evokai_acts_as": who})
    return cap


def _as_person(ctx: Context, capability: str, config: dict[str, Any]) -> Any:
    """A per-person integration with the signed-in person's own token, from Evokai's connection.

    A person who has not connected their account gets a card to connect it and the model is told
    why the tools are missing; nobody's token stands in for theirs.

    Returns:
        The capability.

    Raises:
        CapabilityError: No person to act as, no connection for this service in Evokai, or none
            configured or granted for the application.
    """
    from evokai.capabilities import credentials

    provider = credentials.PERSONAL[capability]
    if provider is None:
        raise caps.CapabilityError(
            f"{capability} acts as each person and Evokai has no connection for it yet: a person "
            "cannot connect their account; set acts_as: application to use the application's "
            "shared account"
        )
    connection = ctx.loaded.get(f"connection:{provider}")
    if connection is None:
        raise caps.CapabilityError(
            f"{capability} acts as each person: the application has no {provider} connection "
            "configured and granted (Connections), or nobody is signed in"
        )
    if not connection["connected"]:
        return ConnectFirst(
            capability=capability,
            card={
                "provider": provider,
                "title": connection["title"],
                "server": connection["server"],
            },
        )
    token = connection["token"]

    async def auth(_run: RunContext[RunState]) -> str | None:  # ruff: ignore[unused-async]
        return token

    cap = caps.construct(capability, config, model_of=_no_model, provided={"auth": auth})
    return Tagged(
        wrapped=cap,
        tags={
            "evokai_capability": capability,
            "evokai_acts_as": "person",
            "evokai_provider": provider,
        },
    )


@dataclass
class Tagged(WrapperCapability[RunState]):
    """A capability whose tools carry who they act as (traces, the confirmation rule)."""

    tags: dict[str, Any] = field(default_factory=dict)

    def get_toolset(self) -> Any:
        """The wrapped capability's toolset, its tools tagged.

        Returns:
            The toolset.
        """
        ts = self.wrapped.get_toolset()
        if isinstance(ts, AbstractToolset):
            return ts.with_metadata(**self.tags)
        return ts


@dataclass
class ConnectFirst(AbstractCapability[RunState]):
    """Stands in for a per-person integration the person has not connected.

    A card to connect it, and the model told why its tools are missing.
    """

    capability: str = ""
    card: dict[str, Any] = field(default_factory=dict)

    def get_instructions(self) -> Any:
        """Why the integration's tools are missing.

        Returns:
            The instructions.
        """
        title = self.card.get("title") or self.capability
        return (
            f"The person has not connected their {title} account, so its tools are not "
            "available in this conversation. A button to connect it is shown to them: if they ask "
            "for something there, ask them to connect and then to ask again. Never act on their "
            "behalf another way."
        )

    async def before_model_request(self, ctx: RunContext[RunState], request_context: Any) -> Any:
        """Show the card once per turn.

        Returns:
            The request, unchanged.
        """
        st = ctx.deps
        if st.emit is not None and not st.extra.get(f"connect:{self.capability}"):
            st.extra[f"connect:{self.capability}"] = True
            st.show({"type": "connect", **self.card})
        return request_context


def factories(ctx: Context) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """The builders of a turn or run, as ``evokai.capabilities.build`` takes them.

    Returns:
        Builder by capability id.
    """
    from evokai.capabilities.credentials import PROVIDER_OF

    out = {cid: _bind(fn, ctx) for cid, fn in BUILDERS.items()}
    for cid in PROVIDER_OF:
        out.setdefault(cid, _bind_credentials(cid, ctx))
    return out


def _bind_credentials(capability: str, ctx: Context) -> Callable[[dict[str, Any]], Any]:
    def build(config: dict[str, Any]) -> Any:
        return with_credentials(ctx, capability, config)

    return build


def _bind(
    fn: Callable[[Context, dict[str, Any]], Any], ctx: Context
) -> Callable[[dict[str, Any]], Any]:
    def build(config: dict[str, Any]) -> Any:
        return fn(ctx, config)

    return build
