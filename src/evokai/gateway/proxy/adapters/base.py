"""Adapters: one per surface, each knowing a request's shape and how to refuse there.

An adapter matches a request (host, method, path), reads what is new in it (the newest user turn
and the tool results since the previous assistant message, as the base URL door does) and the
model, names the key of its session, and builds the refusal its app shows as an answer: a
synthetic answer, or the request rewritten so the real model says the refusal. The checks are the
gateway's: an adapter never decides.

A request on a known sensitive path whose body does not parse, or lacks the expected field, is a
``FormatUnknown``: it is refused, never forwarded unread (fail closed).

Adapters see neutral values (``Request``), never the network engine's objects, so the OpenAI and
Copilot adapters, and another engine, plug in unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from evokai.gateway.proxy.identity import Key
from evokai.gateway.proxy.texts import Texts


class FormatUnknown(Exception):
    """The request's shape is not the one the adapter knows."""

    def __init__(self, why: str) -> None:
        """Say what was missing (never a value of the request)."""
        super().__init__(why)
        self.why = why


@dataclass
class Request:
    """A request as adapters see it: decoded body, headers as sent (names lower case)."""

    method: str
    host: str
    path: str
    """Without the query."""
    headers: list[tuple[str, str]]
    body: bytes = b""
    query: str = ""
    client: str = ""
    """The PC's address."""
    _json: Any = field(default=None, repr=False)
    _parsed: bool = field(default=False, repr=False)

    def header(self, name: str) -> str:
        """A header's first value.

        Returns:
            The value, or an empty string.
        """
        name = name.lower()
        return next((v for k, v in self.headers if k == name), "")

    def all(self, name: str) -> list[str]:
        """Every value of a header.

        Returns:
            The values, in order.
        """
        name = name.lower()
        return [v for k, v in self.headers if k == name]

    def json(self) -> Any:
        """The body as JSON.

        Returns:
            The value.

        Raises:
            FormatUnknown: It is not JSON.
        """
        if not self._parsed:
            try:
                self._json = json.loads(self.body)
            except (ValueError, UnicodeDecodeError) as e:
                raise FormatUnknown("the body is not JSON") from e
            self._parsed = True
        return self._json


@dataclass
class Inspection:
    """What an adapter read from a request."""

    text: str
    """The new text the checks look at."""
    model: str = ""
    check_model: bool = True
    """False when the surface names no model in its requests."""
    count: bool = False
    """A token count: not a model call (no rate, no record)."""
    record: bool = True
    """One usage row for the request."""
    usage: bool = False
    """The answer carries token counts in the Anthropic or OpenAI format."""
    surface: str = ""
    data: Any = None
    """The parsed body, for the refusal."""
    parts: list[str] | None = None
    """The text of each unit a refusal may rewrite alone (Cowork's user events)."""
    offending: set[int] | None = None
    """Set by the service: the parts that break the policy (None: all)."""


@dataclass
class Answer:
    """A response the proxy gives itself (the request is not forwarded)."""

    status: int
    headers: dict[str, str]
    body: bytes


@dataclass
class Rewrite:
    """The request forwarded with another body (the real model says the refusal)."""

    body: bytes


Verdict = Answer | Rewrite


def json_answer(status: int, data: Any, **headers: str) -> Answer:
    """A JSON answer.

    Returns:
        The answer.
    """
    return Answer(
        status, {"content-type": "application/json", **headers}, json.dumps(data).encode()
    )


def error_answer(status: int, message: str) -> Answer:
    """An error in the Anthropic shape (claude.ai and the API both show its message).

    Returns:
        The answer.
    """
    return json_answer(
        status, {"type": "error", "error": {"type": "permission_error", "message": message}}
    )


class Adapter:
    """A surface's HTTP requests."""

    name: ClassVar[str] = ""
    provider: ClassVar[str] = ""
    """The catalogue provider (``evokai.gateway.proxy.catalogue``)."""
    gateway_provider: ClassVar[str] = ""
    """The gateway provider whose switch and checks apply (``anthropic``, ``openai``)."""
    mask: ClassVar[bool] = False
    """Matched values are masked instead of the request being refused."""

    def matches(self, method: str, host: str, path: str) -> bool:
        """Whether this adapter handles a request.

        Returns:
            True when it does.
        """
        raise NotImplementedError

    def key(self, req: Request) -> Key | None:
        """The key that names the request's session (``evokai.gateway.proxy.identity``).

        Returns:
            The key, or None when the request carries none.
        """
        raise NotImplementedError

    def inspect(self, req: Request) -> Inspection | None:
        """Read the request.

        Returns:
            What to check, or None when there is nothing to check (it is forwarded unchanged).

        Raises:
            FormatUnknown: The shape is not the known one.
        """
        raise NotImplementedError

    def refuse(self, req: Request, found: Inspection, texts: Texts, refusal: str) -> Verdict:
        """The refusal as this surface's app shows it.

        Args:
            req: The request.
            found: What ``inspect`` read.
            texts: The gateway's texts.
            refusal: What the person reads.

        Returns:
            An answer, or the rewritten request.
        """
        raise NotImplementedError

    def failed(self, req: Request, message: str) -> Answer:  # ruff: ignore[unused-method-argument] (the default ignores the request)
        """A refusal before any inspection (format, account, organisation, size).

        Returns:
            The answer.
        """
        return error_answer(403, message)


class FrameAdapter:
    """A surface's WebSocket messages (from the app to the server)."""

    name: ClassVar[str] = ""
    provider: ClassVar[str] = ""
    gateway_provider: ClassVar[str] = ""

    def matches(self, host: str, path: str) -> bool:
        """Whether this adapter reads a WebSocket.

        Returns:
            True when it does.
        """
        raise NotImplementedError

    def inspect(self, content: bytes) -> Inspection | None:
        """Read a message from the app.

        Returns:
            What to check, or None when the message carries nothing to check.

        Raises:
            FormatUnknown: It looks like what this adapter reads, in another shape.
        """
        raise NotImplementedError

    def refuse(self, content: bytes, found: Inspection, notice: str) -> bytes:
        """The message with what it carried replaced by a notice the agent relays.

        Returns:
            The new message.
        """
        raise NotImplementedError


def registry() -> tuple[list[Adapter], list[FrameAdapter]]:
    """Every adapter of this build, the most specific first (``mask`` last).

    Returns:
        (HTTP adapters, WebSocket adapters).
    """
    from evokai.gateway.proxy.adapters import anthropic, claude

    return (
        [
            anthropic.Messages(),
            anthropic.CountTokens(),
            claude.Chat(),
            claude.Cowork(),
            claude.Mask(),
        ],
        [claude.Bridge()],
    )


def providers_with_adapters() -> set[str]:
    """The catalogue providers at least one adapter handles.

    Returns:
        The providers.
    """
    http, ws = registry()
    return {a.provider for a in http if not a.mask} | {a.provider for a in ws}
