"""Measure a model on the built-in assistant's eval suite and record it in models.yaml.

Run at release time, once per model the assistant may recommend (it needs the model's provider
credentials in the environment, like the service). Everything runs in process on a throw-away
database, against the recorded admin MCP server: no application is touched.

    uv run python scripts/assistant_evals.py bedrock:eu.anthropic.claude-sonnet-4-5-20250929-v1:0
    uv run python scripts/assistant_evals.py MODEL --pattern "bedrock:eu.anthropic.claude-sonnet-4-5*" --write
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from evokai.assistant import activation
from evokai.assistant.bootstrap import ensure_assistant
from evokai.core.config import Settings
from evokai.main import create_app
from evokai.services import build_services


class _App(httpx.AsyncBaseTransport):
    """Requests to the service's own address go to the app in process."""

    app: Any = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await httpx.ASGITransport(app=self.app).handle_async_request(request)


async def measure(model: str) -> dict[str, Any]:
    """Run the suite on a model.

    Returns:
        The numbers (tool_calls_pct, confirm_pct, redteam_pct, context_tokens...).

    Raises:
        RuntimeError: The run did not finish.
    """
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{Path(tmp) / 'evals.db'}",
            model_allow=[model],
            redteam_on_save=False,
        )
        late = _App()
        svc = build_services(settings, http=httpx.AsyncClient(transport=late, timeout=180))
        await svc.db.create_all()
        app = create_app(settings, svc)
        late.app = app
        try:
            async with app.state.admin_mcp.lifespan():
                app_id = await ensure_assistant(svc.store, svc.keyring, settings)
                system = await svc.store.applications.get(app_id or "")
                if system is None:
                    raise RuntimeError("the assistant was not created")
                defaults = {**(system.defaults or {}), "eval_monthly_usd": 50}
                await svc.store.applications.update(system.id, defaults=defaults)
                system = await svc.store.applications.get(system.id)
                assert system is not None
                run = await activation.start_trial(svc, system, model, "release")
                while True:
                    current = await svc.store.evals.run(system.id, run.id)
                    if current is not None and current.status != "running":
                        break
                    await asyncio.sleep(0.5)
                if current.status != "done":
                    raise RuntimeError(f"the run ended {current.status}")
                return dict((current.summary or {}).get("measured") or {})
        finally:
            await svc.close()


def write(pattern: str, numbers: dict[str, Any], path: Path = activation.MODELS) -> None:
    """Replace or add a pattern's entry in models.yaml (the header comments are kept)."""
    text = path.read_text(encoding="utf-8")
    header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
    entries = [e for e in activation.released(path) if e.get("pattern") != pattern]
    entries.append({"pattern": pattern, **numbers})
    body = yaml.safe_dump({"models": entries}, sort_keys=False, allow_unicode=True)
    path.write_text(header + body, encoding="utf-8")


def main() -> None:
    """Measure, print, and with --write record the numbers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model")
    parser.add_argument("--pattern", help="the model id pattern to record (default: the model)")
    parser.add_argument("--write", action="store_true", help="record the numbers in models.yaml")
    args = parser.parse_args()
    numbers = asyncio.run(measure(args.model))
    print(json.dumps(numbers, indent=2))
    if args.write:
        write(args.pattern or args.model, numbers)


if __name__ == "__main__":
    main()
