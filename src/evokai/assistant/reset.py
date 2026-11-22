"""What a system application may hold, and the reset that brings it back to the shipped definition.

- **Own kinds only.** On a system application, locked or not, a change that would create or alter a
  resource the assistant does not own is refused (409 ``system_app``): workflows, knowledge bases,
  other agents, channels, OAuth apps, capability credentials, keys and other MCP servers. What stays
  writable (when unlocked): its agent and that agent's evals, prompts, skills, its MCP server entry,
  grants, limits and settings (``own_write``).
- **The reset deletes exactly what its preview lists.** ``plan`` lists every item the reset deletes
  or restores, by kind and name, with each deleted item's definition (no secrets) for the owner to
  keep; ``reset`` deletes those items and applies the shipped definition again. Conversations
  (they follow their time to live), eval and red team results and the audit log are not part of
  the definition and stay.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from evokai.agents.skills import skills_in
from evokai.assistant import bootstrap as shipped
from evokai.assistant.activation import MEASURED_KEY
from evokai.audit import scrub
from evokai.core.caps import DEFAULT_MONTHLY_USD
from evokai.db import models as m

if TYPE_CHECKING:
    from evokai.services import Services

_AGENT = {"name": shipped.AGENT}
_OF_AGENT = {"agent": shipped.AGENT}
OWN_WRITES: dict[str, Mapping[str, str] | None] = {
    "/v1/admin/application": None,
    "/v1/admin/application/unlock": None,
    "/v1/admin/application/reset": None,
    "/v1/admin/assistant/activate": None,
    "/v1/admin/assistant/try": None,
    "/v1/admin/assistant/conversations/{conversation_id}/inspect": None,
    "/v1/admin/agents/{name}": _AGENT,
    "/v1/admin/agents/{agent}/switch-model": _OF_AGENT,
    "/v1/admin/agents/{agent}/model-recommendations": _OF_AGENT,
    "/v1/admin/agents/{agent}/versions/{version}/publish": _OF_AGENT,
    "/v1/admin/agents/{agent}/evals/cases/{name}": _OF_AGENT,
    "/v1/admin/agents/{agent}/evals/runs": _OF_AGENT,
    "/v1/admin/agents/{agent}/redteam/runs": _OF_AGENT,
    "/v1/admin/prompts/{name}": None,
    "/v1/admin/prompts/{name}/preview": None,
    "/v1/admin/skills/{name}": None,
    "/v1/admin/skills/{name}/files/{path}": None,
    "/v1/admin/mcp-servers/{name}": {"name": shipped.MCP_SERVER},
    "/v1/admin/mcp-servers/{name}/test": {"name": shipped.MCP_SERVER},
    "/v1/admin/grants": None,
    "/v1/admin/security": None,
    "/v1/admin/ask-rules": None,
    "/v1/admin/costs/alerts/{alert_id}/ack": None,
    "/v1/admin/compliance/profile": None,
    "/v1/admin/compliance/snapshots": None,
    "/v1/admin/conversations/{conversation_id}/notes": None,
    "/v1/admin/conversations/{conversation_id}/notes/{note_id}": None,
    "/v1/admin/voices/{provider}/{voice_id}": None,
}
"""Admin routes that change only the system application's own resources (a path parameter, when
given, must name the assistant's own agent or MCP server). The deployment's voices are not the
application's: an owner changes them whatever application is selected."""


FRESH: dict[str, Any] = {
    "monthly_cap_usd": DEFAULT_MONTHLY_USD,
    "daily_cap_usd": None,
    "currency": "USD",
    "exchange_rate": None,
    "caps": None,
    "conversation_ttl_days": None,
    "allowed_origins": [],
    "allowed_models": [],
    "storage_mode": "evokai",
    "security": None,
    "ask_rules": None,
    "residency": None,
    "data_protection": None,
    "security_event_ttl_days": None,
    "audit_ttl_days": None,
}
"""Every application setting as a new application has it: a reset sets them all back before the
shipped definition sets its own (a test keeps this list complete as columns are added)."""
IDENTITY = frozenset({
    "id",
    "slug",
    "name",
    "status",
    "system",
    "locked",
    "system_version",
    "created_at",
})
"""Columns that say which application it is, not how it is set."""
KEPT_DEFAULTS = (shipped.MODEL_KEY, MEASURED_KEY)
"""Defaults a reset keeps: the activated model and the owner's measured tries (results, not
settings). Every other default goes, the shipped definition writes its own."""


def _settings_changed(app: m.Application) -> dict[str, Any]:
    """The settings a reset sets back: columns not as a new application has them, and defaults.

    Returns:
        Their current values, scrubbed like an audit entry (credentials masked).
    """
    out = {k: getattr(app, k) for k, v in FRESH.items() if getattr(app, k) != v}
    out |= {k: v for k, v in (app.defaults or {}).items() if k not in KEPT_DEFAULTS}
    masked = {k: "***" if v and any(h in k for h in SECRETISH) else v for k, v in out.items()}
    return scrub(masked)


def own_write(path: str, params: Mapping[str, Any]) -> bool:
    """Whether a change on a system application stays within its own kinds of resources.

    Args:
        path: The route's path template, without the base path.
        params: The request's path parameters.

    Returns:
        True for a change of the assistant's own resources, or a route outside the admin API.
    """
    if not path.startswith("/v1/admin/"):
        return True
    if path not in OWN_WRITES:
        return False
    need = OWN_WRITES[path] or {}
    return all(str(params.get(k)) == v for k, v in need.items())


SECRETISH = ("secret", "token", "encrypted", "hash", "credential", "state")
"""Columns never exported: secrets (encrypted or not) and a channel's delivery state."""


def _export(row: Any, *, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    """A stored row as an owner may keep it: its columns, never an identifier, a time or a secret.

    Returns:
        The columns, scrubbed like an audit entry.
    """
    out: dict[str, Any] = {}
    for column in row.__table__.columns:
        key = column.key
        if key in {"id", "application_id", *skip} or key.endswith("_at"):
            continue
        if any(h in key for h in SECRETISH):
            continue
        out[key] = getattr(row, key)
    return scrub(out)


def _item(kind: str, name: str, action: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"kind": kind, "name": name, "action": action, **({"item": item} if item else {})}


async def plan(svc: Services, app_id: str) -> list[dict[str, Any]]:
    """Every item a reset of the system application deletes or restores.

    Returns:
        Items: kind, name, action (``delete`` or ``restore``) and, for a deleted one, ``item``,
        its definition without secrets.
    """
    store = svc.store
    shipped_skills = {s.name for s in skills_in(shipped.SHIPPED.parent / "skills")}
    items: list[dict[str, Any]] = []
    for agent in await store.agents.active_all(app_id):
        if agent.name == shipped.AGENT:
            items.append(_item("agent", agent.name, "restore"))
        else:
            items.append(_item("agent", agent.name, "delete", dict(agent.definition)))
    for server in await store.mcp_servers.all(app_id):
        if server.name == shipped.MCP_SERVER:
            items.append(_item("mcp_server", server.name, "restore"))
        else:
            kept = _export(
                server, skip=("tools", "checked_at", "status", "latency_ms", "last_error")
            )
            items.append(_item("mcp_server", server.name, "delete", kept))
    for skill in await store.skills.all(app_id):
        if skill.name in shipped_skills:
            items.append(_item("skill", skill.name, "restore"))
        else:
            items.append(_item("skill", skill.name, "delete", _export(skill)))
    latest = {p.name: p for p in await store.prompts.all(app_id)}
    items.extend(
        _item("prompt", prompt.name, "delete", _export(prompt)) for prompt in latest.values()
    )
    async with svc.db.session() as s:
        q = sa.select(m.EvalCase).where(m.EvalCase.application_id == app_id)
        cases = (await s.execute(q.order_by(m.EvalCase.agent_name, m.EvalCase.name))).scalars()
        for case in cases.all():
            name = f"{case.agent_name}/{case.name}"
            items.append(_item("eval_case", name, "delete", _export(case)))
    items.extend(
        _item("workflow", flow.name, "delete", dict(flow.definition))
        for flow in await store.workflows.active_all(app_id)
    )
    items.extend(
        _item("knowledge", base.name, "delete", _export(base))
        for base in await store.knowledge.bases(app_id)
    )
    for channel in await store.channels.all(app_id):
        name = f"{channel.kind}/{channel.name}"
        items.append(_item("channel", name, "delete", _export(channel)))
    items.extend(
        _item("connection_app", oauth.provider, "delete", _export(oauth))
        for oauth in await store.connections.apps(app_id)
    )
    items.extend(
        _item("capability_credential", cred.provider, "delete", _export(cred))
        for cred in await store.capability_credentials.all(app_id)
    )
    for key in await store.applications.keys(app_id):
        if key.revoked_at is None:
            kept = {"kind": key.kind, "prefix": key.prefix, "label": key.label}
            items.append(_item("key", key.id, "delete", kept))
    app = await store.applications.get(app_id)
    items += [
        _item("settings", "application", "restore", _settings_changed(app) if app else None),
        _item("settings", "grants", "restore"),
        _item("settings", "security", "restore"),
    ]
    return items


async def reset(svc: Services, app_id: str, model: str | None) -> list[dict[str, Any]]:
    """Delete what ``plan`` lists and apply the shipped definition again (locked).

    Returns:
        The items of the plan that was carried out.
    """
    store = svc.store
    items = await plan(svc, app_id)
    for i in (i for i in items if i["action"] == "delete"):
        kind, name = i["kind"], i["name"]
        if kind == "prompt":
            await store.prompts.delete(app_id, name)
        elif kind == "eval_case":
            agent, case = name.split("/", 1)
            await store.evals.delete_case(app_id, agent, case)
        elif kind == "workflow":
            await store.workflows.deactivate(app_id, name)
        elif kind == "knowledge":
            base = await store.knowledge.base(app_id, name)
            if base is not None:
                await store.knowledge.delete_base(base)
        elif kind == "channel":
            channel_kind, channel = name.split("/", 1)
            await store.channels.delete(app_id, channel_kind, channel)
        elif kind == "connection_app":
            await store.connections.delete_provider(app_id, name)
            await store.connections.delete_app(app_id, name)
        elif kind == "capability_credential":
            await store.capability_credentials.delete(app_id, name)
    # Every setting back to a new application's, keeping the activation and the measured tries;
    # the shipped definition then writes its own.
    app = await store.applications.get(app_id)
    kept = {k: v for k, v in ((app.defaults if app else None) or {}).items() if k in KEPT_DEFAULTS}
    await store.applications.update(app_id, **FRESH, defaults=kept)
    # Agents, MCP servers, skills and keys the definition does not ship go with it.
    await shipped.apply_shipped(svc.store, svc.keyring, svc.settings, model=model)
    return items
