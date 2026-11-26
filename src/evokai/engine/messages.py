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
    NativeToolCallPart,
    TextContent,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from evokai.engine.builtin_tools import CHANGING, kind_of
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
    """An answer as blocks: its text, its tool calls, then its calls of built-in tools that act.

    A call of a provider built-in tool that can change data is kept as a mark
    (``{"builtinTool": {"id", "name", "changes": true}}``): the model never sees it again, and the
    answer cannot be regenerated.

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
    blocks += [
        {"builtinTool": {"id": p.tool_call_id, "name": p.tool_name, "changes": True}}
        for p in response.parts
        if isinstance(p, NativeToolCallPart) and kind_of(p.tool_name) in CHANGING
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
                attached = _file_block(p)
                if attached is not None:
                    blocks.append(attached)
                    continue
                text = p.content if isinstance(p.content, str) else content_text(p.content)
                if text.startswith(SUMMARY_HEADER):
                    blocks.append({"text": text, "summary": True})
                elif text:
                    blocks.append({"text": text})
        if blocks:
            out.append(Stored(role="user", content=blocks))
    return out


def _file_block(part: UserPromptPart) -> dict[str, Any] | None:
    """The attachment block a user prompt was built from (``history.file_part``), if any.

    Returns:
        The block, unchanged (never the bytes).
    """
    first = part.content[0] if isinstance(part.content, list) and part.content else None
    meta = first.metadata if isinstance(first, TextContent) else None
    block = meta.get("file") if isinstance(meta, dict) else None
    return block if isinstance(block, dict) else None


def _result(tool_use_id: str, name: str, ok: bool, text: str) -> dict[str, Any]:
    return {
        "toolResult": {
            "id": tool_use_id,
            "name": name,
            "text": text or ("ok" if ok else "errore"),
            "ok": ok,
        }
    }


# ── reasoning sent back ───────────────────────────────────────────────────────────────────────────
# A model's reasoning (``ThinkingPart``) is never part of the stored history the model sees again,
# with one exception the providers require: while a tool-use step is not over, its reasoning goes
# back with its calls, unchanged (Anthropic's thinking blocks with their signatures, OpenAI's
# encrypted reasoning). That is every later request of the same turn, and the turn that carries out
# a confirmation (the calls it waited on are still that step's).


def _thinks(response: ModelResponse) -> bool:
    return any(isinstance(p, ThinkingPart) for p in response.parts) and any(
        isinstance(p, ToolCallPart) for p in response.parts
    )


def with_reasoning(rebuilt: ModelResponse, original: ModelResponse) -> ModelResponse:
    """A response rebuilt from stored blocks, with the reasoning of the response it came from.

    The parts keep the original order (reasoning, text, calls), the text and the calls are the
    rebuilt ones (protected as the history is), and the provider's fields say whose reasoning it is.

    Returns:
        The response.
    """
    calls = {p.tool_call_id: p for p in rebuilt.parts if isinstance(p, ToolCallPart)}
    texts = [p for p in rebuilt.parts if isinstance(p, TextPart)]
    out: list[Any] = []
    for p in original.parts:
        if isinstance(p, ThinkingPart):
            out.append(p)
        elif isinstance(p, TextPart) and texts:
            out += texts
            texts = []
        elif isinstance(p, ToolCallPart) and p.tool_call_id in calls:
            out.append(calls.pop(p.tool_call_id))
    out += texts + list(calls.values())
    return ModelResponse(
        parts=out,
        model_name=original.model_name,
        provider_name=original.provider_name,
        provider_url=original.provider_url,
        canonical_system=original.canonical_system,
    )


def keep_reasoning(messages: list[ModelMessage], rebuilt: list[ModelMessage]) -> list[ModelMessage]:
    """The rebuilt history of a request, with the reasoning of its responses that called tools.

    Args:
        messages: The run's messages (pydantic-ai's, with their reasoning).
        rebuilt: The same history as the model receives it (from stored blocks, without it).

    Returns:
        ``rebuilt``, each response that called tools paired with its original by call id.
    """
    by_call = {
        p.tool_call_id: m
        for m in messages
        if isinstance(m, ModelResponse) and _thinks(m)
        for p in m.parts
        if isinstance(p, ToolCallPart)
    }
    if not by_call:
        return rebuilt
    out: list[ModelMessage] = []
    for m in rebuilt:
        if isinstance(m, ModelResponse):
            first = next((p for p in m.parts if isinstance(p, ToolCallPart)), None)
            original = by_call.get(first.tool_call_id) if first is not None else None
            if original is not None:
                m = with_reasoning(m, original)
        out.append(m)
    return out


def reasoning_block(response: ModelResponse) -> dict[str, Any] | None:
    """The reasoning of a response that waits for a confirmation, as a stored block.

    Kept only so the turn that carries the confirmation out can send it back with the calls
    (``resume_reasoning``); never shown, never sent with any other message.

    Returns:
        ``{"reasoning": {...}}``, or None when the response has no reasoning or no calls.
    """
    if not _thinks(response):
        return None
    parts: list[dict[str, Any]] = []
    for p in response.parts:
        if isinstance(p, ThinkingPart):
            parts.append({
                "thinking": p.content,
                "signature": p.signature,
                "id": p.id,
                "provider": p.provider_name,
                "details": p.provider_details,
            })
        elif isinstance(p, TextPart):
            parts.append({"text": True})
        elif isinstance(p, ToolCallPart):
            parts.append({"call": p.tool_call_id})
    return {
        "reasoning": {
            "model": response.model_name,
            "provider": response.provider_name,
            "url": response.provider_url,
            "system": response.canonical_system,
            "parts": parts,
        }
    }


def stored_reasoning(block: dict[str, Any]) -> ModelResponse:
    """A stored reasoning block back as the response it was taken from (calls without arguments).

    Returns:
        A response to pair with the rebuilt one (``with_reasoning``).
    """
    r = block.get("reasoning") or {}
    parts: list[Any] = []
    for p in r.get("parts") or []:
        if "thinking" in p:
            parts.append(
                ThinkingPart(
                    content=p.get("thinking") or "",
                    id=p.get("id"),
                    signature=p.get("signature"),
                    provider_name=p.get("provider"),
                    provider_details=p.get("details"),
                )
            )
        elif p.get("text"):
            parts.append(TextPart(content=""))
        elif p.get("call"):
            parts.append(ToolCallPart(tool_name="", args={}, tool_call_id=p["call"]))
    return ModelResponse(
        parts=parts,
        model_name=r.get("model"),
        provider_name=r.get("provider"),
        provider_url=r.get("url"),
        canonical_system=r.get("system"),
    )


def resume_reasoning(history: list[ModelMessage], stored: list[Any]) -> list[ModelMessage]:
    """The history of a confirmation being carried out, its waiting calls with their reasoning.

    Args:
        history: The rebuilt history, ending on the response whose calls waited.
        stored: The stored messages it was rebuilt from (``Stored``).

    Returns:
        The history (the same list when no reasoning was kept).
    """
    last = next((m for m in reversed(stored) if m.role == "assistant"), None)
    block = next(
        (b for b in (last.content if last else []) if isinstance(b, dict) and "reasoning" in b),
        None,
    )
    at = next(
        (i for i in range(len(history) - 1, -1, -1) if isinstance(history[i], ModelResponse)), None
    )
    if block is None or at is None:
        return history
    rebuilt = history[at]
    assert isinstance(rebuilt, ModelResponse)  # ruff: ignore[assert] (found above)
    return [*history[:at], with_reasoning(rebuilt, stored_reasoning(block)), *history[at + 1 :]]
