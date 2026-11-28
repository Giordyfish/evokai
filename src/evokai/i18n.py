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


def is_language_tag(tag: str | None) -> bool:
    """Whether a text is a well-formed BCP 47 language tag (``it``, ``pt-BR``, ``zh-Hant``).

    Returns:
        True for a tag, whether or not Evokai ships a pack for its language.
    """
    return bool(_TAG.match(tag or ""))


def session_locale(app: Any, session: Any = None) -> str:
    """The language of a session as its token named it, any language, with or without a pack.

    Messages and speech rules follow :func:`locale_of` (a shipped pack); prompts read this one
    (``{{session.locale}}``), so an answer can be in a language Evokai has no pack for.

    Returns:
        The session's tag, else the application's ``defaults.locale``, else English.
    """
    options = getattr(session, "prompt_options", None) or {}
    own = options.get("locale")
    if isinstance(own, str) and is_language_tag(own):
        return own
    return str(((app.defaults if app is not None else None) or {}).get("locale") or DEFAULT_LOCALE)


def language_name(tag: str) -> str:
    """The English name of a tag's language (``it-IT`` is Italian), for prompts.

    Returns:
        The name, or the tag itself for a language without one.
    """
    from evokai.language_names import LANGUAGE_NAMES

    m = _TAG.match(tag or "")
    return LANGUAGE_NAMES.get(m[1].lower(), tag) if m else tag


def speech_language(app: Any, session: Any = None) -> str:
    """The language speech engines hear and speak in a session.

    Returns:
        The session's pack (:func:`locale_of`), or the primary language of its tag when Evokai
        ships no pack for it (``zh-CN`` is ``zh``).
    """
    tag = session_locale(app, session)
    if pack_language(tag) is None and (m := _TAG.match(tag)):
        return m[1].lower()
    return locale_of(app, session)


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


def speech_redaction(app: Any) -> bool:
    """Whether an application's speech says personal identifiers as neutral words.

    Returns:
        True unless the application set ``speech_redaction: false``.
    """
    return ((app.defaults if app is not None else None) or {}).get("speech_redaction") is not False


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
    redact: bool = True
    """Whether speech replaces personal identifiers with neutral words (the application's
    ``speech_redaction``, on)."""


@lru_cache(maxsize=512)
def speech_rules(
    locale: str = DEFAULT_LOCALE,
    identifiers: tuple[Identifier, ...] = (),
    redact: bool = True,
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
        redact=redact,
    )
