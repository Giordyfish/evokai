"""Context windows: how many input tokens a model accepts.

From a vendored snapshot of models.dev (``context_windows.json``, refreshed by
``scripts/models-dev-sync.py`` and committed); nothing is fetched at runtime. A deployment can set
or correct any model with ``EVOKAI_CONTEXT_WINDOWS`` (``{"ollama:llama3.2": 8192}``), which wins
over the snapshot.

Mapping of Evokai ids to the snapshot's tables:

- ``bedrock:<id>``: the ``bedrock`` table, by the id as written and then without its inference
  profile prefix (``eu.anthropic.claude-haiku-4-5-20251001-v1:0`` is read as
  ``anthropic.claude-haiku-4-5-20251001-v1:0``);
- ``openai:``, ``openai-chat:``, ``openai-responses:``, ``azure:``: ``openai``; ``anthropic:``;
  ``mistral:``; ``google-gla:`` and ``google-vertex:``: ``google``; ``ollama:``: ``ollama`` (the
  models.dev Ollama Cloud windows, the model's maximum: a local server truncates at its own
  ``num_ctx``, so set those models in ``EVOKAI_CONTEXT_WINDOWS``);
- anything else (``test:``, ``function:``, an unknown model): ``DEFAULT_WINDOW``.

Prices are not read from here (``evokai.llm.prices`` stays hand-maintained).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import cache
from importlib import resources

from evokai.llm.catalog import bare, normalize

DEFAULT_WINDOW = 128_000
"""The window of a model the snapshot does not know: what most current chat models accept at least,
and small enough that compaction starts in time on any of them."""

TABLES = {
    "bedrock": "bedrock",
    "openai": "openai",
    "openai-chat": "openai",
    "openai-responses": "openai",
    "azure": "openai",
    "anthropic": "anthropic",
    "mistral": "mistral",
    "google-gla": "google",
    "google-vertex": "google",
    "ollama": "ollama",
}
"""Evokai provider: the snapshot table its models are looked up in."""

BEDROCK_PROFILES = ("eu.", "us.", "us-gov.", "apac.", "global.", "jp.", "au.", "ca.", "in.")
"""Inference profile prefixes of Bedrock model ids (as in ``scripts/models-dev-sync.py``)."""


@cache
def snapshot() -> dict[str, dict[str, int]]:
    """The vendored tables, read once.

    Returns:
        Table name to model id to input tokens (empty if the file is missing).
    """
    try:
        raw = (resources.files("evokai.llm") / "context_windows.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return json.loads(raw).get("providers") or {}


def context_window(model: str, overrides: Mapping[str, int] | None = None) -> int:
    """The input tokens a model accepts.

    Args:
        model: An Evokai model id (``bedrock:eu.anthropic…``, ``openai:gpt-5``, a bare Bedrock id).
        overrides: The deployment's own windows (``EVOKAI_CONTEXT_WINDOWS``), by Evokai id or bare
            name; they win over the snapshot.

    Returns:
        The window in tokens (``DEFAULT_WINDOW`` when unknown).
    """
    name = normalize(model)
    provider, _, rest = name.partition(":")
    if overrides:
        for key in (name, rest):
            value = overrides.get(key)
            if value and value > 0:
                return int(value)
    table = snapshot().get(TABLES.get(provider, ""), {})
    candidates = [rest]
    if provider == "bedrock":
        candidates += [rest[len(p) :] for p in BEDROCK_PROFILES if rest.startswith(p)]
    for key in candidates:
        if key in table:
            return int(table[key])
    return DEFAULT_WINDOW


def smallest_window(models: list[str], overrides: Mapping[str, int] | None = None) -> int:
    """The smallest window of a model chain (a fallback may accept less than the primary).

    Returns:
        Tokens (``DEFAULT_WINDOW`` for an empty chain).
    """
    return min((context_window(x, overrides) for x in models), default=DEFAULT_WINDOW)


def known(model: str) -> bool:
    """Whether the snapshot has this model (for the docs and tests; unknown ones use the default).

    Returns:
        True when the window comes from the snapshot.
    """
    name = normalize(model)
    provider = name.partition(":")[0]
    table = snapshot().get(TABLES.get(provider, ""), {})
    rest = bare(name)
    return rest in table or any(
        rest.startswith(p) and rest[len(p) :] in table for p in BEDROCK_PROFILES
    )
