"""The OAuth providers a person can connect, as data: endpoints, scopes, quirks.

Each provider is one ``Provider`` value: where people sign in, where codes and refresh tokens are
traded, which scopes each tool family needs, how a token is revoked and how the account is named.
The service (``evokai.connections.service``) reads these values and has no provider of its own.

Scopes are the least each tool family needs; write scopes are asked only when the application
configured writes (``writes: true``). A family-less provider (GitHub, Linear, Notion, Slack) asks
its ``base_scopes`` and, with writes, its ``write_base`` scopes.

Two ways an application configures a provider (``configured_by``):

- ``connector``: a delegated connector (``microsoft_365``, ``google_workspace``) carries the client
  id and the name of the environment variable that holds the secret;
- ``application``: the application's OAuth app (``evokai_oauth_apps``: client id, the secret
  encrypted, writes), set from the console or the application file; the tools are the harness
  integration of the same name, acting as the person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

FAMILIES = ("mail", "calendar", "files")
"""Tool families of the office providers (grants are ``<provider>/<family>``)."""

RevokeStyle = Literal["form", "form_client", "github_grant", "bearer"]
"""How a token is revoked at the provider.

- ``form``: ``POST`` the token as a form field ``token`` (Google, Linear; the token is its own
  authentication);
- ``form_client``: the same with the client id and secret in the form (Notion MCP, RFC 7009);
- ``github_grant``: ``DELETE {url}`` with the client's Basic auth and ``{"access_token": ...}``,
  which removes the whole authorization of the app for the person (GitHub);
- ``bearer``: ``POST`` with the access token as the bearer (Slack's ``auth.revoke``).
"""


@dataclass(frozen=True)
class Account:
    """How the account a person connected is named, when no ID token says it."""

    url: str
    """An endpoint that answers with the person's identity, called with their access token."""
    paths: tuple[str, ...]
    """Dotted paths in its JSON answer, joined with « @ » (the first that exist)."""
    method: Literal["GET", "POST"] = "GET"
    json: dict[str, str] | None = None
    """A JSON body (a GraphQL query) for a ``POST``."""


@dataclass(frozen=True)
class Provider:
    """A provider: where people sign in, which scopes each family needs, and its quirks."""

    id: str
    title: str
    connector: str | None
    """The connector kind whose tools use this provider's connections, or None when the tools are
    the harness integration ``integration`` and the application's OAuth app configures it."""
    authorize_endpoint: str
    """The authorization endpoint (``{tenant}`` is replaced by the configured tenant)."""
    exchange_endpoint: str
    """The token endpoint, where codes and refresh tokens are exchanged."""
    base_scopes: tuple[str, ...]
    read_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    write_scopes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    write_base: tuple[str, ...] = ()
    """Scopes added with writes, whatever the families (family-less providers)."""
    families: tuple[str, ...] = ()
    """The tool families this provider has (grants ``<provider>/<family>``)."""
    scope_separator: str = " "
    """How scopes are joined in the authorization request (Linear and Slack use commas)."""
    params: dict[str, str] = field(default_factory=dict)
    """Extra parameters of the authorization request."""
    resource: str | None = None
    """RFC 8707 resource indicator, sent on the authorization and token requests (Notion MCP binds
    its tokens to its MCP endpoint this way)."""
    revoke_url: str | None = None
    """Where a token is revoked at the provider, if it can be (``{client_id}`` is replaced)."""
    revoke_style: RevokeStyle = "form"
    account: Account | None = None
    """How the account is named when the token response carries no ID token."""
    default_tenant: str | None = None
    """The tenant used when the configuration names none (Microsoft: ``organizations``)."""
    integration: str | None = None
    """The harness integration (capability id) that acts as the person with this connection."""
    console: str | None = None
    """Where the OAuth app is registered (for the console's hint and the docs)."""

    @property
    def configured_by(self) -> Literal["connector", "application"]:
        """Whether a delegated connector or the application's OAuth app configures it.

        Returns:
            ``connector`` or ``application``.
        """
        return "connector" if self.connector else "application"

    def authorize_url(self, tenant: str | None = None) -> str:
        """The authorization endpoint.

        Returns:
            Its URL.
        """
        return self.authorize_endpoint.format(tenant=tenant or self.default_tenant or "")

    def token_url(self, tenant: str | None = None) -> str:
        """The token endpoint.

        Returns:
            Its URL.
        """
        return self.exchange_endpoint.format(tenant=tenant or self.default_tenant or "")

    def scopes(self, families: list[str], writes: bool) -> list[str]:
        """The scopes to ask for a configuration's families.

        Returns:
            Scopes, without duplicates, base scopes first.
        """
        out = list(self.base_scopes)
        for f in families:
            out += self.read_scopes.get(f, ())
            if writes:
                out += self.write_scopes.get(f, ())
        if writes:
            out += self.write_base
        return list(dict.fromkeys(out))

    def extra_params(self) -> dict[str, str]:
        """Provider-specific parameters of the authorization request.

        Returns:
            Parameters (Google needs ``access_type=offline`` and a consent prompt to return a
            refresh token).
        """
        out = dict(self.params)
        if self.resource:
            out["resource"] = self.resource
        return out


MICROSOFT_LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"

MICROSOFT_365 = Provider(
    id="microsoft365",
    title="Microsoft 365",
    connector="microsoft_365",
    authorize_endpoint=f"{MICROSOFT_LOGIN}/{{tenant}}/oauth2/v2.0/authorize",
    exchange_endpoint=f"{MICROSOFT_LOGIN}/{{tenant}}/oauth2/v2.0/token",
    default_tenant="organizations",
    base_scopes=("openid", "profile", "email", "offline_access", "User.Read"),
    read_scopes={
        "mail": ("Mail.Read",),
        "calendar": ("Calendars.Read",),
        "files": ("Files.Read.All", "Sites.Read.All"),
    },
    write_scopes={"mail": ("Mail.ReadWrite",), "calendar": ("Calendars.ReadWrite",)},
    families=FAMILIES,
    params={"response_mode": "query", "prompt": "select_account"},
    console="Microsoft Entra admin center, App registrations",
)

GOOGLE = Provider(
    id="google",
    title="Google Workspace",
    connector="google_workspace",
    authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    exchange_endpoint="https://oauth2.googleapis.com/token",
    base_scopes=("openid", "email", "profile"),
    read_scopes={
        "mail": ("https://www.googleapis.com/auth/gmail.readonly",),
        "calendar": ("https://www.googleapis.com/auth/calendar.readonly",),
        "files": ("https://www.googleapis.com/auth/drive.readonly",),
    },
    write_scopes={
        "mail": ("https://www.googleapis.com/auth/gmail.compose",),
        "calendar": ("https://www.googleapis.com/auth/calendar.events",),
    },
    families=FAMILIES,
    params={
        "response_mode": "query",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    },
    revoke_url="https://oauth2.googleapis.com/revoke",
    integration="google_workspace",
    console="Google Cloud console, APIs & Services, Credentials",
)

GITHUB = Provider(
    # A GitHub App: its permissions are chosen on the app (read only, or read and write), the
    # scope parameter is ignored; user tokens expire after 8 hours and are refreshed (6 months).
    # An OAuth App works too (tokens without expiry, no refresh): then the scopes below apply.
    id="github",
    title="GitHub",
    connector=None,
    authorize_endpoint="https://github.com/login/oauth/authorize",
    exchange_endpoint="https://github.com/login/oauth/access_token",
    base_scopes=("read:user", "read:org"),
    write_base=("repo",),
    params={"allow_signup": "false"},
    revoke_url="https://api.github.com/applications/{client_id}/grant",
    revoke_style="github_grant",
    account=Account(url="https://api.github.com/user", paths=("login",)),
    integration="github",
    console="GitHub, Settings, Developer settings, GitHub Apps",
)

LINEAR = Provider(
    id="linear",
    title="Linear",
    connector=None,
    authorize_endpoint="https://linear.app/oauth/authorize",
    exchange_endpoint="https://api.linear.app/oauth/token",
    base_scopes=("read",),
    write_base=("write",),
    scope_separator=",",
    params={"prompt": "consent", "actor": "user"},
    revoke_url="https://api.linear.app/oauth/revoke",
    revoke_style="form",
    account=Account(
        url="https://api.linear.app/graphql",
        method="POST",
        json={"query": "{ viewer { email } }"},
        paths=("data.viewer.email",),
    ),
    integration="linear",
    console="Linear, Settings, API, OAuth applications",
)

NOTION = Provider(
    # The hosted Notion MCP server has its own authorization server (mcp.notion.com), separate
    # from the REST API's public integrations: its tokens are what the MCP server accepts. The
    # client is registered once with dynamic client registration; the person picks the
    # workspace and Notion's own permissions apply (no scopes to choose).
    id="notion",
    title="Notion",
    connector=None,
    authorize_endpoint="https://mcp.notion.com/authorize",
    exchange_endpoint="https://mcp.notion.com/token",
    base_scopes=(),
    resource="https://mcp.notion.com/mcp",
    revoke_url="https://mcp.notion.com/token",
    revoke_style="form_client",
    integration="notion",
    console="https://mcp.notion.com/register (dynamic client registration)",
)

SLACK = Provider(
    # Slack's MCP server takes user tokens from the user-only flow (oauth/v2_user); only
    # internal or Slack Marketplace apps may use it. Token rotation is the app's choice: with it,
    # tokens expire after 12 hours and are refreshed, without it they last until revoked.
    id="slack",
    title="Slack",
    connector=None,
    authorize_endpoint="https://slack.com/oauth/v2_user/authorize",
    exchange_endpoint="https://slack.com/api/oauth.v2.user.access",
    base_scopes=(
        "search:read.public",
        "search:read.private",
        "search:read.im",
        "search:read.mpim",
        "search:read.files",
        "search:read.users",
        "channels:history",
        "groups:history",
        "im:history",
        "mpim:history",
        "channels:read",
        "groups:read",
        "im:read",
        "mpim:read",
        "users:read",
        "files:read",
        "canvases:read",
    ),
    write_base=("chat:write", "canvases:write", "reactions:write"),
    scope_separator=",",
    revoke_url="https://slack.com/api/auth.revoke",
    revoke_style="bearer",
    account=Account(url="https://slack.com/api/auth.test", paths=("user", "team")),
    integration="slack",
    console="api.slack.com/apps",
)

PROVIDERS: dict[str, Provider] = {
    p.id: p for p in (MICROSOFT_365, GOOGLE, GITHUB, LINEAR, NOTION, SLACK)
}

PERSONAL: dict[str, str] = {
    p.integration: p.id for p in PROVIDERS.values() if p.integration is not None
}
"""Harness integration (capability id) → the connection that supplies the person's token."""


def provider_of_kind(kind: str | None) -> Provider | None:
    """The provider whose connections a connector kind uses.

    Returns:
        The provider, or None for a connector that acts with its own credentials.
    """
    if not kind:
        return None
    return next((p for p in PROVIDERS.values() if p.connector == kind), None)
