"""The capability catalog: Code Mode, compaction, subagents and every pydantic-ai-harness one."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from evokai import capabilities as caps
from evokai.capabilities import harness
from evokai.capabilities.catalog import ENTRIES, HOST_LOCAL, HOST_REMOTE, HOST_WORKSPACE
from evokai.db import models as m
from tests.conftest import Env, Tool, types


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _grant(env: Env, *names: str) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "capability", "name": n, "value": True} for n in names]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text


async def _helper(env: Env, **update: Any) -> dict[str, Any]:
    cur = (await env.client.get("/agent/v1/admin/agents", headers=_adm(env))).json()["items"]
    helper = next(a for a in cur if a["name"] == "helper")
    helper.pop("version")
    helper.update(update)
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
    assert r.status_code == 200, r.text
    return helper


async def _enable(env: Env, name: str, config: dict[str, Any] | None = None, **update: Any) -> None:
    env.svc.settings.capabilities = ["*"]
    await _grant(env, name)
    await _helper(env, capabilities={name: {"enabled": True, "config": config or {}}}, **update)


def _returns(messages: list[Any]) -> list[str]:
    return [
        str(p.content)
        for x in messages
        if isinstance(x, ModelRequest)
        for p in x.parts
        if isinstance(p, ToolReturnPart)
    ]


async def _trace(env: Env, turn_id: str) -> dict[str, Any]:
    r = await env.client.get(f"/agent/v1/admin/traces/{turn_id}", headers=_adm(env))
    assert r.status_code == 200, r.text
    return r.json()


# the three switches


async def test_a_capability_is_off_until_deployment_grant_and_agent_all_say_yes(env: Env) -> None:
    program = "r = orders_month(period='2026-10')\nr"
    tok = await env.token()
    # The agent asks, but the application is not granted it: saving is refused.
    cur = (await env.client.get("/agent/v1/admin/agents", headers=_adm(env))).json()["items"]
    helper = next(a for a in cur if a["name"] == "helper")
    helper.pop("version")
    helper["capabilities"] = {"code_mode": {"enabled": True}}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
    assert r.status_code == 400 and "capability code_mode is not granted" in r.text
    # Granted and asked for, but the deployment does not offer it: no run_code.
    await _grant(env, "code_mode")
    await _helper(env, capabilities={"code_mode": {"enabled": True}})
    await env.chat(tok["token"], text="Ciao")
    assert "run_code" not in env.script.tools_seen
    # All three: run_code is offered.
    env.svc.settings.capabilities = ["code_mode"]
    env.script.add("function:primary", [Tool("run_code", {"code": program})], ["Fatto."])
    await env.chat(tok["token"], text="Ottobre?")
    assert "run_code" in env.script.tools_seen


async def test_the_grant_view_lists_the_whole_catalog_off_by_default(env: Env) -> None:
    view = (await env.client.get("/agent/v1/admin/grants", headers=_adm(env))).json()
    group = next(g for g in view["groups"] if g["id"] == "capabilities")
    ids = [x["name"] for x in group["items"]]
    assert ids == [e.id for e in ENTRIES]
    assert all(x["value"] is False and x["default"] is False for x in group["items"])
    code = next(x for x in group["items"] if x["name"] == "code_mode")
    assert code["installed"] is True and code["offered"] is False
    exa = next(x for x in group["items"] if x["name"] == "exa_search")
    assert "exa.api_key" in exa["missingCredentials"] or not exa["installed"]
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "capability", "name": "nope", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 400


# Code Mode


async def test_code_mode_runs_read_only_tools_as_the_user_inside_one_program(env: Env) -> None:
    await _enable(env, "code_mode")
    program = (
        "a = orders_month(period='2026-09')\n"
        "b = orders_month(period='2026-10')\n"
        "[a['orders'], b['orders']]"
    )
    env.script.add("function:primary", [Tool("run_code", {"code": program})], ["Due mesi."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Settembre e ottobre?")
    assert ev[-1]["reason"] == "end_turn"
    # Both calls went to the MCP server as the user, from inside the program.
    assert [c for c in env.mcp.calls if c[0] == "orders_month"] == [
        ("orders_month", {"period": "2026-09"}),
        ("orders_month", {"period": "2026-10"}),
    ]
    assert all(a == "Bearer user-token" for a in env.mcp.auth)
    # The read-only tools are still offered as normal calls; the one that changes data is too,
    # never inside the program.
    _, second = env.script.seen[-1]
    assert "[24,24]" in _returns(second)[-1].replace(" ", "")
    assert {"run_code", "orders_month", "order_refund"} <= set(env.script.tools_seen)
    tools = [e for e in ev if e["type"] == "tool"]
    assert {"name": "run_code", "status": "start"}.items() <= tools[0].items()
    # In the trace the calls are children of run_code.
    t = await _trace(env, ev[0]["turnId"])
    spans = {s["id"]: s for s in t["spans"]}
    run_code = next(s for s in t["spans"] if s["attrs"].get("tool") == "run_code")
    inner = [s for s in t["spans"] if s["attrs"].get("tool") == "orders_month"]
    assert len(inner) == 2 and all(s["parent"] == run_code["id"] for s in inner)
    assert spans[run_code["parent"]]["kind"] == "turn"


async def test_a_program_cannot_call_a_tool_that_changes_data(env: Env) -> None:
    await _enable(env, "code_mode")
    program = "order_refund(period='2026-10')"
    env.script.add("function:primary", [Tool("run_code", {"code": program})], ["Non posso."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa")
    assert [c for c in env.mcp.calls if c[0] == "order_refund"] == []
    assert "confirm" not in types(ev)
    _, second = env.script.seen[-1]
    assert "order_refund" in _returns(second)[-1]


# compaction


async def test_compaction_folds_older_messages_into_a_stored_summary(env: Env) -> None:
    await _enable(env, "compaction", {"keep_tokens": 500})
    env.svc.settings.history_messages = 4
    tok = await env.token()
    for i in range(3):
        env.script.add("function:primary", [f"Risposta {i}."])
        await env.chat(tok["token"], text=f"Domanda {i}")
    env.script.add("function:primary", ["## Goal\nSapere le risposte."], ["Ultima."])
    ev = await env.chat(tok["token"], text="Domanda finale")
    assert ev[-1]["reason"] == "end_turn"
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(m.ConversationSummary))).scalars().all()
        kinds = (await s.execute(sa.select(m.UsageRecord.kind))).scalars().all()
    assert len(rows) == 1 and "Sapere le risposte" in rows[0].summary
    assert "summary" in kinds
    # The answering call starts from the summary, never at the provider: it is in the request.
    _, last = env.script.seen[-1]
    texts = [str(p.content) for x in last for p in x.parts if isinstance(p, UserPromptPart)]
    assert any("Summary of the earlier part" in x for x in texts)


async def test_external_storage_is_never_compacted(env: Env) -> None:
    await _enable(env, "compaction", {"keep_tokens": 500})
    env.svc.settings.history_messages = 2
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "storage", "name": "external", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    tok = await env.token(storageMode="external")
    history = [
        {"role": "user", "content": [{"text": f"Domanda {i}"}], "text": f"Domanda {i}"}
        if i % 2 == 0
        else {"role": "assistant", "content": [{"text": f"Risposta {i}"}], "text": f"Risposta {i}"}
        for i in range(8)
    ]
    env.script.add("function:primary", ["Ok."])
    ev = await env.chat(tok["token"], text="Ancora", history=history)
    assert ev[-1]["reason"] == "end_turn"
    async with env.svc.db.session() as s:
        assert (await s.execute(sa.select(m.ConversationSummary))).scalars().all() == []


async def test_a_too_long_request_is_compacted_once_and_sent_again(env: Env) -> None:
    await _enable(env, "compaction", {"keep_tokens": 500})
    tok = await env.token()
    for i in range(3):
        env.script.add("function:primary", [f"Risposta {i}."])
        await env.chat(tok["token"], text=f"Domanda {i}")
    too_long = ModelHTTPError(400, "primary", {"message": "Input is too long for requested model"})
    env.script.add("function:primary", too_long, ["## Goal\nRiassunto."], ["Eccomi."])
    ev = await env.chat(tok["token"], text="Domanda finale")
    assert ev[-1]["reason"] == "end_turn"
    assert "".join(e["delta"] for e in ev if e["type"] == "text") == "Eccomi."


# subagents

RESEARCHER = {
    "name": "researcher",
    "title": "Researcher",
    "model": "function:child",
    "mcp_servers": ["app"],
    "instructions": "You research the months.",
}


async def test_a_subagent_answers_a_task_as_the_same_person_with_read_only_tools(
    env: Env,
) -> None:
    r = await env.client.put(
        "/agent/v1/admin/agents/researcher", json=RESEARCHER, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    await _enable(env, "subagents", subagents=["researcher"])
    env.script.add(
        "function:primary",
        [Tool("delegate_task", {"agent_name": "researcher", "task": "Ottobre?"})],
        ["Ottobre ha 24 ordini."],
    )
    env.script.add(
        "function:child", [Tool("orders_month", {"period": "2026-10"}, "c1")], ["24 ordini."]
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Chiedi al ricercatore")
    assert ev[-1]["reason"] == "end_turn"
    assert ("orders_month", {"period": "2026-10"}) in env.mcp.calls
    assert all(a == "Bearer user-token" for a in env.mcp.auth)
    child_calls = [x for name, x in env.script.seen if name == "function:child"]
    assert child_calls and "Ottobre?" in str(child_calls[0])
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(m.UsageRecord))).scalars().all()
    assert {(x.agent_name, x.kind) for x in rows} >= {("helper", "llm"), ("researcher", "subagent")}
    t = await _trace(env, ev[0]["turnId"])
    delegate = next(s for s in t["spans"] if s["attrs"].get("tool") == "delegate_task")
    inner = next(s for s in t["spans"] if s["attrs"].get("tool") == "orders_month")
    assert inner["parent"] == delegate["id"]


async def test_subagent_calls_are_capped_per_turn(env: Env) -> None:
    r = await env.client.put(
        "/agent/v1/admin/agents/researcher", json=RESEARCHER, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    env.svc.settings.subagent_calls = 1
    await _enable(env, "subagents", subagents=["researcher"], max_tool_calls_per_turn=6)
    ask = {"agent_name": "researcher", "task": "Ottobre?"}
    env.script.add(
        "function:primary",
        [Tool("delegate_task", ask, "d1")],
        [Tool("delegate_task", ask, "d2")],
        ["Fine."],
    )
    env.script.add("function:child", ["24."], ["25."])
    tok = await env.token()
    await env.chat(tok["token"], text="Due volte")
    assert len([1 for name, _ in env.script.seen if name == "function:child"]) == 1
    _, last = env.script.seen[-1]
    assert "already called 1 subagents" in _returns(last)[-1]


# the catalog


def test_every_harness_capability_is_in_the_catalog() -> None:
    import pydantic_ai_harness as h
    from pydantic_ai.capabilities import AbstractCapability

    listed = {e.cls for e in ENTRIES}
    exported = set()
    for name in h.__all__:
        try:
            obj = getattr(h, name)
        except ImportError:
            exported.add(name)  # its extra is missing here: it must still be listed
            continue
        if isinstance(obj, type) and issubclass(obj, AbstractCapability):
            exported.add(name)
    backends = {n for n in exported if n.endswith("Backend") or n == "BubblewrappedWorkspace"}
    helpers = {"BenchmarkInstructions"}
    listed |= {"CodeMode", "SubAgents"}  # Evokai's own subclasses: code_mode, subagents
    assert exported - backends - helpers - listed == set()


@pytest.mark.parametrize("entry", ENTRIES, ids=[e.id for e in ENTRIES])
def test_each_capability_is_constructed_with_its_settings_or_refused(entry: Any) -> None:
    config = dict(entry.sample)
    if entry.host == HOST_WORKSPACE:
        config["sandbox"] = "e2b_sandbox"

    def placeholder(_config: dict[str, Any]) -> Any:
        return object()

    factories: dict[str, Any] = {
        **harness.factories(stub_context()),
        "code_mode": placeholder,
        "compaction": placeholder,
        "subagents": placeholder,
    }

    def model_of(_name: str) -> Any:
        return TestModel()

    if entry.host == HOST_LOCAL:
        with pytest.raises(caps.CapabilityError, match="server's own machine"):
            caps.build(entry.id, config, model_of=model_of, factories=factories)
        return
    if not caps.installed(entry.id):
        pytest.skip(f"{entry.id}: its extra ({entry.extra}) is not installed here")
    enabled = {entry.id, "e2b_sandbox"}
    try:
        built = caps.build(
            entry.id, config, model_of=model_of, factories=factories, enabled=enabled
        )
    except caps.CapabilityError as e:
        # Some need objects Evokai still has to provide (the follow-up wave says which), or
        # refuse a run like this one (no person's memory, no conversation kept by Evokai).
        from evokai.capabilities.credentials import PERSONAL

        assert entry.wiring or entry.id in harness.BUILDERS or entry.id in PERSONAL, (
            f"{entry.id} cannot be built and says nothing: {e}"
        )
        return
    assert built is not None


def stub_context() -> Any:
    """A server run's context for Evokai's builders (no database, no person to ask).

    Returns:
        The context.
    """
    from types import SimpleNamespace

    rs = SimpleNamespace(
        guard=SimpleNamespace(max_calls=lambda d: d),
        settings=SimpleNamespace(tool_result_chars=8000),
        emit=None,
    )
    scope = SimpleNamespace(
        session_id=None,
        grants=SimpleNamespace(client=lambda _n: False),
        storage=None,
        app=SimpleNamespace(id="app", storage_mode="external", defaults={}),
        who=SimpleNamespace(subject="u1", application_id="app"),
    )
    from evokai.capabilities.credentials import PROVIDERS

    # Placeholder keys: each provider's client is built with them, nothing is called.
    secrets = {p: {f.name: f"test-{f.name}" for f in fields} for p, fields in PROVIDERS.items()}
    secrets["opensandbox"]["domain"] = "sandbox.example"
    return harness.Context(
        engine=None,
        agent=SimpleNamespace(max_tool_calls_per_turn=4, name="helper"),
        rs=rs,  # ty: ignore[invalid-argument-type]
        scope=scope,  # ty: ignore[invalid-argument-type]
        secrets=secrets,
    )


def test_workspace_capabilities_need_a_remote_sandbox() -> None:
    shell = caps.get("shell")
    assert shell.host == HOST_WORKSPACE
    with pytest.raises(caps.CapabilityError, match="remote sandbox"):
        caps.check_use("shell", {})
    with pytest.raises(caps.CapabilityError, match="remote sandbox"):
        caps.check_use("shell", {"sandbox": "docker_container"})
    caps.check_use("shell", {"sandbox": "modal_sandbox"})
    assert caps.get("modal_sandbox").host == HOST_REMOTE
    with pytest.raises(caps.CapabilityError, match="another machine"):
        caps.build(
            "ssh_workspace",
            {"destination": "root@localhost"},
            model_of=lambda _n: None,
        )


async def test_a_capability_s_own_model_calls_are_recorded_without_provider_state() -> None:
    from pydantic_ai.models import ModelRequestParameters

    from evokai.engine.models import RecordedModel

    seen: list[Any] = []
    settings_seen: list[Any] = []

    class Spy(TestModel):
        async def request(
            self, messages: Any, model_settings: Any, model_request_parameters: Any
        ) -> Any:
            settings_seen.append(dict(model_settings or {}))
            return await super().request(messages, model_settings, model_request_parameters)

    async def record(name: str, _response: Any) -> None:  # ruff: ignore[unused-async]
        seen.append(name)

    model = RecordedModel(Spy(custom_output_text="ok"), "function:judge", record)
    await model.request(
        [ModelRequest(parts=[UserPromptPart("Is it safe?")])],
        cast(Any, {"openai_store": True, "openai_previous_response_id": "auto"}),
        ModelRequestParameters(),
    )
    assert seen == ["function:judge"]
    assert settings_seen[0]["openai_store"] is False
    assert "openai_previous_response_id" not in settings_seen[0]


LISTING = """
import json, resource, sys, time
import evokai.main  # the service as it starts (the engine loads Code Mode and subagents itself)

before = set(sys.modules)
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t0 = time.perf_counter()
from evokai.agents.definitions import AgentDef
from evokai.api.grants_routes import capability_items
from evokai.capabilities import all_ids, check_use, installed, offered
from evokai.grants import CAPABILITY, Grants, check_value

items = capability_items(Grants(), [], ["*"])
AgentDef.model_validate({
    "name": "a", "model": "function:x", "instructions": "x",
    "capabilities": {
        "code_mode": {"enabled": True, "config": {"tool_calls": 5}},
        "exa_search": {"enabled": True},
        "shell": {"enabled": True, "config": {"sandbox": "e2b_sandbox"}},
    },
})
for i in all_ids():
    check_value(CAPABILITY, i, True)
    installed(i), offered(i, ["*"])
new = sorted(set(sys.modules) - before)
print(json.dumps({
    "items": len(items),
    "new": [m for m in new if m.split(".")[0] not in ("evokai", "packaging")],
    "seconds": round(time.perf_counter() - t0, 2),
    "rssGrowthMb": round((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - rss) / 1024),
}))
"""


def test_listing_the_catalog_imports_no_capability() -> None:
    import json
    import subprocess  # ruff: ignore[suspicious-subprocess-import]
    import sys

    out = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", LISTING], capture_output=True, text=True, timeout=120, check=True
    )
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["items"] == len(ENTRIES)
    # Listing, granting and checking the catalog import nothing beyond Evokai's own modules.
    assert got["new"] == []
    assert got["seconds"] < 2 and got["rssGrowthMb"] < 30, got


def test_a_local_workspace_is_refused_at_run_start() -> None:
    from pydantic_ai.workspaces import Workspace
    from pydantic_ai.workspaces.local import LocalWorkspaceBackend

    from evokai.engine.workspace import is_local

    assert is_local(Workspace(LocalWorkspaceBackend(".")))


def test_the_lite_image_says_why_a_capability_is_missing(monkeypatch: Any) -> None:
    import tomllib
    from pathlib import Path

    from evokai.capabilities import registry
    from evokai.capabilities.catalog import LITE_LEFT_OUT

    monkeypatch.setattr(registry, "installed", lambda _c: False)
    assert caps.not_installed_reason("pixeltable", "lite") == "lite"
    assert caps.not_installed_reason("pixeltable", "full") == "missing"
    assert caps.not_installed_reason("exa_search", "lite") == "missing"
    monkeypatch.setattr(registry, "installed", lambda _c: True)
    assert caps.not_installed_reason("pixeltable", "lite") is None
    # The lite extra is the full one without exactly the heavy extras.
    extras = tomllib.loads(Path("pyproject.toml").read_text())["project"]["optional-dependencies"]

    def of(name: str) -> set[str]:
        (line,) = extras[name]
        return set(line.split("[", 1)[1].split("]", 1)[0].split(","))

    assert of("harness") - of("harness-lite") == LITE_LEFT_OUT
    assert of("harness-lite") <= of("harness")


async def test_the_grant_view_names_the_lite_image(env: Env, monkeypatch: Any) -> None:
    from evokai.capabilities import registry

    real = registry.installed

    def fake(c: str) -> bool:
        return c != "pixeltable" and real(c)

    monkeypatch.setattr(registry, "installed", fake)
    monkeypatch.setattr(caps, "installed", fake)
    env.svc.settings.image_flavour = "lite"
    view = (await env.client.get("/agent/v1/admin/grants", headers=_adm(env))).json()
    group = next(g for g in view["groups"] if g["id"] == "capabilities")
    item = next(x for x in group["items"] if x["name"] == "pixeltable")
    assert item["installed"] is False and item["notInstalled"] == "lite"


def test_the_console_says_every_capability_in_english_and_italian() -> None:
    # The service sends the catalog's English; the console's dictionaries hold each text in both
    # languages, keyed by the id, so a new capability without its Italian shows up here.
    root = Path(__file__).resolve().parents[1] / "admin" / "src" / "i18n"
    for lang in ("en", "it"):
        text = (root / f"{lang}.ts").read_text(encoding="utf-8")
        missing = [e.id for e in ENTRIES if f'"cap.{e.id}":' not in text]
        assert missing == [], f"{lang}.ts has no description for {missing}"
        missing = [e.id for e in ENTRIES if e.wiring and f'"capw.{e.id}":' not in text]
        assert missing == [], f"{lang}.ts has no wiring text for {missing}"
