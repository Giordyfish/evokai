"""Personal data and secrets masked in log records (the ``pii.logs`` control).

A logging filter on every handler of the service: string fields of a record (its message and every
extra field) lose identifiers and secrets before any formatter sees them. The policy of the turn
being served decides (a context variable set by the guard); outside a turn the deployment default
applies.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from evokai.guard import pii, secrets

log_masking: ContextVar[bool | None] = ContextVar("evokai_log_masking", default=None)
"""Set per turn by the guard: True masks, False leaves records alone, None follows the default."""

_default = {"on": True}
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
_KEEP = {
    "correlation_id",
    "trace_id",
    "span_id",
    "turnId",
    "conversationId",
    "runId",
    "applicationId",
}


def configure(enabled: bool) -> None:
    """The deployment default outside turns (on unless the guard is disabled)."""
    _default["on"] = enabled


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return secrets.redact(pii.mask(value)) if len(value) < 20_000 else value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


class PiiLogFilter(logging.Filter):
    """Masks identifiers and secrets in log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Clean the record in place.

        Returns:
            Always True (records are never dropped).
        """
        on = log_masking.get()
        if not (_default["on"] if on is None else on):
            return True
        if isinstance(record.msg, str) and not record.args:
            record.msg = _clean(record.msg)
        for key, value in list(record.__dict__.items()):
            if key in _STD or key in _KEEP or key in {"msg", "args"}:
                continue
            record.__dict__[key] = _clean(value)
        return True
