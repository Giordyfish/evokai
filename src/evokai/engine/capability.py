"""Evokai's rules around every model request, as one pydantic-ai capability.

Once per request step (``before_model_request``):

- the caps and the token budget are checked (``RunState.before_call`` raises ``StopRun``);
- the model sees the history from Evokai's provider-neutral blocks, windowed, with tool output
  marked and identifiers protected by the guard, and the system prompt first (``RunState.view``);
  the run's own history is not changed, so what is stored stays what happened.

For every attempt (``prepare_model_request``, ``after_model_request``, ``on_model_request_error``):

- prompt caching is asked only of the model that supports it (``evokai.llm.caching``);
- each attempt is a ``model`` span of the trace, a ``provider_operation`` log and a model health
  record;
- a capacity error moves the same step to the next model of the chain, but only if nothing of the
  answer reached the user yet (``RetryModelRequest``); any other error ends the turn;
- an answered call reports its usage with the model that actually answered
  (``RunState.on_response``).

The no-provider-storage policy (``evokai.llm.policy``) is applied to the settings of every attempt.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import RetryModelRequest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.settings import ModelSettings

from evokai.core.util import clip
from evokai.engine.messages import rename_unknown, restore_names
from evokai.engine.state import Attempt, RunState
from evokai.engine.tools import UNKNOWN_TOOL
from evokai.llm.caching import cache_label, cache_settings
from evokai.llm.catalog import bare
from evokai.llm.errors import is_capacity_error, is_context_overflow
from evokai.llm.policy import enforce_settings
from evokai.observability.logging import error_class, provider_operation_extra
from evokai.observability.tracing import provider_of

log = logging.getLogger("evokai.engine")
ops = logging.getLogger("evokai.provider")


def with_added_parts(view: list[Any], messages: list[Any]) -> list[Any]:
    """The view the model sees, with the system parts capabilities added to the run.

    Evokai's view is rebuilt from its stored blocks, which keep no system parts: a reminder, a
    loop nudge, a review round or a plan a capability added to the run's history would be lost.
    They are carried to the end of the view's last request, in the order they were added (a part
    scoped to one request only while it is the latest); the system prompt at the head of the
    history is the view's own.

    Returns:
        The view.
    """
    last_index = len(messages) - 1
    added = [
        p
        for i, m in enumerate(messages)
        if isinstance(m, ModelRequest)
        for j, p in enumerate(m.parts)
        if isinstance(p, SystemPromptPart)
        and (i, j) != (0, 0)
        # A part meant for one request only reaches that request.
        and (getattr(p, "scope", None) != "request" or i == last_index)
    ]
    if not added or not view or not isinstance(view[-1], ModelRequest):
        return view
    last = view[-1]
    return [*view[:-1], replace(last, parts=[*last.parts, *added])]


@dataclass
class EvokaiCapability(AbstractCapability[RunState]):
    """Caps, history view, prompt caching, fallback, traces and usage of every model call."""

    async def before_model_request(
        self, ctx: RunContext[RunState], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Check the caps, then build the history the model sees.

        Returns:
            The request, with its messages replaced by the guarded view.
        """
        st = ctx.deps
        st.new_step()
        if st.before_call is not None:
            await st.before_call()
        if st.view is not None:
            request_context.messages = with_added_parts(
                st.view(list(ctx.messages)), list(ctx.messages)
            )
        else:
            request_context.messages = restore_names(list(request_context.messages), st.renamed)
        params = request_context.model_request_parameters
        hidden = [t for t in params.function_tools if t.name == UNKNOWN_TOOL]
        if hidden:
            request_context.model_request_parameters = replace(
                params, function_tools=[t for t in params.function_tools if t.name != UNKNOWN_TOOL]
            )
        return request_context

    async def prepare_model_request(
        self, ctx: RunContext[RunState], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Open the attempt: its span, and the model's own cache settings.

        Returns:
            The request for this attempt.
        """
        st = ctx.deps
        if st.extra.pop("reviewed", False) and st.view is not None:
            # The history was compacted after a too-long error: this attempt sends the new one.
            request_context.messages = with_added_parts(
                st.view(list(ctx.messages)), list(ctx.messages)
            )
        name = _name_of(st, request_context.model)
        index = st.attempts
        st.attempts += 1
        cached = cache_settings(
            request_context.model,
            name,
            cast(Any, st.prompt_cache),
            conversation=bool(request_context.model_request_parameters.function_tools),
        )
        settings = {**(request_context.model_settings or {}), **cached}
        request_context.model_settings = cast(ModelSettings, enforce_settings(settings))
        span = st.trace.start(
            "model",
            f"chat {bare(name)}",
            model=bare(name),
            provider=provider_of(name),
            step=st.step,
            fallback=True if index else None,
            maxTokens=st.max_tokens,
            temperature=settings.get("temperature"),
        )
        st.attempt = Attempt(name=name, started=time.perf_counter(), span=span, index=index)
        st.extra["promptCache"] = cache_label(
            cached, request_context.model, name, cast(Any, st.prompt_cache)
        )
        return request_context

    async def after_model_request(
        self,
        ctx: RunContext[RunState],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        """Close the attempt and report the call's usage.

        Returns:
            The response, unchanged.
        """
        st = ctx.deps
        a = st.attempt
        if a is not None:
            st.attempt = None
            await finish_attempt(st, a, response, request_context.messages)
        return rename_unknown(response, set(ctx.tools), st.renamed)

    async def on_model_request_error(
        self,
        ctx: RunContext[RunState],
        *,
        request_context: ModelRequestContext,
        error: Exception,
    ) -> ModelResponse:
        """Close the failed attempt; on a capacity error try the next model, if nothing was shown.

        The error itself is raised again when there is no next model or something was shown.

        Raises:
            RetryModelRequest: The same step goes to the next model.
        """
        st = ctx.deps
        a = st.attempt
        st.attempt = None
        st.extra["model_error"] = error
        name = a.name if a else _name_of(st, request_context.model)
        if a is not None and a.span is not None:
            st.trace.end(a.span, status="error", errorType=error_class(error))
        _op(st, name, a, outcome="error", error=error)
        if st.health is not None:
            st.health.record(bare(name), ok=False, error=error_class(error))
        log.warning(
            "model_failed",
            extra={
                **st.op_tags,
                "model": bare(name),
                "error": error_class(error),
                "detail": clip(str(error), 300),
            },
        )
        if (
            st.compact is not None
            and not st.shown
            and not st.extra.get("recovered")
            and is_context_overflow(error)
        ):
            # Too long for the model: compact once and send the same step again.
            st.extra["recovered"] = True
            if await st.compact(list(ctx.messages)):
                st.extra["model_error"] = None
                st.extra["reviewed"] = True
                raise RetryModelRequest(model=request_context.model) from error
        index = next((i for i, (n, _) in enumerate(st.models) if n == name), len(st.models))
        if not st.shown and index + 1 < len(st.models) and is_capacity_error(error):
            st.extra["model_error"] = None
            raise RetryModelRequest(model=st.models[index + 1][1]) from error
        raise error


async def finish_attempt(
    st: RunState, a: Attempt, response: ModelResponse, messages: list[Any] | None = None
) -> None:
    """Close an answered attempt: span, log, health, usage.

    Also used by the driver when a streamed answer was stopped early (the output filter), when
    pydantic-ai does not reach ``after_model_request``.
    """
    name = a.name
    u = response.usage
    latency = round((time.perf_counter() - a.started) * 1000)
    calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
    if a.span is not None:
        st.trace.end(
            a.span,
            inputTokens=u.input_tokens or 0,
            outputTokens=u.output_tokens or 0,
            cacheReadTokens=u.cache_read_tokens or 0,
            cacheWriteTokens=u.cache_write_tokens or 0,
            ttftMs=st.first_ms,
            stop="tool_call" if calls else "end_turn",
            toolCalls=len(calls) or None,
        )
        st.trace.put(
            a.span,
            input=st.extra.get("traceInput", _new_input(messages or [])),
            output={
                "text": "".join(p.content for p in response.parts if isinstance(p, TextPart)),
                **(
                    {"toolCalls": [{"name": c.tool_name, "args": c.args_as_dict()} for c in calls]}
                    if calls
                    else {}
                ),
            },
        )
    _op(
        st,
        name,
        a,
        outcome="success",
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cacheReadTokens=u.cache_read_tokens or 0,
        cacheWriteTokens=u.cache_write_tokens or 0,
        promptCache=st.extra.get("promptCache", "off"),
        tools=[c.tool_name for c in calls],
    )
    if st.health is not None:
        st.health.record(bare(name), ok=True, latency_ms=latency)
    st.answered = name
    if a.index:
        st.fallback = True
    if st.on_response is not None:
        await st.on_response(name, response, latency)


def _name_of(st: RunState, model: Any) -> str:
    for name, m in st.models:
        if m is model:
            return name
    return f"{getattr(model, 'system', 'model')}:{getattr(model, 'model_name', '')}"


def _op(
    st: RunState, name: str, a: Attempt | None, *, outcome: str, error: Any = None, **data: Any
) -> None:
    started = a.started if a is not None else time.perf_counter()
    ops.info(
        "provider_operation",
        extra=provider_operation_extra(
            provider="model",
            operation=st.operation,
            outcome=outcome,
            duration_ms=round((time.perf_counter() - started) * 1000),
            error_type=type(error).__name__ if error is not None else None,
            http_status=getattr(error, "status_code", None) if error is not None else None,
            model=bare(name),
            **st.op_tags,
            **data,
        ),
    )


def _new_input(messages: list[Any]) -> list[str]:
    """The parts of the last request a model call saw (its input, tool returns or a retry).

    Returns:
        The parts as text.
    """
    last = messages[-1] if messages else None
    if not isinstance(last, ModelRequest):
        return []
    return [
        str(getattr(p, "content", "") or "")
        for p in last.parts
        if not isinstance(p, SystemPromptPart)
    ]
