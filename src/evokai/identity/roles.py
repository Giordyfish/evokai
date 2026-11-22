"""Console roles, checked on the server for every admin and console route.

- **viewer**: reads everything of its applications, changes nothing.
- **operator**: also changes agents, permissions (grants), MCP servers, skills, prompts and keys,
  runs the playground, and answers people's data requests.
- **owner**: everything of its applications, including their settings (caps, origins, models,
  retention, residency) and their security policy. An owner of every application (``*``) is a
  **deployment owner**: creates applications and manages console sign-in and its users.

An admin key's login is the owner of its application; the operator key's login is a deployment
owner. Single sign-on users get roles from their identity provider's claims.

The rule of a route is decided by its method and path template: reads need viewer, changes need
operator, and the routes in ``OWNER_ROUTES`` need owner. A route without an entry is a change like
any other, so a new route is never open by mistake.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Request

from evokai.core.errors import Forbidden

Role = Literal["viewer", "operator", "owner"]
ROLES: tuple[Role, ...] = ("viewer", "operator", "owner")
RANK: dict[str, int] = {r: i for i, r in enumerate(ROLES)}

OWNER_ROUTES: frozenset[tuple[str, str]] = frozenset({
    ("PATCH", "/v1/admin/application"),
    ("PUT", "/v1/admin/security"),
    ("PUT", "/v1/admin/voices/{provider}/{voice_id}"),
    ("POST", "/v1/admin/agents/{agent}/versions/{version}/publish"),
    ("PUT", "/v1/admin/compliance/profile"),
    ("POST", "/v1/admin/application/unlock"),
    ("POST", "/v1/admin/application/reset"),
    ("POST", "/v1/admin/assistant/activate"),
    ("POST", "/v1/admin/assistant/try"),
})
"""Changes that need the owner role (application settings and policy, the deployment's voices)."""

READ_ONLY_POSTS: frozenset[str] = frozenset({
    "/v1/console/skills/parse",
    "/v1/admin/knowledge/{name}/search",
    "/v1/admin/workflows/validate",
    "/v1/console/assistant/session",
})
"""POST routes that change nothing of the application (they parse what they are sent, or open the
console assistant's own session, which acts with the caller's role)."""


def at_least(role: str | None, needed: str) -> bool:
    """Whether a role includes another.

    Returns:
        True when ``role`` is ``needed`` or above.
    """
    return role in RANK and RANK[role] >= RANK[needed]


def highest(*roles: str | None) -> Role | None:
    """The highest of some roles.

    Returns:
        The role, or None when none is given.
    """
    known = [r for r in roles if r in RANK]
    return max(known, key=RANK.__getitem__) if known else None  # ty: ignore[invalid-explicit-return]


def route_of(request: Request, base_path: str) -> str:
    """The path template of the route a request matched, without the base path.

    Returns:
        e.g. ``/v1/admin/agents/{name}``.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    base = base_path.rstrip("/")
    return path[len(base) :] if base and path.startswith(base) else path


def required(method: str, path: str) -> Role:
    """The role a route needs.

    Returns:
        viewer for reads, owner for ``OWNER_ROUTES``, operator for every other change.
    """
    method = method.upper()
    if (method, path) in OWNER_ROUTES:
        return "owner"
    if method in {"GET", "HEAD", "OPTIONS"} or (method == "POST" and path in READ_ONLY_POSTS):
        return "viewer"
    return "operator"


def authorize(request: Request, role: str | None, base_path: str) -> None:
    """Refuse a request the role does not allow.

    Raises:
        Forbidden: The role is below what the route needs (403 ``role``).
    """
    needed = required(request.method, route_of(request, base_path))
    if not at_least(role, needed):
        raise Forbidden(f"This needs the {needed} role.", code="role")
