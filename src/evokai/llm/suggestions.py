"""Models the console offers when an agent is created, filtered by the deployment's allow-list.

A suggestion is ``ready`` when its provider has credentials in the environment (or, for Ollama, when
the local server answers with its installed models). Anything else allowed can still be typed in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import httpx

from evokai.llm.catalog import ModelCatalog


@dataclass(frozen=True)
class Suggestion:
    """A model the console can offer."""

    id: str
    label: str
    provider: str
    ready: bool | None
    """True: credentials found; False: missing; None: unknown (e.g. an AWS role on the host)."""
    note: str = ""


CURATED: list[tuple[str, str, str]] = [
    ("eu.anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5 (Bedrock, EU)", "bedrock"),
    ("eu.anthropic.claude-sonnet-4-6", "Claude Sonnet 4.6 (Bedrock, EU)", "bedrock"),
    ("eu.amazon.nova-2-lite-v1:0", "Amazon Nova 2 Lite (Bedrock, EU)", "bedrock"),
    ("openai:gpt-5-mini", "GPT-5 mini (OpenAI)", "openai"),
    ("openai:gpt-5", "GPT-5 (OpenAI)", "openai"),
    ("mistral:mistral-small-latest", "Mistral Small (Mistral)", "mistral"),
    ("mistral:mistral-large-latest", "Mistral Large (Mistral)", "mistral"),
    ("test:canned", "Test model (canned answers, no provider)", "test"),
]

NOTES = {
    "bedrock": "Needs AWS credentials with Bedrock access in the selected Region.",
    "openai": "Needs OPENAI_API_KEY in the service's environment.",
    "mistral": "Needs MISTRAL_API_KEY in the service's environment.",
    "ollama": "Runs on the machine's own Ollama server (OLLAMA_BASE_URL).",
    "test": "Answers with placeholder text: good to try the flow, not for people.",
}


def _ready(provider: str, env: Mapping[str, str]) -> bool | None:
    if provider == "openai":
        return bool(env.get("OPENAI_API_KEY"))
    if provider == "mistral":
        return bool(env.get("MISTRAL_API_KEY"))
    if provider == "bedrock":
        hints = (
            "AWS_ACCESS_KEY_ID",
            "AWS_PROFILE",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_WEB_IDENTITY_TOKEN_FILE",
        )
        return True if any(env.get(h) for h in hints) else None
    return True


async def ollama_models(http: httpx.AsyncClient, base_url: str) -> list[str]:
    """The models installed on an Ollama server.

    Args:
        http: HTTP client.
        base_url: ``OLLAMA_BASE_URL`` (with or without its ``/v1`` suffix).

    Returns:
        Model names (``qwen3:8b``), empty when the server does not answer.
    """
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        r = await http.get(f"{root}/api/tags", timeout=2)
        r.raise_for_status()
        return sorted(str(m["name"]) for m in r.json().get("models", []) if m.get("name"))
    except Exception:
        return []


async def suggestions(
    catalog: ModelCatalog, http: httpx.AsyncClient, env: Mapping[str, str] | None = None
) -> list[dict[str, object]]:
    """The models to offer, allowed by the deployment.

    Returns:
        Suggestions as dicts (id, label, provider, ready, note), ready ones first.
    """
    env = os.environ if env is None else env
    out = [
        Suggestion(model, label, provider, _ready(provider, env), NOTES[provider])
        for model, label, provider in CURATED
    ]
    if base := env.get("OLLAMA_BASE_URL"):
        out += [
            Suggestion(f"ollama:{name}", f"{name} (Ollama, local)", "ollama", True, NOTES["ollama"])
            for name in await ollama_models(http, base)
        ]
    allowed = [s for s in out if catalog.allowed(s.id)]
    order = {True: 0, None: 1, False: 2}
    allowed.sort(key=lambda s: order[s.ready])
    return [asdict(s) for s in allowed]
