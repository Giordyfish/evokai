"""Traces of turns and server runs.

Every turn (browser, voice, eval case) and every server run is a trace: a root span and one span
per model call, tool call, guard decision, retrieval and speech, with their timings, counts and
attributes. The trace is stored with the turn (``evokai_traces``: a summary to filter on and the
spans as one JSON document) and, when OpenTelemetry is on, exported as OTLP spans that follow the
GenAI semantic conventions (``gen_ai.*``), so Langfuse, LangSmith, Grafana Tempo or any OTLP
backend can read it.

Content (the messages a model saw and wrote, tool arguments and results) is optional per
application, ``tracing_content``:

- ``none``: timings, counts and attributes only;
- ``masked`` (the default): content with identifiers masked and secrets removed;
- ``full``: content as it was, secrets still removed; it falls back to ``masked`` while the
  application enforces ``pii.history`` or ``pii.model``.

With external storage a trace never holds content, whatever the setting: Evokai keeps no message
content at rest. The same content, or none, goes to OpenTelemetry. Raw tool arguments are never
kept: what a span shows is what the model wrote (with personal data tokens, when ``pii.model``
tokenises), masked as above.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from evokai.core.util import new_id, utcnow
from evokai.guard import pii, secrets
from evokai.observability import telemetry

log = logging.getLogger("evokai.tracing")

CONTENT_MODES = ("none", "masked", "full")
DEFAULT_CONTENT = "masked"
CONTENT_CHARS = 4_000
"""Every content string of a span is clipped to this many characters."""
CONTENT_ITEMS = 12
"""A model span keeps at most this many of the messages new to that call."""
FAILED = frozenset({"error", "blocked"})
"""Tool outcomes that count as tool errors."""
MAX_SPANS = 400
"""A trace keeps at most this many spans (a loop must not grow a row without bound)."""


def content_mode(app: Any, *, storage: str, pii_masked: bool) -> str:
    """What content the traces of a conversation may hold.

    Args:
        app: The application (its ``defaults.tracing_content``).
        storage: The conversation's storage mode.
        pii_masked: Whether the turn enforces ``pii.history`` or ``pii.model``: then ``full``
            is read as ``masked``, so no raw identifier (in a tool's arguments either) is kept.

    Returns:
        ``none``, ``masked`` or ``full``.
    """
    if storage == "external":
        return "none"
    mode = str((getattr(app, "defaults", None) or {}).get("tracing_content") or DEFAULT_CONTENT)
    if mode not in CONTENT_MODES:
        mode = DEFAULT_CONTENT
    if mode == "full" and pii_masked:
        return "masked"
    return mode


def _clip(text: str) -> str:
    return text if len(text) <= CONTENT_CHARS else f"{text[:CONTENT_CHARS]}…"


@dataclass
class Span:
    """One timed step of a trace."""

    id: str
    parent: str | None
    kind: str
    """``turn``, ``run``, ``eval``, ``workflow`` (the root), ``model``, ``tool``, ``guard``,
    ``retrieval``, ``voice``, ``step`` (a workflow's step)."""
    name: str
    start: float
    """Milliseconds from the trace's start."""
    end: float | None = None
    status: str = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)
    content: dict[str, Any] = field(default_factory=dict)

    @property
    def ms(self) -> float:
        """Duration in milliseconds (0 while open)."""
        return max(0.0, (self.end if self.end is not None else self.start) - self.start)

    def set(self, **attrs: Any) -> None:
        """Add attributes (None values are left out)."""
        self.attrs.update({k: v for k, v in attrs.items() if v is not None})

    def as_json(self) -> dict[str, Any]:
        """The stored form: short, empty fields left out.

        Returns:
            A JSON-able dict.
        """
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "start": round(self.start),
            "ms": round(self.ms),
            "status": self.status,
        }
        if self.parent:
            out["parent"] = self.parent
        if self.attrs:
            out["attrs"] = self.attrs
        if self.content:
            out["content"] = self.content
        return out


class Trace:
    """The trace of one turn or run, being recorded."""

    def __init__(
        self,
        *,
        trace_id: str,
        kind: str,
        application_id: str,
        agent: str,
        version: str,
        subject: str,
        subject_key: str,
        session_id: str | None,
        mode: str = "text",
        channel: str = "browser",
        content: str = "none",
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        """Start a trace (its root span opens now).

        Args:
            trace_id: The turn's or run's id.
            kind: ``turn``, ``run``, ``eval`` or ``workflow``.
            application_id: The application.
            agent: The agent's name.
            version: The agent's version.
            subject: The subject (stored for erase, never exported).
            subject_key: A keyed hash of the subject (shown, searched, exported as the user).
            session_id: The conversation, when there is one.
            mode: ``text`` or ``voice``.
            channel: ``browser`` or ``server``.
            content: ``none``, ``masked`` or ``full`` (see :func:`content_mode`).
            known_secrets: The deployment's own keys, always removed from content.
        """
        self.id = trace_id
        self.kind = kind
        self.application_id = application_id
        self.agent = agent
        self.version = version
        self.subject = subject
        self.subject_key = subject_key
        self.session_id = session_id
        self.mode = mode
        self.channel = channel
        self.content = content if content in CONTENT_MODES else "none"
        self.known_secrets = known_secrets
        self.started_at: datetime = utcnow()
        self._t0 = time.perf_counter()
        self._epoch_ns = time.time_ns()
        self.spans: list[Span] = []
        self._stack: list[Span] = []
        self.cost_micro_usd = 0
        self.feedback: int | None = None
        self.root = self.start(kind, f"invoke_agent {agent}")
        self._stack.append(self.root)
        self._otel_parent = telemetry.current_context()

    # recording

    def now(self) -> float:
        """Milliseconds since the trace started.

        Returns:
            The offset.
        """
        return (time.perf_counter() - self._t0) * 1000

    def start(self, kind: str, name: str, *, parent: Span | None = None, **attrs: Any) -> Span:
        """Open a span (a child of the innermost open one, unless a parent is given).

        Returns:
            The span (dropped silently past ``MAX_SPANS``, but still usable).
        """
        p = parent or (self._stack[-1] if self._stack else None)
        span = Span(
            id=new_id().replace("-", "")[:16],
            parent=p.id if p else None,
            kind=kind,
            name=name[:120],
            start=self.now(),
        )
        span.set(**attrs)
        if len(self.spans) < MAX_SPANS:
            self.spans.append(span)
        return span

    def end(self, span: Span, *, status: str | None = None, **attrs: Any) -> Span:
        """Close a span.

        Returns:
            The span.
        """
        if span.end is None:
            span.end = self.now()
        if status:
            span.status = status
        span.set(**attrs)
        if span.kind == "tool" and span.status == "ok" and span.attrs.get("outcome") in FAILED:
            span.status = "error"
        return span

    def push(self, span: Span) -> None:
        """Make a span the parent of the spans opened next (until ``pop``)."""
        self._stack.append(span)

    def pop(self, span: Span) -> None:
        """Stop parenting new spans on a span pushed before."""
        if span in self._stack:
            self._stack.remove(span)

    @contextlib.contextmanager
    def span(self, kind: str, name: str, **attrs: Any) -> Generator[Span]:
        """A span around a block; spans opened inside become its children.

        Yields:
            The span. An exception marks it ``error`` (with ``errorType``) and goes on.
        """
        s = self.start(kind, name, **attrs)
        self._stack.append(s)
        try:
            yield s
        except BaseException as e:
            cancelled = "Cancel" in type(e).__name__
            self.end(s, status="cancelled" if cancelled else "error", errorType=type(e).__name__)
            raise
        finally:
            if self._stack and self._stack[-1] is s:
                self._stack.pop()
            self.end(s)

    def event(self, kind: str, name: str, *, ms: float = 0, **attrs: Any) -> Span:
        """A point in time (a guard decision), or a step measured elsewhere (``ms`` long).

        Returns:
            The span.
        """
        s = self.start(kind, name, **attrs)
        s.start = max(0.0, s.start - ms)
        s.end = s.start + ms
        return s

    # content

    def text(self, value: Any) -> str | None:
        """A content value as the trace may keep it.

        Returns:
            The text (masked or as it was, secrets removed, clipped), or None without content.
        """
        if self.content == "none" or value is None:
            return None
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        text = text[: CONTENT_CHARS * 3]
        if self.content == "masked":
            text = pii.mask(text)
        return _clip(secrets.redact(text, known=self.known_secrets))

    def put(self, span: Span, **values: Any) -> None:
        """Attach content to a span, as the application allows (nothing with ``none``)."""
        if self.content == "none":
            return
        for k, v in values.items():
            if isinstance(v, list):
                kept = [t for t in (self.text(x) for x in v[-CONTENT_ITEMS:]) if t is not None]
                if kept:
                    span.content[k] = kept
            elif (t := self.text(v)) is not None:
                span.content[k] = t

    # the end

    def finish(self, *, status: str, reason: str | None, export: bool = True) -> dict[str, Any]:
        """Close the trace.

        Args:
            status: How it ended.
            reason: Why.
            export: Send it to OpenTelemetry (a workflow's trace is rebuilt while it runs and
                exported once, at the end).

        Returns:
            The row to store (``evokai_traces``).
        """
        for s in self.spans:
            if s.end is None:
                self.end(s)
        root = self.root
        root.status = "error" if status == "failed" else "ok"
        root.set(status=status, reason=reason)
        models = [s for s in self.spans if s.kind == "model"]
        tools = [s for s in self.spans if s.kind == "tool"]
        answered = [s for s in models if s.status == "ok"]
        tool_errors = sum(1 for s in tools if s.attrs.get("outcome") in FAILED)
        first = root.attrs.get("firstTokenMs")

        def total(key: str) -> int:
            return sum(int(s.attrs.get(key) or 0) for s in answered)

        row = {
            "id": self.id,
            "application_id": self.application_id,
            "kind": self.kind,
            "session_id": self.session_id,
            "subject": self.subject,
            "subject_key": self.subject_key[:16],
            "agent_name": self.agent[:80],
            "agent_version": self.version[:40],
            "mode": self.mode,
            "status": status[:16],
            "reason": (reason or None) and str(reason)[:40],
            "started_at": self.started_at,
            "duration_ms": round(root.ms),
            "first_token_ms": round(first) if isinstance(first, (int, float)) else None,
            "model": str(answered[-1].attrs.get("model"))[:120] if answered else None,
            "fallback": any(s.attrs.get("fallback") for s in answered),
            "input_tokens": total("inputTokens"),
            "output_tokens": total("outputTokens"),
            "cache_read_tokens": total("cacheReadTokens"),
            "cache_write_tokens": total("cacheWriteTokens"),
            "model_calls": len(models),
            "tool_calls": len(tools),
            "tool_errors": tool_errors,
            # A guard decision has an action; the span around a check (``guard input``) has none.
            "guard_events": sum(1 for s in self.spans if s.kind == "guard" and "action" in s.attrs),
            "error": status == "failed"
            or tool_errors > 0
            or any(s.status == "error" for s in self.spans),
            "feedback": self.feedback,
            "cost_micro_usd": int(self.cost_micro_usd),
            "content": self.content,
            "spans": [s.as_json() for s in self.spans],
        }
        root.set(
            inputTokens=row["input_tokens"],
            outputTokens=row["output_tokens"],
            toolCalls=row["tool_calls"],
        )
        row["spans"][0] = root.as_json()
        if export:
            export_otel(self)
        return row


# OpenTelemetry (GenAI semantic conventions)

_PROVIDERS = {
    "bedrock": "aws.bedrock",
    "openai": "openai",
    "azure": "azure.ai.openai",
    "anthropic": "anthropic",
    "anthropic-foundry": "anthropic",
    "mistral": "mistral_ai",
    "google-gla": "gcp.gemini",
    "google-vertex": "gcp.vertex_ai",
    "groq": "groq",
    "cohere": "cohere",
    "ollama": "ollama",
}


def provider_of(model: str) -> str:
    """The ``gen_ai.provider.name`` of a model id (``bedrock:eu.anthropic...``).

    Returns:
        The provider's semantic-convention name, or the id's prefix.
    """
    from evokai.llm.catalog import normalize

    prefix = normalize(model).partition(":")[0]
    return _PROVIDERS.get(prefix, prefix)


_COMMON = {
    "model": "gen_ai.request.model",
    "inputTokens": "gen_ai.usage.input_tokens",
    "outputTokens": "gen_ai.usage.output_tokens",
    "errorType": "error.type",
}
_BY_KIND: dict[str, dict[str, str]] = {
    "model": {
        "provider": "gen_ai.provider.name",
        "cacheReadTokens": "gen_ai.usage.cache_read.input_tokens",
        "cacheWriteTokens": "gen_ai.usage.cache_creation.input_tokens",
        "maxTokens": "gen_ai.request.max_tokens",
        "temperature": "gen_ai.request.temperature",
    },
    "tool": {
        "tool": "gen_ai.tool.name",
        "callId": "gen_ai.tool.call.id",
        "server": "evokai.mcp.server",
    },
    "retrieval": {"source": "gen_ai.data_source.id"},
}
_CONTENT = {
    ("model", "input"): "gen_ai.input.messages",
    ("model", "output"): "gen_ai.output.messages",
    ("tool", "args"): "gen_ai.tool.call.arguments",
    ("tool", "result"): "gen_ai.tool.call.result",
}


def _snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name)


def _value(v: Any) -> Any:
    if isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)) and all(isinstance(x, (bool, int, float, str)) for x in v):
        return list(v)
    return json.dumps(v, ensure_ascii=False, default=str)


def otel_span(trace: Trace, span: Span) -> tuple[str, dict[str, Any]]:
    """The name and attributes of a span in OpenTelemetry's GenAI conventions.

    Returns:
        (span name, attributes).
    """
    a = span.attrs
    attrs: dict[str, Any] = {"evokai.span.kind": span.kind}
    if span is trace.root and trace.kind == "workflow":
        name = f"workflow {trace.agent}"
        attrs.update({
            "gen_ai.operation.name": "workflow",
            "evokai.workflow.name": trace.agent,
            "evokai.workflow.version": trace.version,
            "evokai.application.id": trace.application_id,
            "evokai.trace.kind": trace.kind,
            "user.id": trace.subject_key[:16],
        })
    elif span is trace.root:
        name = f"invoke_agent {trace.agent}"
        attrs.update({
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": trace.agent,
            "gen_ai.agent.version": trace.version,
            "evokai.application.id": trace.application_id,
            "evokai.trace.kind": trace.kind,
            "evokai.channel": trace.channel,
            "evokai.mode": trace.mode,
            "evokai.content": trace.content,
            "user.id": trace.subject_key[:16],
        })
        if trace.session_id:
            attrs["gen_ai.conversation.id"] = trace.session_id
            attrs["session.id"] = trace.session_id
    elif span.kind == "model":
        model = str(a.get("model") or "")
        name = f"chat {model}"
        attrs["gen_ai.operation.name"] = "chat"
        attrs.setdefault("gen_ai.provider.name", provider_of(model))
        attrs["gen_ai.response.model"] = model
        if a.get("stop"):
            attrs["gen_ai.response.finish_reasons"] = [str(a["stop"])]
    elif span.kind == "tool":
        name = f"execute_tool {a.get('tool') or span.name}"
        attrs["gen_ai.operation.name"] = "execute_tool"
        attrs["gen_ai.tool.type"] = "extension" if a.get("server") else "function"
    else:
        name = span.name
    mapping = {**_COMMON, **_BY_KIND.get(span.kind, {})}
    for k, v in a.items():
        if v is None:
            continue
        key = mapping.get(k) or f"evokai.{span.kind}.{_snake(k)}"
        attrs[key] = _value(v)
    if trace.content != "none":
        for k, v in span.content.items():
            key = _CONTENT.get((span.kind, k)) or f"evokai.{span.kind}.content.{_snake(k)}"
            attrs[key] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return name, attrs


def export_otel(trace: Trace) -> int:
    """Send a finished trace to OpenTelemetry, when tracing is on (never raises).

    Spans are created with their recorded start and end times, children under their parents, the
    root under the request's span when there was one.

    Returns:
        How many spans were exported.
    """
    tracer = telemetry.tracer()
    if tracer is None:
        return 0
    try:
        from opentelemetry import trace as ot
        from opentelemetry.trace import Status, StatusCode

        made: dict[str, Any] = {}
        n = 0
        for s in trace.spans:
            name, attrs = otel_span(trace, s)
            parent = made.get(s.parent or "")
            ctx = ot.set_span_in_context(parent) if parent is not None else trace._otel_parent
            start = trace._epoch_ns + int(s.start * 1_000_000)
            end = trace._epoch_ns + int((s.end if s.end is not None else s.start) * 1_000_000)
            kind = ot.SpanKind.CLIENT if s.kind in {"model", "tool"} else ot.SpanKind.INTERNAL
            o = tracer.start_span(name, context=ctx, kind=kind, attributes=attrs, start_time=start)
            if s.status == "error":
                o.set_status(Status(StatusCode.ERROR, str(s.attrs.get("errorType") or "")))
            o.end(end_time=end)
            made[s.id] = o
            n += 1
        return n
    except Exception as e:
        log.warning("trace_not_exported", extra={"error": type(e).__name__})
        return 0
