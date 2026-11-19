# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Refresh the vendored snapshot of model context windows from models.dev.

    uv run scripts/models-dev-sync.py                       # fetch https://models.dev/api.json
    uv run scripts/models-dev-sync.py --source api.json     # or read a copy downloaded before

Writes ``src/evokai/llm/context_windows.json``: per Evokai provider, the input tokens each text model
accepts (models.dev's ``limit.input`` when it gives one, else ``limit.context``). Evokai reads only
that file and never fetches anything at runtime (an EU deployment has no outbound dependency on
models.dev). Review the diff and commit it like any other change.

Only the providers Evokai maps are kept (``evokai.llm.context``):

- ``bedrock``: models.dev ``amazon-bedrock``, keyed by the base id without its inference profile
  prefix (``eu.``, ``us.``, ``global.``...): every profile of a model has the same window. When two
  profiles of the same base model disagree, the smaller window is kept.
- ``openai`` (also read for ``openai-chat``, ``openai-responses`` and ``azure``), ``anthropic``,
  ``mistral``, ``google`` (read for ``google-gla`` and ``google-vertex``).
- ``ollama``: models.dev ``ollama-cloud``. A local Ollama server truncates at its own ``num_ctx``,
  which may be much smaller: deployments set ``EVOKAI_CONTEXT_WINDOWS`` for local models.

Prices are not taken from here: they stay hand-maintained in ``evokai.llm.prices`` (the Bedrock EU
list is not what models.dev publishes).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

URL = "https://models.dev/api.json"
OUT = Path(__file__).resolve().parents[1] / "src" / "evokai" / "llm" / "context_windows.json"

PROVIDERS = {
    "bedrock": "amazon-bedrock",
    "openai": "openai",
    "anthropic": "anthropic",
    "mistral": "mistral",
    "google": "google",
    "ollama": "ollama-cloud",
}
"""Evokai's table name: the models.dev provider it is read from."""

BEDROCK_PROFILES = ("eu.", "us.", "us-gov.", "apac.", "global.", "jp.", "au.", "ca.", "in.")
"""Inference profile prefixes of Bedrock model ids (kept in sync with ``evokai.llm.context``)."""


def window(model: dict[str, Any]) -> int | None:
    """The input tokens a model accepts, or None for a model that does not answer with text.

    Returns:
        ``limit.input``, else ``limit.context``, when positive.
    """
    outputs = (model.get("modalities") or {}).get("output") or ["text"]
    if "text" not in outputs:
        return None
    limit = model.get("limit") or {}
    value = limit.get("input") or limit.get("context") or 0
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def base_bedrock(model_id: str) -> str:
    """A Bedrock id without its inference profile prefix.

    Returns:
        e.g. ``anthropic.claude-haiku-4-5-20251001-v1:0``.
    """
    for prefix in BEDROCK_PROFILES:
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def build(data: dict[str, Any]) -> dict[str, Any]:
    """The snapshot: one table of windows per Evokai provider.

    Returns:
        The JSON document to write.
    """
    tables: dict[str, dict[str, int]] = {}
    for ours, theirs in PROVIDERS.items():
        models = (data.get(theirs) or {}).get("models") or {}
        table: dict[str, int] = {}
        for model_id, model in models.items():
            tokens = window(model)
            if tokens is None:
                continue
            key = base_bedrock(model_id) if ours == "bedrock" else model_id
            table[key] = min(tokens, table.get(key, tokens))
        if not table:
            raise SystemExit(f"models.dev has no models for {theirs}: refusing to write")
        tables[ours] = dict(sorted(table.items()))
    return {
        "source": URL,
        "fetched": datetime.now(UTC).date().isoformat(),
        "note": "Input tokens per model, written by scripts/models-dev-sync.py. Do not edit.",
        "providers": tables,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", help="a downloaded api.json instead of fetching it")
    parser.add_argument("--out", default=str(OUT), help="where to write the snapshot")
    args = parser.parse_args()
    if args.source:
        raw = Path(args.source).read_text(encoding="utf-8")
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": "evokai-models-dev-sync"})
        with urllib.request.urlopen(req, timeout=60) as r:  # ruff: ignore[S310] (fixed https URL)
            raw = r.read().decode("utf-8")
    snapshot = build(json.loads(raw))
    out = Path(args.out)
    out.write_text(json.dumps(snapshot, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    counts = ", ".join(f"{k} {len(v)}" for k, v in snapshot["providers"].items())
    print(f"wrote {out} ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
