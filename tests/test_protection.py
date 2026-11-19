"""The data protection profile: residency, jurisdiction, retention, every processor."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evokai.agents.bootstrap import bootstrap
from evokai.core.errors import ConfigError
from evokai.protection import (
    DataProtection,
    model_verdict,
    processor_verdict,
    profile_of,
    residency_allows,
)
from tests.conftest import Env

REGION = "eu-north-1"
HAIKU_EU = "bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0"


def _dp(**rule: Any) -> dict[str, Any]:
    return {"data_protection": DataProtection.model_validate(rule).stored()}


def _reasons(source: Any, model: str) -> set[tuple[str, str]]:
    return {(r.dimension, r.code) for r in model_verdict(source, model, REGION).reasons}


def test_residency_by_geography_or_country() -> None:
    eu = _dp(residency={"geographies": ["eu"]})
    assert residency_allows(eu, HAIKU_EU, REGION)
    assert residency_allows(eu, "scaleway:gpt-oss-120b", REGION)
    assert residency_allows(eu, "mistral-eu:mistral-large-4", REGION)
    # Mistral's global endpoint promises no inference location; OpenAI's default is global.
    assert _reasons(eu, "mistral:mistral-large-4") == {("residency", "outside")}
    assert not residency_allows(eu, "openai:gpt-4.1", REGION)
    # A country list: every processing country must be in it.
    france = _dp(residency={"countries": ["FR"]})
    assert residency_allows(france, "scaleway:gpt-oss-120b", REGION)
    assert _reasons(france, HAIKU_EU) == {("residency", "outside")}
    # A geography nobody states is unknown, never allowed.
    assert _reasons(france, "ovhcloud:gpt-oss-120b") == {("residency", "unknown")}


def test_jurisdiction_keeps_us_controlled_providers_out() -> None:
    eu = _dp(jurisdiction=["eu"])
    assert _reasons(eu, HAIKU_EU) == {("jurisdiction", "outside")}
    for model in ("scaleway:gpt-oss-120b", "ionos:x", "stackit:x", "mistral:x", "ovhcloud:x"):
        assert residency_allows(eu, model, REGION), model
    # Self-hosted without a declaration: unknown.
    assert _reasons(eu, "ollama:qwen") == {("jurisdiction", "unknown")}
    # In-process models send nothing out.
    assert residency_allows(eu, "function:primary", REGION)


def test_retention_needs_known_terms() -> None:
    zdr = _dp(retention="zero_retention")
    assert residency_allows(zdr, "scaleway:gpt-oss-120b", REGION)
    assert ("retention", "retained") in _reasons(zdr, "openai:gpt-4.1")
    # Mistral keeps 30 days for abuse monitoring and its training default is not stated.
    assert _reasons(zdr, "mistral:x") == {("retention", "unknown"), ("retention", "retained")}
    no_training = _dp(retention="no_training")
    assert residency_allows(no_training, "openai:gpt-4.1", REGION)
    assert residency_allows(no_training, HAIKU_EU, REGION)


def test_sovereignty_is_refused_in_a_config_and_ignored_when_stored() -> None:
    with pytest.raises(ValidationError, match="sovereignty is not a dimension"):
        DataProtection.model_validate({"jurisdiction": ["eu"], "sovereignty": {"seal": 2}})
    # A profile stored before the dimension was dropped is read without it.
    stored = {"data_protection": {"jurisdiction": ["eu"], "sovereignty": {"seal": 2}}}
    rule = profile_of(stored).rule
    assert rule is not None and rule.jurisdiction == ["eu"]
    assert _reasons(stored, HAIKU_EU) == {("jurisdiction", "outside")}
    assert profile_of({"data_protection": {"sovereignty": {"seal": 2}}}).empty


async def test_a_config_file_with_sovereignty_fails_clearly(env: Env, tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    path.write_text(
        "application:\n  slug: sov\n  name: Sov\n"
        "  data_protection:\n    jurisdiction: [eu]\n    sovereignty: {seal: 2}\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ConfigError, match=r"invalid data_protection: .*sovereignty is not a dimension"
    ):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})


def test_dimensions_combine_and_the_old_residency_still_holds() -> None:
    both = _dp(residency={"geographies": ["eu"]}, jurisdiction=["eu"], retention="zero_retention")
    assert residency_allows(both, "scaleway:gpt-oss-120b", REGION)
    # Bedrock keeps nothing and does not train: only who controls it fails.
    assert _reasons(both, HAIKU_EU) == {("jurisdiction", "outside")}
    legacy = {"model_regions": ["eu"]}
    assert residency_allows(legacy, HAIKU_EU, REGION)
    assert not residency_allows(legacy, "bedrock:us.anthropic.claude-haiku-4-5", REGION)
    # The catalog now knows non-Bedrock geographies: an EU provider passes the old rule too.
    assert residency_allows(legacy, "scaleway:gpt-oss-120b", REGION)
    assert residency_allows({"models": ["ollama:*"], "model_regions": ["eu"]}, "ollama:x", REGION)
    # Old and new together: both must hold.
    mixed = {**legacy, **_dp(jurisdiction=["eu"])}
    assert not residency_allows(mixed, HAIKU_EU, REGION)


def test_a_profile_is_validated() -> None:
    with pytest.raises(ValidationError):
        DataProtection.model_validate({"residency": {"geographies": ["mars"]}})
    with pytest.raises(ValidationError):
        DataProtection.model_validate({"jurisdiction": ["Europe"]})
    assert DataProtection.model_validate({"jurisdiction": ["EU", "ch"]}).jurisdiction == [
        "eu",
        "CH",
    ]


def test_processors_meet_the_profile_too() -> None:
    eu = _dp(jurisdiction=["eu"])
    assert processor_verdict(eu, "prompt-guard").allowed  # in process
    assert not processor_verdict(eu, "bedrock-guardrails").allowed
    assert not processor_verdict(eu, "agentcore-code-interpreter").allowed
    assert [r.code for r in processor_verdict(eu, "connection:github").reasons] == ["unknown"]
    assert not processor_verdict(eu, "nobody-knows").allowed


async def test_the_profile_is_set_enforced_and_reported(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    profile = {
        "residency": {"geographies": ["eu"]},
        "jurisdiction": ["eu"],
        "browser": {"online_voices": True, "vendors": ["Google"]},
    }
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"data_protection": profile}, headers=adm
    )
    assert r.status_code == 200, r.text
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"data_protection": {**profile, "sovereignty": {"seal": 2}}},
        headers=adm,
    )
    assert r.status_code == 400 and r.json()["fields"] == ["body.data_protection"]
    app = (await env.client.get("/agent/v1/admin/application", headers=adm)).json()
    assert app["data_protection"]["jurisdiction"] == ["eu"]
    # A requested model outside the profile is refused at token mint.
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={
            "subject": "u1",
            "agent": "helper",
            "model": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
        },
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 403 and r.json()["error"] == "model_residency"
    # The report says what is excluded and why.
    r = await env.client.get("/agent/v1/admin/data-protection", headers=adm)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {(x["kind"], x["name"]): x for x in body["processors"]}
    haiku = rows["model", HAIKU_EU]
    assert not haiku["allowed"]
    assert {(x["dimension"], x["code"]) for x in haiku["reasons"]} == {("jurisdiction", "outside")}
    assert haiku["facts"]["controlledFrom"] == "US" and haiku["facts"]["sources"]
    assert rows["classifier", "prompt_guard"]["allowed"]
    assert "framework" not in body and "seal" not in haiku["facts"]
    assert body["browser"]["online_voices"] is True
    # The compliance pack states the profile and every processor.
    r = await env.client.get("/agent/v1/admin/compliance/gdpr", headers=adm)
    assert r.status_code == 200, r.text
    text = r.text
    assert "EU or EEA" in text and "data-protection" in text.replace("_", "-").lower()
    # Clearing it lifts every rule.
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"data_protection": None}, headers=adm
    )
    assert r.status_code == 200
    body = (await env.client.get("/agent/v1/admin/data-protection", headers=adm)).json()
    assert body["empty"] and all(x["allowed"] for x in body["processors"])
