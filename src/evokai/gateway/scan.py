r"""Linear-time detectors for gateway bodies: secrets, personal data and denied topics.

A gateway checks texts of hundreds of kilobytes (a file the tool read, a long tool result), and
Python's ``re`` backtracks: some of the guard's patterns take seconds on an adversarial run of one
character. Every pattern here runs on RE2 (``google-re2``), whose matching time is linear in the
text, and on the text's UTF-8 bytes (a ``str`` would be encoded again on every search).

The detectors are the guard's own (``evokai.guard.secrets`` and ``evokai.guard.pii``), compiled for
RE2. RE2 has no lookaround, so the guard's boundary conditions (``(?<![A-Za-z0-9])``, ``(?!\\w)``)
are checked on each match instead: a match whose boundaries fail is cut back at a separator, as
backtracking would, and the next search starts where a new match could begin, never inside the
same run, so the scan stays linear. Checksums (codice fiscale, Luhn, IBAN mod 97) are the guard's.

The base URL door only needs to know whether a text holds a secret or an identifier and which
kinds: there a check blocks or records, it never changes the request. The gateway proxy's ``mask``
adapter (titles and other writes of an app) replaces the values instead (``mask``).
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache

import re2

from evokai.guard import pii
from evokai.guard import secrets as guard_secrets

_OPTIONS = re2.Options()
_OPTIONS.log_errors = False


def _compile(pattern: str | bytes) -> re2._Regexp:
    return re2.compile(pattern.encode() if isinstance(pattern, str) else pattern, _OPTIONS)


# Secrets: the guard's rules. Only the AWS secret key's lookahead needs another spelling.

_SECRET_SPELLING = {
    "aws_secret_key": (
        r"(?i)aws.{0,20}?(?:secret|private).{0,20}?[:=]\s*['\"]?[A-Za-z0-9/+=]{40}"
        r"(?:[^A-Za-z0-9/+=]|$)"
    ),
}
SECRET_RULES: tuple[tuple[str, re2._Regexp], ...] = tuple(
    (rule, _compile(_SECRET_SPELLING.get(rule, pattern.pattern)))
    for rule, pattern in guard_secrets.RULES
)
"""(rule, pattern): the same rules, and names, as ``output.secrets`` and ``input.secrets``."""


def find_secrets(text: str, known: tuple[str, ...] = ()) -> list[str]:
    """The rules of the secrets in a text (one entry per secret found).

    Args:
        text: The text.
        known: The deployment's own secrets (at least 12 characters to count).

    Returns:
        Rule names, ``deployment_secret`` for a known secret.
    """
    data = text.encode("utf-8", "surrogatepass")
    out: list[str] = []
    for rule, pattern in SECRET_RULES:
        out.extend(rule for _ in _iter(pattern, data))
    out.extend(
        "deployment_secret" for secret in known if len(secret) >= 12 and secret.encode() in data
    )
    return out


def _iter(pattern: re2._Regexp, data: bytes) -> Iterator[tuple[int, int]]:
    pos = 0
    while pos <= len(data):
        m = pattern.search(data, pos)
        if m is None:
            return
        start, end = m.span()
        yield start, end
        pos = end if end > start else end + 1


# Personal data: the guard's types, with their boundaries checked on the match.

_WORD = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_") | frozenset(
    range(0x80, 0x100)
)
"""``\\w`` on UTF-8 bytes: ASCII letters, digits, underscore, and every byte of a non-ASCII
character (letters, mostly)."""
_ALNUM = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
_HEX = frozenset(b"0123456789ABCDEFabcdef")


@dataclass(frozen=True)
class _Detector:
    """A type of identifier: an RE2 pattern and what the guard's lookarounds required."""

    type: str
    pattern: re2._Regexp
    not_before: frozenset[int]
    """Bytes that may not come just before a match."""
    ends_ok: Callable[[bytes, int], bool]
    """Whether a match may end at a position (the guard's lookahead)."""
    cuts: bytes = b""
    """Separators a match may be cut back at when its end fails (what backtracking would try)."""
    valid: Callable[[bytes], bool] | None = None
    """The checksum, or the shape a match must also have."""


def _not_next(chars: frozenset[int]) -> Callable[[bytes, int], bool]:
    def ok(data: bytes, end: int) -> bool:
        return end >= len(data) or data[end] not in chars

    return ok


def _digits_end(data: bytes, end: int) -> bool:
    # (?![\w/-]|[.,]\d)
    if end >= len(data):
        return True
    c = data[end]
    if c in _WORD or c in b"/-":
        return False
    return not (c in b".," and end + 1 < len(data) and data[end + 1 : end + 2].isdigit())


def _digits_valid(value: bytes) -> bool:
    raw = value.decode()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and pii.luhn_ok(digits):
        return True
    prefixed = raw[:2].upper() == "IT"
    return bool(
        13 <= len(digits) <= 19
        and not prefixed
        and pii.luhn_ok(digits)
        and pii._CARD_PREFIX.match(digits)
    )


def _digits_type(value: bytes) -> str:
    digits = sum(1 for c in value if 48 <= c <= 57)
    return "partita_iva" if digits == 11 else "card"


def _landline_valid(value: bytes) -> bool:
    return pii._landline_ok(value.decode())


def _iban(value: bytes) -> bool:
    """The guard's IBAN check: the match, or the longest valid IBAN it starts with.

    A following word can join the match (spaces are allowed inside), so it is cut back at each
    space until it holds (``evokai.guard.pii``).

    Returns:
        True for an IBAN.
    """
    v = value.decode()
    while True:
        if pii.iban_ok(v):
            return True
        cut = v.rfind(" ")
        if cut < 15:
            return False
        v = v[:cut]


DETECTORS: tuple[_Detector, ...] = (
    _Detector(
        "iban",
        _compile(r"(?i)[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}"),
        _ALNUM,
        _not_next(_ALNUM),
        cuts=b" ",
        valid=_iban,
    ),
    _Detector(
        "codice_fiscale",
        _compile(
            r"(?i)[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z]"
            r"[0-9LMNPQRSTUV]{3}[A-Z]"
        ),
        _ALNUM,
        _not_next(_ALNUM),
        valid=lambda v: pii.codice_fiscale_ok(v.decode()),
    ),
    _Detector(
        "email",
        _compile(r"[\w.+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}"),
        _WORD | frozenset(b".+-"),
        lambda _d, _e: True,
    ),
    _Detector(
        "digits",
        _compile(r"(?i)(?:IT ?)?\d(?:[ -]?\d){10,18}"),
        _WORD | frozenset(b".,/-"),
        _digits_end,
        cuts=b" ",
        valid=_digits_valid,
    ),
    _Detector(
        "phone",
        _compile(
            r"(?:(?:\+|00)\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d){5,11}"
            r"|3\d{2}[ .-]?\d{3}[ .-]?\d{3,4})"
        ),
        _WORD | frozenset(b"+"),
        _not_next(_WORD),
        cuts=b" .-",
    ),
    _Detector(
        "landline",
        _compile(r"0\d{1,3}(?:[ .-]\d{2,8}){1,4}"),
        _WORD | frozenset(b"+"),
        _not_next(_WORD),
        cuts=b" .-",
        valid=_landline_valid,
    ),
)
_UUID = _Detector(
    "uuid",
    _compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"),
    _HEX,
    _not_next(_HEX),
)


class _Starts:
    """Where a match could begin: after a byte its detector allows before it (found at C speed)."""

    def __init__(self, data: bytes, not_before: frozenset[int]) -> None:
        """Hold the text; its map of allowed bytes is made on the first need."""
        self.data = data
        self.not_before = not_before
        self._allowed: bytes | None = None

    def after(self, start: int) -> int:
        """The first position after ``start`` whose previous byte is allowed.

        Returns:
            The position (past the end when there is none).
        """
        if self._allowed is None:
            table = bytes(0 if b in self.not_before else 1 for b in range(256))
            self._allowed = self.data.translate(table)
        j = self._allowed.find(1, start)
        return len(self.data) + 1 if j < 0 else j + 1


def _matches(d: _Detector, data: bytes) -> Iterator[tuple[int, int]]:
    """Every match of a detector, with its boundaries and checksum checked, in a linear scan.

    Yields:
        (start, end) byte offsets.
    """
    pos, n = 0, len(data)
    starts = _Starts(data, d.not_before)
    while pos <= n:
        m = d.pattern.search(data, pos)
        if m is None:
            return
        start, end = m.span()
        if start > 0 and data[start - 1] in d.not_before:
            # No match starts inside the same run: skip to where one could.
            pos = starts.after(start)
            continue
        # The longest end the guard's lookahead allows (what backtracking would settle on).
        hit = next(
            (e for e in _ends(d, data, start, end) if _ends_ok(d, data, start, end, e)), None
        )
        if hit is None:
            pos = starts.after(start)
            continue
        if d.valid is None or d.valid(data[start:hit]):
            yield start, hit
        # As ``finditer`` does, the next match starts after this one, valid or not.
        pos = max(hit, start + 1)


def _ends(d: _Detector, data: bytes, start: int, end: int) -> Iterator[int]:
    """The ends a match may have: its own, then each separator inside it, from the last.

    Yields:
        End offsets.
    """
    yield end
    if d.cuts:
        for i in range(end - 1, start, -1):
            if data[i] in d.cuts:
                yield i


def _ends_ok(d: _Detector, data: bytes, start: int, end: int, e: int) -> bool:
    """Whether a match may end at ``e``: the lookahead holds and the pattern still matches.

    Returns:
        True when it may.
    """
    if not d.ends_ok(data, e):
        return False
    return e == end or d.pattern.fullmatch(data, start, e) is not None


def find_pii(
    text: str, types: frozenset[str] | set[str] | None = None, *, limit: int | None = None
) -> dict[str, int]:
    """How many personal identifiers of each type a text holds (the guard's built-in types).

    Overlaps are resolved as the guard does: the earlier, longer one wins, and a phone number
    never counts inside a UUID.

    Args:
        text: The text.
        types: Only these types (default all).
        limit: Stop each detector after this many matches (the gateway needs which types a text
            holds, not every one of them: a file full of e-mail addresses costs no more).

    Returns:
        Type to count (types with none left out; with a limit, the counts are at least that).
    """
    counts: dict[str, int] = {}
    for _, _, kind in _pii_spans(text.encode("utf-8", "surrogatepass"), types, limit):
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _pii_spans(
    data: bytes, types: frozenset[str] | set[str] | None, limit: int | None
) -> list[tuple[int, int, str]]:
    """The personal identifiers of a text's bytes, without overlaps, in text order.

    Returns:
        (start, end, type) byte spans.
    """
    want = set(types) if types is not None else set(pii.PII_TYPES)
    ranked: list[tuple[int, int, int, str]] = []
    for d in DETECTORS:
        if d.type == "digits":
            if not want & {"partita_iva", "card"}:
                continue
        elif d.type in ("phone", "landline"):
            if "phone" not in want:
                continue
        elif d.type not in want:
            continue
        for n, (start, end) in enumerate(_matches(d, data), 1):
            kind = {"digits": _digits_type(data[start:end]), "landline": "phone"}.get(
                d.type, d.type
            )
            if kind in want:
                ranked.append((start, end, 3 if kind == "phone" else 1, kind))
            if limit is not None and n >= limit:
                break
    phones = [r for r in ranked if r[3] == "phone"]
    if phones:
        # UUIDs never overlap each other: the one that starts last before a phone is the only one
        # that could hold it.
        upto = max(r[1] for r in phones)
        ids = list(_matches(_UUID, data[: upto + 40]))
        starts = [a for a, _ in ids]

        def in_uuid(r: tuple[int, int, int, str]) -> bool:
            i = bisect.bisect_right(starts, r[0]) - 1
            return i >= 0 and r[1] <= ids[i][1]

        ranked = [r for r in ranked if r[3] != "phone" or not in_uuid(r)]
    ranked.sort(key=lambda r: (r[0], -(r[1] - r[0]), r[2]))
    out: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, _, kind in ranked:
        if start < last_end:
            continue
        out.append((start, end, kind))
        last_end = end
    return out


def mask(
    text: str,
    *,
    secrets: bool,
    known: tuple[str, ...] = (),
    pii_types: frozenset[str] | set[str] | None = None,
    replacement: str = "[bloccato]",
) -> tuple[str, list[str]]:
    """A text with its secrets and personal identifiers replaced (the gateway proxy's ``mask``).

    Args:
        text: The text.
        secrets: Mask the secrets (``input.secrets``).
        known: The deployment's own secrets.
        pii_types: Mask these personal data types (None or empty: none).
        replacement: What a value becomes.

    Returns:
        The text, and the rule or type of each value masked.
    """
    data = text.encode("utf-8", "surrogatepass")
    spans: list[tuple[int, int, str]] = []
    if secrets:
        for rule, pattern in SECRET_RULES:
            spans += [(a, b, rule) for a, b in _iter(pattern, data)]
        for secret in known:
            if len(secret) < 12:
                continue
            raw = secret.encode()
            at = data.find(raw)
            while at >= 0:
                spans.append((at, at + len(raw), "deployment_secret"))
                at = data.find(raw, at + 1)
    if pii_types:
        spans += _pii_spans(data, pii_types, None)
    if not spans:
        return text, []
    spans.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    out, rules, pos = bytearray(), [], 0
    for start, end, rule in spans:
        if start < pos:
            continue
        out += data[pos:start] + replacement.encode()
        rules.append(rule)
        pos = end
    out += data[pos:]
    return out.decode("utf-8", "surrogatepass"), rules


# Denied topics (scope.deny)


_REGEX_CHARS = frozenset("\\[]()|?*+^$")


@cache
def topic(word: str) -> re2._Regexp:
    """A denied topic for RE2: a regular expression when it looks like one, else a whole word.

    A regular expression RE2 cannot run (a lookaround, a backreference) is matched as a phrase.

    Returns:
        The pattern (case-insensitive, over the text's bytes).
    """
    if any(c in _REGEX_CHARS for c in word):
        try:
            return _compile("(?i)" + word)
        except re2.error:
            pass
    return _compile(r"(?i)\b" + re2.escape(word.strip()) + r"\b")


def denied_topic(text: str, deny: list[str]) -> str | None:
    """The first denied topic a text mentions.

    Returns:
        The topic as written in the policy, or None.
    """
    if not deny:
        return None
    data = text.encode("utf-8", "surrogatepass")
    return next((d for d in deny if d.strip() and topic(d).search(data) is not None), None)
