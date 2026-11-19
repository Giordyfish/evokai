"""Models: any pydantic-ai provider, behind an allow-list.

Model objects are built once and reused. A model whose provider endpoint the provider catalog
describes with a connection (``evokai.llm.providers``: an Azure resource and its deployment, a
Vertex location, the base URL of an OpenAI-compatible EU provider or of a self-hosted server) is
built from it; Bedrock in the deployment's region (or the endpoint's); anything else with
pydantic-ai's ``infer_model``.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from typing import Any

from pydantic_ai.models import Model, infer_model

from evokai.core.errors import ConfigError

KNOWN_PROVIDERS = {
    "bedrock",
    "openai",
    "openai-chat",
    "openai-responses",
    "azure",
    "anthropic",
    "mistral",
    "google-gla",
    "google-vertex",
    "google",
    "google-cloud",
    "groq",
    "cohere",
    "ollama",
    "deepseek",
    "openrouter",
    "huggingface",
    "ovhcloud",
    "vllm",
    "lmstudio",
    "scaleway",
    "ionos",
    "stackit",
    "aleph-alpha",
    "mistral-eu",
    "mistral-us",
    "test",
    "function",
}
"""Model name prefixes: pydantic-ai's providers, and every provider of the catalog
(``register_providers``)."""


def register_providers(prefixes: set[str]) -> None:
    """Make the provider catalog's ids known prefixes (``scaleway:``, a deployment's ``our-vllm:``).

    Args:
        prefixes: Provider ids.
    """
    KNOWN_PROVIDERS.update(prefixes)


def normalize(model: str) -> str:
    """Give a model name its provider.

    Bare Bedrock ids (eu.anthropic…, with their own ':0') become 'bedrock:…'.

    Returns:
        provider:name.
    """
    prefix, sep, _ = model.partition(":")
    return model if sep and prefix in KNOWN_PROVIDERS else f"bedrock:{model}"


def bare(model: str) -> str:
    """The model name without the provider.

    Returns:
        e.g. eu.anthropic.claude-haiku-4-5-20251001-v1:0.
    """
    return normalize(model).partition(":")[2]


def provider_of(model: str) -> str:
    """The provider prefix of a model name.

    Returns:
        e.g. ``bedrock``.
    """
    return normalize(model).partition(":")[0]


def _key(env: str | None, provider: str) -> str | None:
    if env is None:
        return None
    value = os.environ.get(env)
    if not value:
        raise ConfigError(f"{provider}: the API key variable {env} is not set")
    return value


def build_from_endpoint(name: str, endpoint: Any, aws_region: str) -> Model | None:
    """A model built from its catalog endpoint's connection, when the endpoint has one.

    Args:
        name: ``provider:model``.
        endpoint: The endpoint (``evokai.llm.providers.Endpoint``) or None.
        aws_region: The deployment's region (Bedrock without a region of its own).

    Returns:
        The model, or None to use the default construction.

    Raises:
        ConfigError: The endpoint lacks what its API needs, or the provider's package is not
        installed.
    """
    provider, _, model = name.partition(":")
    api = getattr(endpoint, "api", None)
    if endpoint is None or api in (None, "pydantic-ai"):
        return None
    if api == "bedrock":
        from pydantic_ai.models.bedrock import BedrockConverseModel
        from pydantic_ai.providers.bedrock import BedrockProvider

        return BedrockConverseModel(
            model, provider=BedrockProvider(region_name=endpoint.region or aws_region)
        )
    if api == "openai-compatible":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider

        if not endpoint.base_url:
            raise ConfigError(f"{provider}: endpoint {endpoint.id} needs a base_url")
        key = _key(endpoint.api_key_env, provider) or "none"
        return OpenAIChatModel(
            model,
            provider=OpenAICompatibleProvider(provider, base_url=endpoint.base_url, api_key=key),
        )
    if api == "azure":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.azure import AzureProvider

        if not endpoint.base_url or not endpoint.api_version:
            raise ConfigError(f"azure: endpoint {endpoint.id} needs base_url and api_version")
        return OpenAIChatModel(
            model,
            provider=AzureProvider(
                azure_endpoint=endpoint.base_url,
                api_version=endpoint.api_version,
                api_key=_key(endpoint.api_key_env, provider),
            ),
        )
    if api == "mistral":
        from pydantic_ai.models.mistral import MistralModel
        from pydantic_ai.providers.mistral import MistralProvider

        return MistralModel(
            model,
            provider=MistralProvider(
                api_key=_key(endpoint.api_key_env, provider), base_url=endpoint.base_url
            ),
        )
    if api == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(
            model,
            provider=AnthropicProvider(
                api_key=_key(endpoint.api_key_env, provider), base_url=endpoint.base_url
            ),
        )
    if api == "google-vertex":
        try:
            from pydantic_ai.models.google import GoogleModel
            from pydantic_ai.providers.google_cloud import GoogleCloudProvider
        except ImportError as e:
            raise ConfigError("google-vertex needs pydantic-ai-slim[google] installed") from e
        if not endpoint.location:
            raise ConfigError(
                f"{provider}: endpoint {endpoint.id} needs a location (e.g. europe-west4 or eu)"
            )
        return GoogleModel(
            model,
            provider=GoogleCloudProvider(project=endpoint.project, location=endpoint.location),
        )
    raise ConfigError(f"{provider}: unknown endpoint api {api}")


class ModelCatalog:
    """Models allowed in this deployment, built once and reused."""

    def __init__(
        self,
        allow: list[str],
        region: str,
        factory: Callable[[str], Model] | None = None,
        providers: Any = None,
    ) -> None:
        """Create the catalog.

        Args:
            allow: Glob patterns over provider:model.
            region: AWS region for Bedrock.
            factory: Builds a model from its name (tests).
            providers: The provider catalog (``evokai.llm.providers``); default the process's.
        """
        self.allow = allow
        self.region = region
        self._factory = factory
        self._providers = providers
        self._models: dict[str, Model] = {}

    @property
    def providers(self) -> Any:
        """The provider catalog.

        Returns:
            ``evokai.llm.providers.ProviderCatalog``.
        """
        if self._providers is None:
            from evokai.llm import providers

            return providers.current()
        return self._providers

    def allowed(self, model: str) -> bool:
        """Whether a model may be used here.

        Returns:
            True if it matches the allow-list.
        """
        name = normalize(model)
        return any(fnmatch.fnmatchcase(name, pat) for pat in self.allow)

    def get(self, model: str) -> Model:
        """The model object for a name.

        Returns:
            A pydantic-ai model.

        Raises:
            ConfigError: The model is not allowed.
        """
        name = normalize(model)
        if not self.allowed(name):
            raise ConfigError(f"model not allowed: {name}")
        hit = self._models.get(name)
        if hit:
            return hit
        if self._factory:
            built = self._factory(name)
        elif name.startswith("test:"):
            built = test_model()
        else:
            built = build_from_endpoint(name, self.providers.endpoint_of(name), self.region)
            if built is None and name.startswith("bedrock:"):
                from pydantic_ai.models.bedrock import BedrockConverseModel
                from pydantic_ai.providers.bedrock import BedrockProvider

                built = BedrockConverseModel(
                    bare(name), provider=BedrockProvider(region_name=self.region)
                )
            elif built is None:
                built = infer_model(name)
        self._models[name] = built
        return built


TEST_ANSWER = (
    "This is Evokai's test model: it answers with this text and calls no tools. Pick a real model "
    "in the agent's settings to talk to a language model."
)


def test_model() -> Model:
    """The ``test:`` model: a fixed answer and no tool calls, to try the flow without a provider.

    Returns:
        A pydantic-ai test model.
    """
    from pydantic_ai.models.test import TestModel

    return TestModel(call_tools=[], custom_output_text=TEST_ANSWER)
