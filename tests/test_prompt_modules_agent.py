"""Prompt modules: an agent lists the modules it may use, with their defaults."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evokai.agents.definitions import AgentDef
from tests.conftest import Env

AGENTS = "/agent/v1/admin/agents"
TONE: dict[str, Any] = {
    "type": "object",
    "properties": {"style": {"type": "string", "enum": ["brief", "detailed"], "default": "brief"}},
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _helper(env: Env) -> dict[str, Any]:
    r = await env.client.get(AGENTS, headers=_adm(env))
    agent = next(x for x in r.json()["items"] if x["name"] == "helper")
    agent.pop("version", None)
    return agent


def test_old_agent_hash_unchanged() -> None:
    # Computed with the definitions before modules existed (literal hash).
    stored = {"name": "helper", "instructions": "Help with books.", "model": "function:x"}
    agent = AgentDef.model_validate(stored)
    assert "modules" not in agent.content()
    assert agent.with_version().version == "fb266d5fe85b"
    with_module = AgentDef.model_validate({**stored, "modules": [{"name": "tone"}]})
    assert with_module.with_version().version != "fb266d5fe85b"


def test_module_names_are_unique_and_capped() -> None:
    base = {"name": "helper", "instructions": "x", "model": "function:x"}
    with pytest.raises(ValidationError, match="once"):
        AgentDef.model_validate({**base, "modules": [{"name": "tone"}, {"name": "tone"}]})
    with pytest.raises(ValidationError):
        AgentDef.model_validate({**base, "modules": [{"name": f"m{i}"} for i in range(21)]})
    ref = AgentDef.model_validate({**base, "modules": [{"name": "tone"}]}).modules[0]
    assert (ref.version, ref.on, ref.params, ref.session) == (None, True, {}, False)


async def test_agent_save_refuses_unknown_module(env: Env) -> None:
    body = {**await _helper(env), "modules": [{"name": "tone"}]}
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "unknown_module", r.text
    await env.svc.store.prompts.put(env.app_id, "tone", "Answer {{style}}.", parameters=TONE)
    body["modules"] = [{"name": "tone", "version": 2}]
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "unknown_module", r.text
    body["modules"] = [{"name": "tone", "params": {"style": "detailed"}, "session": True}]
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text
    r = await env.client.get(AGENTS, headers=_adm(env))
    saved = next(x for x in r.json()["items"] if x["name"] == "helper")
    assert saved["modules"][0]["params"] == {"style": "detailed"}


async def test_agent_save_validates_default_params(env: Env) -> None:
    await env.svc.store.prompts.put(env.app_id, "tone", "Answer {{style}}.", parameters=TONE)
    await env.svc.store.prompts.put(env.app_id, "house", "Be kind.")
    body = {**await _helper(env), "modules": [{"name": "tone", "params": {"style": "chatty"}}]}
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "module_params", r.text
    # A plain prompt (no parameters) is a module without parameters.
    body["modules"] = [{"name": "house", "params": {"x": 1}}]
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "module_params", r.text
    body["modules"] = [{"name": "house"}, {"name": "tone"}]
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text
