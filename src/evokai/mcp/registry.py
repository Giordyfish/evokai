"""The MCP servers of an agent, as seen in one session.

- Clients are reused per (server, credential) for a few minutes, so a conversation does not
  re-handshake on every step; tool lists are cached for a minute.
- A server that does not answer drops its tools for this turn; the agent goes on without them.
- Two servers exposing the same tool name is a configuration error (calls would be ambiguous).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from evokai.agents.definitions import AgentDef
from evokai.core.errors import AppError, ConfigError
from evokai.core.security import Keyring
from evokai.core.util import TtlCache, utcnow
from evokai.db import models as m
from evokai.db.repositories import Store
from evokai.mcp.client import McpClient, McpTool, ToolResult

if TYPE_CHECKING:
    from evokai.grants import Grants

log = logging.getLogger("evokai.mcp")

SLOW_MS = 2_000
"""A server that takes longer than this to list its tools is reported as slow."""
RECHECK = timedelta(minutes=10)
"""How often a server's unchanged listing is written down (status and latency)."""


def tool_info(t: McpTool) -> dict[str, Any]:
    """What the console keeps of a listed tool.

    Returns:
        name, description, readOnly, destructive, mutating.
    """
    return {
        "name": t.name,
        "description": (t.description or "")[:500],
        "readOnly": t.read_only,
        "destructive": t.destructive,
        "mutating": t.mutating,
    }


def status_of(latency_ms: int) -> str:
    """The status of a listing from its latency: ok or slow.

    Returns:
        The status.
    """
    return "slow" if latency_ms > SLOW_MS else "ok"


class ToolSource(Protocol):
    """Where a tool call goes: an MCP server's client, or a connector served in process."""

    name: str

    async def list_tools(self) -> list[McpTool]:
        """The tools."""
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a tool."""
        ...


WORKER_TOKEN_TTL = 300
"""Seconds a capability worker's call token lives at most (and only while its turn runs)."""


def worker_run_key(run_id: str) -> str:
    """The shared mark of a turn or run whose workers may still call models.

    Returns:
        The key.
    """
    return f"worker-run:{run_id}"


@dataclass
class Toolbox:
    """The app tools available in a turn and where each call goes."""

    tools: list[McpTool] = field(default_factory=list)
    routes: dict[str, tuple[ToolSource, str]] = field(default_factory=dict)
    """Tool name as the model sees it → (client, the server's own tool name)."""
    unavailable: list[str] = field(default_factory=list)
    meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    """The ``_meta`` of each server's calls, by server (servers with ``auth: secret``: the
    conversation, so a capability worker keeps its browser or pod per conversation)."""

    def names(self) -> set[str]:
        """Tool names.

        Returns:
            The set of names.
        """
        return {t.name for t in self.tools}

    def mutating(self) -> set[str]:
        """Tools that change data (they need confirmation).

        Returns:
            The set of names.
        """
        return {t.name for t in self.tools if t.mutating}

    def server_of(self, name: str) -> str | None:
        """The MCP server a tool comes from (for traces).

        Returns:
            The server's name, or None for a tool that is not the application's.
        """
        route = self.routes.get(name)
        return getattr(route[0], "name", None) if route else None

    async def call(self, name: str, arguments: dict) -> ToolResult:
        """Call a tool. Failures become a result the model can read, never an exception.

        Args:
            name: Tool name as the model sees it.
            arguments: Tool arguments.

        Returns:
            The result.
        """
        route = self.routes.get(name)
        if not route:
            return ToolResult(ok=False, text=f"Strumento sconosciuto: {name}")
        client, own_name = route
        try:
            meta = self.meta.get(getattr(client, "name", ""))
            if meta and isinstance(client, McpClient):
                return await client.call_tool(own_name, arguments, meta)
            return await client.call_tool(own_name, arguments)
        except Exception as e:
            log.warning(
                "mcp_call_failed",
                extra={"server": client.name, "tool": name, "error": type(e).__name__},
            )
            return ToolResult(ok=False, text="Error: the tool did not answer.")


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class McpRegistry:
    """Builds toolboxes from an application's registered servers."""

    def __init__(
        self, http: httpx.AsyncClient, store: Store, keyring: Keyring, shared: Any = None
    ) -> None:
        """Create the registry.

        Args:
            http: Shared HTTP client.
            store: Repositories.
            keyring: To decrypt server secrets and sign capability workers' call tokens.
            shared: State shared by the instances (which turns may still call models through a
                worker).
        """
        self.http = http
        self.store = store
        self.keyring = keyring
        self.shared = shared
        self._servers: TtlCache[str, list[m.McpServerRecord]] = TtlCache(200, 30)
        self._clients: TtlCache[tuple[str, str], McpClient] = TtlCache(1_000, 300)
        self._tools: TtlCache[tuple[str, str], list[McpTool]] = TtlCache(1_000, 60)
        self._connectors: dict[str, tuple[str, Any]] = {}
        """Connector servers by id: (configuration hash, connector). Built once, kept while the
        configuration is the same."""
        self.connections: Any = None
        """The per-user OAuth connections (``evokai.connections.service.ConnectionService``),
        which delegated connectors act with; set by the services."""

    async def _app_servers(self, application_id: str) -> list[m.McpServerRecord]:
        hit = self._servers.get(application_id)
        if hit is None:
            hit = await self.store.mcp_servers.all(application_id)
            self._servers.set(application_id, hit)
        return hit

    def client_for(
        self,
        server: m.McpServerRecord,
        credential: str | None,
        subject: str | None = None,
    ) -> tuple[ToolSource, str]:
        """The client of a server for a credential (a connector needs none).

        Args:
            server: The server.
            credential: The session's forwarded credential, for ``forward_user`` servers.
            subject: The person a delegated connector acts as (``microsoft_365``,
                ``google_workspace``), with their own OAuth connection.

        Returns:
            The client and the key of its credential (for caches). A forward_user server without
            a credential, an unreadable secret or an invalid connector configuration raise
            ``ConfigError``.
        """
        if server.connector:
            source, key = self._connector(server)
            bind = getattr(source, "bind", None)
            if bind is not None and self.connections is not None:
                return bind(self.connections, server.application_id, subject), key
            return source, key
        return self._client(server, credential)

    def _connector(self, server: m.McpServerRecord) -> tuple[ToolSource, str]:
        from evokai.connectors import build

        digest = _h(json.dumps([server.connector, server.config], sort_keys=True, default=str))
        held = self._connectors.get(server.id)
        if held is None or held[0] != digest:
            try:
                connector = build(server.name, server.connector or "", server.config, self.http)
            except AppError as e:
                raise ConfigError(f"connector {server.name}: {e.message}") from e
            self._connectors[server.id] = (digest, connector)
            return connector, f"connector:{digest}"
        return held[1], f"connector:{digest}"

    def _client(self, server: m.McpServerRecord, credential: str | None) -> tuple[McpClient, str]:
        headers: dict[str, str] = {}
        if server.auth == "forward_user":
            if not credential:
                raise ConfigError(f"MCP server {server.name}: no end-user credential in session")
            headers["Authorization"] = f"Bearer {credential}"
        elif server.auth == "secret":
            secret = self.keyring.decrypt(server.secret_encrypted or "")
            if not secret:
                raise ConfigError(f"MCP server {server.name}: secret missing or unreadable")
            headers["Authorization"] = f"Bearer {secret}"
        key = (server.id, _h(headers.get("Authorization", "")))
        client = self._clients.get(key)
        if client is None:
            client = McpClient(
                name=server.name,
                url=server.url,
                http=self.http,
                headers=headers,
                timeout=server.timeout_seconds,
            )
            self._clients.set(key, client)
        return client, key[1]

    async def toolbox(
        self,
        application_id: str,
        agent: AgentDef,
        credential: str | None,
        grants: Grants | None = None,
        subject: str | None = None,
        conversation: str | None = None,
        tags: list[str] | None = None,
        run_id: str | None = None,
        worker_calls: int = 50,
    ) -> Toolbox:
        """The tools of an agent for this session.

        Args:
            application_id: Owner application.
            agent: The agent definition.
            credential: The end user's credential for forward_user servers, if any.
            grants: The application's grants: servers and tools that are not granted are left
                out, and so are tools that change data when confirmations are not granted, and
                tools of a delegated connector whose provider or family is not granted.
            subject: The person the turn or run is for (delegated connectors act as them).
            conversation: The conversation (or run) the calls belong to, sent with the person,
                the agent and the tags as ``_meta`` to servers marked ``worker`` only (Evokai's
                capability workers), never to any other server.
            tags: The session's usage tags (a worker's model calls are recorded with them).
            run_id: The turn or run: a worker's call token is valid while it runs.
            worker_calls: The model calls a worker may make through Evokai in this turn.

        Returns:
            Tools and routes.

        Raises:
            ConfigError: A tool name exposed by two servers.
        """
        box = Toolbox()
        owner: dict[str, str] = {}
        servers = {s.name: s for s in await self._app_servers(application_id)}
        for name in agent.mcp_servers:
            server = servers.get(name)
            if server is None or (grants is not None and not grants.mcp(name)):
                box.unavailable.append(name)
                continue
            try:
                client, cred_key = self.client_for(server, credential, subject)
                if conversation and run_id and server.worker and server.auth == "secret":
                    # Only an Evokai capability worker gets the conversation (it keeps its
                    # browser or pod) and a call token: short-lived, bound to this turn, the
                    # person, the agent and the tags, valid only to call models through Evokai.
                    token = await self._worker_token(
                        application_id,
                        server.name,
                        run_id=run_id,
                        conversation=conversation,
                        subject=subject or "",
                        agent=agent.name,
                        tags=list(tags or []),
                        calls=worker_calls,
                    )
                    box.meta[getattr(client, "name", server.name)] = {
                        "evokai/conversation": conversation,
                        "evokai/token": token,
                    }
                tools = self._tools.get((server.id, cred_key))
                if tools is None:
                    started = time.perf_counter()
                    tools = await client.list_tools()
                    self._tools.set((server.id, cred_key), tools)
                    await self._seen(server, tools, round((time.perf_counter() - started) * 1000))
            except Exception as e:
                log.warning("mcp_unavailable", extra={"server": name, "error": type(e).__name__})
                box.unavailable.append(name)
                await self._failed(server, e)
                continue
            if grants is not None:
                tools = [
                    t
                    for t in tools
                    if grants.tool(name, t.name) and (grants.client("confirm") or not t.mutating)
                ]
                delegated = getattr(client, "connector", None)
                if delegated is not None and hasattr(delegated, "family_of"):
                    pid = delegated.provider.id
                    tools = [
                        t for t in tools if grants.connection(pid, delegated.family_of(t.name))
                    ]
            for t in tools:
                tool_name = f"{server.tool_prefix}{t.name}"
                if tool_name in owner:
                    raise ConfigError(f"tool {tool_name} exposed by {owner[tool_name]} and {name}")
                owner[tool_name] = name
                box.tools.append(replace(t, name=tool_name, server=name))
                box.routes[tool_name] = (client, t.name)
        return box

    async def _worker_token(
        self,
        application_id: str,
        server: str,
        *,
        run_id: str,
        conversation: str,
        subject: str,
        agent: str,
        tags: list[str],
        calls: int,
    ) -> str:
        """A capability worker's call token for one turn, and the mark that the turn runs.

        Returns:
            The token.
        """
        token, _jti = self.keyring.mint_worker(
            {
                "aud": application_id,
                "sub": subject,
                "cid": conversation,
                "agt": agent,
                "tags": tags,
                "tid": run_id,
                "srv": server,
                "mcl": calls,
            },
            ttl=WORKER_TOKEN_TTL,
        )
        if self.shared is not None:
            await self.shared.set(worker_run_key(run_id), application_id, WORKER_TOKEN_TTL)
        return token

    async def end_run(self, run_id: str) -> None:
        """The turn or run is over: its workers' tokens stop working (whatever their expiry)."""
        if self.shared is not None:
            try:
                await self.shared.delete(worker_run_key(run_id))
            except Exception as e:
                log.warning("worker_run_end_failed", extra={"error": type(e).__name__})

    async def _seen(self, server: m.McpServerRecord, tools: list[McpTool], latency_ms: int) -> None:
        """Write down a listing when its tools changed or the last note is old."""
        listed = [tool_info(t) for t in tools]
        status = status_of(latency_ms)
        fresh = server.checked_at is not None and utcnow() - _aware(server.checked_at) < RECHECK
        if fresh and server.tools == listed and server.status == status:
            return
        try:
            await self.store.mcp_servers.seen(
                server.id, status=status, latency_ms=latency_ms, tools=listed
            )
            self._servers.pop(server.application_id)
        except Exception as e:
            log.warning("mcp_seen_failed", extra={"server": server.name, "error": type(e).__name__})

    async def _failed(self, server: m.McpServerRecord, error: Exception) -> None:
        """Write down that a server did not answer (once per status change or recheck period)."""
        fresh = server.checked_at is not None and utcnow() - _aware(server.checked_at) < RECHECK
        if fresh and server.status == "down":
            return
        try:
            await self.store.mcp_servers.seen(server.id, status="down", error=type(error).__name__)
            self._servers.pop(server.application_id)
        except Exception as e:
            log.warning("mcp_seen_failed", extra={"server": server.name, "error": type(e).__name__})

    def forget(self, application_id: str) -> None:
        """Re-read an application's servers on next use (after an admin change)."""
        self._servers.pop(application_id)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored time is UTC.

    Returns:
        The time, aware.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)
