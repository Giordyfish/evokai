"""The provider catalog: facts with their sources, a deployment's own endpoints, models built from
them."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.models.openai import OpenAIChatModel

from evokai.core.errors import ConfigError
from evokai.llm import providers as pc
from evokai.llm.catalog import KNOWN_PROVIDERS, ModelCatalog, normalize
from tests.conftest import Env
from tests.test_no_amounts_in_browsers import MONEY, _keys


def _f(cat: pc.ProviderCatalog, model: str) -> pc.Facts:
    f = cat.model_facts(model)
    assert f is not None, model
    return f


DEPLOYMENT = """
providers:
  - id: our-vllm
    title: Our vLLM in Rome
    self_hosted: true
    entity: {name: ACME S.p.A., country: IT, controlled_from: IT}
    terms: {training: false, retention: none, zero_retention: default}
    endpoints:
      - id: rome
        models: ["our-vllm:*"]
        api: openai-compatible
        base_url: http://vllm.internal:8000/v1
        api_key_env: OUR_VLLM_KEY
        place: {geography: eu, countries: [IT]}
        declared_by: operator
  - id: azure
    title: Azure OpenAI
    endpoints:
      - id: our-sweden
        like: azure-datazone-eu
        models: ["azure:*"]
        api: azure
        base_url: https://ours.openai.azure.com
        api_version: "2024-10-21"
        api_key_env: OUR_AZURE_KEY
        declared_by: operator
"""


def test_every_built_in_entry_has_its_sources_and_date() -> None:
    cat = pc.ProviderCatalog.load()
    assert cat.data.version == 1 and cat.data.checked is not None
    for p in cat.data.providers:
        assert p.checked is not None, p.id
        if p.self_hosted:
            continue
        assert p.entity.sources, p.id
        for e in p.endpoints:
            assert e.sources and e.checked, (p.id, e.id)
        for x in p.prices:
            assert x.sources and x.checked and x.models, (p.id, x.models)
    for x in cat.data.processors:
        assert x.checked is not None, x.id
    text = pc.BUILT_IN.read_text(encoding="utf-8")
    assert chr(0x2014) not in text
    cov = cat.coverage()
    assert cov["providers"] >= 20 and cov["known"] > cov["unknown"] > 0


def test_bedrock_profiles_and_in_region_ids_have_their_places() -> None:
    cat = pc.ProviderCatalog.load(aws_region="eu-north-1")
    eu = cat.model_facts("eu.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert eu is not None and eu.endpoint == "bedrock-eu-profile" and eu.geography == "eu"
    assert eu.countries is not None and "SE" in eu.countries and "US" not in eu.countries
    assert eu.jurisdiction == "US" and eu.terms.training is False
    local = cat.model_facts("bedrock:amazon.titan-embed-text-v2:0")
    assert local is not None and local.endpoint == "bedrock-in-region"
    assert (local.geography, local.countries) == ("eu", ["SE"])
    frankfurt = pc.ProviderCatalog.load(aws_region="eu-central-1")
    assert _f(frankfurt, "bedrock:amazon.titan-embed-text-v2:0").countries == ["DE"]
    glob = cat.model_facts("bedrock:global.anthropic.claude-sonnet-4-6")
    assert glob is not None and glob.geography == "global"
    # A profile the catalog does not describe is not mistaken for the deployment's region.
    assert _f(cat, "bedrock:au.anthropic.claude-haiku-4-5").geography is None


def test_eu_providers_are_eu_controlled_and_unknown_models_have_no_facts() -> None:
    cat = pc.ProviderCatalog.load()
    for model in (
        "scaleway:gpt-oss-120b",
        "ionos:openai/gpt-oss-120b",
        "stackit:openai/gpt-oss-20b",
    ):
        f = cat.model_facts(model)
        assert f is not None and f.jurisdiction == "eu" and f.geography == "eu", model
    mistral_eu = cat.model_facts("mistral-eu:mistral-large-4")
    assert mistral_eu is not None and mistral_eu.endpoint == "mistral-eu"
    assert _f(cat, "mistral:mistral-large-4").geography == "global"
    assert cat.model_facts("google:gemini-x") is None  # a pydantic-ai provider the catalog lacks
    assert _f(cat, "function:primary").in_process


def test_a_deployment_adds_its_own_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text(DEPLOYMENT, encoding="utf-8")
    cat = pc.configure(path, "eu-north-1")
    try:
        assert "our-vllm" in KNOWN_PROVIDERS and normalize("our-vllm:llama") == "our-vllm:llama"
        ours = cat.model_facts("our-vllm:llama")
        assert ours is not None and ours.self_hosted and ours.declared_by == "operator"
        assert (ours.geography, ours.countries, ours.jurisdiction) == ("eu", ["IT"], "eu")
        # The Azure resource copies the EU data zone's facts and keeps the built-in endpoints after
        # it.
        az = cat.model_facts("azure:gpt-4.1")
        assert az is not None and az.endpoint == "our-sweden" and az.geography == "eu"
        assert az.declared_by == "operator" and any("microsoft" in s for s in az.sources)
        assert cat.providers["azure"].endpoints[0].id == "our-sweden"
        assert len(cat.providers["azure"].endpoints) == 5

        models = ModelCatalog(["*"], "eu-north-1", providers=cat)
        with pytest.raises(ConfigError, match="OUR_VLLM_KEY"):
            models.get("our-vllm:llama")
        monkeypatch.setenv("OUR_VLLM_KEY", "k")
        monkeypatch.setenv("OUR_AZURE_KEY", "k")
        vllm = models.get("our-vllm:llama")
        assert isinstance(vllm, OpenAIChatModel) and vllm.base_url.startswith(
            "http://vllm.internal:8000/v1"
        )
        azure = models.get("azure:gpt-4.1")
        assert isinstance(azure, OpenAIChatModel) and "ours.openai.azure.com" in azure.base_url
    finally:
        pc.configure(None, "eu-north-1")


def test_built_in_eu_endpoints_build_their_models(monkeypatch: pytest.MonkeyPatch) -> None:
    models = ModelCatalog(["*"], "eu-north-1", providers=pc.ProviderCatalog.load())
    monkeypatch.setenv("SCALEWAY_API_KEY", "k")
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    scw = models.get("scaleway:gpt-oss-120b")
    assert isinstance(scw, OpenAIChatModel) and scw.base_url.startswith(
        "https://api.scaleway.ai/v1"
    )
    eu = models.get("mistral-eu:mistral-large-4")
    assert isinstance(eu, MistralModel) and "api.eu.mistral.ai" in eu.base_url
    # A self-hosted server without its address is a configuration error, not a guess.
    with pytest.raises(ConfigError, match="base_url"):
        models.get("lmstudio:qwen")


def test_a_bad_deployment_file_is_a_configuration_error(tmp_path: Path) -> None:
    path = tmp_path / "providers.yaml"
    path.write_text("providers: [{id: x}]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="provider catalog"):
        pc.ProviderCatalog.load(path)


async def test_the_console_reads_the_catalog_without_prices(env: Env) -> None:
    r = await env.client.get(
        "/agent/v1/admin/providers", headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {p["id"] for p in body["providers"]}
    assert {"bedrock", "azure", "google-vertex", "openai", "mistral", "scaleway", "ollama"} <= ids
    assert body["coverage"]["unknown"] > 0
    assert any(x["kind"] == "classifier" for x in body["processors"])
    # No key names money anywhere (notes may mention prices in words, never an amount field).
    assert not [k for k in _keys(body) if MONEY.search(k)]
