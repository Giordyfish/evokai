"""A capability worker: one pydantic-ai-harness toolset served over MCP, for Evokai.

Some harness capabilities cannot run inside Evokai's service: browser-use drives a browser on the
machine that runs it, and the Kubernetes sandbox needs a gRPC client the main image cannot install.
This process hosts one of them and serves its tools over MCP (streamable HTTP, JSON responses).
The application registers it as an MCP server with ``auth: secret``, so everything Evokai does
for MCP tools applies: the grants, the tool policy and the call checks, a confirmation for every
tool not marked ``readOnlyHint``, the untrusted-content guard, traces and the tool-call cap.

State (a browser, a pod) lives per conversation: Evokai sends ``_meta["evokai/conversation"]`` with
every call of a server with ``auth: secret``. A conversation's tools run inside one long-lived
pydantic-ai run whose model is this process (``Session``): each MCP call becomes the next tool
call of that run, so the capability's workspace and session stay across calls, and an idle
conversation's run ends (its browser or pod torn down) after ``EVOKAI_WORKER_IDLE_SECONDS``.

Environment:
    EVOKAI_WORKER_CAPABILITY  ``kubernetes``, ``browser_use`` or ``playwright``
    EVOKAI_WORKER_SECRET      the bearer secret Evokai sends (required)
    EVOKAI_URL                where browser-use's model calls go: Evokai, with the turn's call
                              token from ``_meta`` (usage, caps, guard as the person); the worker
                              holds no provider key and no Evokai key
    EVOKAI_WORKER_CONFIG      the capability's settings as JSON (``kubernetes``: ``warm_pool``,
                              ``namespace``...; ``browser_use``: its own settings)
    EVOKAI_WORKER_IDLE_SECONDS  default 900
    EVOKAI_WORKER_PORT        default 8790
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger("evokai.worker")

PROTOCOL = "2025-06-18"
READ_ONLY = {
    # file_system's own read-only tools
    "read_file",
    "list_directory",
    "list_files",
    "search_files",
    "find_files",
    "file_info",
    "grep",
    # Playwright's reads of the page (every action, navigate included, waits for the person)
    "snapshot",
    "get_text",
    "screenshot",
    "console_messages",
    "network_requests",
}
"""Tools marked ``readOnlyHint`` (Evokai asks the person before any other)."""


KINDS = ("kubernetes", "browser_use", "playwright")
"""What a worker can host (``browser_use`` and ``playwright`` share the browser image)."""


def capabilities_for(kind: str, config: dict[str, Any], model: Any = None) -> list[Any]:
    """The harness capabilities a worker hosts.

    Args:
        kind: The capability.
        config: Its settings.
        model: The model browser-use's own agent calls (``EvokaiModel``: through Evokai).

    Returns:
        Capabilities.

    Raises:
        ValueError: An unknown kind, or browser-use without a way to call models through Evokai.
    """
    if kind == "kubernetes":
        from pydantic_ai_harness import FileSystem, Shell
        from pydantic_ai_harness.kubernetes_sandbox import KubernetesSandbox

        return [KubernetesSandbox(**config), Shell(), FileSystem()]
    if kind == "playwright":
        from pydantic_ai_harness.playwright import PlaywrightBrowser

        return [PlaywrightBrowser(**config)]
    if kind == "browser_use":
        from pydantic_ai_harness.browser_use import BrowserUse

        if model is None:
            raise ValueError("browser_use calls models only through Evokai: set EVOKAI_URL")
        args = dict(config)
        chrome = _chromium()
        if chrome and "browser_profile" not in args:
            from browser_use import BrowserProfile

            args["browser_profile"] = BrowserProfile(executable_path=chrome, headless=True)
        return [BrowserUse(llm=model, **args)]
    raise ValueError(f"unknown capability {kind!r}: {', '.join(KINDS)}")


def _chromium() -> str | None:
    """The Chromium Playwright installed in the image (browser-use drives the same one).

    Returns:
        Its path, or None.
    """
    import glob

    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "/opt/ms-playwright"
    found = sorted(glob.glob(f"{root}/chromium-*/chrome-linux*/chrome"))
    return found[-1] if found else None


@dataclass
class Session:
    """One conversation's long-lived run: each call is the next tool call of the run."""

    build: Callable[[], list[Any]]
    idle: float
    calls: asyncio.Queue[tuple[str, dict[str, Any], asyncio.Future[dict[str, Any]]]] = field(
        default_factory=asyncio.Queue
    )
    tools: list[Any] = field(default_factory=list)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    pending: tuple[str, asyncio.Future[dict[str, Any]]] | None = None
    task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        """Start the run."""
        self.task = asyncio.create_task(self._run())

    @property
    def alive(self) -> bool:
        """Whether the run still takes calls.

        Returns:
            True while it runs.
        """
        return self.task is not None and not self.task.done()

    async def _run(self) -> None:
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel

        agent = Agent(FunctionModel(self._model), capabilities=self.build(), name="worker")
        try:
            await agent.run("Serve the calls.")
        except Exception as e:  # noqa: BLE001 (a failed session is logged, its callers answered)
            log.warning("session_failed: %s", type(e).__name__)
        finally:
            self.ready.set()
            if self.pending is not None and not self.pending[1].done():
                self.pending[1].set_result({"ok": False, "text": "The worker's session ended."})

    async def _model(self, messages: list[Any], info: Any) -> Any:
        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            RetryPromptPart,
            TextPart,
            ToolCallPart,
            ToolReturnPart,
        )

        self.tools = list(info.function_tools)
        self.ready.set()
        if self.pending is not None:
            cid, fut = self.pending
            self.pending = None
            last = messages[-1] if messages else None
            for p in last.parts if isinstance(last, ModelRequest) else []:
                if isinstance(p, ToolReturnPart) and p.tool_call_id == cid:
                    fut.set_result({"ok": True, "text": p.model_response_str()})
                elif isinstance(p, RetryPromptPart) and p.tool_call_id == cid:
                    fut.set_result({"ok": False, "text": p.model_response()})
            if not fut.done():
                fut.set_result({"ok": False, "text": "No result."})
        try:
            name, args, fut = await asyncio.wait_for(self.calls.get(), timeout=self.idle)
        except TimeoutError:
            return ModelResponse(parts=[TextPart("idle")])
        cid = uuid.uuid4().hex
        self.pending = (cid, fut)
        return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=cid)])

    async def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run one tool call in this conversation's run.

        Returns:
            ``{ok, text}``.
        """
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        await self.calls.put((name, args, fut))
        return await fut


@dataclass
class Worker:
    """The sessions of every conversation, and the MCP endpoint."""

    kind: str
    config: dict[str, Any]
    secret: str
    idle: float = 900.0
    sessions: dict[str, Session] = field(default_factory=dict)
    builder: Callable[[dict[str, Any]], list[Any]] | None = None
    """Builds the hosted capabilities for a conversation's ``_meta`` (tests); by default
    ``capabilities_for`` the kind, with ``EvokaiModel`` as the person for browser-use."""
    evokai_url: str = ""
    metas: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Each conversation's latest ``_meta`` (its call token changes every turn)."""

    def _build(self, meta: dict[str, Any]) -> list[Any]:
        if self.builder is not None:
            return self.builder(meta)
        model = None
        if self.evokai_url:
            from evokai_worker.model import EvokaiModel

            model = EvokaiModel(self.evokai_url, meta)
        return capabilities_for(self.kind, self.config, model)

    def _session(self, key: str, meta: dict[str, Any] | None = None) -> Session:
        current = self.metas.setdefault(key, {})
        current.update(meta or {})
        s = self.sessions.get(key)
        if s is None or not s.alive:
            s = Session(lambda: self._build(current), self.idle)
            s.start()
            self.sessions[key] = s
        return s

    async def tool_list(self) -> list[dict[str, Any]]:
        """The capability's tools, as MCP lists them.

        Returns:
            Tools with their schemas and read-only hints.
        """
        s = self._session("__catalog__")
        await asyncio.wait_for(s.ready.wait(), timeout=60)
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "inputSchema": t.parameters_json_schema,
                "annotations": {"readOnlyHint": t.name in READ_ONLY},
            }
            for t in s.tools
        ]

    def authorized(self, request: Request) -> bool:
        """Whether the request carries the worker's secret.

        Returns:
            True when it does.
        """
        got = request.headers.get("authorization", "")
        return hmac.compare_digest(got.encode(), f"Bearer {self.secret}".encode())

    async def endpoint(self, request: Request) -> Response:
        """The MCP endpoint: initialize, tools/list, tools/call.

        Returns:
            The JSON-RPC answer.
        """
        if not self.authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json()
        rid = body.get("id")
        method = body.get("method")
        if rid is None:
            return Response(status_code=202)
        params = body.get("params") or {}
        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": f"evokai-worker-{self.kind}", "version": "0.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": await self.tool_list()}
        elif method == "tools/call":
            meta = params.get("_meta") or {}
            key = str(meta.get("evokai/conversation") or "")
            if not key:
                result = {
                    "content": [{"type": "text", "text": "No conversation: calls need one."}],
                    "isError": True,
                }
            else:
                out = await self._session(key, meta).call(
                    str(params.get("name")), dict(params.get("arguments") or {})
                )
                result = {
                    "content": [{"type": "text", "text": out["text"]}],
                    "isError": not out["ok"],
                }
        else:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "not found"}}
            )
        return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})

    async def health(self, _request: Request) -> Response:
        """Liveness.

        Returns:
            ok.
        """
        return JSONResponse({"ok": True, "capability": self.kind, "sessions": len(self.sessions)})

    async def close(self) -> None:
        """End every session (their browsers and pods are torn down by the runs' cleanup)."""
        for s in self.sessions.values():
            if s.task is not None:
                s.task.cancel()
                with contextlib.suppress(BaseException):
                    await s.task


def app(worker: Worker) -> Starlette:
    """The worker's HTTP app.

    Returns:
        Starlette app.
    """

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        yield
        await worker.close()

    return Starlette(
        routes=[
            Route("/mcp", worker.endpoint, methods=["POST"]),
            Route("/health", worker.health, methods=["GET"]),
        ],
        lifespan=lifespan,
    )


def from_env() -> Worker:
    """The worker the environment describes.

    Returns:
        The worker.

    Raises:
        SystemExit: No secret, or an unknown capability.
    """
    secret = os.environ.get("EVOKAI_WORKER_SECRET", "")
    if len(secret) < 16:
        raise SystemExit("EVOKAI_WORKER_SECRET must be set (at least 16 characters)")
    kind = os.environ.get("EVOKAI_WORKER_CAPABILITY", "")
    kind = "browser_use" if kind == "browser" else kind  # the browser image's own default
    if kind not in KINDS:
        raise SystemExit(f"EVOKAI_WORKER_CAPABILITY is one of {', '.join(KINDS)}")
    held = [k for k in ("EVOKAI_SERVER_KEY", "EVOKAI_ADMIN_KEY") if os.environ.get(k)]
    if held:
        # Least privilege: a worker runs untrusted pages; it gets per-turn tokens, never a key.
        raise SystemExit(f"remove {', '.join(held)}: a worker never holds an Evokai key")
    url = os.environ.get("EVOKAI_URL", "")
    if kind == "browser_use" and not url:
        # browser-use's agent calls models: only through Evokai, never with a key of its own.
        raise SystemExit("browser_use needs EVOKAI_URL (its model calls go through Evokai)")
    return Worker(
        kind=kind,
        config=json.loads(os.environ.get("EVOKAI_WORKER_CONFIG") or "{}"),
        secret=secret,
        idle=float(os.environ.get("EVOKAI_WORKER_IDLE_SECONDS") or 900),
        evokai_url=url,
    )


def main() -> None:
    """Serve."""
    import uvicorn

    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        app(from_env()),
        host="0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces] (a container's port)
        port=int(os.environ.get("EVOKAI_WORKER_PORT") or 8790),
    )
