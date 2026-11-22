"""Who is calling.

Two kinds of callers:

- an **application** with its API key (server-to-server, or admin), from its own backend;
- a **session** with a short-lived token the application minted for one subject, one agent and
  one conversation (the browser holds only this).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evokai.core.security import SessionClaims
from evokai.db.repositories import Who


@dataclass(frozen=True)
class AppPrincipal:
    """An application authenticated with an API key."""

    application_id: str
    kind: str
    """``srv`` or ``adm``."""
    key_id: str
    console: bool = False
    """Authenticated by a console login (a person's browser): amounts are never sent to it."""
    role: str = "owner"
    """The console role on this application (keys act as its owner)."""
    actor: str = ""
    """Who, for the audit log: ``key:<prefix>``, ``admin-key:<id>``, ``operator-key``,
    ``sso:<e-mail>``, ``assistant:<the console login>``."""
    via: str = ""
    """``assistant`` for the console assistant's delegation token (``evokai.assistant``)."""


@dataclass(frozen=True)
class SessionPrincipal:
    """A browser session for one subject, one agent and one conversation."""

    claims: SessionClaims
    origins: list[str] = field(default_factory=list)

    @property
    def who(self) -> Who:
        """The data owner.

        Returns:
            Application and subject.
        """
        return Who(application_id=self.claims.application_id, subject=self.claims.subject)
