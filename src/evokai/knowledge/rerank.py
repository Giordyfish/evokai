"""Reranking: a cross-encoder orders a knowledge search's candidates by relevance to the query.

Optional and off by default. ``EVOKAI_RERANK_MODEL`` names a reranker as ``provider:model``;
its endpoint in the provider catalog (``evokai.llm.providers``, ``EVOKAI_PROVIDERS_FILE``) gives
the base URL, the key (``api_key_env``, sent as a bearer token) and the facts the data protection
profile reads, exactly as for a chat or an embedding model. One API, the Cohere v2 rerank shape
(``POST {base_url}/rerank`` with ``model``, ``query``, ``documents``, ``top_n``; the answer's
``results`` carry ``index`` and ``relevance_score``), served by:

- Cohere Rerank in Microsoft Foundry (``azure:Cohere-rerank-v4.0-fast`` on an ``api: azure``
  endpoint whose base URL ends in ``/providers/cohere/v2``; without a key, a Microsoft Entra ID
  token of the managed identity);
- a self-hosted server: vLLM (``/v2/rerank``), Infinity (``/rerank``), on an
  ``api: openai-compatible`` endpoint.

A knowledge base turns it on with ``retrieval.rerank``. A reranker that fails, times out or is not
allowed by the application's profile leaves the fused order as it is: a search never fails because
of it.

https://docs.cohere.com/v2/reference/rerank
https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html#re-rank-api
Not verified against a live Azure account.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from evokai.core.errors import ConfigError
from evokai.llm.catalog import _key, normalize

log = logging.getLogger("evokai.knowledge")

MAX_DOCUMENT_CHARS = 4_000
"""Characters of a passage sent to the reranker."""


class Reranker:
    """A reranker behind the Cohere v2 rerank API, connected by its catalog endpoint."""

    def __init__(self, model: str, endpoint: Any, *, timeout: float = 5.0) -> None:
        """Bind the model to its endpoint.

        Args:
            model: ``provider:model``.
            endpoint: Its catalog endpoint (``evokai.llm.providers.Endpoint``).
            timeout: Seconds a rerank call may take.

        Raises:
            ConfigError: The endpoint has no base URL.
        """
        self.model = normalize(model)
        if endpoint is None or not endpoint.base_url:
            raise ConfigError(
                f"EVOKAI_RERANK_MODEL {self.model}: declare its endpoint with a base_url in "
                "EVOKAI_PROVIDERS_FILE"
            )
        self.url = endpoint.base_url.rstrip("/") + "/rerank"
        self.api = endpoint.api
        self.key_env = endpoint.api_key_env
        self.timeout = timeout
        self._token: Any = None

    @classmethod
    def from_settings(cls, settings: Any) -> Reranker | None:
        """The deployment's reranker (``EVOKAI_RERANK_MODEL``), or None.

        Returns:
            The reranker.
        """
        if not settings.rerank_model:
            return None
        from evokai.llm import providers

        return cls(settings.rerank_model, providers.current().endpoint_of(settings.rerank_model))

    async def _headers(self) -> dict[str, str]:
        key = _key(self.key_env, self.model.partition(":")[0])
        if key:
            return {"Authorization": f"Bearer {key}"}
        if self.api == "azure":
            if self._token is None:
                from evokai.core.azure import COGNITIVE_SERVICES, token_provider

                self._token = token_provider(COGNITIVE_SERVICES)
            return {"Authorization": f"Bearer {await self._token()}"}
        return {}

    async def rerank(
        self, http: httpx.AsyncClient, query: str, documents: Sequence[str]
    ) -> list[float] | None:
        """Score each passage against the question.

        Args:
            http: The HTTP client.
            query: The question.
            documents: The passages, in their current order.

        Returns:
            One relevance score (0 to 1) per passage, or None when the reranker failed.
        """
        body = {
            "model": self.model.partition(":")[2],
            "query": query,
            "documents": [d[:MAX_DOCUMENT_CHARS] for d in documents],
            "top_n": len(documents),
        }
        try:
            r = await http.post(
                self.url, json=body, headers=await self._headers(), timeout=self.timeout
            )
            if r.status_code >= 400:
                log.warning(
                    "knowledge_rerank_failed",
                    extra={"model": self.model, "error": f"HTTP {r.status_code}"},
                )
                return None
            scores = [0.0] * len(documents)
            for item in r.json().get("results") or []:
                i = int(item["index"])
                if 0 <= i < len(documents):
                    scores[i] = float(item["relevance_score"])
            return scores
        except Exception as e:  # the fused order stays: a search never fails because of it
            log.warning(
                "knowledge_rerank_failed", extra={"model": self.model, "error": str(e)[:200]}
            )
            return None
