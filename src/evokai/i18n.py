"""Language packs: user-facing messages and speech rules per locale.

Defaults are English. Other languages are data files in ``evokai/locales`` (``it.json``), picked
by an application's ``locale``; an application may also override any message in
``defaults.messages``. Model-facing text (prompts, tool descriptions, tool results) is English.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

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


def locale_of(app: Any) -> str:
    """An application's locale.

    Returns:
        ``defaults.locale``, else English.
    """
    return str(((app.defaults if app is not None else None) or {}).get("locale") or DEFAULT_LOCALE)


def message(app: Any, key: str, **params: Any) -> str:
    """A user-facing message: the application's own text, else its locale's, else English.

    Returns:
        The text with ``{name}`` parameters filled in (``{appName}`` always available).
    """
    defaults = (app.defaults if app is not None else None) or {}
    text = (defaults.get("messages") or {}).get(key) or pack(locale_of(app))["messages"][key]
    values = {"appName": getattr(app, "name", ""), **params}
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


@dataclass(frozen=True, eq=False)
class SpeechRules:
    """Language rules of speech: redaction words, sources headings, abbreviations, replies.

    ``locale`` is the language they were asked for (it picks how numbers are read aloud).

    Compared and hashed by identity: one cached instance per locale (:func:`speech_rules`).
    """

    redactions: dict[str, str]
    sources_headings: tuple[str, ...]
    abbreviations: tuple[str, ...]
    yes: tuple[str, ...]
    no: tuple[str, ...]
    ordinals: dict[str, int]
    locale: str = DEFAULT_LOCALE


@cache
def speech_rules(locale: str = DEFAULT_LOCALE) -> SpeechRules:
    """The speech rules of a locale.

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
    )
