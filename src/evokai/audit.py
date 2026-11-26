"""The audit log of changes.

Every change through the console or the admin and server APIs: who made it, when, and the state
before and after.

Entries never hold a secret (fields named like one are masked, and values that look like one are
redacted) and never a subject in clear (a keyed hash stands for it). Writing an entry never fails
the change: a failure is logged.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from evokai.guard import secrets
from evokai.identity.console import ConsoleLogin
from evokai.identity.models import AppPrincipal
from evokai.mcp_admin.via import client_address, via_mcp
from evokai.observability.correlation import get_correlation_id

log = logging.getLogger("evokai.audit")

SECRET_FIELDS = frozenset({
    "secret",
    "client_secret",
    "client_secret_encrypted",
    "secret_encrypted",
    "password",
    "token",
    "key",
    "credential",
    "forwardCredential",
    "serverKey",
    "adminKey",
    "code",
})
MAX_TEXT = 20_000


def scrub(value: Any) -> Any:
    """A value fit for the audit log: secret fields masked, secret-looking strings redacted.

    Returns:
        The cleaned value.
    """
    if isinstance(value, dict):
        return {
            k: ("***" if k in SECRET_FIELDS and v not in (None, "", False) else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return secrets.redact(value[:MAX_TEXT])
    return value


def actor_of(who: AppPrincipal | ConsoleLogin | None) -> tuple[str, str, str | None]:
    """Who made a change.

    Returns:
        (actor, actor kind, role).
    """
    if who is None:
        return "anonymous", "anonymous", None
    if isinstance(who, ConsoleLogin):
        kind = {"op": "operator_key", "adm": "admin_key", "sso": "sso"}.get(who.kind, who.kind)
        return who.actor, kind, "owner" if who.kind != "sso" else None
    kind = who.via or (
        "console" if who.console else "admin_key" if who.kind == "adm" else "server_key"
    )
    return who.actor or f"key:{who.key_id[:8]}", kind, who.role


async def record(
    request: Request,
    who: AppPrincipal | ConsoleLogin | None,
    action: str,
    *,
    application_id: str | None = None,
    target: str = "",
    before: Any = None,
    after: Any = None,
) -> None:
    """Write an audit entry for a change."""
    actor, kind, role = actor_of(who)
    if isinstance(who, AppPrincipal) and not who.via and via_mcp(request.headers):
        # Through the admin MCP server (this process's own header, never one sent from outside).
        actor, kind = f"mcp:{actor}", "mcp"
    if application_id is None and isinstance(who, AppPrincipal):
        application_id = who.application_id
    try:
        await request.app.state.services.store.audit.add(
            application_id=application_id,
            actor=actor[:200],
            actor_kind=kind,
            role=role,
            action=action[:60],
            target=target[:300],
            before=scrub(before),
            after=scrub(after),
            ip=client_address(request.headers, request.client.host if request.client else None),
            correlation_id=(get_correlation_id() or "")[:64],
        )
    except Exception as e:
        log.warning("audit_not_written", extra={"action": action, "error": type(e).__name__})
