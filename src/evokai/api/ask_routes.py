"""The application's ask rules: tool calls that wait for the person's confirmation.

``GET /v1/admin/ask-rules`` shows the application's rules with the deployment's defaults before them
and each agent's after them, and the tools they can name; ``PUT`` replaces the application's rules.
Rules only add confirmations (``evokai.guard.ask``): an effect other than ``ask`` or ``default`` is
refused, and so is a ``default`` rule that names a tool that changes data.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.core.errors import BadRequest, NotFound
from evokai.db import models as m
from evokai.guard.ask import AskRules, check_rules, explain, parse_rules, stored
from evokai.identity.models import AppPrincipal
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


async def known_tools(svc: Services, application_id: str) -> list[dict[str, Any]]:
    """The MCP tools of the application's servers, as last listed, and whether each changes data.

    Returns:
        ``{name, server, mutating, description}`` sorted by name.
    """
    out = [
        {
            "name": f"{s.tool_prefix or ''}{t['name']}",
            "server": s.name,
            "mutating": bool(t.get("mutating")),
            "description": str(t.get("description") or "")[:300],
        }
        for s in await svc.store.mcp_servers.all(application_id)
        for t in s.tools or []
    ]
    return sorted(out, key=lambda x: x["name"])


def validated(raw: Any, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rules as they will be stored, checked: only ``ask`` and ``default``, no lifted write.

    Returns:
        The stored form.

    Raises:
        BadRequest: A rule is not valid (400 ``ask_rules``), with every problem in the message.
    """
    try:
        rules = parse_rules(raw)
    except ValidationError as e:
        raise BadRequest(explain(e), code="ask_rules") from e
    problems = check_rules(rules, {t["name"] for t in tools if t["mutating"]})
    if problems:
        raise BadRequest("; ".join(problems), code="ask_rules")
    return stored(rules)


async def ask_view(svc: Services, app: m.Application) -> dict[str, Any]:
    """The application's rules, the deployment's before them, each agent's after them.

    Returns:
        rules, deployment, agents (``{name, rules}``), tools.
    """
    agents = [
        {"name": r.name, "rules": r.definition.get("ask_rules") or []}
        for r in await svc.store.agents.active_all(app.id)
    ]
    return {
        "rules": app.ask_rules or [],
        "deployment": AskRules.build(deployment=svc.settings.ask_rules).view(),
        "agents": [a for a in agents if a["rules"]],
        "tools": await known_tools(svc, app.id),
    }


class AskRulesBody(BaseModel):
    """The application's rules, in order (they replace the current ones)."""

    rules: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


@router.get("/ask-rules")
async def get_ask_rules(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's ask rules, with the deployment's and the agents'.

    Returns:
        The view.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return await ask_view(svc, app)


@router.put("/ask-rules")
async def put_ask_rules(body: AskRulesBody, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Replace the application's ask rules. Effective from the next turn.

    Returns:
        The view, as ``GET``.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    rules = validated(body.rules, await known_tools(svc, app.id))
    before = app.ask_rules
    await svc.store.applications.update(app.id, ask_rules=rules or None)
    await audit.record(request, a, "ask_rules.put", before=before, after=rules)
    fresh = await svc.store.applications.get(app.id)
    assert fresh is not None  # ruff: ignore[assert] (just updated)
    return await ask_view(svc, fresh)
