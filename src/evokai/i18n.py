"""Language packs: user-facing messages and speech rules per locale.

Defaults are English. Other languages are data files in ``evokai/locales`` (``it.json``), picked
by an application's ``locale``; an application may also override any message in
``defaults.messages``. Model-facing text (prompts, tool descriptions, tool results) is English.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache, lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evokai.guard.pii import Identifier

DEFAULT_LOCALE = "en"


@cache
def pack(locale: str) -> dict[str, Any]:
    """A language pack, falling back to English for unknown locales and missing keys.

    Returns:
        ``messages``, ``speech`` and ``calendar`` (weekday and month names).
    """
    base = _load(DEFAULT_LOCALE)
    if locale == DEFAULT_LOCALE:
        return base
    try:
        own = _load(locale)
    except FileNotFoundError:
        return base
    return {
        "messages": {**base["messages"], **own.get("messages", {})},
        "speech": {**base["speech"], **own.get("speech", {})},
        "calendar": {**base.get("calendar", {}), **own.get("calendar", {})},
    }


def _load(locale: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z]{2}(-[A-Z]{2})?", locale):
        raise FileNotFoundError(locale)
    path = resources.files("evokai.locales") / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@cache
def known_locales() -> frozenset[str]:
    """The languages Evokai ships a pack for.

    Returns:
        Their codes (``en``, ``it``).
    """
    files = resources.files("evokai.locales").iterdir()
    return frozenset(f.name[:-5] for f in files if f.name.endswith(".json"))


_TAG = re.compile(r"^([A-Za-z]{2,3})(-[A-Za-z0-9]{1,8})*$")


def pack_language(tag: str | None) -> str | None:
    """The shipped pack for a BCP 47 language tag (``it-IT`` reads the ``it`` pack).

    Returns:
        The pack's language, or None for a malformed tag or a language without a pack.
    """
    m = _TAG.match(tag or "")
    if not m:
        return None
    language = m[1].lower()
    return language if language in known_locales() else None


def locale_of(app: Any, session: Any = None) -> str:
    """The locale of an application, or of one of its sessions.

    Args:
        app: The application.
        session: A session whose token chose a locale (``prompt_options.locale``), if any.

    Returns:
        The session's locale (its pack's language), else ``defaults.locale``, else English.
    """
    options = getattr(session, "prompt_options", None) or {}
    if own := pack_language(options.get("locale")):
        return own
    return str(((app.defaults if app is not None else None) or {}).get("locale") or DEFAULT_LOCALE)


def message(app: Any, key: str, *, session: Any = None, **params: Any) -> str:
    """A user-facing message: the application's own text, else its locale's, else English.

    Args:
        app: The application.
        key: The message key.
        session: The session, whose token may have chosen another locale (``locale_of``).
        **params: Values for the ``{name}`` placeholders.

    Returns:
        The text with ``{name}`` parameters filled in (``{appName}`` always available).
    """
    defaults = (app.defaults if app is not None else None) or {}
    text = (defaults.get("messages") or {}).get(key) or pack(locale_of(app, session))["messages"][
        key
    ]
    values = {"appName": getattr(app, "name", ""), **params}
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


@dataclass(frozen=True, eq=False)
class SpeechRules:
    """Language rules of speech: redaction words, sources headings, abbreviations, replies.

    ``locale`` is the language they were asked for (it picks how numbers are read aloud);
    ``identifiers`` are the application's own (``application.identifiers``), said the way each
    one asks.

    Compared and hashed by identity: one cached instance per locale and set of identifiers
    (:func:`speech_rules`).
    """

    redactions: dict[str, str]
    sources_headings: tuple[str, ...]
    abbreviations: tuple[str, ...]
    yes: tuple[str, ...]
    no: tuple[str, ...]
    ordinals: dict[str, int]
    locale: str = DEFAULT_LOCALE
    identifiers: tuple[Identifier, ...] = ()


@lru_cache(maxsize=512)
def speech_rules(
    locale: str = DEFAULT_LOCALE, identifiers: tuple[Identifier, ...] = ()
) -> SpeechRules:
    """The speech rules of a locale, with an application's own identifiers.

    Returns:
        The rules.
    """
    s = pack(locale)["speech"]
    return SpeechRules(
        redactions=dict(s["redactions"]),
        sources_headings=tuple(s["sourcesHeadings"]),
        abbreviations=tuple(s["abbreviations"]),
        yes=tuple(s["yes"]),
        no=tuple(s["no"]),
        ordinals=dict(s["ordinals"]),
        locale=locale,
        identifiers=identifiers,
    )
