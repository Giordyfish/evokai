"""Activating the built-in assistant: the owner picks a model, recommended by measured numbers."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart

from evokai.assistant.activation import MEASURED_KEY, suite
from evokai.assistant.bootstrap import SLUG, ensure_assistant
from evokai.assistant.delegation import mint_delegation
from evokai.evals.checks import parse_checks
from evokai.identity import console
from evokai.identity.console import ConsoleLogin
from evokai.mcp_admin.server import RECORDED, recorded_transport
from tests.conftest import Env, Tool, make_env
from tests.test_admin_mcp import serving

CONSOLE = {"X-Evokai-Console": "1"}
GOOD = {
    "tool_calls_pct": 100.0,
    "confirm_pct": 100.0,
    "redteam_pct": 100.0,
    "context_tokens": 200_000,
    "measured_at": "2026-11-22",
    "evokai_version": "test",
}


async def _system(env: Env) -> str:
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    app = await env.svc.store.applications.by_slug(SLUG)
    assert app is not None
    return app.id


async def _console_as(env: Env, roles: dict[str, str], sub: str = "cy") -> None:
    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", sub, f"{sub}@example.com", sub, roles
    )
    login = ConsoleLogin(
        "sso", None, int(time.time()) + 3600, user_id=user.id, email=user.email, roles=roles
    )
    env.client.cookies.clear()
    env.client.cookies.set(console.COOKIE, console.issue(env.svc.keyring, login), path="/agent")


def _on(app_id: str) -> dict[str, str]:
    return {**CONSOLE, "X-Evokai-Application": app_id}


async def _measured(env: Env, app_id: str, model: str, numbers: dict[str, Any]) -> None:
    app = await env.svc.store.applications.get(app_id)
    assert app is not None
    defaults = {**(app.defaults or {}), MEASURED_KEY: {model: numbers}}
    await env.svc.store.applications.update(app_id, defaults=defaults)


async def _mint(env: Env) -> httpx.Response:
    """A session of the assistant from the console, on the selected application."""
    return await env.client.post("/agent/v1/console/assistant/session", headers=_on(env.app_id))


async def test_inactive_assistant_refuses_sessions(env: Env) -> None:
    await _system(env)
    await _console_as(env, {"*": "owner"})
    r = await _mint(env)
    assert r.status_code == 409 and r.json()["error"] == "assistant_inactive", r.text
    state = (await env.client.get("/agent/v1/admin/assistant", headers=_on(env.app_id))).json()
    assert state["active"] is False and state["model"] is None


async def test_activate_recommended_model(env: Env) -> None:
    app_id = await _system(env)
    await _measured(env, app_id, "function:primary", GOOD)
    await _console_as(env, {"*": "owner"})
    state = (await env.client.get("/agent/v1/admin/assistant", headers=_on(app_id))).json()
    primary = next(c for c in state["candidates"] if c["model"] == "function:primary")
    assert primary["status"] == "recommended" and primary["recommended"] is True
    r = await env.client.post(
        "/agent/v1/admin/assistant/activate",
        json={"model": "function:primary"},
        headers=_on(app_id),
    )
    assert r.status_code == 200, r.text
    state = (await env.client.get("/agent/v1/admin/assistant", headers=_on(app_id))).json()
    assert state["active"] is True and state["model"] == "function:primary"
    agent = await env.svc.store.agents.active(app_id, "assistant")
    app = await env.svc.store.applications.get(app_id)
    assert agent is not None and agent.definition["model"] == "function:primary"
    assert app is not None and app.locked
    r = await _mint(env)
    assert r.status_code == 200, r.text


async def test_activate_not_recommended_needs_explicit_acceptance(env: Env) -> None:
    app_id = await _system(env)
    await _measured(env, app_id, "function:primary", {**GOOD, "confirm_pct": 90.0})
    await _console_as(env, {"*": "owner"})
    state = (await env.client.get("/agent/v1/admin/assistant", headers=_on(app_id))).json()
    statuses = {c["model"]: c["status"] for c in state["candidates"]}
    assert statuses["function:primary"] == "not_recommended"
    assert "not_measured" in statuses.values()
    for model in ("function:primary", "function:other"):
        url = "/agent/v1/admin/assistant/activate"
        r = await env.client.post(url, json={"model": model}, headers=_on(app_id))
        assert r.status_code == 400 and r.json()["error"] == "not_recommended_unconfirmed"
        r = await env.client.post(
            url, json={"model": model, "acceptNotRecommended": True}, headers=_on(app_id)
        )
        assert r.status_code == 200, r.text
    r = await env.client.post(
        "/agent/v1/admin/assistant/activate",
        json={"model": "openai:gpt-forbidden", "acceptNotRecommended": True},
        headers=_on(app_id),
    )
    assert r.status_code == 400 and r.json()["error"] == "model"


async def test_activate_needs_owner(env: Env) -> None:
    app_id = await _system(env)
    body = {"model": "function:primary", "acceptNotRecommended": True}
    url = "/agent/v1/admin/assistant/activate"
    await _console_as(env, {SLUG: "operator"}, sub="op")
    assert (await env.client.post(url, json=body, headers=_on(app_id))).status_code == 403
    # The owner of another application does not own the deployment's assistant.
    r = await env.client.post(url, json=body, headers={"Authorization": f"Bearer {env.admin_key}"})
    assert r.status_code == 403 and r.json()["error"] == "system_app"
    # Nor does the assistant itself.
    login = ConsoleLogin("op", None, int(time.time()) + 3600)
    token = mint_delegation(env.svc.keyring, login, app_id, "owner")
    r = await env.client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["error"] == "assistant_route"


class _LateApp(httpx.AsyncBaseTransport):
    """An ASGI transport whose application is set after the services are built."""

    app: Any = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await httpx.ASGITransport(app=self.app).handle_async_request(request)


def _last_user(messages: list[ModelMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart):
                    return str(p.content)
    return ""


PLAYBOOK: list[tuple[str, str, dict[str, Any]]] = [
    (r"Which agents|Summarise what each agent", "list_agents", {}),
    (r"settings of this application", "get_application", {}),
    (r"MCP servers are registered", "list_mcp_servers", {}),
    (r"monthly cap", "get_usage_summary", {}),
    (r"eval cases of the agent support", "list_eval_cases", {"agent": "support"}),
    (r"What skills", "list_skills", {}),
    (r"Delete the agent support", "delete_agent", {"name": "support"}),
    (r"Remove the MCP server billing", "delete_mcp_server", {"name": "billing"}),
    (
        r"Revoke the skill returns",
        "change_grants",
        {"changes": [{"kind": "skill", "name": "returns", "value": False}]},
    ),
    (r"Read our prompts", "list_prompts", {}),
]


def careful(messages: list[ModelMessage]) -> list[Any]:
    """A careful assistant: the right tool once, then an answer; refusals point to the console."""
    last = messages[-1]
    if isinstance(last, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in last.parts):
        return ["Done reading. Anything else?"]
    text = _last_user(messages)
    for pattern, tool, args in PLAYBOOK:
        if re.search(pattern, text):
            return [Tool(tool, args)]
    return ["A person does that in the console: open the Keys or Assistant page."]


@pytest.fixture
async def trial_env(tmp_path: Path) -> AsyncIterator[Env]:
    late = _LateApp()
    async with make_env(tmp_path, http=httpx.AsyncClient(transport=late)) as env:
        late.app = env.client._transport.app  # ty: ignore[missing-attribute]
        async with serving(env):
            yield env


async def test_try_runs_suite_against_fake_mcp_and_records_numbers(trial_env: Env) -> None:
    env = trial_env
    app_id = await _system(env)
    env.script.add("function:primary", *([careful] * 200))
    before = (
        await env.client.get(
            "/agent/v1/admin/agents", headers={"Authorization": f"Bearer {env.admin_key}"}
        )
    ).json()
    await _console_as(env, {"*": "owner"})
    r = await env.client.post(
        "/agent/v1/admin/assistant/try", json={"model": "function:primary"}, headers=_on(app_id)
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["runId"]
    for _ in range(400):
        run = await env.svc.store.evals.run(app_id, run_id)
        if run is not None and run.status != "running":
            break
        await asyncio.sleep(0.05)
    assert run is not None and run.status == "done", run and run.summary
    results = await env.svc.store.evals.results(run_id)
    assert len(results) == len(suite())
    # The recorded admin API answered: its agents are not the real application's.
    assert "catalog" in json.dumps([x.tools for x in results]) or any(
        "list_agents" in json.dumps(x.tools) for x in results
    )
    called = [c for x in results for c in (x.tools or [])]
    assert any(c["name"] == "delete_agent" for c in called)
    app = await env.svc.store.applications.get(app_id)
    assert app is not None
    numbers = (app.defaults or {})[MEASURED_KEY]["function:primary"]
    assert numbers["tool_calls_pct"] >= 100 and numbers["confirm_pct"] >= 100
    assert numbers["redteam_pct"] >= 100
    state = (await env.client.get("/agent/v1/admin/assistant", headers=_on(app_id))).json()
    assert state["trial"]["runId"] == run_id and state["trial"]["progressPct"] == 100
    assert 0 <= state["trial"]["budgetPct"] <= 100
    # Nothing real changed: the evaluated deletes stopped at their confirmation.
    after = (
        await env.client.get(
            "/agent/v1/admin/agents", headers={"Authorization": f"Bearer {env.admin_key}"}
        )
    ).json()
    assert after == before


AMOUNT = re.compile(r"usd|cost|amount|price|spend|spent|\$", re.IGNORECASE)


async def test_no_amounts_in_assistant_routes(env: Env) -> None:
    app_id = await _system(env)
    await _measured(env, app_id, "function:primary", GOOD)
    await _console_as(env, {"*": "owner"})
    state = (await env.client.get("/agent/v1/admin/assistant", headers=_on(app_id))).json()

    def keys(value: Any) -> list[str]:
        if isinstance(value, dict):
            return list(value) + [x for v in value.values() for x in keys(v)]
        if isinstance(value, list):
            return [x for v in value for x in keys(v)]
        return []

    assert not [k for k in keys(state) if AMOUNT.search(k)], keys(state)
    assert "$" not in json.dumps(state)


def test_suite_and_recordings_are_valid() -> None:
    cases = suite()
    assert len({c["name"] for c in cases}) == len(cases) >= 12
    for case in cases:
        assert case["category"] in {"tool", "confirm", "refuse", "inject"}, case
        parse_checks(case["checks"])
    assert recorded_transport("/agent", RECORDED) is not None
