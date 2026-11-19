"""OpenTelemetry traces, metrics and logs. Optional and off by default (EVOKAI_OTEL_ENABLED).

When enabled, the OTLP exporters read the standard variables (OTEL_EXPORTER_OTLP_ENDPOINT,
OTEL_EXPORTER_OTLP_HEADERS, OTEL_RESOURCE_ATTRIBUTES…). The packages are an optional extra: ``uv
sync --extra otel``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("evokai.telemetry")

_tracer: Any = None
_meter_instruments: dict[str, Any] = {}


def setup_telemetry(app: Any, *, enabled: bool, service_name: str) -> bool:
    """Enable OpenTelemetry when asked and installed.

    Args:
        app: The FastAPI app to instrument.
        enabled: The EVOKAI_OTEL_ENABLED flag.
        service_name: Resource service name.

    Returns:
        Whether tracing is on.
    """
    global _tracer
    if not enabled:
        return False
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("otel_unavailable", extra={"hint": "install the 'otel' extra"})
        return False
    resource = Resource.create({"service.name": service_name})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
        )
    )
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    _tracer = trace.get_tracer("evokai")
    meter = metrics.get_meter("evokai")
    _meter_instruments["turn_latency"] = meter.create_histogram("evokai.turn.latency_ms", unit="ms")
    _meter_instruments["model_cost"] = meter.create_counter("evokai.model.cost_micro_usd")
    log.info("otel_enabled")
    return True


def tracer() -> Any:
    """The tracer, or None when tracing is off.

    Returns:
        An OpenTelemetry tracer or None.
    """
    return _tracer


def set_tracer(value: Any) -> None:
    """Use this tracer (tests, or an application that set OpenTelemetry up itself); None stops."""
    global _tracer
    _tracer = value


def current_context() -> Any:
    """The active OpenTelemetry context, to parent spans recorded later on it.

    Returns:
        The context, or None when tracing is off.
    """
    if _tracer is None:
        return None
    from opentelemetry import context

    return context.get_current()


@contextmanager
def span(name: str, **attributes: Any):
    """A tracing span, or nothing when tracing is off.

    Yields:
        The span or None.
    """
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(
        name, attributes={k: v for k, v in attributes.items() if v is not None}
    ) as s:
        yield s


def record(metric: str, value: float, **attributes: Any) -> None:
    """Record a metric value when metrics are on."""
    inst = _meter_instruments.get(metric)
    if inst is None:
        return
    attrs = {k: v for k, v in attributes.items() if v is not None}
    if hasattr(inst, "record"):
        inst.record(value, attrs)
    else:
        inst.add(value, attrs)
