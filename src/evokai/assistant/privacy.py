"""Who reads the console assistant's conversations: each person their own, owners by inspection.

A system application's conversations belong to their subject, ``console:<login>``
(``evokai.assistant.delegation.login_subject``). Every route that returns or touches a
conversation's content on a system application keeps to the caller's own subject
(``own_subject``): the conversation list and detail, notes, traces, the server API's conversations,
the privacy export, bug reports and security events. A key has no subject and sees none. The admin
MCP server uses the same routes with the person's delegation, so the assistant reads no more.

An owner may list other people's conversations without their content and open one only through an
inspection: a written reason, an audit entry the author's console shows
(``POST /v1/admin/assistant/conversations/{id}/inspect``).

Conversations live ``MAX_TTL_DAYS`` at most (the hourly housekeeping deletes them); within that time
the author cannot delete them, so a person's administrative changes stay accountable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from evokai.privacy import subject_key
from evokai.protection import DataProtection, stored_profile

if TYPE_CHECKING:
    from evokai.identity.models import AppPrincipal
    from evokai.services import Services

MAX_TTL_DAYS = 30
"""The longest a system application keeps its conversations (an owner may lower it)."""
NOBODY = "\x00nobody"
"""The subject of a caller without one (a key): it matches no conversation."""
INSPECT = "assistant.conversation.inspect"
"""The audit action of an owner's inspection."""
NOTICE = "assistant.notice"
"""The audit action of a person accepting the first-use notice."""
NOTICE_VERSION = 1
"""The version of the first-use notice: a new version is shown again to everyone."""


async def is_system(svc: Services, application_id: str) -> bool:
    """Whether an application is a system application.

    Returns:
        True for a system application.
    """
    app = await svc.store.applications.get(application_id)
    return bool(app is not None and app.system)


async def own_subject(svc: Services, a: AppPrincipal) -> str | None:
    """The only subject whose conversations the caller may read on its application.

    Returns:
        None on an ordinary application (no restriction); on a system application the caller's
        subject, or ``NOBODY`` for a caller without one.
    """
    if not await is_system(svc, a.application_id):
        return None
    return a.subject or NOBODY


def lists_everyone(a: AppPrincipal) -> bool:
    """Whether the caller may list other people's conversations (without their content).

    Returns:
        True for an owner acting as a person (never the assistant acting for one).
    """
    return a.role == "owner" and not a.via and bool(a.subject)


OUTSIDE_EU = frozenset({"us", "global"})
"""Geographies that admit a processor outside the EU (the browser's online voice is Google's)."""


def online_voice_allowed(app: Any) -> bool:
    """Whether an application's data protection lets the browser's online voice speak its answers.

    The online voice sends the text to the browser vendor's servers, outside the EU. It is allowed
    only when nothing in the application's profile keeps processors in the EU: no residency (new
    or older declaration) that leaves out every geography outside the EU, no jurisdiction rule
    without the United States, no retention rule (the vendor's retention is unknown), and no
    browser declaration that turns online voices off. Otherwise the voice stays on the device.

    Returns:
        True when the online voice is allowed.
    """
    if app is None:
        return False
    regions = (getattr(app, "residency", None) or {}).get("model_regions")
    if regions and not OUTSIDE_EU & {str(r).lower() for r in regions}:
        return False
    raw = stored_profile(getattr(app, "data_protection", None))
    if not raw:
        return True
    profile = DataProtection.from_stored(raw)
    if profile.browser is not None and not profile.browser.online_voices:
        return False
    rule = profile.residency
    if rule is not None and (rule.geographies or rule.countries):
        if not (OUTSIDE_EU & set(rule.geographies or []) or "US" in (rule.countries or [])):
            return False
    if profile.jurisdiction and "US" not in profile.jurisdiction:
        return False
    return profile.retention in (None, "any")


def key_of(svc: Services, application_id: str, subject: str) -> str:
    """A subject's keyed hash as traces and the audit log keep it.

    Returns:
        16 hex characters.
    """
    return subject_key(svc.keyring.signing_key, application_id, subject)[:16]
