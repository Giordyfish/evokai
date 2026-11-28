"""GitHub Copilot (best effort: built from the public formats, not measured on this project).

- ``github.copilot_chat``: ``POST *.githubcopilot.com/chat/completions`` (Copilot Chat in the IDEs
  and the Copilot CLI), and ``/responses`` and ``/v1/messages`` for the models Copilot serves in
  those formats. Checked: the ``user`` and ``tool`` messages after the last ``assistant`` message
  (the newest items, or the newest user turn, in the other two formats). A refusal is a
  **synthetic answer** in the request's own format: OpenAI chat completion chunks over SSE (JSON
  when not streaming), Responses events, or Anthropic Messages events.
- ``github.copilot_completions``: the inline suggestions,
  ``POST .../v1/engines/{engine}/completions`` or ``.../completions`` on the Copilot hosts.
  Checked: ``prompt`` and ``suffix`` (the code around the cursor). A refusal is an **empty
  completion** (no suggestion) and a security event. They are not recorded as usage rows, nor
  counted by the rate limit (one per keystroke pause).

Identity: the Copilot token's ``tid``, bound from ``api.github.com``'s answers
(``evokai.gateway.proxy.identity``).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, ClassVar

from evokai.gateway import checks
from evokai.gateway.proxy.adapters import anthropic as anthropic_format
from evokai.gateway.proxy.adapters import openai as openai_format
from evokai.gateway.proxy.adapters.base import (
    Adapter,
    Answer,
    FormatUnknown,
    Inspection,
    Request,
    Verdict,
    json_answer,
)
from evokai.gateway.proxy.identity import Key, copilot_key
from evokai.gateway.proxy.texts import Texts

PROXY_HOST = "copilot-proxy.githubusercontent.com"
CHAT_PATHS = {
    "/chat/completions": "chat",
    "/v1/chat/completions": "chat",
    "/responses": "responses",
    "/v1/responses": "responses",
    "/v1/messages": "messages",
}
COMPLETIONS = re.compile(r"^(?:/v1/engines/[^/]+)?/(?:v1/)?completions$")


def copilot_host(host: str) -> bool:
    """Whether a host is one of Copilot's model hosts.

    Returns:
        True for ``*.githubcopilot.com`` and the completions proxy.
    """
    return host.endswith(".githubcopilot.com") or host == PROXY_HOST


def _sse_data(data: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


_SSE_HEADERS = {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache",
    "x-evokai-proxy": "refused",
}


def chat_synthetic(model: str, text: str, *, stream: bool) -> Answer:
    """An answer in the OpenAI chat completions format, saying the text.

    Returns:
        The answer (200): chunks over SSE ending with ``[DONE]``, or a ``chat.completion``.
    """
    cid = "chatcmpl-evokai-" + uuid.uuid4().hex[:20]
    base = {"id": cid, "created": int(time.time()), "model": model or "copilot"}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not stream:
        full = {
            **base,
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage,
        }
        return json_answer(200, full, **{"x-evokai-proxy": "refused"})
    chunk = {**base, "object": "chat.completion.chunk"}
    body = b"".join([
        _sse_data({
            **chunk,
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}
            ],
        }),
        _sse_data({
            **chunk,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }),
        b"data: [DONE]\n\n",
    ])
    return Answer(200, dict(_SSE_HEADERS), body)


def empty_completion(model: str, *, stream: bool) -> Answer:
    """A completion with no suggestion.

    Returns:
        The answer (200).
    """
    data = {
        "id": "cmpl-evokai-" + uuid.uuid4().hex[:20],
        "object": "text_completion",
        "created": int(time.time()),
        "model": model or "copilot",
        "choices": [{"text": "", "index": 0, "finish_reason": "stop", "logprobs": None}],
    }
    if not stream:
        return json_answer(200, data, **{"x-evokai-proxy": "refused"})
    return Answer(200, dict(_SSE_HEADERS), _sse_data(data) + b"data: [DONE]\n\n")


def _body(req: Request) -> dict[str, Any]:
    body = req.json()
    if not isinstance(body, dict):
        raise FormatUnknown("the body is not an object")
    if not isinstance(body.get("model", ""), str):
        raise FormatUnknown("model is not a string")
    return body


class Chat(Adapter):
    """Copilot Chat's model requests (best effort, not measured)."""

    name: ClassVar[str] = "github.copilot_chat"
    provider: ClassVar[str] = "github"
    gateway_provider: ClassVar[str] = "github"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and host.endswith(".githubcopilot.com") and path in CHAT_PATHS

    def key(self, req: Request) -> Key | None:
        """The Copilot token's ``tid``.

        Returns:
            The key, or None.
        """
        return copilot_key(req.header("authorization"))

    def inspect(self, req: Request) -> Inspection | None:
        """The newest user and tool messages, in the request's format.

        Returns:
            What to check.

        Raises:
            FormatUnknown: The format's messages (or input) are missing.
        """
        body = _body(req)
        kind = CHAT_PATHS[req.path]
        if kind == "chat":
            messages = body.get("messages")
            if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
                raise FormatUnknown("no messages list")
            text = checks.chat_new_text(body)
        elif kind == "responses":
            if not isinstance(body.get("input"), list | str):
                raise FormatUnknown("no input list")
            text = checks.openai_new_text(body)
        else:
            if not isinstance(body.get("messages"), list):
                raise FormatUnknown("no messages list")
            text = checks.anthropic_new_text(body)
        return Inspection(
            text=text,
            model=str(body.get("model") or ""),
            usage=True,
            surface="copilot",
            data=body,
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """A synthetic answer saying the refusal, in the request's format.

        Returns:
            The answer.
        """
        body = found.data if isinstance(found.data, dict) else {}
        return self._say(req.path, found.model, refusal, stream=bool(body.get("stream")))

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
        model = body.get("model")
        model = model if isinstance(model, str) else ""
        return self._say(req.path, model, message, stream=bool(body.get("stream")))

    @staticmethod
    def _say(path: str, model: str, text: str, *, stream: bool) -> Answer:
        kind = CHAT_PATHS.get(path, "chat")
        if kind == "responses":
            return openai_format.synthetic(model, text, stream=stream)
        if kind == "messages":
            return anthropic_format.synthetic(model or "claude", text, stream=stream)
        return chat_synthetic(model, text, stream=stream)


class Completions(Adapter):
    """Copilot's inline suggestions (best effort, not measured)."""

    name: ClassVar[str] = "github.copilot_completions"
    provider: ClassVar[str] = "github"
    gateway_provider: ClassVar[str] = "github"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and copilot_host(host) and COMPLETIONS.match(path) is not None

    def key(self, req: Request) -> Key | None:
        """The Copilot token's ``tid``.

        Returns:
            The key, or None.
        """
        return copilot_key(req.header("authorization"))

    def inspect(self, req: Request) -> Inspection | None:
        """The code around the cursor (``prompt`` and ``suffix``).

        Not a recorded model request and not rate limited: its content is checked, as a token
        count's is. Repeats in the same file share a fingerprint (one event every few minutes): the
        prompt's first line (Copilot opens it with the file's path) and the language.

        Returns:
            What to check.

        Raises:
            FormatUnknown: No prompt.
        """
        body = _body(req)
        prompt = body.get("prompt")
        suffix = body.get("suffix") or ""
        if not isinstance(prompt, str) or not isinstance(suffix, str):
            raise FormatUnknown("no prompt")
        extra = body.get("extra")
        extra = extra if isinstance(extra, dict) else {}
        first = prompt.split("\n", 1)[0][:300]
        head = f"{extra.get('language', '')}\x00{first}".encode("utf-8", "surrogatepass")
        return Inspection(
            text=prompt + "\n" + suffix,
            model=str(body.get("model") or ""),
            check_model=False,
            count=True,
            record=False,
            surface="copilot",
            data=body,
            fingerprint="completion:" + hashlib.sha256(head).hexdigest()[:24],
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """An empty completion: no suggestion.

        Returns:
            The answer.
        """
        body = found.data if isinstance(found.data, dict) else {}
        return empty_completion(found.model, stream=bool(body.get("stream")))

    def failed(self, req: Request, message: str) -> Answer:  # ruff: ignore[unused-method-argument]
        """No suggestion either (a suggestion has nowhere to show a message).

        Returns:
            The answer.
        """
        try:
            body = req.json()
        except FormatUnknown:
            body = {}
        body = body if isinstance(body, dict) else {}
        return empty_completion("", stream=bool(body.get("stream")))
