"""Microsoft Entra ID tokens for Azure services, without keys: Managed Identity first.

Every Azure piece Evokai calls (Azure OpenAI and Foundry models, Claude in Foundry, Document
Intelligence, Container Apps dynamic sessions, Azure Monitor) takes either the resource's key or an
Entra ID bearer token. Without a key, the token comes from ``azure.identity``'s
``DefaultAzureCredential``: in Azure Container Apps that is the app's managed identity (a
user-assigned one when ``AZURE_CLIENT_ID`` names it), on a laptop the Azure CLI sign-in, in CI a
workload identity or a service principal (``AZURE_TENANT_ID``, ``AZURE_CLIENT_ID``,
``AZURE_CLIENT_SECRET``). One credential serves the whole process; it caches and refreshes its
tokens.

``azure-identity`` is an optional extra (``evokai[azure]``): nothing here is imported until a
keyless Azure connection is built, and a missing package is a configuration error that says which
extra to install.

Sources (read 26 November 2026):
https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.aio.defaultazurecredential
https://learn.microsoft.com/en-us/azure/container-apps/managed-identity
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from evokai.core.errors import ConfigError

COGNITIVE_SERVICES = "https://cognitiveservices.azure.com/.default"
"""Azure OpenAI, the Foundry OpenAI v1 endpoint, Document Intelligence, Language."""
FOUNDRY = "https://ai.azure.com/.default"
"""Claude in Microsoft Foundry (the ``/anthropic`` endpoint of a Foundry resource)."""
DYNAMIC_SESSIONS = "https://dynamicsessions.io/.default"
"""Azure Container Apps dynamic sessions (a session pool's management endpoint)."""

TokenProvider = Callable[[], Awaitable[str]]

_credential: Any = None


def credential() -> Any:
    """The process's Entra ID credential (``azure.identity.aio.DefaultAzureCredential``).

    Returns:
        An async token credential.

    Raises:
        ConfigError: ``azure-identity`` is not installed.
    """
    global _credential
    if _credential is None:
        try:
            from azure.identity.aio import DefaultAzureCredential
        except ImportError as e:
            raise ConfigError(
                "a keyless Azure connection needs azure-identity: install the 'azure' extra"
            ) from e
        _credential = DefaultAzureCredential()
    return _credential


def set_credential(value: Any) -> None:
    """Use this credential (tests, or an application that builds its own); None resets it."""
    global _credential
    _credential = value


def token_provider(scope: str) -> TokenProvider:
    """A function that returns a fresh bearer token for a scope, refreshed before it expires.

    Args:
        scope: The resource's ``.default`` scope (:data:`COGNITIVE_SERVICES`, :data:`FOUNDRY`,
            :data:`DYNAMIC_SESSIONS`).

    Returns:
        An async callable returning the token (what the OpenAI and Anthropic SDKs call
        ``azure_ad_token_provider``).
    """
    cred = credential()
    from azure.identity.aio import get_bearer_token_provider

    return get_bearer_token_provider(cred, scope)
