"""Text out of the files the document connectors read.

Plain text, CSV, JSON, HTML, Word, Excel and PowerPoint.

No conversion service and no extra dependency: Office files are zip archives of XML, read with the
standard library. Other binary files (PDF, images) are not read; the agent is told so.
"""

from __future__ import annotations

import html
import io
import re
import zipfile

TEXT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/csv",
    "application/x-yaml",
)
OFFICE = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
EXTENSIONS = {".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx"}
MAX_BYTES = 15_000_000


class Unreadable(ValueError):
    """A file whose text cannot be read here."""


def _xml_text(xml: str, paragraph: str) -> str:
    xml = re.sub(rf"</{paragraph}>", "\n", xml)
    return html.unescape(re.sub(r"<[^>]+>", "", xml))


def html_text(markup: str) -> str:
    """Visible text of HTML (scripts and styles dropped).

    Returns:
        The text.
    """
    markup = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<(br|/p|/div|/li|/tr|/h[1-6])\b[^>]*>", "\n", markup, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text))
    return re.sub(r" *\n *", "\n", text).strip()


def office_text(data: bytes, kind: str) -> str:
    """Text of a Word, Excel or PowerPoint file.

    Returns:
        The text, paragraph by paragraph (Excel: cells separated by tabs, sheet by sheet).

    Raises:
        Unreadable: Not a valid file of that kind.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise Unreadable("the file is damaged") from e
    with z:
        names = z.namelist()
        if kind == "docx":
            return _xml_text(z.read("word/document.xml").decode("utf-8", "replace"), "w:p").strip()
        if kind == "pptx":
            slides = sorted(
                (n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                key=lambda n: int(re.findall(r"\d+", n)[-1]),
            )
            return "\n\n".join(
                f"[Slide {i}]\n" + _xml_text(z.read(n).decode("utf-8", "replace"), "a:p").strip()
                for i, n in enumerate(slides, 1)
            )
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [
                html.unescape(re.sub(r"<[^>]+>", "", si))
                for si in re.findall(r"<si>(.*?)</si>", xml, re.S)
            ]
        out = []
        for n in sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
            xml = z.read(n).decode("utf-8", "replace")
            rows = []
            for row in re.findall(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, body in re.findall(r"<c\b([^>]*)>(.*?)</c>", row, re.S):
                    v = re.search(r"<v>(.*?)</v>", body, re.S)
                    t = re.search(r"<t[^>]*>(.*?)</t>", body, re.S)
                    if 't="s"' in attrs and v:
                        idx = int(v.group(1))
                        cells.append(shared[idx] if idx < len(shared) else "")
                    else:
                        found = t or v
                        cells.append(html.unescape(found.group(1)) if found else "")
                rows.append("\t".join(cells))
            out.append(f"[{n.rsplit('/', 1)[-1].removesuffix('.xml')}]\n" + "\n".join(rows))
        return "\n\n".join(out)


def extract_text(name: str, mime: str, data: bytes) -> str:
    """The text of a file.

    Returns:
        The text.

    Raises:
        Unreadable: A type that cannot be read here, or a file too large.
    """
    if len(data) > MAX_BYTES:
        raise Unreadable("the file is too large to read")
    mime = (mime or "").split(";")[0].strip().lower()
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kind = OFFICE.get(mime) or EXTENSIONS.get(ext)
    if kind:
        return office_text(data, kind)
    if mime == "text/html" or ext in {".html", ".htm"}:
        return html_text(data.decode("utf-8", "replace"))
    if mime.startswith(TEXT_TYPES) or ext in {
        ".txt",
        ".md",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".log",
    }:
        return data.decode("utf-8", "replace")
    raise Unreadable(f"files of type {mime or ext or 'unknown'} cannot be read as text")
