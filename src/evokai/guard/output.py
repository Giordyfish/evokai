"""Answers on their way out: secrets, personal data, links, system prompt leakage, tokens put back.

The model's text streams to the browser. When a control that changes the text is enforced, the
filter holds back a short tail (and any link or token not closed yet) so that a secret or an
identifier split across two chunks is still caught whole; otherwise text passes at once and the
checks run on the whole answer at the end of the step (monitor costs no latency).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from evokai.guard import content, secrets
from evokai.guard import pii as identifiers
from evokai.guard.text import normalize

HOLD = 64
"""Characters held back while streaming (an IBAN with spaces is 42)."""
MAX_HOLD = 4_000
"""An unclosed construct is released after this many characters."""
SHINGLE = 8
"""Words per shingle for the system prompt leak check."""
LEAK_SHINGLES = 3
"""Shingles of the instructions an answer must repeat to be a leak (10 consecutive words)."""

_WORDS = re.compile(r"[^\W_]+", re.U)


def shingles(text: str, size: int = SHINGLE) -> set[str]:
    """Overlapping word n-grams of a text, normalised.

    Returns:
        The shingles.
    """
    words = _WORDS.findall(normalize(text, decode=False).text)
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def leaked(answer: str, prompt_shingles: set[str], threshold: int = LEAK_SHINGLES) -> bool:
    """Whether an answer repeats a long enough piece of the instructions.

    Returns:
        True on a leak.
    """
    return bool(prompt_shingles) and len(shingles(answer) & prompt_shingles) >= threshold


@dataclass
class OutputRules:
    """What the filter does to an answer (each flag on only when its control is enforced)."""

    secrets: bool = False
    pii: bool = False
    links: bool = False
    leak: bool = False
    hold_pii: bool = False
    """Never split an identifier between two chunks (stored events are masked chunk by chunk)."""
    pii_types: frozenset[str] | None = None
    pii_declared: tuple[identifiers.Identifier, ...] = ()
    """The application's own identifiers."""
    link_domains: list[str] = field(default_factory=list)
    known_secrets: tuple[str, ...] = ()
    prompt_shingles: set[str] = field(default_factory=set)
    vault: identifiers.Vault | None = None
    """Tokens to put back (reversible tokenisation towards the model)."""

    @property
    def changes_text(self) -> bool:
        """Whether the filter may change the text (so it must hold a tail back).

        Returns:
            True when any rewriting rule is on.
        """
        return (
            self.secrets
            or self.pii
            or self.hold_pii
            or self.links
            or self.leak
            or self.vault is not None
        )


@dataclass
class Changes:
    """What the filter changed, for the security events."""

    secrets: dict[str, int] = field(default_factory=dict)
    pii: dict[str, int] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)
    leak: bool = False


def apply(text: str, rules: OutputRules, changes: Changes) -> str:
    """Rewrite a finished piece of text (tokens back, secrets, identifiers, links).

    Returns:
        The text to show.
    """
    if rules.vault is not None:
        text = rules.vault.restore(text)
    if rules.secrets:
        found = secrets.find(text, rules.known_secrets)
        for f in found:
            changes.secrets[f.rule] = changes.secrets.get(f.rule, 0) + 1
        text = secrets.redact(text, found)
    if rules.pii:
        spans = identifiers.find(text, rules.pii_types, rules.pii_declared)
        for t, n in identifiers.counts(spans).items():
            changes.pii[t] = changes.pii.get(t, 0) + n
        text = identifiers.mask(text, rules.pii_types, rules.pii_declared) if spans else text
    if rules.links:
        text, bad = content.defuse_links(text, rules.link_domains)
        changes.links += [x.url[:200] for x in bad]
    return text


def _open_bracket(buffer: str) -> int | None:
    """Where a link or a token that is still streaming starts (``[`` not closed yet).

    Returns:
        Its index (the ``!`` of an image included), or None.
    """
    i = buffer.rfind("[")
    if i < 0:
        return None
    rest = buffer[i:]
    if "\n" in rest:
        return None  # links and tokens never span lines
    closed = re.match(r"\[[^\]]*\]", rest)
    after = rest[closed.end() :] if closed else None
    still_open = (
        closed is None
        or after == ""
        or (after is not None and after.startswith("(") and ")" not in after)
    )
    if not still_open:
        return None
    return i - 1 if i and buffer[i - 1] == "!" else i


@dataclass
class OutputFilter:
    """Filters one model step's streamed text."""

    rules: OutputRules
    changes: Changes = field(default_factory=Changes)
    raw: str = ""
    """Everything the model wrote."""
    shown: str = ""
    """Everything let through."""
    blocked: bool = False
    """The answer leaked the system prompt: nothing more goes out."""
    _buffer: str = ""

    def push(self, delta: str) -> str:
        """Take a chunk of the answer.

        Returns:
            The text that may be shown now.
        """
        self.raw += delta
        if self.blocked:
            return ""
        if not self.rules.changes_text:
            self.shown += delta
            return delta
        self._buffer += delta
        if self.rules.leak and leaked(self.raw, self.rules.prompt_shingles):
            self.blocked = True
            self.changes.leak = True
            self._buffer = ""
            return ""
        cut = self._cut()
        if cut <= 0:
            return ""
        out = apply(self._buffer[:cut], self.rules, self.changes)
        self._buffer = self._buffer[cut:]
        self.shown += out
        return out

    def flush(self) -> str:
        """End of the step: release what was held back.

        Returns:
            The rest of the text to show.
        """
        if self.blocked or not self._buffer:
            self._buffer = ""
            return ""
        if self.rules.leak and leaked(self.raw, self.rules.prompt_shingles):
            self.blocked = True
            self.changes.leak = True
            self._buffer = ""
            return ""
        out = apply(self._buffer, self.rules, self.changes)
        self._buffer = ""
        self.shown += out
        return out

    def _cut(self) -> int:
        """How much of the buffer can go out now without splitting anything a rule must see whole.

        With identifiers, links or tokens to rewrite, a tail of ``HOLD`` characters waits (and any
        link not closed yet). With secrets only, just the last word waits, and only when it could
        be the start of a secret, so ordinary answers stream as before.

        Returns:
            The cut position.
        """
        buf, r = self._buffer, self.rules
        if len(buf) > MAX_HOLD:
            return len(buf)
        heavy = r.pii or r.hold_pii or r.links or r.vault is not None
        cut = len(buf) - HOLD if heavy else len(buf)
        if cut <= 0:
            return 0
        if r.links or r.vault is not None:
            bracket = _open_bracket(buf)
            if bracket is not None:
                cut = min(cut, bracket)
        spans: list[tuple[int, int]] = []
        if r.secrets:
            spans += [(f.start, f.end) for f in secrets.find(buf, r.known_secrets)]
            word = _LAST_WORD.search(buf)
            if word and _may_start_secret(word.group(0), r.known_secrets):
                cut = min(cut, word.start())
            tail = len(buf) - HOLD
            hint = max((m.start() for m in _SECRET_HINT.finditer(buf, max(0, tail))), default=-1)
            if hint >= 0:
                cut = min(cut, hint)
        if r.pii or r.hold_pii or r.vault is not None:
            spans += [(s.start, s.end) for s in identifiers.find(buf, r.pii_types, r.pii_declared)]
        if r.links:
            spans += [(x.start, x.end) for x in content.links(buf)]
        for start, end in spans:
            if start < cut < end or end == len(buf):
                cut = min(cut, start)
        if cut == len(buf):
            return cut
        space = max(buf.rfind(" ", 0, cut), buf.rfind("\n", 0, cut))
        return space + 1 if space > 0 else cut


_LAST_WORD = re.compile(r"\S+$")
_SECRET_HINT = re.compile(
    r"(?i)bearer|passw|pwd|secret|api[_-]?key|token|private|-----begin|aws|://"
)
"""Words that may introduce a secret: the text after them waits for the whole value."""
_SECRET_PREFIXES = (
    "evk_",
    "AKIA",
    "ASIA",
    "ABIA",
    "ACCA",
    "eyJ",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "xox",
    "sk-",
    "sk_",
    "rk_",
    "whsec_",
    "AIza",
    "-----",
)


def _may_start_secret(word: str, known: tuple[str, ...]) -> bool:
    """Whether a word still being written could become a secret.

    Returns:
        True when it is the start of a known secret format or of a deployment secret.
    """
    w = word.lstrip("([\"'`<")
    if not w:
        return False
    if any(w.startswith(p) or p.startswith(w) for p in _SECRET_PREFIXES):
        return True
    return len(w) >= 4 and any(k.startswith(w) or w.startswith(k[:8]) for k in known)


def restore_output(value: Any, vault: identifiers.Vault | None) -> Any:
    """Put tokens back in a structured answer (server runs).

    Returns:
        The value with real values.
    """
    return vault.restore_any(value) if vault is not None else value


_SENTENCE_END = re.compile(r"[.!?…](?=\s)|\n")


@dataclass
class SentenceGate:
    """Streams an answer through the output filter, in whole sentences when the filter rewrites.

    With no enforced output control the filter changes nothing and every delta goes out as it
    comes (token by token). Otherwise only finished sentences leave, each already filtered: the
    filter keeps the tail of what it has seen in its scan, so a secret split across two deltas or
    two sentences is still caught whole, and a leak found in a later sentence stops the answer
    (``blocked``) before that sentence goes out.
    """

    filter: OutputFilter
    _pending: str = ""

    @property
    def token_by_token(self) -> bool:
        """Whether deltas pass as they come (nothing to rewrite).

        Returns:
            True when no enforced control changes the text.
        """
        return not self.filter.rules.changes_text

    @property
    def blocked(self) -> bool:
        """Whether the answer leaked the instructions: nothing more goes out.

        Returns:
            True once blocked.
        """
        return self.filter.blocked

    def push(self, delta: str) -> list[str]:
        """Take a delta of the answer.

        Returns:
            The pieces that may go out now.
        """
        out = self.filter.push(delta)
        if self.token_by_token:
            return [out] if out else []
        if self.filter.blocked:
            self._pending = ""
            return []
        self._pending += out
        ends = [m.end() for m in _SENTENCE_END.finditer(self._pending)]
        if not ends:
            return []
        starts = [0, *ends[:-1]]
        pieces = [self._pending[a:b] for a, b in zip(starts, ends, strict=True)]
        self._pending = self._pending[ends[-1] :]
        return [p for p in pieces if p]

    def flush(self) -> list[str]:
        """The end of the answer: what is left, unless the end leaked.

        Returns:
            The last pieces.
        """
        out = self.filter.flush()
        rest, self._pending = self._pending + out, ""
        if self.filter.blocked or not rest:
            return []
        return [rest]
