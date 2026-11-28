"""The Anthropic API: Claude Code (CLI and Desktop's Code tab) with a seat's OAuth login.

- ``anthropic.messages``: ``POST api.anthropic.com/v1/messages``, the public Messages API. A
  refusal is a **synthetic assistant answer** in the public format (SSE when ``stream``, JSON
  otherwise), so the tool shows it as Claude's reply and goes on. Measured, refusal verified in
  Desktop's Code tab and with the Claude Code CLI.
- ``anthropic.count_tokens``: ``POST .../v1/messages/count_tokens`` carries the same text (a leak
  the probe found): a refusal answers ``{"input_tokens": <estimate>}`` without forwarding.

The session key is the account uuid in ``metadata.user_id``; the account's e-mail and
organisation come from ``GET /api/oauth/profile``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, ClassVar

from evokai.gateway import checks
from evokai.gateway.proxy.adapters.base import (
    Adapter,
    Answer,
    FormatUnknown,
    Inspection,
    Request,
    Verdict,
    json_answer,
)
from evokai.gateway.proxy.identity import Key, account_from_user_id
from evokai.gateway.proxy.texts import Texts

HOST = "api.anthropic.com"
PROFILE_PATH = "/api/oauth/profile"


def surface(req: Request) -> str:
    """Desktop's Code tab or the Claude Code CLI, by the user agent.

    Returns:
        ``desktop_code`` or ``claude_code``.
    """
    return "desktop_code" if "claude-desktop" in req.header("user-agent") else "claude_code"


def _body(req: Request) -> dict[str, Any]:
    body = req.json()
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise FormatUnknown("no messages list")
    if not isinstance(body.get("model", ""), str):
        raise FormatUnknown("model is not a string")
    for msg in body["messages"]:
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), str | list):
            raise FormatUnknown("a message without content")
    return body


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def synthetic(model: str, text: str, *, stream: bool) -> Answer:
    """An assistant answer in the public Messages format, saying the text.

    Returns:
        The answer (200).
    """
    mid = "msg_evokai_" + uuid.uuid4().hex[:20]
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    message = {
        "id": mid,
        "type": "message",
        "role": "assistant",
        "model": model,
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    if not stream:
        full = {
            **message,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
        }
        return json_answer(200, full, **{"x-evokai-proxy": "refused"})
    parts = [
        _sse("message_start", {"type": "message_start", "message": {**message, "content": []}}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
            },
        ),
        _sse("message_stop", {"type": "message_stop"}),
    ]
    return Answer(
        200,
        {
            "content-type": "text/event-stream; charset=utf-8",
            "cache-control": "no-cache",
            "x-evokai-proxy": "refused",
        },
        b"".join(parts),
    )


class Messages(Adapter):
    """``POST api.anthropic.com/v1/messages``."""

    name: ClassVar[str] = "anthropic.messages"
    provider: ClassVar[str] = "anthropic"
    gateway_provider: ClassVar[str] = "anthropic"
    path: ClassVar[str] = "/v1/messages"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and host == HOST and path == self.path

    def key(self, req: Request) -> Key | None:
        """The account uuid in ``metadata.user_id``.

        Returns:
            The key, or None.
        """
        try:
            account = account_from_user_id(req.json())
        except FormatUnknown:
            return None
        return Key("anthropic", "account", account) if account else None

    def inspect(self, req: Request) -> Inspection | None:
        """The newest user turn and the tool results since the last assistant message.

        Returns:
            What to check.
        """
        body = _body(req)
        return Inspection(
            text=checks.anthropic_new_text(body),
            model=str(body.get("model") or ""),
            usage=True,
            surface=surface(req),
            data=body,
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """A synthetic assistant answer saying the refusal.

        Returns:
            The answer.
        """
        body = found.data if isinstance(found.data, dict) else {}
        return synthetic(found.model or "claude", refusal, stream=bool(body.get("stream")))

    def failed(self, req: Request, message: str) -> Answer:
        """Refusals before the checks are synthetic answers too (Claude Code shows them as is).

        Returns:
            The answer.
        """
        try:
            body = req.json()
        except FormatUnknown:
            body = {}
        body = body if isinstance(body, dict) else {}
        return synthetic(
            str(body.get("model") or "claude"), message, stream=bool(body.get("stream"))
        )


class CountTokens(Messages):
    """``POST api.anthropic.com/v1/messages/count_tokens``."""

    name: ClassVar[str] = "anthropic.count_tokens"
    path: ClassVar[str] = "/v1/messages/count_tokens"

    def inspect(self, req: Request) -> Inspection | None:
        """The same text as a Messages request.

        Returns:
            What to check (a count: no rate, no record).
        """
        found = super().inspect(req) or Inspection(text="")
        found.count = True
        found.record = False
        found.usage = False
        return found

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """An estimate, without forwarding the text.

        Returns:
            The answer.
        """
        return json_answer(200, {"input_tokens": max(1, len(req.body) // 4)})

    def failed(self, req: Request, message: str) -> Answer:  # ruff: ignore[unused-method-argument]
        """A count is never answered with text: an estimate, not forwarded.

        Returns:
            The answer.
        """
        return json_answer(200, {"input_tokens": max(1, len(req.body) // 4)})
