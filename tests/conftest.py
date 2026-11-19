"""Fixtures: an application on SQLite, scripted models, a fake MCP server, an HTTP client."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
import sqlalchemy as sa
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from sqlalchemy.exc import DBAPIError

from evokai.agents.bootstrap import bootstrap
from evokai.core.config import Settings
from evokai.core.security import generate_key
from evokai.llm.catalog import ModelCatalog
from evokai.main import create_app
from evokai.services import Services, build_services

ORIGIN = "http://app.test"
MCP_URL = "http://app.test/mcp"


class Tool:
    """A tool call in a scripted step."""

    def __init__(self, name: str, args: dict[str, Any], call_id: str = "t1") -> None:
        self.name, self.args, self.call_id = name, args, call_id


def _resolve(step: Any, messages: list[ModelMessage]) -> Any:
    """A scripted step: a function of the messages is called to produce its items."""
    return step(messages) if callable(step) and not isinstance(step, BaseException) else step


@dataclass
class Script:
    """Scripted model steps per model name: text chunks, Tool calls, or an exception to raise."""

    steps: dict[str, list[Any]] = field(default_factory=dict)
    seen: list[tuple[str, list[ModelMessage]]] = field(default_factory=list)
    tools_seen: list[str] = field(default_factory=list)
    tool_defs: dict[str, Any] = field(default_factory=dict)
    """The last definition the model received of each tool (name to ``ToolDefinition``)."""
    gate: Any = None
    """An asyncio.Event a step can wait on (to test cancellation mid-stream)."""
    waiting: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when a step reaches its WAIT: what it said before is on its way (no fixed sleeps)."""

    def add(self, model: str, *steps: Any) -> None:
        """Steps: a list of items, an exception, or a function of the messages returning a list."""
        self.steps.setdefault(model, []).extend(steps)

    def model(self, name: str) -> FunctionModel:
        async def stream(messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[Any]:
            self.seen.append((name, messages))
            self.tools_seen.extend(t.name for t in _info.function_tools)
            self.tool_defs.update({t.name: t for t in _info.function_tools})
            queue = self.steps.get(name) or []
            step: Any = _resolve(queue.pop(0) if queue else ["ok"], messages)
            if isinstance(step, BaseException):
                raise step
            for i, item in enumerate(step):
                if item == "WAIT" and self.gate is not None:
                    self.waiting.set()
                    await self.gate.wait()
                    continue
                if isinstance(item, Tool):
                    yield {
                        i: DeltaToolCall(
                            name=item.name,
                            json_args=json.dumps(item.args),
                            tool_call_id=item.call_id,
                        )
                    }
                else:
                    yield item

        def call(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.seen.append((name, messages))
            self.tools_seen.extend(t.name for t in info.function_tools)
            self.tool_defs.update({t.name: t for t in info.function_tools})
            self.tools_seen.extend(t.name for t in info.output_tools)
            queue = self.steps.get(name) or []
            step: Any = _resolve(queue.pop(0) if queue else ["ok"], messages)
            if isinstance(step, BaseException):
                raise step
            items: list[Any] = list(step)
            parts: list[Any] = []
            text = "".join(x for x in items if isinstance(x, str) and x != "WAIT")
            if text:
                parts.append(TextPart(content=text))
            parts += [
                ToolCallPart(tool_name=x.name, args=x.args, tool_call_id=x.call_id)
                for x in items
                if isinstance(x, Tool)
            ]
            return ModelResponse(parts=parts)

        return FunctionModel(call, stream_function=stream, model_name=name.partition(":")[2])


ORDERS_MONTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string"},
        "args": {"type": "object"},
        "orders": {"type": "integer"},
    },
    "required": ["tool", "orders"],
}
"""The ``outputSchema`` of ``orders_month`` when the fake server declares structured output."""


@dataclass
class FakeMcp:
    """A fake MCP server: two read tools and one that changes data."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    auth: list[str] = field(default_factory=list)
    down: bool = False
    structured: bool = False
    """``orders_month`` declares an ``outputSchema`` and its calls return ``structuredContent``
    (with a text block that is NOT its JSON copy, so a test sees which one Evokai used)."""
    bad_structured: bool = False
    """The structured content does not match the declared schema."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        self.auth.append(request.headers.get("authorization", ""))
        body = json.loads(request.content)
        method = body.get("method")
        if "id" not in body:
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"protocolVersion": "2025-06-18"},
                },
            )
        if method == "tools/list":
            tools = [
                {
                    "name": "orders_month",
                    "description": "Orders of a month",
                    "inputSchema": {"type": "object", "properties": {"period": {"type": "string"}}},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "stock_report",
                    "description": "Stock report",
                    "inputSchema": {"type": "object", "properties": {}},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "order_refund",
                    "description": "Rimborsa",
                    "inputSchema": {"type": "object", "properties": {}},
                    "annotations": {"readOnlyHint": False, "destructiveHint": True},
                },
            ]
            if self.structured:
                tools[0]["outputSchema"] = ORDERS_MONTH_SCHEMA
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}}
            )
        if method == "tools/call":
            name, args = body["params"]["name"], body["params"].get("arguments", {})
            self.calls.append((name, args))
            text = json.dumps({"tool": name, "args": args, "orders": 24})
            result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
            if self.structured and name == "orders_month":
                orders: Any = "many" if self.bad_structured else 31
                result = {
                    "content": [{"type": "text", "text": "31 orders"}],
                    "structuredContent": {"tool": name, "args": args, "orders": orders},
                }
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "nope"}},
        )


AGENTS_YAML = """
application:
  slug: test
  name: Test App
  monthly_cap_usd: 5
  allowed_origins: ["http://app.test"]
mcp_servers:
  - name: app
    url: "http://app.test/mcp"
    auth: forward_user
skills_dir: skills
agents:
  - name: helper
    title: Helper
    model: "function:primary"
    models: ["function:other"]
    fallbacks: ["function:backup"]
    mcp_servers: [app]
    confirm_labels:
      order_refund:
        - "Refund the orders of {period} as {reason|map:reasons}"
        - "Refund the orders of {period}"
    confirm_values:
      reasons: {damaged: "Damaged", wrong_item: "Wrong item"}
    max_tool_calls_per_turn: 4
    suggestions: ["How did the last month go?", "What is due this week?"]
    voice:
      first_clause_min_chars: 0
      fillers: {"*": "One moment."}
    instructions: "You are an assistant. Today is {date}."
"""

SKILL = """---
name: procedura
description: Come si gestisce un reso.
---
Primo passo, secondo passo.
"""


@dataclass
class Env:
    """Everything a test needs."""

    svc: Services
    client: httpx.AsyncClient
    script: Script
    mcp: FakeMcp
    server_key: str
    admin_key: str
    app_id: str

    async def token(self, subject: str = "u1", **body: Any) -> dict[str, Any]:
        payload = {"subject": subject, "agent": "helper", "forwardCredential": "user-token", **body}
        r = await self.client.post(
            "/agent/v1/session-tokens",
            json=payload,
            headers={"Authorization": f"Bearer {self.server_key}"},
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def chat(self, token: str, **body: Any) -> list[dict[str, Any]]:
        r = await self.client.post(
            "/agent/v1/session/chat",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        assert r.status_code == 200, r.text
        return parse_sse(r.text)


def parse_sse(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("\n\n"):
        data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def system_text(messages: list[ModelMessage]) -> str:
    """The system prompt a model received."""
    first = messages[0]
    assert isinstance(first, ModelRequest)
    return "\n".join(p.content for p in first.parts if isinstance(p, SystemPromptPart))


def types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


@contextlib.asynccontextmanager
async def make_env(
    tmp_path: Path,
    *,
    agents_yaml: str = AGENTS_YAML,
    http: httpx.AsyncClient | None = None,
    models: Callable[[str], Any] | Literal["scripted"] | None = "scripted",
) -> AsyncGenerator[Env]:
    """An application on a fresh database, reached through Evokai's HTTP API.

    Args:
        tmp_path: Where the database and the application's files go.
        agents_yaml: The application YAML.
        http: The HTTP client of MCP servers (default: the fake MCP server, in memory).
        models: Builds a model from its name: ``scripted`` (the environment's scripted models),
            or None for the real providers (``tests/test_recorded.py``).

    Yields:
        The environment.
    """
    (tmp_path / "skills" / "procedura").mkdir(parents=True)
    (tmp_path / "skills" / "procedura" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (tmp_path / "agents.yaml").write_text(agents_yaml, encoding="utf-8")
    pg = os.environ.get("EVOKAI_TEST_DATABASE_URL")
    schema = f"t_{uuid.uuid4().hex[:12]}" if pg else None
    settings = Settings(
        database_url=pg or f"sqlite+aiosqlite:///{tmp_path / 'evokai.db'}",
        database_schema=schema,
        model_allow=["function:*", "bedrock:eu.*"],
        filler_after_ms=10,
        memory_embedding_model="test:hash",
        redteam_judge_model=None,
        redteam_on_save=False,
    )
    script = Script()
    mcp = FakeMcp()
    if http is None:
        http = httpx.AsyncClient(transport=httpx.MockTransport(mcp.handler))
    factory = script.model if models == "scripted" else models
    catalog = ModelCatalog(settings.model_allow, settings.aws_region, factory=factory)
    svc = build_services(settings, http=http, catalog=catalog)
    if schema:
        async with svc.db.engine.begin() as c:
            await c.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    await svc.db.create_all()
    srv, adm = generate_key("srv"), generate_key("adm")
    app_id = await bootstrap(
        svc.store, svc.keyring, tmp_path / "agents.yaml", {"srv": srv.value, "adm": adm.value}
    )
    app = create_app(settings, svc)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evokai")
    try:
        yield Env(
            svc=svc,
            client=client,
            script=script,
            mcp=mcp,
            server_key=srv.value,
            admin_key=adm.value,
            app_id=app_id,
        )
    finally:
        await client.aclose()
        await svc.close()
        if schema:
            await _drop_schema(settings, schema)


LEAK_WAIT_SECONDS = 5.0
"""How long the tasks a test left running get to honour their cancellation."""


def _describe(task: asyncio.Task[Any]) -> str:
    """A task, where its coroutine waits and on what (for a log a person can act on).

    Returns:
        One line.
    """
    frames = task.get_stack(limit=6)
    where = " <- ".join(
        f"{f.f_code.co_filename.rsplit('/', 2)[-1]}:{f.f_lineno} {f.f_code.co_name}" for f in frames
    )
    return f"{task.get_name()} {task.get_coro()!r}: {where or 'no frame'}"


async def _cancel_hard(tasks: list[asyncio.Task[Any]], seconds: float) -> set[asyncio.Task[Any]]:
    """Cancel tasks again and again until they end or time runs out.

    A task that catches its cancellation to wait on a cleanup (a network close without a timeout)
    gets cancelled again in that wait.

    Returns:
        The tasks still running.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    pending = {t for t in tasks if not t.done()}
    while pending and loop.time() < deadline:
        for t in pending:
            t.cancel()
        _, pending = await asyncio.wait(pending, timeout=min(0.5, max(0.0, deadline - loop.time())))
    return pending


@pytest.fixture(autouse=True)
async def _no_task_outlives_its_test(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """After a test and its fixtures, the tasks it left running are cancelled, hard, within seconds.

    A task that kept ignoring its cancellation used to hang the event loop's closing for good: the
    suite stopped at its 20 minute limit and no test was named (seen once in CI). The task is
    now cancelled until it ends; one that still runs is named at once on stderr, before the loop
    closes, and fails its test. ``EVOKAI_TEST_LEAKS=report`` prints every task a test left behind.
    """
    before = set(asyncio.all_tasks())
    yield
    me = asyncio.current_task()
    left = [t for t in asyncio.all_tasks() if t is not me and t not in before and not t.done()]
    if not left:
        return
    if os.environ.get("EVOKAI_TEST_LEAKS") == "report":
        print(f"\nLEFT RUNNING by {request.node.nodeid}:\n  " + "\n  ".join(map(_describe, left)))
    stuck = await _cancel_hard(left, LEAK_WAIT_SECONDS)
    if stuck:
        text = (
            f"{request.node.nodeid}: {len(stuck)} task(s) still running after "
            f"{LEAK_WAIT_SECONDS:.0f} s of cancellations:\n  " + "\n  ".join(map(_describe, stuck))
        )
        print(text, file=sys.__stderr__, flush=True)
        pytest.fail(text, pytrace=False)


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    async with make_env(tmp_path) as e:
        yield e


async def _drop_schema(settings: Settings, schema: str) -> None:
    """Drop a test schema once nothing uses it (a cancelled turn may still be finishing)."""
    from evokai.db.engine import Database

    db = Database(settings.model_copy(update={"database_schema": None}))
    try:
        for attempt in range(5):
            try:
                async with db.engine.begin() as c:
                    await c.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
                return
            except DBAPIError:
                if attempt == 4:
                    raise
                await asyncio.sleep(0.2)
    finally:
        await db.close()
