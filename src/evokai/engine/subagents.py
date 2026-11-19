"""Subagents: an agent calls other agents of its application as tools (harness ``SubAgents``).

An agent lists the agents it may call (``subagents: [names]``) and turns the ``subagents``
capability on. The model gets one tool, ``delegate_task(agent_name, task)``; each call runs the
named agent on the task with its own definition (its active version, models, instructions, MCP
tools, skills, knowledge), in a fresh history, and returns its answer.

What Evokai keeps around each call (v1):

- the same person: the child runs as the turn's subject, with the session's forwarded credential
  for MCP servers with ``forward_user``, within the application's grants;
- the guard: the child's tools go through the same guarded toolset (tool policy, arguments, egress,
  loop, rate) and its tool results are marked as data; the caps are checked before each of its
  model calls, and its usage is recorded under the child agent with kind ``subagent`` (counted
  against the caps like any call);
- only read-only tools: nobody could confirm a change inside a delegation, so tools that change
  data are not offered; no client tools, no voice, no handoff, no code execution, and no
  subagents of its own (depth 1);
- at most ``EVOKAI_SUBAGENT_CALLS`` calls per turn or run, each within
  ``EVOKAI_SUBAGENT_TIMEOUT_SECONDS``;
- the trace: the call is a ``tool`` span (``delegate_task``) and the child's model and tool spans
  are its children; the turn's cancellation stops the child with it.

Handoff (``handoff_to``) is different: it gives the rest of the conversation to another agent, while
a subagent answers one task and the caller goes on (docs/workflows.md).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.agent.wrapper import WrapperAgent
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness import SubAgent, SubAgents

from evokai.engine.state import RunState

TOOL_NAME = "delegate_task"
"""The tool the model calls to delegate a task."""

ChildFactory = Callable[[RunState], Awaitable[tuple[Any, RunState]]]
"""Builds a child for one call: ``(the parent run's state) -> (Agent, the child's state)``."""


class _Child(WrapperAgent[RunState, Any]):
    """A child agent built when it is called, with its own state as deps.

    The harness forwards the parent's deps to the child; Evokai replaces them with the child's own
    state (its tools, models, usage under its own name), which shares the parent's guard and trace.
    """

    def __init__(self, name: str, description: str, factory: ChildFactory, template: Any) -> None:
        super().__init__(template)
        self._name = name
        self.description = description
        self._factory = factory

    @property
    def name(self) -> str | None:
        """The name the parent model calls it by.

        Returns:
            The agent's name.
        """
        return self._name

    @name.setter
    def name(self, value: str | None) -> None:  # ruff: ignore[unused-method-argument]
        """The name is fixed (the harness may try to set an inferred one)."""
        return

    @asynccontextmanager
    async def iter(self, *args: Any, deps: Any = None, **kwargs: Any) -> AsyncGenerator[Any]:
        """Build the child for this call and run it with its own state.

        Yields:
            The child's run.
        """
        agent, state = await self._factory(deps)
        kwargs.pop("toolsets", None)
        async with agent.iter(*args, deps=state, **kwargs) as run:
            yield run


@dataclass(init=False)
class EvokaiSubAgents(SubAgents[RunState]):
    """``SubAgents`` over Evokai agents, with a span per call and a cap per turn."""

    calls: int = 4
    """Calls per turn or run (all children together)."""

    def __init__(
        self,
        children: list[tuple[str, str, ChildFactory, Any]],
        *,
        calls: int,
        timeout_seconds: float,
    ) -> None:
        """The roster.

        Args:
            children: ``(name, description, factory, template agent)`` of each agent it may call.
            calls: Calls per turn or run, all children together.
            timeout_seconds: What one call may take.
        """
        super().__init__(
            agents=[
                SubAgent(
                    _Child(name, description, factory, template),
                    name=name,
                    max_calls=calls,
                    timeout_seconds=timeout_seconds,
                )
                for name, description, factory, template in children
            ],
            agent_folders=None,
            forward_usage=False,
            max_depth=2,
        )
        self.calls = calls

    async def wrap_tool_execute(
        self,
        ctx: RunContext[RunState],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        handler: Any,
    ) -> Any:
        """A call is one tool call of the turn, a span, and counts against the cap.

        Returns:
            The child's answer (or why it did not run).
        """
        if call.tool_name != TOOL_NAME:
            return await super().wrap_tool_execute(
                ctx, call=call, tool_def=tool_def, args=args, handler=handler
            )
        st = ctx.deps
        used = int(st.extra.get("subagentCalls", 0))
        st.tool_calls += 1
        tr = st.trace
        name = str(call.args_as_dict().get("agent_name") or "")
        ts = tr.start("tool", f"execute_tool {TOOL_NAME}", tool=TOOL_NAME, agent=name or None)
        tr.put(ts, args=call.args_as_dict())
        tr.push(ts)
        if st.on_tool is not None:
            st.on_tool(TOOL_NAME, "start")
        ok = False
        try:
            if used >= self.calls:
                ts.set(outcome="error", errorType="subagent_cap")
                return (
                    f"Not run: this turn already called {self.calls} subagents. Answer with what "
                    "you have."
                )
            st.extra["subagentCalls"] = used + 1
            out = await super().wrap_tool_execute(
                ctx, call=call, tool_def=tool_def, args=args, handler=handler
            )
            ok = True
            ts.set(outcome="ok", type="subagent")
            tr.put(ts, result=out if isinstance(out, str) else str(out))
            return out
        except BaseException:
            ts.status = "error"
            raise
        finally:
            tr.pop(ts)
            tr.end(ts)
            if st.on_tool is not None:
                st.on_tool(TOOL_NAME, "done" if ok else "error")
