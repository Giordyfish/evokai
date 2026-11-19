"""Hard rule: conversations are never stored at model providers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from evokai.agents.definitions import AgentDef
from evokai.llm.policy import FORBIDDEN_SETTINGS, request_settings
from tests.conftest import Env

SRC = Path(__file__).resolve().parents[1] / "src" / "evokai"


async def test_every_model_request_has_storage_off(env: Env) -> None:
    seen: list[dict] = []
    original = env.script.model

    def spy(name: str):
        model = original(name)
        inner = model.stream_function
        assert inner is not None

        async def stream(messages, info):
            seen.append(dict(info.model_settings or {}))
            async for x in inner(messages, info):
                yield x

        model.stream_function = stream
        return model

    env.svc.catalog._factory = spy
    env.svc.catalog._models.clear()
    env.script.add("function:primary", ["ok"])
    tok = await env.token()
    await env.chat(tok["token"], text="ciao")
    assert seen
    for settings in seen:
        assert settings.get("openai_store") is False
        assert not (set(settings) & (FORBIDDEN_SETTINGS - {"openai_store"}))


def test_agent_definitions_cannot_turn_storage_on() -> None:
    base = {"name": "a", "instructions": "x", "model": "function:x"}
    for key in FORBIDDEN_SETTINGS:
        with pytest.raises(ValueError, match="storage"):
            AgentDef.model_validate({**base, "model_settings": {key: True}})
    ok = AgentDef.model_validate({
        **base,
        "model_settings": {"openai_reasoning_effort": "low", "openai_service_tier": "priority"},
    })
    assert request_settings(ok.model_settings, 100)["openai_store"] is False


def test_request_settings_strip_chaining_even_if_injected() -> None:
    out = request_settings({"openai_previous_response_id": "auto", "openai_store": True}, 100)
    assert out["openai_store"] is False and "openai_previous_response_id" not in out


def test_no_code_path_uses_provider_side_conversations() -> None:
    pattern = re.compile(
        r"previous_response_id|openai_conversation_id|openai_store\s*=\s*True"
        r"|beta\.threads|\.assistants\.|conversations\.create"
    )
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in SRC.rglob("*.py")
        if p.name != "policy.py"
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []
