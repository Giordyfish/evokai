"""Isolated code execution: the ``code_run`` tool, its grant, guard, usage, traces and AgentCore."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from typing import Any

import pytest
import sqlalchemy as sa

from evokai.core.config import Settings
from evokai.db import models as m
from evokai.sandbox.interpreter import AgentCoreInterpreter, Execution, OutputFile, eu_region
from evokai.sandbox.tool import CodeService, cost_micro_usd, parse_args
from tests.conftest import Env, Tool

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


@dataclass
class FakeInterpreter:
    """Records calls and answers with a scripted execution."""

    kind: str = "agentcore"
    region: str = "eu-north-1"
    identifier: str = "aws.codeinterpreter.v1"
    result: Execution = field(default_factory=lambda: Execution(stdout="42\n", exit_code=0))
    calls: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    delay: float = 0.0

    async def run(self, code: str, files: list[tuple[str, str]], *, timeout: float) -> Execution:
        del timeout
        self.calls.append((code, files))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _enable(env: Env, fake: FakeInterpreter, *, grant: bool = True, agent: bool = True):
    env.svc.code = env.svc.engine.code = CodeService(env.svc.settings, fake)
    if grant:
        r = await env.client.patch(
            "/agent/v1/admin/grants",
            json={"changes": [{"kind": "code", "name": "*", "value": True}]},
            headers=_adm(env),
        )
        assert r.status_code == 200, r.text
    if agent:
        cur = (await env.client.get("/agent/v1/admin/agents", headers=_adm(env))).json()["items"]
        helper = next(a for a in cur if a["name"] == "helper")
        helper.pop("version")
        helper["code"] = {"enabled": True}
        r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
        assert r.status_code == 200, r.text


CALL = {"code": "print(6*7)", "files": [{"name": "data.csv", "content": "a,b\n1,2\n"}]}


async def test_code_run_is_off_until_deployment_grant_and_agent_all_say_yes(env: Env) -> None:
    fake = FakeInterpreter()
    tok = await env.token()
    await env.chat(tok["token"], text="ciao")
    assert "code_run" not in env.script.tools_seen  # no sandbox in the deployment

    await _enable(env, fake, grant=False, agent=False)
    await env.chat((await env.token())["token"], text="ciao")
    assert "code_run" not in env.script.tools_seen  # not granted, agent does not ask

    r = await env.client.put(
        "/agent/v1/admin/agents/helper",
        json={
            "name": "helper",
            "model": "function:primary",
            "instructions": "x",
            "code": {"enabled": True},
        },
        headers=_adm(env),
    )
    assert r.status_code == 400 and "code execution is not granted" in r.text

    await _enable(env, fake)
    await env.chat((await env.token())["token"], text="ciao")
    assert "code_run" in env.script.tools_seen


async def test_a_call_runs_returns_output_and_shows_images_as_charts(env: Env) -> None:
    fake = FakeInterpreter(
        result=Execution(
            stdout="mean 2\n",
            exit_code=0,
            files=[
                OutputFile("monthly_trend.png", "image/png", data=PNG),
                OutputFile("summary.csv", "text/csv", text="a,b\n1,2\n"),
            ],
            session_ms=1_500,
            execution_ms=300,
        )
    )
    await _enable(env, fake)
    env.script.add("function:primary", [Tool("code_run", CALL)], ["The mean is 2."])
    events = await env.chat((await env.token())["token"], text="analyse")
    assert fake.calls == [("print(6*7)", [("data.csv", "a,b\n1,2\n")])]
    charts = [e["chart"] for e in events if e["type"] == "chart"]
    assert charts and charts[0]["type"] == "image" and charts[0]["mediaType"] == "image/png"
    assert charts[0]["title"] == "monthly trend" and charts[0]["data"]
    tools = [(e["name"], e["status"]) for e in events if e["type"] == "tool"]
    assert ("code_run", "start") in tools and ("code_run", "done") in tools
    result = str(env.script.seen[-1][1][-1])
    assert "mean 2" in result and "output/summary.csv" in result and "Charts shown" in result
    # The system prompt explains the tool.
    assert "code_run runs Python 3" in str(env.script.seen[0][1][0])

    # Usage: session time as a record of kind code (cost estimated, never in the UI).
    async with env.svc.db.session() as s:
        rows = (
            (await s.execute(sa.select(m.UsageRecord).where(m.UsageRecord.kind == "code")))
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].latency_ms == 1_500 and rows[0].tool_calls == 1
    assert rows[0].model == "agentcore:code-interpreter" and rows[0].cost_micro_usd > 0

    # Trace: a tool span with the sandbox's metadata.
    traces = (await env.client.get("/agent/v1/admin/traces", headers=_adm(env))).json()["items"]
    t = (
        await env.client.get(f"/agent/v1/admin/traces/{traces[0]['id']}", headers=_adm(env))
    ).json()
    span = next(
        s for s in t["spans"] if s["kind"] == "tool" and s["attrs"].get("tool") == "code_run"
    )
    a = span["attrs"]
    assert a["type"] == "code" and a["sandbox"] == "agentcore" and a["region"] == "eu-north-1"
    assert a["network"] == "none" and a["sessionMs"] == 1_500 and a["images"] == 1
    assert a["filesIn"] == 1 and a["exitCode"] == 0


async def test_secrets_in_the_output_are_removed_and_recorded(env: Env) -> None:
    fake = FakeInterpreter(result=Execution(stdout=f"key={AWS_KEY}\n", exit_code=0))
    await _enable(env, fake)
    env.script.add("function:primary", [Tool("code_run", {"code": "print(1)"})], ["done"])
    await env.chat((await env.token())["token"], text="go")
    result = str(env.script.seen[-1][1][-1])
    assert AWS_KEY not in result and "[SECRET]" in result
    async with env.svc.db.session() as s:
        ev = (await s.execute(sa.select(m.SecurityEvent))).scalars().all()
    assert any(e.control == "output.secrets" and e.tool == "code_run" for e in ev)


async def test_errors_timeouts_and_bad_arguments(env: Env) -> None:
    fake = FakeInterpreter(
        result=Execution(stderr="ValueError: boom", exit_code=1, error=True, session_ms=900)
    )
    await _enable(env, fake)
    env.script.add(
        "function:primary",
        [Tool("code_run", {"code": "x", "files": [{"name": "../etc", "content": "x"}]}, "c1")],
        [Tool("code_run", {"code": "raise ValueError('boom')"}, "c2")],
        ["sorry"],
    )
    events = await env.chat((await env.token())["token"], text="go")
    assert len(fake.calls) == 1  # the bad file name never reached the sandbox
    assert ("code_run", "error") in [
        (e["name"], e["status"]) for e in events if e["type"] == "tool"
    ]
    assert "ValueError: boom" in str(env.script.seen[-1][1][-1])

    assert parse_args({"code": ""}) == "Give the Python code to run."
    assert isinstance(parse_args({"code": "x" * 20_001}), str)
    assert isinstance(
        parse_args({"code": "x", "files": [{"name": "a.csv", "content": "1"}] * 2}), str
    )
    assert parse_args({"code": "x", "files": [{"name": "a.csv", "content": "1"}]}) == (
        "x",
        [("a.csv", "1")],
    )


async def test_a_server_run_gets_the_text_but_not_the_images(env: Env) -> None:
    fake = FakeInterpreter(
        result=Execution(
            stdout="total 7", exit_code=0, files=[OutputFile("c.png", "image/png", data=PNG)]
        )
    )
    await _enable(env, fake, agent=False)
    body = {
        "name": "analyst",
        "model": "function:primary",
        "channels": ["server"],
        "instructions": "Analyse.",
        "code": {"enabled": True},
    }
    r = await env.client.put("/agent/v1/admin/agents/analyst", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text
    env.script.add("function:primary", [Tool("code_run", {"code": "print(7)"})], ["Total is 7."])
    r = await env.client.post(
        "/agent/v1/agents/analyst/run",
        json={"input": "sum it"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["output"] == "Total is 7."
    sent = str(env.script.seen[-1][1][-1])
    assert "total 7" in sent and "not delivered in server runs" in sent


async def test_the_grant_view_lists_code_and_it_is_off_by_default(env: Env) -> None:
    view = (await env.client.get("/agent/v1/admin/grants", headers=_adm(env))).json()
    item = next(g for g in view["groups"] if g["id"] == "code")["items"][0]
    assert item["value"] is False and item["default"] is False and item["enabled"] is False


# The AgentCore client, with a fake boto3 client.


class FakeAgentCore:
    def __init__(self, *, block: threading.Event | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.block = block

    def start_code_interpreter_session(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("start", kw))
        return {"sessionId": "s1"}

    def stop_code_interpreter_session(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(("stop", kw))
        if self.block is not None:
            self.block.set()
        return {}

    def invoke_code_interpreter(self, **kw: Any) -> dict[str, Any]:
        name = kw["name"]
        self.calls.append((name, kw["arguments"]))
        if name == "executeCode" and self.block is not None:
            self.block.wait(5)
            return {"stream": []}
        results: dict[str, Any] = {
            "writeFiles": {"content": [{"type": "text", "text": "ok"}]},
            "executeCode": {
                "content": [{"type": "text", "text": "3"}],
                "structuredContent": {
                    "stdout": "3\n",
                    "stderr": "",
                    "exitCode": 0,
                    "executionTime": 0.25,
                },
                "isError": False,
            },
            "listFiles": {
                "content": [
                    {
                        "type": "resource_link",
                        "uri": "file:///output/a.png",
                        "name": "a.png",
                        "mimeType": "image/png",
                    },
                    {
                        "type": "resource_link",
                        "uri": "file:///output/t.csv",
                        "name": "t.csv",
                        "mimeType": "text/csv",
                    },
                    {"type": "resource_link", "uri": "file:///output/m.bin", "name": "m.bin"},
                ]
            },
            "readFiles": {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///output/a.png",
                            "mimeType": "image/png",
                            "blob": PNG,
                        },
                    },
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "file:///output/t.csv",
                            "mimeType": "text/csv",
                            "text": "x\n1\n",
                        },
                    },
                ]
            },
        }
        return {"stream": [{"result": results[name]}]}


async def test_agentcore_runs_one_session_per_call_and_reads_the_outputs() -> None:
    fake = FakeAgentCore()
    ci = AgentCoreInterpreter("eu-north-1", client_factory=lambda: fake)
    ex = await ci.run("print(3)", [("d.csv", "a\n1\n")], timeout=10)
    names = [c[0] for c in fake.calls]
    assert names == ["start", "writeFiles", "executeCode", "listFiles", "readFiles", "stop"]
    assert fake.calls[0][1]["codeInterpreterIdentifier"] == "aws.codeinterpreter.v1"
    assert fake.calls[0][1]["sessionTimeoutSeconds"] == 40
    assert fake.calls[1][1] == {"content": [{"path": "input/d.csv", "text": "a\n1\n"}]}
    assert fake.calls[4][1] == {"paths": ["output/a.png", "output/t.csv"]}
    assert ex.stdout == "3\n" and ex.exit_code == 0 and ex.execution_ms == 250
    assert [f.name for f in ex.files] == ["a.png", "t.csv"] and ex.skipped == ["m.bin"]
    assert ex.files[0].data == PNG and ex.files[1].text == "x\n1\n"


async def test_agentcore_stops_the_session_at_the_timeout() -> None:
    fake = FakeAgentCore(block=threading.Event())
    ci = AgentCoreInterpreter("eu-north-1", client_factory=lambda: fake)
    ex = await ci.run("while True: pass", [], timeout=0.2)
    assert ex.timed_out and ex.error
    assert ("stop", {"codeInterpreterIdentifier": "aws.codeinterpreter.v1", "sessionId": "s1"}) in (
        fake.calls
    )
    assert sum(1 for c in fake.calls if c[0] == "stop") == 1


def test_only_eu_regions_and_off_by_default() -> None:
    assert eu_region("eu-north-1") and not eu_region("us-east-1") and not eu_region(None)
    with pytest.raises(ValueError, match="EU"):
        AgentCoreInterpreter("us-east-1")
    off = CodeService(Settings())
    assert not off.available and off.problem == "EVOKAI_CODE_ENABLED is off"
    us = CodeService(Settings(code_enabled=True, code_region="us-west-2"))
    assert not us.available and "outside the EU" in (us.problem or "")
    on = CodeService(Settings(code_enabled=True))
    assert on.available and on.status()["region"] == "eu-north-1"
    assert on.status()["network"] == "none"
    assert cost_micro_usd(1, 0.00003) == 30 and cost_micro_usd(2_001, 0.00003) == 90


@pytest.mark.skipif(
    os.environ.get("EVOKAI_LIVE_CODE") != "1", reason="live AgentCore call: EVOKAI_LIVE_CODE=1"
)
async def test_live_agentcore_in_eu_north_1() -> None:  # pragma: no cover
    ci = AgentCoreInterpreter(os.environ.get("EVOKAI_CODE_REGION", "eu-north-1"))
    ex = await ci.run(
        "import pandas as pd\nprint(pd.read_csv('input/d.csv')['a'].sum())",
        [("d.csv", "a\n1\n2\n")],
        timeout=60,
    )
    assert ex.stdout.strip() == "3" and ex.exit_code == 0
