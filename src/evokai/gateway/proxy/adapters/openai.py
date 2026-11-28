"""OpenAI: Codex (CLI, IDE, desktop app) with a ChatGPT login, the OpenAI API, ChatGPT's chat.

- ``openai.codex_ws``: the WebSocket ``chatgpt.com/backend-api/codex/responses`` (and the OpenAI
  API's ``api.openai.com/v1/responses``). Each ``response.create`` message from Codex is one model
  request: its new items (the person's input and the tool outputs since the model's last item) are
  checked. A refusal **rewrites** the message: the person's input becomes the instruction to repeat
  the refusal and an offending tool output becomes a notice carrying it, so the real model answers
  and Codex shows the refusal as its reply (the conversation goes on). A refusal before the checks
  (format, account, organisation) drops the message and answers Codex itself with a synthetic event
  sequence in the public Responses streaming format. Measured; verified with the Codex CLI 0.167.2.
- ``openai.responses_http``: ``POST chatgpt.com/backend-api/codex/responses`` and
  ``POST api.openai.com/v1/responses`` (Codex without WebSocket, other Responses clients). A
  refusal is a **synthetic answer** in the public Responses format (SSE when ``stream``, JSON
  otherwise), as the base URL door reads it. Tested with fixtures.
- ``openai.chatgpt``: ``POST chatgpt.com/backend-api/conversation`` (ChatGPT on the web and the
  desktop app). The newest user message's text parts are checked and, on a refusal, rewritten to
  the instruction. Best effort, not measured.

Identity: every request carries the ChatGPT login's JWT, whose claims name the user, the e-mail
and the ChatGPT account (``evokai.gateway.proxy.identity.openai_identity``).
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any, ClassVar

from evokai.gateway import checks
from evokai.gateway.proxy.adapters.base import (
    Adapter,
    Answer,
    FormatUnknown,
    Frame,
    FrameAdapter,
    Inspection,
    Request,
    Rewrite,
    Verdict,
    json_answer,
)
from evokai.gateway.proxy.identity import Claims, Key, openai_identity
from evokai.gateway.proxy.texts import Texts

CHATGPT = "chatgpt.com"
API = "api.openai.com"
CODEX_PATH = "/backend-api/codex/responses"
API_PATH = "/v1/responses"
CONVERSATION_PATHS = frozenset({"/backend-api/conversation", "/backend-api/f/conversation"})
_ENDS = {
    "response.completed": "forwarded",
    "response.incomplete": "forwarded",
    "response.failed": "upstream_error",
    "error": "upstream_error",
}


def _key(req: Request) -> Key | None:
    found = openai_identity(req.header("authorization"))
    return found[0] if found else None


def _claims(req: Request) -> Claims | None:
    found = openai_identity(req.header("authorization"))
    return found[1] if found else None


def _responses_body(data: Any) -> dict[str, Any]:
    """A Responses request, checked for the fields the checks read.

    Returns:
        The body.

    Raises:
        FormatUnknown: No input, or a model that is not a string.
    """
    if not isinstance(data, dict) or not isinstance(data.get("input"), list | str):
        raise FormatUnknown("no input list")
    if not isinstance(data.get("model", ""), str):
        raise FormatUnknown("model is not a string")
    return data


def response_events(model: str, text: str) -> list[dict[str, Any]]:
    """A whole answer saying the text, as the public Responses streaming events.

    Returns:
        The events, from ``response.created`` to ``response.completed``.
    """
    rid = "resp_evokai_" + uuid.uuid4().hex[:24]
    mid = "msg_evokai_" + uuid.uuid4().hex[:24]
    response: dict[str, Any] = {
        "id": rid,
        "object": "response",
        "created_at": int(time.time()),
        "status": "in_progress",
        "model": model,
        "output": [],
        "usage": None,
    }
    item = {"id": mid, "type": "message", "status": "in_progress", "role": "assistant"}
    part = {"type": "output_text", "text": text, "annotations": []}
    done = {**item, "status": "completed", "content": [part]}
    at = {"item_id": mid, "output_index": 0, "content_index": 0}
    usage = {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }
    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": response},
        {"type": "response.in_progress", "response": response},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
        {"type": "response.content_part.added", **at, "part": {**part, "text": ""}},
        {"type": "response.output_text.delta", **at, "delta": text},
        {"type": "response.output_text.done", **at, "text": text},
        {"type": "response.content_part.done", **at, "part": part},
        {"type": "response.output_item.done", "output_index": 0, "item": done},
        {
            "type": "response.completed",
            "response": {**response, "status": "completed", "output": [done], "usage": usage},
        },
    ]
    for n, event in enumerate(events):
        event["sequence_number"] = n
    return events


def synthetic(model: str, text: str, *, stream: bool) -> Answer:
    """An answer in the public Responses format, saying the text.

    Returns:
        The answer (200): SSE when ``stream``, else the response object.
    """
    events = response_events(model or "gpt", text)
    if not stream:
        return json_answer(200, events[-1]["response"], **{"x-evokai-proxy": "refused"})
    body = b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n".encode()
        for e in events
    )
    return Answer(
        200,
        {
            "content-type": "text/event-stream; charset=utf-8",
            "cache-control": "no-cache",
            "x-evokai-proxy": "refused",
        },
        body,
    )


def _new_parts(data: dict[str, Any]) -> tuple[list[tuple[int, str, str]], str]:
    items = checks.openai_new_items(data)
    return items, "\n".join(text for _, _, text in items if text)


def rewrite_items(
    data: dict[str, Any], offending: set[int] | None, texts: Texts, refusal: str
) -> dict[str, Any]:
    """A Responses request whose offending new items say the refusal instead.

    The person's input becomes the instruction to repeat the refusal; a tool output becomes a
    notice that carries the same instruction.

    Args:
        data: The request.
        offending: Which new items break the policy (None: every one).
        texts: The gateway's texts.
        refusal: What the person reads.

    Returns:
        The new request.
    """
    data = copy.deepcopy(data)
    items, _ = _new_parts(data)
    entries: Any = data.get("input")
    for n, (i, kind, _) in enumerate(items):
        if offending is not None and n not in offending:
            continue
        if i < 0:
            data["input"] = texts.instruction(refusal)
        elif kind == "user":
            entries[i]["content"] = [{"type": "input_text", "text": texts.instruction(refusal)}]
        else:
            entries[i]["output"] = texts.get("tool_notice", refusal=refusal)
    return data


class CodexSocket(FrameAdapter):
    """``response.create`` messages on Codex's Responses WebSocket."""

    name: ClassVar[str] = "openai.codex_ws"
    provider: ClassVar[str] = "openai"
    gateway_provider: ClassVar[str] = "openai"

    def matches(self, host: str, path: str) -> bool:
        """Whether this adapter reads a WebSocket.

        Returns:
            True for the Codex backend's and the OpenAI API's Responses WebSocket.
        """
        return (host == CHATGPT and path == CODEX_PATH) or (host == API and path == API_PATH)

    def key(self, upgrade: Request) -> Key | None:
        """The ChatGPT login of the request that opened the WebSocket.

        Returns:
            The key, or None.
        """
        return _key(upgrade)

    def claims(self, upgrade: Request) -> Claims | None:
        """The account the login's JWT names.

        Returns:
            The claims, or None.
        """
        return _claims(upgrade)

    def inspect(self, content: bytes) -> Inspection | None:
        """A ``response.create`` message's new items (any other message passes unchecked).

        A warm-up (``generate: false``, no output) is not a model request.

        Returns:
            What to check, or None.

        Raises:
            FormatUnknown: A ``response.create`` without an input list.
        """
        if b"response.create" not in content:
            return None
        try:
            data = json.loads(content)
        except (ValueError, UnicodeDecodeError) as e:
            raise FormatUnknown("a response.create that is not JSON") from e
        if not isinstance(data, dict) or data.get("type") != "response.create":
            return None
        data = _responses_body(data)
        items, text = _new_parts(data)
        if data.get("generate") is False and not text:
            return None
        return Inspection(
            text=text,
            model=str(data.get("model") or ""),
            usage=True,
            surface="codex",
            data=data,
            parts=[t for _, _, t in items],
        )

    def refuse(self, content: bytes, found: Inspection, texts: Texts, notice: str) -> bytes:  # ruff: ignore[unused-method-argument]
        """The offending items say the refusal; the real model repeats it.

        Returns:
            The new message.
        """
        new = rewrite_items(found.data, found.offending, texts, notice)
        return json.dumps(new, ensure_ascii=False).encode()

    def failed(self, content: bytes, found: Inspection | None, texts: Texts, message: str) -> Frame:  # ruff: ignore[unused-method-argument]
        """The message is not sent: Codex gets the refusal as a whole synthetic answer.

        Returns:
            Drop it, and the synthetic events.
        """
        model = found.model if found is not None else ""
        reply = [
            json.dumps(e, ensure_ascii=False).encode() for e in response_events(model, message)
        ]
        return Frame(drop=True, reply=reply)

    def ends(self, content: bytes) -> str | None:
        """Whether a server message ends an answer (``response.completed`` and the failures).

        Returns:
            The outcome, or None.
        """
        if not any(f'"{kind}"'.encode() in content for kind in _ENDS):
            return None
        try:
            data = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            return None
        return _ENDS.get(data.get("type")) if isinstance(data, dict) else None


class Responses(Adapter):
    """``POST chatgpt.com/backend-api/codex/responses`` and ``POST api.openai.com/v1/responses``."""

    name: ClassVar[str] = "openai.responses_http"
    provider: ClassVar[str] = "openai"
    gateway_provider: ClassVar[str] = "openai"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and (
            (host == CHATGPT and path == CODEX_PATH) or (host == API and path == API_PATH)
        )

    def key(self, req: Request) -> Key | None:
        """The ChatGPT login (or the API key).

        Returns:
            The key, or None.
        """
        return _key(req)

    def claims(self, req: Request) -> Claims | None:
        """The account the login's JWT names.

        Returns:
            The claims, or None.
        """
        return _claims(req)

    def inspect(self, req: Request) -> Inspection | None:
        """The new items, as the base URL door reads them.

        Returns:
            What to check.
        """
        body = _responses_body(req.json())
        _, text = _new_parts(body)
        return Inspection(
            text=text,
            model=str(body.get("model") or ""),
            usage=True,
            surface="codex",
            data=body,
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """A synthetic answer saying the refusal.

        Returns:
            The answer.
        """
        body = found.data if isinstance(found.data, dict) else {}
        return synthetic(found.model, refusal, stream=bool(body.get("stream")))

    def failed(self, req: Request, message: str) -> Answer:
        """Refusals before the checks are synthetic answers too.

        Returns:
            The answer.
        """
        try:
            body = req.json()
        except FormatUnknown:
            body = {}
        body = body if isinstance(body, dict) else {}
        return synthetic(
            str(body.get("model") or ""), message, stream=body.get("stream") is not False
        )


class ChatGPT(Adapter):
    """``POST chatgpt.com/backend-api/conversation`` (best effort, not measured)."""

    name: ClassVar[str] = "openai.chatgpt"
    provider: ClassVar[str] = "openai"
    gateway_provider: ClassVar[str] = "openai"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and host == CHATGPT and path in CONVERSATION_PATHS

    def key(self, req: Request) -> Key | None:
        """The ChatGPT login.

        Returns:
            The key, or None.
        """
        return _key(req)

    def claims(self, req: Request) -> Claims | None:
        """The account the login's JWT names.

        Returns:
            The claims, or None.
        """
        return _claims(req)

    @staticmethod
    def newest(body: Any) -> dict[str, Any] | None:
        """The newest message of a conversation request, if it is the person's.

        Returns:
            Its ``content``, or None when the request sends no new message (a regenerate).

        Raises:
            FormatUnknown: No messages list, or a message without text parts.
        """
        if not isinstance(body, dict):
            raise FormatUnknown("the body is not an object")
        messages = body.get("messages")
        if not messages and body.get("action") not in (None, "next"):
            return None
        if not isinstance(messages, list) or not messages:
            raise FormatUnknown("no messages list")
        last = messages[-1]
        author = last.get("author") if isinstance(last, dict) else None
        if not isinstance(author, dict) or author.get("role") != "user":
            return None
        content = last.get("content")
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            raise FormatUnknown("a message without content parts")
        return content

    def inspect(self, req: Request) -> Inspection | None:
        """The newest user message's text parts.

        Returns:
            What to check, or None.
        """
        body = req.json()
        content = self.newest(body)
        if content is None:
            return None
        parts = []
        for p in content["parts"]:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        model = str(body.get("model") or "")
        return Inspection(
            text="\n".join(parts),
            model=model,
            check_model=bool(model),
            surface="chatgpt",
            data=body,
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """The newest message becomes the instruction to repeat the refusal; attachments go.

        Returns:
            The rewritten request.
        """
        body = copy.deepcopy(found.data)
        last = body["messages"][-1]
        last["content"] = {"content_type": "text", "parts": [texts.instruction(refusal)]}
        meta = last.get("metadata")
        if isinstance(meta, dict):
            meta.pop("attachments", None)
        return Rewrite(json.dumps(body, ensure_ascii=False).encode())

    def failed(self, req: Request, message: str) -> Answer:  # ruff: ignore[unused-method-argument]
        """An error ChatGPT shows (its ``detail``).

        Returns:
            The answer.
        """
        return json_answer(403, {"detail": message})
