"""Spoken replies matched by rules, not by a model.

Confirmations, option picks, and echo detection.

The words come from the locale (see evokai.i18n); the client uses the same rules, so a spoken
"yes" or "the second one" means the same on both sides.
"""

from __future__ import annotations

import re
import unicodedata
from functools import cache

from evokai.i18n import SpeechRules, speech_rules


@cache
def _words(words: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(re.escape(norm(w)) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"^({alternatives})\b")


def norm(text: str) -> str:
    """Lower case, no accents, letters and digits only, single spaces.

    Returns:
        The normalised text.
    """
    s = unicodedata.normalize("NFD", text.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^\w ]|_", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def spoken_decision(said: str, rules: SpeechRules | None = None) -> bool | None:
    """A spoken yes or no to a confirmation.

    Returns:
        True, False, or None when it is neither.
    """
    r = rules or speech_rules()
    s = norm(said)
    if _words(r.yes).match(s):
        return True
    if _words(r.no).match(s):
        return False
    return None


def spoken_option(said: str, options: list[str], rules: SpeechRules | None = None) -> str | None:
    """Map a spoken reply to an option: by number or ordinal («la seconda», «2») or by its words.

    Returns:
        The option, or None.
    """
    s = norm(said)
    num = None
    if m := re.search(r"\b([1-5])\b", s):
        num = int(m.group(1))
    else:
        # The ordinal said first wins: "the second one" is 2.
        ordinals = (rules or speech_rules()).ordinals
        found = [
            (m.start(), n)
            for w, n in ordinals.items()
            if (m := re.search(rf"\b{re.escape(norm(w))}\b", s))
        ]
        num = min(found)[1] if found else None
    if num and 0 < num <= len(options):
        return options[num - 1]
    best, best_score = None, 0.0
    for o in options:
        words = [w for w in norm(o).split(" ") if len(w) > 2]
        score = sum(1 for w in words if w in s) / len(words) if words else 0.0
        if score > best_score:
            best, best_score = o, score
    return best if best_score >= 0.5 else None


def is_echo(heard: str, spoken: str) -> bool:
    """Whether what the microphone heard is mostly the agent's own voice.

    Returns:
        True for an echo.
    """
    h = [w for w in norm(heard).split(" ") if w]
    if not h:
        return True
    if len(h) < 4:
        # Short replies ("yes, go ahead") are answers, even when the agent used the same words.
        return False
    said = set(norm(spoken).split(" "))
    return sum(1 for w in h if w in said) / len(h) > 0.6
