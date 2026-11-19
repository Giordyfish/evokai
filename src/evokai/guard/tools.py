"""The tool policy: which tools, with which arguments, how often, to which addresses.

Every check returns a :class:`ToolDecision`; the turn engine turns a refusal into a tool result the
model can read (``Blocked by the security policy: ...``), never into a failed turn, so a model
that tried something it may not do can still answer.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from evokai.guard.config import ToolSettings
from evokai.guard.content import host_allowed

_URL = re.compile(r"^(?:https?|ftp|wss?)://", re.I)
_HOSTLIKE = re.compile(r"^(?:www\.[^\s/]+|(?:[a-z0-9-]+\.)+[a-z]{2,24}(?::\d+)?/)", re.I)
"""A host without a scheme: www.example.com, or example.com/path (not a file name)."""


def matches(name: str, patterns: list[str] | None) -> bool:
    """Whether a tool name matches any glob.

    Returns:
        True on a match.
    """
    return any(fnmatch.fnmatchcase(name, p) for p in patterns or [])


@dataclass(frozen=True)
class ToolDecision:
    """The outcome of a check."""

    allowed: bool
    rule: str = ""
    reason: str = ""
    """English text for the model (and the event)."""


ALLOW = ToolDecision(True)


def offered(name: str, settings: ToolSettings) -> bool:
    """Whether a tool may be offered at all (allow and deny lists).

    Returns:
        True when allowed.
    """
    if matches(name, settings.deny):
        return False
    return settings.allow is None or matches(name, settings.allow)


def needs_confirmation(name: str, settings: ToolSettings) -> bool:
    """Whether the policy forces a confirmation for this tool.

    Returns:
        True when the tool is in ``confirm``.
    """
    return matches(name, settings.confirm)


def check_args(args: dict[str, Any], schema: dict[str, Any] | None) -> ToolDecision:
    """Validate a call's arguments against the tool's input schema.

    A schema that is itself invalid is not held against the call.

    Returns:
        The decision, with the first errors in the reason.
    """
    if not schema:
        return ALLOW
    try:
        errors = sorted(Draft202012Validator(schema).iter_errors(args), key=lambda e: list(e.path))
    except SchemaError:
        return ALLOW
    if not errors:
        return ALLOW
    detail = "; ".join(f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5])
    return ToolDecision(False, "invalid_args", f"invalid arguments ({detail[:400]})")


def addresses(value: Any) -> list[str]:
    """Every string in the arguments that is a network address.

    Returns:
        The addresses.
    """
    out: list[str] = []
    if isinstance(value, str):
        v = value.strip()
        if _URL.match(v) or _HOSTLIKE.match(v):
            out.append(v)
    elif isinstance(value, list):
        for x in value:
            out += addresses(x)
    elif isinstance(value, dict):
        for x in value.values():
            out += addresses(x)
    return out


def check_egress(name: str, args: dict[str, Any], settings: ToolSettings) -> ToolDecision:
    """Addresses in a call's arguments must be in the egress allow-list (when one is set).

    Returns:
        The decision.
    """
    allow = settings.egress_allow
    if allow is None or not matches(name, settings.egress_tools or ["*"]):
        return ALLOW
    for addr in addresses(args):
        host = urlsplit(addr if "://" in addr else f"https://{addr}").hostname or ""
        if not host_allowed(host, allow):
            return ToolDecision(False, "egress", f"the address {host or addr[:60]} is not allowed")
    return ALLOW


def call_key(name: str, args: dict[str, Any]) -> str:
    """A fingerprint of a call (tool and arguments), for loop detection.

    Returns:
        A short hash.
    """
    raw = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{name}\x00{raw}".encode()).hexdigest()[:16]


@dataclass
class TurnCalls:
    """The calls of one turn or run, for loop detection and the per-turn budget."""

    seen: dict[str, int] = field(default_factory=dict)
    count: int = 0
    tokens: int = 0

    def loop(self, name: str, args: dict[str, Any], threshold: int) -> ToolDecision:
        """Count a call; the ``threshold``-th identical one is a loop.

        Returns:
            The decision.
        """
        key = call_key(name, args)
        self.seen[key] = self.seen.get(key, 0) + 1
        if self.seen[key] >= threshold:
            return ToolDecision(
                False,
                "loop",
                f"the same call was made {self.seen[key]} times in this turn; stop repeating it",
            )
        return ALLOW


def rate_for(name: str, settings: ToolSettings) -> tuple[str, int] | None:
    """The rate limit that applies to a tool: the most specific glob wins.

    Returns:
        (glob, calls per minute), or None.
    """
    rates = settings.rate_per_minute or {}
    hits = [(p, n) for p, n in rates.items() if fnmatch.fnmatchcase(name, p)]
    if not hits:
        return None
    return max(hits, key=lambda x: (x[0] != "*", len(x[0])))
