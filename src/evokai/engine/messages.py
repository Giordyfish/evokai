"""pydantic-ai messages back to Evokai's provider-neutral blocks.

Evokai stores conversations as blocks (``evokai.sessions.history``) so any provider, and a fallback
to another provider, sees the same history. The agent run keeps pydantic-ai messages; these helpers
turn them back into blocks, to store them and to build what the model sees on each request.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from evokai.sessions.history import Stored

SUMMARY_HEADER = (
    "[Summary of the earlier part of this conversation, written by Evokai when its older messages "
    "were removed. It is context, not a new message from the person.]"
)
"""How a compaction summary starts (``evokai.engine.compaction``): it is data, never the person."""

UNKNOWN_TOOL = "evokai_unknown_tool"
"""See ``evokai.engine.tools.UNKNOWN_TOOL``."""


def rename_unknown(
    response: ModelResponse, known: set[str], renamed: dict[str, str]
) -> ModelResponse:
    """Calls of tools that are not offered go to the hidden tool (the guard answers them).

    Args:
        response: The model's answer.
        known: The tools the run has (an empty set means: not known yet, change nothing).
        renamed: Where the names the model wrote are kept, by call id.

    Returns:
        The response, with those calls renamed.
    """
    if not known:
        return response
    changed = False
    parts: list[Any] = []
    for p in response.parts:
        if isinstance(p, ToolCallPart) and p.tool_name not in known:
            renamed[p.tool_call_id] = p.tool_name
            parts.append(replace(p, tool_name=UNKNOWN_TOOL))
            changed = True
        else:
            parts.append(p)
    return replace(response, parts=parts) if changed else response


def restore_names(messages: list[ModelMessage], renamed: dict[str, str]) -> list[ModelMessage]:
    """The history with the names the model wrote in place of the hidden tool.

    Returns:
        The messages (the same list when nothing was renamed).
    """
    if not renamed:
        return messages
    out: list[ModelMessage] = []
    for m in messages:
        parts: list[Any] = []
        for p in m.parts:
            if isinstance(p, (ToolCallPart, ToolReturnPart)) and p.tool_name == UNKNOWN_TOOL:
                p = replace(p, tool_name=renamed.get(p.tool_call_id or "", p.tool_name))
            parts.append(p)
        out.append(replace(m, parts=parts))
    return out


def content_text(content: Any) -> str:
    """A tool result's content as text.

    Returns:
        The text (JSON for structured content, such as a program's value).
    """
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)


def response_blocks(
    response: ModelResponse,
    args: dict[str, dict[str, Any]] | None = None,
    renamed: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """An answer as blocks: its text, then its tool calls.

    Args:
        response: The model's answer.
        args: The arguments each call ran with (protected identifiers put back), by call id.
        renamed: The names the model wrote for calls of tools not offered, by call id.

    Returns:
        Blocks.
    """
    args = args or {}
    renamed = renamed or {}
    text = "".join(p.content for p in response.parts if isinstance(p, TextPart))
    blocks: list[dict[str, Any]] = [{"text": text}] if text else []
    blocks += [
        {
            "toolUse": {
                "id": p.tool_call_id,
                "name": renamed.get(p.tool_call_id, p.tool_name),
                "input": args.get(p.tool_call_id, p.args_as_dict()),
            }
        }
        for p in response.parts
        if isinstance(p, ToolCallPart)
    ]
    return blocks


def result_blocks(
    request: ModelRequest,
    ok: dict[str, bool] | None = None,
    renamed: dict[str, str] | None = None,
    wrote: set[str] | None = None,
) -> list[dict[str, Any]]:
    """The tool results of a request as blocks.

    Args:
        request: The request that carries the results.
        ok: Whether each call succeeded, by call id (a denied, failed or retried call is not ok).
        renamed: The names the model wrote for calls of tools not offered, by call id.
        wrote: Calls of tools that change data that ran, by call id: their result is marked
            ``wrote`` (a turn that changed data cannot be rewritten).

    Returns:
        Blocks.
    """
    ok = ok or {}
    renamed = renamed or {}
    wrote = wrote or set()
    blocks: list[dict[str, Any]] = []
    for p in request.parts:
        if isinstance(p, ToolReturnPart):
            good = ok.get(p.tool_call_id, p.outcome not in ("denied", "failed", "retried"))
            name = renamed.get(p.tool_call_id, p.tool_name)
            block = _result(p.tool_call_id, name, good, content_text(p.content))
            if p.tool_call_id in wrote:
                block["toolResult"]["wrote"] = True
            blocks.append(block)
    return blocks


def to_stored(
    messages: list[ModelMessage],
    args: dict[str, dict[str, Any]] | None = None,
    ok: dict[str, bool] | None = None,
    renamed: dict[str, str] | None = None,
) -> list[Stored]:
    """A run's history as stored messages (system prompt left out).

    Returns:
        The messages.
    """
    out: list[Stored] = []
    for m in messages:
        if isinstance(m, ModelResponse):
            blocks = response_blocks(m, args, renamed)
            if blocks:
                out.append(Stored(role="assistant", content=blocks))
            continue
        blocks = result_blocks(m, ok, renamed)
        for p in m.parts:
            if isinstance(p, UserPromptPart):
                text = p.content if isinstance(p.content, str) else content_text(p.content)
                if text.startswith(SUMMARY_HEADER):
                    blocks.append({"text": text, "summary": True})
                elif text:
                    blocks.append({"text": text})
        if blocks:
            out.append(Stored(role="user", content=blocks))
    return out


def _result(tool_use_id: str, name: str, ok: bool, text: str) -> dict[str, Any]:
    return {
        "toolResult": {
            "id": tool_use_id,
            "name": name,
            "text": text or ("ok" if ok else "errore"),
            "ok": ok,
        }
    }
