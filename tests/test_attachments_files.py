"""Attachment files: the type comes from the bytes, metadata goes, documents are read."""

from __future__ import annotations

import struct
import zlib

import pytest

from evokai.attachments import (
    KINDS,
    MAX_TEXT_CHARS,
    AttachmentError,
    media_type_of,
    prepare,
    sniff,
    strip_metadata,
)
from tests.knowledge_files import docx, pdf

# ── tiny files, byte by byte ────────────────────────────────────────────────────────────────────


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(*extra: tuple[bytes, bytes]) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    chunks = [_png_chunk(b"IHDR", ihdr), *(_png_chunk(k, d) for k, d in extra)]
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"".join(chunks)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _jpeg_segment(marker: int, data: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(data) + 2) + data


GPS = b"Exif\x00\x00MM\x00\x2a GPSLatitude 41.9 GPSLongitude 12.5 Canon"


def jpeg() -> bytes:
    return (
        b"\xff\xd8"
        + _jpeg_segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + _jpeg_segment(0xE1, GPS)
        + _jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta>author</x:xmpmeta>")
        + _jpeg_segment(0xED, b"Photoshop 3.0\x00IPTC author")
        + _jpeg_segment(0xFE, b"a comment with a name")
        + _jpeg_segment(0xDB, b"\x00" + bytes(64))
        + _jpeg_segment(0xDA, b"\x01\x01\x00\x00\x3f\x00")
        + b"\x12\x34\xff\x00\x56"
        + b"\xff\xd9"
    )


def _riff_chunk(kind: bytes, data: bytes) -> bytes:
    pad = b"\x00" if len(data) % 2 else b""
    return kind + struct.pack("<I", len(data)) + data + pad


def webp() -> bytes:
    flags = 0x08 | 0x04  # EXIF and XMP present
    vp8x = bytes([flags, 0, 0, 0]) + (0).to_bytes(3, "little") + (0).to_bytes(3, "little")
    body = (
        b"WEBP"
        + _riff_chunk(b"VP8X", vp8x)
        + _riff_chunk(b"VP8L", b"\x2f\x00\x00\x00\x00")
        + _riff_chunk(b"EXIF", GPS)
        + _riff_chunk(b"XMP ", b"<x:xmpmeta>author</x:xmpmeta>")
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


def gif() -> bytes:
    header = b"GIF89a" + struct.pack("<HH", 1, 1) + b"\x80\x00\x00" + b"\x00\x00\x00\xff\xff\xff"
    netscape = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    xmp = b"\x21\xff\x0bXMP DataXMP\x06author\x00"
    comment = b"\x21\xfe\x0ea secret place\x00"
    image = b"\x2c" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02\x02\x44\x01\x00"
    return header + netscape + xmp + comment + image + b"\x3b"


# ── sniffing ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "data", "kind"),
    [
        ("a.bin", png(), "png"),
        ("photo", jpeg(), "jpeg"),
        ("x.png", webp(), "webp"),
        ("anim.jpg", gif(), "gif"),
        ("doc", pdf(["hello"]), "pdf"),
        ("w.pdf", docx([("", "Hello")]), "docx"),
        ("notes.txt", b"plain words\n", "txt"),
        ("notes.md", b"# Title\n\nWords", "md"),
        ("table.csv", b"a,b\n1,2\n", "csv"),
        ("noext", b"just text", "txt"),
    ],
)
def test_the_kind_comes_from_the_bytes(name: str, data: bytes, kind: str) -> None:
    assert sniff(name, data) == kind
    assert kind in KINDS


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("cute.png", b"<!DOCTYPE html><html><script>alert(1)</script></html>"),
        ("logo.svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'),
        ("logo.png", b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"></svg>'),
        ("page.txt", b"  <html><body>hi</body></html>"),
        ("feed.md", b"<?xml version='1.0'?><rss></rss>"),
        ("tool.exe", b"MZ\x90\x00\x03\x00\x00\x00binary\x00\x00"),
        ("archive.docx", b"PK\x03\x04not really a word file"),
        ("empty.txt", b""),
    ],
)
def test_markup_binaries_and_lies_are_refused(name: str, data: bytes) -> None:
    with pytest.raises(AttachmentError) as e:
        sniff(name, data)
    assert e.value.code == "attachment_type"
    assert e.value.status == 400


def test_media_types() -> None:
    assert media_type_of("jpeg") == "image/jpeg"
    assert media_type_of("docx").endswith("wordprocessingml.document")
    assert media_type_of("md") == "text/markdown"


# ── metadata ────────────────────────────────────────────────────────────────────────────────────


def test_jpeg_loses_exif_gps_xmp_iptc_and_comments() -> None:
    out = strip_metadata("jpeg", jpeg())
    assert out.startswith(b"\xff\xd8\xff\xe0") and out.endswith(b"\xff\xd9")
    for gone in (b"GPSLatitude", b"Canon", b"xmpmeta", b"IPTC", b"a comment"):
        assert gone not in out
    assert b"\x12\x34\xff\x00\x56" in out  # the image data is untouched
    assert sniff("x.jpg", out) == "jpeg"


def test_png_loses_text_and_exif_chunks() -> None:
    data = png((b"tEXt", b"Author\x00Someone"), (b"eXIf", GPS), (b"tIME", bytes(7)))
    out = strip_metadata("png", data)
    assert b"Someone" not in out and b"GPSLatitude" not in out and b"tIME" not in out
    assert b"IHDR" in out and b"IDAT" in out and out.endswith(_png_chunk(b"IEND", b""))


def test_webp_loses_exif_and_xmp_and_stays_valid() -> None:
    out = strip_metadata("webp", webp())
    assert b"GPSLatitude" not in out and b"xmpmeta" not in out
    assert struct.unpack("<I", out[4:8])[0] == len(out) - 8
    flags = out[20]
    assert flags & 0x0C == 0  # the VP8X header no longer announces EXIF or XMP
    assert b"VP8L" in out


def test_gif_loses_comments_and_foreign_application_blocks() -> None:
    out = strip_metadata("gif", gif())
    assert b"secret place" not in out and b"XMP Data" not in out
    assert b"NETSCAPE2.0" in out  # the loop count of an animation stays
    assert out.endswith(b"\x3b") and out.startswith(b"GIF89a")


def test_documents_are_not_rewritten() -> None:
    data = pdf(["hello"])
    assert strip_metadata("pdf", data) == data


# ── prepare ─────────────────────────────────────────────────────────────────────────────────────


def test_prepare_an_image() -> None:
    p = prepare("../../etc/me.jpg", jpeg(), kinds=set(KINDS), max_bytes=1_000_000)
    assert p.kind == "jpeg" and p.media_type == "image/jpeg"
    assert p.name == "me.jpg"  # never a path
    assert b"GPSLatitude" not in p.data
    assert p.size == len(p.data) and len(p.sha256) == 64
    assert p.text is None and p.image


def test_prepare_reads_documents() -> None:
    p = prepare(
        "cv.docx",
        docx([("Heading1", "Experience"), ("", "Ten years")]),
        kinds=set(KINDS),
        max_bytes=1_000_000,
    )
    assert p.text is not None and "Ten years" in p.text
    assert not p.image


def test_a_scanned_pdf_has_no_text_but_says_why() -> None:
    p = prepare("scan.pdf", pdf([None]), kinds=set(KINDS), max_bytes=1_000_000)
    assert p.text is None and p.unreadable


def test_long_text_is_cut() -> None:
    p = prepare("big.txt", b"word " * 30_000, kinds=set(KINDS), max_bytes=1_000_000)
    assert p.text is not None and len(p.text) <= MAX_TEXT_CHARS + 200
    assert p.truncated


def test_a_type_the_agent_does_not_take() -> None:
    with pytest.raises(AttachmentError) as e:
        prepare("a.png", png(), kinds={"pdf"}, max_bytes=1_000_000)
    assert e.value.code == "attachment_type"


def test_too_large() -> None:
    with pytest.raises(AttachmentError) as e:
        prepare("a.png", png(), kinds=set(KINDS), max_bytes=10)
    assert (e.value.code, e.value.status) == ("attachment_too_large", 413)


def test_a_damaged_image_is_refused() -> None:
    with pytest.raises(AttachmentError) as e:
        prepare("a.png", b"\x89PNG\r\n\x1a\n\x00\x00", kinds=set(KINDS), max_bytes=1_000)
    assert e.value.code == "attachment_damaged"
