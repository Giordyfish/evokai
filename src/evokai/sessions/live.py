"""What a model step shows besides its answer: its reasoning and its tool calls' arguments, live.

Both are opt-in per agent and both are model output on their way to a browser, so each goes
through the output checks first, exactly as the answer's text does (``TurnGuard.output_filter``):
secrets and identifiers are rewritten as the policy says, tokens are put back, and a piece that
repeats the agent's instructions stops that stream. Nothing the filter holds back or blocks is
sent.

- ``show_reasoning``: the model's reasoning (``ThinkingPart``) streams as ``reasoning`` events
  (``{"type": "reasoning", "delta"}``). Providers return a summary of it (Anthropic and Bedrock
  Claude with ``display: summarized``, OpenAI reasoning models with ``openai_reasoning_summary``);
  some OpenAI-compatible servers return their raw reasoning.
- ``show_tool_args``: the arguments of a call of a tool that shows a card (an application tool,
  ``knowledge_search``, ``code_run``, ``skill_run``) stream as ``tool`` events with
  ``status: "args"``, ``id`` (the call) and ``args``, the object parsed so far (partial JSON closed
  where it stops). They are sent at most every ``ARGS_EVERY`` seconds per call, plus once complete.
  The call then starts (``start``, then ``done`` or ``error``), waits for the person
  (``waiting``: a confirmation follows), or does not run (``skipped``).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)
from pydantic_core import from_json

ARGS_EVERY = 0.15
"""Seconds between two ``args`` events of one call (the last one always goes)."""

CARD_KINDS = frozenset({"app", "knowledge", "code", "skill_run"})
"""Tool kinds that show a card (their calls emit ``tool`` events), so their arguments can too."""


def partial_object(text: str) -> dict[str, Any] | None:
    """A JSON object still being written, parsed as far as it goes.

    Returns:
        The object, or None when the text is not the start of one.
    """
    if not text.strip():
        return None
    try:
        value = from_json(text, allow_partial="trailing-strings")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


@dataclass
class _Call:
    id: str
    name: str
    filter: Any
    text: str = ""
    """What the filter let through."""
    sent: dict[str, Any] | None = None
    at: float = 0.0


@dataclass
class LiveStep:
    """One model step's reasoning and tool arguments, through the output checks, as events."""

    guard: Any
    emit: Any
    reasoning: bool
    """Show the reasoning (``show_reasoning``)."""
    args_of: frozenset[str]
    """The tools whose arguments are shown (empty: none, ``show_tool_args`` off)."""
    clean: Any = None
    _reasoning_filter: Any = None
    _gap: bool = False
    _thinking: set[int] = field(default_factory=set)
    _calls: dict[int, _Call] = field(default_factory=dict)

    def event(self, ev: Any) -> None:
        """Take one event of the model's stream (others than reasoning and calls are ignored)."""
        if isinstance(ev, PartStartEvent):
            part = ev.part
            if not isinstance(part, ThinkingPart):
                # What the reasoning filter held back goes before the answer or the call after it.
                self._release()
            if isinstance(part, ThinkingPart) and self.reasoning:
                self._thinking.add(ev.index)
                f = self._reasoning_filter
                self._gap = f is not None and bool(f.raw.strip())
                if part.content:
                    self._think(part.content)
            elif isinstance(part, ToolCallPart) and part.tool_name in self.args_of:
                call = _Call(part.tool_call_id, part.tool_name, self.guard.output_filter())
                self._calls[ev.index] = call
                self._args(call, part.args)
        elif isinstance(ev, PartDeltaEvent):
            delta = ev.delta
            if isinstance(delta, ThinkingPartDelta) and ev.index in self._thinking:
                if delta.content_delta:
                    self._think(delta.content_delta)
            elif isinstance(delta, ToolCallPartDelta) and ev.index in self._calls:
                self._args(self._calls[ev.index], delta.args_delta)

    def _think(self, delta: str) -> None:
        if self._reasoning_filter is None:
            self._reasoning_filter = self.guard.output_filter(clean=self.clean)
        if self._gap:
            # Another block of reasoning: a paragraph of its own.
            self._gap, delta = False, "\n\n" + delta
        out = self._reasoning_filter.push(delta)
        if out:
            self.emit({"type": "reasoning", "delta": out})

    def _release(self) -> None:
        f = self._reasoning_filter
        out = f.flush() if f is not None else ""
        if out:
            self.emit({"type": "reasoning", "delta": out})

    def _args(self, call: _Call, delta: Any, *, last: bool = False) -> None:
        if delta is None and not last:
            return
        piece = delta if isinstance(delta, str) else json.dumps(delta or {}, ensure_ascii=False)
        call.text += call.filter.push(piece) if piece else ""
        if last:
            call.text += call.filter.flush()
        if call.filter.blocked:
            return
        now = time.monotonic()
        if not last and now - call.at < ARGS_EVERY:
            return
        value = partial_object(call.text)
        if value is None or value == call.sent:
            return
        call.sent, call.at = value, now
        self.emit({
            "type": "tool",
            "name": call.name,
            "id": call.id,
            "status": "args",
            "args": value,
        })

    def finish(self) -> dict[str, str]:
        """The end of the step: what was held back goes, the checks are recorded.

        Returns:
            The calls whose arguments were shown (id to name), to settle once the tools ran.
        """
        self._release()
        if self._reasoning_filter is not None:
            self.guard.after_output(self._reasoning_filter)
        shown: dict[str, str] = {}
        for call in self._calls.values():
            self._args(call, "", last=True)
            self.guard.after_output(call.filter)
            if call.sent is not None:
                shown[call.id] = call.name
        return shown
