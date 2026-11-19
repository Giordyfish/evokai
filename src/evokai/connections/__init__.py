"""Per-user OAuth connections: the agent acts as the person at a third-party provider.

- ``providers``: the providers as data (Microsoft 365, Google Workspace, GitHub, Linear, Notion,
  Slack): their endpoints, the scopes of each tool family, how tokens are revoked;
- ``store``: the token vault (tokens encrypted with the deployment's key);
- ``service``: the authorization-code flow with PKCE, refreshes, revocation.

The tools themselves are the delegated connectors (``evokai.connectors.microsoft_365`` and
``evokai.connectors.google_workspace``) and the harness integrations (``github``, ``linear``,
``notion``, ``slack``, ``google_workspace``), which take the person's token from
``ConnectionService.person``.
"""

from evokai.connections.providers import PERSONAL, PROVIDERS, Provider, provider_of_kind

__all__ = ["PERSONAL", "PROVIDERS", "Provider", "provider_of_kind"]
