"""Harness capabilities wired to Evokai: the person answers, the guard decides, the trace shows."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from pydantic_ai.messages import ModelRequest, SystemPromptPart, ToolReturnPart
from pydantic_ai.toolsets import FunctionToolset

from evokai.capabilities import harness
from evokai.db import models as m
from tests.conftest import Env, Tool
from tests.test_capabilities import _adm, _enable, _trace


def _returns(messages: list[Any]) -> list[str]:
    return [
        str(p.content)
        for x in messages
        if isinstance(x, ModelRequest)
        for p in x.parts
        if isinstance(p, ToolReturnPart)
    ]


def _system_parts(messages: list[Any]) -> list[str]:
    return [
        str(p.content)
        for x in messages
        if isinstance(x, ModelRequest)
        for p in x.parts
        if isinstance(p, SystemPromptPart)
    ]


async def _events(env: Env, control: str) -> list[m.SecurityEvent]:
    async with env.svc.store.db.session() as s:
        q = sa.select(m.SecurityEvent).where(m.SecurityEvent.control == control)
        return list((await s.execute(q)).scalars().all())


# ask_user


async def test_ask_user_ends_the_turn_on_the_question_and_the_choice_resumes_it(
    env: Env,
) -> None:
    await _enable(env, "ask_user")
    question = {
        "header": "Month",
        "question": "Which month should I look at?",
        "options": [{"label": "September"}, {"label": "October"}],
    }
    env.script.add(
        "function:primary",
        [Tool("ask_user_question", {"questions": [question]})],
        ["October it is."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Show me the orders")
    ask = next(e for e in ev if e["type"] == "ask")
    assert ask["question"] == "Which month should I look at?"
    assert ask["options"] == ["September", "October"]
    assert ev[-1]["reason"] == "ask"
    # The person's choice is the next message: the model reads it as the answer.
    ev2 = await env.chat(tok["token"], text="October")
    assert ev2[-1]["reason"] == "end_turn"
    last = env.script.seen[-1][1]
    assert "Question shown" in " ".join(_returns(last))
    assert 'Answer to your question "Which month should I look at?": October' in str(last)
    t = await _trace(env, ev[0]["turnId"])
    span = next(s for s in t["spans"] if s["attrs"].get("tool") == "ask_user_question")
    assert span["attrs"]["source"] == "capability"


async def test_ask_user_shows_every_question_one_at_a_time(env: Env) -> None:
    import sqlalchemy as sa

    from evokai.db import models as m

    await _enable(env, "ask_user")
    questions = [
        {
            "header": "Month",
            "question": "Which month?",
            "options": [{"label": "September"}, {"label": "October"}],
        },
        {
            "header": "Format",
            "question": "Table or chart?",
            "options": [{"label": "Table"}, {"label": "Chart"}],
        },
        {
            "header": "Scope",
            "question": "All shelves?",
            "options": [{"label": "Yes"}, {"label": "Only poetry"}],
        },
    ]
    env.script.add(
        "function:primary",
        [Tool("ask_user_question", {"questions": questions})],
        ["A table of October for poetry."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Show me the orders")
    assert [e["question"] for e in ev if e["type"] == "ask"] == ["Which month?"]
    assert ev[-1]["reason"] == "ask"
    calls = len(env.script.seen)
    # Each answer shows the next question without a model call.
    ev2 = await env.chat(tok["token"], text="October")
    assert [e["question"] for e in ev2 if e["type"] == "ask"] == ["Table or chart?"]
    assert ev2[-1]["reason"] == "ask" and len(env.script.seen) == calls
    ev3 = await env.chat(tok["token"], text="Table")
    assert [e["question"] for e in ev3 if e["type"] == "ask"] == ["All shelves?"]
    assert len(env.script.seen) == calls
    # The last answer reaches the model with every question and its answer.
    ev4 = await env.chat(tok["token"], text="Only poetry")
    assert ev4[-1]["reason"] == "end_turn" and len(env.script.seen) == calls + 1
    seen = str(env.script.seen[-1][1])
    assert '"Which month?": October' in seen and '"Table or chart?": Table' in seen
    assert '"All shelves?": Only poetry' in seen
    assert ev4[-1]["rewritable"] is True  # nothing was written: the last answer may be edited
    # A rewritten last answer reaches the model again, with every other answer.
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "Yes", "rewrite": {}},
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": "http://app.test"},
    )
    assert r.status_code == 200, r.text
    seen = str(env.script.seen[-1][1])
    assert '"All shelves?": Yes' in seen and '"Which month?": October' in seen
    # The next message ends the questions.
    env.script.add("function:primary", ["Thanks."])
    await env.chat(tok["token"], text="Thanks")
    async with env.svc.store.db.session() as s:
        q = sa.select(m.CapabilityRecord).where(m.CapabilityRecord.capability == "ask_user")
        assert (await s.execute(q)).scalars().all() == []


async def test_a_rewritten_answer_shows_the_next_question_again(env: Env) -> None:
    await _enable(env, "ask_user")
    questions = [
        {
            "header": "Month",
            "question": "Which month?",
            "options": [{"label": "September"}, {"label": "October"}],
        },
        {
            "header": "Format",
            "question": "Table or chart?",
            "options": [{"label": "Table"}, {"label": "Chart"}],
        },
    ]
    env.script.add(
        "function:primary",
        [Tool("ask_user_question", {"questions": questions})],
        ["Done."],
    )
    tok = await env.token()
    await env.chat(tok["token"], text="Show me the orders")
    ev = await env.chat(tok["token"], text="October")
    assert [e["question"] for e in ev if e["type"] == "ask"] == ["Table or chart?"]
    assert ev[-1]["rewritable"] is True
    r = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "September", "rewrite": {}},
        headers={"Authorization": f"Bearer {tok['token']}", "Origin": "http://app.test"},
    )
    assert r.status_code == 200, r.text
    from tests.conftest import parse_sse

    ev2 = parse_sse(r.text)
    assert [e["question"] for e in ev2 if e["type"] == "ask"] == ["Table or chart?"]
    await env.chat(tok["token"], text="Chart")
    seen = str(env.script.seen[-1][1])
    assert '"Which month?": September' in seen and '"Table or chart?": Chart' in seen


async def test_ask_user_in_a_server_run_tells_the_model_to_choose(env: Env) -> None:
    from evokai.capabilities.ask import NO_ONE_TO_ASK, EvokaiAskToolset

    ts = EvokaiAskToolset(capability_id="ask_user", can_ask=False)
    ctx = SimpleNamespace(deps=SimpleNamespace(), tool_call_id="c1")
    out = await ts.ask_user_question(ctx, [])  # ty: ignore[invalid-argument-type]
    assert out == NO_ONE_TO_ASK
    del env


# budgets, loops, reminders


async def test_tool_budgets_refuse_over_budget_calls_as_a_guard_event(env: Env) -> None:
    await _enable(env, "tool_budgets", {"per_tool": {"orders_month": 1}})
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-09"}, "c1")],
        [Tool("orders_month", {"period": "2026-10"}, "c2")],
        ["Done."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Two months")
    assert ev[-1]["reason"] == "end_turn"
    assert [c[0] for c in env.mcp.calls] == ["orders_month"]
    assert "no more are allowed" in _returns(env.script.seen[-1][1])[-1]
    rows = await _events(env, "turn.budget")
    assert rows and rows[0].rule == "tool_budget_per_tool"


def test_tool_budgets_never_exceed_the_turn_cap() -> None:
    ctx = harness.Context(
        engine=None,
        agent=SimpleNamespace(max_tool_calls_per_turn=4),
        rs=SimpleNamespace(guard=SimpleNamespace(max_calls=lambda d: d)),  # ty: ignore[invalid-argument-type]
        scope=None,  # ty: ignore[invalid-argument-type]
    )
    cap = harness.tool_budgets(ctx, {"total": 50})
    assert cap.total == 4


async def test_detect_loops_stop_ends_the_turn_as_max_steps(env: Env) -> None:
    await _enable(
        env,
        "detect_loops",
        {"action": "stop", "repeated_call_threshold": 2, "window": 6},
        max_tool_calls_per_turn=10,
    )
    same = Tool("orders_month", {"period": "2026-10"})
    env.script.add("function:primary", [same], [same], [same], ["never"])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Loop")
    assert ev[-1]["reason"] == "max_steps"
    assert any(e["type"] == "text" and "maximum number of steps" in e["delta"] for e in ev)
    rows = await _events(env, "tools.loop")
    assert rows and rows[0].action == "blocked"


async def test_detect_loops_nudge_reaches_the_model(env: Env) -> None:
    await _enable(
        env,
        "detect_loops",
        {"repeated_call_threshold": 2, "window": 6},
        max_tool_calls_per_turn=10,
    )
    same = Tool("orders_month", {"period": "2026-10"})
    env.script.add("function:primary", [same], [same], ["Changing approach."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Loop")
    assert ev[-1]["reason"] == "end_turn"
    assert "loop" in str(env.script.seen[-1][1]).lower()


async def test_system_reminders_reach_the_model_and_the_trace(env: Env) -> None:
    await _enable(env, "system_reminders", {"reminders": [{"content": "Answer in one sentence."}]})
    env.script.add("function:primary", ["Fine."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Hello")
    assert "Answer in one sentence." in str(env.script.seen[-1][1])
    t = await _trace(env, ev[0]["turnId"])
    assert any(s["kind"] == "capability" and "fired" in s["name"] for s in t["spans"])


async def test_tool_output_limits_spill_an_oversized_result_kept_out_of_local_files(
    env: Env,
) -> None:
    await _enable(env, "tool_output_limits", {"over": 300, "action": "spill"})
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "x" * 900})],
        ["Read it."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Big")
    assert ev[-1]["reason"] == "end_turn"
    out = _returns(env.script.seen[-1][1])[-1]
    assert "stored to handle" in out


# guardrails on Evokai's guard


class _FakeGuard:
    def __init__(self, *, block: bool = False, clean: str | None = None) -> None:
        self.checked: list[str] = []
        self.block, self.clean = block, clean
        self.calls: list[str] = []

    async def check_input(self, text: str) -> Any:
        self.calls.append(text)
        return SimpleNamespace(
            text=self.clean or text, blocked="input.injection" if self.block else None, refusal=None
        )


async def test_the_input_guardrail_checks_only_what_the_turn_did_not() -> None:
    cap = harness.input_guardrail(None, {})  # ty: ignore[invalid-argument-type]
    guard = _FakeGuard()
    guard.checked = ["What is due?"]
    run = SimpleNamespace(deps=SimpleNamespace(guard=guard, trace=None))
    verdict = await cap.guard(run, 'Answer to your question "x": What is due?')
    assert verdict.action == "allow" and guard.calls == []
    blocked = _FakeGuard(block=True)
    run = SimpleNamespace(deps=SimpleNamespace(guard=blocked, trace=SimpleNamespace(event=_noop)))
    verdict = await cap.guard(run, "A delegated task")
    assert verdict.action == "block" and blocked.calls == ["A delegated task"]


def _noop(*_a: Any, **_k: Any) -> None:
    return None


def test_the_output_guardrail_applies_the_output_filter_to_a_server_run() -> None:
    cap = harness.output_guardrail(None, {})  # ty: ignore[invalid-argument-type]

    class Filter:
        blocked = False

        def push(self, text: str) -> str:
            return text.replace("sk-secret", "[redacted]")

        def flush(self) -> str:
            return ""

    seen: list[Any] = []
    g = SimpleNamespace(output_filter=Filter, after_output=seen.append)
    run = SimpleNamespace(deps=SimpleNamespace(guard=g, emit=None))
    verdict = cap.guard(run, "the key is sk-secret")
    assert verdict.action == "replace" and verdict.replacement == "the key is [redacted]"
    assert len(seen) == 1
    turn = SimpleNamespace(deps=SimpleNamespace(guard=g, emit=print))
    assert cap.guard(turn, "the key is sk-secret").action == "allow"


def test_the_tool_guardrail_blocks_on_an_enforced_argument_check() -> None:
    from evokai.guard import tools as tp

    cap = harness.tool_guardrail(None, {})  # ty: ignore[invalid-argument-type]
    recorded: list[Any] = []
    policy = SimpleNamespace(
        settings=SimpleNamespace(tools=SimpleNamespace(egress=None)),
        on=lambda c: c == "tools.args",
        enforced=lambda c: c == "tools.args",
    )
    g = SimpleNamespace(policy=policy, record=lambda *a, **_k: recorded.append(a))
    spec = SimpleNamespace(schema={"type": "object", "required": ["period"], "properties": {}})
    run = SimpleNamespace(deps=SimpleNamespace(guard=g, tools={"orders_month": spec}))
    call = SimpleNamespace(name="orders_month", args={})
    verdict = cap.guard(run, call)
    assert verdict.action == "block" and recorded and recorded[0][0] == "tools.args"
    assert tp.check_args({"period": "x"}, spec.schema).allowed


# output review, argument repair, cache busts


async def test_refine_output_reviews_a_server_run_and_is_left_out_of_a_turn(env: Env) -> None:
    from tests.test_server_runs import _put, _run

    await _put(
        env,
        {
            "name": "writer",
            "model": "function:primary",
            "channels": ["server"],
            "instructions": "Write.",
        },
    )
    env.svc.settings.capabilities = ["*"]
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "capability", "name": "refine_output", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200
    await _put(
        env,
        {
            "name": "writer",
            "model": "function:primary",
            "channels": ["server"],
            "instructions": "Write.",
            "capabilities": {"refine_output": {"enabled": True, "config": {"rounds": 1}}},
        },
    )
    env.script.add("function:primary", ["Draft."], ["Final."])
    r = await _run(env, "writer")
    assert r.status_code == 200, r.text
    assert r.json()["output"] == "Final."
    assert len(env.script.seen) == 2
    assert "Draft." in str(env.script.seen[-1][1])
    assert harness.refine_output(SimpleNamespace(turn=False), {"rounds": 9}).rounds == 3  # ty: ignore[invalid-argument-type]
    ctx = SimpleNamespace(turn=True)
    try:
        harness.refine_output(ctx, {})  # ty: ignore[invalid-argument-type]
    except Exception as e:
        assert "shown as it is written" in str(e)
    else:
        raise AssertionError("refine_output built in a turn")


async def test_repair_tool_arguments_repairs_and_traces() -> None:
    events: list[str] = []
    cap = harness.repair_tool_arguments(None, {})  # ty: ignore[invalid-argument-type]
    call = SimpleNamespace(tool_name="t", provider_name=None, id=None, args="{'a': 1,}")
    trace = SimpleNamespace(event=lambda kind, name, **_: events.append(f"{kind}:{name}"))
    run = SimpleNamespace(deps=SimpleNamespace(trace=trace), tracer=_Tracer())
    out = await cap.before_tool_validate(run, call=call, tool_def=None, args=call.args)
    assert out == '{"a": 1}' and events == ["capability:repair_tool_arguments repaired"]


class _Tracer:
    def start_as_current_span(self, _name: str) -> Any:
        import contextlib

        return contextlib.nullcontext()


def test_cache_bust_marks_are_shared_across_the_turns_of_a_process() -> None:
    ctx = SimpleNamespace(scope=SimpleNamespace(session_id="s1"))
    a = harness.warn_on_cache_busts(ctx, {})  # ty: ignore[invalid-argument-type]
    b = harness.warn_on_cache_busts(ctx, {})  # ty: ignore[invalid-argument-type]
    assert a._store is b._store


# capability tools follow Evokai's tool rules


async def test_a_capability_tool_that_writes_waits_for_the_person(
    env: Env, monkeypatch: Any
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_linear(_ctx: Any, _config: dict[str, Any]) -> Any:
        from pydantic_ai.capabilities import Toolset

        ts = FunctionToolset()

        def create_issue(title: str) -> str:
            calls.append({"title": title})
            return "LIN-1 created"

        def search_issues(query: str) -> str:
            return f"found {query}"

        ts.add_function(create_issue, name="linear_create_issue", metadata={"annotations": {}})
        ts.add_function(
            search_issues,
            name="linear_search_issues",
            metadata={"annotations": {"readOnlyHint": True}},
        )
        return Toolset(ts)

    monkeypatch.setitem(harness.BUILDERS, "linear", fake_linear)
    await _enable(env, "linear")
    env.script.add(
        "function:primary",
        [Tool("linear_search_issues", {"query": "bug"}, "s1")],
        [Tool("linear_create_issue", {"title": "Bug"}, "w1")],
        ["Created."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="File the bug")
    confirm = next(e for e in ev if e["type"] == "confirm")
    assert ev[-1]["reason"] == "confirm" and calls == []
    ev2 = await env.chat(tok["token"], confirm={"actionId": confirm["actionId"], "approve": True})
    assert ev2[-1]["reason"] == "end_turn" and calls == [{"title": "Bug"}]
    t = await _trace(env, ev[0]["turnId"])
    spans = [s for s in t["spans"] if s["attrs"].get("source") == "capability"]
    assert {s["attrs"]["tool"] for s in spans} == {"linear_search_issues", "linear_create_issue"}


async def test_the_tool_policy_hides_a_capability_tool(env: Env, monkeypatch: Any) -> None:
    def fake(_ctx: Any, _config: dict[str, Any]) -> Any:
        from pydantic_ai.capabilities import Toolset

        ts = FunctionToolset()

        def secret_tool() -> str:
            return "no"

        ts.add_function(secret_tool, name="linear_secret")
        return Toolset(ts)

    monkeypatch.setitem(harness.BUILDERS, "linear", fake)
    await _enable(env, "linear")
    r = await env.client.put(
        "/agent/v1/admin/security", json={"tools": {"deny": ["linear_*"]}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    tok = await env.token()
    await env.chat(tok["token"], text="Hello")
    assert "linear_secret" not in env.script.tools_seen
