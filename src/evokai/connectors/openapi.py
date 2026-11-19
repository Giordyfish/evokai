"""REST APIs described by an OpenAPI document: the operations you choose become tools.

Each selected operation (by ``operationId``) is a tool whose arguments are its path, query and
header parameters and, when it has one, its JSON ``body``. Calls go only to hosts in
``allowed_hosts`` (by default the API's own), never follow redirects, and carry the API's credential
from the environment (a header or a query parameter). GET and HEAD read; any other method changes
data, so it is offered only with ``writes: true`` and asks the user to confirm.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, ClassVar, Literal, cast
from urllib.parse import quote, urljoin, urlparse

import httpx
import yaml
from pydantic import BaseModel, Field, model_validator

from evokai.connectors.base import Connector, ConnectorConfig, secret
from evokai.core.errors import ConfigError
from evokai.mcp.client import McpTool

READS = {"get", "head"}
METHODS = ("get", "put", "post", "delete", "patch", "head")


class ApiAuth(BaseModel):
    """How the API's credential is sent."""

    secret_env: str
    header: str | None = "Authorization"
    """Sent in this header (with ``prefix``)..."""
    prefix: str = "Bearer "
    query: str | None = None
    """...or as this query parameter."""


class OpenApiConfig(ConnectorConfig):
    """``connector: openapi``."""

    spec_url: str | None = None
    spec_file: str | None = None
    """The OpenAPI document (JSON or YAML), by URL or by path (relative: the working directory)."""
    spec: dict[str, Any] | None = None
    """Or the document itself."""
    base_url: str | None = None
    """Default: the document's first server."""
    operations: list[str] = Field(min_length=1)
    """The operationIds that become tools."""
    allowed_hosts: list[str] = Field(default_factory=list)
    """Hosts calls may reach (default: the base URL's)."""
    auth: ApiAuth | None = None
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    headers: dict[str, str] = Field(default_factory=dict)
    """Fixed extra headers (never secrets)."""

    @model_validator(mode="after")
    def _one_spec(self) -> OpenApiConfig:
        if sum(x is not None for x in (self.spec_url, self.spec_file, self.spec)) != 1:
            raise ValueError("give exactly one of spec_url, spec_file or spec")
        return self


class Operation(BaseModel):
    """A selected operation."""

    id: str
    method: Literal["get", "put", "post", "delete", "patch", "head"]
    path: str
    summary: str
    params: list[dict[str, Any]]
    body: dict[str, Any] | None


def _resolve(node: Any, root: dict[str, Any], depth: int = 0) -> Any:
    """Inline local ``$ref``s (depth-limited: recursive schemas stop as plain objects).

    Returns:
        The node without references.
    """
    if depth > 8:
        return {"type": "object"}
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str) and node["$ref"].startswith("#/"):
            target: Any = root
            for part in node["$ref"][2:].split("/"):
                target = (
                    target.get(part.replace("~1", "/").replace("~0", "~"), {})
                    if isinstance(target, dict)
                    else {}
                )
            return _resolve(target, root, depth + 1)
        return {k: _resolve(v, root, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, root, depth + 1) for v in node]
    return node


def _tool_name(op_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", op_id)[:64]


class OpenApiConnector(Connector):
    """A REST API, from its OpenAPI document."""

    kind: ClassVar[str] = "openapi"
    title: ClassVar[str] = "REST API (OpenAPI)"
    Config: ClassVar[type[ConnectorConfig]] = OpenApiConfig

    def __init__(
        self, name: str, config: ConnectorConfig, http: httpx.AsyncClient | None = None
    ) -> None:
        """Bind to the configuration; the document is read on first use."""
        super().__init__(name, config)
        self.cfg = cast(OpenApiConfig, config)
        self.http = http or httpx.AsyncClient()
        self._ops: dict[str, Operation] | None = None
        self._base = ""

    async def load(self) -> dict[str, Operation]:
        """The selected operations, from the document.

        Returns:
            Tool name to operation.

        Raises:
            ConfigError: The document cannot be read, or an operationId is missing.
        """
        if self._ops is not None:
            return self._ops
        doc = self.cfg.spec
        if doc is None and self.cfg.spec_file:
            text = Path(self.cfg.spec_file).read_text(encoding="utf-8")
            doc = yaml.safe_load(text)
        elif doc is None:
            r = await self.http.get(str(self.cfg.spec_url), timeout=self.cfg.timeout_seconds)
            r.raise_for_status()
            doc = yaml.safe_load(r.text)
        if not isinstance(doc, dict):
            raise ConfigError(f"openapi connector {self.name}: the document is not an object")
        servers = doc.get("servers") or []
        self._base = (self.cfg.base_url or (servers[0].get("url") if servers else "") or "").rstrip(
            "/"
        )
        if not self._base.startswith(("http://", "https://")):
            raise ConfigError(f"openapi connector {self.name}: set base_url (absolute)")
        found: dict[str, Operation] = {}
        for path, item in (doc.get("paths") or {}).items():
            shared = item.get("parameters") or []
            for method in METHODS:
                op = item.get(method)
                if not isinstance(op, dict) or op.get("operationId") not in self.cfg.operations:
                    continue
                params = [_resolve(p, doc) for p in [*shared, *(op.get("parameters") or [])]]
                body = None
                content = _resolve(op.get("requestBody") or {}, doc).get("content") or {}
                if "application/json" in content:
                    body = content["application/json"].get("schema") or {"type": "object"}
                found[_tool_name(op["operationId"])] = Operation(
                    id=op["operationId"],
                    method=method,
                    path=path,
                    summary=str(op.get("summary") or op.get("description") or op["operationId"])[
                        :500
                    ],
                    params=[p for p in params if p.get("in") in {"path", "query", "header"}],
                    body=body,
                )
        missing = set(self.cfg.operations) - {o.id for o in found.values()}
        if missing:
            raise ConfigError(f"openapi connector {self.name}: no operation {sorted(missing)}")
        self._ops = found
        return found

    def tools(self) -> list[McpTool]:
        """The selected operations (writes only with ``writes: true``).

        Returns:
            Tools.
        """
        out = []
        for name, op in (self._ops or {}).items():
            writes = op.method not in READS
            if writes and not self.cfg.writes:
                continue
            props: dict[str, Any] = {}
            required: list[str] = []
            for p in op.params:
                if self._is_auth(p):
                    continue
                props[p["name"]] = {
                    **(p.get("schema") or {"type": "string"}),
                    "description": str(p.get("description") or "")[:300],
                }
                if p.get("required") or p.get("in") == "path":
                    required.append(p["name"])
            if op.body is not None:
                props["body"] = op.body
                required.append("body")
            out.append(
                McpTool(
                    name=name,
                    description=f"{op.summary} ({op.method.upper()} {op.path})",
                    input_schema={"type": "object", "properties": props, "required": required},
                    read_only=not writes,
                    destructive=writes,
                )
            )
        return out

    def _is_auth(self, p: dict[str, Any]) -> bool:
        a = self.cfg.auth
        if a is None:
            return False
        return bool(
            p.get("in") == "header" and a.header and p.get("name", "").lower() == a.header.lower()
        ) or bool(p.get("in") == "query" and a.query is not None and p.get("name") == a.query)

    async def list_tools(self) -> list[McpTool]:
        """Read the document first, then list.

        Returns:
            Tools.
        """
        await self.load()
        return await super().list_tools()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Read the document first, then call.

        Returns:
            The result.
        """
        await self.load()
        return await super().call_tool(name, arguments)

    def allowed(self, url: str) -> bool:
        """Whether a URL's host may be called.

        Returns:
            True for the base URL's host or an allowed host, over http(s).
        """
        u = urlparse(url)
        hosts = {h.lower() for h in self.cfg.allowed_hosts} or {urlparse(self._base).hostname or ""}
        return u.scheme in {"http", "https"} and (u.hostname or "").lower() in hosts

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Call the operation.

        Returns:
            status and the answer (JSON when it is JSON).

        Raises:
            PermissionError: A write without ``writes: true``, or a host that is not allowed.
            ValueError: A missing path parameter.
        """
        op = (await self.load())[tool_name]
        if op.method not in READS and not self.cfg.writes:
            raise PermissionError("this API is read-only here")
        path = op.path
        query: dict[str, Any] = {}
        headers = {"Accept": "application/json", **self.cfg.headers}
        for p in op.params:
            if self._is_auth(p):
                continue
            v = args.get(p["name"])
            if v is None:
                if p.get("in") == "path":
                    raise ValueError(f"{p['name']} is required")
                continue
            if p["in"] == "path":
                path = path.replace("{" + p["name"] + "}", quote(str(v), safe=""))
            elif p["in"] == "query":
                query[p["name"]] = v
            else:
                headers[p["name"]] = str(v)
        if a := self.cfg.auth:
            value = secret(a.secret_env, f"openapi connector {self.name}")
            if a.query:
                query[a.query] = value
            else:
                headers[a.header or "Authorization"] = f"{a.prefix}{value}"
        url = urljoin(self._base + "/", path.lstrip("/"))
        if not self.allowed(url):
            raise PermissionError(f"host not allowed: {urlparse(url).hostname}")
        r = await self.http.request(
            op.method.upper(),
            url,
            params=query,
            headers=headers,
            json=args.get("body") if op.body is not None else None,
            timeout=self.cfg.timeout_seconds,
            follow_redirects=False,
        )
        try:
            answer: Any = r.json()
        except (json.JSONDecodeError, ValueError):
            answer = r.text
        return {"status": r.status_code, "body": answer}
