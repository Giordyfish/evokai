"""The worker's model: every model call goes through Evokai, never straight to a provider.

browser-use's own agent makes model calls. The worker holds no provider key and no Evokai key:
``EvokaiModel`` sends each request to Evokai (``POST {EVOKAI_URL}/v1/workers/model``) with the call
token Evokai gave with the conversation's latest MCP call (``_meta["evokai/token"]``): signed by
Evokai for that turn, the person, the agent and the tags, valid a few minutes at most and only
while the turn runs. Evokai picks a model the agent may use, checks the caps first (a spent cap is
429 ``limit``), applies the no-provider-storage policy, records the call as the person's usage
and passes its text through the guard's output checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

_PARAMS = TypeAdapter(ModelRequestParameters)
_RESPONSE = TypeAdapter(ModelResponse)


class CapReached(Exception):
    """Evokai refused the call: a cap of the person or the application is spent."""


class TurnOver(Exception):
    """Evokai refused the call: the turn's token expired or the turn is over."""


@dataclass(init=False)
class EvokaiModel(Model):
    """A pydantic-ai model that asks Evokai to make each request, with the turn's call token."""

    url: str
    meta: dict[str, Any] = field(default_factory=dict)
    http: httpx.AsyncClient | None = None

    def __init__(
        self,
        url: str,
        meta: dict[str, Any],
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """Bind to Evokai and to the conversation.

        Args:
            url: Evokai's base URL (``https://host/agent``).
            meta: The conversation's ``_meta``, updated by every MCP call (its ``evokai/token``
                is the current turn's).
            http: The HTTP client (tests).
        """
        super().__init__()
        self.url, self.meta, self.http = url.rstrip("/"), meta, http

    @property
    def model_name(self) -> str:
        """Evokai chooses the model.

        Returns:
            ``evokai``.
        """
        return "evokai"

    @property
    def system(self) -> str:
        """The provider, as pydantic-ai names it.

        Returns:
            ``evokai``.
        """
        return "evokai"

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Make one request through Evokai.

        Returns:
            The response.

        Raises:
            CapReached: A cap is spent.
        """
        body = {
            "messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
            "parameters": _PARAMS.dump_python(model_request_parameters, mode="json"),
            "settings": dict(model_settings or {}),
        }
        headers = {"Authorization": f"Bearer {self.meta.get('evokai/token') or ''}"}
        if self.http is not None:
            r = await self.http.post(f"{self.url}/v1/workers/model", json=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{self.url}/v1/workers/model", json=body, headers=headers)
        if r.status_code == 429:
            raise CapReached(r.json().get("message") or "A cap is spent.")
        if r.status_code == 401:
            raise TurnOver(r.json().get("message") or "The turn is over.")
        r.raise_for_status()
        return _RESPONSE.validate_python(r.json()["response"])
