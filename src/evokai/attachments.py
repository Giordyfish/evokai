"""Files a person attaches to a chat message: what they are, what is kept, what the model reads.

The type of a file comes from its bytes only: the name and the type the browser declares are never
trusted (the name only tells a Markdown or CSV text from plain text). Images are PNG, JPEG, WebP and
GIF; documents are what the knowledge extractor reads (PDF, Word, PowerPoint, plain text, Markdown,
CSV). SVG, HTML, XML and anything else are refused: markup can carry scripts, and a file served back
to a browser must never be one it would run.

Images lose their metadata (EXIF with the GPS position and the camera, XMP, IPTC, comments) before
they are stored or sent, at the container level: the pixels are not decoded or re-encoded, so
nothing is lost but the metadata (an EXIF orientation goes too). Documents keep their bytes; their
text is extracted for models that do not read the format, and is untrusted data either way.

See ``docs/attachments.md``.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
import zipfile
from dataclasses import dataclass, field

from evokai.core.errors import AppError
from evokai.core.util import new_id
from evokai.knowledge.extract import Unreadable, decode, extract

KINDS = ("png", "jpeg", "webp", "gif", "pdf", "docx", "pptx", "txt", "md", "csv")
"""Every kind of file an agent may accept (``attachments.types``)."""
IMAGES = frozenset({"png", "jpeg", "webp", "gif"})
MEDIA_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
}
MAX_TEXT_CHARS = 50_000
"""Characters of a document's text the model reads; the rest is cut with a note."""
MAX_NAME_CHARS = 200
TRUNCATED_NOTE = "\n\n[The rest of this file was cut: it is longer than {n} characters.]"


class AttachmentError(AppError):
    """An attachment that is refused; the code says why (``attachment_*``)."""

    status, code = 400, "attachment_type"


@dataclass
class Prepared:
    """An attachment checked, cleaned and read."""

    name: str
    kind: str
    data: bytes
    """The bytes as stored and sent (an image without its metadata)."""
    text: str | None = None
    """A document's text (cut at ``MAX_TEXT_CHARS``); None for images and unreadable files."""
    unreadable: str | None = None
    """Why a document has no text (a scan, a damaged file)."""
    truncated: bool = False
    id: str = field(default_factory=new_id)

    @property
    def media_type(self) -> str:
        """The media type of its kind."""
        return MEDIA_TYPES[self.kind]

    @property
    def size(self) -> int:
        """Bytes kept."""
        return len(self.data)

    @property
    def sha256(self) -> str:
        """The hash of the bytes kept (traces keep it, never the content)."""
        return hashlib.sha256(self.data).hexdigest()

    @property
    def image(self) -> bool:
        """Whether it is an image."""
        return self.kind in IMAGES

    def meta(self) -> dict[str, object]:
        """What a message, an event and a trace keep of it.

        Returns:
            id, name, mediaType, size, sha256.
        """
        return {
            "id": self.id,
            "name": self.name,
            "mediaType": self.media_type,
            "size": self.size,
            "sha256": self.sha256,
        }


def media_type_of(kind: str) -> str:
    """The media type of a kind.

    Returns:
        The media type.
    """
    return MEDIA_TYPES[kind]


def clean_name(name: str) -> str:
    """A file name as shown and stored: no folders, no control characters, at most 200 characters.

    Returns:
        The name (``file`` when nothing is left).
    """
    base = re.split(r"[\\/]", name or "")[-1]
    base = "".join(c for c in base if c.isprintable()).strip().lstrip(".")
    return base[:MAX_NAME_CHARS] or "file"


_MARKUP = (b"<!doctype", b"<html", b"<svg", b"<?xml", b"<script", b"<body", b"<head", b"<iframe")


def sniff(name: str, data: bytes) -> str:
    """The kind of a file, from its bytes.

    Returns:
        One of ``KINDS``.

    Raises:
        AttachmentError: Markup (SVG, HTML, XML), an empty file, or a kind Evokai does not read
            (400 ``attachment_type``).
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        office = _office(data)
        if office:
            return office
        raise AttachmentError("This kind of archive is not accepted.", code="attachment_type")
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n\x0c").lower()
    if not head:
        raise AttachmentError("The file is empty.", code="attachment_type")
    if head.startswith(b"<") and any(m in head for m in _MARKUP):
        raise AttachmentError("SVG, HTML and XML files are not accepted.", code="attachment_type")
    if b"\x00" in data[:8192] or not _is_text(data[:8192]):
        raise AttachmentError("This kind of file is not accepted.", code="attachment_type")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return {"md": "md", "markdown": "md", "csv": "csv"}.get(ext, "txt")


def _office(data: bytes) -> str | None:
    try:
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if "word/document.xml" in names:
        return "docx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return None


def _is_text(sample: bytes) -> bool:
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError as e:
        # A sample may end in the middle of a character.
        if e.start < len(sample) - 4:
            text = sample.decode("cp1252", "replace")
        else:
            text = sample[: e.start].decode("utf-8")
    controls = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r\x0c")
    return controls <= len(text) // 100


# ── metadata ────────────────────────────────────────────────────────────────────────────────────


def strip_metadata(kind: str, data: bytes) -> bytes:
    """An image without its metadata (documents are returned as they are).

    Returns:
        The bytes.

    Raises:
        AttachmentError: A damaged image (400 ``attachment_damaged``).
    """
    strip = {"jpeg": _jpeg, "png": _png, "webp": _webp, "gif": _gif}.get(kind)
    if strip is None:
        return data
    try:
        return strip(data)
    except (IndexError, ValueError, struct.error) as e:
        raise AttachmentError("The image is damaged.", code="attachment_damaged") from e


def _need(data: bytes, end: int) -> None:
    if end > len(data):
        raise ValueError("truncated")


_JPEG_KEEP_APP = {0xE0: b"", 0xE2: b"ICC_PROFILE\x00", 0xEE: b"Adobe"}
"""APP segments that carry no metadata: JFIF, the colour profile and Adobe's colour transform."""


def _jpeg(data: bytes) -> bytes:
    out = bytearray(b"\xff\xd8")
    i = 2
    while True:
        _need(data, i + 2)
        if data[i] != 0xFF:
            raise ValueError("not a marker")
        while data[i + 1] == 0xFF:  # fill bytes
            i += 1
            _need(data, i + 2)
        marker = data[i + 1]
        if marker == 0xD9:
            out += b"\xff\xd9"
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            out += data[i : i + 2]
            i += 2
            continue
        _need(data, i + 4)
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        end = i + 2 + length
        _need(data, end)
        body = data[i + 4 : end]
        keep = marker not in range(0xE0, 0xF0) and marker != 0xFE
        if marker in _JPEG_KEEP_APP:
            keep = body.startswith(_JPEG_KEEP_APP[marker])
        if keep:
            out += data[i:end]
        i = end
        if marker == 0xDA:
            # Entropy-coded data up to the next marker that is not a stuffed byte or a restart.
            j = i
            while True:
                j = data.index(b"\xff", j)
                _need(data, j + 2)
                nxt = data[j + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7 or nxt == 0xFF:
                    j += 1 if nxt == 0xFF else 2
                    continue
                break
            out += data[i:j]
            i = j


_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _png(data: bytes) -> bytes:
    out = bytearray(data[:8])
    i = 8
    while True:
        _need(data, i + 8)
        length = struct.unpack(">I", data[i : i + 4])[0]
        kind = data[i + 4 : i + 8]
        end = i + 12 + length
        _need(data, end)
        if kind not in _PNG_DROP:
            out += data[i:end]
        i = end
        if kind == b"IEND":
            return bytes(out)


def _webp(data: bytes) -> bytes:
    chunks = bytearray()
    i = 12
    total = 8 + struct.unpack("<I", data[4:8])[0]
    _need(data, total)
    while i < total:
        _need(data, i + 8)
        kind = data[i : i + 4]
        size = struct.unpack("<I", data[i + 4 : i + 8])[0]
        end = i + 8 + size + (size & 1)
        if end > total + (size & 1):  # a last odd chunk may lack its padding byte
            raise ValueError("truncated")
        chunk = bytearray(data[i:end].ljust(end - i, b"\x00"))
        if kind == b"VP8X":
            chunk[8] &= ~0x0C & 0xFF  # no EXIF, no XMP
        if kind not in (b"EXIF", b"XMP "):
            chunks += chunk
        i = end
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WEBP" + bytes(chunks)


_GIF_KEEP_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")
"""Application extensions that loop an animation; the others (XMP among them) go."""


def _sub_blocks(data: bytes, i: int) -> int:
    while True:
        _need(data, i + 1)
        n = data[i]
        i += 1 + n
        if n == 0:
            _need(data, i)
            return i


def _gif(data: bytes) -> bytes:
    _need(data, 13)
    packed = data[10]
    i = 13 + (3 * 2 ** ((packed & 7) + 1) if packed & 0x80 else 0)
    _need(data, i)
    out = bytearray(data[:i])
    while True:
        _need(data, i + 1)
        b = data[i]
        if b == 0x3B:
            out += b"\x3b"
            return bytes(out)
        if b == 0x21:
            _need(data, i + 3)
            label = data[i + 1]
            end = _sub_blocks(data, i + 2)
            app = data[i + 3 : i + 3 + data[i + 2]] if label == 0xFF else b""
            if label != 0xFE and (label != 0xFF or app in _GIF_KEEP_APPS):
                out += data[i:end]
            i = end
        elif b == 0x2C:
            _need(data, i + 10)
            local = data[i + 9]
            j = i + 10 + (3 * 2 ** ((local & 7) + 1) if local & 0x80 else 0)
            end = _sub_blocks(data, j + 1)
            out += data[i:end]
            i = end
        else:
            raise ValueError("unknown block")


# ── reading ─────────────────────────────────────────────────────────────────────────────────────


def _text_of(kind: str, name: str, data: bytes) -> tuple[str | None, str | None]:
    if kind in IMAGES:
        return None, None
    if kind in ("txt", "md", "csv"):
        return decode(data), None
    try:
        return extract(name, MEDIA_TYPES[kind], data).text(), None
    except Unreadable as e:
        return None, str(e)
    except Exception:  # a damaged file of any kind is a reason, not a crash
        return None, "the file is damaged"


def prepare(name: str, data: bytes, *, kinds: set[str], max_bytes: int) -> Prepared:
    """Check a file, strip its metadata and read its text.

    Args:
        name: The name the person gave it.
        data: Its bytes.
        kinds: The kinds the agent accepts.
        max_bytes: The agent's size limit for one file.

    Returns:
        The prepared file.

    Raises:
        AttachmentError: Too large (413 ``attachment_too_large``), a kind that is refused or not
            accepted by the agent (400 ``attachment_type``), a damaged image (400
            ``attachment_damaged``).
    """
    if len(data) > max_bytes:
        raise AttachmentError(
            f"An attachment is larger than {max_bytes // 1_000_000} MB.",
            code="attachment_too_large",
            status=413,
        )
    name = clean_name(name)
    kind = sniff(name, data)
    if kind not in kinds:
        raise AttachmentError("This agent does not accept this kind of file.")
    data = strip_metadata(kind, data)
    text, unreadable = _text_of(kind, name, data)
    truncated = False
    if text is not None:
        text = text.strip()
        if not text:
            text, unreadable = None, "the document has no text"
        elif len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + TRUNCATED_NOTE.format(n=f"{MAX_TEXT_CHARS:,}")
            truncated = True
    return Prepared(
        name=name, kind=kind, data=data, text=text, unreadable=unreadable, truncated=truncated
    )
