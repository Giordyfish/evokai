"""A model a capability calls by itself (a judge, a reviewer, an advisor), within Evokai's rules.

Capabilities that make their own model calls do so outside the run's model request hooks, so the
engine hands them a ``RecordedModel``: every call has its settings passed through
``evokai.llm.policy.enforce_settings`` (no provider-side state) and is recorded as usage of kind
``cap`` with the model that answered, counted against the caps like any call.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from evokai.llm.policy import enforce_settings

USAGE_KIND = "cap"
"""The usage kind of a capability's own model calls (the column holds eight characters)."""

Record = Callable[[str, ModelResponse], Awaitable[None]]
"""Records one answered call: ``(Evokai model name, response)``."""


class RecordedModel(WrapperModel):
    """A model whose every call is recorded and kept free of provider-side state."""

    def __init__(self, wrapped: Model, name: str, record: Record) -> None:
        """Wrap a model.

        Args:
            wrapped: The model.
            name: Its Evokai name (``bedrock:eu.anthropic...``), for usage and prices.
            record: Records an answered call.
        """
        super().__init__(wrapped)
        self.evokai_name = name
        self._record = record

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """One call, recorded.

        Returns:
            The response.
        """
        settings = cast(ModelSettings, enforce_settings(dict(model_settings or {})))
        response = await super().request(messages, settings, model_request_parameters)
        await self._record(self.evokai_name, response)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncGenerator[StreamedResponse]:
        """One streamed call, recorded once it is consumed.

        Yields:
            The stream.
        """
        settings = cast(ModelSettings, enforce_settings(dict(model_settings or {})))
        async with super().request_stream(
            messages, settings, model_request_parameters, run_context
        ) as stream:
            yield stream
        await self._record(self.evokai_name, stream.get())
