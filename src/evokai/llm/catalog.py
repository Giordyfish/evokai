"""Models: any pydantic-ai provider, behind an allow-list.

Model objects are built once and reused. A model whose provider endpoint the provider catalog
describes with a connection (``evokai.llm.providers``: an Azure resource and its deployment, Claude
in a Microsoft Foundry resource, a Vertex location, the base URL of an OpenAI-compatible EU
provider or of a self-hosted server) is built from it; Azure models without one from the standard
variables (``from_environment``), with a Microsoft Entra ID token when there is no key; Bedrock in
the deployment's region (or the endpoint's); anything else with pydantic-ai's ``infer_model``.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from pydantic_ai.models import Model, infer_model

from evokai.core.errors import ConfigError

KNOWN_PROVIDERS = {
    "bedrock",
    "openai",
    "openai-chat",
    "openai-responses",
    "azure",
    "anthropic",
    "anthropic-foundry",
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


def _v1_base_url(endpoint: str) -> str | None:
    # The Azure OpenAI v1 API (``.../openai/v1``) and Foundry serverless endpoints
    # (``*.models.ai.azure.com``) take no api-version: a plain OpenAI client reaches them.
    stripped = endpoint.rstrip("/")
    if stripped.endswith("/v1"):
        return stripped
    if (urlsplit(stripped).hostname or "").endswith(".models.ai.azure.com"):
        return f"{stripped}/v1"
    return None


def azure_provider(
    base_url: str, api_version: str | None, key: str | None, *, http_client: Any = None
) -> Any:
    """A pydantic-ai Azure provider: the resource's key, or a Microsoft Entra ID token without one.

    Azure OpenAI and the OpenAI v1 endpoint of a Foundry resource
    (``https://<resource>.openai.azure.com/openai/v1`` or
    ``https://<resource>.services.ai.azure.com/openai/v1``, which also serves the Foundry models
    sold by Azure: DeepSeek, Llama, Mistral, Grok...) take an ``api-key`` or an Entra ID token for
    the ``https://cognitiveservices.azure.com/.default`` scope. Without a key (neither the
    endpoint's ``api_key_env`` nor ``AZURE_OPENAI_API_KEY``) the token comes from the managed
    identity (``evokai.core.azure``). A dated endpoint needs ``api_version``; a v1 one refuses it.
    https://learn.microsoft.com/en-us/azure/ai-foundry/openai/api-version-lifecycle
    https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/managed-identity

    Args:
        base_url: The resource (``https://<resource>.openai.azure.com``) or its v1 base URL.
        api_version: The data plane version of a dated endpoint (``2024-10-21``).
        key: The resource's key, or None.
        http_client: An ``httpx2.AsyncClient`` of its own (tests).

    Returns:
        ``pydantic_ai.providers.azure.AzureProvider``.

    Raises:
        ConfigError: A dated endpoint without an api_version, or a v1 one with it.
    """
    from pydantic_ai.providers.azure import AzureProvider

    v1 = _v1_base_url(base_url)
    if v1 is not None and api_version:
        raise ConfigError(f"azure: {base_url} is a v1 endpoint, which takes no api_version")
    if v1 is None and not api_version:
        raise ConfigError(f"azure: {base_url} needs an api_version (or a /openai/v1 base URL)")
    key = key or os.environ.get("AZURE_OPENAI_API_KEY") or None
    if key is not None:
        return AzureProvider(
            azure_endpoint=base_url, api_version=api_version, api_key=key, http_client=http_client
        )
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    from evokai.core.azure import COGNITIVE_SERVICES, token_provider

    token = token_provider(COGNITIVE_SERVICES)
    client: Any = (
        AsyncOpenAI(base_url=v1, api_key=token, http_client=http_client)
        if v1 is not None
        else AsyncAzureOpenAI(
            azure_endpoint=base_url,
            api_version=api_version,
            azure_ad_token_provider=token,
            http_client=http_client,
        )
    )
    return AzureProvider(openai_client=client)


def anthropic_foundry_model(
    model: str, base_url: str, key: str | None, *, http_client: Any = None
) -> Model:
    """Claude in Microsoft Foundry: the Messages API of a Foundry resource.

    ``https://<resource>.services.ai.azure.com/anthropic`` with the deployment name as the model.
    The resource's key goes in ``x-api-key``; without one, an Entra ID token for the
    ``https://ai.azure.com/.default`` scope (the managed identity, ``evokai.core.azure``).
    https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-claude

    Args:
        model: The deployment name (``claude-sonnet-5-5``).
        base_url: The resource's ``/anthropic`` base URL.
        key: The resource's key, or None for Entra ID.
        http_client: An ``httpx2.AsyncClient`` of its own (tests).

    Returns:
        A pydantic-ai Anthropic model on an ``AsyncAnthropicFoundry`` client.

    Raises:
        ConfigError: The anthropic package is not installed (the 'azure' extra).
    """
    try:
        from anthropic import AsyncAnthropicFoundry
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider
    except ImportError as e:
        raise ConfigError("anthropic-foundry needs the 'azure' extra installed") from e
    if key is None:
        from evokai.core.azure import FOUNDRY, token_provider

        client = AsyncAnthropicFoundry(
            base_url=base_url,
            azure_ad_token_provider=token_provider(FOUNDRY),
            http_client=http_client,
        )
    else:
        client = AsyncAnthropicFoundry(base_url=base_url, api_key=key, http_client=http_client)
    return AnthropicModel(model, provider=AnthropicProvider(anthropic_client=client))


def from_environment(name: str) -> Model | None:
    """An Azure model from the standard variables, when no catalog endpoint connects it.

    ``azure:<deployment>`` with ``AZURE_OPENAI_ENDPOINT`` and no ``AZURE_OPENAI_API_KEY`` (Entra ID;
    with a key pydantic-ai's own construction applies), ``anthropic-foundry:<deployment>`` with
    ``ANTHROPIC_FOUNDRY_BASE_URL`` or ``ANTHROPIC_FOUNDRY_RESOURCE`` (and
    ``ANTHROPIC_FOUNDRY_API_KEY``, or Entra ID without it).

    Returns:
        The model, or None for the default construction.

    Raises:
        ConfigError: ``anthropic-foundry:`` without a base URL or resource.
    """
    provider, _, model = name.partition(":")
    env = os.environ
    if (
        provider == "azure"
        and env.get("AZURE_OPENAI_ENDPOINT")
        and not env.get("AZURE_OPENAI_API_KEY")
    ):
        from pydantic_ai.models.openai import OpenAIChatModel

        return OpenAIChatModel(
            model,
            provider=azure_provider(
                env["AZURE_OPENAI_ENDPOINT"], env.get("OPENAI_API_VERSION"), None
            ),
        )
    if provider == "anthropic-foundry":
        base = env.get("ANTHROPIC_FOUNDRY_BASE_URL") or (
            f"https://{env['ANTHROPIC_FOUNDRY_RESOURCE']}.services.ai.azure.com/anthropic"
            if env.get("ANTHROPIC_FOUNDRY_RESOURCE")
            else None
        )
        if base is None:
            raise ConfigError(
                "anthropic-foundry: declare an endpoint in EVOKAI_PROVIDERS_FILE or set "
                "ANTHROPIC_FOUNDRY_BASE_URL (or ANTHROPIC_FOUNDRY_RESOURCE)"
            )
        return anthropic_foundry_model(model, base, env.get("ANTHROPIC_FOUNDRY_API_KEY") or None)
    return None


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

        if not endpoint.base_url:
            raise ConfigError(f"azure: endpoint {endpoint.id} needs a base_url")
        return OpenAIChatModel(
            model,
            provider=azure_provider(
                endpoint.base_url, endpoint.api_version, _key(endpoint.api_key_env, provider)
            ),
        )
    if api == "anthropic-foundry":
        if not endpoint.base_url:
            raise ConfigError(f"{provider}: endpoint {endpoint.id} needs a base_url")
        return anthropic_foundry_model(
            model, endpoint.base_url, _key(endpoint.api_key_env, provider)
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
            if built is None:
                built = from_environment(name)
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
