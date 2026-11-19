"""Personal data: detection with checksums, masking and reversible tokenisation.

Formats (EU and Italian first): codice fiscale (with its check letter and omocodia), partita IVA
(checksum), IBAN (mod 97, any country), e-mail, phone numbers
(Italian mobile, landline with a separator, international), payment cards (Luhn and a known issuer
prefix). A checksum keeps random numbers from being taken for identifiers.

Two ways to hide a value:

- **mask** (irreversible): ``[CODICE_FISCALE]``;
- **tokenize** (reversible): ``[CODICE_FISCALE_3f9a1c]``, a keyed hash of the value, stable for a
  conversation, so the model sees placeholders while tools still get the real value
  (:class:`Vault` puts it back).
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

PII_TYPES = (
    "codice_fiscale",
    "partita_iva",
    "iban",
    "email",
    "phone",
    "card",
)

_CF = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z]"
    r"[0-9LMNPQRSTUV]{3}[A-Z](?![A-Za-z0-9])",
    re.I,
)
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Za-z0-9])", re.I)
_DIGITS = re.compile(r"(?<![\w.,/-])(?:IT ?)?\d(?:[ -]?\d){10,18}(?![\w/-]|[.,]\d)", re.I)
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}")
_PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"(?:\+|00)\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d){5,11}"  # international
    r"|3\d{2}[ .-]?\d{3}[ .-]?\d{3,4}"  # Italian mobile
    r")(?![\w])"
)
_LANDLINE = re.compile(r"(?<![\w+])0\d{1,3}(?:[ .-]\d{2,8}){1,4}(?![\w])")
"""An Italian landline: the prefix and its groups, whatever the separator (06 1234 5678,
0571 123 456, 06-1234-5678); 6 to 11 digits in all, and never a date (02-11-2026)."""
_DATE = re.compile(r"\d{2}([ .-])\d{2}\1\d{2}(?:\d{2})?")
_UUID = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
"""A UUID: two of its groups can be all digits (``...-0936-4843-...``) and read as a landline."""

_CF_ODD = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            2,
            4,
            18,
            20,
            11,
            3,
            6,
            8,
            12,
            14,
            16,
            10,
            22,
            25,
            24,
            23,
        ],
        strict=True,
    )
)
_CF_EVEN = {
    **{str(d): d for d in range(10)},
    **{c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
}
_CARD_PREFIX = re.compile(r"^(?:4|5[1-5]|2[2-7]|3[47]|3[068]|35|6011|65|62)")
_LABELS = {
    "codice_fiscale": "CODICE_FISCALE",
    "partita_iva": "PARTITA_IVA",
    "iban": "IBAN",
    "email": "EMAIL",
    "phone": "PHONE",
    "card": "CARD",
}
PLACEHOLDER = re.compile(r"\[(" + "|".join(_LABELS.values()) + r")(?:_([0-9a-f]{6}))?\]")
"""A mask or a token in a text."""


def codice_fiscale_ok(value: str) -> bool:
    """Check a codice fiscale's check letter.

    Returns:
        Whether the last letter matches the first fifteen characters.
    """
    v = value.upper()
    if len(v) != 16:
        return False
    total = sum((_CF_ODD if i % 2 == 0 else _CF_EVEN).get(ch, -1000) for i, ch in enumerate(v[:15]))
    return total >= 0 and chr(ord("A") + total % 26) == v[15]


def luhn_ok(digits: str) -> bool:
    """The Luhn checksum (cards, and the Italian partita IVA).

    Returns:
        Whether the digits pass.
    """
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def iban_ok(value: str) -> bool:
    """The IBAN mod-97 check.

    Returns:
        Whether the IBAN is valid.
    """
    v = re.sub(r"\s", "", value).upper()
    if not 15 <= len(v) <= 34 or not v[:2].isalpha() or not v[2:4].isdigit():
        return False
    moved = v[4:] + v[:4]
    number = "".join(str(int(c, 36)) for c in moved)
    return int(number) % 97 == 1


@dataclass(frozen=True)
class Span:
    """A personal identifier found in a text."""

    start: int
    end: int
    type: str
    value: str


def _digit_spans(text: str) -> list[Span]:
    out = []
    for m in _DIGITS.finditer(text):
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        prefixed = raw[:2].upper() == "IT"
        if len(digits) == 11 and luhn_ok(digits):
            out.append(Span(m.start(), m.end(), "partita_iva", raw))
        elif (
            13 <= len(digits) <= 19
            and not prefixed
            and luhn_ok(digits)
            and _CARD_PREFIX.match(digits)
        ):
            out.append(Span(m.start(), m.end(), "card", raw))
    return out


def _iban_span(m: re.Match[str]) -> Span | None:
    """An IBAN match, or the longest valid IBAN it starts with.

    The pattern allows spaces between characters, so a following word («IT60... del cliente») can
    join the match and spoil its check digits: the match is cut back at each space until it holds.

    Returns:
        The span, or None.
    """
    value = m.group(0)
    while True:
        if iban_ok(value):
            return Span(m.start(), m.start() + len(value), "iban", value)
        cut = value.rfind(" ")
        if cut < 15:
            return None
        value = value[:cut]


def find(text: str, types: frozenset[str] | set[str] | None = None) -> list[Span]:
    """Every personal identifier in a text, without overlaps (the earlier, longer one wins).

    Args:
        text: The text.
        types: Only these types (default all).

    Returns:
        Spans in text order.
    """
    want = set(types) if types is not None else set(PII_TYPES)
    found: list[Span] = []
    if "iban" in want:
        found += [s for m in _IBAN.finditer(text) if (s := _iban_span(m)) is not None]
    if "codice_fiscale" in want:
        found += [
            Span(m.start(), m.end(), "codice_fiscale", m.group(0))
            for m in _CF.finditer(text)
            if codice_fiscale_ok(m.group(0))
        ]
    if "email" in want:
        found += [Span(m.start(), m.end(), "email", m.group(0)) for m in _EMAIL.finditer(text)]
    found += [s for s in _digit_spans(text) if s.type in want]
    if "phone" in want:
        ids = [(m.start(), m.end()) for m in _UUID.finditer(text)]
        found += [
            Span(m.start(), m.end(), "phone", m.group(0))
            for m in [*_PHONE.finditer(text), *_LANDLINE.finditer(text)]
            if not any(a <= m.start() and m.end() <= b for a, b in ids)
            and (m.re is _PHONE or _landline_ok(m.group(0)))
        ]
    found.sort(key=lambda s: (s.start, -(s.end - s.start)))
    out: list[Span] = []
    for s in found:
        if out and s.start < out[-1].end:
            continue
        out.append(s)
    return out


def _landline_ok(match: str) -> bool:
    """Whether a landline-shaped match is a phone number: 6 to 11 digits, not a date.

    Returns:
        True for a phone number.
    """
    digits = sum(c.isdigit() for c in match)
    return 6 <= digits <= 11 and not _DATE.fullmatch(match)


def counts(spans: list[Span]) -> dict[str, int]:
    """How many identifiers of each type.

    Returns:
        type → count.
    """
    return dict(Counter(s.type for s in spans))


def mask(text: str, types: frozenset[str] | set[str] | None = None) -> str:
    """Replace identifiers with their type, for good.

    Returns:
        The masked text.
    """
    return _replace(text, find(text, types), lambda s: f"[{_LABELS[s.type]}]")


def _replace(text: str, spans: list[Span], sub: Any) -> str:
    if not spans:
        return text
    out, at = [], 0
    for s in spans:
        out += [text[at : s.start], sub(s)]
        at = s.end
    out.append(text[at:])
    return "".join(out)


@dataclass
class Vault:
    """Reversible tokenisation with a key: the same value always gets the same token.

    The key is per conversation, so tokens are stable across turns and history windows while
    meaning nothing elsewhere. Only values seen through this vault can be put back.
    """

    key: bytes
    types: frozenset[str] | None = None
    values: dict[str, str] = field(default_factory=dict)
    """token → value."""

    def token(self, span: Span) -> str:
        """The token of a value (remembered for :meth:`restore`).

        Returns:
            ``[TYPE_xxxxxx]``.
        """
        norm = re.sub(r"\s", "", span.value).upper()
        digest = hmac.new(self.key, f"{span.type}:{norm}".encode(), hashlib.sha256).hexdigest()
        tok = f"[{_LABELS[span.type]}_{digest[:6]}]"
        self.values[tok] = span.value
        return tok

    def tokenize(self, text: str) -> tuple[str, list[Span]]:
        """Replace identifiers with tokens.

        Returns:
            The text and what was replaced.
        """
        spans = find(text, self.types)
        return _replace(text, spans, self.token), spans

    def restore(self, text: str) -> str:
        """Put the real values back in place of known tokens.

        Returns:
            The text with the values (unknown tokens stay as they are).
        """
        if "[" not in text:
            return text
        return PLACEHOLDER.sub(lambda m: self.values.get(m.group(0), m.group(0)), text)

    def restore_any(self, value: Any) -> Any:
        """Put values back everywhere in a JSON-like value (tool arguments, structured output).

        Returns:
            The value with real values in its strings.
        """
        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, list):
            return [self.restore_any(v) for v in value]
        if isinstance(value, dict):
            return {k: self.restore_any(v) for k, v in value.items()}
        return value


def transform_any(value: Any, fn: Any) -> Any:
    """Apply a text transformation to every string of a JSON-like value.

    Returns:
        The transformed value.
    """
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [transform_any(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: transform_any(v, fn) for k, v in value.items()}
    return value
