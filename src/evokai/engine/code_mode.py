"""Code Mode: the harness ``CodeMode`` (a Monty program calls tools as functions), Evokai's way.

- Only read-only tools go inside ``run_code`` (MCP tools that do not change data, ``skill_load``):
  ``CodeMode(tools={READ_ONLY: True})``. Tools that change data, client tools (they show something
  to the person), knowledge search, handoff and code execution stay normal calls, with the usual
  confirmation.
- The read-only tools stay available as normal calls too (``_AlsoNative``): an agent that would
  rather call one tool directly still can.
- Every call inside a program goes through the agent's assembled toolset, so through the guard
  (policy, arguments, egress, loop, rate) and MCP as the user; in the trace it is a child span of
  ``run_code``; it is not counted against the turn's tool-call cap (``run_code`` itself is), the
  program's own cap is ``max_tool_calls``.
- Monty runs in process with limits tighter than the harness defaults and no host access: no
  ``mount``, no ``os_access``, the default OS policy (no clock, no environment, no files).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness import CodeMode
from pydantic_ai_harness.code_mode._parts import RUN_CODE_TOOL_NAME

from evokai.engine.state import RunState
from evokai.engine.tools import READ_ONLY

TOOL_NAME = RUN_CODE_TOOL_NAME
"""The tool the model writes programs for."""


@dataclass
class _AlsoNative(WrapperToolset[RunState]):
    """Code Mode's toolset, with the sandboxed tools also offered as normal calls."""

    native: AbstractToolset[RunState] = field(repr=False, kw_only=True)

    async def get_tools(self, ctx: RunContext[RunState]) -> dict[str, ToolsetTool[RunState]]:
        """``run_code``, the tools Code Mode left native, and the sandboxed ones again.

        Returns:
            Name to tool.
        """
        tools = dict(await self.wrapped.get_tools(ctx))
        for name, tool in (await self.native.get_tools(ctx)).items():
            tools.setdefault(name, tool)
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[RunState],
        tool: ToolsetTool[RunState],
    ) -> Any:
        """``run_code`` goes to Code Mode; every other call to the agent's own toolset.

        Returns:
            What the tool returned.
        """
        if name == TOOL_NAME:
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        return await self.native.call_tool(name, tool_args, ctx, tool)


@dataclass(init=False)
class EvokaiCodeMode(CodeMode[RunState]):
    """Code Mode over the read-only tools, with Evokai's trace, events and counters."""

    def __init__(self, *, timeout_s: float, memory_mb: int, tool_calls: int) -> None:
        """Code Mode with Evokai's limits.

        Args:
            timeout_s: Execution time of one program (time awaiting tools is not counted).
            memory_mb: Heap of the sandbox.
            tool_calls: Tool calls one program may make.
        """
        super().__init__(
            tools={READ_ONLY: True},
            max_tool_calls=tool_calls,
            resource_limits={
                "max_duration_seconds": float(timeout_s),
                "max_memory": int(memory_mb) * 1024 * 1024,
            },
            dynamic_catalog=False,
        )

    def get_wrapper_toolset(
        self, toolset: AbstractToolset[RunState]
    ) -> AbstractToolset[RunState] | None:
        """Code Mode's toolset, the sandboxed tools kept as normal calls too.

        Returns:
            The wrapper.
        """
        inner = super().get_wrapper_toolset(toolset)
        if inner is None:
            return None
        return _AlsoNative(wrapped=inner, native=toolset)

    async def wrap_tool_execute(
        self,
        ctx: RunContext[RunState],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,  # ruff: ignore[unused-method-argument]
        args: Any,
        handler: Any,
    ) -> Any:
        """``run_code`` is a span (its calls are its children), a tool event and one call.

        Returns:
            What the program returned.
        """
        if call.tool_name != TOOL_NAME:
            return await handler(args)
        st = ctx.deps
        st.tool_calls += 1
        tr = st.trace
        ts = tr.start("tool", f"execute_tool {TOOL_NAME}", tool=TOOL_NAME, callId=call.tool_call_id)
        tr.put(ts, args=call.args_as_dict())
        tr.push(ts)
        if st.emit is not None:
            st.emit({"type": "tool", "name": TOOL_NAME, "status": "start"})
        if st.on_tool is not None:
            st.on_tool(TOOL_NAME, "start")
        st.nested += 1
        ok = False
        try:
            out = await handler(args)
            ok = True
            tr.put(ts, result=out if isinstance(out, str) else str(out))
            return out
        except BaseException:
            ts.status = "error"
            raise
        finally:
            st.nested -= 1
            ts.set(outcome="ok" if ok else "error", type="code_mode")
            tr.pop(ts)
            tr.end(ts)
            st.ok[call.tool_call_id] = ok
            if st.emit is not None:
                st.emit({"type": "tool", "name": TOOL_NAME, "status": "done" if ok else "error"})
            if st.on_tool is not None:
                st.on_tool(TOOL_NAME, "done" if ok else "error")
