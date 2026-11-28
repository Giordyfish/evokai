"""What one turn or run carries through pydantic-ai: the ``deps`` of the agent run.

Tools and the capability read and write it; the driver (``evokai.sessions.turns`` or
``evokai.sessions.runs``) reads it between nodes to emit events and store messages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import Model

    from evokai.core.config import Settings
    from evokai.engine.tools import ToolSpec
    from evokai.guard import TurnGuard
    from evokai.observability.tracing import Span, Trace


class StopRun(Exception):
    """A hook ends the turn or run before the next model call (a cap, the token budget).

    The driver catches it; ``reason`` is the ``done`` reason, ``detail`` what the hook found.
    """

    def __init__(self, reason: str, detail: Any = None) -> None:
        """Keep why the run stops.

        Args:
            reason: ``limit``, ``guard`` or another ``done`` reason.
            detail: What the hook found (the cap that was hit).
        """
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Scope:
    """Who a turn or run is for, as capabilities that make their own calls need it."""

    app: Any
    grants: Any
    who: Any
    tags: list[str]
    run_id: str
    session_id: str | None = None
    credential: str | None = None
    """The session's forwarded credential (MCP servers with ``forward_user``)."""
    groups: list[str] = field(default_factory=list)
    evaluation: bool = False
    on_cost: Callable[[int], None] | None = None
    """Adds a call's cost to the turn or run (server-side totals)."""
    locale: str = "en"
    """The session's or run's language tag, for ``{{session.locale}}`` in subagents' prompts."""
    storage: str | None = None
    """Where the conversation is kept (``evokai`` or ``external``); None for a server run, which
    keeps nothing of a conversation for capabilities."""


@dataclass
class Attempt:
    """One attempt at a model call: which model, since when, its trace span."""

    name: str
    started: float
    span: Span | None = None
    index: int = 0


@dataclass
class RunState:
    """The deps of an Evokai agent run."""

    guard: TurnGuard
    trace: Trace
    settings: Settings
    run_id: str
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    """Every tool the run offers, by name."""
    models: list[tuple[str, Model]] = field(default_factory=list)
    """The model chain: the primary, then the fallbacks (Evokai name and pydantic-ai model)."""
    prompt_cache: str = "off"
    """The agent's prompt caching (``off``, ``5m``, ``1h``)."""
    view: Callable[[list[Any]], list[Any]] | None = None
    """The history the model sees from the run's history (window, guard, system prompt); None
    sends the run's history as it is."""
    before_call: Callable[[], Awaitable[None]] | None = None
    """Called before each model call (caps, budget); raises ``StopRun`` to end the run."""
    compact: Callable[[list[Any]], Awaitable[bool]] | None = None
    """Compacts the conversation once when a provider refuses a request as too long (with the run's
    history); True when the history the model sees changed."""
    on_response: Callable[[str, ModelResponse, int], Awaitable[None]] | None = None
    """Called after each answered model call with the model's name, the response and its latency in
    ms (usage, the ``usage`` event)."""
    on_tool: Callable[[str, str], None] | None = None
    """Called when a call starts and ends, with the tool and ``start``, ``done`` or ``error``
    (server runs: the progress of the streaming standards)."""
    emit: Callable[[dict[str, Any]], Any] | None = None
    """Publishes an event to the session (turns only)."""
    say: Callable[[str], None] | None = None
    """Speaks a sentence in a voice turn (a question a capability shows, for instance)."""
    label: Callable[[str, dict[str, Any]], str] | None = None
    """The confirmation label of a mutating call (turns only)."""
    health: Any = None
    """The process's model health (``evokai.llm.health.ModelHealth``)."""
    operation: str = "stream"
    """The provider-operation name of a model call (``stream`` in turns, ``request`` in runs)."""
    max_tokens: int | None = None
    """The output token cap of every call (traces)."""
    op_tags: dict[str, Any] = field(default_factory=dict)
    """Extra fields of the provider-operation log (``turnId`` or ``runId``)."""
    step: int = -1
    """The model step being run (0 for the first)."""
    attempt: Attempt | None = None
    attempts: int = 0
    """Attempts of this step (the fallback counter)."""
    answered: str = ""
    """The model that answered the last call."""
    fallback: bool = False
    """Whether a fallback model answered any call."""
    shown: bool = False
    """Whether this step's answer reached the user (no fallback after that)."""
    text: str = ""
    """What this step showed of its answer."""
    first_ms: int | None = None
    """Milliseconds from this step's call to its first streamed part."""
    args: dict[str, dict[str, Any]] = field(default_factory=dict)
    """The arguments each call was run with (protected identifiers put back), by call id."""
    renamed: dict[str, str] = field(default_factory=dict)
    """Calls of tools that were not offered, by call id: the name the model wrote (the call runs
    as ``evokai.engine.tools.UNKNOWN_TOOL``)."""
    ok: dict[str, bool] = field(default_factory=dict)
    """Whether each call succeeded, by call id."""
    waiting: list[dict[str, Any]] = field(default_factory=list)
    """Calls that wait for the user's confirmation (id, name, input, label)."""
    events: list[dict[str, Any]] = field(default_factory=list)
    """Events shown in this step (kept with its assistant message)."""
    tool_calls: int = 0
    """Tool calls of the turn so far (not counting calls inside a program)."""
    confirm: dict[str, Any] | None = None
    """The user's decision being carried out: ``{approve, waitedMs}``."""
    nested: int = 0
    """Depth of calls made from inside a program (``run_code``)."""
    locale: str = "en"
    """The application's language (the label of an ask rule on a confirmation)."""
    wrote: set[str] = field(default_factory=set)
    """Calls of tools that change data that ran in this turn, by call id."""
    expired: Callable[[str], Awaitable[None]] | None = None
    """Called with a provider id when its service refused the person's token (a 401): the
    connection is marked so the person is asked to connect again."""
    extra: dict[str, Any] = field(default_factory=dict)
    """What a capability keeps for the driver (a compaction summary)."""

    def show(self, event: dict[str, Any]) -> None:
        """Emit an event and keep it with this step's message."""
        self.events.append(event)
        if self.emit is not None:
            self.emit(event)

    def new_step(self) -> None:
        """Reset what belongs to one model step."""
        self.step += 1
        self.attempts = 0
        self.attempt = None
        self.shown = False
        self.text = ""
        self.first_ms = None
        self.events = []
        self.waiting = []
