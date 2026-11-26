"""Phrases speech recognisers make up from silence or noise, dropped from transcripts.

Recognisers trained on subtitled video (the Whisper family above all) answer silence, a breath or
background noise with the credits and sign-offs that closed their training clips: «Thanks for
watching», «Subtitles by the Amara.org community», and their versions in other languages. Each
language pack lists the ones known in its language (``speech.hallucinations`` in
``evokai/locales/<code>.json``). A transcript that is one of them, or nothing but a sound
annotation (``[Music]``, ``(applause)``) or punctuation, is not something the person said.

The English phrases apply in every language: a recogniser asked for Italian still answers silence
with an English sign-off. A pattern is compared with the whole transcript, both lowercased with
punctuation turned into spaces, and ``*`` stands for any text (``subtitles by *``), so a phrase
inside a longer sentence never matches unless its pattern says so.
"""

from __future__ import annotations

import fnmatch
import re
import unicodedata
from functools import lru_cache

from evokai.i18n import DEFAULT_LOCALE, pack

_ANNOTATION = re.compile(r"^\s*(\[[^\]]*\]|\([^)]*\)|\*[^*]*\*)\s*$")
"""A transcript that is only a sound annotation: ``[Music]``, ``(laughter)``, ``*cough*``."""
_SEPARATORS = re.compile(r"[^\w*]+")
_ROMANIAN = str.maketrans({"ş": "ș", "ţ": "ț", "Ş": "Ș", "Ţ": "Ț"})
"""Romanian s and t with a cedilla, still common, read as their comma-below letters."""


def normal(text: str) -> str:
    """A transcript or a pattern as they are compared.

    Returns:
        Lowercased (Unicode NFKC, case-folded), punctuation as single spaces, trimmed; ``*`` kept.
    """
    folded = unicodedata.normalize("NFKC", text.translate(_ROMANIAN)).casefold()
    return _SEPARATORS.sub(" ", folded.replace("_", " ")).strip()


@lru_cache(maxsize=64)
def patterns(locale: str) -> tuple[str, ...]:
    """The phrases of a language, and the English ones, normalised.

    Returns:
        The patterns.
    """
    own = pack(locale)["speech"].get("hallucinations") or []
    english = pack(DEFAULT_LOCALE)["speech"].get("hallucinations") or []
    return tuple(dict.fromkeys(normal(p) for p in [*own, *english] if normal(p)))


def made_up(text: str, locale: str = DEFAULT_LOCALE) -> bool:
    """Whether a transcript is a phrase recognisers make up rather than speech.

    Args:
        text: What the engine heard.
        locale: The language pack of the session.

    Returns:
        True for a known phrase of the language (or English), a lone sound annotation, or
        punctuation only; False for an empty transcript (there is nothing to drop) and for speech.
    """
    if not text.strip():
        return False
    if _ANNOTATION.match(text):
        return True
    said = normal(text).replace("*", " ").strip()
    if not said:
        return True
    return any(fnmatch.fnmatchcase(said, p) for p in patterns(locale))
