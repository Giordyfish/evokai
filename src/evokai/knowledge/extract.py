"""Text and structure out of a document: headings, paragraphs, tables and pages.

Formats: PDF (its text layer, with ``pypdf``), Word (``.docx``), PowerPoint (``.pptx``), HTML,
Markdown, plain text and CSV. Office files are zip archives of XML read with the standard library.

There is no OCR: a PDF without a text layer (a scan) fails with a clear reason. A local OCR engine
can be added later as another extractor without changing the rest of the pipeline.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Literal
from xml.etree import ElementTree as ET  # ruff: ignore[suspicious-xml-etree-import] (the zip's own XML; no entities are resolved)

BlockKind = Literal["heading", "text", "table"]


class Unreadable(ValueError):
    """A document whose text cannot be read; the message says why, for whoever uploaded it."""


@dataclass
class Block:
    """A piece of a document in reading order."""

    kind: BlockKind
    text: str
    level: int = 0
    """Heading level (1 is the highest)."""
    page: int | None = None
    """The page (or slide) it is on, when the format has pages."""


@dataclass
class Extracted:
    """A document's structure."""

    blocks: list[Block] = field(default_factory=list)
    title: str | None = None
    pages: int | None = None

    def text(self) -> str:
        """The whole text, block after block.

        Returns:
            The text.
        """
        return "\n\n".join(b.text for b in self.blocks)


FORMATS = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
    "text/plain": "text",
    "text/csv": "csv",
    "application/csv": "csv",
}
EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".text": "text",
    ".csv": "csv",
}
MIME_OF = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "html": "text/html",
    "markdown": "text/markdown",
    "text": "text/plain",
    "csv": "text/csv",
}
MAX_PAGES = 3_000


def detect(name: str, mime: str | None, data: bytes = b"") -> str | None:
    """The format of a document from its type, its name and its first bytes.

    Returns:
        ``pdf``, ``docx``, ``pptx``, ``html``, ``markdown``, ``text`` or ``csv``; None when unknown.
    """
    if data.startswith(b"%PDF-"):
        return "pdf"
    mime = (mime or "").split(";")[0].strip().lower()
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in EXTENSIONS and (mime in {"", "application/octet-stream"} or ext in {".md", ".csv"}):
        return EXTENSIONS[ext]
    if mime in FORMATS:
        return FORMATS[mime]
    if ext in EXTENSIONS:
        return EXTENSIONS[ext]
    if data[:2] == b"PK" and ext in {".docx", ".pptx"}:
        return EXTENSIONS[ext]
    head = data[:512].lstrip().lower()
    if head.startswith((b"<!doctype html", b"<html")):
        return "html"
    return None


def decode(data: bytes) -> str:
    """Bytes as text: UTF-8 (with or without a byte order mark), else Windows-1252.

    Returns:
        The text.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def extract(name: str, mime: str | None, data: bytes) -> Extracted:
    """Read a document.

    Returns:
        Its blocks, title and page count.

    Raises:
        Unreadable: An unknown or damaged format, or a document without text.
    """
    kind = detect(name, mime, data)
    if kind is None:
        raise Unreadable(f"files of type {mime or name.rsplit('.', 1)[-1]} cannot be read")
    out = {
        "pdf": _pdf,
        "docx": _docx,
        "pptx": _pptx,
        "html": lambda d: html_blocks(decode(d)),
        "markdown": lambda d: markdown_blocks(decode(d)),
        "text": lambda d: text_blocks(decode(d)),
        "csv": lambda d: csv_blocks(decode(d)),
    }[kind](data)
    out.blocks = [b for b in out.blocks if b.text.strip()]
    if not any(b.kind != "heading" for b in out.blocks):
        raise Unreadable("the document has no text")
    return out


# Headings in plain text (PDF pages, text files): regulations, numbered sections, capitals.

_HEADING_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^(titolo|title|parte|part|libro|book)\s+[ivxlc\d]+\b", re.I), 1),
    (re.compile(r"^(capo|chapter|capitolo|sezione|section)\s+[ivxlc\d]+\b", re.I), 1),
    (re.compile(r"^(allegato|annex|appendice|appendix)\b", re.I), 1),
    (re.compile(r"^(art\.|articolo|article)\s*\d+", re.I), 2),
]
_LABEL = re.compile(
    r"^(titolo|title|parte|part|libro|book|capo|chapter|capitolo|sezione|section)\s", re.I
)
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+\S")


def heading_level(line: str) -> int:
    """Whether a line of plain text reads as a heading, and its level.

    Returns:
        1 to 4, or 0 for an ordinary line.
    """
    s = line.strip()
    if not s or len(s) > 90 or (s.endswith((".", ",", ";", ":")) and not _art(s)):
        return 0
    for rule, level in _HEADING_RULES:
        if rule.match(s):
            return level
    if (m := _NUMBERED.match(s)) and len(s.split()) <= 12:
        return min(m.group(1).count(".") + 1, 4)
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 4 and all(c.isupper() for c in letters) and len(s.split()) <= 10:
        return 1
    return 0


def _art(s: str) -> bool:
    return bool(re.match(r"^(art\.|articolo|article)\s*\d+", s, re.I))


def _paragraphs(text: str, page: int | None = None) -> list[Block]:
    """Plain text as headings and paragraphs (blank lines and headings separate paragraphs).

    Returns:
        Blocks.
    """
    out: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            joined = re.sub(r"-\n(?=[a-zà-ù])", "", "\n".join(para))
            joined = re.sub(r"[ \t]*\n[ \t]*", " ", joined).strip()
            if joined:
                out.append(Block("text", joined, page=page))
            para.clear()

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        level = heading_level(line)
        if level:
            flush()
            prev = out[-1] if out else None
            if (
                prev is not None
                and prev.kind == "heading"
                and _LABEL.match(prev.text)
                and len(prev.text.split()) <= 3
                and level == 1
            ):
                # «TITOLO I» then «DISPOSIZIONI GENERALI»: one heading, the label and its name.
                prev.text = f"{prev.text} {line}"
                continue
            out.append(Block("heading", line, level, page))
        else:
            para.append(line)
    flush()
    return out


def text_blocks(text: str) -> Extracted:
    """Plain text.

    Returns:
        Its blocks.
    """
    return Extracted(_paragraphs(text))


def _pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Unreadable("the PDF is protected by a password")
        pages = reader.pages
        n = len(pages)
        if n > MAX_PAGES:
            raise Unreadable(f"the PDF has {n} pages (at most {MAX_PAGES})")
        blocks: list[Block] = []
        chars = 0
        for i, page in enumerate(pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception:  # a broken page loses its text, not the document
                text = ""
            chars += len(re.sub(r"\s", "", text))
            blocks += _paragraphs(text, i)
        title = None
        meta = reader.metadata
        if meta is not None and meta.title and str(meta.title).strip():
            title = str(meta.title).strip()[:300]
    except PdfReadError as e:
        raise Unreadable("the PDF is damaged") from e
    if chars < max(20, 10 * n):
        raise Unreadable(
            "the PDF has no text layer (a scan?): text recognition (OCR) is not available"
        )
    return Extracted(blocks, title, n)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _zip(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise Unreadable("the file is damaged") from e


def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        info = z.getinfo(name)
        if info.file_size > 60_000_000:
            raise Unreadable("the file is too large once unpacked")
        return ET.fromstring(z.read(name))  # ruff: ignore[suspicious-xml-element-tree-usage] (expat resolves no external entities)
    except KeyError as e:
        raise Unreadable("the file is damaged") from e
    except ET.ParseError as e:
        raise Unreadable("the file is damaged") from e


def _w_text(el: ET.Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if node.tag == f"{W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{W}tab":
            parts.append("\t")
        elif node.tag in {f"{W}br", f"{W}cr"} and node.get(f"{W}type") != "page":
            parts.append("\n")
    return "".join(parts).strip()


def table_text(rows: list[list[str]]) -> str:
    """A table as pipe-separated rows (the first row is the header).

    Returns:
        The text.
    """
    clean = [[re.sub(r"\s+", " ", c).strip().replace("|", "/") for c in r] for r in rows]
    clean = [r for r in clean if any(r)]
    return "\n".join("| " + " | ".join(r) + " |" for r in clean)


def _docx(data: bytes) -> Extracted:
    with _zip(data) as z:
        root = _xml(z, "word/document.xml")
        title = None
        if "docProps/core.xml" in z.namelist():
            core = _xml(z, "docProps/core.xml")
            t = core.find("{http://purl.org/dc/elements/1.1/}title")
            title = (t.text or "").strip() or None if t is not None else None
    body = root.find(f"{W}body")
    blocks: list[Block] = []
    page = 1
    seen_break = False
    for el in list(body) if body is not None else []:
        if el.tag == f"{W}p":
            breaks = sum(
                1
                for n in el.iter()
                if n.tag == f"{W}lastRenderedPageBreak"
                or (n.tag == f"{W}br" and n.get(f"{W}type") == "page")
            )
            if breaks and (blocks or seen_break):
                page += breaks
            seen_break = seen_break or bool(breaks)
            text = _w_text(el)
            if not text:
                continue
            style = el.find(f"{W}pPr/{W}pStyle")
            sid = (style.get(f"{W}val") or "") if style is not None else ""
            m = re.match(r"(?i)^(heading|titolo|berschrift|titre)\s*(\d)$", sid)
            if m:
                blocks.append(Block("heading", text, int(m.group(2)), page))
            elif sid.lower() in {"title", "titolo"}:
                blocks.append(Block("heading", text, 1, page))
                title = title or text
            else:
                blocks.append(Block("text", text, page=page))
        elif el.tag == f"{W}tbl":
            rows = [[_w_text(tc) for tc in tr.findall(f"{W}tc")] for tr in el.findall(f"{W}tr")]
            if rows:
                blocks.append(Block("table", table_text(rows), page=page))
    return Extracted(blocks, title, page if seen_break else None)


def _a_text(el: ET.Element) -> list[str]:
    out = []
    for p in el.iter(f"{A}p"):
        text = "".join(t.text or "" for t in p.iter(f"{A}t")).strip()
        if text:
            out.append(text)
    return out


def _pptx(data: bytes) -> Extracted:
    with _zip(data) as z:
        names = sorted(
            (n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda n: int(re.findall(r"\d+", n)[-1]),
        )
        slides = [_xml(z, n) for n in names]
    blocks: list[Block] = []
    for i, root in enumerate(slides, 1):
        title = None
        texts: list[str] = []
        for sp in root.iter(f"{P}sp"):
            ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
            lines = _a_text(sp)
            if ph is not None and ph.get("type") in {"title", "ctrTitle"} and lines:
                title = " ".join(lines)
            else:
                texts += lines
        blocks.append(Block("heading", f"Slide {i}" + (f": {title}" if title else ""), 1, i))
        if texts:
            blocks.append(Block("text", "\n".join(texts), page=i))
        for tbl in root.iter(f"{A}tbl"):
            rows = [
                [" ".join(_a_text(tc)) for tc in tr.findall(f"{A}tc")]
                for tr in tbl.findall(f"{A}tr")
            ]
            if rows:
                blocks.append(Block("table", table_text(rows), page=i))
    return Extracted(blocks, None, len(slides))


class _Html(HTMLParser):
    """Headings, paragraphs and tables of an HTML page (scripts, styles and navigation dropped)."""

    SKIP = frozenset({"script", "style", "noscript", "template", "svg", "head", "nav", "iframe"})
    BREAK = frozenset({
        "p", "div", "li", "br", "blockquote", "pre", "section", "article", "dd", "dt",
        "main", "footer", "header", "aside", "form", "figcaption", "ul", "ol", "hr",
    })  # fmt: skip

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.skip = 0
        self.heading = 0
        self.title: str | None = None
        self.in_title = False
        self.rows: list[list[str]] | None = None
        self.cell: list[str] | None = None
        self.tables = 0

    def flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not text:
            return
        if self.heading:
            self.blocks.append(Block("heading", text, self.heading))
        else:
            self.blocks.append(Block("text", text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "title":
            self.in_title = True
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if self.rows is not None:
            if tag == "table":
                self.tables += 1
            elif tag == "tr":
                self.rows.append([])
            elif tag in {"td", "th"}:
                self.cell = []
            return
        if tag == "table":
            self.flush()
            self.rows, self.tables = [], 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.heading = int(tag[1])
        elif tag in self.BREAK:
            self.flush()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.rows is not None:
            if tag in {"td", "th"} and self.cell is not None:
                if not self.rows:
                    self.rows.append([])
                self.rows[-1].append("".join(self.cell))
                self.cell = None
            elif tag == "table":
                self.tables -= 1
                if self.tables <= 0:
                    if any(any(c.strip() for c in r) for r in self.rows):
                        self.blocks.append(Block("table", table_text(self.rows)))
                    self.rows = None
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.flush()
            self.heading = 0
        elif tag in self.BREAK:
            self.flush()

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title = ((self.title or "") + data).strip()[:300] or None
        if self.skip:
            return
        if self.cell is not None:
            self.cell.append(data)
        elif self.rows is None:
            self.buf.append(data)


def html_blocks(markup: str) -> Extracted:
    """An HTML page.

    Returns:
        Its blocks and its title.
    """
    p = _Html()
    p.feed(markup)
    p.close()
    p.flush()
    return Extracted(p.blocks, p.title)


def markdown_blocks(text: str) -> Extracted:
    """Markdown: ATX headings, paragraphs, pipe tables and fenced code.

    Returns:
        Its blocks and its title (the first level-1 heading).
    """
    blocks: list[Block] = []
    para: list[str] = []
    table: list[str] = []
    fence: list[str] | None = None

    def flush() -> None:
        if para:
            blocks.append(Block("text", "\n".join(para).strip()))
            para.clear()
        if table:
            rows = [
                line.strip().strip("|").split("|")
                for line in table
                if not re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", line.strip())
            ]
            blocks.append(Block("table", table_text(rows)))
            table.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if fence is not None:
            if line.strip().startswith("```"):
                blocks.append(Block("text", "\n".join(fence)))
                fence = None
            else:
                fence.append(line)
            continue
        if line.strip().startswith("```"):
            flush()
            fence = []
            continue
        if m := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line):
            flush()
            blocks.append(Block("heading", m.group(2), len(m.group(1))))
        elif line.strip().startswith("|"):
            if para:
                blocks.append(Block("text", "\n".join(para).strip()))
                para.clear()
            table.append(line)
        elif not line.strip():
            flush()
        else:
            if table:
                flush()
            para.append(line.rstrip())
    if fence:
        blocks.append(Block("text", "\n".join(fence)))
    flush()
    title = next((b.text for b in blocks if b.kind == "heading" and b.level == 1), None)
    return Extracted(blocks, title)


def csv_blocks(text: str) -> Extracted:
    """A CSV file as one table (the chunker splits a long one by rows, header repeated).

    Returns:
        One table block.
    """
    sample = text[:4096]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(sample, ";,\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    return Extracted([Block("table", table_text(rows))] if rows else [])
