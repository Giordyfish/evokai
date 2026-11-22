"""The model suitability estimate: complexity from the definition, verdicts from sourced data."""

from __future__ import annotations

import json

import yaml

from evokai.agents.definitions import AgentDef
from evokai.assistant.bootstrap import AGENT, SHIPPED, rendered
from evokai.core.config import Settings
from evokai.llm import suitability
from evokai.llm.suitability import Complexity, agent_complexity, estimate, profile
from evokai.mcp_admin.tools import ADMIN_TOOLS
from tests.conftest import Env

HIGH = Complexity(tier="high", factors={}, points=9)
MEDIUM = Complexity(tier="medium", factors={}, points=4)
SOURCED = "bedrock:eu.anthropic.claude-opus-4-5-20251101-v1:0"
"""A model with a known window (the snapshot) and a sourced benchmark result above the bar."""


def _shipped_agent() -> AgentDef:
    data = rendered(SHIPPED, Settings(), "bedrock:eu.anthropic.claude-sonnet-4-5-20250929-v1:0")
    agent = next(a for a in data["agents"] if a["name"] == AGENT)
    return AgentDef.model_validate(agent)


def test_shipped_assistant_is_high_complexity() -> None:
    agent = _shipped_agent()
    writes = sum(1 for t in ADMIN_TOOLS if t.method != "GET")
    c = agent_complexity(agent, len(ADMIN_TOOLS), confirm_tools=writes, languages=2)
    assert c.tier == "high", c
    assert c.factors["tools"] >= len(ADMIN_TOOLS)
    assert c.factors["confirmTools"] == writes
    assert c.factors["skills"] == 1 and c.factors["untrusted"] >= 1
    # A small agent with no tools is low.
    plain = AgentDef(name="faq", model="test:x", instructions="Answer briefly.")
    assert agent_complexity(plain, 0).tier == "low"


def test_estimate_needs_sourced_data_for_recommended() -> None:
    e = estimate(SOURCED, HIGH)
    assert e.verdict == "recommended" and e.basis == "estimate", e
    assert {"context_ok", "tools_ok", "streaming_ok", "tau2_ok"} <= set(e.reasons)
    # A sourced result below the bar is not recommended, whatever else is known.
    weak = estimate("openai:gpt-4.1", HIGH)
    assert weak.verdict == "not_recommended" and "tau2_low" in weak.reasons, weak
    # A known window too small for the tier is not recommended.
    small = estimate(SOURCED, HIGH, context_windows={SOURCED: 32_000})
    assert small.verdict == "not_recommended" and "context_small" in small.reasons
    # Every figure the estimate used comes with its source.
    p = profile(SOURCED)
    assert p is not None and p["tau2_bench"]["source"].startswith("https://")
    assert p["tau2_bench"]["as_of"]


def test_missing_data_is_to_verify() -> None:
    for model in (
        "openai:a-model-nobody-measured",
        "ollama:llama3.2",
        "bedrock:eu.amazon.nova-pro-v1:0",
    ):
        e = estimate(model, HIGH)
        assert e.verdict == "to_verify", (model, e)
        assert e.basis == "estimate"
    # A medium agent needs no benchmark, but tool calling must be sourced.
    assert estimate("ollama:llama3.2", MEDIUM).verdict == "to_verify"
    assert estimate(SOURCED, MEDIUM).verdict == "recommended"


def test_measured_overrides_estimate() -> None:
    ok = estimate("openai:gpt-4.1", HIGH, measured=True)
    assert ok.verdict == "recommended" and ok.basis == "measured"
    low = estimate(SOURCED, HIGH, measured=False)
    assert low.verdict == "not_recommended" and low.basis == "measured"
    unknown = estimate("openai:a-model-nobody-measured", HIGH, measured=True)
    assert unknown.basis == "measured"


def test_profiles_are_sourced_and_dated() -> None:
    data = yaml.safe_load(suitability.PROFILES.read_text(encoding="utf-8"))
    for entry in data["models"]:
        bench = entry.get("tau2_bench")
        if bench:
            assert bench["source"].startswith("https://github.com/sierra-research/tau2-bench/")
            assert bench["as_of"] and all(0 <= bench[d] <= 100 for d in suitability.TAU2_DOMAINS)
    for entry in data["providers"]:
        assert entry["source"].startswith("https://") and entry["as_of"]


async def test_no_amounts(env: Env) -> None:
    text = suitability.PROFILES.read_text(encoding="utf-8").lower()
    assert "cost" not in text and "usd" not in text and "$" not in text
    admin = {"Authorization": f"Bearer {env.admin_key}"}
    r = await env.client.get(
        "/agent/v1/admin/agents/helper/suitability", params={"model": SOURCED}, headers=admin
    )
    assert r.status_code == 200, r.text
    body = json.dumps(r.json()).lower()
    assert "cost" not in body and "usd" not in body


async def test_agent_editor_shows_estimate(env: Env) -> None:
    admin = {"Authorization": f"Bearer {env.admin_key}"}
    r = await env.client.get(
        "/agent/v1/admin/agents/helper/suitability", params={"model": SOURCED}, headers=admin
    )
    body = r.json()
    assert body["complexity"]["tier"] in {"low", "medium", "high"}
    assert body["estimate"]["basis"] == "estimate"
    assert body["estimate"]["verdict"] in {"recommended", "not_recommended", "to_verify"}
    r = await env.client.get(
        "/agent/v1/admin/agents/nobody/suitability", params={"model": SOURCED}, headers=admin
    )
    assert r.status_code == 404
