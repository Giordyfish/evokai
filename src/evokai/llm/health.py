"""How the models answered lately, per process: for the console's health page.

Nothing is called to find out: every model call of a turn or a run is noted (success or failure
and its latency), and the console reads the last ones.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

KEEP = 50


@dataclass
class _Call:
    at: float
    ok: bool
    latency_ms: int
    error: str = ""


@dataclass
class ModelHealth:
    """The last calls of every model."""

    calls: dict[str, deque[_Call]] = field(default_factory=dict)

    def record(self, model: str, *, ok: bool, latency_ms: int = 0, error: str = "") -> None:
        """Note a call."""
        self.calls.setdefault(model, deque(maxlen=KEEP)).append(
            _Call(time.time(), ok, latency_ms, error)
        )

    def snapshot(self) -> list[dict[str, Any]]:
        """Per model: calls kept, failures, median latency, last success and failure.

        Returns:
            One row per model, by name.
        """
        out = []
        for model, calls in sorted(self.calls.items()):
            ok = sorted(c.latency_ms for c in calls if c.ok)
            failed = [c for c in calls if not c.ok]
            last_ok = max((c.at for c in calls if c.ok), default=None)
            last_failed = failed[-1] if failed else None
            out.append({
                "model": model,
                "calls": len(calls),
                "failures": len(failed),
                "p50Ms": ok[len(ok) // 2] if ok else None,
                "lastOkAt": last_ok,
                "lastErrorAt": last_failed.at if last_failed else None,
                "lastError": last_failed.error if last_failed else None,
            })
        return out
