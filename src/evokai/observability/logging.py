"""Logging: one JSON line per record (or a coloured console view in development).

Every record carries the request's correlation id and, when tracing is on, the trace and span ids.
Calls to outside providers (models, MCP servers, speech) are logged once each with the
``provider_operation`` contract: provider, operation, outcome, duration and error class.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

from evokai.observability.correlation import get_correlation_id


class LogSettings(BaseSettings):
    """Logging settings (prefix ``LOG_``)."""

    name: str = "evokai"
    level: str = "INFO"
    format: str = "json"
    """``json`` (one JSON object per line, for containers) or ``console`` (coloured, for humans)."""
    enable_file_logging: bool = False
    dir: str = "./logs"
    rotation_interval: str = "midnight"
    model_config = SettingsConfigDict(
        env_prefix="LOG_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )


_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        k: v for k, v in record.__dict__.items() if k not in _STD_ATTRS and k not in {"msg", "args"}
    }


class JsonLineFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, then every extra field."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a record.

        Returns:
            A JSON line.
        """
        msg: Any = record.msg if isinstance(record.msg, (dict, list)) else record.getMessage()
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        for k, v in _extras(record).items():
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = str(v)
        if record.exc_info:
            payload["err"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class ColoredConsoleFormatter(logging.Formatter):
    """Human view: ``time │ LEVEL │ logger │ message`` and the extra fields below it."""

    COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[31;47m",
    }
    CYAN, PURPLE, DIM, RESET = "\033[36m", "\033[35m", "\033[37m", "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Format a record.

        Returns:
            One or more lines.
        """
        color = self.COLORS.get(record.levelname, "")
        msg = (
            json.dumps(record.msg, ensure_ascii=False)
            if isinstance(record.msg, (dict, list))
            else record.getMessage()
        )
        lines = [
            f"{self.CYAN}{self.formatTime(record, '%Y-%m-%d %H:%M:%S')} {color}│{self.RESET} "
            f"{color}{record.levelname:<8}{self.RESET} {color}│{self.RESET} "
            f"{self.PURPLE}{record.name:<14}{color}│{self.RESET} {msg}"
        ]
        extras = list(_extras(record).items())
        for i, (k, v) in enumerate(extras):
            connector = "└─" if i == len(extras) - 1 else "├─"
            value = (
                json.dumps(v, ensure_ascii=False, default=str)
                if isinstance(v, (dict, list))
                else str(v)
            )
            lines.append(f'    {self.DIM}{connector} "{k}": {value}{self.RESET}')
        if record.exc_info:
            lines.append(
                f"{self.COLORS['ERROR']}{self.formatException(record.exc_info)}{self.RESET}"
            )
        return "\n".join(lines)


class CorrelationFilter(logging.Filter):
    """Adds the request's correlation id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Annotate the record.

        Returns:
            Always True.
        """
        cid = get_correlation_id()
        if cid:
            record.correlation_id = cid
        return True


class OtelTraceFilter(logging.Filter):
    """Adds trace_id and span_id of the active OpenTelemetry span, when there is one."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Annotate the record.

        Returns:
            Always True.
        """
        try:
            from opentelemetry import trace

            ctx = trace.get_current_span().get_span_context()
            if ctx is not None and ctx.is_valid:
                record.trace_id = format(ctx.trace_id, "032x")
                record.span_id = format(ctx.span_id, "016x")
        except Exception:  # ruff: ignore[try-except-pass] (never log from inside a filter)
            pass
        return True


logger = logging.getLogger("evokai")


def error_class(e: BaseException) -> str:
    """A short error label: class name and HTTP status when there is one.

    Returns:
        e.g. ``ModelHTTPError:503``.
    """
    status = getattr(e, "status_code", None)
    return f"{type(e).__name__}{f':{status}' if status else ''}"


def setup_logging(settings: LogSettings | None = None) -> None:
    """Configure the evokai logger, uvicorn's and the noisy libraries'.

    Args:
        settings: Logging settings (from the environment by default).
    """
    settings = settings or LogSettings()
    logging.Formatter.converter = time.gmtime
    handler: logging.Handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        ColoredConsoleFormatter() if settings.format == "console" else JsonLineFormatter()
    )
    handlers = [handler]
    if settings.enable_file_logging:
        Path(settings.dir).mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            Path(settings.dir) / f"{settings.name}.log",
            when=settings.rotation_interval,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonLineFormatter())
        handlers.append(file_handler)
    from evokai.guard.logfilter import PiiLogFilter

    for h in handlers:
        h.addFilter(CorrelationFilter())
        h.addFilter(OtelTraceFilter())
        h.addFilter(PiiLogFilter())
    logger.handlers = list(handlers)
    logger.setLevel(settings.level.upper())
    logger.propagate = False
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_error.handlers = list(handlers)
    uvicorn_error.setLevel(logging.WARNING)
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.handlers = []
    uvicorn_access.propagate = False
    for noisy in (
        "httpx",
        "httpcore",
        "botocore",
        "urllib3",
        "aiosqlite",
        "sqlalchemy.engine",
        "opentelemetry",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def provider_operation_extra(
    *,
    provider: str,
    operation: str,
    outcome: str,
    duration_ms: int,
    session_id: str | None = None,
    error_type: str | None = None,
    error_code: str | None = None,
    http_status: int | None = None,
    **data: Any,
) -> dict[str, Any]:
    """Build the structured payload of a provider_operation log.

    Returns:
        The extra fields, under the provider_operation contract.
    """
    return {
        "event_name": "provider_operation",
        "event_data_source": "evokai",
        "event_data_provider": provider,
        "event_data_operation": operation,
        "event_data_outcome": outcome,
        "event_data_duration_ms": duration_ms,
        "event_data_error_type": error_type,
        "event_data_error_code": error_code,
        "event_data_http_status": http_status,
        "event_data_session_id": session_id,
        **{f"event_data_{k}": v for k, v in data.items()},
    }


@contextmanager
def provider_operation(provider: str, operation: str, **data: Any) -> Generator[dict[str, Any]]:
    """Time a call to an outside provider and log it once, whatever the outcome.

    Callers may add fields to the yielded dict (they are logged as event_data_*).

    Args:
        provider: e.g. model, mcp, tts.
        operation: e.g. stream, tools/call.
        **data: Extra fields.

    Yields:
        A dict for extra fields (re-raises whatever the call raised, after logging it).
    """
    started = time.perf_counter()
    fields: dict[str, Any] = dict(data)
    outcome, error_type, status = "success", None, None
    try:
        yield fields
    except BaseException as e:
        outcome, error_type = "error", type(e).__name__
        status = getattr(e, "status_code", None)
        raise
    finally:
        ok = fields.pop("ok", True)
        if outcome == "success" and ok is False:
            outcome = "failure"
        logger.info(
            "provider_operation",
            extra=provider_operation_extra(
                provider=provider,
                operation=operation,
                outcome=outcome,
                duration_ms=round((time.perf_counter() - started) * 1000),
                session_id=fields.pop("session_id", None),
                error_type=error_type,
                http_status=status if isinstance(status, int) else fields.pop("status", None),
                **fields,
            ),
        )
