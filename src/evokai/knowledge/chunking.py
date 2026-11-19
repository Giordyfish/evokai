"""Structure-aware chunking: passages that follow the document's headings, pages and tables.

- A heading closes the passage before it and opens a new section; the heading path
  (``Titolo I > Art. 3``) goes with every passage of the section. Headings with nothing between
  them stay together.
- Paragraphs are added to a passage up to ``max_chars``; a paragraph longer than that is cut between
  sentences.
- A table up to ``table_max_chars`` is a passage of its own, kept whole; a longer one is cut between
  rows, each part with the header row repeated.
- Consecutive passages of a section overlap by ``overlap_chars`` (cut at a word), so a sentence on
  the border is found from both sides.

Every passage keeps its exact place in the document: the page it starts and ends on, and its first
and last character in the document's text (``Extracted.text``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, model_validator

from evokai.knowledge.extract import Block, Extracted

SEP = "\n\n"


class ChunkSettings(BaseModel):
    """How a knowledge base cuts its documents."""

    max_chars: int = Field(default=1_500, ge=200, le=8_000)
    """A passage's length, about 350 tokens by default."""
    overlap_chars: int = Field(default=200, ge=0, le=2_000)
    table_max_chars: int = Field(default=3_000, ge=200, le=16_000)
    """A table up to this length is never cut."""

    @model_validator(mode="after")
    def _overlap(self) -> ChunkSettings:
        if self.overlap_chars >= self.max_chars // 2:
            raise ValueError("overlap_chars must be less than half of max_chars")
        return self


@dataclass
class Chunk:
    """A passage of a document."""

    text: str
    position: int
    char_start: int
    char_end: int
    headings: list[str] = field(default_factory=list)
    page: int | None = None
    page_end: int | None = None
    kind: str = "text"

    @property
    def section(self) -> str:
        """The heading path as one line.

        Returns:
            e.g. ``Titolo I > Art. 3``.
        """
        return " > ".join(self.headings)

    @property
    def tokens(self) -> int:
        """An estimate of its tokens (four characters each).

        Returns:
            The estimate.
        """
        return max(1, len(self.text) // 4)


@dataclass
class _Span:
    start: int
    end: int
    block: Block


_SENTENCE = re.compile(r"(?<=[.!?;:])\s+(?=\S)")


def _cut(text: str, start: int, limit: int) -> list[tuple[int, int]]:
    """Cut a long text between sentences (else between words) into pieces of at most ``limit``.

    Returns:
        (start, end) offsets of the pieces, relative to the document.
    """
    pieces: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if n - i <= limit:
            pieces.append((start + i, start + n))
            break
        window = text[i : i + limit]
        cuts = [m.end() for m in _SENTENCE.finditer(window) if m.end() > limit // 3]
        if not cuts:
            cuts = [m.end() for m in re.finditer(r"\s+", window) if m.end() > limit // 3]
        j = cuts[-1] if cuts else limit
        pieces.append((start + i, start + i + len(window[:j].rstrip())))
        i += j
    return pieces


class Chunker:
    """Cuts one document."""

    def __init__(self, settings: ChunkSettings) -> None:
        """Bind the settings.

        Args:
            settings: Sizes and overlap.
        """
        self.s = settings

    def chunks(self, doc: Extracted) -> list[Chunk]:
        """The document's passages, in order.

        Returns:
            Chunks.
        """
        self.text = doc.text()
        self.out: list[Chunk] = []
        self.stack: list[tuple[int, str]] = []
        self.buf: list[_Span] = []
        self.last: Chunk | None = None
        """The previous passage of the current section (the overlap comes from it)."""
        pos = 0
        for b in doc.blocks:
            span = _Span(pos, pos + len(b.text), b)
            pos = span.end + len(SEP)
            if b.kind == "heading":
                self._heading(span)
            elif b.kind == "table":
                self._table(span)
            else:
                self._paragraph(span)
        self._flush()
        return self.out

    # sections

    def _only_headings(self) -> bool:
        return all(s.block.kind == "heading" for s in self.buf)

    def _heading(self, span: _Span) -> None:
        if not self._only_headings():
            self._flush()
        self.last = None
        while self.stack and self.stack[-1][0] >= span.block.level:
            self.stack.pop()
        self.stack.append((span.block.level, span.block.text[:200]))
        self.buf.append(span)

    # passages

    def _emit(
        self, start: int, end: int, kind: str, pages: list[int | None], text: str = ""
    ) -> None:
        known = [p for p in pages if p is not None]
        chunk = Chunk(
            text=text or self.text[start:end],
            position=len(self.out),
            char_start=start,
            char_end=end,
            headings=[h for _, h in self.stack],
            page=known[0] if known else None,
            page_end=known[-1] if known else None,
            kind=kind,
        )
        self.out.append(chunk)
        self.last = chunk if kind == "text" else None

    def _overlap_start(self, start: int) -> int:
        """Where a passage starts once the overlap with the previous one is added.

        Returns:
            An offset at or before ``start``.
        """
        last = self.last
        if last is None or not self.s.overlap_chars:
            return start
        from_ = max(last.char_start, last.char_end - self.s.overlap_chars)
        # Start at a word, never in the middle of one.
        m = re.search(r"\s", self.text[from_ : last.char_end])
        if from_ > last.char_start and m:
            from_ += m.end()
        return from_ if from_ < last.char_end else start

    def _flush(self) -> None:
        if not self.buf:
            return
        if self._only_headings():
            # A trailing heading with no text under it still says something (an annex's title).
            if not self.out or self.buf[-1].end > self.out[-1].char_end:
                start = self.buf[0].start
                self._emit(start, self.buf[-1].end, "text", [s.block.page for s in self.buf])
            self.buf = []
            return
        start = self._overlap_start(self.buf[0].start)
        pages = [s.block.page for s in self.buf]
        self._emit(start, self.buf[-1].end, "text", pages)
        self.buf = []

    def _paragraph(self, span: _Span) -> None:
        limit = self.s.max_chars
        size = len(span.block.text)
        if self.buf:
            start = (
                self._overlap_start(self.buf[0].start)
                if not self._only_headings()
                else (self.buf[0].start)
            )
            if span.end - start <= limit:
                self.buf.append(span)
                return
            if not self._only_headings():
                self._flush()
        if size <= limit - (span.start - self._overlap_start(span.start)):
            self.buf.append(span)
            return
        # A paragraph longer than a passage: cut between sentences, with the headings before it.
        head = self.buf[0].start if self.buf else None
        pages = [s.block.page for s in self.buf] + [span.block.page]
        self.buf = []
        for i, (a, b) in enumerate(_cut(span.block.text, span.start, limit)):
            start = head if i == 0 and head is not None else self._overlap_start(a)
            self._emit(start, b, "text", pages if i == 0 else [span.block.page])

    def _table(self, span: _Span) -> None:
        text = span.block.text
        headings = self._only_headings() and bool(self.buf)
        if len(text) <= self.s.table_max_chars:
            if headings and span.end - self.buf[0].start <= self.s.table_max_chars:
                start = self.buf[0].start
                pages = [s.block.page for s in self.buf] + [span.block.page]
                self.buf = []
                self._emit(start, span.end, "table", pages)
                return
            self._flush()
            self._emit(span.start, span.end, "table", [span.block.page])
            return
        self._flush()
        rows = text.split("\n")
        header, body = rows[0], rows[1:]
        part: list[str] = []
        offset = span.start + len(header) + 1
        part_start = offset
        for row in body:
            if part and len(header) + sum(len(r) + 1 for r in part) + len(row) > self.s.max_chars:
                self._emit(
                    part_start,
                    offset - 1,
                    "table",
                    [span.block.page],
                    text="\n".join([header, *part]),
                )
                part, part_start = [], offset
            part.append(row)
            offset += len(row) + 1
        if part:
            self._emit(
                part_start, span.end, "table", [span.block.page], text="\n".join([header, *part])
            )


def chunk(doc: Extracted, settings: ChunkSettings | None = None) -> list[Chunk]:
    """Cut a document into passages.

    Returns:
        Chunks in document order.
    """
    return Chunker(settings or ChunkSettings()).chunks(doc)
