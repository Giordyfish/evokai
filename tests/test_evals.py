"""Evals: checks, runs on demand and on save, the comparison, the eval budget and no amounts."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from evokai.db.repositories import Who
from evokai.evals.checks import parse_checks, run_checks
from tests.conftest import Env, Tool, system_text

CONSOLE = {"X-Evokai-Console": "1"}
MONEY = re.compile(r"usd|eur|cost|price|amount|spend|€|\$", re.IGNORECASE)


def adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def wait_run(env: Env, run_id: str) -> dict[str, Any]:
    for _ in range(200):
        r = await env.client.get(f"/agent/v1/admin/evals/runs/{run_id}", headers=adm(env))
        body = r.json()
        if body["status"] != "running":
            return body
        await asyncio.sleep(0.02)
    raise AssertionError("the run did not end")


async def put_case(env: Env, name: str, **body: Any) -> None:
    r = await env.client.put(
        f"/agent/v1/admin/agents/helper/evals/cases/{name}", json=body, headers=adm(env)
    )
    assert r.status_code == 200, r.text


# ── checks ────────────────────────────────────────────────────────────────────────────────────────


async def test_every_check_kind() -> None:
    checks = parse_checks([
        {"type": "contains", "value": "OCTOBER"},
        {"type": "not_contains", "value": "sorry"},
        {"type": "regex", "pattern": r"\b24\b"},
        {"type": "json_schema", "schema": {"type": "object", "required": ["n"]}},
        {"type": "tool_called", "tool": "orders_month", "args": {"period": "2026-10"}},
        {"type": "tool_not_called", "tool": "return_close"},
        {"type": "rubric", "rubric": "Polite."},
    ])
    answer = 'In October there are 24 orders.\n```json\n{"n": 24}\n```'
    calls = [{"name": "orders_month", "args": {"period": "2026-10"}}]
    out = await run_checks(checks, answer=answer, calls=calls, question="q", judge=None)
    assert [c["outcome"] for c in out] == ["passed"] * 6 + ["skipped"]
    bad = await run_checks(checks[3:5], answer="no json here", calls=[], question="q", judge=None)
    assert [c["outcome"] for c in bad] == ["failed", "failed"]
    assert bad[1]["detail"] == "called: nothing"
    with pytest.raises(ValueError):
        parse_checks([{"type": "regex", "pattern": "("}])


# ── runs ──────────────────────────────────────────────────────────────────────────────────────────


async def test_a_run_on_demand_is_a_real_turn_of_the_test_subject(env: Env) -> None:
    await put_case(
        env,
        "october",
        input="How did October go?",
        page={"path": "/orders/2026-10", "title": "Orders in October", "text": "24 orders open"},
        checks=[{"type": "contains", "value": "24"}, {"type": "not_contains", "value": "sorry"}],
    )
    bad = await env.client.put(
        "/agent/v1/admin/agents/helper/evals/cases/x",
        json={"input": "x", "checks": [{"type": "regex", "pattern": "("}]},
        headers=adm(env),
    )
    assert bad.status_code == 400
    env.script.add("function:primary", ["October has 24 orders open."])
    start = await env.client.post(
        "/agent/v1/admin/agents/helper/evals/runs", json={}, headers=adm(env)
    )
    assert start.status_code == 202, start.text
    run = await wait_run(env, start.json()["runId"])
    assert run["status"] == "done" and run["trigger"] == "manual" and run["baseline"] is None
    (case,) = run["cases"]
    result = case["results"][run["version"]]
    assert result["passed"] and result["answer"] == "October has 24 orders open."
    assert [c["outcome"] for c in result["checks"]] == ["passed", "passed"]
    # The page went with the input, as from a browser.
    assert "24 orders open" in system_text(env.script.seen[-1][1])
    summary = run["summary"][run["version"]]
    assert summary["passRate"] == pytest.approx(100) and summary["capPct"] >= 0

    # Priced as eval usage of the test subject; the users' caps never see it.
    rows = await env.svc.store.usage.report(
        env.app_id,
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime.now(UTC),
        group_by=["kind", "subject"],
    )
    assert [(r["kind"], r["subject"]) for r in rows] == [("eval", "eval:helper")]
    spend = await env.svc.store.usage.spend(Who(env.app_id, "eval:helper"), datetime.now(UTC))
    assert spend["appMonthUsd"] == 0 and spend["subjectMonthUsd"] == 0
    assert await env.svc.store.usage.eval_spend(env.app_id, datetime.now(UTC)) > 0
    # Nothing of the throw-away conversation stays.
    assert await env.svc.store.sessions.recent(Who(env.app_id, "eval:helper")) == []


async def test_saving_a_new_version_compares_it_with_the_previous_one(env: Env) -> None:
    await put_case(
        env, "greeting", input="Hello", checks=[{"type": "contains", "value": "welcome"}]
    )
    agents = (await env.client.get("/agent/v1/admin/agents", headers=adm(env))).json()["items"]
    old = agents[0]["version"]
    helper = {k: v for k, v in agents[0].items() if k != "version"}
    helper["instructions"] = "You are a warm assistant. Today is {{date}}."
    env.script.add("function:primary", ["Hi, welcome!"], ["Hi."])
    saved = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=adm(env))
    assert saved.status_code == 200 and saved.json()["created"]
    run = await wait_run(env, saved.json()["evalRunId"])
    assert (run["trigger"], run["version"], run["baseline"]) == (
        "save",
        saved.json()["version"],
        old,
    )
    (case,) = run["cases"]
    assert case["results"][run["version"]]["passed"] is True
    assert case["results"][old]["passed"] is False
    assert case["changed"] is True and case["sameAnswer"] is False
    comparison = run["comparison"]
    assert comparison["passRateDelta"] == pytest.approx(100)
    assert comparison["tokensVsBaselinePct"] > 0
    history = (
        await env.client.get("/agent/v1/admin/agents/helper/evals/runs", headers=adm(env))
    ).json()
    assert history["items"][0]["id"] == run["id"] and history["budget"]["usedPct"] >= 0

    # Turned off per application: saving runs nothing.
    await env.client.patch(
        "/agent/v1/admin/application", json={"eval_on_save": False}, headers=adm(env)
    )
    helper["instructions"] = "You are terse. Today is {{date}}."
    again = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=adm(env))
    assert again.status_code == 200 and "evalRunId" not in again.json(), again.text


async def test_tools_run_read_only_and_changes_stop_at_the_confirmation(env: Env) -> None:
    patched = await env.client.patch(
        "/agent/v1/admin/application", json={"eval_credential": "test-user-token"}, headers=adm(env)
    )
    assert patched.status_code == 200
    app = (await env.client.get("/agent/v1/admin/application", headers=adm(env))).json()
    assert app["hasEvalCredential"] is True and "eval_credential" not in app
    await put_case(
        env,
        "classify",
        input="Classify October",
        checks=[
            {"type": "tool_called", "tool": "orders_month"},
            {"type": "tool_called", "tool": "order_refund"},
            {"type": "tool_not_called", "tool": "stock_report"},
        ],
    )
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        [Tool("order_refund", {"period": "2026-10"}, call_id="t2")],
    )
    start = await env.client.post(
        "/agent/v1/admin/agents/helper/evals/runs", json={}, headers=adm(env)
    )
    run = await wait_run(env, start.json()["runId"])
    result = run["cases"][0]["results"][run["version"]]
    assert result["reason"] == "confirm" and result["passed"], result
    assert [c[0] for c in env.mcp.calls] == ["orders_month"]  # the change never ran
    assert "Bearer test-user-token" in env.mcp.auth


async def test_the_eval_budget_stops_runs(env: Env) -> None:
    await put_case(env, "a", input="Hi", checks=[])
    await env.client.patch(
        "/agent/v1/admin/application", json={"eval_monthly_usd": 0}, headers=adm(env)
    )
    start = await env.client.post(
        "/agent/v1/admin/agents/helper/evals/runs", json={}, headers=adm(env)
    )
    run = await wait_run(env, start.json()["runId"])
    assert run["status"] == "limit" and run["cases"] == []


async def test_rubrics_are_judged_only_with_a_judge_model(env: Env) -> None:
    await put_case(
        env, "tone", input="Hi", checks=[{"type": "rubric", "rubric": "It greets the user."}]
    )
    env.script.add("function:primary", ["Hello there!"], ["Hello again!"])
    first = await env.client.post(
        "/agent/v1/admin/agents/helper/evals/runs", json={}, headers=adm(env)
    )
    run = await wait_run(env, first.json()["runId"])
    check = run["cases"][0]["results"][run["version"]]["checks"][0]
    assert check["outcome"] == "skipped" and run["summary"][run["version"]]["skippedChecks"] == 1
    await env.client.patch(
        "/agent/v1/admin/application", json={"eval_judge_model": "function:judge"}, headers=adm(env)
    )
    env.script.add("function:judge", [json.dumps({"pass": True, "reason": "It greets."})])
    second = await env.client.post(
        "/agent/v1/admin/agents/helper/evals/runs", json={}, headers=adm(env)
    )
    run = await wait_run(env, second.json()["runId"])
    check = run["cases"][0]["results"][run["version"]]["checks"][0]
    assert (check["outcome"], check["detail"]) == ("passed", "It greets.")


async def test_the_console_sees_shares_and_counts_never_amounts(env: Env) -> None:
    await put_case(env, "a", input="Hi", checks=[{"type": "contains", "value": "ok"}])
    env.script.add("function:primary", ["ok"])
    login = await env.client.post("/agent/v1/console/login", json={"key": env.admin_key})
    assert login.status_code == 204
    start = await env.client.post(
        "/agent/v1/admin/agents/helper/evals/runs", json={}, headers=CONSOLE
    )
    run_id = start.json()["runId"]
    for _ in range(200):
        detail = (
            await env.client.get(f"/agent/v1/admin/evals/runs/{run_id}", headers=CONSOLE)
        ).json()
        if detail["status"] != "running":
            break
        await asyncio.sleep(0.02)
    listed = (
        await env.client.get("/agent/v1/admin/agents/helper/evals/runs", headers=CONSOLE)
    ).json()
    cases = (
        await env.client.get("/agent/v1/admin/agents/helper/evals/cases", headers=CONSOLE)
    ).json()

    def keys(v: object) -> list[str]:
        if isinstance(v, dict):
            return [*v, *(k for x in v.values() for k in keys(x))]
        if isinstance(v, list):
            return [k for x in v for k in keys(x)]
        return []

    for payload in (detail, listed, cases):
        assert not [k for k in keys(payload) if MONEY.search(k)], json.dumps(payload)[:300]
    busy = await env.client.post(
        "/agent/v1/admin/agents/nobody/evals/runs", json={}, headers=CONSOLE
    )
    assert busy.status_code == 404


# ── trying an agent (the console assistant's try_agent) ───────────────────────────────────────


async def test_trying_an_agent_answers_and_keeps_nothing(env: Env) -> None:
    env.script.add("function:primary", ["October has 24 orders open."])
    r = await env.client.post(
        "/agent/v1/admin/agents/helper/try",
        json={"message": "How did October go?"},
        headers=adm(env),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] == "October has 24 orders open."
    assert body["stoppedAtConfirmation"] is False and body["error"] is None
    assert 0 <= body["budgetPct"] <= 100
    assert not MONEY.search(json.dumps({k: v for k, v in body.items() if k != "answer"}))
    # Nothing is kept: no conversation of the test subject remains.
    convs = await env.client.get("/agent/v1/admin/conversations", headers=adm(env))
    assert all(
        not str(c.get("subject", "")).startswith("try:") for c in convs.json().get("items", [])
    )


async def test_a_change_asked_for_while_trying_stops_at_its_confirmation(env: Env) -> None:
    await env.client.patch(
        "/agent/v1/admin/application", json={"eval_credential": "test-user-token"}, headers=adm(env)
    )
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        [Tool("order_refund", {"period": "2026-10"}, call_id="t2")],
    )
    r = await env.client.post(
        "/agent/v1/admin/agents/helper/try", json={"message": "Refund October"}, headers=adm(env)
    )
    body = r.json()
    assert body["stoppedAtConfirmation"] is True
    assert body["toolsAsked"] == ["orders_month", "order_refund"]
    assert [c[0] for c in env.mcp.calls] == ["orders_month"]  # the change never ran


async def test_trying_an_agent_stops_at_the_eval_budget(env: Env) -> None:
    await env.client.patch(
        "/agent/v1/admin/application", json={"eval_monthly_usd": 0}, headers=adm(env)
    )
    r = await env.client.post(
        "/agent/v1/admin/agents/helper/try", json={"message": "Hi"}, headers=adm(env)
    )
    assert r.status_code == 409 and r.json()["error"] == "eval_budget"
