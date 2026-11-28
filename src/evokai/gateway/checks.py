"""The controls of a gateway request, before it is forwarded.

In order: the provider is enabled on the gateway, the model is allowed, the data protection
profiles (the gateway's and the system policy's) let the provider have the data, the person's rate
and token caps, then the guard's input checks that make sense for a coding tool, on the **new text
only**: the newest user turn's text and the tool results since the previous assistant message
(what the person or their files add now). The history was checked when it was new; the tool's own
system prompt is not the person's.

The input checks are ``input.size`` (``input.max_chars``), ``input.secrets``, ``pii.model`` (the
guard's personal data types, ``pii.types``) and ``scope`` (``scope.deny``), each run with a
linear-time matcher (``evokai.gateway.scan``). In ``monitor`` a check records a security event; in
``enforce`` it refuses the request. A gateway never masks: changing the request would break the
provider's prompt cache and the tool's own bookkeeping. The injection and moderation classifiers,
hidden-text cleaning, tool controls and confirmations do not apply to a gateway.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any

from evokai.core.util import new_id, utcnow
from evokai.gateway import scan
from evokai.guard.config import Effective
from evokai.guard.pii import PII_TYPES

log = logging.getLogger("evokai.gateway")

ANTHROPIC = "anthropic"
OPENAI = "openai"
GITHUB = "github"
PROVIDERS = (ANTHROPIC, OPENAI, GITHUB)
"""A gateway's provider switches. ``github`` (GitHub Copilot) has no base URL door: only the gateway
proxy serves it."""
PROVIDER_NAMES = {ANTHROPIC: "Anthropic", OPENAI: "OpenAI", GITHUB: "GitHub Copilot"}
GATEWAY_CONTROLS = ("input.size", "input.secrets", "pii.model", "scope", "abuse.rate")
"""The guard's controls that apply on a gateway (the policy's other controls do nothing there)."""
PII_LIMIT = 20
"""Matches per detector a gateway looks for: it needs which types a text holds, not all of them."""
DEFAULT_MAX_CHARS = 400_000
"""``input.max_chars`` of a new gateway's policy: the new text of one request (a file or two)."""
DEFAULT_REQUESTS_PER_MINUTE = 120
"""``abuse.requests_per_minute`` of a new gateway's policy (a coding tool and its subagents)."""


def initial_policy() -> dict[str, Any]:
    """The security policy a new gateway starts with (version 1, changed like any other).

    Returns:
        The policy.
    """
    return {
        "input": {"max_chars": DEFAULT_MAX_CHARS},
        "abuse": {"requests_per_minute": DEFAULT_REQUESTS_PER_MINUTE},
    }


@dataclass
class Block:
    """A refused request: the control, the HTTP status and what the person reads."""

    control: str
    status: int
    kind: str
    """``invalid_request``, ``permission`` or ``rate_limit`` (the provider's error type)."""
    message: str
    retry_after: int | None = None


@dataclass
class Checked:
    """What the controls did: the events to store, and the block if one refused the request."""

    events: list[dict[str, Any]] = field(default_factory=list)
    block: Block | None = None
    fingerprint: str = ""
    """What the controls looked at (the new text's digest, or the model): a tool that sends the
    same refused request again (Claude Code retries a 400 a few times) records its events once."""


# The new text of a request


def _anthropic_blocks(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    out: list[str] = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "text":
            out.append(str(b.get("text") or ""))
        elif kind == "tool_result":
            out += _anthropic_blocks(b.get("content"))
        elif kind == "document":
            src = b.get("source") if isinstance(b.get("source"), dict) else {}
            if src.get("type") == "text":
                out.append(str(src.get("data") or ""))
            elif src.get("type") == "content":
                out += _anthropic_blocks(src.get("content"))
    return out


def anthropic_new_text(body: dict[str, Any]) -> str:
    """The new text of an Anthropic Messages request.

    The ``user`` messages after the last ``assistant`` message: their text and tool results.
    Mid-conversation ``system`` entries are the tool's own reminders, not the person's.

    Returns:
        The text (parts joined by new lines).
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    last = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
        default=-1,
    )
    parts: list[str] = []
    for m in messages[last + 1 :]:
        if isinstance(m, dict) and m.get("role") == "user":
            parts += _anthropic_blocks(m.get("content"))
    return "\n".join(parts)


_OUTPUTS = ("function_call_output", "custom_tool_call_output", "local_shell_call_output")
_MODEL_ITEMS = (
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "reasoning",
    "web_search_call",
    "image_generation_call",
    "mcp_call",
)


def _openai_parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    out: list[str] = []
    for c in content if isinstance(content, list) else []:
        if isinstance(c, dict) and c.get("type") in ("input_text", "text", "output_text"):
            out.append(str(c.get("text") or ""))
        elif isinstance(c, str):
            out.append(c)
    return out


def openai_new_items(body: dict[str, Any]) -> list[tuple[int, str, str]]:
    """The items of an OpenAI Responses request that are new: the person's and the tools'.

    The person's messages and the tool outputs after the model's last item (an assistant message,
    a tool call, reasoning). ``instructions`` and ``developer`` messages are the tool's own.

    Returns:
        (index in ``input``, ``user`` or ``output``, text) of each, in order; a string ``input``
        is one ``user`` item at index -1.
    """
    items = body.get("input")
    if isinstance(items, str):
        return [(-1, "user", items)]
    if not isinstance(items, list):
        return []

    def from_model(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        return item.get("type") in _MODEL_ITEMS or item.get("role") == "assistant"

    last = max((i for i, it in enumerate(items) if from_model(it)), default=-1)
    out: list[tuple[int, str, str]] = []
    for i, it in enumerate(items[last + 1 :], start=last + 1):
        if not isinstance(it, dict):
            continue
        if it.get("type") in _OUTPUTS:
            out.append((i, "output", "\n".join(_openai_parts(it.get("output")))))
        elif it.get("role") == "user":
            out.append((i, "user", "\n".join(_openai_parts(it.get("content")))))
    return out


def openai_new_text(body: dict[str, Any]) -> str:
    """The new text of an OpenAI Responses request (``openai_new_items``).

    Returns:
        The text (parts joined by new lines).
    """
    return "\n".join(text for _, _, text in openai_new_items(body) if text)


def chat_new_text(body: dict[str, Any]) -> str:
    """The new text of an OpenAI chat completions request.

    The ``user`` and ``tool`` messages after the last ``assistant`` message. ``system`` and
    ``developer`` messages are the tool's own.

    Returns:
        The text (parts joined by new lines).
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return ""
    last = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
        default=-1,
    )
    parts: list[str] = []
    for m in messages[last + 1 :]:
        if isinstance(m, dict) and m.get("role") in ("user", "tool"):
            parts += _openai_parts(m.get("content"))
    return "\n".join(parts)


# The controls


def model_allowed(model: str, allowed: list[str] | None) -> bool:
    """Whether a model is among a gateway's allowed models (globs; none set: every model).

    Returns:
        True when allowed.
    """
    if not allowed:
        return True
    return any(fnmatch.fnmatchcase(model, str(p)) for p in allowed)


def _event(
    gateway_id: str,
    person: str,
    control: str,
    rule: str,
    action: str,
    mode: str,
    details: dict[str, Any],
    *,
    severity: str = "medium",
    direction: str = "input",
) -> dict[str, Any]:
    """A security event of a gateway request (no snippet: a gateway never keeps content).

    Returns:
        The row.
    """
    return {
        "id": new_id(),
        "application_id": gateway_id,
        "created_at": utcnow(),
        "control": control,
        "rule": rule[:60],
        "action": action,
        "mode": mode,
        "severity": severity,
        "direction": direction,
        "agent_name": "",
        "subject": person[:200] or None,
        "session_id": None,
        "turn_id": None,
        "tool": None,
        "snippet": "",
        "details": details,
    }


SEVERITY = {"input.secrets": "high", "pii.model": "medium", "scope": "medium", "input.size": "low"}
_HINT = (
    "Remove it from the prompt, or keep the file that holds it out of what the tool reads, "
    "and try again."
)


def text_checks(
    policy: Effective,
    text: str,
    *,
    provider: str,
    known: tuple[str, ...] = (),
    record: Any = None,
    own: tuple[str, ...] = (),
) -> Block | None:
    """The guard's input checks on a request's new text: block (enforce) or record (monitor).

    Args:
        policy: The gateway's effective policy (with the system policy's floors).
        text: The new text.
        provider: The provider the request goes to (for the messages).
        known: The deployment's own secrets.
        record: ``record(control, rule, action, details)`` for each control that acts.
        own: The requester's own identifiers (their e-mail, their name): never a ``pii.model``
            finding in their own request (Claude Code puts the person's e-mail in its context).

    Returns:
        The block of the first enforced control that acts, or None.
    """
    s = policy.settings
    name = PROVIDER_NAMES.get(provider, provider)

    def act(control: str, rule: str, details: dict[str, Any]) -> bool:
        enforce = policy.enforced(control)
        if record is not None:
            record(control, rule, "blocked" if enforce else "logged", details)
        return enforce

    limit = (s.input.max_chars if s.input else None) or 0
    if policy.on("input.size") and limit and len(text) > limit:
        details = {"chars": len(text), "limit": limit}
        if act("input.size", "too_long", details):
            return Block(
                "input.size",
                400,
                "invalid_request",
                f"Evokai gateway: the new text in this request is {len(text):,} characters, over "
                f"this gateway's limit of {limit:,} (input.size). Send less at once.",
            )
    if policy.on("input.secrets") and (found := scan.find_secrets(text, known)):
        rules = sorted(set(found))
        if act("input.secrets", rules[0], {"count": len(found), "rules": rules}):
            return Block(
                "input.secrets",
                400,
                "invalid_request",
                "Evokai gateway: this request was not sent because it contains what looks like a "
                f"secret or credential ({', '.join(rules)}; control input.secrets). {_HINT}",
            )
    if policy.on("pii.model"):
        types = (s.pii.types if s.pii else None) or None
        builtin = {t for t in (types or PII_TYPES) if t in PII_TYPES}
        if builtin and (counts := scan.find_pii(text, builtin, limit=PII_LIMIT, allow=own)):
            kinds = sorted(counts)
            if act("pii.model", kinds[0], {"types": counts}):
                return Block(
                    "pii.model",
                    400,
                    "invalid_request",
                    "Evokai gateway: this request was not sent because it contains personal data "
                    f"({', '.join(kinds)}; control pii.model) and this gateway's policy does not "
                    f"let personal data reach {name}. {_HINT}",
                )
    deny = (s.scope.deny if s.scope else None) or []
    if policy.on("scope") and deny and (hit := scan.denied_topic(text, deny)):
        if act("scope", "denied_topic", {"topic": hit[:80]}):
            refusal = s.scope.refusal if s.scope else None
            return Block(
                "scope",
                400,
                "invalid_request",
                "Evokai gateway: "
                + (refusal or "this request mentions a topic this gateway's policy denies.")
                + f" (control scope: {hit[:80]})",
            )
    return None


def evaluate(policy: Effective, text: str, known: tuple[str, ...] = ()) -> dict[str, Any]:
    """What a gateway's input checks do with a text (its policy tests): nothing is stored.

    Returns:
        ``outcome`` and ``controls`` (each control that acted: ``blocked`` or ``flagged``).
    """
    controls: dict[str, str] = {}

    def record(control: str, _rule: str, action: str, _details: dict[str, Any]) -> None:
        controls[control] = "blocked" if action == "blocked" else "flagged"

    block = text_checks(policy, text, provider="", known=known, record=record)
    outcome = "blocked" if block else "flagged" if controls else "allowed"
    return {"outcome": outcome, "controls": controls}


def event_rows(
    gateway_id: str, person: str, provider: str, model: str
) -> tuple[list[dict[str, Any]], Any]:
    """A list of events and the ``record`` function that fills it, for one request.

    Returns:
        (events, record).
    """
    events: list[dict[str, Any]] = []

    def record(control: str, rule: str, action: str, details: dict[str, Any]) -> None:
        events.append(
            _event(
                gateway_id,
                person,
                control,
                rule,
                action,
                "enforce" if action == "blocked" else "monitor",
                {**details, "provider": provider, "model": model[:120]},
                severity=SEVERITY.get(control, "medium"),
                direction="abuse" if control.startswith("abuse.") else "input",
            )
        )

    return events, record


def gateway_event(
    gateway_id: str, person: str, control: str, rule: str, details: dict[str, Any]
) -> dict[str, Any]:
    """The event of a refusal by the gateway's own settings (provider, model, caps, profile).

    Returns:
        The row (always ``blocked``, in ``enforce``).
    """
    return _event(
        gateway_id,
        person,
        control,
        rule,
        "blocked",
        "enforce",
        details,
        severity="low" if control == "gateway.cap" else "medium",
        direction="gateway",
    )
