"""Prompt caching on Bedrock Anthropic models, preloaded skills and the cost of cached tokens."""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.providers.bedrock import BedrockProvider

from evokai.agents.definitions import AgentDef
from evokai.llm.catalog import bare
from evokai.llm.prices import cost_micro_usd
from tests.conftest import ORIGIN, Env

HAIKU = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
NOVA = "eu.amazon.nova-2-lite-v1:0"
SKILL_BODY = "Primo passo, secondo passo."

USAGE = {
    "inputTokens": 100,
    "outputTokens": 50,
    "totalTokens": 5150,
    "cacheReadInputTokens": 4000,
    "cacheWriteInputTokens": 1000,
    "cacheDetails": [{"ttl": "1h", "inputTokens": 1000}],
}


class _Stream:
    """A Bedrock event stream: iterable and closable."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def __iter__(self) -> Any:
        return iter(self.events)

    def close(self) -> None:
        pass


class FakeBedrock:
    """Bedrock runtime clients that record every request and answer from a script."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.fail: set[str] = set()
        self.answers: list[dict[str, Any]] = []

    def model(self, name: str) -> BedrockConverseModel:
        model_id = bare(name)
        client = boto3.client(
            "bedrock-runtime",
            region_name="eu-north-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )

        def converse(**kw: Any) -> dict[str, Any]:
            self.requests.append((model_id, kw))
            if model_id in self.fail:
                raise ClientError(
                    {
                        "Error": {"Code": "ThrottlingException", "Message": "Too many requests"},
                        "ResponseMetadata": {"HTTPStatusCode": 429},
                    },
                    "Converse",
                )
            content = self.answers.pop(0) if self.answers else {"text": "ok"}
            return {
                "output": {"message": {"role": "assistant", "content": [content]}},
                "stopReason": "tool_use" if "toolUse" in content else "end_turn",
                "usage": USAGE,
                "metrics": {"latencyMs": 5},
                "ResponseMetadata": {},
            }

        def converse_stream(**kw: Any) -> dict[str, Any]:
            self.requests.append((model_id, kw))
            events = [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Ciao."}}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                {"metadata": {"usage": USAGE, "metrics": {"latencyMs": 5}}},
            ]
            return {"stream": _Stream(events), "ResponseMetadata": {}}

        client.converse = converse
        client.converse_stream = converse_stream
        return BedrockConverseModel(model_id, provider=BedrockProvider(bedrock_client=client))


@pytest.fixture
def bedrock(env: Env) -> FakeBedrock:
    fake = FakeBedrock()
    script_model = env.script.model

    def factory(name: str) -> Any:
        return fake.model(name) if name.startswith("bedrock:") else script_model(name)

    env.svc.catalog._factory = factory
    env.svc.catalog._models.clear()
    return fake


TRIAGE: dict[str, Any] = {
    "name": "triage",
    "model": HAIKU,
    "fallbacks": [NOVA],
    "mcp_servers": ["app"],
    "skills": ["procedura"],
    "preload_skills": ["procedura"],
    "prompt_cache": "1h",
    "channels": ["server"],
    "instructions": "Classify the customer's e-mail.",
    "output_schema": {
        "type": "object",
        "required": ["category"],
        "properties": {"category": {"type": "string"}},
        "additionalProperties": False,
    },
}
ANSWER = {"toolUse": {"toolUseId": "o1", "name": "final_result", "input": {"category": "order"}}}


async def _put(env: Env, body: dict[str, Any]) -> None:
    r = await env.client.put(
        f"/agent/v1/admin/agents/{body['name']}",
        json=body,
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


async def _run(env: Env, text: str = "My order arrived twice this month.") -> Any:
    return await env.client.post(
        "/agent/v1/agents/triage/run",
        json={"input": text},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )


def _cache_points(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b["cachePoint"] for b in blocks if "cachePoint" in b]


async def test_a_cached_run_sends_cache_points_with_the_agents_ttl(
    env: Env, bedrock: FakeBedrock
) -> None:
    await _put(env, TRIAGE)
    bedrock.answers = [ANSWER]
    r = await _run(env)
    assert r.status_code == 200, r.text
    model, req = bedrock.requests[0]
    assert model == HAIKU
    system = req["system"]
    # The whole system prompt is the stable prefix, the cache point closes it (1 hour).
    assert [list(b) for b in system] == [["text"], ["cachePoint"]]
    assert system[1]["cachePoint"] == {"type": "default", "ttl": "1h"}
    assert "Classify the customer's e-mail." in system[0]["text"]
    assert SKILL_BODY in system[0]["text"]  # preloaded: in the cached prefix
    # Tool definitions end with their own cache point; no skill_load once every skill is preloaded.
    tools = req["toolConfig"]["tools"]
    assert _cache_points(tools) == [{"type": "default", "ttl": "1h"}]
    names = [t["toolSpec"]["name"] for t in tools if "toolSpec" in t]
    assert "skill_load" not in names and "final_result" in names
    # The per-request text is in the messages, after the prefix. With no tool to call (the app's
    # server forwards a user credential, which a server run does not have) the run ends with its
    # answer, so the conversation itself is not cached.
    user = req["messages"][0]["content"]
    assert "My order arrived twice" in user[0]["text"] and SKILL_BODY not in user[0]["text"]
    assert not _cache_points(user)


async def test_the_stable_prefix_is_the_same_for_every_input(
    env: Env, bedrock: FakeBedrock
) -> None:
    await _put(env, TRIAGE)
    bedrock.answers = [ANSWER, ANSWER]
    assert (await _run(env, "First e-mail")).status_code == 200
    assert (await _run(env, "Second, different e-mail")).status_code == 200
    first, second = (req for _, req in bedrock.requests)
    assert first["system"] == second["system"]
    assert first["toolConfig"] == second["toolConfig"]


async def test_cache_tokens_are_priced_and_recorded(env: Env, bedrock: FakeBedrock) -> None:
    await _put(env, TRIAGE)
    bedrock.answers = [ANSWER]
    body = (await _run(env)).json()
    usage = body["usage"]
    assert usage["inputTokens"] == 5100  # cached tokens included
    assert usage["cacheReadTokens"] == 4000 and usage["cacheWriteTokens"] == 1000
    # 100 fresh at 1.1, 4000 read at 0.11, 1000 written for 1 hour at 2.2, 50 out at 5.5 ($/M).
    assert usage["costUsd"] == pytest.approx((110 + 440 + 2200 + 275) / 1e6)
    report = await env.client.get(
        "/agent/v1/usage",
        params={"groupBy": "agent"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    row = next(x for x in report.json()["rows"] if x["agent"] == "triage")
    assert row["cacheReadTokens"] == 4000 and row["cacheWriteTokens"] == 1000
    assert row["costUsd"] == pytest.approx(usage["costUsd"])


async def test_a_fallback_of_another_family_caches_its_own_way(
    env: Env, bedrock: FakeBedrock
) -> None:
    await _put(env, TRIAGE)
    bedrock.fail = {HAIKU}
    bedrock.answers = [ANSWER]
    r = await _run(env)
    assert r.status_code == 200, r.text
    assert bare(r.json()["model"]) == NOVA
    (m1, cached), (m2, nova) = bedrock.requests
    assert (m1, m2) == (HAIKU, NOVA)
    assert {p.get("ttl") for p in _cache_points(cached["system"])} == {"1h"}
    # Amazon Nova caches too, but only for 5 minutes and never the tool definitions.
    assert _cache_points(nova["system"]) and all(
        p.get("ttl") in (None, "5m") for p in _cache_points(nova["system"])
    )
    assert not _cache_points(nova["toolConfig"]["tools"])
    assert SKILL_BODY in nova["system"][0]["text"]


async def test_a_fallback_without_caching_runs_uncached(env: Env, bedrock: FakeBedrock) -> None:
    llama = "eu.meta.llama3-2-3b-instruct-v1:0"
    await _put(env, {**TRIAGE, "fallbacks": [llama]})
    bedrock.fail = {HAIKU}
    bedrock.answers = [ANSWER]
    r = await _run(env)
    assert r.status_code == 200, r.text
    (_, cached), (m2, plain) = bedrock.requests
    assert m2 == llama
    assert _cache_points(cached["system"])
    assert not _cache_points(plain["system"])
    assert not _cache_points((plain.get("toolConfig") or {}).get("tools") or [])
    assert not any(_cache_points(m["content"]) for m in plain["messages"])
    assert SKILL_BODY in plain["system"][0]["text"]  # still preloaded, just not cached


async def test_without_prompt_cache_no_cache_point_is_sent(env: Env, bedrock: FakeBedrock) -> None:
    await _put(env, {**TRIAGE, "prompt_cache": "off"})
    bedrock.answers = [ANSWER]
    assert (await _run(env)).status_code == 200
    _, req = bedrock.requests[0]
    assert not _cache_points(req["system"]) and not _cache_points(req["toolConfig"]["tools"])


async def test_a_browser_turn_keeps_the_page_after_the_cache_point(
    env: Env, bedrock: FakeBedrock
) -> None:
    helper = {
        "name": "helper",
        "model": HAIKU,
        "mcp_servers": ["app"],
        "skills": ["procedura"],
        "preload_skills": ["procedura"],
        "prompt_cache": "5m",
        "instructions": "You are an assistant.",
    }
    await _put(env, helper)
    tok = await env.token()
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={
            "text": "Ciao",
            "page": {"path": "/orders/42", "title": "Ordine", "text": "Totale 120"},
        },
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN},
    )
    assert r.status_code == 200, r.text
    _, req = bedrock.requests[0]
    system = req["system"]
    assert [list(b) for b in system] == [["text"], ["cachePoint"], ["text"]]
    assert system[1]["cachePoint"] == {"type": "default", "ttl": "5m"}
    stable, volatile = system[0]["text"], system[2]["text"]
    assert "You are an assistant." in stable and SKILL_BODY in stable
    assert "/orders/42" not in stable and "Totale 120" not in stable
    assert "/orders/42" in volatile and "Totale 120" in volatile
    # A turn has tools, so the conversation so far is cached for the next step (5 minutes).
    assert req["messages"][-1]["content"][-1] == {"cachePoint": {"type": "default", "ttl": "5m"}}
    usage = await env.client.get(
        "/agent/v1/usage",
        params={"groupBy": "agent"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    row = next(x for x in usage.json()["rows"] if x["agent"] == "helper")
    assert row["cacheReadTokens"] == 4000 and row["cacheWriteTokens"] == 1000


def test_cached_tokens_cost_a_tenth_to_read_and_more_to_write() -> None:
    plain = cost_micro_usd(HAIKU, 10_000, 0)
    assert plain == 11_000
    assert cost_micro_usd(HAIKU, 10_000, 0, cache_read_tokens=10_000) == 1_100
    assert cost_micro_usd(HAIKU, 10_000, 0, cache_write_tokens=10_000) == 13_750
    assert (
        cost_micro_usd(HAIKU, 10_000, 0, cache_write_tokens=10_000, cache_write_1h_tokens=10_000)
        == 22_000
    )
    mixed = cost_micro_usd(HAIKU, 10_000, 100, cache_read_tokens=6_000, cache_write_tokens=3_000)
    assert mixed == pytest.approx(1_000 * 1.1 + 6_000 * 0.11 + 3_000 * 1.375 + 100 * 5.5, abs=1)


def test_prompt_cache_and_preload_in_definitions() -> None:
    base = {"name": "a", "instructions": "x", "model": "function:x"}
    assert AgentDef.model_validate({**base, "prompt_cache": False}).prompt_cache == "off"
    assert AgentDef.model_validate({**base, "prompt_cache": "1h"}).prompt_cache == "1h"
    with pytest.raises(ValueError, match="prompt_cache"):
        AgentDef.model_validate({**base, "prompt_cache": "2h"})
    with pytest.raises(ValueError, match="preload_skills"):
        AgentDef.model_validate({**base, "skills": ["a"], "preload_skills": ["b"]})
    # An agent that does not use the options keeps the version it had before they existed.
    plain = AgentDef.model_validate(base)
    assert "prompt_cache" not in plain.content() and "preload_skills" not in plain.content()
    cached = AgentDef.model_validate({**base, "prompt_cache": "1h"})
    assert cached.with_version().version != plain.with_version().version


def test_voice_rule_rewording_keeps_agent_versions() -> None:
    # Versions computed before the default voice rule was reworded (literal hashes).
    base = {"name": "helper", "instructions": "Help with books.", "model": "function:x"}
    assert AgentDef.model_validate(base).with_version().version == "fb266d5fe85b"
    own = {**base, "voice": {"prompt_rule": "Speak briefly."}}
    assert AgentDef.model_validate(own).with_version().version == "f5e72e5f835f"
    assert "account numbers" in AgentDef.model_validate(base).voice.prompt_rule
