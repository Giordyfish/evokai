"""Capability grants of an application: the resolved view for the console, and changes.

The view lists every item an application could be granted (the models the deployment allows,
its MCP servers and the tools they listed, its skills, the client tools, the voice engines with
their speech-to-text engines and voices, the storage modes and the channels), each with its
default, its override if any, and its effective value.
"""

from __future__ import annotations

import fnmatch
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai import audit
from evokai import grants as g
from evokai.api.deps import admin_app, services
from evokai.audio.choice import stt_engines
from evokai.core.errors import NotFound
from evokai.db import models as m
from evokai.grants import Grants
from evokai.identity.models import AppPrincipal
from evokai.llm.catalog import normalize
from evokai.llm.prices import known_models, price_of
from evokai.privacy import residency_allows
from evokai.protection import protection
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


def _item(grants: Grants, kind: str, name: str, value: Any, **extra: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "value": value,
        "default": grants.default(kind, name),
        "override": grants.override(kind, name),
        **extra,
    }


def capability_items(
    grants: Grants,
    agents: list[Any],
    deployment: list[str],
    stored: dict[str, dict[str, str]] | None = None,
    flavour: str | None = None,
) -> list[dict[str, Any]]:
    """The catalog as grant items, with what keeps each one from working here.

    Imports no capability: whether one is installed comes from package metadata, its description
    from the catalog's own text.

    Returns:
        One item per capability: ``group``, ``offered`` (by the deployment), ``installed``,
        ``credentials`` (the provider whose keys the application sets), ``missingCredentials``,
        ``host``, ``wiring`` and the agents that turn it on.
    """
    from evokai import capabilities as caps
    from evokai.capabilities.catalog import ENTRIES
    from evokai.capabilities.credentials import PERSONAL, provider_of

    out = []
    for e in ENTRIES:
        installed = caps.installed(e.id)
        doc = e.description
        out.append(
            _item(
                grants,
                g.CAPABILITY,
                e.id,
                grants.capability(e.id),
                label=e.id,
                description=doc[:300],
                group=e.group,
                offered=caps.offered(e.id, deployment),
                installed=installed,
                notInstalled=caps.not_installed_reason(e.id, flavour) if not installed else None,
                credentials=provider_of(e.id),
                actsAs="person" if e.id in PERSONAL else None,
                missingCredentials=caps.missing_credentials(e.id, stored=stored),
                host=e.host,
                wiring=e.wiring or None,
                usedBy=sorted(
                    a.name
                    for a in agents
                    if ((a.definition.get("capabilities") or {}).get(e.id) or {}).get("enabled")
                ),
            )
        )
    return out


def _flag(grants: Grants, kind: str, name: str) -> bool:
    ov = grants.override(kind, name)
    return bool(grants.default(kind, name) if ov is None else ov)


def _app_allows(app: m.Application, model: str) -> bool:
    patterns = app.allowed_models or []
    return not patterns or any(
        fnmatch.fnmatchcase(normalize(model), normalize(p)) for p in patterns
    )


async def grant_view(svc: Services, app: m.Application) -> dict[str, Any]:
    """Everything the application could be granted, resolved.

    Returns:
        groups (models, mcp, skills, client, voice, storage, channels), summary, overrides.
    """
    rows = await svc.store.grants.all(app.id)
    grants = Grants.of(rows, app.storage_mode)
    agents = await svc.store.agents.active_all(app.id)
    servers = await svc.store.mcp_servers.all(app.id)
    skills = await svc.store.skills.all(app.id)
    voices = await svc.store.voices.all()

    # Models: the known ones the deployment allows, the agents' own, and every override.
    used: dict[str, list[str]] = {}
    for a in agents:
        d = a.definition
        for model in (d.get("model"), *(d.get("fallbacks") or []), *(d.get("models") or [])):
            if model:
                used.setdefault(normalize(model), []).append(a.name)
    names = set(known_models()) | set(used)
    names = {n for n in names if svc.catalog.allowed(n)}
    names |= {r.name for r in rows if r.kind == g.MODEL}
    models = [
        _item(
            grants,
            g.MODEL,
            n,
            grants.model(n) if not any(c in n for c in "*?[") else grants.override(g.MODEL, n),
            pattern=any(c in n for c in "*?["),
            allowed=svc.catalog.allowed(n)
            and _app_allows(app, n)
            and residency_allows(protection(app), n, svc.settings.aws_region),
            usedBy=sorted(set(used.get(n, []))),
            # Where the model's list price comes from; ``fallback``: nobody prices it, it is
            # charged like an expensive model (never an amount: the console shows shares only).
            rateSource=None if any(c in n for c in "*?[") else price_of(n).source,
        )
        for n in sorted(names)
    ]

    # MCP servers and the tools they listed last (plus overrides of tools not listed now).
    tool_overrides: dict[str, list[str]] = {}
    for r in rows:
        if r.kind == g.TOOL:
            server, _, tool = r.name.partition("/")
            tool_overrides.setdefault(server, []).append(tool)
    mcp = []
    for s in servers:
        listed = {t["name"]: t for t in s.tools or []}
        tools = sorted(set(listed) | set(tool_overrides.get(s.name, [])))
        mcp.append(
            _item(
                grants,
                g.MCP,
                s.name,
                _flag(grants, g.MCP, s.name),
                auth=s.auth,
                status=s.status,
                listed=s.tools is not None,
                usedBy=sorted(
                    a.name for a in agents if s.name in (a.definition.get("mcp_servers") or [])
                ),
                children=[
                    _item(
                        grants,
                        g.TOOL,
                        f"{s.name}/{t}",
                        _flag(grants, g.TOOL, f"{s.name}/{t}"),
                        label=t,
                        mutating=bool((listed.get(t) or {}).get("mutating")),
                        description=(listed.get(t) or {}).get("description", ""),
                        listed=t in listed,
                    )
                    for t in tools
                ],
            )
        )
    known_servers = {s.name for s in servers}
    mcp += [
        _item(grants, g.MCP, r.name, bool(r.value), auth=None, status=None, listed=False,
              usedBy=[], children=[], missing=True)
        for r in rows
        if r.kind == g.MCP and r.name not in known_servers
    ]  # fmt: skip

    skill_items = [
        _item(
            grants,
            g.SKILL,
            s.name,
            _flag(grants, g.SKILL, s.name),
            description=s.description,
            usedBy=sorted(
                a.name
                for a in agents
                if a.definition.get("skills") is None or s.name in a.definition.get("skills", [])
            ),
        )
        for s in skills
    ]
    known_skills = {s.name for s in skills}
    skill_items += [
        _item(grants, g.SKILL, r.name, bool(r.value), description="", usedBy=[], missing=True)
        for r in rows
        if r.kind == g.SKILL and r.name not in known_skills
    ]

    client = [_item(grants, g.CLIENT, n, grants.client(n)) for n in g.CLIENT_TOOLS]
    # Custom client tools: session-declared ones as a whole, each one an agent declares, and the
    # names or patterns an override sets.
    declared: dict[str, list[str]] = {}
    for a in agents:
        for t in (a.definition.get("client_tools") or {}).get("custom") or []:
            declared.setdefault(str(t.get("name")), []).append(a.name)
    custom_names = sorted(
        set(declared)
        | {r.name for r in rows if r.kind == g.CLIENT and r.name not in g.CLIENT_TOOLS}
        - {g.CLIENT_SESSION}
    )
    client_custom = [
        _item(grants, g.CLIENT, g.CLIENT_SESSION, _flag(grants, g.CLIENT, g.CLIENT_SESSION)),
        *(
            _item(
                grants,
                g.CLIENT,
                n,
                grants.client_tool(n, static=n in declared),
                usedBy=sorted(declared.get(n, [])),
                pattern=any(c in n for c in "*?"),
            )
            for n in custom_names
        ),
    ]

    # Voice: browser speech with its on-device voices, server speech with its engines and voices.
    def voice_children(engine: str) -> list[dict[str, Any]]:
        local = engine == "browser"
        catalog = [
            v for v in voices if ((v.region or "").lower() in g.BROWSER_VOICE_REGIONS) == local
        ]
        ids = {v.voice_id for v in catalog}
        out = [
            _item(
                grants,
                g.TTS,
                f"{engine}/{v.voice_id}",
                _flag(grants, g.TTS, f"{engine}/{v.voice_id}"),
                label=v.name,
                language=v.language,
                provider=v.provider,
                region=v.region,
            )
            for v in catalog
        ]
        out += [
            _item(grants, g.TTS, r.name, bool(r.value), label=r.name.partition("/")[2],
                  language=None, provider=None, region=None, missing=True)
            for r in rows
            if r.kind == g.TTS and r.name.startswith(f"{engine}/")
            and r.name.partition("/")[2] not in ids
        ]  # fmt: skip
        return out

    offered = set(stt_engines(svc.settings))
    stt_names = sorted(offered | {r.name for r in rows if r.kind == g.STT})
    stt = [
        _item(grants, g.STT, n, _flag(grants, g.STT, n), label=n, configured=n in offered)
        for n in stt_names
    ]
    voice = [
        _item(
            grants,
            g.VOICE,
            "browser",
            grants.voice("browser"),
            children=voice_children("browser"),
        ),
        _item(
            grants,
            g.VOICE,
            "server",
            grants.voice("server"),
            enabled=svc.settings.voice_enabled,
            children=stt + voice_children("server"),
        ),
    ]
    bases = await svc.store.knowledge.bases(app.id)
    base_names = {b.name for b in bases}
    knowledge = [
        _item(
            grants,
            g.KNOWLEDGE,
            g.KNOWLEDGE_ALL,
            grants.knowledge(),
            children=[
                _item(
                    grants,
                    g.KNOWLEDGE,
                    b.name,
                    _flag(grants, g.KNOWLEDGE, b.name),
                    label=b.title or b.name,
                    description=b.description,
                    usedBy=sorted(
                        a.name
                        for a in agents
                        if b.name in ((a.definition.get("knowledge") or {}).get("bases") or [])
                    ),
                )
                for b in bases
            ]
            + [
                _item(
                    grants,
                    g.KNOWLEDGE,
                    r.name,
                    bool(r.value),
                    label=r.name,
                    description="",
                    usedBy=[],
                    missing=True,
                )
                for r in rows
                if r.kind == g.KNOWLEDGE and r.name != g.KNOWLEDGE_ALL and r.name not in base_names
            ],
        )
    ]
    storage = [_item(grants, g.STORAGE, n, grants.storage(n)) for n in g.STORAGE_MODES]
    channels = [_item(grants, g.CHANNEL, n, grants.channel(n)) for n in g.CHANNELS]
    workflows = [
        _item(
            grants,
            g.WORKFLOW,
            "*",
            grants.workflow(),
            children=[
                _item(grants, g.WORKFLOW, "autonomous", _flag(grants, g.WORKFLOW, "autonomous"))
            ],
        )
    ]

    code_status = svc.code.status() if svc.code is not None else {"enabled": False}
    code = [
        _item(
            grants,
            g.CODE,
            "*",
            grants.code(),
            enabled=bool(code_status.get("enabled")),
            region=code_status.get("region"),
            usedBy=sorted(
                a.name for a in agents if (a.definition.get("code") or {}).get("enabled") is True
            ),
        )
    ]
    builtin = [
        _item(
            grants,
            g.BUILTIN,
            kind,
            grants.builtin(kind),
            usedBy=sorted(
                a.name
                for a in agents
                if any(t.get("kind") == kind for t in a.definition.get("builtin_tools") or [])
            ),
        )
        for kind in g.BUILTIN_KINDS
    ]
    openai = [
        _item(
            grants,
            g.OPENAI,
            "confirm",
            grants.openai("confirm"),
            usedBy=sorted(a.name for a in agents if a.definition.get("openai_confirm") is True),
        )
    ]
    connections = [
        _item(
            grants,
            g.CONNECTION,
            p,
            _flag(grants, g.CONNECTION, p),
            children=[
                _item(
                    grants,
                    g.CONNECTION,
                    f"{p}/{f}",
                    _flag(grants, g.CONNECTION, f"{p}/{f}"),
                    label=f,
                )
                for f in g.CONNECTION_FAMILIES_OF.get(p, ())
            ],
        )
        for p in g.CONNECTION_PROVIDERS
    ]

    from evokai.capabilities.credentials import stored_names

    capabilities = capability_items(
        grants,
        agents,
        svc.settings.capabilities,
        stored_names(await svc.store.capability_credentials.all(app.id)),
        svc.settings.image_flavour,
    )
    # Prompt modules: the ones an agent lets a session change, and every override.
    module_users: dict[str, list[str]] = {}
    for a in agents:
        for ref in a.definition.get("modules") or []:
            if ref.get("session"):
                module_users.setdefault(str(ref.get("name")), []).append(a.name)
    module_names = set(module_users) | {r.name for r in rows if r.kind == g.MODULE}
    modules = [
        _item(grants, g.MODULE, n, grants.module(n), usedBy=sorted(module_users.get(n, [])))
        for n in sorted(module_names)
    ]
    groups = [
        {"id": "models", "items": models},
        {"id": "mcp", "items": mcp},
        {"id": "skills", "items": skill_items},
        {"id": "client", "items": client},
        {"id": "clientCustom", "items": client_custom},
        {"id": "voice", "items": voice},
        {"id": "storage", "items": storage},
        {"id": "channels", "items": channels},
        {"id": "knowledge", "items": knowledge},
        {"id": "workflows", "items": workflows},
        {"id": "code", "items": code},
        {"id": "openai", "items": openai},
        {"id": "builtin", "items": builtin},
        {"id": "connections", "items": connections},
        {"id": "capabilities", "items": capabilities},
        {"id": "modules", "items": modules},
    ]
    return {
        "groups": groups,
        "summary": _summary(grants, models, mcp, skill_items, client, voice),
        "overrides": len(rows),
        "modelAllow": svc.settings.model_allow,
        "applicationModels": app.allowed_models or [],
        "storageMode": app.storage_mode,
    }


def _summary(
    grants: Grants,
    models: list[dict[str, Any]],
    mcp: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    client: list[dict[str, Any]],
    voice: list[dict[str, Any]],
) -> dict[str, Any]:
    concrete = [x for x in models if not x["pattern"] and x["allowed"]]
    on = [x for x in concrete if x["value"] != "off"]
    tools = [(s, t) for s in mcp for t in s["children"]]
    voices = [
        c for v in voice if v["value"] for c in v["children"] if c["kind"] == g.TTS and c["value"]
    ]
    return {
        "models": {
            "on": len(on),
            "total": len(concrete),
            "fallbackOnly": [x["name"] for x in on if x["value"] == "fallback"],
        },
        "mcpServers": {"on": sum(1 for s in mcp if s["value"]), "total": len(mcp)},
        "tools": {
            "on": sum(1 for s, t in tools if s["value"] and t["value"]),
            "total": len(tools),
        },
        "skills": {"on": sum(1 for s in skills if s["value"]), "total": len(skills)},
        "clientTools": {
            "on": sum(1 for c in client if c["value"]),
            "total": len(client),
            "off": [c["name"] for c in client if not c["value"]],
        },
        "voice": {
            "browser": grants.voice("browser"),
            "server": grants.voice("server"),
            "voices": len(voices),
        },
        "storage": [m for m in g.STORAGE_MODES if grants.storage(m)],
        "knowledge": grants.knowledge(),
        "serverRuns": grants.channel("server"),
        "browserSessions": grants.channel("browser"),
        "a2a": grants.channel("a2a"),
        "channels": [c for c in g.MESSAGING_CHANNELS if grants.channel(c)],
        "connections": [p for p in g.CONNECTION_PROVIDERS if grants.connection(p)],
    }


@router.get("/grants")
async def get_grants(svc: Svc, a: Admin) -> dict[str, Any]:
    """What the application may use: every item with its default, override and value.

    Returns:
        groups, summary, overrides (count), modelAllow, applicationModels, storageMode.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return await grant_view(svc, app)


class GrantChange(BaseModel):
    """Grant or revoke one item, or reset it to the default (``value: null``)."""

    kind: str = Field(max_length=16)
    """model, mcp, tool, skill, client, voice, stt, tts, storage, channel or knowledge."""
    name: str = Field(min_length=1, max_length=300)
    """The item: a model (``bedrock:eu.anthropic...`` or a pattern), a server, ``server/tool``,
    a skill, a client tool, ``browser``/``server``, an STT engine, ``browser/<voice>`` or
    ``server/<voice>``, ``evokai``/``external``, a knowledge base or ``*`` (knowledge as a
    whole)."""
    value: bool | str | None = None
    """True or false; for models ``any``, ``fallback`` or ``off``; null resets."""


class GrantChanges(BaseModel):
    """Changes applied together."""

    changes: list[GrantChange] = Field(min_length=1, max_length=500)


@router.patch("/grants")
async def patch_grants(body: GrantChanges, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Grant, revoke or reset items. Effective from the next turn.

    Returns:
        The resolved view, as ``GET /v1/admin/grants``.

    Raises:
        NotFound: The application is gone.
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    changes = []
    for c in body.changes:
        name = g.normalize_name(c.kind, c.name)
        value = None if c.value is None else g.check_value(c.kind, name, c.value)
        changes.append((c.kind, name, value))
    before = {(r.kind, r.name): r.value for r in await svc.store.grants.all(app.id)}
    await svc.store.grants.change(app.id, changes, a.key_id)
    await audit.record(
        request,
        a,
        "grants.change",
        target=", ".join(f"{k}:{n}" for k, n, _ in changes)[:300],
        before=[{"kind": k, "name": n, "value": before.get((k, n))} for k, n, _ in changes],
        after=[{"kind": k, "name": n, "value": v} for k, n, v in changes],
    )
    return await grant_view(svc, app)
