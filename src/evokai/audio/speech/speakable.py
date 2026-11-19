"""What gets spoken, decided by code.

Markdown becomes plain speakable text and personal identifiers are redacted.

These rules mirror the client's own splitter exactly (same sentence ends, same abbreviations, same
redactions), so speech is the same whether the server or the browser cuts it. The one addition is
optional: the first clause of a turn may be released early (at , ; :) once it is long enough, to
start speaking sooner.
"""

from __future__ import annotations

import re
from functools import cache

from evokai.i18n import SpeechRules, speech_rules

REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b", re.I), "taxCode"),
    (re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b", re.I), "iban"),
    (re.compile(r"\b\d{11}\b"), "vat"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "email"),
    (re.compile(r"(?:\+39\s?)?\b3\d{2}[\s.]?\d{6,7}\b"), "phone"),
]
"""Identifier formats (tax code, IBAN, VAT, e-mail, mobile) and the redaction key whose
words come from the locale."""

_CODE = re.compile(r"```[\s\S]*?```")
_TABLE_LINE = re.compile(r"^\s*\|")
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_INLINE_CODE = re.compile(r"`([^`]*)`")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*[-*•]\s+", re.M)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.M)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)(.+?)\1")
_ARROWS = re.compile(r"[→←↑↓]")
_SPACES = re.compile(r"[ \t]+")
_HAS_WORD = re.compile(r"[^\W_]", re.U)
_URL = re.compile(r"https?://\S+")


@cache
def _sources(rules: SpeechRules) -> re.Pattern[str]:
    words = "|".join(re.escape(w) for w in rules.sources_headings)
    return re.compile(rf"^\s*(?:#+\s*)?(?:\*\*)?({words})(?:\*\*)?\s*:?", re.I)


@cache
def _abbreviations(rules: SpeechRules) -> re.Pattern[str]:
    words = "|".join(re.escape(w) for w in rules.abbreviations)
    return re.compile(rf"(?:\b(?:{words})\.)$", re.I)


def redact(text: str, rules: SpeechRules | None = None) -> str:
    """Replace personal identifiers with neutral words.

    Returns:
        The text without tax codes, IBANs, VAT numbers, e-mails and phones.
    """
    words = (rules or speech_rules()).redactions
    for pattern, key in REDACTIONS:
        text = pattern.sub(words[key], text)
    return text


_CITATION = re.compile(r" ?\[\d{1,2}\](?!\()")
"""A citation marker of a knowledge passage (``[1]``): never read aloud."""


def speakable(markdown: str, rules: SpeechRules | None = None) -> str:
    """Markdown to plain speakable text, identifiers redacted.

    Returns:
        The text to speak.
    """
    s = _CODE.sub(" ", markdown)
    s = "\n".join(line for line in s.split("\n") if not _TABLE_LINE.match(line))
    s = _IMAGE.sub(" ", s)
    s = _LINK.sub(r"\1", s)
    s = _CITATION.sub("", s)
    s = _INLINE_CODE.sub(r"\1", s)
    s = _HEADING.sub("", s)
    s = _BULLET.sub("", s)
    s = _NUMBERED.sub("", s)
    s = _EMPHASIS.sub(r"\2", s)
    s = _ARROWS.sub(" ", s)
    s = _URL.sub(" ", s)
    s = redact(s, rules)
    return _SPACES.sub(" ", s).strip()


class SentenceSplitter:
    """Incremental splitter over streamed Markdown.

    `push` returns the segments completed so far; `flush` the rest.

    A sentence ends at . ! ? … followed by white space, or at a blank line / list break, never
    inside numbers (4.572, 1,2) nor after a common abbreviation. With `first_clause_min_chars`, the
    very first segment is released at the first , ; or : once it has at least that many characters,
    so speech can start before the sentence ends.
    """

    def __init__(self, first_clause_min_chars: int = 0, rules: SpeechRules | None = None) -> None:
        """Create a splitter.

        Args:
            first_clause_min_chars: Release the first clause early when this long (0 = off).
            rules: The language's speech rules (English by default).
        """
        self.rules = rules or speech_rules()
        self._sources = _sources(self.rules)
        self._abbr = _abbreviations(self.rules)
        self.buf = ""
        self.first_clause_min_chars = first_clause_min_chars
        self.emitted = 0
        self.muted = False
        """Set at a sources section (web search): sources are shown, never read aloud."""

    def push(self, delta: str) -> list[str]:
        """Add streamed text.

        Returns:
            The segments completed so far.
        """
        self.buf += delta
        return self._take(final=False)

    def flush(self) -> list[str]:
        """End of the text.

        Returns:
            The remaining segments.
        """
        return self._take(final=True)

    def reset(self) -> None:
        """Forget everything (a cancelled answer)."""
        self.buf = ""
        self.emitted = 0

    def _take(self, final: bool) -> list[str]:
        out: list[str] = []
        start = 0
        buf = self.buf
        for i, ch in enumerate(buf):
            nxt = buf[i + 1] if i + 1 < len(buf) else None
            blank = ch == "\n" and nxt is not None and (nxt in "\n-*#|" or nxt.isdigit())
            end = ch in ".!?…" and (final if nxt is None else nxt.isspace())
            clause = (
                not blank
                and not end
                and self.first_clause_min_chars > 0
                and self.emitted + len(out) == 0
                and ch in ",;:"
                and nxt is not None
                and nxt.isspace()
                and len(buf[start : i + 1].strip()) >= self.first_clause_min_chars
            )
            if not (blank or end or clause):
                continue
            piece = buf[start : i + 1]
            if end and ch == "." and self._abbr.search(piece):
                continue
            text = speakable(piece, self.rules)
            if self._sources.match(piece.strip()):
                self.muted = True
            if _HAS_WORD.search(text) and not self.muted:
                out.append(text)
            start = i + 1
        self.buf = buf[start:]
        if final:
            text = speakable(self.buf, self.rules)
            if self._sources.match(self.buf.strip()):
                self.muted = True
            if _HAS_WORD.search(text) and not self.muted:
                out.append(text)
            self.buf = ""
        self.emitted += len(out)
        return out
