"""Evokai's tools as a pydantic-ai toolset, and the guard and the confirmation rule around it.

``EvokaiToolset`` offers the tools of one turn or run (MCP tools called as the user, client tools,
knowledge, code, skills, handoff, memory, subagents), each with a handler that returns a
``ToolResult``. ``GuardedToolset`` wraps it: the tool policy decides what is offered, every call is
checked (policy, arguments, egress, loop, rate) and traced, and a tool that changes data raises
``ApprovalRequired`` until the user confirms it (pydantic-ai's deferred approvals; Evokai keeps the
waiting calls in its own database, never at the provider).

The arguments are not validated by pydantic-ai (any JSON object): the guard checks them against the
tool's schema (``tools.args``) and a handler answers a bad call with a result the model can read,
exactly as before the move to the Agent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ApprovalRequired, CallDeferred
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.external import TOOL_SCHEMA_VALIDATOR

from evokai.engine import messages
from evokai.engine.state import RunState
from evokai.guard.ask import REASON_POLICY, REASON_WRITES, AskMatch
from evokai.mcp.client import ToolResult

log = logging.getLogger("evokai.engine")

READ_ONLY = "evokai_read_only"
"""Tool metadata key: the tool changes nothing (it may be called from a program, ``run_code``)."""

UNKNOWN_TOOL = messages.UNKNOWN_TOOL
"""A hidden tool: a call to a tool that is not offered (unknown, or refused by the tool policy) is
renamed to it (``EvokaiCapability.after_model_request``), so the guard still checks it and the
model reads «Blocked by the security policy» or «Unknown tool», as before the Agent; the history
keeps the name the model wrote."""

Handler = Callable[[dict[str, Any], str, Any], Awaitable[ToolResult]]
"""A tool's handler: ``(arguments, call id, trace span) -> ToolResult``."""


@dataclass
class ToolSpec:
    """One tool of a turn or run."""

    name: str
    description: str
    schema: dict[str, Any]
    run: Handler
    kind: str = "local"
    """``app`` (an MCP tool), ``local`` (client, memory, skills), ``knowledge``, ``code``,
    ``skill_run``, ``handoff``, ``subagent``."""
    server: str | None = None
    """The MCP server of an app tool (traces)."""
    mutating: bool = False
    """Waits for the user's confirmation."""
    read_only: bool = False
    """Changes nothing and shows nothing: may run inside a program (Code Mode)."""
    metadata: dict[str, Any] = field(default_factory=dict)
    return_schema: dict[str, Any] | None = None
    """The JSON Schema of what the tool returns (an MCP tool's ``outputSchema``): Code Mode types
    the function's return with it. Not sent to the model with the tool's definition."""


NEEDS_CONFIRM_TEXT = (
    "Not run: this call needs the person's confirmation. Call the tool directly, not from a "
    "program."
)


def confirm_reason(guard: Any, name: str, asked: AskMatch | None, locale: str) -> dict[str, Any]:
    """Why a call waits for the person: the rule that asked, else the tool changes data.

    Returns:
        ``{key, rule?, level?, label?}``: ``key`` is an i18n key the clients translate.
    """
    if asked is not None:
        return asked.reason(locale)
    if guard.forced_confirmation(name):
        return {"key": REASON_POLICY}
    return {"key": REASON_WRITES}


def blocked_text(decision: Any) -> str:
    """The tool result of a call the security policy refused.

    Returns:
        A text the model can read.
    """
    return f"Blocked by the security policy: {decision.reason}. Do not retry this call."


class EvokaiToolset(AbstractToolset[RunState]):
    """The tools of one turn or run, from their specs."""

    def __init__(self, specs: list[ToolSpec]) -> None:
        """Keep the specs, and the hidden tool that answers calls of tools not offered.

        Args:
            specs: The tools, in the order they are offered.
        """
        self.specs = {s.name: s for s in specs}

        async def unknown(  # ruff: ignore[unused-async] (a handler is awaited)
            _args: dict[str, Any], _cid: str, span: Any
        ) -> ToolResult:
            name = span.attrs.get("tool", "") if span is not None else ""
            if span is not None:
                span.set(outcome="error", errorType="unknown_tool")
            return ToolResult(ok=False, text=f"Unknown tool: {name}")

        self.specs[UNKNOWN_TOOL] = ToolSpec(
            name=UNKNOWN_TOOL,
            description="Not for use.",
            schema={"type": "object", "properties": {}},
            run=unknown,
        )

    @property
    def id(self) -> str | None:
        """The toolset's id.

        Returns:
            ``evokai``.
        """
        return "evokai"

    async def get_tools(
        self,
        ctx: RunContext[RunState],
    ) -> dict[str, ToolsetTool[RunState]]:
        """The tools as pydantic-ai sees them (arguments are checked by the guard, not here).

        Returns:
            Name to tool.
        """
        guard = getattr(ctx.deps, "guard", None)
        return {
            s.name: ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=s.name,
                    description=s.description,
                    parameters_json_schema=s.schema,
                    return_schema=s.return_schema,
                    sequential=True,
                    # A tool an ask rule may make wait for the person is not read-only: it stays
                    # out of programs (Code Mode) and is a normal call, with its confirmation.
                    metadata={
                        **s.metadata,
                        READ_ONLY: s.read_only
                        and not (guard is not None and guard.askable(s.name)),
                    },
                ),
                max_retries=1,
                args_validator=TOOL_SCHEMA_VALIDATOR,
            )
            for s in self.specs.values()
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[RunState],
        tool: ToolsetTool[RunState],  # ruff: ignore[unused-method-argument]
    ) -> Any:
        """Run a tool's handler (the guard has already let the call through).

        Returns:
            The handler's ``ToolResult``.
        """
        spec = self.specs[name]
        return await spec.run(tool_args, ctx.tool_call_id or "", ctx.deps.extra.get("span"))


@dataclass
class GuardedToolset(WrapperToolset[RunState]):
    """The guard and the confirmation rule around Evokai's tools."""

    async def get_tools(self, ctx: RunContext[RunState]) -> dict[str, ToolsetTool[RunState]]:
        """Only what the tool policy allows is offered.

        Returns:
            Name to tool.
        """
        guard = ctx.deps.guard
        tools = await self.wrapped.get_tools(ctx)
        return {n: t for n, t in tools.items() if n == UNKNOWN_TOOL or guard.offered(n)}

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[RunState],
        tool: ToolsetTool[RunState],
    ) -> Any:
        """Check, confirm, run and trace one call.

        Returns:
            The text the model reads.

        Raises:
            ApprovalRequired: The tool changes data and the user has not confirmed it yet.
            CallDeferred: A client tool waits for the client application's result.
        """
        st = ctx.deps
        guard, tr = st.guard, st.trace
        cid = ctx.tool_call_id or ""
        specs = getattr(self.wrapped, "specs", {})
        spec = None
        if name == UNKNOWN_TOOL:
            # A call of a tool that is not offered: checked under the name the model wrote.
            name = st.renamed.get(cid, name)
        else:
            spec = specs.get(name) or st.tools.get(name)
        args = guard.call_args(dict(tool_args or {}))
        st.args.setdefault(cid, args)
        approved = ctx.tool_call_approved
        if not approved and not st.nested:
            st.tool_calls += 1
        attrs: dict[str, Any] = {"tool": name, "callId": cid}
        if spec is not None and spec.server:
            attrs["server"] = spec.server
        asked = guard.asks(name, args) if spec is not None else None
        if asked is not None:
            attrs.update(askedBy="rule", askRule=asked.rule, askRuleLevel=asked.level)
        if approved and st.confirm is not None:
            attrs.update(confirmed=True, waitedMs=st.confirm.get("waitedMs"))
            if st.confirm.get("channel"):
                attrs["confirmChannel"] = st.confirm["channel"]
        elif approved:
            # Approved inside the run, not by the person: a capability the admin turned on for this
            # agent (approval_reviewer). The guard still checks the call below.
            attrs.update(confirmed=True, approvedBy="capability")
        ts = tr.start("tool", f"execute_tool {name}", **attrs)
        tr.put(ts, args=tool_args)
        tr.push(ts)
        if st.on_tool is not None:
            st.on_tool(name, "start")
        try:
            decision = await guard.check_call(name, args, spec.schema if spec else None)
            if not decision.allowed:
                ts.set(outcome="blocked", rule=decision.rule)
                r = ToolResult(ok=False, text=blocked_text(decision))
            elif spec is not None and (spec.mutating or asked is not None) and not approved:
                if st.nested:
                    # Inside a program nobody can confirm: such a tool is never offered there
                    # (``askable``), this only guards a rule that changed during the turn.
                    ts.set(outcome="blocked", rule=asked.rule if asked else "confirm")
                    r = ToolResult(ok=False, text=NEEDS_CONFIRM_TEXT)
                else:
                    ts.set(outcome="confirm")
                    label = st.label(name, args) if st.label is not None else name
                    st.waiting.append({
                        "toolUseId": cid,
                        "name": name,
                        "input": args,
                        "label": label,
                        "why": confirm_reason(guard, name, asked, st.locale),
                    })
                    raise ApprovalRequired()
            else:
                previous = st.extra.get("span")
                st.extra["span"] = ts
                try:
                    r = await self.wrapped.call_tool(
                        name if spec is not None else UNKNOWN_TOOL, args, ctx, tool
                    )
                finally:
                    st.extra["span"] = previous
                    if spec is not None and spec.mutating:
                        # A tool that changes data ran: the turn can no longer be rewritten.
                        st.wrote.add(cid)
                if not isinstance(r, ToolResult):
                    r = ToolResult(ok=True, text=str(r))
                if "outcome" not in ts.attrs:
                    ts.set(outcome="ok" if r.ok else "error")
            tr.put(ts, result=r.text)
        except (ApprovalRequired, CallDeferred):
            # Waits for the person (a confirmation) or the client application (a client tool).
            raise
        except BaseException:
            ts.status = "cancelled"
            raise
        finally:
            tr.pop(ts)
            tr.end(ts)
        st.ok[cid] = r.ok
        if st.on_tool is not None:
            st.on_tool(name, "done" if ts.attrs.get("outcome") == "ok" else "error")
        if st.nested and r.ok:
            # Inside a program (Code Mode) a JSON answer is data the code can index; what the
            # program returns goes back to the model as one result, guarded like any other.
            try:
                return json.loads(r.text)
            except ValueError:
                pass
        return r.text or ("ok" if r.ok else "errore")
