"""Delegated connectors: tools that act as the person, with their own OAuth connection.

A delegated connector (``microsoft_365``, ``google_workspace``) is registered like any connector,
with the application's OAuth client (``client_id`` and ``client_secret_env``). Its tools run with
the access token of the person the turn is for, from the token vault
(``evokai.connections.service``): without a connection, a tool answers that the person must
connect first and the turn shows a card to do it (``connect`` event). Tools that send or create
are annotated destructive, so they always wait for the person's confirmation, and they exist only
with ``writes: true``.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import httpx
from pydantic import Field

from evokai.connections.providers import Provider
from evokai.connectors.base import Connector, ConnectorConfig, as_text
from evokai.core.errors import BadRequest, ConfigError
from evokai.mcp.client import McpTool, ToolResult

if TYPE_CHECKING:
    from evokai.connections.service import ConnectionService

log = logging.getLogger("evokai.connectors")

Family = Literal["mail", "calendar", "files"]


class DelegatedConfig(ConnectorConfig):
    """What every delegated connector accepts: the application's OAuth client and the families."""

    client_id: str = Field(min_length=1, max_length=200)
    """The OAuth client (application) id the application registered at the provider."""
    client_secret_env: str = Field(min_length=1, max_length=120)
    """The environment variable holding the client secret."""
    families: list[Family] = Field(  # ty: ignore[invalid-assignment]
        default_factory=lambda: ["mail", "calendar", "files"], min_length=1
    )
    """Tool families offered (and the scopes asked): ``mail``, ``calendar``, ``files``."""
    max_items: int = Field(default=20, ge=1, le=50)
    """Messages, events or files per search."""
    max_body_chars: int = Field(default=8_000, ge=500, le=100_000)
    """Characters of a message's text."""
    max_file_chars: int = Field(default=20_000, ge=1_000, le=200_000)
    """Characters of a file's text."""


class NotConnected(Exception):
    """The provider refused the person's token (revoked or expired at the provider)."""


class ApiError(Exception):
    """The provider answered with an error the model can read."""


class DelegatedConnector(Connector):
    """A connector whose tools act as the connected person."""

    provider: ClassVar[Provider]
    Config: ClassVar[type[ConnectorConfig]] = DelegatedConfig

    def __init__(
        self, name: str, config: ConnectorConfig, http: httpx.AsyncClient | None = None
    ) -> None:
        """Bind the connector to its configuration.

        Args:
            name: The MCP server name.
            config: Its validated configuration.
            http: The process's HTTP client.
        """
        super().__init__(name, config)
        self.cfg: DelegatedConfig = config  # ty: ignore[invalid-assignment]
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5))

    @abstractmethod
    def family_of(self, tool_name: str) -> str:
        """The family of a tool (``mail``, ``calendar``, ``files``).

        Returns:
            The family.
        """

    @abstractmethod
    async def run_as(self, token: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool with the person's access token.

        Returns:
            A JSON-able result.
        """

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:  # ruff: ignore[unused-method-argument]
        """Without a person there is no connection to act with.

        Raises:
            PermissionError: Always (the registry binds the connector to the turn's person).
        """
        raise PermissionError(f"{self.provider.title} tools act as a connected person")

    async def request(self, token: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Call the provider's API as the person.

        Returns:
            The response (2xx).

        Raises:
            NotConnected: 401 (the token was revoked at the provider).
            ApiError: Any other error status, with the provider's own message.
        """
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        r = await self.http.request(method, url, headers=headers, timeout=30, **kwargs)
        if r.status_code == 401:
            raise NotConnected()
        if r.status_code >= 400:
            detail = ""
            try:
                body = r.json()
                err = body.get("error") if isinstance(body, dict) else None
                detail = str(err.get("message") if isinstance(err, dict) else err or "")
            except ValueError:
                detail = ""
            raise ApiError(f"{self.provider.title} answered {r.status_code}. {detail[:300]}")
        return r

    def bind(
        self, oauth: ConnectionService, application_id: str, subject: str | None
    ) -> BoundConnector:
        """This connector acting as one person.

        Returns:
            A tool source for the toolbox.
        """
        return BoundConnector(self, oauth, application_id, subject)


class BoundConnector:
    """A delegated connector bound to the turn's person: what the toolbox calls."""

    def __init__(
        self,
        connector: DelegatedConnector,
        oauth: ConnectionService,
        application_id: str,
        subject: str | None,
    ) -> None:
        """Bind.

        Args:
            connector: The connector.
            oauth: The connections service (token vault).
            application_id: The application.
            subject: The person (None when nobody is in the loop).
        """
        self.connector = connector
        self.oauth = oauth
        self.application_id = application_id
        self.subject = subject
        self.name = connector.name

    async def list_tools(self) -> list[McpTool]:
        """The connector's tools.

        Returns:
            Tools.
        """
        return await self.connector.list_tools()

    def _connect(self, text: str) -> ToolResult:
        p = self.connector.provider
        return ToolResult(
            ok=False,
            text=text,
            connect={"provider": p.id, "title": p.title, "server": self.connector.name},
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a tool as the person, or ask them to connect first.

        Returns:
            The result (``connect`` set when the person has no usable connection).
        """
        c = self.connector
        p = c.provider
        if name not in {t.name for t in c.tools()}:
            return ToolResult(ok=False, text=f"Unknown tool: {name}")
        if not self.subject:
            return ToolResult(ok=False, text=f"{p.title} tools need a person to act as.")
        not_connected = (
            f"{p.title} is not connected for this user. A button to connect it is shown to "
            "them: ask them to connect and then to ask again. Do not retry now."
        )
        token = await self.oauth.access_token(self.application_id, self.subject, p.id, c.cfg)
        if token is None:
            return self._connect(not_connected)
        try:
            out = await c.run_as(token, name, arguments or {})
        except NotConnected:
            await self.oauth.expired(self.application_id, self.subject, p.id)
            return self._connect(not_connected)
        except (ApiError, BadRequest, ConfigError, ValueError, PermissionError, LookupError) as e:
            return ToolResult(ok=False, text=f"Error: {getattr(e, 'message', None) or e}")
        except Exception as e:
            log.warning("delegated_call_failed", extra={"tool": name, "error": type(e).__name__})
            return ToolResult(ok=False, text=f"Error: the {p.title} call failed.")
        return ToolResult(ok=True, text=as_text(out, c.config.max_result_chars))
