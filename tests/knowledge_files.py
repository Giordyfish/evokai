"""Tiny documents of every format the knowledge bases read, built in memory for the tests."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def pdf(pages: list[str | None], title: str | None = None) -> bytes:
    """A PDF with one text line per page (None: a page without text, like a scan).

    Returns:
        The file.
    """
    objects: list[bytes] = []
    kids = []
    font = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        page_no, content_no = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page_no} 0 R")
        if text is None:
            stream = b"0.5 g 0 0 100 100 re f"
        else:
            safe = text.replace("(", "[").replace(")", "]")
            lines = [
                f"BT /F1 11 Tf 40 {760 - 16 * j} Td ({line}) Tj ET"
                for j, line in enumerate(safe.split("\n"))
            ]
            stream = "\n".join(lines).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_no} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
    pages_obj = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    font_obj = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    all_objects = [catalog, pages_obj, *objects, font_obj]
    info = None
    if title:
        all_objects.append(f"<< /Title ({title}) >>".encode())
        info = len(all_objects)
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(all_objects, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(all_objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    trailer = f"<< /Size {len(all_objects) + 1} /Root 1 0 R" + (
        f" /Info {info} 0 R" if info else ""
    )
    out.write(f"trailer\n{trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(paragraphs: list[tuple[str, str]], table: list[list[str]] | None = None) -> bytes:
    """A Word file: (style, text) paragraphs (style ``Heading1``... or ``""``) and a table.

    Returns:
        The file.
    """

    def p(style: str, text: str) -> str:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{ppr}<w:r><w:t>{escape(text)}</w:t></w:r></w:p>"

    body = "".join(p(s, t) for s, t in paragraphs)
    if table:
        rows = "".join(
            "<w:tr>" + "".join(f"<w:tc>{p('', c)}</w:tc>" for c in row) + "</w:tr>" for row in table
        )
        body += f"<w:tbl>{rows}</w:tbl>"
    xml = f'<?xml version="1.0"?><w:document {W}><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
P = 'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'


def pptx(slides: list[tuple[str, list[str]]]) -> bytes:
    """A PowerPoint file: (title, lines) per slide.

    Returns:
        The file.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, (title, lines) in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{escape(x)}</a:t></a:r></a:p>" for x in lines)
            xml = (
                f'<?xml version="1.0"?><p:sld {A} {P}><p:cSld><p:spTree>'
                '<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:txBody>'
                f"<a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p></p:txBody></p:sp>"
                f"<p:sp><p:nvSpPr><p:nvPr/></p:nvSpPr><p:txBody>{body}</p:txBody></p:sp>"
                "</p:spTree></p:cSld></p:sld>"
            )
            z.writestr(f"ppt/slides/slide{i}.xml", xml)
    return buf.getvalue()
