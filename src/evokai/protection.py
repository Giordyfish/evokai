"""The data protection profile: which external processors an application's data may reach.

A profile has three independent dimensions, each optional; when several are set they combine (all
must hold):

- ``residency``: where data may be processed, as geographies (``eu``, ``us``...) or ISO countries
  (either matches);
- ``jurisdiction``: who may control the provider: ``eu`` (a company controlled from the EU or EEA)
  or ISO countries;
- ``retention``: what the provider may keep: ``any``, ``no_training`` (inputs never used for
  training) or ``zero_retention`` (nothing kept past the request, and no training).

A fact the provider catalog (``evokai.llm.providers``) does not know never satisfies a rule: a model
whose jurisdiction is unknown is excluded by a jurisdiction rule, with the reason ``unknown``. A
processor that runs inside Evokai (a test model, the local Prompt Guard) sends nothing out and
satisfies every rule.

The profile applies to every external processor Evokai uses for the application: chat models (agent
save, token mint, every call and fallback), embeddings (knowledge and memory), guard classifiers,
server speech, the code sandbox and the OAuth connections; observability exporters are
deployment-wide and reported. What the browser sends to its vendor's online voices or recogniser is
the application's choice, declared in ``browser`` and reported in the compliance pack.

The application's older ``residency`` declaration (``model_regions``, ``models``) keeps working
beside it.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evokai.llm import providers
from evokai.llm.catalog import normalize

RETENTION = ("any", "no_training", "zero_retention")

REMOVED_SOVEREIGNTY = (
    "sovereignty is not a dimension of the data protection profile: remove it and use "
    "residency, jurisdiction and retention"
)
"""The error for a profile that sets ``sovereignty`` (a dimension that was never released)."""


def stored_profile(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """A stored profile without the removed ``sovereignty`` key.

    Returns:
        The profile as a dict, or None when nothing is left.
    """
    if not raw:
        return None
    return {k: v for k, v in raw.items() if k != "sovereignty"} or None


class ResidencyRule(BaseModel):
    """Where data may be processed."""

    model_config = ConfigDict(extra="forbid")
    geographies: list[str] | None = None
    """``eu``, ``us``, ``apac``... (``evokai.llm.providers.GEOGRAPHIES``)."""
    countries: list[str] | None = None
    """ISO 3166-1 alpha-2 codes; a processor whose every processing country is listed passes."""

    @field_validator("geographies")
    @classmethod
    def _geos(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            v = [x.lower() for x in v]
            if bad := [x for x in v if x not in providers.GEOGRAPHIES]:
                raise ValueError(
                    f"unknown geographies: {bad}; use {', '.join(providers.GEOGRAPHIES)}"
                )
        return v

    @field_validator("countries")
    @classmethod
    def _countries(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            v = [x.upper() for x in v]
            if bad := [x for x in v if len(x) != 2 or not x.isalpha()]:
                raise ValueError(f"countries are ISO 3166-1 alpha-2 codes: {bad}")
        return v


class BrowserDeclaration(BaseModel):
    """What the application lets the browser send to its vendor's online services (reported)."""

    model_config = ConfigDict(extra="forbid")
    online_voices: bool = False
    """``allowOnlineVoices``: the answers' text to the browser's online speech service, where no
    local voice."""
    online_recognition: bool = False
    """``allowOnlineRecognition``: the user's audio to the browser's online recogniser, where none
    on the device."""
    vendors: list[str] | None = Field(default=None, max_length=10)
    """The vendors the application lists for them (e.g. Google, Apple), as it declares."""


class DataProtection(BaseModel):
    """An application's data protection profile."""

    model_config = ConfigDict(extra="forbid")
    residency: ResidencyRule | None = None
    jurisdiction: list[str] | None = None
    """``eu`` (controlled from the EU or EEA) and/or ISO country codes; unset: any."""
    retention: Literal["any", "no_training", "zero_retention"] | None = None
    browser: BrowserDeclaration | None = None
    provider_tool_storage: bool | None = None
    """Built-in tools may keep data at the model's provider (``file_search`` with its vector
    stores, ``memory``): off unless set, and never with ``zero_retention``."""

    @model_validator(mode="after")
    def _storage_needs_retention(self) -> DataProtection:
        if self.provider_tool_storage and self.retention == "zero_retention":
            raise ValueError(
                "provider_tool_storage keeps data at the provider: not with zero_retention"
            )
        return self

    @model_validator(mode="before")
    @classmethod
    def _no_sovereignty(cls, data: Any) -> Any:
        if isinstance(data, dict) and "sovereignty" in data:
            raise ValueError(REMOVED_SOVEREIGNTY)
        return data

    @classmethod
    def from_stored(cls, raw: dict[str, Any]) -> DataProtection:
        """A profile as stored, ignoring the removed ``sovereignty`` key.

        Returns:
            The profile.
        """
        return cls.model_validate(stored_profile(raw) or {})

    @field_validator("jurisdiction")
    @classmethod
    def _jurisdiction(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        out = []
        for x in v:
            if x.lower() == "eu":
                out.append("eu")
            elif len(x) == 2 and x.isalpha():
                out.append(x.upper())
            else:
                raise ValueError(f"a jurisdiction is eu or an ISO country code: {x}")
        return out

    def stored(self) -> dict[str, Any] | None:
        """The profile as stored.

        Returns:
            A JSON-able dict, or None when empty.
        """
        return self.model_dump(exclude_none=True) or None

    @property
    def empty(self) -> bool:
        """Whether no dimension is set.

        Returns:
            True when the profile constrains nothing.
        """
        return not (
            self.residency and (self.residency.geographies or self.residency.countries)
        ) and not (self.jurisdiction or (self.retention not in (None, "any")))


@dataclass(frozen=True)
class Reason:
    """Why a processor is excluded: the dimension, what failed (``unknown``), the value."""

    dimension: str
    code: str
    value: Any = None

    def view(self) -> dict[str, Any]:
        """The reason as the API shows it.

        Returns:
            ``{dimension, code, value?}``.
        """
        out: dict[str, Any] = {"dimension": self.dimension, "code": self.code}
        if self.value is not None:
            out["value"] = self.value
        return out


@dataclass
class Verdict:
    """A processor under a profile."""

    allowed: bool
    reasons: list[Reason] = field(default_factory=list)
    facts: providers.Facts | None = None


@dataclass(frozen=True)
class Profile:
    """What constrains an application: the profile and the older residency declaration."""

    rule: DataProtection | None = None
    legacy: dict[str, Any] | None = None

    @property
    def empty(self) -> bool:
        """Whether nothing constrains the application.

        Returns:
            True without a profile or residency rule.
        """
        legacy = self.legacy or {}
        return (self.rule is None or self.rule.empty) and not (
            legacy.get("model_regions") or legacy.get("models")
        )


def profile_of(source: Any) -> Profile:
    """The profile of an application, or of what an older call site holds.

    Args:
        source: An application row (``data_protection``, ``residency``), a dict made by
        :func:`protection`, a bare
            residency dict, or None.

    Returns:
        The profile.
    """
    if source is None:
        return Profile()
    if isinstance(source, Profile):
        return source
    if isinstance(source, dict):
        raw = source.get("data_protection")
        legacy = {k: v for k, v in source.items() if k != "data_protection"} or None
        return Profile(DataProtection.from_stored(raw) if raw else None, legacy)
    raw = getattr(source, "data_protection", None)
    return Profile(
        DataProtection.from_stored(raw) if raw else None, getattr(source, "residency", None)
    )


def protection(app: Any) -> dict[str, Any] | None:
    """An application's residency declaration with its profile, as one dict for the checks.

    Returns:
        The residency keys and ``data_protection``, or None when there is neither.
    """
    if app is None:
        return None
    legacy = dict(getattr(app, "residency", None) or {})
    if dp := getattr(app, "data_protection", None):
        legacy["data_protection"] = dp
    return legacy or None


def _legacy_geography(model: str, aws_region: str) -> str | None:
    """Where a Bedrock model runs by its id alone: a profile's prefix, or the region.

    Returns:
        The geography, or None for other providers.
    """
    name = normalize(model)
    provider, _, ident = name.partition(":")
    if provider != "bedrock":
        return None
    prefix = ident.split(".", 1)[0]
    if prefix in providers.GEOGRAPHIES:
        return prefix
    region = aws_region.lower()
    if region.startswith("ap-"):
        return "apac"
    return region.split("-", 1)[0]


def model_geography(model: str, aws_region: str) -> str | None:
    """Where a model runs: the provider catalog's geography of its endpoint, else Bedrock's id.

    Returns:
        ``eu``, ``us``, ``apac``, ``global``..., or None when nobody states it.
    """
    cat = providers.current()
    facts = cat.model_facts(model) if cat.aws_region == aws_region else _facts_in(model, aws_region)
    if facts is not None and facts.geography is not None:
        return facts.geography
    return _legacy_geography(model, aws_region)


def _facts_in(model: str, aws_region: str) -> providers.Facts | None:
    cat = providers.current()
    return providers.ProviderCatalog(cat.data, aws_region, cat.eur_usd).model_facts(model)


def _legacy_reasons(legacy: dict[str, Any] | None, model: str, aws_region: str) -> list[Reason]:
    if not legacy:
        return []
    regions = [str(x).lower() for x in legacy.get("model_regions") or []]
    patterns = [str(x) for x in legacy.get("models") or []]
    if not regions and not patterns:
        return []
    geo = model_geography(model, aws_region)
    if geo is not None and geo in regions:
        return []
    name = normalize(model)
    if any(fnmatch.fnmatchcase(name, normalize(p)) for p in patterns):
        return []
    return [Reason("residency", "unknown" if geo is None else "outside", geo)]


def check(rule: DataProtection | None, facts: providers.Facts | None) -> list[Reason]:
    """Every rule of a profile a processor's facts break (or cannot show they keep).

    Returns:
        The reasons; none when the processor is allowed.
    """
    if rule is None or rule.empty:
        return []
    if facts is not None and facts.in_process:
        return []
    out: list[Reason] = []
    res = rule.residency
    if res and (res.geographies or res.countries):
        geo = facts.geography if facts else None
        countries = facts.countries if facts else None
        by_geo = bool(res.geographies and geo in res.geographies)
        by_country = bool(res.countries and countries and set(countries) <= set(res.countries))
        if not (by_geo or by_country):
            known = geo is not None if res.geographies else bool(countries)
            if res.geographies and res.countries:
                known = geo is not None or bool(countries)
            out.append(Reason("residency", "outside" if known else "unknown", geo or countries))
    if rule.jurisdiction:
        j = facts.jurisdiction if facts else None
        if j is None:
            out.append(Reason("jurisdiction", "unknown"))
        elif j not in rule.jurisdiction:
            out.append(
                Reason("jurisdiction", "outside", facts.entity.controlled_from if facts else None)
            )
    if rule.retention in ("no_training", "zero_retention"):
        terms = facts.terms if facts else None
        training = terms.training if terms else None
        if training is None:
            out.append(Reason("retention", "unknown", "training"))
        elif training:
            out.append(Reason("retention", "training"))
        if rule.retention == "zero_retention":
            kept = terms.retention if terms else None
            if kept is None:
                out.append(Reason("retention", "unknown", "retention"))
            elif kept != "none":
                out.append(Reason("retention", "retained", kept))
    return out


def model_verdict(source: Any, model: str, aws_region: str) -> Verdict:
    """A model under an application's profile and residency declaration.

    Args:
        source: The application, or :func:`protection`'s dict, or a residency dict.
        model: ``provider:model``.
        aws_region: The deployment's region.

    Returns:
        The verdict, with the model's facts.
    """
    prof = profile_of(source)
    cat = providers.current()
    facts = cat.model_facts(model) if cat.aws_region == aws_region else _facts_in(model, aws_region)
    reasons = _legacy_reasons(prof.legacy, model, aws_region) + check(prof.rule, facts)
    return Verdict(not reasons, reasons, facts)


def residency_allows(residency: Any, model: str, aws_region: str) -> bool:
    """Whether an application's profile and residency declaration let a model answer.

    Args:
        residency: :func:`protection`'s dict (or an older bare residency dict, or None).
        model: ``provider:model``.
        aws_region: The deployment's region.

    Returns:
        True when every rule holds.
    """
    return model_verdict(residency, model, aws_region).allowed


def processor_verdict(source: Any, pid: str, *, region: str | None = None) -> Verdict:
    """A processor of the catalog (a classifier, the sandbox, speech, a connection) under a profile.

    Returns:
        The verdict, with the facts (an unknown processor fails every rule that is set).
    """
    prof = profile_of(source)
    facts = providers.current().processor_facts(pid, region=region)
    reasons = check(prof.rule, facts)
    return Verdict(not reasons, reasons, facts)


def processor_allows(source: Any, pid: str, *, region: str | None = None) -> bool:
    """Whether a profile lets data reach a processor.

    Returns:
        True when every rule holds.
    """
    return processor_verdict(source, pid, region=region).allowed


def facts_view(facts: providers.Facts | None) -> dict[str, Any] | None:
    """A processor's facts as the API and the compliance pack show them (no prices).

    Returns:
        The facts, or None when unknown.
    """
    return facts.view() if facts is not None else None


def verdict_view(name: str, kind: str, v: Verdict) -> dict[str, Any]:
    """One processor and its verdict, for the API.

    Returns:
        ``{name, kind, allowed, reasons, facts}``.
    """
    return {
        "name": name,
        "kind": kind,
        "allowed": v.allowed,
        "reasons": [r.view() for r in v.reasons],
        "facts": facts_view(v.facts),
    }


CONNECTIONS = {
    "microsoft365": "connection:microsoft",
    "google": "connection:google",
    "github": "connection:github",
    "linear": "connection:linear",
    "notion": "connection:notion",
    "slack": "connection:slack",
}
"""Connection providers (``evokai.connections``) and their entries in the provider catalog."""


async def report(svc: Any, app: Any) -> dict[str, Any]:
    """Every external processor the application may use, and whether its profile allows each.

    Models (the catalog's priced ones, the agents' own, the allow-list's), embeddings, guard
    classifiers, the code sandbox, server speech, connections and the observability exporter; what
    the browser may send is the application's declaration.

    Returns:
        profile, residency, empty, processors (``verdict_view`` each), browser.
    """
    from evokai.guard.classifiers import CLASSIFIER_IDS, MODERATION_IDS, PROCESSORS
    from evokai.llm.prices import known_models

    st = svc.settings
    region = st.aws_region
    prof = protection(app)
    rule = profile_of(app).rule
    out: list[dict[str, Any]] = []

    agents = await svc.store.agents.active_all(app.id)
    used: set[str] = set()
    for a in agents:
        d = a.definition
        used |= {
            normalize(x)
            for x in (d.get("model"), *(d.get("fallbacks") or []), *(d.get("models") or []))
            if x
        }
    allowed = {normalize(p) for p in app.allowed_models or [] if not any(c in p for c in "*?[")}
    for name in sorted(set(known_models()) | used | allowed):
        if not svc.catalog.allowed(name):
            continue
        row = verdict_view(name, "model", model_verdict(prof, name, region))
        row["usedBy"] = sorted(a.name for a in agents if name in _models_of(a.definition))
        out.append(row)

    knowledge = getattr(svc, "knowledge", None)
    embeddings = getattr(knowledge, "embeddings", None)
    names = set(embeddings.known()) if embeddings is not None else set()
    names |= {normalize(st.embedding_model), normalize(st.memory_embedding_model)}
    for name in sorted(names):
        if name.startswith("test:"):
            continue
        out.append(verdict_view(name, "embeddings", model_verdict(prof, name, region)))

    for cid in dict.fromkeys((*CLASSIFIER_IDS, *MODERATION_IDS)):
        pid = PROCESSORS.get(cid, cid)
        r = (st.guardrail_region or region) if cid == "bedrock_guardrails" else None
        row = verdict_view(cid, "classifier", processor_verdict(prof, pid, region=r))
        clf = svc.guard.classifiers.get(cid) or svc.guard.classifiers.get_moderation(cid)
        row["configured"] = bool(clf is not None and clf.status().available)
        row["processor"] = pid
        out.append(row)

    code = getattr(svc, "code", None)
    if st.code_enabled:
        r = getattr(code, "region", None) or st.code_region or region
        out.append(
            verdict_view(
                "agentcore-code-interpreter",
                "sandbox",
                processor_verdict(prof, "agentcore-code-interpreter", region=r),
            )
        )
    if st.voice_enabled:
        for kind, engine in (("stt", st.voice_stt), ("tts", st.voice_tts)):
            out.append(
                verdict_view(f"{kind}:{engine}", kind, processor_verdict(prof, f"{kind}:{engine}"))
            )
    for provider, pid in CONNECTIONS.items():
        row = verdict_view(provider, "connection", processor_verdict(prof, pid))
        row["processor"] = pid
        out.append(row)
    if st.otel_enabled:
        out.append(
            verdict_view(
                "observability-exporter",
                "observability",
                processor_verdict(prof, "observability-exporter"),
            )
        )
    from evokai.engine.builtin_tools import STORING, storage_allowed

    builtin = [
        {
            "agent": a.name,
            "kind": str(t.get("kind")),
            "keepsData": t.get("kind") in STORING,
            "allowed": t.get("kind") not in STORING or storage_allowed(prof),
        }
        for a in agents
        for t in a.definition.get("builtin_tools") or []
    ]
    return {
        "profile": stored_profile(app.data_protection),
        "residency": app.residency,
        "empty": profile_of(app).empty,
        "processors": out,
        "browser": rule.browser.model_dump() if rule and rule.browser else None,
        "builtinTools": builtin,
    }


def _models_of(d: dict[str, Any]) -> set[str]:
    return {
        normalize(x)
        for x in (d.get("model"), *(d.get("fallbacks") or []), *(d.get("models") or []))
        if x
    }
