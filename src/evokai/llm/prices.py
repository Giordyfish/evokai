"""On-demand prices: model tokens, cached tokens, embeddings and guard classifiers.

Every model call is priced by the model that answered, in millionths of a dollar, in this order:

1. **The provider catalog** (``evokai.llm.providers``, ``prices`` of a provider): Evokai's own list
   prices where they differ from the shared data (Bedrock's EU regions, read from the Stockholm
   list) and the EU providers it lacks; a deployment adds its own in ``EVOKAI_PROVIDERS_FILE``.
   Prices in euros are converted at ``EVOKAI_PRICE_EUR_USD``.
2. **genai-prices** (Pydantic's price data, MIT licensed, the one pydantic-ai prices ``RunUsage``
   with): input, output, cache read and cache write prices (5 minutes and 1 hour) for most
   providers' current models, with long-context tiers. Its bundled snapshot is used as installed: it
   is never updated over the network at run time.
3. **The fallback**: a model nobody prices is priced like an expensive one, never as free, and is
   flagged (``price_of(...).source == "fallback"``, the console's model list, a ``price_unknown``
   log line once per model).

A self-hosted provider of the catalog (``self_hosted: true``: Ollama, vLLM, LM Studio, a
deployment's own server) has no per-token charge. Costs stay on the server: people see shares of the
caps, never these amounts.

Cached tokens: pydantic-ai's convention (and genai-prices') is that input tokens include the ones
read from and written to the cache. A cache price the source does not state is the input price
(never cheaper than it may be), except on Anthropic models, whose cache prices are documented
multiples of the input price on every platform: a read costs a tenth, a write 1.25 times (5 minute
TTL) or twice (1 hour TTL).
"""

from __future__ import annotations

import functools
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from evokai.llm.catalog import bare, normalize, provider_of

log = logging.getLogger("evokai.llm")

FALLBACK_PRICE = (3.3, 16.5)
"""$ per million tokens (input, output) of a model nobody prices: Claude Sonnet's EU list price,
among the highest of the models Evokai is usually given."""

CACHE_READ = 0.1
"""A cache read on an Anthropic model, as a share of the input price."""
CACHE_WRITE_5M = 1.25
"""A cache write with the 5 minute TTL on an Anthropic model, as a multiple of the input price."""
CACHE_WRITE_1H = 2.0
"""A cache write with the 1 hour TTL on an Anthropic model, as a multiple of the input price."""

GENAI_PROVIDERS = {
    "bedrock": "aws",
    "openai": "openai",
    "openai-chat": "openai",
    "openai-responses": "openai",
    "azure": "azure",
    "anthropic": "anthropic",
    "google-gla": "google",
    "google-vertex": "google",
    "google": "google",
    "google-cloud": "google",
    "mistral": "mistral",
    "cohere": "cohere",
    "groq": "groq",
    "deepseek": "deepseek",
    "openrouter": "openrouter",
    "ovhcloud": "ovhcloud",
}
"""Evokai's provider prefixes and genai-prices' provider ids (a catalog provider may name one)."""

Source = Literal["catalog", "genai-prices", "fallback", "free"]


@dataclass(frozen=True)
class ModelPrice:
    """A model's list prices in $ per million tokens, and where they come from."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    cache_write_1h: float
    source: Source
    genai: tuple[str, str] | None = None
    """``(model, provider id)`` in genai-prices, which then prices each call (with its tiers)."""
    factor: float = 1.0
    """The endpoint's surcharge on a list price not stated for it (a regional endpoint's 10%)."""

    @property
    def blended(self) -> float:
        """A list price to order models by (input weighted 3 to 1 over output).

        Returns:
            A number to compare, not an amount anyone sees.
        """
        return (3 * self.input + self.output) / 4


def _anthropic(model: str) -> bool:
    name = bare(model)
    return "anthropic." in name or name.startswith("claude") or "/claude" in name


def _with_cache(
    model: str,
    p_in: float,
    p_out: float,
    read: Any,
    write: Any,
    write_1h: Any,
    source: Source,
    factor: float = 1.0,
    genai: tuple[str, str] | None = None,
) -> ModelPrice:
    if _anthropic(model):
        read = p_in * CACHE_READ if read is None else read
        write = p_in * CACHE_WRITE_5M if write is None else write
        write_1h = p_in * CACHE_WRITE_1H if write_1h is None else write_1h
    read = p_in if read is None else float(read)
    write = p_in if write is None else float(write)
    write_1h = write if write_1h is None else float(write_1h)
    f = factor
    return ModelPrice(
        float(p_in) * f, float(p_out) * f, read * f, write * f, write_1h * f, source, genai, factor
    )


def _genai(model: str, provider_id: str | None) -> Any:
    """genai-prices' model price for a model, or None.

    Returns:
        A ``genai_prices.types.ModelPrice`` (base tier), or None.
    """
    if provider_id is None:
        return None
    try:
        from genai_prices.data_snapshot import get_snapshot

        _, info = get_snapshot().find_provider_model(bare(model), None, provider_id, None)
    except (LookupError, ImportError):
        return None
    try:
        return info.get_prices(datetime.now(UTC))
    except Exception:
        return None


def _base(value: Any) -> float | None:
    """A price field as a number: the base of a tiered price.

    Returns:
        $ per million, or None.
    """
    if value is None:
        return None
    base = getattr(value, "base", value)
    return float(base)


def _providers() -> Any:
    from evokai.llm import providers

    return providers.current()


@functools.lru_cache(maxsize=2048)
def _price_of(model: str, catalog_id: int) -> ModelPrice:  # ruff: ignore[unused-function-argument] (the cache key)
    cat = _providers()
    name = normalize(model)
    provider = cat.provider_of(name)
    if provider is not None and provider.self_hosted:
        return ModelPrice(0, 0, 0, 0, 0, "free")
    factor = cat.price_factor(name)
    entry = cat.price_entry(name)
    if entry is not None:
        rate = cat.eur_usd if entry.currency == "EUR" else 1.0

        def usd(v: float | None) -> float | None:
            return None if v is None else v * rate

        return _with_cache(
            name,
            entry.input * rate,
            entry.output * rate,
            usd(entry.cache_read),
            usd(entry.cache_write),
            usd(entry.cache_write_1h),
            "catalog",
            factor if entry.endpoints is None else 1.0,
        )
    genai_id = (provider.genai_prices if provider else None) or GENAI_PROVIDERS.get(
        provider_of(name)
    )
    mp = _genai(name, genai_id)
    if mp is not None and _base(getattr(mp, "input_mtok", None)) is not None:
        return _with_cache(
            name,
            _base(mp.input_mtok) or 0.0,
            _base(getattr(mp, "output_mtok", None)) or 0.0,
            _base(getattr(mp, "cache_read_mtok", None)),
            _base(getattr(mp, "cache_write_mtok", None)),
            _base(getattr(mp, "cache_write_1h_mtok", None)),
            "genai-prices",
            factor,
            (bare(name), str(genai_id)),
        )
    if provider_of(name) not in ("test", "function"):
        log.warning("price_unknown", extra={"model": name})
    return _with_cache(name, *FALLBACK_PRICE, None, None, None, "fallback")


def price_of(model: str) -> ModelPrice:
    """A model's list prices and their source.

    Returns:
        The price (the fallback, flagged, when nobody prices the model).
    """
    return _price_of(normalize(model), id(_providers()))


def cost_micro_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_write_1h_tokens: int = 0,
) -> int:
    """Cost of a call in millionths of a dollar.

    Args:
        model: The model that answered.
        input_tokens: Every input token, cached ones included (pydantic-ai's convention).
        output_tokens: Output tokens.
        cache_read_tokens: Input tokens read from the cache.
        cache_write_tokens: Input tokens written to the cache (both TTLs).
        cache_write_1h_tokens: The part of ``cache_write_tokens`` written with the 1 hour TTL.

    Returns:
        The cost, rounded up.
    """
    p = price_of(model)
    if p.source == "free":
        return 0
    read = max(cache_read_tokens, 0)
    write = max(cache_write_tokens, 0)
    write_1h = min(max(cache_write_1h_tokens, 0), write)
    fresh = max(input_tokens - read - write, 0)
    if p.genai is not None:
        exact = _genai_cost(p, fresh + read + write, max(output_tokens, 0), read, write, write_1h)
        if exact is not None:
            return exact
    # Rounded to a millionth first: float noise must not add one to an exact cost.
    return math.ceil(
        round(
            fresh * p.input
            + read * p.cache_read
            + (write - write_1h) * p.cache_write
            + write_1h * p.cache_write_1h
            + max(output_tokens, 0) * p.output,
            6,
        )
    )


def _genai_cost(
    p: ModelPrice, input_tokens: int, output_tokens: int, read: int, write: int, write_1h: int
) -> int | None:
    """A call priced by genai-prices (its long-context tiers apply), with the endpoint's surcharge.

    Returns:
        Millionths of a dollar, rounded up, or None when genai-prices cannot price it.
    """
    from genai_prices import Usage, calc_price

    assert p.genai is not None  # ruff: ignore[assert] (checked by the caller)
    model, provider_id = p.genai
    usage: dict[str, int] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if read:
        usage["cache_read_tokens"] = read
    if write:
        usage["cache_write_tokens"] = write
        if write_1h:
            usage["cache_write_1h_tokens"] = write_1h
    try:
        calc = calc_price(Usage(**usage), model, provider_id=provider_id)
    except Exception:
        return None
    return math.ceil(round(float(calc.total_price) * 1e6 * p.factor, 6))


def call_cost_micro_usd(model: str, usage: Any) -> int:
    """Cost of a call from pydantic-ai's request usage.

    Args:
        model: The model that answered.
        usage: A ``RequestUsage`` (input, output and cache tokens).

    Returns:
        The cost, rounded up.
    """
    return cost_micro_usd(
        model,
        usage.input_tokens or 0,
        usage.output_tokens or 0,
        cache_read_tokens=usage.cache_read_tokens or 0,
        cache_write_tokens=usage.cache_write_tokens or 0,
        cache_write_1h_tokens=getattr(usage, "cache_write_1h_tokens", 0) or 0,
    )


def estimate_call_usd(model: str) -> float:
    """A pessimistic estimate of one call, to refuse a step the budget can no longer pay for.

    Returns:
        The estimate in USD.
    """
    return cost_micro_usd(model, 20_000, 1_200) / 1e6


def known_models() -> list[str]:
    """The models the provider catalog prices itself (for the console's model list).

    Returns:
        ``provider:model`` names without wildcards.
    """
    out = []
    for p in _providers().data.providers:
        for entry in p.prices:
            out += [normalize(m) for m in entry.models if not any(c in m for c in "*?[")]
    return sorted(set(out))


GUARDRAIL_TEXT_UNIT_MICRO_USD = 150
"""Amazon Bedrock Guardrails content filters (the prompt attack filter among them): $0.15 per 1,000
text units, a text unit being up to 1,000 characters; the same for the Standard and Classic tiers
(Bedrock price list, read 15/11/2026)."""


def guardrail_cost_micro_usd(units: int) -> int:
    """Cost of Bedrock Guardrails text units in millionths of a dollar.

    Returns:
        The cost.
    """
    return max(units, 0) * GUARDRAIL_TEXT_UNIT_MICRO_USD


def classifier_cost_micro_usd(processor: str, units: int) -> int:
    """Cost of a guard classifier's billed units (records, characters or tokens, as priced).

    Args:
        processor: The classifier's id in the provider catalog.
        units: The units the call was billed for (in the price's unit).

    Returns:
        The cost in millionths of a dollar (0 for a free or self-hosted classifier).
    """
    cat = _providers()
    x = cat.processors.get(processor)
    if x is None or not x.prices or units <= 0:
        return 0
    p = x.prices[0]
    rate = cat.eur_usd if p.currency == "EUR" else 1.0
    per_unit = p.input * rate / _unit_size(p.unit)
    return math.ceil(round(units * per_unit * 1e6, 6))


def _unit_size(unit: str) -> float:
    """How many billed units a price is quoted for.

    Returns:
        1,000 for ``per_1000_*``, a million for ``per_million_*``, else 1.
    """
    if unit.startswith("per_1000_"):
        return 1_000
    if unit.startswith("per_million_"):
        return 1_000_000
    return 1


EMBEDDING_PRICES: dict[str, float] = {
    "amazon.titan-embed-text-v2:0": 0.022,
    "cohere.embed-multilingual-v3": 0.11,
}
"""Embedding models in $ per million input tokens. Titan Text Embeddings V2 lists $0.02 in the US
regions; the EU price is taken 10% higher until the Stockholm list is read. An unknown model is
priced at the highest of these, never as free."""


def embedding_cost_micro_usd(model: str, tokens: int) -> int:
    """Cost of embedding calls in millionths of a dollar.

    Returns:
        The cost, rounded up.
    """
    name = normalize(model)
    if name.startswith("test:"):
        return 0
    p = price_of(name) if _providers().price_entry(name) or _self_hosted(name) else None
    if p is not None:
        return math.ceil(max(tokens, 0) * p.input)
    price = EMBEDDING_PRICES.get(bare(model), max(EMBEDDING_PRICES.values()))
    return math.ceil(max(tokens, 0) * price)


def _self_hosted(model: str) -> bool:
    p = _providers().provider_of(model)
    return bool(p and p.self_hosted)
