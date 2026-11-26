"""«The cheapest model that passes» and prompt-cache suggestions (shares only).

**Models.** An agent's eval cases run on its current model and on the cheaper models it may use
(the deployment's allow-list, the application's allowed models, the data residency and the model
grants, as primary). Each model gets its pass rate and its measured spend on the same cases; the
recommendation is the cheapest model whose pass rate is within ``tolerance`` points of the
current one. Cost is shown as a percentage of the current model's (``relativeCostPct``), never as
an amount.

**Prompt cache.** An agent on an Anthropic model on Bedrock with ``prompt_cache: off`` whose stable
prompt (instructions, preloaded skills, the list of skills) is long enough to be cached is
flagged, with the share of its input tokens a cache read would make ten times cheaper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evokai.llm.caching import anthropic_family
from evokai.llm.catalog import bare, normalize, provider_of
from evokai.llm.prices import price_of

DEFAULT_TOLERANCE = 5.0
"""Pass-rate points a cheaper model may lose and still pass."""
MAX_CANDIDATES = 4
"""Cheaper models tried besides the current one."""
MAX_CASES = 20
"""Cases a model comparison runs on each model."""
CHARS_PER_TOKEN = 3.5


def list_price(model: str) -> float:
    """A blended list price to order models by (input weighted 3 to 1 over output).

    Returns:
        A number to compare, not an amount anyone sees.
    """
    return price_of(model).blended


def pick_candidates(current: str, pool: list[str]) -> list[str]:
    """The models worth trying: the ones whose list price is below the current model's.

    Returns:
        Up to ``MAX_CANDIDATES`` models, cheapest first.
    """
    seen = {bare(current)}
    out = []
    for m in sorted(pool, key=list_price):
        if bare(m) in seen or list_price(m) >= list_price(current):
            continue
        seen.add(bare(m))
        out.append(m)
    return out[:MAX_CANDIDATES]


@dataclass(frozen=True)
class ModelResult:
    """One model's figures over the same cases."""

    model: str
    pass_rate: float | None
    cost_micro_usd: int
    latency_ms: int | None
    cases: int
    errors: int


def recommend(
    current: ModelResult, others: list[ModelResult], tolerance: float = DEFAULT_TOLERANCE
) -> dict[str, Any]:
    """Compare models with the current one and pick the cheapest that passes.

    Returns:
        ``models`` (each with passRate, passRateDelta, relativeCostPct, latencyMs, passes,
        current) cheapest first, and ``recommended`` (a model, or None when no cheaper model
        passes).
    """

    def relative(r: ModelResult) -> float | None:
        # A model that failed every case has no cost worth comparing (it never answered).
        if current.cost_micro_usd <= 0 or (r.cases and r.errors >= r.cases):
            return None
        return round(r.cost_micro_usd / current.cost_micro_usd * 100, 1)

    rows: list[dict[str, Any]] = []
    for r in [current, *others]:
        delta = (
            None
            if r.pass_rate is None or current.pass_rate is None
            else round(r.pass_rate - current.pass_rate, 1)
        )
        passes = r is current or (
            delta is not None and delta >= -tolerance and r.errors < max(1, r.cases)
        )
        rows.append({
            "model": r.model,
            "passRate": r.pass_rate,
            "passRateDelta": delta,
            "relativeCostPct": 100.0 if r is current else relative(r),
            "latencyMs": r.latency_ms,
            "cases": r.cases,
            "errors": r.errors,
            "passes": passes,
            "current": r is current,
        })
    rows.sort(key=lambda x: (x["relativeCostPct"] is None, x["relativeCostPct"] or 0))
    cheaper = [
        x
        for x in rows
        if not x["current"]
        and x["passes"]
        and x["relativeCostPct"] is not None
        and x["relativeCostPct"] < 100
    ]
    best = cheaper[0]["model"] if cheaper else None
    for x in rows:
        x["recommended"] = x["model"] == best
    return {"models": rows, "recommended": best, "tolerance": tolerance}


MIN_CACHE_TOKENS = {"haiku": 4096}
"""The shortest prefix a model caches (Claude Haiku 4.5: 4,096 tokens); others: 1,024."""


def min_cache_tokens(model: str) -> int:
    """The shortest cacheable prefix of a model.

    Returns:
        Tokens.
    """
    name = bare(model).lower()
    return next((v for k, v in MIN_CACHE_TOKENS.items() if k in name), 1024)


def cache_suggestion(
    *,
    agent: str,
    model: str,
    prompt_cache: str,
    stable_chars: int,
    avg_input_tokens: float | None,
) -> dict[str, Any] | None:
    """Whether an agent would gain from prompt caching.

    Returns:
        The suggestion (agent, model, stableTokens, minTokens, inputSharePct: the share of an
        average call's input that is the stable prompt) or None.
    """
    if prompt_cache != "off":
        return None
    name = normalize(model)
    if provider_of(name) not in (
        "bedrock",
        "anthropic",
        "anthropic-foundry",
    ) or not anthropic_family(name):
        return None
    tokens = round(stable_chars / CHARS_PER_TOKEN)
    minimum = min_cache_tokens(model)
    if tokens < minimum:
        return None
    share = None
    if avg_input_tokens:
        share = round(min(tokens, avg_input_tokens) / avg_input_tokens * 100, 1)
    return {
        "agent": agent,
        "model": model,
        "stableTokens": tokens,
        "minTokens": minimum,
        "inputSharePct": share,
        "suggested": "5m",
    }
