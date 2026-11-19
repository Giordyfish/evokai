"""Console preferences: how the console looks and reads for one person, kept per console identity.

A small document with an allow-list of fields, each an enum or a flag, so nothing but these
choices can be stored. The console applies it at once, caches it in the browser for the first
paint, and keeps the copy here so it follows the person across browsers.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

from evokai.identity.console import ConsoleLogin

VERSION = 1


class Preferences(BaseModel):
    """One person's console preferences (version 1). JSON fields are camelCase."""

    model_config = ConfigDict(
        extra="forbid", strict=True, alias_generator=to_camel, validate_by_name=True
    )

    version: Literal[1] = VERSION
    language: Literal["auto", "en", "it"] = "auto"
    """``auto`` follows the browser."""
    date_format: Literal["auto", "dmy", "mdy", "ymd"] = "auto"
    """``auto`` is the language's own order."""
    time_format: Literal["auto", "24h", "12h"] = "auto"
    theme: Literal["system", "light", "dark"] = "system"
    density: Literal["comfortable", "compact"] = "comfortable"
    motion: Literal["system", "reduce", "full"] = "system"
    """``system`` follows the operating system's reduced motion setting."""
    mascot: Literal["on", "still", "hidden"] = "on"
    text_size: Literal[100, 112, 125] = 100
    """Percent."""
    contrast: Literal["system", "more", "normal"] = "system"
    """``system`` follows the operating system's contrast setting."""
    focus_always: bool = False
    """Show the focus outline on every focused control, also after a click."""
    underline_links: bool = False

    def document(self) -> dict[str, Any]:
        """The document as stored and sent (camelCase).

        Returns:
            The JSON document.
        """
        return self.model_dump(by_alias=True)


DEFAULTS = Preferences()


def read(value: Any) -> Preferences:
    """Stored preferences, or the defaults when they no longer validate.

    Returns:
        The preferences.
    """
    try:
        return Preferences.model_validate(value)
    except ValidationError:
        return DEFAULTS


def identity_of(login: ConsoleLogin) -> str:
    """The key preferences are kept under for a console login.

    Returns:
        ``sso:<user id>``, ``admin-key:<key id>`` (or ``admin-app:<application id>`` for a login
        without a key id) or ``operator``.
    """
    if login.kind == "sso":
        return f"sso:{login.user_id or login.email}"[:120]
    if login.kind == "adm":
        if login.key_id:
            return f"admin-key:{login.key_id}"[:120]
        return f"admin-app:{login.application_id}"[:120]
    return "operator"
