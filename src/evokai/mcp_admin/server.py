"""The admin MCP server: Evokai's admin API as MCP tools, generated from its own OpenAPI.

- Only the routes of ``evokai.mcp_admin.tools.ADMIN_TOOLS`` become tools; every other route is
  excluded, including when the route mapping fails (the default mapping is ``EXCLUDE``).
- A tool calls its route in process (an ASGI transport), with the caller's ``Authorization``: the
  route's own rules (roles, validation, grants, the lock of a system application, the audit log)
  decide, exactly as for a direct call. Nothing is re-implemented here.
- Reads are annotated ``readOnlyHint``, every other tool ``destructiveHint``: Evokai's agents ask
  the person to confirm them.
- ``tools/list`` shows only the tools the caller's role may use (``evokai.identity.roles``); a call
  made anyway is refused by the route (403).
- Requests without a valid bearer (an admin key, or the console assistant's delegation token) are
  refused before they reach the MCP server (401).

The server is served at ``{base}/mcp/admin`` (Streamable HTTP, stateless, JSON responses). Its
tools are built on the first request, so starting the service does not pay for the OpenAPI.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Generator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider, RouteMap
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from evokai.assistant import delegation
from evokai.core.errors import AppError, Unauthorized
from evokai.identity.auth import bearer
from evokai.identity.models import AppPrincipal
from evokai.identity.roles import at_least, required
from evokai.mcp_admin.tools import ADMIN_TOOLS, ToolSpec, by_route
from evokai.mcp_admin.via import VIA_HEADER, via_value

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastmcp.server.providers.openapi.routing import HTTPRoute

    from evokai.core.config import Settings
    from evokai.services import Services

INSTRUCTIONS = (
    "Evokai's admin API for one application, with the caller's own rights. Read before you "
    "change; every change is checked and audited by Evokai like a direct call."
)


class ForwardAuth(httpx.Auth):
    """Carries the MCP caller's ``Authorization`` onto the in-process request, and marks it."""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        """Set the headers.

        Yields:
            The request.
        """
        incoming = get_http_headers(include_all=True)
        if value := incoming.get("authorization"):
            request.headers["Authorization"] = value
        request.headers[VIA_HEADER] = via_value()
        yield request


async def caller(svc: Services, authorization: str | None) -> AppPrincipal:
    """Who is calling the MCP server: an admin key, or a console delegation token.

    Returns:
        The principal.

    Raises:
        Unauthorized: No bearer, or an invalid one.
    """
    token = bearer(authorization)
    if not token:
        raise Unauthorized()
    if delegation.is_delegation(token):
        return await delegation.principal(svc, token)
    return await svc.auth.application(token, admin=True)


def _template(path: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")


class _Fields:
    """Refuses body fields outside a tool's allow-list, before the request reaches the route."""

    def __init__(self, base: str) -> None:
        self.rules = [
            (t.method, _template(base + t.path), t) for t in ADMIN_TOOLS if t.fields is not None
        ]

    async def __call__(self, request: httpx.Request) -> None:
        for method, pattern, spec in self.rules:
            if request.method == method and pattern.match(request.url.path):
                body = json.loads(request.content or b"{}")
                extra = sorted(set(body) - set(spec.fields or ())) if isinstance(body, dict) else []
                if extra:
                    raise ValueError(
                        f"{spec.name} cannot set {', '.join(extra)}: a person changes that in "
                        "the console."
                    )


class _RoleFilter(Middleware):
    """``tools/list`` shows only what the caller's role allows."""

    def __init__(self, owner: AdminMcp) -> None:
        self.owner = owner

    async def on_list_tools(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Sequence[Tool]],
    ) -> Sequence[Tool]:
        tools = await call_next(context)
        try:
            who = await caller(
                self.owner.services, get_http_headers(include_all=True).get("authorization")
            )
        except AppError:
            return []
        return [
            t
            for t in tools
            if (spec := self.owner.specs.get(t.name)) is not None
            and at_least(who.role, required(spec.method, spec.path))
        ]


def build_admin_mcp(app: FastAPI, settings: Settings) -> FastMCP:
    """The admin MCP server of an application (its tools built from the app's OpenAPI).

    Returns:
        The server.
    """
    server = FastMCP("evokai-admin", instructions=INSTRUCTIONS)
    server.add_provider(admin_provider(app, settings))
    return server


def admin_provider(app: FastAPI, settings: Settings) -> OpenAPIProvider:
    """The allow-listed admin routes of an app as tools.

    Returns:
        The provider.

    Raises:
        RuntimeError: A tool lacks its annotations (it would run without confirmation).
    """
    base = settings.base_path.rstrip("/")
    allowed = by_route()
    spec = app.openapi()
    paths: dict[str, Any] = {}
    for method, path in allowed:
        item = spec["paths"].get(base + path, {})
        if method.lower() in item:
            paths.setdefault(base + path, {})[method.lower()] = item[method.lower()]

    def route_type(route: HTTPRoute, _default: MCPType) -> MCPType:
        key = (route.method.upper(), route.path[len(base) :] if base else route.path)
        return MCPType.TOOL if key in allowed else MCPType.EXCLUDE

    def customise(route: HTTPRoute, tool: Any) -> None:
        t = allowed[route.method.upper(), route.path[len(base) :] if base else route.path]
        tool.name = t.name
        if t.description:
            tool.description = t.description
        reads = required(t.method, t.path) == "viewer"
        tool.annotations = (
            ToolAnnotations(readOnlyHint=True, destructiveHint=False)
            if reads
            else ToolAnnotations(readOnlyHint=False, destructiveHint=True)
        )
        if t.fields is not None:
            params = {p.name for p in route.parameters} | {
                f"{p.name}__{p.location}" for p in route.parameters
            }
            keep = params | set(t.fields)
            schema = dict(tool.parameters)
            schema["properties"] = {
                k: v for k, v in (schema.get("properties") or {}).items() if k in keep
            }
            schema["required"] = [r for r in schema.get("required") or [] if r in keep]
            schema["additionalProperties"] = False
            tool.parameters = schema

    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://evokai",
        auth=ForwardAuth(),
        event_hooks={"request": [_Fields(base)]},
        timeout=120,
    )
    provider = OpenAPIProvider(
        openapi_spec={**spec, "paths": paths},
        client=client,
        route_maps=[RouteMap(mcp_type=MCPType.EXCLUDE)],
        route_map_fn=route_type,
        mcp_component_fn=customise,
    )
    # A failed customisation is only logged by FastMCP: a tool without its name or annotations
    # would be exposed under another name or run without confirmation. Refuse to serve instead.
    tools = provider._tools
    names = {t.name for t in ADMIN_TOOLS}
    if set(tools) != names or any(t.annotations is None for t in tools.values()):
        raise RuntimeError(f"admin MCP: tools differ from the allow-list: {sorted(tools)}")
    return provider


class AdminMcp:
    """The admin MCP server mounted in the service, behind its bearer check."""

    def __init__(self, app: FastAPI, settings: Settings) -> None:
        """Prepare the server; its tools are built on the first request.

        Args:
            app: The service.
            settings: Deployment settings.
        """
        self.app = app
        self.settings = settings
        self.path = f"{settings.base_path.rstrip('/')}/mcp/admin"
        self.specs: dict[str, ToolSpec] = {t.name: t for t in ADMIN_TOOLS}
        self.server = FastMCP("evokai-admin", instructions=INSTRUCTIONS)
        self.server.add_middleware(_RoleFilter(self))
        self.http = self.server.http_app(
            path=self.path,
            stateless_http=True,
            json_response=True,
            host_origin_protection=False,
        )
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def services(self) -> Services:
        """The process services.

        Returns:
            Services.
        """
        return self.app.state.services

    def lifespan(self) -> AbstractAsyncContextManager[Any]:
        """The MCP transport's lifespan (chained into the service's).

        Returns:
            The context manager.
        """
        return self.http.router.lifespan_context(self.http)

    async def _load(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if not self._loaded:
                self.server.add_provider(admin_provider(self.app, self.settings))
                self._loaded = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve a request: refused without a valid bearer, else passed to the MCP server."""
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        try:
            await caller(self.services, headers.get("authorization"))
        except AppError as e:
            response = JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)
            await response(scope, receive, send)
            return
        await self._load()
        await self.http(scope, receive, send)
