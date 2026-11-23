"""Capability grants: defaults keep what worked; every enforcement point refuses or filters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from evokai.agents.bootstrap import bootstrap
from evokai.core.errors import ConfigError
from evokai.grants import Grants, from_yaml
from tests.conftest import ORIGIN, Env, Tool, types

GRANTS = "/agent/v1/admin/grants"


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _grant(env: Env, *changes: tuple[str, str, Any]) -> dict[str, Any]:
    r = await env.client.patch(
        GRANTS,
        json={"changes": [{"kind": k, "name": n, "value": v} for k, n, v in changes]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _mint(env: Env, **body: Any) -> httpx.Response:
    payload = {"subject": "u1", "agent": "helper", "forwardCredential": "user-token", **body}
    return await env.client.post(
        "/agent/v1/session-tokens",
        json=payload,
        headers={"Authorization": f"Bearer {env.server_key}"},
    )


def _refused(r: httpx.Response, code: str) -> None:
    assert (r.status_code, r.json()["error"]) == (403, code), r.text


def _group(view: dict[str, Any], gid: str) -> list[dict[str, Any]]:
    return next(g["items"] for g in view["groups"] if g["id"] == gid)


# Defaults and the view


async def test_by_default_everything_configured_is_granted(env: Env) -> None:
    await env.client.post(  # the console's test lists the server's tools for the view
        "/agent/v1/admin/mcp-servers/app/test", json={"credential": "x"}, headers=_adm(env)
    )
    view = (await env.client.get(GRANTS, headers=_adm(env))).json()
    assert view["overrides"] == 0
    models = {x["name"]: x for x in _group(view, "models")}
    assert models["function:primary"]["value"] == "any"
    assert models["function:primary"]["usedBy"] == ["helper"]
    assert models["function:primary"]["override"] is None
    (server,) = _group(view, "mcp")
    assert server["value"] is True and server["listed"] is True
    assert {t["label"]: t["mutating"] for t in server["children"]} == {
        "stock_report": False,
        "order_refund": True,
        "orders_month": False,
    }
    assert all(x["value"] for x in _group(view, "client"))
    assert [x["value"] for x in _group(view, "storage")] == [True, False]  # its own mode only
    # browser and server, and A2A off until it is granted
    assert [x["value"] for x in _group(view, "channels")] == [True, True] + [False] * 4
    summary = view["summary"]
    assert summary["tools"] == {"on": 3, "total": 3}
    assert summary["voice"]["browser"] and summary["voice"]["server"]
    assert summary["storage"] == ["evokai"] and summary["serverRuns"] is True


async def test_overrides_are_validated_and_can_be_reset(env: Env) -> None:
    for bad in (
        {"kind": "nope", "name": "x", "value": True},
        {"kind": "model", "name": "function:primary", "value": True},
        {"kind": "client", "name": "Teleport me", "value": False},
        {"kind": "tool", "name": "no-slash", "value": False},
        {"kind": "skill", "name": "procedura", "value": "off"},
    ):
        r = await env.client.patch(GRANTS, json={"changes": [bad]}, headers=_adm(env))
        assert (r.status_code, r.json()["error"]) == (400, "grant"), bad
    view = await _grant(
        env, ("client", "chart", False), ("model", "eu.amazon.nova-2-lite-v1:0", "off")
    )
    assert view["overrides"] == 2
    chart = next(x for x in _group(view, "client") if x["name"] == "chart")
    assert (chart["value"], chart["override"], chart["default"]) == (False, False, True)
    nova = next(
        x for x in _group(view, "models") if x["name"] == "bedrock:eu.amazon.nova-2-lite-v1:0"
    )
    assert nova["value"] == "off"
    view = await _grant(env, ("client", "chart", None))
    assert view["overrides"] == 1


def test_model_patterns_and_the_chain() -> None:
    g = Grants(
        overrides={
            ("model", "bedrock:eu.anthropic.*"): "fallback",
            ("model", "bedrock:eu.anthropic.claude-haiku-4-5"): "any",
            ("model", "bedrock:eu.amazon.*"): "off",
        }
    )
    assert g.model("eu.anthropic.claude-haiku-4-5") == "any"
    assert g.model("eu.anthropic.claude-sonnet-4-6") == "fallback"
    assert g.model("eu.amazon.nova-2-lite-v1:0") == "off"
    assert g.model("bedrock:eu.mistral.large") == "any"
    chain = ["eu.anthropic.claude-sonnet-4-6", "eu.anthropic.claude-haiku-4-5", "eu.amazon.x"]
    # A fallback-only model never answers first; an off model never answers.
    assert g.chain(chain) == ["eu.anthropic.claude-haiku-4-5"]
    assert g.chain(["eu.anthropic.claude-haiku-4-5", "eu.anthropic.claude-sonnet-4-6"]) == [
        "eu.anthropic.claude-haiku-4-5",
        "eu.anthropic.claude-sonnet-4-6",
    ]


# Session token mint


async def test_mint_refuses_what_is_not_granted(env: Env) -> None:
    await _grant(env, ("voice", "server", False))
    _refused(await _mint(env, voiceEngine="server"), "voice_engine_not_granted")
    assert (await _mint(env, voiceEngine="browser")).status_code == 200

    _refused(await _mint(env, storageMode="external"), "storage_mode_not_granted")
    await _grant(env, ("storage", "external", True))
    ok = await _mint(env, storageMode="external")
    assert ok.status_code == 200 and ok.json()["storageMode"] == "external"

    await _grant(env, ("model", "function:other", "fallback"))
    _refused(await _mint(env, model="function:other"), "model_not_granted")

    await _grant(env, ("tts", "browser/paola", False))
    _refused(await _mint(env, voice="paola"), "voice_not_granted")
    assert (await _mint(env, voice="riccardo")).status_code == 200

    await _grant(env, ("voice", "server", True), ("stt", "fake", False))
    _refused(await _mint(env, voiceEngine="server"), "stt_not_granted")

    await _grant(env, ("channel", "browser", False))
    _refused(await _mint(env), "channel_not_granted")


async def test_without_a_request_the_granted_engine_and_mode_are_chosen(env: Env) -> None:
    await _grant(env, ("voice", "browser", False))
    assert (await _mint(env)).json()["voiceEngine"] == "server"
    await _grant(env, ("storage", "evokai", False), ("storage", "external", True))
    assert (await _mint(env)).json()["storageMode"] == "external"
    await _grant(env, ("storage", "external", False))
    _refused(await _mint(env), "storage_mode_not_granted")


async def test_a_conversation_keeps_its_storage_mode(env: Env) -> None:
    await _grant(env, ("storage", "external", True))
    first = (await _mint(env, storageMode="external")).json()
    again = await _mint(env, conversationId=first["conversationId"])
    assert again.status_code == 200 and again.json()["storageMode"] == "external"
    other = await _mint(env, conversationId=first["conversationId"], storageMode="evokai")
    assert (other.status_code, other.json()["error"]) == (409, "storage_mode")
    # The external conversation keeps no content in Evokai even though the application's own
    # mode is evokai: the turn returns its messages instead.
    env.script.add("function:primary", ["Ciao."])
    ev = await env.chat(first["token"], text="Ciao", history=[])
    assert [x["role"] for x in ev[-1]["messages"]] == ["user", "assistant"]


# Turns


async def test_a_turn_offers_only_granted_tools(env: Env) -> None:
    await _grant(
        env,
        ("tool", "app/stock_report", False),
        ("client", "chart", False),
        ("client", "memory", False),
    )
    env.script.add("function:primary", ["Ok."])
    tok = await env.token()
    await env.chat(tok["token"], text="Ciao")
    seen = set(env.script.tools_seen)
    assert {"orders_month", "order_refund", "ui_diagram", "ui_ask"} <= seen
    assert not seen & {"stock_report", "ui_chart", "memory_remember", "memory_forget"}


async def test_without_confirmations_tools_that_change_data_are_not_offered(env: Env) -> None:
    await _grant(env, ("client", "confirm", False))
    env.script.add("function:primary", ["Ok."])
    await env.chat((await env.token())["token"], text="Ciao")
    assert "order_refund" not in env.script.tools_seen
    assert "orders_month" in env.script.tools_seen


async def test_a_revoked_server_drops_all_its_tools_and_a_called_one_is_unknown(env: Env) -> None:
    await _grant(env, ("mcp", "app", False))
    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["Ok."])
    ev = await env.chat((await env.token())["token"], text="Ottobre?")
    assert "orders_month" not in env.script.tools_seen
    assert env.mcp.calls == []
    assert "tool" not in types(ev)


async def test_a_revoked_skill_is_not_in_the_prompt(env: Env) -> None:
    env.script.add("function:primary", ["Ok."], ["Ok."])
    await env.chat((await env.token())["token"], text="Ciao")
    assert "skill_load" in env.script.tools_seen
    env.script.tools_seen.clear()
    await _grant(env, ("skill", "procedura", False))
    await env.chat((await env.token("u2"))["token"], text="Ciao")
    assert "skill_load" not in env.script.tools_seen


async def test_the_model_chain_skips_models_that_are_off_or_fallback_only(env: Env) -> None:
    await _grant(env, ("model", "function:primary", "off"))
    env.script.add("function:backup", ["Dal backup."])
    ev = await env.chat((await env.token())["token"], text="Ciao")
    assert [name for name, _ in env.script.seen] == ["function:backup"]
    assert "Dal backup." in "".join(e.get("delta", "") for e in ev)

    # Fallback only: the backup cannot lead the chain, so nothing can answer.
    await _grant(env, ("model", "function:backup", "fallback"))
    ev = await env.chat((await env.token("u2"))["token"], text="Ciao")
    err = next(e for e in ev if e["type"] == "error")
    assert (err["code"], ev[-1]["reason"]) == ("model", "model")
    assert len(env.script.seen) == 1


async def test_a_fallback_only_model_still_takes_over_on_capacity(env: Env) -> None:
    await _grant(env, ("model", "function:backup", "fallback"))
    env.script.add("function:primary", ModelHTTPError(429, "function:primary", "busy"))
    env.script.add("function:backup", ["Dal backup."])
    ev = await env.chat((await env.token())["token"], text="Ciao")
    assert [name for name, _ in env.script.seen] == ["function:primary", "function:backup"]
    assert ev[-1]["reason"] == "end_turn"


async def test_a_turn_is_refused_once_its_channel_mode_or_voice_is_revoked(env: Env) -> None:
    tok = (await env.token())["token"]
    await _grant(env, ("voice", "browser", False))
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "ciao", "mode": "voice"},
        headers={"Authorization": f"Bearer {tok}", "Origin": ORIGIN},
    )
    _refused(r, "voice_engine_not_granted")
    env.script.add("function:primary", ["Ok."])
    assert (await env.chat(tok, text="ciao"))[-1]["reason"] == "end_turn"
    await _grant(env, ("storage", "evokai", False))
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "ciao"},
        headers={"Authorization": f"Bearer {tok}", "Origin": ORIGIN},
    )
    _refused(r, "storage_mode_not_granted")
    await _grant(env, ("storage", "evokai", None), ("channel", "browser", False))
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "ciao"},
        headers={"Authorization": f"Bearer {tok}", "Origin": ORIGIN},
    )
    _refused(r, "channel_not_granted")


# Server runs

RUNNER = {
    "name": "runner",
    "model": "function:primary",
    "mcp_servers": ["app"],
    "channels": ["server"],
    "instructions": "Answer.",
}


async def test_server_runs_need_their_channel_and_get_only_granted_tools(env: Env) -> None:
    r = await env.client.put("/agent/v1/admin/agents/runner", json=RUNNER, headers=_adm(env))
    assert r.status_code == 200, r.text
    env.mcp.auth.clear()
    run = await env.client.post(
        "/agent/v1/agents/runner/run",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert run.status_code != 403  # granted by default (the server's auth may still fail)
    await _grant(env, ("channel", "server", False))
    run = await env.client.post(
        "/agent/v1/agents/runner/run",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    _refused(run, "channel_not_granted")


async def test_a_server_run_without_a_granted_model_is_refused(env: Env) -> None:
    await env.client.put("/agent/v1/admin/agents/runner", json=RUNNER, headers=_adm(env))
    await _grant(env, ("model", "function:primary", "off"))
    run = await env.client.post(
        "/agent/v1/agents/runner/run",
        json={"input": "hi"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    _refused(run, "model_not_granted")


# Agent save


async def test_an_agent_cannot_reference_what_is_not_granted(env: Env) -> None:
    await _grant(
        env,
        ("skill", "procedura", False),
        ("mcp", "app", False),
        ("model", "function:backup", "off"),
        ("model", "function:other", "fallback"),
        ("client", "chart", False),
        ("channel", "server", False),
    )
    body = {
        "name": "helper2",
        "model": "function:other",
        "fallbacks": ["function:backup"],
        "mcp_servers": ["app"],
        "skills": ["procedura"],
        "client_tools": {"chart": True},
        "channels": ["browser", "server"],
        "instructions": "Hi.",
    }
    r = await env.client.put("/agent/v1/admin/agents/helper2", json=body, headers=_adm(env))
    assert (r.status_code, r.json()["error"]) == (400, "not_granted")
    assert r.json()["problems"] == [
        "model function:other is not granted as primary",
        "fallback function:backup is not granted",
        "MCP server app is not granted",
        "skill procedura is not granted",
        "client tool chart is not granted",
        "server runs are not granted",
    ]
    # Client tools left at their defaults are not references: they are simply not offered.
    plain = {"name": "helper2", "model": "function:primary", "instructions": "Hi."}
    r = await env.client.put("/agent/v1/admin/agents/helper2", json=plain, headers=_adm(env))
    assert r.status_code == 200, r.text


# Configuration file

YAML = """
application:
  slug: test
  name: Test App
agents:
  - name: helper
    model: "function:primary"
    skills: [procedura]
    instructions: "Hi."
grants:
  models:
    "function:backup": off
    "bedrock:eu.anthropic.*": fallback
  skills: {procedura: false}
  client_tools: {memory: false}
  storage: {external: true}
  tools: {"app/order_refund": false}
"""


async def test_the_config_file_sets_exactly_its_grants(env: Env, tmp_path: Path) -> None:
    await _grant(env, ("client", "diagram", False))
    path = tmp_path / "app.yaml"
    path.write_text(YAML, encoding="utf-8")
    await bootstrap(env.svc.store, env.svc.keyring, path, {})
    rows = {(r.kind, r.name): r.value for r in await env.svc.store.grants.all(env.app_id)}
    assert rows == {
        ("model", "function:backup"): "off",
        ("model", "bedrock:eu.anthropic.*"): "fallback",
        ("skill", "procedura"): False,
        ("client", "memory"): False,
        ("storage", "external"): True,
        ("tool", "app/order_refund"): False,
    }
    # The agent referencing the revoked skill is applied anyway (a warning is logged).
    agent = await env.svc.store.agents.active(env.app_id, "helper")
    assert agent is not None and agent.definition["skills"] == ["procedura"]
    assert agent.created_by == "config"
    # A file without grants leaves the console's overrides alone.
    path.write_text(YAML.split("grants:")[0], encoding="utf-8")
    await bootstrap(env.svc.store, env.svc.keyring, path, {})
    assert len(await env.svc.store.grants.all(env.app_id)) == len(rows)


def test_malformed_grants_in_a_config_file_are_refused() -> None:
    assert from_yaml({"models": {"eu.x": "off"}}) == {("model", "bedrock:eu.x"): "off"}
    with pytest.raises(Exception, match="unknown section"):
        from_yaml({"teleport": {"x": True}})


async def test_bootstrap_reports_bad_grants_as_config_errors(env: Env, tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("application: {slug: test, name: T}\ngrants: {voice: {moon: true}}\n")
    with pytest.raises(ConfigError, match="Unknown voice"):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
