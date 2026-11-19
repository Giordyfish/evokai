r"""Identifiers an application declares: its own codes, found and protected like the built-in types.

Declared in the application's config file (``application.identifiers``) and kept in its defaults::

    application:
      identifiers:
        - name: order_number
          label: ORDER_NUMBER
          match:
            - pattern: 'ORD-\d{6}'
            - digits: 10
              check: luhn
              context: 'order|ordine'
          speech: {read: word, words: {en: "the order number", it: "il numero d’ordine"}}

Each identifier is a type of personal data like ``iban``: the PII controls (``pii.model``,
``pii.history``, ``pii.logs``, ``pii.browser``) find, mask (``[ORDER_NUMBER]``) and tokenise
(``[ORDER_NUMBER_3f9a1c]``) it, ``security.pii.types`` may name it, and speech says it the way
``speech`` asks. A value is recognised by any of its ``match`` entries:

- ``pattern``: a regular expression (Python syntax), never inside a longer word or number;
- ``digits``: a number of that many digits (or ``[shortest, longest]``), written together or in
  groups (``grouped: false`` for together only), never a piece of a longer number;
- ``check: luhn``: only values whose digits pass the Luhn checksum;
- ``context``: only values with this text (a regular expression, any case) in the
  ``context_chars`` characters before them.

On the same text a built-in type (IBAN, codice fiscale, e-mail, partita IVA, card) wins over a
declared identifier unless the declared match was confirmed by its context; a declared identifier
wins over a phone number.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from evokai.guard import pii

log = logging.getLogger("evokai.guard")

NAME = re.compile(r"[a-z][a-z0-9_]{1,39}")
LABEL = re.compile(r"[A-Z][A-Z0-9_]{1,39}")
_BUILTIN_LABELS = {"CODICE_FISCALE", "PARTITA_IVA", "IBAN", "EMAIL", "PHONE", "CARD"}


def _regex(value: str, what: str) -> str:
    try:
        compiled = re.compile(value)
    except re.error as e:
        raise ValueError(f"{what} is not a valid regular expression: {e}") from e
    if compiled.match(""):
        raise ValueError(f"{what} matches an empty text")
    return value


class MatchSpec(BaseModel):
    """One way of recognising the identifier."""

    model_config = ConfigDict(extra="forbid")
    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    """A regular expression (Python syntax). Evokai adds the boundaries: it never matches inside
    a longer word or number."""
    ignore_case: bool = True
    """Whether the pattern ignores case."""
    digits: int | tuple[int, int] | None = None
    """A number of this many digits, or ``[shortest, longest]``."""
    grouped: bool = True
    """Whether the number may be written in groups separated by spaces or dashes."""
    check: Literal["luhn"] | None = None
    """A checksum the digits of the value must pass."""
    context: str | None = Field(default=None, min_length=1, max_length=500)
    """A regular expression (any case) that must be found just before the value."""
    context_chars: int = Field(default=30, ge=1, le=200)
    """How many characters before the value the context is looked for in."""

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, v: str | None) -> str | None:
        return None if v is None else _regex(v, "pattern")

    @field_validator("context")
    @classmethod
    def _context(cls, v: str | None) -> str | None:
        return None if v is None else _regex(v, "context")

    @field_validator("digits")
    @classmethod
    def _digits(cls, v: int | tuple[int, int] | None) -> int | tuple[int, int] | None:
        lo, hi = (v, v) if isinstance(v, int) else (v or (1, 40))
        if not 1 <= lo <= hi <= 40:
            raise ValueError("digits must be between 1 and 40, the shortest first")
        return v

    @model_validator(mode="after")
    def _one_kind(self) -> MatchSpec:
        if (self.pattern is None) == (self.digits is None):
            raise ValueError("each match needs either a pattern or digits")
        if self.pattern is not None and not self.grouped:
            raise ValueError("grouped applies to digits, not to a pattern")
        return self

    def compiled(self) -> pii.Matcher:
        """The matcher.

        Returns:
            Its compiled form.
        """
        digits = None
        if self.digits is not None:
            digits = (self.digits, self.digits) if isinstance(self.digits, int) else self.digits
        pattern = None
        if self.pattern is not None:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9])(?:{self.pattern})(?![A-Za-z0-9])",
                re.I if self.ignore_case else 0,
            )
        return pii.Matcher(
            pattern=pattern,
            digits=digits,
            grouped=self.grouped,
            check=self.check,
            context=re.compile(self.context, re.I) if self.context else None,
            context_chars=self.context_chars,
        )


class SpeechSpec(BaseModel):
    """How speech says a value."""

    model_config = ConfigDict(extra="forbid")
    read: pii.SpeechRead = "word"
    """``word``: words in its place (``words``); ``spell``: character by character; ``group``:
    in groups of ``size`` characters; ``omit``: nothing; ``verbatim``: as written."""
    words: dict[str, str] = Field(default_factory=dict)
    """Locale → the words said in its place (``en: the order number``)."""
    size: int = Field(default=3, ge=1, le=12)
    """Characters per group (``read: group``)."""
    match: list[MatchSpec] = Field(default_factory=list, max_length=10)
    """More ways of recognising it when speaking only, besides the identifier's own ``match``:
    a looser shape, so that nothing that looks like it is ever read aloud."""

    @field_validator("words")
    @classmethod
    def _words(cls, v: dict[str, str]) -> dict[str, str]:
        for locale, text in v.items():
            if not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", locale):
                raise ValueError(f"words: {locale!r} is not a locale (en, it, pt-BR)")
            if not 0 < len(text) <= 100:
                raise ValueError(f"words: the {locale} text must have 1 to 100 characters")
        return v


class IdentifierSpec(BaseModel):
    """An identifier the application declares."""

    model_config = ConfigDict(extra="forbid")
    name: str
    """Its type (``order_number``): lower case letters, digits and ``_``, not a built-in type."""
    label: str | None = None
    """In masks and tokens (``ORDER_NUMBER``); the name in upper case by default."""
    match: list[MatchSpec] = Field(min_length=1, max_length=10)
    speech: SpeechSpec = Field(default_factory=SpeechSpec)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME.fullmatch(v):
            raise ValueError(f"{v!r}: a name is 2-40 lower case letters, digits or _")
        if v in pii.PII_TYPES:
            raise ValueError(f"{v!r} is a built-in type")
        return v

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        if v is not None and not LABEL.fullmatch(v):
            raise ValueError(f"{v!r}: a label is 2-40 upper case letters, digits or _")
        return v

    @property
    def mask_label(self) -> str:
        """The label in masks and tokens."""
        return self.label or self.name.upper()

    def compiled(self) -> pii.Identifier:
        """The identifier.

        Returns:
            Its compiled form.
        """
        return pii.Identifier(
            name=self.name,
            label=self.mask_label,
            matchers=tuple(m.compiled() for m in self.match),
            read=self.speech.read,
            words=tuple(sorted(self.speech.words.items())),
            size=self.speech.size,
            speech_matchers=tuple(m.compiled() for m in self.speech.match),
        )


def parse(raw: Any) -> list[IdentifierSpec]:
    """Validate a list of declarations.

    Returns:
        The declarations.

    Raises:
        ValueError: Not a list, an invalid entry, or a name or label used twice.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("identifiers must be a list")
    specs = []
    for n, item in enumerate(raw):
        try:
            specs.append(IdentifierSpec.model_validate(item))
        except ValidationError as e:
            err = e.errors()[0]
            where = ".".join(str(x) for x in err["loc"])
            name = item.get("name") if isinstance(item, dict) else None
            raise ValueError(
                f"identifier {name or n}{': ' + where if where else ''}: {err['msg']}"
            ) from e
    names = [s.name for s in specs]
    labels = [s.mask_label for s in specs]
    if dup := {x for x in names if names.count(x) > 1}:
        raise ValueError(f"identifiers declared twice: {sorted(dup)}")
    if dup := {x for x in labels if labels.count(x) > 1} | (set(labels) & _BUILTIN_LABELS):
        raise ValueError(f"identifier labels used twice or by a built-in type: {sorted(dup)}")
    return specs


def stored(specs: Iterable[IdentifierSpec]) -> list[dict[str, Any]]:
    """Declarations as kept in the application's defaults.

    Returns:
        JSON-ready dictionaries.
    """
    return [s.model_dump(mode="json", exclude_defaults=True) for s in specs]


def unknown_types(types: Iterable[str] | None, specs: Iterable[IdentifierSpec]) -> list[str]:
    """The names of ``security.pii.types`` that are neither built-in nor declared.

    Returns:
        The unknown names.
    """
    declared = {s.name for s in specs}
    return [t for t in types or [] if t not in pii.PII_TYPES and t not in declared]


@lru_cache(maxsize=256)
def _compile(source: str) -> tuple[pii.Identifier, ...]:
    return tuple(s.compiled() for s in parse(json.loads(source)))


def compile_all(raw: Any) -> tuple[pii.Identifier, ...]:
    """Compiled identifiers from stored declarations (cached).

    Returns:
        The identifiers; none when the declarations are invalid (logged).
    """
    if not raw:
        return ()
    try:
        return _compile(json.dumps(raw, sort_keys=True))
    except (ValueError, TypeError) as e:
        log.warning("identifiers_invalid", extra={"error": str(e)})
        return ()


def of_app(app: Any) -> tuple[pii.Identifier, ...]:
    """An application's identifiers, recorded for the masking done outside its turns.

    Returns:
        The compiled identifiers.
    """
    if app is None:
        return ()
    found = compile_all((getattr(app, "defaults", None) or {}).get("identifiers"))
    if app_id := getattr(app, "id", None):
        pii.declare(str(app_id), found)
    return found
