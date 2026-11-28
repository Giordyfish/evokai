"""claude.ai: Claude Desktop's Chat and Cowork, the Cowork bridge, and every other write.

- ``claude.chat``: ``POST claude.ai/api/organizations/{org}/chat_conversations/{id}/completion``
  (gzip JSON). Checked: ``prompt`` and the attachments' extracted text. A refusal **rewrites** the
  request: the prompt becomes the instruction to repeat the refusal and the attachments and files
  are emptied, so the real model answers it and the conversation goes on. Measured, verified.
- ``claude.cowork``: ``POST claude.ai/v1/code/sessions/{id}/events``. Checked: each ``user``
  event's ``message.content``. Only the events that break the policy are rewritten, to a visible
  marker and the instruction (the app shows the substitute in the person's bubble). Measured,
  verified.
- ``claude.bridge``: the WebSocket of ``bridge.claudeusercontent.com``. A JSON-RPC result from the
  app (a file Cowork read on the PC) is checked, and replaced by a notice the agent relays.
  Measured, verified.
- ``mask``: any other write on an intercepted host (titles carried the text in the probe): matched
  values become ``[bloccato]``. Sign-in paths and fields that hold credentials are left alone.

The session key of a claude.ai request is its ``sessionKey`` cookie; the account comes from
``GET claude.ai/api/account``. The organisation is the ``x-organization-uuid`` header.
"""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from evokai.gateway import checks
from evokai.gateway.proxy.adapters.base import (
    Adapter,
    FormatUnknown,
    FrameAdapter,
    Inspection,
    Request,
    Rewrite,
    Verdict,
)
from evokai.gateway.proxy.catalogue import provider_of
from evokai.gateway.proxy.identity import Key, claude_ai_key
from evokai.gateway.proxy.texts import Texts

HOST = "claude.ai"
ACCOUNT_PATH = "/api/account"
COMPLETION = re.compile(r"^/api/organizations/([^/]+)/chat_conversations/[^/]+/completion$")
COWORK_EVENTS = re.compile(r"^/v1/code/sessions/[^/]+/events$")
_ORG_IN_PATH = re.compile(r"^/api/organizations/([0-9a-fA-F-]{36})(?:/|$)")


def surface(req: Request, kind: str) -> str:
    """Desktop's Chat or Cowork, or claude.ai in a browser.

    Returns:
        ``desktop_chat``, ``desktop_cowork`` or ``web_chat``.
    """
    ua = req.header("user-agent")
    if kind == "cowork":
        return "desktop_cowork"
    return "desktop_chat" if ("Electron" in ua or "Claude" in ua) else "web_chat"


def org_of(req: Request) -> str | None:
    """The organisation a claude.ai request states.

    Returns:
        ``x-organization-uuid``, else the one in the path, else None.
    """
    if value := req.header("x-organization-uuid").strip():
        return value
    found = _ORG_IN_PATH.match(req.path)
    return found.group(1) if found else None


class _ClaudeAi(Adapter):
    provider: ClassVar[str] = "anthropic"
    gateway_provider: ClassVar[str] = "anthropic"

    def key(self, req: Request) -> Key | None:
        """The session cookie.

        Returns:
            The key, or None.
        """
        return claude_ai_key(req.all("cookie"), org_of(req))


def _blocks_text(content: Any) -> str:
    """The text of a message's content (a string, or blocks with tool results).

    Returns:
        The text.
    """
    return checks.anthropic_new_text({"messages": [{"role": "user", "content": content}]})


class Chat(_ClaudeAi):
    """``POST claude.ai/api/organizations/{org}/chat_conversations/{id}/completion``."""

    name: ClassVar[str] = "claude.chat"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and host == HOST and COMPLETION.match(path) is not None

    def inspect(self, req: Request) -> Inspection | None:
        """The prompt and the attachments' extracted text.

        Returns:
            What to check.

        Raises:
            FormatUnknown: No prompt, or attachments in an unknown shape.
        """
        body = req.json()
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), str):
            raise FormatUnknown("no prompt")
        attachments = body.get("attachments") or []
        if not isinstance(attachments, list):
            raise FormatUnknown("attachments is not a list")
        parts = [body["prompt"]]
        for a in attachments:
            if not isinstance(a, dict):
                raise FormatUnknown("an attachment is not an object")
            text = a.get("extracted_content")
            if text is None:
                text = json.dumps(a, ensure_ascii=False)
            parts.append(str(text))
        model = body.get("model")
        params = body.get("create_conversation_params")
        if not model and isinstance(params, dict):
            model = params.get("model")
        return Inspection(
            text="\n".join(parts),
            model=str(model or ""),
            check_model=bool(model),
            surface=surface(req, "chat"),
            data=body,
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """The prompt becomes the instruction to repeat the refusal; attachments are dropped.

        Returns:
            The rewritten request.
        """
        body = dict(found.data)
        body["prompt"] = texts.instruction(refusal)
        body["attachments"] = []
        body["files"] = []
        if "sync_sources" in body:
            body["sync_sources"] = []
        return Rewrite(json.dumps(body, ensure_ascii=False).encode())


class Cowork(_ClaudeAi):
    """``POST claude.ai/v1/code/sessions/{id}/events``."""

    name: ClassVar[str] = "claude.cowork"

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        return method == "POST" and host == HOST and COWORK_EVENTS.match(path) is not None

    @staticmethod
    def user_events(body: Any) -> list[dict[str, Any]]:
        """The ``user`` events' payloads of an events request.

        Returns:
            The payloads.

        Raises:
            FormatUnknown: Not an events list, or a user event without a message.
        """
        if not isinstance(body, dict) or not isinstance(body.get("events"), list):
            raise FormatUnknown("no events list")
        out = []
        for ev in body["events"]:
            payload = ev.get("payload") if isinstance(ev, dict) else None
            if not isinstance(payload, dict) or payload.get("type") != "user":
                continue
            msg = payload.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), str | list):
                raise FormatUnknown("a user event without message content")
            out.append(payload)
        return out

    def inspect(self, req: Request) -> Inspection | None:
        """Every user event's content (an events request without one passes unchecked).

        Returns:
            What to check, or None.
        """
        body = req.json()
        users = self.user_events(body)
        if not users:
            return None
        parts = [_blocks_text(p["message"]["content"]) for p in users]
        return Inspection(
            text="\n".join(parts),
            parts=parts,
            check_model=False,
            surface="desktop_cowork",
            data=body,
        )

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:  # ruff: ignore[unused-method-argument]
        """Rewrite the user events that break the policy (``found.offending``; None: every one).

        Returns:
            The rewritten request.
        """
        body = json.loads(json.dumps(found.data))
        replacement = texts.get("marker") + "\n\n" + texts.instruction(refusal)
        for n, payload in enumerate(self.user_events(body)):
            if found.offending is None or n in found.offending:
                payload["message"]["content"] = replacement
        return Rewrite(json.dumps(body, ensure_ascii=False).encode())


class Bridge(FrameAdapter):
    """Client messages on ``wss://bridge.claudeusercontent.com``: JSON-RPC tool results."""

    name: ClassVar[str] = "claude.bridge"
    provider: ClassVar[str] = "anthropic"
    gateway_provider: ClassVar[str] = "anthropic"

    def matches(self, host: str, path: str) -> bool:  # ruff: ignore[unused-method-argument]
        """Whether this adapter reads a WebSocket.

        Returns:
            True for the bridge host.
        """
        return host.startswith("bridge.") and host.endswith(".claudeusercontent.com")

    def inspect(self, content: bytes) -> Inspection | None:
        """A result's content (and structured content), as the agent would read it.

        Returns:
            What to check, or None for any other message.

        Raises:
            FormatUnknown: A result in an unknown shape.
        """
        if b'"result"' not in content:
            return None
        try:
            data = json.loads(content)
        except (ValueError, UnicodeDecodeError) as e:
            raise FormatUnknown("a result that is not JSON") from e
        if not isinstance(data, dict) or "result" not in data:
            return None
        result = data["result"]
        if not isinstance(result, dict):
            return None
        content_blocks = result.get("content")
        if content_blocks is not None and not isinstance(content_blocks, list | str):
            raise FormatUnknown("a result whose content is not a list")
        parts = [_blocks_text(content_blocks)] if content_blocks is not None else []
        if result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        if not parts:
            return None
        return Inspection(
            text="\n".join(parts),
            check_model=False,
            record=False,
            surface="desktop_cowork",
            data=data,
        )

    def refuse(self, content: bytes, found: Inspection, notice: str) -> bytes:  # ruff: ignore[unused-method-argument]
        """The result's content replaced by the notice.

        Returns:
            The new message.
        """
        data = dict(found.data)
        data["result"] = {"content": [{"type": "text", "text": notice}]}
        return json.dumps(data, ensure_ascii=False).encode()


_AUTH_PATH = re.compile(
    r"(?i)/(?:oauth|auth|login|logout|signin|signup|sso|magic_link|token)(?:/|$|_)"
)
_CREDENTIAL_KEY = re.compile(
    r"(?i)(token|secret|password|passwd|session|cookie|auth|signature|csrf|nonce|key$|^key|jwt|credential)"
)
_TEXTUAL = ("application/json", "text/", "application/x-www-form-urlencoded", "+json")
MASKED = "[bloccato]"


class Mask(Adapter):
    """Any other ``POST``, ``PUT`` or ``PATCH`` on an intercepted host."""

    name: ClassVar[str] = "mask"
    provider: ClassVar[str] = ""
    gateway_provider: ClassVar[str] = "anthropic"
    mask: ClassVar[bool] = True

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True for a write outside the sign-in paths.
        """
        return method in ("POST", "PUT", "PATCH") and not _AUTH_PATH.search(path) and bool(host)

    def key(self, req: Request) -> Key | None:
        """The claude.ai session cookie, when the request is claude.ai's.

        Returns:
            The key, or None.
        """
        if provider_of(req.host) != "anthropic":
            return None
        return claude_ai_key(req.all("cookie"), org_of(req))

    def inspect(self, req: Request) -> Inspection | None:
        """The whole body, when it is text.

        Returns:
            What to check, or None for a binary body.
        """
        kind = req.header("content-type").lower()
        if not req.body or not any(t in kind for t in _TEXTUAL):
            return None
        try:
            text = req.body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return Inspection(text=text, check_model=False, record=False, surface="", data=text)

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:
        """Never called: the service masks (``masked``)."""
        raise NotImplementedError


def masked(text: str, is_json: bool, mask: Any) -> str:
    """A body with the values ``mask`` changes masked.

    Every string of a JSON body (but fields that hold credentials), or the whole text.

    Args:
        text: The body.
        is_json: Whether to walk it as JSON (a body that does not parse is masked as text).
        mask: ``mask(str) -> str``.

    Returns:
        The new body (the same text when nothing changed).
    """
    if is_json:
        try:
            data = json.loads(text)
        except ValueError:
            return mask(text)
        changed = [False]

        def walk(v: Any, key: str = "") -> Any:
            if isinstance(v, str):
                if key and _CREDENTIAL_KEY.search(key):
                    return v
                out = mask(v)
                if out != v:
                    changed[0] = True
                return out
            if isinstance(v, list):
                return [walk(x, key) for x in v]
            if isinstance(v, dict):
                return {k: walk(x, str(k)) for k, x in v.items()}
            return v

        new = walk(data)
        return json.dumps(new, ensure_ascii=False) if changed[0] else text
    return mask(text)
