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
- Every tool takes an optional ``application`` (a slug): the console assistant's token is valid on
  every application its login can see and names one on each call; it goes to the route as the
  ``X-Evokai-Application`` header. ``list_applications`` lists them. An admin key acts on its own.

The server is served at ``{base}/mcp/admin`` (Streamable HTTP, stateless, JSON responses). Its
tools are built on the first request, so starting the service does not pay for the OpenAPI.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import re
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import yaml
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider, RouteMap
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from evokai.assistant import delegation
from evokai.core.errors import AppError, Unauthorized
from evokai.identity.auth import bearer
from evokai.identity.console import APPLICATION_HEADER
from evokai.identity.roles import at_least, required
from evokai.mcp_admin.tools import ADMIN_TOOLS, ToolSpec, by_route
from evokai.mcp_admin.via import CLIENT_HEADER, VIA_HEADER, via_value

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastmcp.server.providers.openapi.routing import HTTPRoute

    from evokai.core.config import Settings
    from evokai.services import Services

INSTRUCTIONS = (
    "Evokai's admin API, with the caller's own rights. Every tool acts on one application: name "
    "it with application (its slug; list_applications lists them). Read before you change; every "
    "change is checked and audited by Evokai like a direct call."
)

APPLICATION_ARG = "application"
APPLICATION_SCHEMA: dict[str, Any] = {
    "type": "string",
    "maxLength": 120,
    "description": "The slug of the application this call acts on (list_applications lists "
    "them). An admin key acts on its own application and may leave it out.",
}
_application: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "evokai_admin_mcp_application", default=None
)


class ForwardAuth(httpx.Auth):
    """Carries the MCP caller's ``Authorization`` and address onto the in-process request."""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        """Set the headers.

        Yields:
            The request.
        """
        incoming = get_http_headers(include_all=True)
        if value := incoming.get("authorization"):
            request.headers["Authorization"] = value
        if application := _application.get():
            request.headers[APPLICATION_HEADER] = application
        request.headers[VIA_HEADER] = via_value()
        with contextlib.suppress(RuntimeError):
            caller = get_http_request().client
            if caller is not None:
                request.headers[CLIENT_HEADER] = caller.host
        yield request


class _Application(Middleware):
    """Takes a call's ``application`` out of its arguments: the route receives it as a header."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        args = dict(context.message.arguments or {})
        named = args.pop(APPLICATION_ARG, None)
        mark = _application.set(str(named) if named else None)
        try:
            message = context.message.model_copy(update={"arguments": args})
            return await call_next(context.copy(message=message))
        finally:
            _application.reset(mark)


async def caller_role(svc: Services, authorization: str | None) -> str:
    """The role of who calls the MCP server.

    An admin key's, or the highest a console delegation token acts with on any application (every
    call is checked again on the one it names).

    Returns:
        The role.

    Raises:
        Unauthorized: No bearer, or an invalid one.
    """
    token = bearer(authorization)
    if not token:
        raise Unauthorized()
    if delegation.is_delegation(token):
        return await delegation.listing_role(svc, token)
    return (await svc.auth.application(token, admin=True)).role


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
            role = await caller_role(
                self.owner.services, get_http_headers(include_all=True).get("authorization")
            )
        except AppError:
            return []
        return [
            t
            for t in tools
            if t.name == LIST_APPLICATIONS
            or (
                (spec := self.owner.specs.get(t.name)) is not None
                and at_least(role, required(spec.method, spec.path))
            )
        ]


LIST_APPLICATIONS = "list_applications"


def add_list_applications(server: FastMCP, answer: Any) -> None:
    """The ``list_applications`` tool: the applications the caller may act on, by slug.

    Args:
        server: The MCP server.
        answer: ``async (authorization) -> list`` of ``{slug, name, role}``.
    """

    async def list_applications() -> dict[str, Any]:
        """List the applications.

        Returns:
            items.
        """
        return {"items": await answer(get_http_headers(include_all=True).get("authorization"))}

    server.tool(
        list_applications,
        name=LIST_APPLICATIONS,
        description="The applications you may act on, with your role on each. Name one by its "
        "slug as application on every other tool.",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
    )


def build_admin_mcp(app: FastAPI, settings: Settings) -> FastMCP:
    """The admin MCP server of an application (its tools built from the app's OpenAPI).

    Returns:
        The server.
    """
    server = FastMCP("evokai-admin", instructions=INSTRUCTIONS)
    server.add_provider(admin_provider(app, settings))
    return server


def admin_provider(
    app: FastAPI, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> OpenAPIProvider:
    """The allow-listed admin routes of an app as tools.

    Args:
        app: The service.
        settings: Deployment settings.
        transport: Where the tools' requests go: the service in process by default; the recorded
            answers for the assistant's evals (``recorded_transport``).

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
        schema = dict(tool.parameters)
        properties = dict(schema.get("properties") or {})
        if t.fields is not None:
            params = {p.name for p in route.parameters} | {
                f"{p.name}__{p.location}" for p in route.parameters
            }
            keep = params | set(t.fields)
            properties = {k: v for k, v in properties.items() if k in keep}
            schema["required"] = [r for r in schema.get("required") or [] if r in keep]
            schema["additionalProperties"] = False
        if APPLICATION_ARG in properties:
            raise RuntimeError(f"admin MCP: {t.name} already has a parameter {APPLICATION_ARG}")
        schema["properties"] = {APPLICATION_ARG: APPLICATION_SCHEMA, **properties}
        tool.parameters = schema

    client = httpx.AsyncClient(
        transport=transport or httpx.ASGITransport(app=app),
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
        validate_output=transport is None,
    )
    # A failed customisation is only logged by FastMCP: a tool without its name or annotations
    # would be exposed under another name or run without confirmation. Refuse to serve instead.
    tools = provider._tools
    names = {t.name for t in ADMIN_TOOLS}
    if set(tools) != names or any(t.annotations is None for t in tools.values()):
        raise RuntimeError(f"admin MCP: tools differ from the allow-list: {sorted(tools)}")
    return provider


RECORDED = Path(__file__).resolve().parents[1] / "assistant" / "evals" / "recorded.yaml"


async def _recorded_applications(_authorization: str | None) -> list[dict[str, Any]]:  # ruff: ignore[unused-async] (an answer is awaited)
    """The recorded applications (``applications`` of the recorded answers).

    Returns:
        Them.
    """
    data = yaml.safe_load(RECORDED.read_text(encoding="utf-8")) or {}
    return list(data.get("applications") or [])


def recorded_transport(base: str, path: Path = RECORDED) -> httpx.MockTransport:
    """Answers the admin tools' requests from recorded responses (the assistant's evals).

    No request leaves it: a read not recorded answers ``{"items": []}``, a change ``{"ok": true}``.

    Returns:
        The transport.
    """
    responses = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("responses") or {}
    routes = [(t.method, _template(base + t.path), f"{t.method} {t.path}") for t in ADMIN_TOOLS]

    def answer(request: httpx.Request) -> httpx.Response:
        key = next(
            (k for m, p, k in routes if m == request.method and p.match(request.url.path)), ""
        )
        default: dict[str, Any] = {"items": []} if request.method == "GET" else {"ok": True}
        return httpx.Response(200, json=responses.get(key, default))

    return httpx.MockTransport(answer)


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
        self.server.add_middleware(_Application())
        add_list_applications(self.server, self._applications)
        self.http = self.server.http_app(
            path=self.path,
            stateless_http=True,
            json_response=True,
            host_origin_protection=False,
        )
        # The same tools answered from recorded responses, for the assistant's evals only.
        self.recorded = FastMCP("evokai-admin", instructions=INSTRUCTIONS)
        self.recorded.add_middleware(_Application())
        add_list_applications(self.recorded, _recorded_applications)
        self.recorded_http = self.recorded.http_app(
            path=self.path,
            stateless_http=True,
            json_response=True,
            host_origin_protection=False,
        )
        self._loaded = False
        self._recorded_loaded = False
        self._lock = asyncio.Lock()

    @property
    def services(self) -> Services:
        """The process services.

        Returns:
            Services.
        """
        return self.app.state.services

    async def _applications(self, authorization: str | None) -> list[dict[str, Any]]:
        token = bearer(authorization) or ""
        if delegation.is_delegation(token):
            return await delegation.applications(self.services, token)
        who = await self.services.auth.application(token, admin=True)
        app = await self.services.store.applications.get(who.application_id)
        return [{"slug": app.slug, "name": app.name, "role": who.role}] if app else []

    @asynccontextmanager
    async def lifespan(self) -> AsyncGenerator[None]:
        """The MCP transports' lifespan (chained into the service's).

        Yields:
            While they serve.
        """
        async with (
            self.http.router.lifespan_context(self.http),
            self.recorded_http.router.lifespan_context(self.recorded_http),
        ):
            yield

    async def _load(self, *, recorded: bool) -> None:
        if self._recorded_loaded if recorded else self._loaded:
            return
        async with self._lock:
            if recorded and not self._recorded_loaded:
                transport = recorded_transport(self.settings.base_path.rstrip("/"))
                self.recorded.add_provider(
                    admin_provider(self.app, self.settings, transport=transport)
                )
                self._recorded_loaded = True
            elif not recorded and not self._loaded:
                self.server.add_provider(admin_provider(self.app, self.settings))
                self._loaded = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve a request: refused without a valid bearer, else passed to the MCP server."""
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if delegation.is_recorded(
            self.services.keyring, bearer(headers.get("authorization")) or ""
        ):
            await self._load(recorded=True)
            await self.recorded_http(scope, receive, send)
            return
        try:
            await caller_role(self.services, headers.get("authorization"))
        except AppError as e:
            response = JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)
            await response(scope, receive, send)
            return
        await self._load(recorded=False)
        await self.http(scope, receive, send)
