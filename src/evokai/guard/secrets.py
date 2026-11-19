"""Secrets and credentials in a text: API keys, tokens, private keys, passwords in URLs.

Formats with a recognisable shape only (no entropy guessing, which flags every id and hash), plus
the deployment's own secrets given literally (signing key, encryption key, operator key, MCP
secrets): if one of those ever appears in a model's output it is a leak by definition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)")),
    ("evokai_key", re.compile(r"\bevk_(?:srv|adm)_[A-Za-z0-9]{6,24}_[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws.{0,20}?(?:secret|private).{0,20}?[:=]\s*['\"]?[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("stripe_key", re.compile(r"\b(?:sk|rk|whsec)_(?:live|test)?_?[A-Za-z0-9]{20,}")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{24,}=*")),
    ("url_password", re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,64}:[^\s/@]{3,128}@")),
    ("assigned_secret", re.compile(r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*['\"]?[^\s'\"<>]{8,}")),
]  # fmt: skip
"""(rule, pattern): the rule names a security event."""


@dataclass(frozen=True)
class Found:
    """A secret in a text."""

    start: int
    end: int
    rule: str


def find(text: str, known: tuple[str, ...] = ()) -> list[Found]:
    """Every secret in a text, without overlaps.

    Args:
        text: The text.
        known: Literal secrets of the deployment (at least 12 characters to count).

    Returns:
        What was found, in text order.
    """
    found: list[Found] = []
    for rule, pattern in RULES:
        found += [Found(m.start(), m.end(), rule) for m in pattern.finditer(text)]
    for secret in known:
        if len(secret) < 12:
            continue
        at = text.find(secret)
        while at >= 0:
            found.append(Found(at, at + len(secret), "deployment_secret"))
            at = text.find(secret, at + 1)
    found.sort(key=lambda f: (f.start, -(f.end - f.start)))
    out: list[Found] = []
    for f in found:
        if out and f.start < out[-1].end:
            continue
        out.append(f)
    return out


def redact(text: str, found: list[Found] | None = None, known: tuple[str, ...] = ()) -> str:
    """Replace secrets with ``[SECRET]``.

    Returns:
        The text without them.
    """
    found = find(text, known) if found is None else found
    if not found:
        return text
    out, at = [], 0
    for f in found:
        out += [text[at : f.start], "[SECRET]"]
        at = f.end
    out.append(text[at:])
    return "".join(out)
