"""Evokai's rules around the tools that harness capabilities bring (search, integrations, plans...).

A harness capability adds its own toolset to the agent (``get_toolset``), outside Evokai's
``GuardedToolset``. ``CapabilityTools`` is added to every turn or run that enables one, and wraps
the agent's whole toolset (``get_wrapper_toolset``) so that each of those tools gets what an MCP
tool gets:

- the tool policy decides whether it is offered (``guard.offered``);
- every call is checked like any other (policy, arguments, egress, loop, rate) and is a ``tool``
  span of the trace with its arguments and result, and a ``tool`` event for the client;
- a call counts against the turn's tool-call cap;
- a tool that writes waits for the person's confirmation: a tool of a hosted MCP server (the
  integrations: GitHub, Linear, Notion, Slack...) is read-only only when its server marks it so
  (``readOnlyHint``), the tool policy can force a confirmation on any tool (``tools.confirm``) and
  an ask rule can ask for any call (``guard.asks``), with the same reason the person reads; a
  tool that wrote marks the turn as written (no rewrite of its question);
- its text result is untrusted content, scanned and marked by the guard before the model reads it.

Evokai's own tools (``EvokaiToolset``, already guarded) and the tools of Evokai's own capabilities
(``run_code``, ``delegate_task``) pass through unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, WrapperToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_core import to_jsonable_python

from evokai.core.util import clip
from evokai.engine.state import RunState
from evokai.engine.tools import (
    NEEDS_CONFIRM_TEXT,
    READ_ONLY,
    UNKNOWN_TOOL,
    blocked_text,
    confirm_reason,
)

OWN_TOOLS = frozenset({"run_code", "delegate_task"})
"""Tools of Evokai's own capabilities (Code Mode, subagents): they keep their own spans."""


def is_capability_tool(st: RunState, name: str) -> bool:
    """Whether a tool comes from a harness capability (not Evokai's toolset or own capabilities).

    Returns:
        True for a capability's tool.
    """
    return name not in st.tools and name != UNKNOWN_TOOL and name not in OWN_TOOLS


def writes(tool_def: ToolDefinition) -> bool:
    """Whether a capability's tool may change data, so it waits for the person's confirmation.

    A tool of an MCP server (it carries the server's ``annotations``) changes data unless the
    server marks it ``readOnlyHint``; a tool without annotations (search, plans, memory files,
    a sandbox's workspace) changes nothing of the application's.

    Returns:
        True when it needs a confirmation.
    """
    meta = tool_def.metadata or {}
    if "annotations" not in meta:
        return False
    annotations = meta.get("annotations") or {}
    return not (isinstance(annotations, dict) and annotations.get("readOnlyHint") is True)


@dataclass
class _GuardedCapabilityTools(WrapperToolset[RunState]):
    """The agent's toolset, with Evokai's rules on the tools of harness capabilities."""

    async def get_tools(self, ctx: RunContext[RunState]) -> dict[str, ToolsetTool[RunState]]:
        """What the tool policy allows of the capabilities' tools; Evokai's own as they are.

        A capability's tool is never read-only for Code Mode (``evokai_read_only`` false): it is a
        normal call, with its confirmation when it writes or an ask rule may ask for it.

        Returns:
            Name to tool.
        """
        st = ctx.deps
        tools = await self.wrapped.get_tools(ctx)
        out: dict[str, ToolsetTool[RunState]] = {}
        for n, tool in tools.items():
            if not is_capability_tool(st, n):
                out[n] = tool
            elif st.guard.offered(n):
                meta = {**(tool.tool_def.metadata or {}), READ_ONLY: False}
                out[n] = replace(tool, tool_def=replace(tool.tool_def, metadata=meta))
        return out

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[RunState],
        tool: ToolsetTool[RunState],
    ) -> Any:
        """Check, confirm, run and trace a capability's tool call.

        Returns:
            What the tool returned (a text result is guarded as untrusted content).

        Raises:
            ApprovalRequired: The tool writes and the person has not confirmed it yet.
        """
        st = ctx.deps
        if not is_capability_tool(st, name):
            return await self.wrapped.call_tool(name, tool_args, ctx, tool)
        guard, tr = st.guard, st.trace
        cid = ctx.tool_call_id or ""
        raw = to_jsonable_python(tool_args or {})
        args = guard.call_args(dict(raw))
        st.args.setdefault(cid, args)
        if args != raw:
            # Protected identifiers put back: the tool receives the person's own values.
            tool_args = tool.args_validator.validate_python(args)
        approved = ctx.tool_call_approved
        if not approved and not st.nested:
            st.tool_calls += 1
        meta = tool.tool_def.metadata or {}
        shared = meta.get("evokai_acts_as") == "application"
        attrs: dict[str, Any] = {"tool": name, "callId": cid, "source": "capability"}
        if meta.get("evokai_capability"):
            attrs["capability"] = meta["evokai_capability"]
        if meta.get("evokai_acts_as"):
            attrs["actsAs"] = meta["evokai_acts_as"]
        asked = guard.asks(name, args)
        if asked is not None:
            attrs.update(askedBy="rule", askRule=asked.rule, askRuleLevel=asked.level)
        if approved and st.confirm is not None:
            attrs.update(confirmed=True, waitedMs=st.confirm.get("waitedMs"))
        elif approved:
            attrs.update(confirmed=True, approvedBy="capability")
        ts = tr.start("tool", f"execute_tool {name}", **attrs)
        tr.put(ts, args=raw)
        tr.push(ts)
        if st.on_tool is not None:
            st.on_tool(name, "start")
        ok = False
        try:
            decision = await guard.check_call(
                name, args, tool.tool_def.parameters_json_schema or None
            )
            if not decision.allowed:
                ts.set(outcome="blocked", rule=decision.rule)
                out: Any = blocked_text(decision)
            elif shared and writes(tool.tool_def) and approved and st.confirm is None:
                # A write through a shared account is confirmed by the person, never by a
                # capability (approval_reviewer).
                ts.set(outcome="blocked", rule="person_confirmation")
                out = (
                    "Blocked: a change through the application's shared account needs the "
                    "person's own confirmation. Do not retry this call."
                )
            elif (
                writes(tool.tool_def) or guard.forced_confirmation(name) or asked is not None
            ) and not approved:
                if st.nested:
                    # Inside a program nobody can confirm.
                    ts.set(outcome="blocked", rule=asked.rule if asked else "confirm")
                    out = NEEDS_CONFIRM_TEXT
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
                if st.emit is not None:
                    st.emit({"type": "tool", "name": name, "status": "start"})
                try:
                    out = await self.wrapped.call_tool(name, tool_args, ctx, tool)
                    ok = True
                finally:
                    if writes(tool.tool_def):
                        # A tool that changes data ran: the turn can no longer be rewritten.
                        st.wrote.add(cid)
                    if st.emit is not None:
                        st.emit({"type": "tool", "name": name, "status": "done" if ok else "error"})
                if isinstance(out, str):
                    out = clip(await guard.tool_result(name, out), st.settings.tool_result_chars)
                ts.set(outcome="ok")
            tr.put(ts, result=out if isinstance(out, str) else str(out))
        except ApprovalRequired:
            raise
        except BaseException as e:
            ts.status = "error"
            ts.set(outcome="error")
            provider = meta.get("evokai_provider")
            if meta.get("evokai_acts_as") == "person" and provider and unauthorized(e):
                # The service refused the person's own token: they must connect again (a token
                # that never expires is only known to be revoked this way).
                ts.set(outcome="error", connection="expired")
                if st.expired is not None:
                    await st.expired(str(provider))
            raise
        finally:
            tr.pop(ts)
            tr.end(ts)
            if st.on_tool is not None and ts.attrs.get("outcome") != "confirm":
                st.on_tool(name, "done" if ts.attrs.get("outcome") == "ok" else "error")
        st.ok[cid] = ok
        return out


def unauthorized(error: BaseException) -> bool:
    """Whether an error, or any error it wraps, is an HTTP 401 from the service.

    Returns:
        True for a refused token.
    """
    seen: set[int] = set()
    todo: list[BaseException] = [error]
    while todo:
        e = todo.pop()
        if id(e) in seen:
            continue
        seen.add(id(e))
        response = getattr(e, "response", None)
        if getattr(response, "status_code", None) == 401 or getattr(e, "status_code", None) == 401:
            return True
        todo += [x for x in (e.__cause__, e.__context__) if x is not None]
        todo += list(getattr(e, "exceptions", ()) or ())
    return False


@dataclass
class CapabilityTools(AbstractCapability[RunState]):
    """Evokai's tool rules on every tool a harness capability adds (see the module)."""

    def get_wrapper_toolset(
        self, toolset: AbstractToolset[RunState]
    ) -> AbstractToolset[RunState] | None:
        """Wrap the agent's assembled toolset.

        Returns:
            The guarded toolset.
        """
        return _GuardedCapabilityTools(wrapped=toolset)
