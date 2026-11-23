"""Admin API, with an application's admin key.

The application, its keys, agents, MCP servers, skills.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from evokai import audit
from evokai.agents import skills as sk
from evokai.agents.definitions import AgentDef
from evokai.agents.modules import (
    check_agent_modules,
    check_data,
    check_parameters_schema,
    check_prompt_in_use,
    render_legacy,
    render_template,
    render_text,
    validate_params,
)
from evokai.agents.prompts import long_date
from evokai.agents.templates import check_agent_templates, check_prompt, syntax_to_save
from evokai.api.deps import admin_app, services
from evokai.assistant.privacy import MAX_TTL_DAYS, lists_everyone, own_subject
from evokai.audio.speech.markup import MarkupDef
from evokai.connectors import URL_SCHEME as CONNECTOR_URL
from evokai.connectors import describe as describe_connectors
from evokai.connectors import validate as validate_connector
from evokai.core.caps import (
    LEGACY,
    CapsSpec,
    caps_of,
    check_currency,
    currency_of,
    legacy_conflicts,
)
from evokai.core.errors import AppError, BadRequest, Conflict, Forbidden, NotFound
from evokai.core.security import generate_key
from evokai.core.util import utcnow
from evokai.evals.redteam.service import gate_of
from evokai.grants import explicit_client_tools, grants_of
from evokai.i18n import locale_of, pack
from evokai.identity.models import AppPrincipal
from evokai.privacy import Residency, residency_allows
from evokai.protection import DataProtection, protection, stored_profile
from evokai.sandbox.skill_scripts import unavailable_reason
from evokai.services import Services

router = APIRouter(prefix="/v1/admin", tags=["admin"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]


class AppPatch(BaseModel):
    """Changeable application settings."""

    name: str | None = None
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    """The old monthly cap, in USD (applies while ``caps`` sets no application month)."""
    daily_cap_usd: float | None = Field(default=None, ge=0)
    """The old daily cap, in USD (null clears it)."""
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    """The currency of ``caps``, an ISO code (USD by default). Spend stays recorded in USD."""
    exchange_rate: float | None = Field(default=None, gt=0)
    """Units of ``currency`` per 1 USD (required when the currency is not USD)."""
    caps: CapsSpec | None = None
    """Caps per calendar window in ``currency``: ``application`` and ``subject``, each a list of
    ``{period: day|week|month, amount, resets}`` (a weekday for a week, a day of the month for a
    month), reset at 00:00 in ``timezone``. Replaces the stored caps; null removes them. A cap
    set here must not also be set by an old ``*_usd`` field in the same request; the old field of
    a cap it sets is cleared."""
    allowed_origins: list[str] | None = None
    allowed_models: list[str] | None = None
    storage_mode: Literal["evokai", "external"] | None = None
    conversation_ttl_days: int | None = Field(default=None, ge=1, le=3650)
    """Conversations disappear this many days after their last message; null = kept."""
    residency: Residency | None = None
    """Data residency: the region where data lives and the inference geographies (``eu``...) or
    model patterns allowed; models outside it are never called. null = no declaration."""
    data_protection: DataProtection | None = None
    """The data protection profile: ``residency`` (geographies, countries), ``jurisdiction``
    (``eu`` or countries), ``retention`` (``any``, ``no_training``, ``zero_retention``),
    ``browser`` (what the application lets the browser send to its vendor); every external
    processor must meet all that are set. null = none."""
    audit_ttl_days: int | None = Field(default=None, ge=1, le=3650)
    """Audit log entries are deleted this many days after the change; null = the deployment's
    default (``EVOKAI_AUDIT_TTL_DAYS``)."""
    security_event_ttl_days: int | None = Field(default=None, ge=1, le=3650)
    """Security events are deleted this many days after they happened; null = the deployment's
    default (``EVOKAI_SECURITY_EVENT_TTL_DAYS``)."""
    voice_engine: Literal["browser", "server"] | None = None
    """Default speech engine for sessions whose token does not choose one."""
    subject_monthly_usd: float | None = Field(default=None, ge=0)
    """Default monthly cap of each subject (a session token may override it)."""
    subject_daily_usd: float | None = Field(default=None, ge=0)
    """Default daily cap of each subject (a session token may override it)."""
    locale: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    """Language of Evokai's messages and speech rules (default en; packs in evokai/locales)."""
    timezone: str | None = Field(default=None, max_length=64)
    """IANA time zone for dates in prompts and for the caps' windows (UTC by default)."""
    messages: dict[str, str] | None = None
    """Overrides of Evokai's messages: limit.app, limit.appWeek, limit.appDay,
    limit.subjectMonth, limit.subjectWeek, limit.subjectDay ({appName} and {resets} are
    replaced)."""
    eval_monthly_usd: float | None = Field(default=None, ge=0)
    """The monthly budget of the application's evals (2 by default), apart from its caps."""
    eval_judge_model: str | None = Field(default=None, max_length=200)
    """The model that judges rubric checks; without one they are skipped."""
    eval_on_save: bool | None = None
    """Run an agent's eval cases when a new version is saved (default true)."""
    eval_credential: str | None = Field(default=None, max_length=8_000)
    """A test user's token for forward_user MCP servers in evals (kept encrypted; "" clears)."""
    memory_mode: Literal["off", "ask", "on"] | None = None
    """Long-term memory: ``off``, ``ask`` (only for people who said yes, asked once in the chat
    panel or by the application at token mint) or ``on`` (the default, unless a person said no)."""
    memory_ttl_days: int | None = Field(default=None, ge=1, le=3650)
    """Memories expire this many days after they were written or confirmed; null = kept."""
    redteam_gate: Literal["off", "monitor", "enforce"] | None = None
    """Publishing a version whose red team score is below ``redteam_min_score``: ``off`` (the
    default), ``monitor`` (published, with a warning) or ``enforce`` (held back until it scores
    at least that)."""
    redteam_min_score: float | None = Field(default=None, ge=0, le=100)
    """The least security score a version needs (80 by default)."""
    redteam_on_save: bool | None = None
    """Run the red team suite when a new version is saved (default true)."""
    cost_alert_thresholds: list[Annotated[int, Field(ge=1, le=200)]] | None = Field(
        default=None, max_length=6
    )
    """Shares of a cap (75, 90, 100 by default) a month-end forecast fires an alert at."""
    cost_alert_webhook: str | None = Field(default=None, max_length=500)
    """An https address on a domain of ``EVOKAI_ALERT_WEBHOOK_ALLOW`` that receives each alert
    (signed with ``cost_alert_secret``); "" clears it."""
    cost_alert_secret: str | None = Field(default=None, max_length=200)
    """The HMAC key of the webhook's signature (kept encrypted, never shown; "" clears it)."""
    tracing_content: Literal["none", "masked", "full"] | None = None
    """What content traces keep: ``none`` (timings and counts), ``masked`` (the default:
    identifiers masked, secrets removed) or ``full`` (secrets removed; read as ``masked`` while
    ``pii.history`` or ``pii.model`` is enforced). External storage keeps none, whatever this
    says."""
    markup: MarkupDef | None = None
    """Inline markup the agents may write: ``cue`` (``{wave}``, delivered as marks on the
    spoken segments) and ``speech`` (``[laughs]``, for expressive voices); null removes it."""


EVAL_KEYS = ("eval_monthly_usd", "eval_judge_model", "eval_on_save", "eval_credential")
"""Eval settings, kept in the application's defaults (see evokai.evals)."""
QUALITY_KEYS = (
    "memory_mode",
    "memory_ttl_days",
    "redteam_gate",
    "redteam_min_score",
    "redteam_on_save",
    "cost_alert_thresholds",
    "cost_alert_webhook",
    "cost_alert_secret",
)
"""Memory, red team and cost alert settings, kept in the application's defaults."""

DEFAULT_KEYS = frozenset({
    *EVAL_KEYS,
    *QUALITY_KEYS,
    "voice_engine",
    "subject_monthly_usd",
    "subject_daily_usd",
    "locale",
    "timezone",
    "messages",
    "tracing_content",
    "markup",
})
"""Application settings kept in its defaults."""


def _check_caps(body: AppPatch, fields: dict[str, Any], app: Any) -> None:
    """Validate the caps, the currency and the time zone of a change, and fold in the old fields.

    A cap set both in ``caps`` and by its old ``*_usd`` field in one request is refused; a cap
    ``caps`` sets clears its old field; an old field whose cap the stored ``caps`` set is refused
    (it would not apply).

    Raises:
        BadRequest: Either of these, a currency without its rate, or an unknown time zone.
    """
    old = {name: fields.get(name) for name in LEGACY.values()}
    problems = legacy_conflicts(body.caps, old)
    if "caps" not in fields:
        problems += [
            f"{p.split(' and ')[0]} would not apply: the stored caps set that cap"
            for p in legacy_conflicts(app.caps, old)
        ]
    if problems:
        raise BadRequest("; ".join(problems), code="caps_conflict")
    if "caps" in fields:
        fields["caps"] = body.caps.stored() if body.caps else None
        for scope in ("application", "subject"):
            for cap in getattr(body.caps, scope, None) or []:
                name = LEGACY.get((scope, cap.period))
                if name and name != "monthly_cap_usd":
                    fields[name] = None
    currency = fields.get("currency") or currency_of(app)
    rate = fields["exchange_rate"] if "exchange_rate" in fields else app.exchange_rate
    try:
        check_currency(currency, rate)
    except ValueError as e:
        raise BadRequest(str(e), code="currency") from None
    if fields.get("timezone"):
        try:
            ZoneInfo(fields["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise BadRequest("timezone is an IANA time zone, e.g. Europe/Rome.") from None


@router.get("/application")
async def get_application(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application.

    Returns:
        Its settings, and ``messageDefaults``: the limit messages of its locale (what applies
        where ``messages`` has no override).

    Raises:
        NotFound: Deleted meanwhile.
    """
    app = await svc.store.applications.get(a.application_id)
    if not app:
        raise NotFound()
    return {
        "id": app.id,
        "slug": app.slug,
        "name": app.name,
        "monthly_cap_usd": app.monthly_cap_usd,
        "daily_cap_usd": app.daily_cap_usd,
        "currency": currency_of(app),
        "exchange_rate": app.exchange_rate,
        "caps": app.caps,
        "effectiveCaps": [c.view() for c in caps_of(app)],
        "allowed_origins": app.allowed_origins,
        "allowed_models": app.allowed_models,
        "storage_mode": app.storage_mode,
        "conversation_ttl_days": app.conversation_ttl_days,
        "security_event_ttl_days": app.security_event_ttl_days,
        "audit_ttl_days": app.audit_ttl_days,
        "residency": app.residency,
        "data_protection": stored_profile(app.data_protection),
        "voice_engine": (app.defaults or {}).get("voice_engine", "browser"),
        "subject_monthly_usd": (app.defaults or {}).get("subject_monthly_usd"),
        "subject_daily_usd": (app.defaults or {}).get("subject_daily_usd"),
        "locale": (app.defaults or {}).get("locale", "en"),
        "timezone": (app.defaults or {}).get("timezone", "UTC"),
        "messages": (app.defaults or {}).get("messages") or {},
        "eval_monthly_usd": (app.defaults or {}).get("eval_monthly_usd"),
        "eval_judge_model": (app.defaults or {}).get("eval_judge_model"),
        "eval_on_save": (app.defaults or {}).get("eval_on_save", True),
        "hasEvalCredential": bool((app.defaults or {}).get("eval_credential")),
        "tracing_content": (app.defaults or {}).get("tracing_content", "masked"),
        "markup": (app.defaults or {}).get("markup"),
        "memory_mode": (app.defaults or {}).get("memory_mode", "on"),
        "memory_ttl_days": (app.defaults or {}).get("memory_ttl_days"),
        "redteam_gate": (app.defaults or {}).get("redteam_gate", "off"),
        "redteam_min_score": (app.defaults or {}).get("redteam_min_score", 80),
        "redteam_on_save": (app.defaults or {}).get("redteam_on_save", True),
        "cost_alert_thresholds": (app.defaults or {}).get("cost_alert_thresholds") or [75, 90, 100],
        "cost_alert_webhook": (app.defaults or {}).get("cost_alert_webhook"),
        "hasCostAlertSecret": bool((app.defaults or {}).get("cost_alert_secret")),
        "messageDefaults": {
            k: v for k, v in pack(locale_of(app))["messages"].items() if k.startswith("limit.")
        },
    }


@router.patch("/application")
async def patch_application(
    body: AppPatch, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Change caps, origins, models, storage mode or the default voice engine (owner).

    Returns:
        ok.

    Raises:
        NotFound: The application is gone.
        BadRequest: A system application kept longer than 30 days (400 ``system_ttl``), or its
            audit log kept less (400 ``system_audit_ttl``).
    """
    before = await get_application(svc, a)
    fields = body.model_dump(exclude_unset=True)
    current = await svc.store.applications.get(a.application_id)
    if current is None:
        raise NotFound()
    _check_caps(body, fields, current)
    ttl = fields.get("conversation_ttl_days", -1)
    if current.system and "conversation_ttl_days" in fields and not (ttl and ttl <= MAX_TTL_DAYS):
        raise BadRequest(
            f"A system application keeps conversations {MAX_TTL_DAYS} days at most.",
            code="system_ttl",
        )
    audit_ttl = fields.get("audit_ttl_days")
    if current.system and audit_ttl is not None and audit_ttl < MAX_TTL_DAYS:
        # Inspections of the assistant's conversations live in the audit log: they must outlive
        # the conversations, so their authors can see them.
        raise BadRequest(
            f"A system application keeps its audit log {MAX_TTL_DAYS} days at least.",
            code="system_audit_ttl",
        )
    if "markup" in fields:
        fields["markup"] = body.markup.model_dump(exclude_none=True) if body.markup else None
    if "residency" in fields:
        fields["residency"] = body.residency.stored() if body.residency else None
    if "data_protection" in fields:
        fields["data_protection"] = body.data_protection.stored() if body.data_protection else None
    if "eval_credential" in fields:
        # Kept encrypted like a forwarded credential; an empty value clears it.
        fields["eval_credential"] = (
            svc.keyring.encrypt(fields["eval_credential"]) if fields["eval_credential"] else None
        )
    if "cost_alert_secret" in fields:
        fields["cost_alert_secret"] = (
            svc.keyring.encrypt(fields["cost_alert_secret"])
            if fields["cost_alert_secret"]
            else None
        )
    if fields.get("cost_alert_webhook"):
        from evokai.costs.alerts import check_webhook

        check_webhook(fields["cost_alert_webhook"], svc.settings.alert_webhook_allow)
    elif "cost_alert_webhook" in fields:
        fields["cost_alert_webhook"] = None
    if fields.get("cost_alert_thresholds") is not None:
        fields["cost_alert_thresholds"] = sorted(set(fields["cost_alert_thresholds"]))
    in_defaults = DEFAULT_KEYS & set(fields)
    if in_defaults:
        app = await svc.store.applications.get(a.application_id)
        defaults = dict((app.defaults if app else None) or {})
        for key in in_defaults:
            value = fields.pop(key)
            if value is None:
                defaults.pop(key, None)
            else:
                defaults[key] = value
        fields["defaults"] = defaults
    if fields:
        await svc.store.applications.update(a.application_id, **fields)
    after = await get_application(svc, a)
    changed = [k for k in after if before.get(k) != after.get(k)]
    await audit.record(
        request,
        a,
        "application.update",
        target=", ".join(changed),
        before={k: before.get(k) for k in changed},
        after={k: after.get(k) for k in changed},
    )
    return {"ok": True}


class KeyRequest(BaseModel):
    """A new key."""

    kind: Literal["srv", "adm"] = "srv"
    label: str = Field(default="", max_length=120)


@router.post("/keys")
async def create_key(body: KeyRequest, svc: Svc, a: Admin, request: Request) -> dict[str, str]:
    """Create a key (rotation: create the new one, deploy it, revoke the old one).

    Returns:
        The key, shown only now, and its id.
    """
    key = generate_key(body.kind)
    key_id = await svc.store.applications.add_key(
        a.application_id, key.kind, key.prefix, key.secret_hash, body.label
    )
    await audit.record(
        request, a, "key.create", target=key.prefix, after={"kind": key.kind, "label": body.label}
    )
    return {"id": key_id, "key": key.value}


@router.get("/keys")
async def list_keys(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's keys (never their secrets).

    Returns:
        items.
    """
    rows = await svc.store.applications.keys(a.application_id)
    return {
        "items": [
            {
                "id": k.id,
                "kind": k.kind,
                "prefix": k.prefix,
                "label": k.label,
                "createdAt": k.created_at.isoformat(),
                "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
                "revoked": k.revoked_at is not None,
                "current": k.id == a.key_id,
            }
            for k in rows
        ]
    }


@router.post("/keys/{key_id}/rotate")
async def rotate_key(key_id: str, svc: Svc, a: Admin, request: Request) -> dict[str, str]:
    """Create the successor of a key (same kind and label).

    The old key stays valid until it is revoked, so the new one can be deployed first.

    Returns:
        The new key, shown only now, its id, and the id of the key it replaces.

    Raises:
        NotFound: Unknown or revoked key.
    """
    old = next(
        (
            k
            for k in await svc.store.applications.keys(a.application_id)
            if k.id == key_id and k.revoked_at is None
        ),
        None,
    )
    if old is None:
        raise NotFound()
    key = generate_key(old.kind)
    new_id = await svc.store.applications.add_key(
        a.application_id, key.kind, key.prefix, key.secret_hash, old.label
    )
    await audit.record(request, a, "key.rotate", target=key.prefix, before={"replaces": old.prefix})
    return {"id": new_id, "key": key.value, "replaces": old.id}


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Revoke a key.

    Returns:
        ok.

    Raises:
        BadRequest: Revoking the key making this request.
        NotFound: Unknown or already revoked.
    """
    if key_id == a.key_id:
        raise BadRequest("You cannot revoke the key you are using.", code="self_revoke")
    if not await svc.store.applications.revoke_key(a.application_id, key_id):
        raise NotFound()
    await svc.auth.forget_cached_keys()
    row = await svc.store.applications.key(key_id)
    await audit.record(request, a, "key.revoke", target=row.prefix if row else key_id)
    return {"ok": True}


@router.get("/agents")
async def list_agents(svc: Svc, a: Admin) -> dict[str, Any]:
    """Active agents with their definitions.

    Returns:
        items, each with its ``template_syntax`` (``legacy`` for a version saved before
        Handlebars).
    """
    rows = await svc.store.agents.active_all(a.application_id)
    return {
        "items": [{"template_syntax": "legacy", **r.definition, "version": r.version} for r in rows]
    }


def _check_builtin_tools(app: Any, body: AgentDef) -> None:
    """Built-in tools that keep data at the provider need the data protection's consent.

    Raises:
        AppError: 400 ``provider_storage``.
    """
    from evokai.engine.builtin_tools import STORING, storage_allowed

    keeping = [t.kind for t in body.builtin_tools if t.kind in STORING]
    if keeping and not storage_allowed(protection(app)):
        raise AppError(
            f"{', '.join(keeping)} would keep data at the model's provider: the application's "
            "data protection does not allow it (provider_tool_storage).",
            code="provider_storage",
            status=400,
        )


@router.put("/agents/{name}")
async def put_agent(
    name: str, body: AgentDef, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Create or update an agent; a changed definition becomes a new active version.

    Returns:
        version and whether it is new.

    Raises:
        BadRequest: Name mismatch, or a model outside the deployment's allow-list.
        NotGrantedInAgent: The definition references a model, MCP server, skill, client tool or
            channel the application is not granted (400 ``not_granted``, with ``problems``).
        NotFound: The application is gone.
        AppError: A model outside the application's data residency (400 ``residency``).
        Forbidden: A console operator changing the agent's security policy (owner only).
        Conflict: The red team gate blocked this version (409 ``redteam_blocked``).
        BadRequest: A prompt module that does not exist or whose parameters do not fit
            (``unknown_module``, ``module_params``).
    """
    if body.name != name:
        raise BadRequest("The name in the path and in the body must be the same.")
    # What is saved now is Handlebars; a body that is exactly a stored legacy version is a restore.
    body = await syntax_to_save(svc.store, a.application_id, body)
    for model in (body.model, *body.fallbacks, *body.models):
        if not svc.catalog.allowed(model):
            raise BadRequest(f"Model not allowed: {model}", code="model")
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    problems = (await grants_of(svc.store, app)).problems(body, explicit_client_tools(body))
    if problems:
        raise NotGrantedInAgent(problems)
    _check_builtin_tools(app, body)
    outside = [
        x
        for x in dict.fromkeys((body.model, *body.fallbacks, *body.models))
        if not residency_allows(protection(app), x, svc.settings.aws_region)
    ]
    if outside:
        raise AppError(
            "Outside the application's data residency: " + ", ".join(outside) + ".",
            code="residency",
            status=400,
        )
    current = await svc.store.agents.active(a.application_id, name)
    before = current.definition if current else None
    latest = before
    if a.via and current is None:
        # Deleting and saving again must not drop a policy: the last version counts.
        versions = await svc.store.agents.versions(a.application_id, name)
        latest = versions[0].definition if versions else None
    if (a.role != "owner" or a.via) and (latest or {}).get("security") != body.security:
        # The console assistant never changes a security policy, whoever it acts for.
        raise Forbidden("Changing an agent's security policy needs the owner role.", code="role")
    stored = await svc.store.agents.version(a.application_id, name, body.with_version().version)
    check_agent_templates(body, before, stored=stored is not None)
    await _check_ask_rules(svc, a, body)
    await check_agent_modules(svc.store, a.application_id, body)
    agent = body.with_version()
    gate, _minimum = gate_of(app)
    existing = await svc.store.agents.version(a.application_id, name, agent.version)
    if gate == "enforce" and existing is not None and existing.publish_state:
        if existing.publish_state == "pending":
            return {
                "version": agent.version,
                "created": False,
                "published": False,
                "publishState": "pending",
            }
        raise Conflict(
            "This version's red team score is below the application's gate: an owner can "
            "publish it anyway from its versions.",
            code="redteam_blocked",
        )
    # With the gate enforced a new version waits for its red team run before it is published.
    hold = gate == "enforce" and existing is None
    created = await svc.store.agents.put(
        a.application_id,
        name,
        agent.version,
        agent.content(),
        created_by=a.key_id,
        activate=not hold,
    )
    svc.agents.invalidate(a.application_id, name)
    run = redteam = None
    if created:
        await audit.record(
            request, a, "agent.put", target=name, before=before, after=agent.content()
        )
        previous = current.version if current else None
        run = await svc.evals.on_save(app, name, agent.version, previous, a.key_id)
        redteam = await svc.redteam.on_save(
            app, name, agent.version, previous, a.key_id, gated=hold
        )
    out: dict[str, Any] = {"version": agent.version, "created": created, "published": not hold}
    if hold:
        out["publishState"] = "pending"
    if run is not None:
        out["evalRunId"] = run.id
    if redteam is not None:
        out["redteamRunId"] = redteam.id
    return out


async def _check_ask_rules(svc: Services, a: AppPrincipal, body: AgentDef) -> None:
    """An agent's ask rules only add confirmations (``evokai.guard.ask.check_rules``).

    Raises:
        BadRequest: A ``default`` rule names a tool that changes data, or has no ``ask`` above it
            in the agent's own list (it could only lift another level's): 400 ``ask_rules``.
    """
    from evokai.api.ask_routes import known_tools
    from evokai.guard.ask import check_rules

    if not body.ask_rules:
        return
    tools = await known_tools(svc, a.application_id)
    problems = check_rules(body.ask_rules, {t["name"] for t in tools if t["mutating"]})
    if problems:
        raise BadRequest("; ".join(problems), code="ask_rules")


class NotGrantedInAgent(AppError):
    """An agent definition references something the application is not granted."""

    def __init__(self, problems: list[str]) -> None:
        """Create the error.

        Args:
            problems: One line per item that is not granted.
        """
        super().__init__(
            "The agent uses what this application is not granted: " + "; ".join(problems) + ".",
            code="not_granted",
            status=400,
        )
        self.extra = {"problems": problems}


@router.delete("/agents/{name}")
async def delete_agent(name: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Retire an agent (pinned conversations keep their version).

    Returns:
        ok.

    Raises:
        NotFound: No such active agent.
    """
    current = await svc.store.agents.active(a.application_id, name)
    if not await svc.store.agents.deactivate(a.application_id, name):
        raise NotFound()
    svc.agents.invalidate(a.application_id, name)
    await audit.record(
        request, a, "agent.delete", target=name, before=current.definition if current else None
    )
    return {"ok": True}


class McpServerBody(BaseModel):
    """An MCP server: a URL, or a ready connector served by Evokai (``connector``, ``config``)."""

    url: str | None = Field(default=None, pattern=r"^https?://")
    auth: Literal["forward_user", "secret", "none"] = "forward_user"
    secret: str | None = None
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    tool_prefix: str = Field(default="", max_length=40)
    connector: str | None = Field(default=None, max_length=40)
    """``sql``, ``openapi``, ``google_drive``, ``microsoft_files`` or ``imap`` (docs/connectors)."""
    config: dict[str, Any] | None = None
    """The connector's configuration: environment variable names for secrets, never secrets."""
    worker: bool = False
    """An Evokai capability worker (``workers/capability``, ``auth: secret``): its calls carry the
    conversation, the person and the agent, and it may call models through Evokai."""


@router.get("/mcp-servers")
async def list_mcp(svc: Svc, a: Admin) -> dict[str, Any]:
    """MCP servers (secrets never leave the service).

    Returns:
        items.
    """
    rows = await svc.store.mcp_servers.all(a.application_id)
    return {
        "items": [
            {
                "name": r.name,
                "url": r.url,
                "auth": r.auth,
                "hasSecret": bool(r.secret_encrypted),
                "timeout_seconds": r.timeout_seconds,
                "tool_prefix": r.tool_prefix,
                "status": r.status,
                "latencyMs": r.latency_ms,
                "checkedAt": r.checked_at.isoformat() if r.checked_at else None,
                "lastError": r.last_error,
                "tools": r.tools,
                "connector": r.connector,
                "config": r.config,
                "worker": bool(r.worker),
            }
            for r in rows
        ]
    }


@router.put("/mcp-servers/{name}")
async def put_mcp(
    name: str, body: McpServerBody, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Register or replace an MCP server.

    Returns:
        ok.

    Raises:
        BadRequest: auth=secret without a secret (a server that has one keeps it when the body
            leaves it out), neither or both of url and connector, or an invalid connector
            configuration (a secret in clear among them).
    """
    if (body.url is None) == (body.connector is None):
        raise BadRequest("Give a url or a connector.", code="mcp_target")
    if body.worker and (body.connector or body.auth != "secret"):
        raise BadRequest("A capability worker is a url with auth=secret.", code="worker")
    if body.connector:
        validate_connector(body.connector, body.config)
        await svc.store.mcp_servers.put(
            a.application_id,
            name,
            url=f"{CONNECTOR_URL}{body.connector}",
            auth="none",
            secret_encrypted=None,
            timeout_seconds=body.timeout_seconds,
            tool_prefix=body.tool_prefix,
            connector=body.connector,
            config=body.config or {},
            worker=False,
        )
        svc.mcp.forget(a.application_id)
        return {"ok": True}
    existing = next(
        (x for x in await svc.store.mcp_servers.all(a.application_id) if x.name == name), None
    )
    kept = existing.secret_encrypted if existing and existing.auth == "secret" else None
    if body.auth == "secret" and not body.secret and not kept:
        raise BadRequest("auth=secret needs a secret.", code="secret")
    if body.auth != "secret":
        encrypted = None
    elif body.secret:
        encrypted = svc.keyring.encrypt(body.secret)
    else:
        encrypted = kept
    await svc.store.mcp_servers.put(
        a.application_id,
        name,
        url=body.url,
        auth=body.auth,
        secret_encrypted=encrypted,
        timeout_seconds=body.timeout_seconds,
        tool_prefix=body.tool_prefix,
        connector=None,
        config=None,
        worker=body.worker,
    )
    svc.mcp.forget(a.application_id)
    shown = {
        "url": body.url,
        "auth": body.auth,
        "timeout_seconds": body.timeout_seconds,
        "worker": body.worker,
    }
    await audit.record(
        request,
        a,
        "mcp_server.put",
        target=name,
        before={"url": existing.url, "auth": existing.auth, "hasSecret": bool(kept)}
        if existing
        else None,
        after={**shown, "tool_prefix": body.tool_prefix, "hasSecret": bool(encrypted)},
    )
    return {"ok": True}


@router.get("/connectors")
async def connectors(_a: Admin) -> dict[str, Any]:
    """The ready connectors an application can enable, with their configuration schemas.

    Returns:
        items: kind, title, schema.
    """
    return {"items": describe_connectors()}


@router.delete("/mcp-servers/{name}")
async def delete_mcp(name: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Remove an MCP server.

    Returns:
        ok.

    Raises:
        NotFound: Unknown server.
    """
    if not await svc.store.mcp_servers.delete(a.application_id, name):
        raise NotFound()
    svc.mcp.forget(a.application_id)
    await audit.record(request, a, "mcp_server.delete", target=name)
    return {"ok": True}


class SkillFileBody(BaseModel):
    """A file of a skill besides SKILL.md: a reference, a data file or template, a script."""

    path: str = Field(min_length=1, max_length=sk.MAX_PATH_CHARS)
    """Relative path inside the skill, like ``references/terms.md`` or ``scripts/compute.py``."""
    content: str = Field(max_length=sk.MAX_SKILL_BYTES)


class SkillBody(BaseModel):
    """A skill: its description, the body of SKILL.md and, optionally, its files."""

    description: str = Field(min_length=1)
    """At most ``MAX_DESCRIPTION_CHARS`` (1,024); over it, 400 ``skill_limit`` names the limit."""
    body: str = Field(min_length=1)
    """At most ``MAX_BODY_CHARS`` (20,000); over it, 400 ``skill_limit``."""
    files: list[SkillFileBody] | None = Field(default=None, max_length=sk.MAX_SKILL_FILES)
    """All the skill's files, replacing the stored ones; omitted, the stored files are kept."""


class SkillFileContent(BaseModel):
    """The text of one file of a skill."""

    content: str = Field(max_length=sk.MAX_SKILL_BYTES)


async def _skill(svc: Services, application_id: str, name: str) -> Any:
    row = next((r for r in await svc.store.skills.all(application_id) if r.name == name), None)
    if row is None:
        raise NotFound()
    return row


def _checked(name: str, description: str, body: str, files: list[tuple[str, str]]) -> None:
    """Check a skill against the limits of ``evokai.agents.skills``.

    Raises:
        BadRequest: A limit or a path rule is broken (``skill_limit``, with the rule).
    """
    try:
        sk.check_skill(name, description, body, files)
    except sk.SkillError as e:
        raise BadRequest(str(e), code="skill_limit") from None


async def _scripts(svc: Services, application_id: str, name: str) -> dict[str, Any]:
    """Whether a skill's scripts can run, and why not (deployment, grant, agent).

    Returns:
        runnable, reason (``deployment``, ``grant``, ``agent`` or None) and the agents that may
        run them.
    """
    app = await svc.store.applications.get(application_id)
    granted = (await grants_of(svc.store, app)).code() if app else False
    using = [
        AgentDef.model_validate(r.definition)
        for r in await svc.store.agents.active_all(application_id)
    ]
    using = [d for d in using if d.skills is None or name in d.skills]
    agents = sorted(d.name for d in using if d.code is not None and d.code.enabled)
    deployment = svc.code is not None and svc.code.available
    reason = unavailable_reason(deployment=deployment, granted=granted, agent=bool(agents))
    return {"runnable": reason is None, "reason": reason, "agents": agents if granted else []}


def _file_view(path: str, size: int, title: str) -> dict[str, Any]:
    return {"path": path, "kind": sk.kind_of(path), "size": size, "title": title}


@router.get("/skills")
async def list_skills(svc: Svc, a: Admin) -> dict[str, Any]:
    """Skills.

    Returns:
        items (name, description, size, files, scripts, updatedAt).
    """
    rows = await svc.store.skills.all(a.application_id)
    index = await svc.store.skills.file_index(a.application_id)
    return {
        "items": [
            {
                "name": r.name,
                "description": r.description,
                "size": len(r.body.encode()),
                "files": len(index.get(r.name, [])),
                "scripts": sum(1 for p, _, _ in index.get(r.name, []) if sk.kind_of(p) == "script"),
                "updatedAt": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/skills/{name}")
async def get_skill(name: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """One skill with its body (the SKILL.md text without its front matter) and its files.

    Returns:
        name, description, body, size, updatedAt, files (path, kind, size, title), scripts
        (runnable, reason, agents) and limits; 404 for an unknown skill.
    """
    row = await _skill(svc, a.application_id, name)
    files = (await svc.store.skills.file_index(a.application_id)).get(name, [])
    return {
        "name": row.name,
        "description": row.description,
        "body": row.body,
        "size": len(row.body.encode()),
        "updatedAt": row.created_at.isoformat(),
        "files": [_file_view(*f) for f in files],
        "scripts": await _scripts(svc, a.application_id, name),
        "limits": SKILL_LIMITS,
    }


@router.put("/skills/{name}")
async def put_skill(
    name: str, body: SkillBody, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Create or replace a skill (with ``files``, its files too; without, they are kept).

    Returns:
        ok; 400 ``skill_limit`` when the skill breaks a limit or a path rule.
    """
    old = next((r for r in await svc.store.skills.all(a.application_id) if r.name == name), None)
    files = (
        [(f.path, f.content) for f in body.files]
        if body.files is not None
        else await svc.store.skills.files(a.application_id, name)
    )
    _checked(name, body.description, body.body, files)
    await svc.store.skills.put(
        a.application_id,
        name,
        body.description,
        body.body,
        files if body.files is not None else None,
    )
    after: dict[str, Any] = {"description": body.description, "body": body.body}
    if body.files is not None:
        after["files"] = sorted(p for p, _ in files)
    await audit.record(
        request,
        a,
        "skill.put",
        target=name,
        before={"description": old.description, "body": old.body} if old else None,
        after=after,
    )
    return {"ok": True}


@router.get("/skills/{name}/files/{path:path}")
async def get_skill_file(name: str, path: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """One file of a skill, with its text.

    Returns:
        path, kind, size, title, content.

    Raises:
        NotFound: Unknown skill or file.
    """
    await _skill(svc, a.application_id, name)
    content = await svc.store.skills.file(a.application_id, name, path)
    if content is None:
        raise NotFound()
    return {**_file_view(path, len(content), sk.title_of(path, content)), "content": content}


@router.put("/skills/{name}/files/{path:path}")
async def put_skill_file(
    name: str, path: str, body: SkillFileContent, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Add or replace one file of a skill.

    Returns:
        ok; 404 for an unknown skill, 400 ``skill_limit`` when a path or limit rule is broken.
    """
    row = await _skill(svc, a.application_id, name)
    files = dict(await svc.store.skills.files(a.application_id, name))
    before = files.get(path)
    files[path] = body.content
    _checked(name, row.description, row.body, sorted(files.items()))
    await svc.store.skills.put_file(a.application_id, name, path, body.content)
    await audit.record(
        request,
        a,
        "skill.file_put",
        target=f"{name}/{path}",
        before={"size": len(before)} if before is not None else None,
        after={"size": len(body.content)},
    )
    return {"ok": True}


@router.delete("/skills/{name}/files/{path:path}")
async def delete_skill_file(
    name: str, path: str, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Remove one file of a skill.

    Returns:
        ok.

    Raises:
        NotFound: Unknown skill or file.
    """
    await _skill(svc, a.application_id, name)
    if not await svc.store.skills.delete_file(a.application_id, name, path):
        raise NotFound()
    await audit.record(request, a, "skill.file_delete", target=f"{name}/{path}")
    return {"ok": True}


@router.delete("/skills/{name}")
async def delete_skill(name: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Remove a skill and its files.

    Returns:
        ok.

    Raises:
        NotFound: Unknown skill.
    """
    if not await svc.store.skills.delete(a.application_id, name):
        raise NotFound()
    await audit.record(request, a, "skill.delete", target=name)
    return {"ok": True}


SKILL_LIMITS = {
    "descriptionChars": sk.MAX_DESCRIPTION_CHARS,
    "bodyChars": sk.MAX_BODY_CHARS,
    "readChars": sk.MAX_READ_CHARS,
    "skillBytes": sk.MAX_SKILL_BYTES,
    "files": sk.MAX_SKILL_FILES,
    "scriptBytes": sk.MAX_SCRIPT_BYTES,
}
"""The limits of a skill, as the console shows them."""


class PromptBody(BaseModel):
    """A prompt's text; saving a different text, schema, description or data makes a new version."""

    text: str = Field(min_length=1, max_length=60_000)
    parameters: dict[str, Any] | None = None
    """A prompt module's parameter schema (``evokai.agents.modules``); the text is then a
    Handlebars template checked against it."""
    description: str = Field("", max_length=500)
    data: dict[str, str] | None = None
    """A module's lookup table (key to text, at most 256 KB), read in the template as ``data``."""


@router.put("/prompts/{name}")
async def put_prompt(
    name: str, body: PromptBody, svc: Svc, a: Admin, request: Request
) -> dict[str, int]:
    """Save a prompt (a changed text, schema, description or data becomes the next version).

    Returns:
        The version.

    Raises:
        BadRequest: A schema, template or data table that does not pass the module checks, or
            a new version whose parameters no longer fit an agent's values (``module_in_use``).
    """
    # What the body leaves out stays as the latest version has it: a text-only save (an older
    # client) keeps a module a module. Send ``parameters: null`` to make it a plain prompt.
    last = await svc.store.prompts.get(a.application_id, name)
    sent = body.model_fields_set
    keep = last is not None
    parameters = body.parameters if "parameters" in sent or not keep else last.parameters
    data = body.data if "data" in sent or not keep else last.data
    description = (
        body.description if "description" in sent or not keep else (last.description or "")
    )
    if parameters is not None:
        check_parameters_schema(parameters)
        check_data(data)
    elif data is not None:
        raise BadRequest("A data table needs parameters (a module).", code="module_data")
    check_prompt(body.text, parameters, data)
    await check_prompt_in_use(svc.store, a.application_id, name, parameters)
    version = await svc.store.prompts.put(
        a.application_id,
        name,
        body.text,
        parameters=parameters,
        description=description,
        data=data,
    )
    await audit.record(
        request,
        a,
        "prompt.put",
        target=f"{name} v{version}",
        after={"text": body.text, "parameters": parameters},
    )
    return {"version": version}


class PreviewBody(BaseModel):
    """Sample values for a prompt preview, and optionally an unsaved draft to preview instead."""

    params: dict[str, Any] = Field(default_factory=dict)
    version: int | None = Field(default=None, ge=1)
    """A saved version (default: the latest), when no draft is sent."""
    text: str | None = Field(default=None, min_length=1, max_length=60_000)
    """A draft text: checked like a save (with ``parameters`` and ``data``) and rendered."""
    parameters: dict[str, Any] | None = None
    data: dict[str, str] | None = None


@router.post("/prompts/{name}/preview")
async def preview_prompt(name: str, body: PreviewBody, svc: Svc, a: Admin) -> dict[str, str]:
    """Render a prompt (a saved version or a draft) with sample parameter values.

    The values get the parameters' defaults and are checked against the schema; required ones may
    be left out. ``date`` and ``app`` are today's date and the application's name.

    Returns:
        text: the rendered prompt.

    Raises:
        NotFound: No such prompt (and no draft) or application. A draft or values that do not
            pass the module checks are refused as when saving (400 ``module_*``).
    """
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    if body.text is not None:
        text, parameters, data, syntax = body.text, body.parameters, body.data, "handlebars"
        if parameters is not None:
            check_parameters_schema(parameters)
            check_data(data)
        check_prompt(text, parameters, data)
    else:
        row = await svc.store.prompts.get(a.application_id, name, body.version)
        if row is None:
            raise NotFound("Unknown prompt.", code="unknown_prompt")
        text, parameters, data, syntax = row.text, row.parameters, row.data, row.template_syntax
    params = validate_params(parameters, body.params, partial=True) if parameters else {}
    timezone = str((app.defaults or {}).get("timezone") or "UTC")
    values = {"date": long_date(utcnow(), timezone), "app": app.name, "data": data or {}, **params}
    if syntax != "legacy":
        return {"text": render_text(text, syntax, values)}
    return {"text": render_template(text, values) if parameters else render_legacy(text, values)}


@router.get("/prompts")
async def list_prompts(svc: Svc, a: Admin) -> dict[str, Any]:
    """Every version of every prompt.

    Returns:
        items: name, version, text, description, parameters (a module's schema, or null), data,
        templateSyntax and createdAt.
    """
    rows = await svc.store.prompts.all(a.application_id)
    return {
        "items": [
            {
                "name": r.name,
                "version": r.version,
                "text": r.text,
                "description": r.description or "",
                "parameters": r.parameters,
                "data": r.data,
                "templateSyntax": r.template_syntax,
                "createdAt": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


class VoiceBody(BaseModel):
    """A voice of a text-to-speech provider."""

    language: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=120)
    gender: str | None = Field(default=None, max_length=16)
    region: str | None = Field(default=None, max_length=40)


@router.put("/voices/{provider}/{voice_id}")
async def put_voice(
    provider: str, voice_id: str, body: VoiceBody, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Add or replace a voice in the deployment's catalog (owner).

    Returns:
        ok.
    """
    await svc.store.voices.put(provider, voice_id, **body.model_dump())
    await audit.record(
        request, a, "voice.put", target=f"{provider}/{voice_id}", after=body.model_dump()
    )
    return {"ok": True}


@router.get("/bug-reports")
async def bug_reports(svc: Svc, a: Admin) -> dict[str, Any]:
    """Recent problem reports.

    Returns:
        items.
    """
    rows = await svc.store.bug_reports.all(a.application_id)
    mine = await own_subject(svc, a)
    if mine is not None and not lists_everyone(a):
        rows = [r for r in rows if r.subject == mine]
    return {
        "items": [
            {
                "id": r.id,
                "subject": r.subject,
                "sessionId": r.session_id,
                "description": r.description,
                "context": r.context,
                "status": r.status,
                "createdAt": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
