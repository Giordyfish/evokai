"""How well a model suits an agent, estimated before anything is measured.

Two steps, both shown with their reasons:

- **Complexity** (``agent_complexity``), from the agent's real definition: how many tools it has
  (MCP tools as last listed, client tools, built-ins), how long its prompt is, how many skills it
  loads, how many of its tools change something (they ask for a confirmation), how many languages
  it answers in and how many kinds of untrusted input reach it (MCP outputs, knowledge, outside
  capabilities, page text). Each factor scores points against the documented thresholds below;
  the points give a tier, ``low``, ``medium`` or ``high``.
- **Estimate** (``estimate``) of a model for that tier, from sourced facts only: the context
  window (``evokai.llm.context``, only when the snapshot or the deployment knows the model),
  tool calling and streaming, and the τ²-bench tool-use results published in the benchmark's own
  repository (``model_profiles.yaml``, each figure with its source and date). A fact nobody sourced
  is unknown: a tier that needs it gives ``to_verify``, never ``recommended``. A known fact that
  misses the tier's bar gives ``not_recommended``.

A measurement always wins over the estimate: the assistant's measured numbers (its release
``models.yaml`` or an owner's try) give ``basis: "measured"``.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from evokai.agents.definitions import AgentDef
from evokai.engine.compaction import estimate_tokens
from evokai.llm.catalog import normalize, provider_of
from evokai.llm.context import context_window, known

PROFILES = Path(__file__).resolve().parent / "model_profiles.yaml"
TAU2_DOMAINS = ("airline", "retail", "telecom")

# Complexity: (points at or above the first value, points at or above the second value).
TOOLS = (6, 21)
"""Tools: 1 point from 6 tools, 2 from 21 (a model picks well among a few; dozens confuse it)."""
PROMPT_TOKENS = (1_500, 4_000)
"""Prompt tokens (instructions and prompt modules): 1 point from 1,500, 2 from 4,000."""
SKILLS = (1, 3)
"""Skills: 1 point from 1 skill, 2 from 3 (each loads on demand, more decisions per turn)."""
CONFIRM_TOOLS = (1, 5)
"""Tools that change something and ask for a confirmation: 1 point from 1, 2 from 5."""
LANGUAGES = (2, 4)
"""Languages answered in: 1 point from 2, 2 from 4."""
UNTRUSTED = (1, 2)
"""Kinds of untrusted input (MCP outputs, knowledge, capabilities, page text): 1 point from 1, 2
from 2: each is a way for instructions hidden in data to reach the model."""
TIERS = (3, 6)
"""Total points: ``medium`` from 3, ``high`` from 6; below 3 ``low``."""

# What each tier needs of a model.
CONTEXT = {"low": 32_000, "medium": 64_000, "high": 128_000}
"""The context window each tier needs, in tokens."""
TAU2_HIGH = 75.0
"""The τ²-bench pass^1 (mean of airline, retail and telecom) a high-complexity agent needs: a model
that completes at least three policy-bound, multi-turn tool tasks in four on its first try
(docs/models.md explains the choice and its sources)."""
TAU2_FLOOR = 50.0
"""Below this τ²-bench mean a model fails half such tasks: not recommended for a medium agent."""


@dataclass(frozen=True)
class Complexity:
    """How demanding an agent is."""

    tier: str
    """``low``, ``medium`` or ``high``."""
    factors: dict[str, int]
    """tools, promptTokens, skills, confirmTools, languages, untrusted."""
    points: int = 0


@dataclass(frozen=True)
class Estimate:
    """A model's suitability for an agent, with its reasons."""

    verdict: str
    """``recommended``, ``not_recommended`` or ``to_verify``."""
    reasons: list[str]
    """Codes: ``context_ok``, ``context_small``, ``context_unknown``, ``tools_ok``,
    ``tools_unknown``, ``streaming_ok``, ``streaming_unknown``, ``tau2_ok``, ``tau2_low``,
    ``tau2_unknown``, ``measured_ok``, ``measured_low``."""
    basis: str = "estimate"
    """``estimate`` or ``measured``."""
    facts: dict[str, Any] = field(default_factory=dict)
    """The figures behind the reasons: contextTokens, tau2Pass1, tau2AsOf, tau2Source, and the
    tier's bars (needContext, needTau2)."""

    def view(self) -> dict[str, Any]:
        """The estimate as the console receives it.

        Returns:
            verdict, reasons, basis, facts.
        """
        return {
            "verdict": self.verdict,
            "reasons": self.reasons,
            "basis": self.basis,
            "facts": self.facts,
        }


def _points(value: int, bars: tuple[int, int]) -> int:
    return 2 if value >= bars[1] else 1 if value >= bars[0] else 0


def agent_complexity(
    agent: AgentDef,
    toolbox_size: int,
    *,
    confirm_tools: int = 0,
    languages: int = 1,
    page_text: bool = False,
) -> Complexity:
    """The complexity of an agent, from its definition.

    Args:
        agent: The agent's definition.
        toolbox_size: Its MCP tools, as its servers last listed them.
        confirm_tools: How many of those change something (they ask for a confirmation).
        languages: How many languages it answers in.
        page_text: Whether the page's text is sent with each message.

    Returns:
        The tier and its factors.
    """
    ct = agent.client_tools
    client = sum([ct.navigate.enabled, ct.chart, ct.diagram, ct.ask, ct.memory])
    builtins = (
        (1 if agent.knowledge and agent.knowledge.bases else 0)
        + (1 if agent.code else 0)
        + len(agent.capabilities)
        + len(agent.subagents)
        + len(agent.handoff_to)
    )
    untrusted = (
        (1 if agent.mcp_servers else 0)
        + (1 if agent.knowledge and agent.knowledge.bases else 0)
        + (1 if agent.capabilities else 0)
        + (1 if page_text else 0)
    )
    factors = {
        "tools": toolbox_size + client + builtins,
        "promptTokens": estimate_tokens(len(agent.instructions or "")),
        "skills": len(agent.skills or []),
        "confirmTools": confirm_tools,
        "languages": max(1, languages),
        "untrusted": untrusted,
    }
    points = (
        _points(factors["tools"], TOOLS)
        + _points(factors["promptTokens"], PROMPT_TOKENS)
        + _points(factors["skills"], SKILLS)
        + _points(factors["confirmTools"], CONFIRM_TOOLS)
        + _points(factors["languages"], LANGUAGES)
        + _points(factors["untrusted"], UNTRUSTED)
    )
    tier = "high" if points >= TIERS[1] else "medium" if points >= TIERS[0] else "low"
    return Complexity(tier=tier, factors=factors, points=points)


@cache
def _profiles() -> dict[str, Any]:
    return yaml.safe_load(PROFILES.read_text(encoding="utf-8")) or {}


def profile(model: str) -> dict[str, Any] | None:
    """A model's sourced profile (the first entry whose pattern matches its id).

    Returns:
        The entry, or None.
    """
    name = normalize(model)
    for entry in _profiles().get("models") or []:
        if any(fnmatch.fnmatchcase(name, p) for p in entry.get("patterns") or []):
            return entry
    return None


def streams(model: str) -> bool | None:
    """Whether a model streams, when a source says so.

    Returns:
        True, or None when unknown.
    """
    provider = provider_of(normalize(model))
    for entry in _profiles().get("providers") or []:
        if provider in (entry.get("providers") or []) and entry.get("streaming"):
            return True
    return None


def tau2_mean(entry: Mapping[str, Any] | None) -> float | None:
    """The τ²-bench pass^1 averaged over its three domains.

    Returns:
        The mean, or None without all three.
    """
    bench = (entry or {}).get("tau2_bench") or {}
    values = [float(v) for d in TAU2_DOMAINS if (v := bench.get(d)) is not None]
    if len(values) != len(TAU2_DOMAINS):
        return None
    return round(sum(values) / len(values), 1)


def estimate(
    model: str,
    complexity: Complexity,
    *,
    context_windows: Mapping[str, int] | None = None,
    measured: bool | None = None,
) -> Estimate:
    """A model's suitability for an agent of this complexity.

    Args:
        model: The model id.
        complexity: The agent's complexity.
        context_windows: The deployment's own windows (``EVOKAI_CONTEXT_WINDOWS``).
        measured: Whether the model's measured numbers meet the requirements (None: none).

    Returns:
        The verdict with its reasons.
    """
    tier = complexity.tier
    entry = profile(model)
    window_known = known(model) or bool(
        context_windows and any(k in context_windows for k in (normalize(model), model))
    )
    window = context_window(model, context_windows) if window_known else None
    mean = tau2_mean(entry)
    facts: dict[str, Any] = {"needContext": CONTEXT[tier], "contextTokens": window}
    if mean is not None and entry:
        bench = entry["tau2_bench"]
        facts |= {"tau2Pass1": mean, "tau2AsOf": bench.get("as_of"), "tau2Source": bench["source"]}
    if tier != "low":
        facts["needTau2"] = TAU2_HIGH if tier == "high" else TAU2_FLOOR
    if measured is not None:
        reason = "measured_ok" if measured else "measured_low"
        verdict = "recommended" if measured else "not_recommended"
        return Estimate(verdict, [reason], "measured", facts)

    reasons: list[str] = []
    missing = failed = False

    def check(ok: bool | None, name: str) -> None:
        nonlocal missing, failed
        if ok is None:
            reasons.append(f"{name}_unknown")
            missing = True
        elif ok:
            reasons.append(f"{name}_ok")
        else:
            reasons.append(f"{name}_small" if name == "context" else f"{name}_low")
            failed = True

    check(None if window is None else window >= CONTEXT[tier], "context")
    tools_needed = tier != "low" or complexity.factors.get("tools", 0) > 0
    if tools_needed:
        check(True if entry and entry.get("tool_calling") else None, "tools")
    if tier != "low":
        check(streams(model), "streaming")
    if tier == "high":
        check(None if mean is None else mean >= TAU2_HIGH, "tau2")
    elif mean is not None and tier == "medium":
        check(mean >= TAU2_FLOOR, "tau2")
    verdict = "not_recommended" if failed else "to_verify" if missing else "recommended"
    return Estimate(verdict, reasons, "estimate", facts)
