"""The capability worker: tools over MCP, state per conversation, the secret."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai.capabilities import Toolset
from pydantic_ai.toolsets import FunctionToolset

from evokai_worker.server import Worker, app

SECRET = "s" * 24  # gitleaks:allow (a fake secret of the tests)


def _counter(_meta: dict[str, Any] | None = None) -> list[Any]:
    """A stateful capability: what it counted lives in its run, like a browser or a pod."""
    seen: list[str] = []
    ts = FunctionToolset()

    def remember(item: str) -> str:
        """Remember an item.

        Args:
            item: What to remember.
        """
        seen.append(item)
        return ", ".join(seen)

    def read_file(path: str) -> str:
        """Read a file.

        Args:
            path: The file.
        """
        return f"contents of {path}"

    ts.add_function(remember)
    ts.add_function(read_file)
    return [Toolset(ts)]


def _client(worker: Worker) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app(worker)), base_url="http://w")


async def _rpc(c: httpx.AsyncClient, method: str, params: Any = None, secret: str = SECRET) -> Any:
    r = await c.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Authorization": f"Bearer {secret}"},
    )
    return r


async def test_tools_are_listed_with_read_only_hints() -> None:
    w = Worker(kind="kubernetes", config={}, secret=SECRET, builder=_counter)
    async with _client(w) as c:
        init = (await _rpc(c, "initialize")).json()["result"]
        assert init["capabilities"] == {"tools": {}}
        tools = {t["name"]: t for t in (await _rpc(c, "tools/list")).json()["result"]["tools"]}
        assert tools["read_file"]["annotations"] == {"readOnlyHint": True}
        assert tools["remember"]["annotations"] == {"readOnlyHint": False}
        assert tools["remember"]["inputSchema"]["required"] == ["item"]
    await w.close()


async def test_state_lives_per_conversation() -> None:
    w = Worker(kind="kubernetes", config={}, secret=SECRET, builder=_counter)

    def call(item: str, conversation: str) -> dict[str, Any]:
        return {
            "name": "remember",
            "arguments": {"item": item},
            "_meta": {"evokai/conversation": conversation},
        }

    async with _client(w) as c:
        await _rpc(c, "tools/call", call("a", "c1"))
        second = (await _rpc(c, "tools/call", call("b", "c1"))).json()["result"]
        other = (await _rpc(c, "tools/call", call("x", "c2"))).json()["result"]
        assert second["content"][0]["text"] == "a, b" and second["isError"] is False
        assert other["content"][0]["text"] == "x"
        none = (
            await _rpc(c, "tools/call", {"name": "remember", "arguments": {"item": "z"}})
        ).json()["result"]
        assert none["isError"] is True
    await w.close()


async def test_the_secret_is_required() -> None:
    w = Worker(kind="kubernetes", config={}, secret=SECRET, builder=_counter)
    async with _client(w) as c:
        assert (await _rpc(c, "tools/list", secret="wrong")).status_code == 401
    await w.close()


async def test_browser_use_calls_models_through_evokai_with_the_turn_s_token() -> None:
    import json

    from pydantic import TypeAdapter
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models import ModelRequestParameters

    from evokai_worker.model import CapReached, EvokaiModel, TurnOver

    seen: list[dict[str, Any]] = []
    answer = {"status": 200}
    meta = {"evokai/conversation": "c1", "evokai/token": "turn-1-token"}

    def evokai(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {meta['evokai/token']}"
        seen.append(json.loads(request.content))
        if answer["status"] == 429:
            return httpx.Response(429, json={"error": "limit", "message": "Cap reached."})
        if answer["status"] == 401:
            return httpx.Response(401, json={"error": "token", "message": "Turn over."})
        out = TypeAdapter(ModelResponse).dump_python(
            ModelResponse(parts=[TextPart("click the blue button")]), mode="json"
        )
        return httpx.Response(200, json={"response": out})

    http = httpx.AsyncClient(transport=httpx.MockTransport(evokai))
    model = EvokaiModel("http://evokai/agent", meta, http)

    async def ask() -> Any:
        return await model.request(
            [ModelRequest(parts=[UserPromptPart("Which button?")])], None, ModelRequestParameters()
        )

    r = await ask()
    assert r.parts[0].content == "click the blue button"
    # Who the call is for is the token's business: the body says nothing about the person.
    assert set(seen[0]) == {"messages", "parameters", "settings"}
    # The next turn's MCP call brings a new token, used from then on.
    meta["evokai/token"] = "turn-2-token"
    await ask()
    answer["status"] = 429
    try:
        await ask()
    except CapReached as e:
        assert "Cap reached" in str(e)
    else:
        raise AssertionError("a spent cap was ignored")
    answer["status"] = 401
    try:
        await ask()
    except TurnOver:
        pass
    else:
        raise AssertionError("an expired token was ignored")


def test_browser_use_needs_evokai_and_holds_no_key(monkeypatch: Any) -> None:
    import pytest

    from evokai_worker.server import from_env

    monkeypatch.setenv("EVOKAI_WORKER_SECRET", SECRET)
    monkeypatch.setenv("EVOKAI_WORKER_CAPABILITY", "browser_use")
    monkeypatch.delenv("EVOKAI_URL", raising=False)
    monkeypatch.delenv("EVOKAI_SERVER_KEY", raising=False)
    with pytest.raises(SystemExit, match="EVOKAI_URL"):
        from_env()
    # Least privilege: an Evokai key in its environment is refused.
    monkeypatch.setenv("EVOKAI_URL", "http://evokai/agent")
    monkeypatch.setenv("EVOKAI_SERVER_KEY", "evk_srv_x")
    with pytest.raises(SystemExit, match="never holds an Evokai key"):
        from_env()


def test_playwright_runs_chromium_without_its_sandbox_unless_asked(monkeypatch: Any) -> None:
    import sys
    import types

    from evokai_worker.server import capabilities_for

    # The browser extra is not installed with the kubernetes one the gates sync: a stand-in records what
    # the worker passes to PlaywrightBrowser.
    class FakeBrowser:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake = types.ModuleType("pydantic_ai_harness.playwright")
    fake.PlaywrightBrowser = FakeBrowser  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pydantic_ai_harness.playwright", fake)

    # Chromium's sandbox needs user namespaces, which Docker's default seccomp profile refuses: with it on,
    # Chromium dies at launch and every browser tool answers "the active tab has closed" (#25).
    (default,) = capabilities_for("playwright", {"block_private_addresses": False})
    assert default.kwargs == {"chromium_sandbox": False, "block_private_addresses": False}
    # A runtime that grants user namespaces (Playwright's seccomp profile) can turn it back on.
    (asked,) = capabilities_for("playwright", {"chromium_sandbox": True})
    assert asked.kwargs == {"chromium_sandbox": True}
