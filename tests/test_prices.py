"""Prices for every model: the catalog's own, genai-prices', and a fallback that is never free."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from evokai.llm import prices
from evokai.llm import providers as pc
from evokai.llm.prices import cost_micro_usd, price_of
from tests.conftest import Env

M = 1_000_000


def test_the_catalog_prices_bedrock_eu_from_the_stockholm_list() -> None:
    p = price_of("eu.amazon.nova-2-lite-v1:0")
    assert p.source == "catalog" and (p.input, p.output) == (0.363, 2.992)
    haiku = price_of("eu.anthropic.claude-haiku-4-5-20251001-v1:0")
    # Anthropic's cache prices are multiples of the input price on every platform.
    assert haiku.source == "catalog" and haiku.cache_read == pytest.approx(0.11)
    assert haiku.cache_write == pytest.approx(1.375) and haiku.cache_write_1h == pytest.approx(2.2)


def test_genai_prices_prices_the_rest_with_its_cache_prices_and_tiers() -> None:
    gpt = price_of("openai:gpt-4.1")
    assert (
        gpt.source in ("catalog", "genai-prices") and gpt.input > 0 and gpt.cache_read < gpt.input
    )
    other = price_of("bedrock:eu.anthropic.claude-sonnet-4-5-20250929-v1:0")
    assert other.source == "genai-prices" and other.cache_write > other.input
    # A long prompt is priced at its tier (genai-prices applies the provider's long-context rate).
    gemini = price_of("google-gla:gemini-2.5-pro")
    assert gemini.source == "genai-prices"
    short = cost_micro_usd("google-gla:gemini-2.5-pro", 100_000, 0)
    long = cost_micro_usd("google-gla:gemini-2.5-pro", 300_000, 0)
    assert long > 3 * short


def test_eu_prices_in_euros_are_converted_and_regional_surcharges_applied() -> None:
    scw = price_of("scaleway:gpt-oss-120b")
    assert scw.source == "catalog" and scw.input == pytest.approx(0.15 * 1.2)
    base = price_of("mistral:mistral-large-4")
    eu = price_of("mistral-eu:mistral-large-4")
    assert base.source == "catalog" and eu.input == pytest.approx(base.input * 1.1)
    assert cost_micro_usd("mistral-eu:mistral-large-4", M, 0) == pytest.approx(
        cost_micro_usd("mistral:mistral-large-4", M, 0) * 1.1, abs=2
    )


def test_self_hosted_models_are_free_and_unknown_ones_never_are(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert cost_micro_usd("ollama:qwen3:8b", 10_000, 1_000) == 0
    assert cost_micro_usd("vllm:llama", 10_000, 1_000) == 0
    with caplog.at_level(logging.WARNING, logger="evokai.llm"):
        unknown = price_of("groq:some-model-nobody-prices")
    assert unknown.source == "fallback" and (unknown.input, unknown.output) == prices.FALLBACK_PRICE
    # A cache read of an unknown model is never cheaper than its input.
    assert unknown.cache_read == unknown.input
    assert any(r.getMessage() == "price_unknown" for r in caplog.records)
    assert cost_micro_usd("groq:some-model-nobody-prices", 10_000, 0) > 0


def test_a_deployment_prices_its_own_models(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(
        """
providers:
  - id: groq
    title: Groq
    prices:
      - models: ["groq:our-model"]
        currency: EUR
        input: 1.0
        output: 2.0
        sources: ["https://example.test/our-contract"]
        checked: 2026-11-19
""",
        encoding="utf-8",
    )
    pc.configure(path, "eu-north-1", eur_usd=1.1)
    try:
        ours = price_of("groq:our-model")
        assert ours.source == "catalog" and ours.input == pytest.approx(1.1)
        assert ours.output == pytest.approx(2.2)
    finally:
        pc.configure(None, "eu-north-1")


def test_classifier_units_are_priced_from_the_catalog() -> None:
    # Azure Content Safety: per 1,000 text records.
    assert prices.classifier_cost_micro_usd("azure-prompt-shields", 1_000) == 375_000
    assert prices.classifier_cost_micro_usd("openai-moderation", 10) == 0
    assert prices.classifier_cost_micro_usd("prompt-guard", 10) == 0


async def test_the_console_flags_a_model_nobody_prices(env: Env) -> None:
    env.svc.catalog.allow.append("groq:*")
    r = await env.client.get(
        "/agent/v1/admin/grants", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert r.status_code == 200, r.text
    models = next(g for g in r.json()["groups"] if g["id"] == "models")["items"]
    rated = {m["name"]: m.get("rateSource") for m in models}
    assert rated["bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0"] == "catalog"
    assert all(v in ("catalog", "genai-prices", "fallback", "free") for v in rated.values())
