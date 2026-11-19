"""Text normalisation for the detectors: what an attacker hides must be seen as plain text.

Rules match the normalised text, never the raw one: Unicode compatibility forms are folded (NFKC),
invisible characters (zero-width, bidirectional controls, soft hyphens, tag characters) are removed,
look-alike letters from Cyrillic and Greek are folded to Latin, and whitespace is collapsed. The
same pass reports what it found, because the obfuscation itself is a signal.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

INVISIBLE = re.compile(
    r"[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180e\u200b-\u200f\u202a-\u202e"
    r"\u2060-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\U000e0000-\U000e007f]"
)
"""Zero-width spaces and joiners, bidirectional controls, variation selectors, tag characters."""

_LOOKALIKES = (
    "\u0430\u0432\u0435\u043a\u043c\u043d\u043e\u0440\u0441\u0442\u0443\u0445\u0455\u0456"
    "\u0458\u0501\u0261\u051b\u051d\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421"
    "\u0422\u0423\u0425\u0405\u0406\u0408\u03b1\u03bf\u03c1\u03b5\u03b9\u03ba\u03bd\u03c4"
    "\u03c5\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5"
    "\u03a7"
)
"""Cyrillic and Greek letters that render like Latin ones (escaped: the source stays ASCII)."""
_AS_LATIN = "abekmhopctyxsijdgqwABEKMHOPCTYXSIJaopeikvtuABEZHIKMNOPTYX"
_HOMOGLYPHS = str.maketrans(_LOOKALIKES, _AS_LATIN)
"""NFKC already folds full-width and mathematical forms; this folds the look-alikes."""

_LATIN = re.compile(r"[A-Za-z]")
_CONFUSABLE = re.compile(f"[{re.escape(_LOOKALIKES)}]")
_WORD = re.compile(r"\S+")
_SPACES = re.compile(r"\s+")
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9+/=_-])")
_PRINTABLE = re.compile(r"^[\x20-\x7e\t\r\n\u00a0-\u024f]+$")


@dataclass
class Normalized:
    """A text as the detectors see it, and the tricks found on the way."""

    text: str
    """NFKC, invisible characters removed, homoglyphs folded, whitespace collapsed, lower case."""
    invisible: int = 0
    """How many invisible characters were removed."""
    mixed_scripts: int = 0
    """Words that mix Latin letters with look-alike Cyrillic or Greek ones."""
    decoded: list[str] = field(default_factory=list)
    """Readable texts hidden in base64 blobs."""
    blobs: int = 0
    """Base64-looking blobs (decodable or not)."""


def _fold_homoglyphs(text: str) -> tuple[str, int]:
    mixed = 0
    for word in _WORD.findall(text):
        if _LATIN.search(word) and _CONFUSABLE.search(word):
            mixed += 1
    return text.translate(_HOMOGLYPHS), mixed


def decode_blob(blob: str) -> str | None:
    """Decode a base64 (or base64url) blob into readable text, if it is one.

    Returns:
        The decoded text, or None when it is not printable text.
    """
    raw = blob.replace("-", "+").replace("_", "/")
    raw += "=" * (-len(raw) % 4)
    try:
        data = base64.b64decode(raw, validate=True)
        text = data.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return text if len(text) >= 12 and _PRINTABLE.match(text) else None


def normalize(text: str, *, decode: bool = True) -> Normalized:
    """Normalise a text for matching.

    Args:
        text: The raw text.
        decode: Also look for readable texts inside base64 blobs.

    Returns:
        The normalised text and what was hidden in it.
    """
    nfkc = unicodedata.normalize("NFKC", text)
    stripped, invisible = INVISIBLE.subn("", nfkc)
    folded, mixed = _fold_homoglyphs(stripped)
    out = Normalized(
        text=_SPACES.sub(" ", folded).strip().lower(), invisible=invisible, mixed_scripts=mixed
    )
    if decode:
        for blob in _BASE64.findall(stripped)[:20]:
            out.blobs += 1
            hidden = decode_blob(blob)
            if hidden:
                out.decoded.append(_SPACES.sub(" ", hidden).strip().lower())
    return out


def strip_invisible(text: str) -> str:
    """Remove invisible characters (zero-width, bidirectional controls, tag characters).

    Returns:
        The text without them.
    """
    return INVISIBLE.sub("", text)
