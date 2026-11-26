"""OpenTelemetry traces, metrics and logs. Optional and off by default (EVOKAI_OTEL_ENABLED).

When enabled, the OTLP exporters read the standard variables (OTEL_EXPORTER_OTLP_ENDPOINT,
OTEL_EXPORTER_OTLP_HEADERS, OTEL_RESOURCE_ATTRIBUTES…): an OpenTelemetry Collector of the
deployment's own, or any OTLP backend. The packages are an optional extra: ``uv sync --extra otel``.

``EVOKAI_OTEL_EXPORTER=azure-monitor`` sends the same traces and metrics to Azure Monitor
Application Insights with Microsoft's exporter (the ``azure`` extra): the resource from
``APPLICATIONINSIGHTS_CONNECTION_STRING``, Microsoft Entra ID authentication from
``APPLICATIONINSIGHTS_AUTHENTICATION_STRING`` (``Authorization=AAD`` for the system-assigned managed
identity, ``Authorization=AAD;ClientId=<id>`` for a user-assigned one), and the
``ApplicationInsightsSampler`` the exporter requires (every trace kept).
https://learn.microsoft.com/en-us/python/api/overview/azure/monitor-opentelemetry-exporter-readme
Not verified against a live Azure account.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("evokai.telemetry")

_tracer: Any = None
_meter_instruments: dict[str, Any] = {}


def exporters(kind: str) -> tuple[Any, Any, Any] | None:
    """The span exporter, metric exporter and sampler of an exporter kind.

    Args:
        kind: ``otlp`` or ``azure-monitor`` (``EVOKAI_OTEL_EXPORTER``).

    Returns:
        The three (the sampler None for the SDK's default), or None when the package is missing
        or the exporter cannot be built (Azure Monitor without its connection string).
    """
    if kind == "azure-monitor":
        try:
            from azure.monitor.opentelemetry.exporter import (
                ApplicationInsightsSampler,
                AzureMonitorMetricExporter,
                AzureMonitorTraceExporter,
            )
        except ImportError:
            log.warning("otel_unavailable", extra={"hint": "install the 'azure' extra"})
            return None
        try:
            return (
                AzureMonitorTraceExporter(),
                AzureMonitorMetricExporter(),
                ApplicationInsightsSampler(1.0),
            )
        except ValueError as e:  # no APPLICATIONINSIGHTS_CONNECTION_STRING
            log.warning("otel_unavailable", extra={"hint": str(e)[:200]})
            return None
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(), OTLPMetricExporter(), None


def setup_telemetry(app: Any, *, enabled: bool, service_name: str, exporter: str = "otlp") -> bool:
    """Enable OpenTelemetry when asked and installed.

    Args:
        app: The FastAPI app to instrument.
        enabled: The EVOKAI_OTEL_ENABLED flag.
        service_name: Resource service name.
        exporter: ``otlp`` or ``azure-monitor`` (EVOKAI_OTEL_EXPORTER).

    Returns:
        Whether tracing is on.
    """
    global _tracer
    if not enabled:
        return False
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        made = exporters(exporter)
    except ImportError:
        log.warning("otel_unavailable", extra={"hint": "install the 'otel' extra"})
        return False
    if made is None:
        return False
    span_exporter, metric_exporter, sampler = made
    resource = Resource.create({"service.name": service_name})
    tp = (
        TracerProvider(resource=resource, sampler=sampler)
        if sampler is not None
        else TracerProvider(resource=resource)
    )
    tp.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource, metric_readers=[PeriodicExportingMetricReader(metric_exporter)]
        )
    )
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    _tracer = trace.get_tracer("evokai")
    meter = metrics.get_meter("evokai")
    _meter_instruments["turn_latency"] = meter.create_histogram("evokai.turn.latency_ms", unit="ms")
    _meter_instruments["model_cost"] = meter.create_counter("evokai.model.cost_micro_usd")
    log.info("otel_enabled", extra={"exporter": exporter})
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
