"""Bootstrap an application from a YAML file: application, keys, MCP servers, skills and agents.

Idempotent: running it again only stores what changed (a changed agent becomes a new version).
``${VAR}`` and ``${VAR:-default}`` are replaced from the environment.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from evokai.agents.definitions import AgentDef
from evokai.agents.modules import (
    check_agent_modules,
    check_data,
    check_parameters_schema,
)
from evokai.agents.skills import Skill, SkillError, check_skill, make_file, skills_in
from evokai.agents.templates import check_agent_templates, check_prompt
from evokai.audio.speech.markup import MarkupDef
from evokai.connectors import URL_SCHEME as CONNECTOR_URL
from evokai.connectors import validate as validate_connector
from evokai.core.caps import (
    DEFAULT_CURRENCY,
    DEFAULT_MONTHLY_USD,
    LEGACY,
    CapsSpec,
    check_currency,
    legacy_conflicts,
)
from evokai.core.config import BootstrapApp
from evokai.core.errors import AppError, ConfigError
from evokai.core.security import Keyring, hash_secret, parse_key
from evokai.db.repositories import Store
from evokai.grants import explicit_client_tools, from_yaml, grants_of
from evokai.guard import identifiers
from evokai.guard.ask import check_rules
from evokai.observability.tracing import CONTENT_MODES

log = logging.getLogger("evokai.bootstrap")
_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def expand_env(text: str) -> str:
    """Replace ${VAR} and ${VAR:-default}.

    Args:
        text: YAML source.

    Returns:
        The text with variables replaced.
    """
    return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), text)


async def bootstrap(
    store: Store, keyring: Keyring, path: str | Path, keys: dict[str, str | None]
) -> str:
    """Create or update an application from a file.

    Args:
        store: Repositories.
        keyring: For encrypting MCP secrets.
        path: The YAML file.
        keys: Optional keys to register: {"srv": "evk_srv_…", "adm": "evk_adm_…"}.

    Returns:
        The application id.

    Raises:
        ConfigError: Malformed file or keys.
    """
    p = Path(path)
    data: dict[str, Any] = yaml.safe_load(expand_env(p.read_text(encoding="utf-8"))) or {}
    spec = data.get("application") or {}
    slug, name = spec.get("slug"), spec.get("name")
    if not slug or not name:
        raise ConfigError("bootstrap: application.slug and application.name are required")
    fields = {
        k: spec[k]
        for k in (
            "allowed_origins",
            "allowed_models",
            "storage_mode",
            "defaults",
            "conversation_ttl_days",
            "security_event_ttl_days",
        )
        if k in spec
    }
    money = _caps(spec)
    if "defaults" in money:
        money["defaults"] = {**(fields.get("defaults") or {}), **money["defaults"]}
    fields.update(money)
    if "security" in spec:
        fields["security"] = _security(spec.get("security"))
    if "ask_rules" in spec:
        fields["ask_rules"] = _ask_rules(spec.get("ask_rules"))
    if "residency" in spec:
        fields["residency"] = _residency(spec.get("residency"))
    if "data_protection" in spec:
        fields["data_protection"] = _data_protection(spec.get("data_protection"))
    in_defaults = {
        k: spec[k]
        for k in (
            "locale",
            "timezone",
            "messages",
            "voice_engine",
        )
        if k in spec
    }
    if spec.get("markup") is not None:
        try:
            markup = MarkupDef.model_validate(spec["markup"])
        except ValueError as e:
            raise ConfigError(f"bootstrap: markup: {e}") from e
        in_defaults["markup"] = markup.model_dump(exclude_none=True)
    memory = spec.get("memory")
    if isinstance(memory, dict):
        if memory.get("mode") is not None:
            if memory["mode"] not in ("off", "ask", "on"):
                raise ConfigError("bootstrap: memory.mode must be off, ask or on")
            in_defaults["memory_mode"] = memory["mode"]
        if memory.get("ttl_days") is not None:
            in_defaults["memory_ttl_days"] = int(memory["ttl_days"])
    redteam = spec.get("redteam")
    if isinstance(redteam, dict):
        if redteam.get("gate") is not None:
            if redteam["gate"] not in ("off", "monitor", "enforce"):
                raise ConfigError("bootstrap: redteam.gate must be off, monitor or enforce")
            in_defaults["redteam_gate"] = redteam["gate"]
        if redteam.get("min_score") is not None:
            in_defaults["redteam_min_score"] = float(redteam["min_score"])
        if redteam.get("on_save") is not None:
            in_defaults["redteam_on_save"] = bool(redteam["on_save"])
    alerts = spec.get("cost_alerts")
    if isinstance(alerts, dict):
        if alerts.get("thresholds"):
            in_defaults["cost_alert_thresholds"] = sorted({int(x) for x in alerts["thresholds"]})
        if alerts.get("webhook"):
            in_defaults["cost_alert_webhook"] = str(alerts["webhook"])
    tracing = spec.get("tracing")
    if isinstance(tracing, dict) and "content" in tracing:
        if tracing["content"] not in CONTENT_MODES:
            raise ConfigError(
                f"bootstrap: tracing.content must be one of {', '.join(CONTENT_MODES)}"
            )
        in_defaults["tracing_content"] = tracing["content"]
    # The file is the whole set: identifiers left out of it are no longer recognised.
    declared = _identifiers(spec.get("identifiers"))
    in_defaults["identifiers"] = identifiers.stored(declared) or None
    _check_pii_types(spec.get("security"), declared, "application")
    for raw in data.get("agents") or []:
        if isinstance(raw, dict):
            _check_pii_types(raw.get("security"), declared, f"agent {raw.get('name')}")
    fields["defaults"] = {**(fields.get("defaults") or {}), **in_defaults}
    if fields["defaults"].get("identifiers") is None:
        fields["defaults"].pop("identifiers", None)
        fields["defaults_removed"] = ["identifiers"]
    if not fields["defaults"]:
        fields.pop("defaults")
    app_id = await ensure_application(store, slug, name, fields)
    # Keys from the file (usually ``${ENV_VAR}``) come first; the settings' keys are a fallback.
    await register_keys(
        store,
        app_id,
        {
            "srv": spec.get("server_key") or keys.get("srv"),
            "adm": spec.get("admin_key") or keys.get("adm"),
        },
    )

    for server in data.get("mcp_servers") or []:
        if server.get("connector"):
            kind = str(server["connector"])
            try:
                validate_connector(kind, server.get("config"))
            except AppError as e:
                raise ConfigError(f"bootstrap: mcp server {server.get('name')}: {e.message}") from e
            await store.mcp_servers.put(
                app_id,
                server["name"],
                url=f"{CONNECTOR_URL}{kind}",
                auth="none",
                secret_encrypted=None,
                timeout_seconds=float(server.get("timeout_seconds", 60)),
                tool_prefix=server.get("tool_prefix", ""),
                connector=kind,
                config=server.get("config") or {},
            )
            continue
        secret_env = server.get("secret_env")
        secret = os.environ.get(secret_env, "") if secret_env else ""
        await store.mcp_servers.put(
            app_id,
            server["name"],
            url=server["url"],
            auth=server.get("auth", "forward_user"),
            secret_encrypted=keyring.encrypt(secret) if secret else None,
            timeout_seconds=float(server.get("timeout_seconds", 60)),
            tool_prefix=server.get("tool_prefix", ""),
            connector=None,
            config=None,
            worker=bool(server.get("worker")) and server.get("auth") == "secret",
        )

    await _oauth_apps(store, keyring, app_id, data.get("oauth_apps"))

    for skill in _skills(p, data):
        await store.skills.put(
            app_id,
            skill.name,
            skill.description,
            skill.body,
            [(f.path, f.content or "") for f in skill.files],
        )

    for raw in data.get("knowledge_bases") or []:
        await store.knowledge.put_base(app_id, str(raw["name"]), **knowledge_base(raw))

    for raw in data.get("prompts") or []:
        await _prompt(store, app_id, raw, p)

    if "grants" in data:
        await apply_grants(store, app_id, data.get("grants") or {})

    # Capability credentials (web search, integrations, sandboxes): usually ``${ENV_VAR}``,
    # encrypted like MCP secrets; an empty value keeps what the console set.
    from evokai.capabilities import credentials

    for provider, values in (data.get("capability_credentials") or {}).items():
        if not isinstance(values, dict):
            raise ConfigError(f"bootstrap: capability_credentials.{provider} must be a mapping")
        try:
            await credentials.save(
                store,
                keyring,
                app_id,
                str(provider),
                {str(k): (None if v is None else str(v)) for k, v in values.items()},
            )
        except ValueError as e:
            raise ConfigError(f"bootstrap: capability_credentials: {e}") from e

    app = await store.applications.get(app_id)
    identifiers.of_app(app)
    grants = await grants_of(store, app) if app else None
    for raw in data.get("agents") or []:
        agent = AgentDef.model_validate({"template_syntax": "handlebars", **raw}).with_version()
        if problems := check_rules(agent.ask_rules, set()):
            raise ConfigError(f"bootstrap: agent {agent.name}: {'; '.join(problems)}")
        if agent.template_syntax == "legacy":
            raise ConfigError(
                f"bootstrap: {p}: agent {agent.name}: the file is Handlebars, "
                "remove template_syntax: legacy"
            )
        try:
            check_agent_templates(agent, None)
        except AppError as e:
            raise ConfigError(f"bootstrap: {p}: agent {agent.name}: {e.message}") from e
        try:
            await check_agent_modules(store, app_id, agent)
        except AppError as e:
            raise ConfigError(f"bootstrap: agent {agent.name}: {e.message}") from e
        if grants and (problems := grants.problems(agent, explicit_client_tools(raw))):
            # Applied anyway: the runtime offers only what is granted, and a start must not fail
            # because of a grant changed in the console.
            log.warning("agent_not_granted", extra={"agent": agent.name, "problems": problems})
        if await store.agents.put(
            app_id, agent.name, agent.version, agent.content(), created_by="config"
        ):
            log.info("agent_version", extra={"agent": agent.name, "version": agent.version})

    for raw in data.get("workflows") or []:
        await _workflow(store, app_id, raw)
    return app_id


async def _prompt(store: Store, app_id: str, raw: Any, path: Path) -> None:
    """Store a prompt of the application YAML (a new version only when something changed).

    An entry has a ``name`` and a ``text``, and optionally a ``description``; a prompt module
    also has ``parameters`` (its schema) and may have ``data`` (``evokai.agents.modules``).

    Raises:
        ConfigError: A malformed entry, schema, template or data table.
    """
    if not isinstance(raw, dict) or not raw.get("name") or not isinstance(raw.get("text"), str):
        raise ConfigError("bootstrap: every prompt needs a name and a text")
    name = str(raw["name"])
    parameters, data = raw.get("parameters"), raw.get("data")
    if parameters is None and data is not None:
        raise ConfigError(f"bootstrap: prompt {name}: a data table needs parameters (a module)")
    try:
        if parameters is not None:
            check_parameters_schema(parameters)
            check_data(data)
        check_prompt(raw["text"], parameters, data)
    except AppError as e:
        raise ConfigError(f"bootstrap: {path}: prompt {name}: {e.message}") from e
    await store.prompts.put(
        app_id,
        name,
        raw["text"],
        parameters=parameters,
        description=str(raw.get("description") or ""),
        data=data,
    )


async def _workflow(store: Store, app_id: str, raw: Any) -> None:
    """Store a workflow of the config file as its active version.

    Raises:
        ConfigError: The definition has problems.
    """
    from evokai.workflows.definitions import parse

    defn, problems = parse(raw)
    if defn is None:
        name = raw.get("name") if isinstance(raw, dict) else None
        detail = "; ".join(f"{p.path}: {p.message}" if p.path else p.message for p in problems[:5])
        raise ConfigError(f"bootstrap: workflow {name}: {detail}")
    if await store.workflows.put(
        app_id, defn.name, defn.version, defn.content(), created_by="config"
    ):
        log.info("workflow_version", extra={"workflow": defn.name, "version": defn.version})


async def _oauth_apps(store: Store, keyring: Keyring, app_id: str, raw: Any) -> None:
    """The application's OAuth apps at the providers no connector configures.

    ``oauth_apps: {github: {client_id, client_secret_env | client_secret, writes}}``; the secret
    is encrypted with the deployment's key. An app without a secret keeps the stored one.

    Raises:
        ConfigError: An unknown provider, one a connector configures, or a missing client id or
            secret.
    """
    from evokai.connections.providers import PROVIDERS

    if not raw:
        return
    if not isinstance(raw, dict):
        raise ConfigError("bootstrap: oauth_apps is a map of provider to its OAuth app")
    for provider, spec in raw.items():
        p = PROVIDERS.get(str(provider))
        if p is None or p.configured_by != "application":
            names = ", ".join(k for k, v in PROVIDERS.items() if v.configured_by == "application")
            raise ConfigError(f"bootstrap: oauth_apps.{provider}: one of {names}")
        spec = spec if isinstance(spec, dict) else {}
        client_id = str(spec.get("client_id") or "").strip()
        env = spec.get("client_secret_env")
        value = os.environ.get(str(env), "") if env else str(spec.get("client_secret") or "")
        if not client_id:
            raise ConfigError(f"bootstrap: oauth_apps.{provider}: client_id is required")
        try:
            await store.connections.put_app(
                app_id,
                p.id,
                client_id=client_id,
                secret=keyring.encrypt(value) if value else None,
                writes=bool(spec.get("writes", False)),
            )
        except ValueError as e:
            raise ConfigError(
                f"bootstrap: oauth_apps.{provider}: the client secret is missing"
                + (f" (set {env})" if env else "")
            ) from e


def knowledge_base(raw: dict[str, Any]) -> dict[str, Any]:
    """A knowledge base's settings from a config file (documents are added through the API).

    Returns:
        The stored fields.

    Raises:
        ConfigError: An invalid setting.
    """
    from pydantic import ValidationError

    from evokai.knowledge.chunking import ChunkSettings
    from evokai.knowledge.search import RetrievalSettings
    from evokai.knowledge.store import LANGUAGES
    from evokai.llm.catalog import normalize

    language = str(raw.get("language") or "simple")
    if language not in LANGUAGES:
        raise ConfigError(f"knowledge_bases.{raw.get('name')}: unknown language {language}")
    try:
        chunking = ChunkSettings.model_validate(raw.get("chunking") or {}).model_dump()
        retrieval = RetrievalSettings.model_validate(raw.get("retrieval") or {}).model_dump()
    except ValidationError as e:
        raise ConfigError(f"knowledge_bases.{raw.get('name')}: {e.errors()[0]['msg']}") from e
    return {
        "title": str(raw.get("title") or ""),
        "description": str(raw.get("description") or "")[:1000],
        "embedding_model": normalize(
            str(raw.get("embedding_model") or "bedrock:amazon.titan-embed-text-v2:0")
        ),
        "dimensions": int(raw.get("dimensions") or 1024),
        "language": language,
        "chunking": chunking,
        "retrieval": retrieval,
        "access": [str(x) for x in raw.get("access") or []],
        "link_template": raw.get("link_template") or None,
        "sources": list(raw.get("sources") or []),
        "retention_days": raw.get("retention_days"),
    }


def _security(raw: Any) -> dict[str, Any] | None:
    """A security policy from a config file, validated.

    Returns:
        The policy as stored (None for an empty one).

    Raises:
        ConfigError: An invalid policy.
    """
    from pydantic import ValidationError

    from evokai.guard import SecurityPolicy

    try:
        return SecurityPolicy.model_validate(raw or {}).stored()
    except ValidationError as e:
        raise ConfigError(f"bootstrap: invalid security policy: {e.errors()[0]['msg']}") from e


def _identifiers(raw: Any) -> list[identifiers.IdentifierSpec]:
    """The application's own identifiers from a config file, validated.

    Returns:
        The declarations.

    Raises:
        ConfigError: An invalid declaration.
    """
    try:
        return identifiers.parse(raw)
    except ValueError as e:
        raise ConfigError(f"bootstrap: application.identifiers: {e}") from e


def _check_pii_types(security: Any, declared: list[identifiers.IdentifierSpec], where: str) -> None:
    """``security.pii.types`` names only built-in or declared identifiers.

    Raises:
        ConfigError: An unknown name.
    """
    pii_settings = (security or {}).get("pii") if isinstance(security, dict) else None
    types = pii_settings.get("types") if isinstance(pii_settings, dict) else None
    if isinstance(types, list) and (bad := identifiers.unknown_types(types, declared)):
        raise ConfigError(
            f"bootstrap: {where}: unknown personal data types: {bad} "
            "(declare them in application.identifiers)"
        )


def _ask_rules(raw: Any) -> list[dict[str, Any]] | None:
    """The application's ask rules from a config file, validated (``evokai.guard.ask``).

    Returns:
        The rules as stored (None for none).

    Raises:
        ConfigError: An invalid rule (an effect other than ask or default, say).
    """
    from pydantic import ValidationError

    from evokai.guard.ask import check_rules, explain, parse_rules, stored

    try:
        rules = parse_rules(raw)
    except ValidationError as e:
        raise ConfigError(f"bootstrap: invalid ask rules: {explain(e)}") from e
    if problems := check_rules(rules, set()):
        raise ConfigError(f"bootstrap: invalid ask rules: {'; '.join(problems)}")
    return stored(rules) or None


def _data_protection(raw: Any) -> dict[str, Any] | None:
    """A data protection profile from a config file, validated.

    Returns:
        The profile as stored (None for an empty one).

    Raises:
        ConfigError: An invalid profile.
    """
    from pydantic import ValidationError

    from evokai.protection import DataProtection

    try:
        return DataProtection.model_validate(raw or {}).stored()
    except ValidationError as e:
        raise ConfigError(f"bootstrap: invalid data_protection: {e.errors()[0]['msg']}") from e


def _residency(raw: Any) -> dict[str, Any] | None:
    """A data residency declaration from a config file, validated.

    Returns:
        The declaration as stored (None for an empty one).

    Raises:
        ConfigError: An invalid declaration.
    """
    from pydantic import ValidationError

    from evokai.privacy import Residency

    try:
        return Residency.model_validate(raw or {}).stored()
    except ValidationError as e:
        raise ConfigError(f"bootstrap: invalid residency: {e.errors()[0]['msg']}") from e


async def apply_grants(store: Store, application_id: str, section: dict[str, Any]) -> None:
    """Make the application's grant overrides exactly those of a config section.

    Raises:
        ConfigError: An unknown section, item or value.
    """
    try:
        overrides = from_yaml(section)
    except AppError as e:
        raise ConfigError(f"bootstrap: {e.message}") from e
    if await store.grants.replace(application_id, overrides, "config"):
        log.info("grants_applied", extra={"overrides": len(overrides)})


CAP_KEYS = ("caps", *LEGACY.values())
"""The keys of an application's config that set caps (the new form and the old fields)."""


def _caps(spec: dict[str, Any]) -> dict[str, Any]:
    """The currency, the exchange rate and the caps of an application's config.

    When the config sets any cap (``caps`` or an old ``*_usd`` field), its caps are the
    application's whole set: a cap it does not name is removed (the old monthly cap goes back to
    its default, ``DEFAULT_MONTHLY_USD``). A config without caps leaves the stored ones alone.

    Args:
        spec: The ``application`` section (``currency``, ``exchange_rate``, ``caps``, the old
            ``*_usd`` fields).

    Returns:
        Application fields (``defaults`` holds the subject's old fields).

    Raises:
        ConfigError: A malformed cap, a cap set in both forms, or a currency without its rate.
    """
    out: dict[str, Any] = {}
    if "currency" in spec or "exchange_rate" in spec:
        currency = str(spec.get("currency") or DEFAULT_CURRENCY).upper()
        rate = spec.get("exchange_rate")
        try:
            check_currency(currency, float(rate) if rate is not None else None)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"bootstrap: {e}") from None
        out["currency"] = currency
        out["exchange_rate"] = float(rate) if rate is not None else None
    if not any(spec.get(k) is not None for k in CAP_KEYS):
        return out
    try:
        caps = CapsSpec.model_validate(spec.get("caps") or {})
        old = {k: None if spec.get(k) is None else float(spec[k]) for k in LEGACY.values()}
    except (ValidationError, TypeError, ValueError) as e:
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        raise ConfigError(f"bootstrap: caps: {msg}") from None
    if problems := legacy_conflicts(caps, old):
        raise ConfigError("bootstrap: " + "; ".join(problems))
    if any(v is not None and v < 0 for v in old.values()):
        raise ConfigError("bootstrap: a cap is zero or more")
    out["caps"] = caps.stored()
    out["monthly_cap_usd"] = (
        old["monthly_cap_usd"] if old["monthly_cap_usd"] is not None else DEFAULT_MONTHLY_USD
    )
    out["daily_cap_usd"] = old["daily_cap_usd"]
    out["defaults"] = {k: old[k] for k in ("subject_monthly_usd", "subject_daily_usd")}
    return out


async def ensure_application(store: Store, slug: str, name: str, fields: dict[str, Any]) -> str:
    """Create the application or update its settings.

    Returns:
        Its id.
    """
    fields = dict(fields)
    removed = fields.pop("defaults_removed", [])
    app = await store.applications.by_slug(slug)
    if app is None:
        app = await store.applications.create(slug, name, **fields)
        log.info("application_created", extra={"application": slug})
        return app.id
    if "defaults" in fields or any(k in (app.defaults or {}) for k in removed):
        merged = {**(app.defaults or {}), **(fields.get("defaults") or {})}
        fields = {**fields, "defaults": {k: v for k, v in merged.items() if k not in removed}}
    await store.applications.update(app.id, name=name, **fields)
    return app.id


async def register_keys(store: Store, application_id: str, keys: dict[str, str | None]) -> None:
    """Register keys given in full (by infrastructure); known keys are left as they are.

    Raises:
        ConfigError: A malformed key, or a key that belongs to another application.
    """
    for kind, value in keys.items():
        if not value:
            continue
        parsed = parse_key(value)
        if not parsed or parsed[0] != kind:
            raise ConfigError(f"bootstrap: malformed {kind} key")
        _, prefix, secret = parsed
        known = await store.applications.key_by_prefix(prefix)
        if known is None:
            await store.applications.add_key(
                application_id, kind, prefix, hash_secret(secret), "bootstrap"
            )
        elif known.application_id != application_id:
            raise ConfigError(f"bootstrap: the {kind} key belongs to another application")


async def bootstrap_apps(store: Store, apps: list[BootstrapApp]) -> list[str]:
    """Provision applications from the environment (``EVOKAI_BOOTSTRAP_APPS``).

    Returns:
        Their ids.
    """
    ids = []
    for a in apps:
        existing = await store.applications.by_slug(a.id)
        fields: dict[str, Any] = {}
        if a.origins is not None:
            fields["allowed_origins"] = a.origins
        if a.storageMode:
            fields["storage_mode"] = a.storageMode
        if a.conversationTtlDays is not None:
            fields["conversation_ttl_days"] = a.conversationTtlDays
        if a.securityEventTtlDays is not None:
            fields["security_event_ttl_days"] = a.securityEventTtlDays
        if a.security is not None:
            fields["security"] = _security(a.security)
        if a.residency is not None:
            fields["residency"] = _residency(a.residency)
        if a.dataProtection is not None:
            fields["data_protection"] = _data_protection(a.dataProtection)
        defaults: dict[str, Any] = {}
        if a.voiceEngine:
            defaults["voice_engine"] = a.voiceEngine
        if a.locale:
            defaults["locale"] = a.locale
        if a.timezone:
            defaults["timezone"] = a.timezone
        if a.messages:
            defaults["messages"] = a.messages
        if a.tracingContent:
            defaults["tracing_content"] = a.tracingContent
        spec: dict[str, Any] = {}
        if a.currency is not None:
            spec.update(currency=a.currency, exchange_rate=a.exchangeRate)
        if a.caps:
            # Given caps are the application's whole set, as in a config file.
            c = a.caps
            spec.update(
                monthly_cap_usd=c.monthlyUsd,
                daily_cap_usd=c.dailyUsd,
                subject_monthly_usd=c.subjectMonthlyUsd,
                subject_daily_usd=c.subjectDailyUsd,
                caps={
                    "application": [x.model_dump(exclude_none=True) for x in c.application or []],
                    "subject": [x.model_dump(exclude_none=True) for x in c.subject or []],
                },
            )
        money = _caps(spec)
        defaults.update(money.pop("defaults", {}))
        fields.update(money)
        if defaults:
            fields["defaults"] = defaults
        name = a.name or (existing.name if existing else a.id)
        app_id = await ensure_application(store, a.id, name, fields)
        await register_keys(store, app_id, {"srv": a.serverKey, "adm": a.adminKey})
        if a.grants is not None:
            await apply_grants(store, app_id, a.grants)
        ids.append(app_id)
    return ids


def _skills(p: Path, data: dict[str, Any]) -> list[Skill]:
    """The skills of a config file: folders under ``skills_dir`` and inline ``skills`` entries.

    An inline entry (what the console's export writes) has ``name``, ``description``, ``body`` and
    optionally ``files``, a map of path to text. Both are checked against the limits of
    ``evokai.agents.skills``; a skill over one stops the bootstrap with its name and the rule.

    Returns:
        The skills (an inline one replaces a folder of the same name).

    Raises:
        ConfigError: A skill breaks a limit or a path rule, or an entry is malformed.
    """
    out: dict[str, Skill] = {}
    try:
        if data.get("skills_dir"):
            for skill in skills_in((p.parent / data["skills_dir"]).resolve()):
                out[skill.name] = skill
        for raw in data.get("skills") or []:
            if not isinstance(raw, dict) or not raw.get("name") or not raw.get("description"):
                raise ConfigError("bootstrap: a skill needs name, description and body")
            name = str(raw["name"]).strip()
            description = " ".join(str(raw["description"]).split())
            body = str(raw.get("body") or "").strip()
            files = raw.get("files") or {}
            if not isinstance(files, dict):
                raise ConfigError(f"bootstrap: skill {name}: files is a map of path to text")
            pairs = [(str(k), str(v)) for k, v in sorted(files.items())]
            check_skill(name, description, body, pairs)
            out[name] = Skill(name, description, body, tuple(make_file(k, v) for k, v in pairs))
    except SkillError as e:
        raise ConfigError(f"bootstrap: skill {e}") from None
    return list(out.values())
