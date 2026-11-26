"""The provider catalog: who processes a call, where, and under whose control.

``providers.yaml`` next to this module is the versioned, built-in catalog: per provider its legal
entity and who controls it, its API terms (training on API data, retention, zero data retention),
its caching style, and per endpoint (a Bedrock inference profile, an Azure deployment type, a Vertex
location, an EU provider's base URL) the models it offers and where processing happens. Every entry
carries the official pages it was read from (``sources``) and when (``checked``); a fact nobody
could verify is ``null`` and stays unknown, never guessed. ``processors`` in the same file describes
what Evokai calls besides chat models (guard classifiers, the code sandbox, speech, connections,
observability exporters).

A deployment adds or overrides entries with ``EVOKAI_PROVIDERS_FILE`` (the same shape): its own
endpoints (an Azure resource with its deployment type, a Vertex location, a self-hosted vLLM or
Ollama with the residency and jurisdiction its operator declares), facts its contract changes (an
approved zero data retention), its own prices. Overrides are merged by ``id``: a provider's fields
replace the built-in ones, an endpoint with a known ``id`` replaces it, a new endpoint is tried
before the built-in ones.

The data protection profile (``evokai.protection``) reads these facts; nothing here decides what is
allowed.
"""

from __future__ import annotations

import fnmatch
import functools
import logging
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evokai.core.errors import ConfigError

log = logging.getLogger("evokai.llm")

BUILT_IN = Path(__file__).with_name("providers.yaml")

EU_EEA = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
    "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO",
})  # fmt: skip
"""The EU member states and the three other EEA countries (ISO 3166-1 alpha-2). ``eu`` in a
jurisdiction rule means these: GDPR applies in all of them."""

GEOGRAPHIES = ("eu", "us", "apac", "us-gov", "ca", "jp", "au", "sa", "me", "af", "global")
"""Processing geographies a residency rule names (``eu`` is Europe as the providers name it: a
provider's EU zone may include Switzerland or the United Kingdom, which a ``countries`` rule can
exclude)."""

AWS_REGIONS: dict[str, tuple[str, str]] = {
    "eu-north-1": ("eu", "SE"),
    "eu-central-1": ("eu", "DE"),
    "eu-central-2": ("eu", "CH"),
    "eu-west-1": ("eu", "IE"),
    "eu-west-2": ("eu", "GB"),
    "eu-west-3": ("eu", "FR"),
    "eu-south-1": ("eu", "IT"),
    "eu-south-2": ("eu", "ES"),
    "us-east-1": ("us", "US"),
    "us-east-2": ("us", "US"),
    "us-west-1": ("us", "US"),
    "us-west-2": ("us", "US"),
    "us-gov-west-1": ("us-gov", "US"),
    "us-gov-east-1": ("us-gov", "US"),
    "ca-central-1": ("ca", "CA"),
    "ap-northeast-1": ("apac", "JP"),
    "ap-south-1": ("apac", "IN"),
    "ap-southeast-1": ("apac", "SG"),
    "ap-southeast-2": ("apac", "AU"),
    "ap-northeast-2": ("apac", "KR"),
    "sa-east-1": ("sa", "BR"),
}
"""AWS regions and where they are
(https://docs.aws.amazon.com/global-infrastructure/latest/regions/aws-regions.html), for the
in-region models and the services that run in the deployment's region."""

Retention = Literal["none", "abuse_monitoring", "stored"]
ZeroRetention = Literal["default", "on_request", "no"]
CachingStyle = Literal["explicit", "automatic", "implicit_and_explicit", "none"]


class _Facts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str] = Field(default_factory=list)
    """The official pages the facts were read from."""


class Entity(_Facts):
    """Who operates the service and who controls that company."""

    name: str | None = None
    country: str | None = None
    """Where the company (or its ultimate parent) is headquartered, ISO 3166-1 alpha-2."""
    controlled_from: str | None = None
    """Who controls it: an ISO country code, or ``EU`` for a group owned across member states."""
    eu_contracting_entity: str | None = None
    """The company a customer in the EU contracts with, when stated."""

    @field_validator("country", "controlled_from")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class Terms(_Facts):
    """What the API terms let the provider do with a request."""

    training: bool | None = None
    """Inputs and outputs are used to train models by default."""
    retention: Retention | None = None
    """``none`` (nothing kept past the request), ``abuse_monitoring`` (kept for a while for safety
    review), ``stored`` (kept for other purposes)."""
    retention_days: int | None = None
    zero_retention: ZeroRetention | None = None
    """``default`` (nothing kept unless asked), ``on_request`` (by approval or contract), ``no``."""
    notes: str | None = None


class Caching(_Facts):
    """How the provider caches a prompt's prefix."""

    style: CachingStyle | None = None
    """``explicit`` (the request marks cache points), ``automatic`` (the provider caches by itself
    and reports it), ``implicit_and_explicit`` (both), ``none``."""
    reports_usage: bool | None = None
    notes: str | None = None


class Place(BaseModel):
    """Where processing happens."""

    model_config = ConfigDict(extra="forbid")
    geography: str | None = None
    countries: list[str] | None = None
    regions: list[str] | None = None
    from_deployment_region: bool = False
    """The deployment's own AWS region (``EVOKAI_AWS_REGION``): Bedrock in-region model ids,
    AgentCore."""

    @field_validator("countries")
    @classmethod
    def _upper(cls, v: list[str] | None) -> list[str] | None:
        return [x.upper() for x in v] if v is not None else v


class Price(BaseModel):
    """A list price, per million tokens (or per unit for a classifier)."""

    model_config = ConfigDict(extra="forbid")
    models: list[str] = Field(default_factory=list)
    """Globs over ``provider:model`` (models) or nothing (a processor's own price)."""
    endpoints: list[str] | None = None
    """Only for models served by these endpoints (their ``id`` or the ``like`` they copy)."""
    currency: Literal["USD", "EUR"] = "USD"
    unit: str = "per_million_tokens"
    input: float = Field(ge=0)
    output: float = Field(default=0, ge=0)
    cache_read: float | None = Field(default=None, ge=0)
    cache_write: float | None = Field(default=None, ge=0)
    cache_write_1h: float | None = Field(default=None, ge=0)
    notes: str | None = None
    sources: list[str] = Field(default_factory=list)
    checked: date | None = None


Api = Literal[
    "bedrock",
    "openai-compatible",
    "azure",
    "anthropic-foundry",
    "google-vertex",
    "anthropic",
    "mistral",
    "pydantic-ai",
]


class Endpoint(BaseModel):
    """One way to reach a provider: a region, a data zone, a base URL, a deployment type."""

    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str | None = None
    how_to_select: str | None = None
    models: list[str] = Field(default_factory=list)
    """Globs over ``provider:model`` this endpoint serves (the first endpoint that matches a model
    is its)."""
    exclude: list[str] = Field(default_factory=list)
    """Globs over ``provider:model`` it does not serve, though ``models`` matches them."""
    like: str | None = None
    """A built-in endpoint of the same provider whose facts this one copies (a deployment's Azure
    resource ``like: azure-datazone-eu``); what this one states replaces what it copies."""
    price_factor: float = Field(default=1.0, gt=0, le=10)
    """The provider's surcharge on its list prices here (Mistral's and OpenAI's regional endpoints:
    1.1), applied to prices that are not stated for this endpoint."""
    offered: list[str] | None = None
    """The main models the provider lists here (ids as its API takes them), for people only."""
    place: Place = Field(default_factory=Place)
    data_at_rest: str | None = None
    entity: Entity | None = None
    terms: Terms | None = None
    notes: str | None = None
    sources: list[str] = Field(default_factory=list)
    checked: date | None = None
    declared_by: Literal["catalog", "operator"] = "catalog"
    """``operator``: the facts are the deployment's own declaration (a self-hosted server, a
    contract)."""
    # How Evokai connects (a deployment's own endpoints)
    api: Api | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_version: str | None = None
    location: str | None = None
    project: str | None = None
    region: str | None = None


class Provider(BaseModel):
    """A model provider: its company, terms, caching and endpoints."""

    model_config = ConfigDict(extra="forbid")
    id: str
    """Also the model name prefix (``mistral:mistral-large-latest``)."""
    title: str
    self_hosted: bool = False
    """Runs on the operator's own machines: residency and jurisdiction are what the operator
    declares."""
    entity: Entity = Field(default_factory=Entity)
    terms: Terms = Field(default_factory=Terms)
    caching: Caching = Field(default_factory=Caching)
    aliases: list[str] = Field(default_factory=list)
    """More model name prefixes of this provider, each an endpoint of its own (``mistral-eu:``)."""
    endpoints: list[Endpoint] = Field(default_factory=list)
    prices: list[Price] = Field(default_factory=list)
    genai_prices: str | None = None
    """The provider's id in ``genai-prices`` (prices of the models the catalog does not price
    itself)."""
    notes: str | None = None
    sources: list[str] = Field(default_factory=list)
    checked: date | None = None
    unknowns: list[str] = Field(default_factory=list)
    """Facts that could not be verified from an official source, said in words."""


ProcessorKind = Literal[
    "embeddings", "classifier", "sandbox", "stt", "tts", "connection", "observability", "other"
]


class Processor(BaseModel):
    """Something besides a chat model that receives data: a classifier, the sandbox, speech."""

    model_config = ConfigDict(extra="forbid")
    id: str
    kind: ProcessorKind
    title: str
    provider: str | None = None
    """A provider of the catalog whose entity and terms this processor shares unless it states its
    own."""
    detects: Literal["injection", "moderation", "both"] | None = None
    """A classifier's job: prompt injection, harmful content (moderation is not injection
    detection), or both."""
    api: str | None = None
    place: Place = Field(default_factory=Place)
    entity: Entity | None = None
    terms: Terms | None = None
    prices: list[Price] = Field(default_factory=list)
    notes: str | None = None
    sources: list[str] = Field(default_factory=list)
    checked: date | None = None
    declared_by: Literal["catalog", "operator"] = "catalog"
    unknowns: list[str] = Field(default_factory=list)


class CatalogFile(BaseModel):
    """The whole catalog (built-in or a deployment's additions)."""

    model_config = ConfigDict(extra="forbid")
    version: int = 1
    checked: date | None = None
    providers: list[Provider] = Field(default_factory=list)
    processors: list[Processor] = Field(default_factory=list)


class Facts(BaseModel):
    """The resolved facts of one model or processor: the provider's, with its endpoint's on top."""

    provider: str
    title: str
    endpoint: str | None = None
    kind: str = "model"
    self_hosted: bool = False
    in_process: bool = False
    """Runs inside Evokai (a test model, a local classifier): no data leaves the process."""
    geography: str | None = None
    countries: list[str] | None = None
    regions: list[str] | None = None
    entity: Entity = Field(default_factory=Entity)
    terms: Terms = Field(default_factory=Terms)
    caching: Caching = Field(default_factory=Caching)
    declared_by: str = "catalog"
    sources: list[str] = Field(default_factory=list)
    checked: date | None = None

    def view(self) -> dict[str, Any]:
        """The facts as the API and the compliance pack show them (no prices).

        Returns:
            provider, endpoint, place, entity, jurisdiction, terms, who declared them, sources.
        """
        t = self.terms
        facts = self
        return {
            "provider": facts.provider,
            "title": facts.title,
            "endpoint": facts.endpoint,
            "kind": facts.kind,
            "inProcess": facts.in_process,
            "selfHosted": facts.self_hosted,
            "geography": facts.geography,
            "countries": facts.countries,
            "regions": facts.regions,
            "entity": facts.entity.name,
            "controlledFrom": facts.entity.controlled_from,
            "jurisdiction": facts.jurisdiction,
            "training": t.training,
            "retention": t.retention,
            "retentionDays": t.retention_days,
            "zeroRetention": t.zero_retention,
            "declaredBy": facts.declared_by,
            "sources": facts.sources,
            "checked": facts.checked.isoformat() if facts.checked else None,
        }

    @property
    def jurisdiction(self) -> str | None:
        """Who controls the provider, as a jurisdiction.

        ``eu`` for an EU or EEA country (or ``EU``), else the country.

        Returns:
            ``eu``, an ISO country code, or None when unknown.
        """
        c = self.entity.controlled_from
        if not c:
            return None
        return "eu" if c == "EU" or c in EU_EEA else c


def _merge_model(base: BaseModel, over: BaseModel) -> Any:
    """``over``'s explicitly set fields on top of ``base``.

    Returns:
        A new model of ``base``'s type.
    """
    data = base.model_dump()
    data.update(over.model_dump(exclude_unset=True))
    return type(base).model_validate(data)


def _merge(built_in: CatalogFile, extra: CatalogFile) -> CatalogFile:
    providers = {p.id: p for p in built_in.providers}
    for p in extra.providers:
        cur = providers.get(p.id)
        if cur is None:
            providers[p.id] = p
            continue
        merged = _merge_model(cur, p)
        if "endpoints" in p.model_fields_set:
            known = {e.id: e for e in cur.endpoints}
            new = [e for e in p.endpoints if e.id not in known]
            replaced = {e.id: e for e in p.endpoints if e.id in known}
            merged.endpoints = new + [replaced.get(e.id, e) for e in cur.endpoints]
        if "prices" in p.model_fields_set:
            merged.prices = p.prices + cur.prices
        providers[p.id] = merged
    processors = {x.id: x for x in built_in.processors}
    for x in extra.processors:
        processors[x.id] = _merge_model(processors[x.id], x) if x.id in processors else x
    return CatalogFile(
        version=built_in.version,
        checked=built_in.checked,
        providers=list(providers.values()),
        processors=list(processors.values()),
    )


def _read(path: Path) -> CatalogFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return CatalogFile.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise ConfigError(f"provider catalog {path}: {e}") from e


IN_PROCESS = ("test", "function")
"""Model providers that run inside Evokai (tests and the ``test:`` model): no data leaves the
process."""


class ProviderCatalog:
    """The built-in catalog with the deployment's additions; the facts of a model or processor."""

    def __init__(
        self, data: CatalogFile, aws_region: str = "eu-north-1", eur_usd: float = 1.2
    ) -> None:
        """Index the catalog.

        Args:
            data: The merged catalog.
            aws_region: The deployment's AWS region (in-region Bedrock models, AgentCore).
            eur_usd: Dollars per euro for prices stated in euros (``EVOKAI_PRICE_EUR_USD``).
        """
        self.data = data
        self.aws_region = aws_region
        self.eur_usd = eur_usd
        self.providers = {p.id: p for p in data.providers}
        self.by_prefix = {**{a: p for p in data.providers for a in p.aliases}, **self.providers}
        self.processors = {x.id: x for x in data.processors}

    @classmethod
    def load(
        cls, extra: str | Path | None = None, aws_region: str = "eu-north-1", eur_usd: float = 1.2
    ) -> ProviderCatalog:
        """The built-in catalog, with a deployment's file merged on top.

        Returns:
            The catalog.
        """
        data = _built_in()
        if extra:
            data = _merge(data, _read(Path(extra)))
        return cls(data, aws_region, eur_usd)

    def prefixes(self) -> set[str]:
        """Every provider id (the model name prefixes the catalog knows).

        Returns:
            Ids.
        """
        return set(self.by_prefix)

    def provider_of(self, model: str) -> Provider | None:
        """The provider of a model name.

        Returns:
            The provider, or None when the catalog does not know it.
        """
        from evokai.llm.catalog import normalize

        return self.by_prefix.get(normalize(model).partition(":")[0])

    def endpoint_of(self, model: str) -> Endpoint | None:
        """The endpoint a model name is served by: the first of its provider's whose patterns match.

        Returns:
            The endpoint, or None.
        """
        from evokai.llm.catalog import normalize

        p = self.provider_of(model)
        if p is None:
            return None
        name = normalize(model)
        hit = next(
            (
                e
                for e in p.endpoints
                if any(fnmatch.fnmatchcase(name, pat) for pat in e.models)
                and not any(fnmatch.fnmatchcase(name, pat) for pat in e.exclude)
            ),
            None,
        )
        return self._resolved(p, hit) if hit is not None else None

    def _resolved(self, p: Provider, e: Endpoint) -> Endpoint:
        """An endpoint with the facts of the endpoint it is ``like`` filled in.

        Returns:
            The endpoint.

        Raises:
            ConfigError: It is like an endpoint the provider does not have.
        """
        if not e.like:
            return e
        base = next((x for x in p.endpoints if x.id == e.like and x is not e), None)
        if base is None:
            raise ConfigError(
                f"provider catalog: {p.id} endpoint {e.id} is like an unknown endpoint {e.like}"
            )
        data = base.model_dump()
        data.update(e.model_dump(exclude_unset=True))
        data["sources"] = [*base.sources, *e.sources]
        return Endpoint.model_validate(data)

    def _place(
        self, place: Place, region: str | None = None
    ) -> tuple[str | None, list[str] | None, list[str] | None]:
        if place.from_deployment_region:
            r = (region or self.aws_region).lower()
            geo, country = AWS_REGIONS.get(r, (None, None))
            return geo, [country] if country else None, [r]
        return place.geography, place.countries, place.regions

    def model_facts(self, model: str) -> Facts | None:
        """The facts of a model: its provider's, with its endpoint's on top.

        Returns:
            The facts, or None for a provider the catalog does not know.
        """
        from evokai.llm.catalog import normalize

        prefix = normalize(model).partition(":")[0]
        if prefix in IN_PROCESS:
            return Facts(provider=prefix, title="In process", in_process=True, kind="model")
        p = self.provider_of(model)
        if p is None:
            return None
        e = self.endpoint_of(model)
        geo, countries, regions = self._place(e.place, e.region) if e else (None, None, None)
        return Facts(
            provider=p.id,
            title=p.title,
            endpoint=e.id if e else None,
            self_hosted=p.self_hosted,
            geography=geo,
            countries=countries,
            regions=regions,
            entity=_merge_model(p.entity, e.entity) if e and e.entity else p.entity,
            terms=_merge_model(p.terms, e.terms) if e and e.terms else p.terms,
            caching=p.caching,
            declared_by=e.declared_by if e else "catalog",
            sources=[*p.sources, *(e.sources if e else [])],
            checked=(e.checked if e and e.checked else p.checked),
        )

    def processor_facts(self, pid: str, *, region: str | None = None) -> Facts | None:
        """The facts of a processor (a classifier, the sandbox, speech, a connection, an exporter).

        Args:
            pid: The processor's id in the catalog.
            region: Its AWS region, when it runs in one other than the deployment's.

        Returns:
            The facts, or None when the catalog does not know it.
        """
        x = self.processors.get(pid)
        if x is None:
            return None
        p = self.providers.get(x.provider or "")
        geo, countries, regions = self._place(x.place, region)
        entity = p.entity if p else Entity()
        terms = p.terms if p else Terms()
        return Facts(
            provider=x.provider or x.id,
            title=x.title,
            endpoint=x.id,
            kind=x.kind,
            in_process=x.place.geography == "in_process",
            self_hosted=bool(p and p.self_hosted),
            geography=None if geo == "in_process" else geo,
            countries=countries,
            regions=regions,
            entity=_merge_model(entity, x.entity) if x.entity else entity,
            terms=_merge_model(terms, x.terms) if x.terms else terms,
            caching=Caching(),
            declared_by=x.declared_by,
            sources=[*(p.sources if p else []), *x.sources],
            checked=x.checked or (p.checked if p else None),
        )

    def price_entry(self, model: str) -> Price | None:
        """The catalog's own price of a model, when it states one.

        Returns:
            The price, or None (genai-prices or the fallback then apply).
        """
        from evokai.llm.catalog import normalize

        p = self.provider_of(model)
        if p is None:
            return None
        name = normalize(model)
        e = self.endpoint_of(name)
        ids = {e.id, e.like} if e else set()
        return next(
            (
                x
                for x in p.prices
                if any(fnmatch.fnmatchcase(name, pat) for pat in x.models)
                and (x.endpoints is None or ids & set(x.endpoints))
            ),
            None,
        )

    def price_factor(self, model: str) -> float:
        """The surcharge of the endpoint a model is served by, on list prices not stated for it.

        Returns:
            A factor (1 without one).
        """
        e = self.endpoint_of(model)
        return e.price_factor if e else 1.0

    def coverage(self) -> dict[str, int]:
        """How many facts the catalog states and how many it leaves unknown.

        Returns:
            ``{providers, endpoints, processors, rates (list prices stated), known, unknown}``.
        """
        known = unknown = 0

        def count(values: list[Any]) -> None:
            nonlocal known, unknown
            for v in values:
                if v is None:
                    unknown += 1
                else:
                    known += 1

        for p in self.data.providers:
            if p.self_hosted:
                continue
            count([p.entity.name, p.entity.controlled_from, p.terms.training, p.terms.retention])
            count([p.terms.zero_retention, p.caching.style])
            for e in p.endpoints:
                if e.place.from_deployment_region:
                    known += 1
                else:
                    count([e.place.geography])
        for x in self.data.processors:
            if x.declared_by == "operator" or x.place.geography == "in_process":
                continue
            count([x.place.geography if not x.place.from_deployment_region else "aws"])
        return {
            "providers": len(self.data.providers),
            "endpoints": sum(len(p.endpoints) for p in self.data.providers),
            "processors": len(self.data.processors),
            "rates": sum(len(p.prices) for p in self.data.providers),
            "known": known,
            "unknown": unknown,
        }


@functools.cache
def _built_in() -> CatalogFile:
    return _read(BUILT_IN)


_current: ProviderCatalog | None = None


def current() -> ProviderCatalog:
    """The process's provider catalog (the built-in one until the service configures it).

    Returns:
        The catalog.
    """
    global _current
    if _current is None:
        _current = ProviderCatalog(_built_in())
        from evokai.llm.catalog import register_providers

        register_providers(_current.prefixes())
    return _current


def configure(extra: str | Path | None, aws_region: str, eur_usd: float = 1.2) -> ProviderCatalog:
    """Set the process's catalog from the deployment's settings (at start).

    Args:
        extra: ``EVOKAI_PROVIDERS_FILE``.
        aws_region: ``EVOKAI_AWS_REGION``.
        eur_usd: ``EVOKAI_PRICE_EUR_USD``.

    Returns:
        The catalog.
    """
    global _current
    _current = ProviderCatalog.load(extra, aws_region, eur_usd)
    from evokai.llm.catalog import register_providers

    register_providers(_current.prefixes())
    return _current
