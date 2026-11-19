"""Thread history.

Stored in a provider-neutral block format, windowed and repaired before every model call, then
converted to pydantic-ai messages (so any provider, and a fallback to another provider, sees the
same history).

Blocks: {"text": str} · {"toolUse": {"id", "name", "input"}} · {"toolResult": {"id", "name", "text",
"ok"}}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

INTERRUPTED = "Interrotto."


@dataclass
class Stored:
    """A stored message as the window and repair see it."""

    role: str
    content: list[dict[str, Any]]


def _is_plain_user(m: Stored) -> bool:
    return (
        m.role == "user"
        and any("text" in b for b in m.content)
        and not any("toolResult" in b for b in m.content)
    )


def window(messages: list[Stored], max_messages: int, max_chars: int) -> list[Stored]:
    """The last messages within the caps, starting at a plain user message.

    Never at a tool result, which needs the call before it.

    Returns:
        The window, repaired.
    """
    picked: list[Stored] = []
    chars = 0
    for m in reversed(messages):
        if len(picked) >= max_messages:
            break
        size = len(json.dumps(m.content, ensure_ascii=False))
        if picked and chars + size > max_chars:
            break
        picked.insert(0, m)
        chars += size
    while picked and not _is_plain_user(picked[0]):
        picked.pop(0)
    return repair([Stored(role=m.role, content=list(m.content)) for m in picked])


def repair(messages: list[Stored]) -> list[Stored]:
    """Strict user/assistant alternation and a result for every tool call.

    Merge repeated roles, and answer calls left without a result (an interrupted or superseded turn)
    as interrupted.

    Returns:
        The repaired messages.
    """
    out: list[Stored] = []
    for m in messages:
        if out and out[-1].role == m.role:
            out[-1].content.extend(m.content)
        else:
            out.append(Stored(role=m.role, content=list(m.content)))
    i = 0
    while i < len(out):
        m = out[i]
        if m.role == "assistant":
            calls = [
                (b["toolUse"]["id"], b["toolUse"]["name"]) for b in m.content if "toolUse" in b
            ]
            if calls:
                nxt = out[i + 1] if i + 1 < len(out) else None
                answered = (
                    {b["toolResult"]["id"] for b in nxt.content if "toolResult" in b}
                    if nxt and nxt.role == "user"
                    else set()
                )
                fill = [
                    {"toolResult": {"id": cid, "name": name, "text": INTERRUPTED, "ok": False}}
                    for cid, name in calls
                    if cid not in answered
                ]
                if fill:
                    if nxt and nxt.role == "user":
                        nxt.content[:0] = fill
                    else:
                        out.insert(i + 1, Stored(role="user", content=fill))
        i += 1
    return out


def flatten_tools(messages: list[Stored], known: set[str]) -> list[Stored]:
    """Turn calls of tools the agent does not have into plain text.

    After a handoff the conversation may go on with an agent that has no tools at all: the earlier
    agent's tool calls and results then stay in the history as text (Bedrock refuses tool calls in
    a history sent without a tool configuration).

    Returns:
        The messages (the same objects when nothing changes).
    """
    foreign = {
        b["toolUse"]["id"]
        for m in messages
        for b in m.content
        if "toolUse" in b and b["toolUse"].get("name") not in known
    }
    if not foreign:
        return messages
    out: list[Stored] = []
    for m in messages:
        blocks: list[dict[str, Any]] = []
        for b in m.content:
            if "toolUse" in b and b["toolUse"]["id"] in foreign:
                tu = b["toolUse"]
                args = json.dumps(tu.get("input") or {}, ensure_ascii=False)[:2_000]
                blocks.append({"text": f"[called {tu.get('name')} with {args}]"})
            elif "toolResult" in b and b["toolResult"]["id"] in foreign:
                tr = b["toolResult"]
                blocks.append({
                    "text": f"[result of {tr.get('name')}: {(tr.get('text') or '')[:4_000]}]"
                })
            else:
                blocks.append(b)
        out.append(Stored(role=m.role, content=blocks))
    return out


def to_model_messages(system: str, messages: list[Stored]) -> list[ModelMessage]:
    """Convert to pydantic-ai messages, with the system prompt first.

    Returns:
        Model messages.
    """
    out: list[ModelMessage] = []
    for m in messages:
        if m.role == "assistant":
            parts: list[Any] = []
            for b in m.content:
                if b.get("text"):
                    parts.append(TextPart(content=b["text"]))
                elif "toolUse" in b:
                    tu = b["toolUse"]
                    parts.append(
                        ToolCallPart(
                            tool_name=tu["name"], args=tu.get("input") or {}, tool_call_id=tu["id"]
                        )
                    )
            if parts:
                out.append(ModelResponse(parts=parts))
        else:
            parts = []
            for b in m.content:
                if "toolResult" in b:
                    tr = b["toolResult"]
                    parts.append(
                        ToolReturnPart(
                            tool_name=tr["name"],
                            content=tr["text"] or ("ok" if tr.get("ok") else "errore"),
                            tool_call_id=tr["id"],
                        )
                    )
                elif b.get("text"):
                    parts.append(UserPromptPart(content=b["text"]))
            if parts:
                out.append(ModelRequest(parts=parts))
    if out and isinstance(out[0], ModelRequest):
        out[0] = ModelRequest(parts=[SystemPromptPart(content=system), *out[0].parts])
    else:
        out.insert(0, ModelRequest(parts=[SystemPromptPart(content=system)]))
    return out


def open_ask(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The latest ui_ask event, if the user has not written anything since.

    Returns:
        The ask event (question, options), or None.
    """
    for m in reversed(messages):
        if m["role"] == "user" and m.get("text"):
            return None
        if m["role"] == "assistant":
            for e in m.get("events") or []:
                if isinstance(e, dict) and e.get("type") == "ask" and e.get("question"):
                    return e
    return None


def open_question(messages: list[dict[str, Any]]) -> str | None:
    """The question of the latest ui_ask, if the user has not written anything since.

    Returns:
        The question, or None.
    """
    ask = open_ask(messages)
    return str(ask["question"]) if ask else None
