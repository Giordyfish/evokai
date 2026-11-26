"""The facts a compliance pack is written from: configuration and activity counts, never people.

``collect`` reads the stored configuration of one application (agents and their versions,
models, MCP servers and connectors, skills, knowledge bases, workflows, grants, the security
policy, retention, residency), the deployment's settings that matter to it (region, embeddings,
guardrails, voice, code sandbox, observability, single sign-on) and recent activity as counts
(turns, server runs, security events by control, red team and eval scores). Nothing that names or
identifies a person is read: no subject, message, title, e-mail or audit actor. The audit log
contributes the action, its target and the kind of actor only; subject requests are left out.

The result is a JSON document: it is what a snapshot stores, and the packs are pure functions of
it (``evokai.compliance.packs``), so a snapshot renders the same in any language later.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import sqlalchemy as sa

from evokai import policies
from evokai.agents.definitions import AgentDef
from evokai.agents.skills import kind_of
from evokai.core.caps import caps_of, currency_of
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.grants import grants_of
from evokai.guard import identifiers
from evokai.guard.config import CONTROLS
from evokai.llm.catalog import normalize
from evokai.privacy import model_geography
from evokai.protection import report as protection_report
from evokai.sessions.limits import caps_set

if TYPE_CHECKING:
    from evokai.services import Services

SCHEMA = 1
ACTIVITY_DAYS = 30
TREND_RUNS = 6
CHANGE_PREFIXES = (
    "agent.",
    "application.",
    "grants.",
    "key.create",
    "key.revoke",
    "key.rotate",
    "knowledge.put",
    "knowledge.delete",
    "mcp_server.",
    "prompt.",
    "security.",
    "skill.",
    "voice.",
    "workflow.put",
    "workflow.delete",
    "sso.",
    "compliance.",
)
"""Audit actions that change the system (runs, sign-ins and subject requests are not changes)."""
MAX_CHANGES = 200
EU_PROVIDERS_LOCAL = ("ollama", "test", "function")
"""Providers that never leave the deployment (self-hosted or test models)."""


def _host(url: str | None) -> str:
    try:
        return urlsplit(url or "").hostname or ""
    except ValueError:
        return ""


def _step_kinds(steps: list[Any]) -> list[str]:
    """The kinds of a workflow's steps, the branches of parallel steps included.

    Returns:
        One kind per step.
    """
    out: list[str] = []
    for x in steps:
        if not isinstance(x, dict):
            continue
        out.append(str(x.get("kind") or ""))
        for b in x.get("branches") or []:
            if isinstance(b, dict):
                out += _step_kinds(b.get("steps") or [])
    return out


def _iso(value: Any) -> str | None:
    # Seconds only: a fraction of a second reads like a phone number to identifier scanners.
    return value.isoformat(timespec="seconds") if value is not None else None


def _provider(model: str) -> str:
    return normalize(model).partition(":")[0]


def _mutating(tool: dict[str, Any]) -> bool:
    if tool.get("destructive"):
        return True
    return not tool.get("readOnly")


async def _counts(svc: Services, app_id: str) -> dict[str, Any]:
    since = utcnow() - timedelta(days=ACTIVITY_DAYS)
    async with svc.db.session() as s:
        t = m.Trace
        rows = (
            await s.execute(
                sa
                .select(t.kind, sa.func.count())
                .where(t.application_id == app_id, t.started_at >= since)
                .group_by(t.kind)
            )
        ).all()
        traces = {str(k): int(n) for k, n in rows}
        e = m.SecurityEvent
        rows = (
            await s.execute(
                sa
                .select(e.control, e.action, sa.func.count())
                .where(e.application_id == app_id, e.created_at >= since)
                .group_by(e.control, e.action)
            )
        ).all()
        events: dict[str, dict[str, int]] = {}
        for control, action, n in rows:
            events.setdefault(str(control), {})[str(action)] = int(n)
        sessions = int(
            (
                await s.execute(
                    sa.select(sa.func.count()).where(
                        m.Session.application_id == app_id, m.Session.created_at >= since
                    )
                )
            ).scalar_one()
        )
        consented = int(
            (
                await s.execute(
                    sa.select(sa.func.count()).where(
                        m.Session.application_id == app_id,
                        m.Session.created_at >= since,
                        m.Session.consent.is_not(None),
                    )
                )
            ).scalar_one()
        )
        code = int(
            (
                await s.execute(
                    sa.select(sa.func.count()).where(
                        m.UsageRecord.application_id == app_id,
                        m.UsageRecord.created_at >= since,
                        m.UsageRecord.kind == "code",
                    )
                )
            ).scalar_one()
        )
    return {
        "days": ACTIVITY_DAYS,
        "since": since.date().isoformat(),
        "turns": traces.get("turn", 0),
        "serverRuns": traces.get("run", 0),
        "evalTurns": traces.get("eval", 0),
        "workflowRuns": traces.get("workflow", 0),
        "conversations": sessions,
        "conversationsWithConsent": consented,
        "codeRuns": code,
        "securityEvents": events,
    }


def _scores(runs: list[m.EvalRun], key: str) -> dict[str, Any] | None:
    done = [r for r in runs if r.status == "done"]
    if not done:
        return None
    trend = []
    for r in reversed(done[:TREND_RUNS]):
        v = ((r.summary or {}).get("versions") or {}).get(r.version) or {}
        if v.get(key) is not None:
            trend.append({"version": r.version, "at": _iso(r.started_at), "value": v[key]})
    if not trend:
        return None
    last = done[0]
    v = ((last.summary or {}).get("versions") or {}).get(last.version) or {}
    out: dict[str, Any] = {
        "value": v.get(key),
        "version": last.version,
        "at": _iso(last.started_at),
        "trend": trend,
    }
    if key == "score":
        out["byCategory"] = {
            c: x.get("score") for c, x in (v.get("byCategory") or {}).items() if isinstance(x, dict)
        }
    else:
        out["cases"] = (last.summary or {}).get("cases")
    return out


async def _agents(svc: Services, app: m.Application) -> list[dict[str, Any]]:
    out = []
    for rec in await svc.store.agents.active_all(app.id):
        d = AgentDef.model_validate({**rec.definition, "version": rec.version})
        policy = svc.guard.policy(app, d)
        versions = await svc.store.agents.versions(app.id, d.name)
        redteam = await svc.store.evals.runs(app.id, d.name, TREND_RUNS * 2, suite="redteam")
        evals = await svc.store.evals.runs(app.id, d.name, TREND_RUNS * 2, suite="cases")
        ct = d.client_tools
        out.append({
            "name": d.name,
            "title": d.title,
            "version": rec.version,
            "savedAt": _iso(rec.created_at),
            "versions": len(versions),
            "model": normalize(d.model),
            "models": [normalize(x) for x in d.models],
            "fallbacks": [normalize(x) for x in d.fallbacks],
            "channels": list(d.channels),
            "a2a": d.a2a,
            "mcpServers": list(d.mcp_servers),
            "skills": d.skills,
            "knowledgeBases": list(d.knowledge.bases) if d.knowledge else [],
            "memoryEpisodes": bool(d.memory and d.memory.episodes),
            "code": bool(d.code and d.code.enabled),
            "handoffTo": list(d.handoff_to),
            "structuredOutput": d.output_schema is not None,
            "maxToolCalls": d.max_tool_calls_per_turn,
            "promptCache": d.prompt_cache,
            "clientTools": {
                "navigate": ct.navigate.enabled,
                "chart": ct.chart,
                "diagram": ct.diagram,
                "ask": ct.ask,
                "memory": ct.memory,
            },
            "security": {c.id: policy.mode(c.id) for c in CONTROLS},
            "ownPolicy": d.security is not None,
            "redteam": _scores(redteam, "score"),
            "evals": _scores(evals, "passRate"),
        })
    return out


def _models(agents: list[dict[str, Any]], region: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for a in agents:
        for role, names in (
            ("primary", [a["model"], *a["models"]]),
            ("fallback", a["fallbacks"]),
        ):
            for n in names:
                row = seen.setdefault(
                    n,
                    {
                        "id": n,
                        "provider": _provider(n),
                        "geography": model_geography(n, region),
                        "region": region if _provider(n) == "bedrock" else None,
                        "roles": [],
                        "usedBy": [],
                    },
                )
                if role not in row["roles"]:
                    row["roles"].append(role)
                if a["name"] not in row["usedBy"]:
                    row["usedBy"].append(a["name"])
    return sorted(seen.values(), key=lambda r: r["id"])


async def collect(svc: Services, app: m.Application, profile: dict[str, Any]) -> dict[str, Any]:
    """Everything the packs need about one application, as of now.

    Args:
        svc: The services.
        app: The application.
        profile: Its stored compliance profile (``evokai.compliance.profile``).

    Returns:
        The facts (JSON-able).
    """
    st = svc.settings
    d = app.defaults or {}
    grants = await grants_of(svc.store, app)
    agents = await _agents(svc, app)
    region = st.aws_region
    servers = []
    for s in await svc.store.mcp_servers.all(app.id):
        tools = [t for t in s.tools or [] if isinstance(t, dict)]
        servers.append({
            "name": s.name,
            "host": "" if s.connector else _host(s.url),
            "auth": s.auth,
            "connector": s.connector,
            "writes": bool((s.config or {}).get("writes")),
            "granted": grants.mcp(s.name),
            "tools": [
                {
                    "name": str(t.get("name") or ""),
                    "mutating": _mutating(t),
                    "granted": grants.tool(s.name, str(t.get("name") or "")),
                }
                for t in tools
            ],
            "status": s.status,
        })
    bases = [
        {
            "name": b.name,
            "embeddingModel": b.embedding_model,
            "retentionDays": b.retention_days,
            "restricted": bool(b.access),
            "sources": sorted({
                str(x.get("kind") or x.get("type") or "url")
                for x in b.sources or []
                if isinstance(x, dict)
            }),
        }
        for b in await svc.store.knowledge.bases(app.id)
    ]
    workflows = []
    for w in await svc.store.workflows.active_all(app.id):
        kinds = _step_kinds((w.definition or {}).get("steps") or [])
        triggers = (w.definition or {}).get("triggers") or []
        workflows.append({
            "name": w.name,
            "version": w.version,
            "steps": len(kinds),
            "approvals": kinds.count("approval"),
            "toolSteps": kinds.count("tool"),
            "agentSteps": kinds.count("agent"),
            "triggers": sorted({
                str(t.get("kind") or t.get("type") or "") for t in triggers if isinstance(t, dict)
            }),
            "retentionDays": (w.definition or {}).get("retention_days"),
        })
    changes = []
    for e in await svc.store.audit.query(app.id, limit=MAX_CHANGES * 3):
        if not e.action.startswith(CHANGE_PREFIXES):
            continue
        changes.append({
            "id": e.id,
            "at": _iso(e.at),
            "action": e.action,
            "target": e.target[:120],
            "actorKind": e.actor_kind,
            "role": e.role,
        })
        if len(changes) >= MAX_CHANGES:
            break
    from evokai import grants as g
    from evokai.identity.sso import configured

    counts = await svc.store.connections.counts(app.id)
    connections = [
        {
            "provider": p,
            "granted": grants.connection(p),
            "families": [f for f in g.CONNECTION_FAMILIES_OF.get(p, ()) if grants.connection(p, f)],
            "people": sum((counts.get(p) or {}).values()),
        }
        for p in g.CONNECTION_PROVIDERS
    ]
    channels = [
        {
            "kind": c.kind,
            "name": c.name,
            "agent": c.agent,
            "enabled": bool(c.enabled) and grants.channel(c.kind),
            "status": c.status,
        }
        for c in await svc.store.channels.all(app.id)
    ]

    try:
        sso, sso_source = await configured(svc.store, st, svc.keyring)
    except Exception:
        sso, sso_source = None, None
    code_status = svc.code.status() if svc.code is not None else {"enabled": False}
    voice_server = bool(st.voice_enabled) and grants.voice("server")
    caps = caps_set(app)
    return {
        "schema": SCHEMA,
        "collectedAt": _iso(utcnow()),
        "profile": profile,
        "application": {
            "id": app.id,
            "slug": app.slug,
            "name": app.name,
            "createdAt": _iso(app.created_at),
            "storageMode": app.storage_mode,
            "conversationTtlDays": app.conversation_ttl_days,
            "auditTtlDays": app.audit_ttl_days,
            "securityEventTtlDays": app.security_event_ttl_days,
            "residency": app.residency,
            "allowedModels": list(app.allowed_models or []),
            "allowedOrigins": [_host(o) or o for o in app.allowed_origins or []],
            "locale": d.get("locale", "en"),
            "voiceEngine": d.get("voice_engine", "browser"),
            "tracingContent": d.get("tracing_content", "masked"),
            "memoryMode": d.get("memory_mode", "on"),
            "memoryTtlDays": d.get("memory_ttl_days"),
            "redteamGate": d.get("redteam_gate", "off"),
            "redteamMinScore": d.get("redteam_min_score", 80),
            "redteamOnSave": d.get("redteam_on_save", True),
            "evalOnSave": d.get("eval_on_save", True),
            "costAlerts": bool(d.get("cost_alert_webhook")),
            "monthlyCapSet": caps["monthly"],
            "weeklyCapSet": caps["weekly"],
            "dailyCapSet": caps["daily"],
            "subjectCapsSet": caps["subjectMonthly"]
            or caps["subjectWeekly"]
            or caps["subjectDaily"],
            "caps": [
                {"scope": c.scope, "period": c.period, "resets": c.resets_label()}
                for c in caps_of(app)
            ],
            "capWindows": "calendar",
            "capTimezone": d.get("timezone") or "UTC",
            "currency": currency_of(app),
        },
        "deployment": {
            "awsRegion": region,
            "modelAllow": list(st.model_allow),
            "embeddingModel": st.embedding_model,
            "memoryEmbeddingModel": st.memory_embedding_model,
            "voiceEnabled": bool(st.voice_enabled),
            "voiceStt": st.voice_stt if st.voice_enabled else None,
            "voiceTts": st.voice_tts if st.voice_enabled else None,
            "otelEnabled": bool(st.otel_enabled),
            "systemPolicy": policies.system().version > 0,
            "sharedState": bool(st.redis_url),
            "guardEnabled": bool(st.guard_enabled),
            "guardrail": bool(st.guardrail_id),
            "guardrailRegion": (st.guardrail_region or region) if st.guardrail_id else None,
            "databaseTls": bool(st.database_ssl) or "sslmode=verify" in st.database_url,
            "databaseKind": st.database_url.split(":", 1)[0].split("+", 1)[0],
            "sso": {"configured": sso is not None, "source": sso_source},
            "knowledgeFetchAllow": list(st.knowledge_fetch_allow),
            "auditTtlDays": st.audit_ttl_days,
            "securityEventTtlDays": st.security_event_ttl_days,
            "sessionTokenMaxTtl": st.session_token_max_ttl,
            "code": code_status,
            "production": bool(st.production),
            "build": st.build_commit or None,
        },
        "grants": {
            "browser": grants.channel("browser"),
            "server": grants.channel("server"),
            "a2a": grants.channel("a2a"),
            "knowledge": grants.knowledge(),
            "workflows": grants.workflow(),
            "autonomous": grants.workflow("autonomous"),
            "code": grants.code(),
            "voiceBrowser": grants.voice("browser"),
            "voiceServer": voice_server,
            "storageExternal": grants.storage("external"),
            "memory": grants.client("memory"),
            "email": grants.channel("email"),
            "teams": grants.channel("teams"),
            "slack": grants.channel("slack"),
        },
        "connections": connections,
        "channels": channels,
        "agents": agents,
        "models": _models(agents, region),
        "mcpServers": servers,
        "skills": sorted(s.name for s in await svc.store.skills.all(app.id)),
        "skillScripts": sorted(
            name
            for name, files in (await svc.store.skills.file_index(app.id)).items()
            if any(kind_of(p) == "script" for p, _, _ in files)
        ),
        "knowledgeBases": bases,
        "workflows": workflows,
        "security": {
            "applicationPolicy": app.security is not None,
            "controls": [
                {"id": c.id, "owasp": list(c.owasp), "default": c.default} for c in CONTROLS
            ],
            "piiTypes": list(((app.security or {}).get("pii") or {}).get("types") or []) or None,
            "piiDeclared": [i.name for i in identifiers.of_app(app)],
            "classifier": ((app.security or {}).get("injection") or {}).get("classifier")
            or st.guard_classifier
            or "none",
            "egressAllow": bool(((app.security or {}).get("tools") or {}).get("egress_allow")),
        },
        "activity": await _counts(svc, app.id),
        "changes": changes,
        "dataProtection": await protection_report(svc, app),
    }
