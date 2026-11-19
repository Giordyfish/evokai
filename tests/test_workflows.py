"""Durable workflows: steps, approvals, retries, leases, schedules, triggers, privacy, traces."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa

from evokai.db import models as m
from evokai.workflows.engine import WorkflowService
from evokai.workflows.webhooks import Notifier, sign
from tests.conftest import MCP_URL, Env, Tool

TRIAGE = {
    "name": "triage",
    "model": "function:primary",
    "channels": ["server"],
    "instructions": "Classify the customer's e-mail.",
    "output_schema": {
        "type": "object",
        "required": ["category"],
        "properties": {"category": {"type": "string", "enum": ["order", "technical"]}},
    },
}
WRITER = {
    "name": "writer",
    "model": "function:writer",
    "channels": ["server"],
    "instructions": "Draft a reply.",
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


def _srv(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.server_key}"}


async def _setup(env: Env, *agents: dict[str, Any]) -> None:
    for a in agents or (TRIAGE, WRITER):
        r = await env.client.put(f"/agent/v1/admin/agents/{a['name']}", json=a, headers=_adm(env))
        assert r.status_code == 200, r.text
    r = await env.client.put(
        "/agent/v1/admin/mcp-servers/back", json={"url": MCP_URL, "auth": "none"}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text


async def _put(env: Env, defn: dict[str, Any]) -> dict[str, Any]:
    r = await env.client.put(
        f"/agent/v1/admin/workflows/{defn['name']}", json=defn, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _start(env: Env, name: str, **body: Any) -> dict[str, Any]:
    r = await env.client.post(f"/agent/v1/workflows/{name}/runs", json=body, headers=_srv(env))
    assert r.status_code == 202, r.text
    return r.json()


async def _run(env: Env, run_id: str) -> dict[str, Any]:
    r = await env.client.get(f"/agent/v1/workflows/runs/{run_id}", headers=_srv(env))
    assert r.status_code == 200, r.text
    return r.json()


def _steps(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in run["steps"]}


async def _approvals(env: Env, **q: Any) -> list[dict[str, Any]]:
    r = await env.client.get("/agent/v1/workflows/approvals", params=q, headers=_srv(env))
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _decide(
    env: Env, approval_id: str, decision: str, groups: list[str] | None = None
) -> httpx.Response:
    return await env.client.post(
        f"/agent/v1/workflows/approvals/{approval_id}",
        json={"decision": decision, "comment": "ok", "by": {"name": "Ada", "groups": groups or []}},
        headers=_srv(env),
    )


MAIL = {
    "name": "mail",
    "input_schema": {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    },
    "steps": [
        {
            "id": "classify",
            "kind": "agent",
            "agent": "triage",
            "input": "Message: {{ input.text }}",
        },
        {
            "id": "route",
            "kind": "condition",
            "if": "steps.classify.output.category == 'order'",
            "then": "draft",
            "else": "skip",
        },
        {"id": "draft", "kind": "agent", "agent": "writer", "input": "Reply to: {{ input.text }}"},
        {
            "id": "approve",
            "kind": "approval",
            "question": "Send the reply to {{ input.text }}?",
            "details": {"draft": "{{ steps.draft.output }}"},
            "approvers": {"groups": ["backoffice"]},
        },
        {
            "id": "send",
            "kind": "tool",
            "server": "back",
            "tool": "order_refund",
            "approval": "approve",
            "arguments": {"reply": "{{ steps.draft.output }}", "key": "{{ step.key }}"},
            "label": "Send the reply «{reply}»",
            "next": "end",
        },
        {"id": "skip", "kind": "wait", "seconds": 0},
    ],
    "output": {
        "sent": "{{ steps.send.status == 'done' }}",
        "category": "{{ steps.classify.output.category }}",
    },
}


async def test_a_run_routes_waits_for_a_person_and_calls_the_tool(env: Env) -> None:
    await _setup(env)
    await _put(env, MAIL)
    env.script.add("function:primary", [Tool("final_result", {"category": "order"})])
    env.script.add("function:writer", ["Dear customer, fixed."])
    started = await _start(env, "mail", input={"text": "Order arrived twice"}, subject="cust-7")
    await env.svc.workflows.drain()
    run = await _run(env, started["id"])
    assert run["status"] == "waiting"
    steps = _steps(run)
    assert steps["classify"]["output"] == {"category": "order"}
    assert steps["route"]["output"] == {"value": True} and "skip" not in steps
    assert steps["approve"]["status"] == "waiting"
    assert "Message: Order arrived twice" in str(env.script.seen[0][1])

    (a,) = await _approvals(env, group="backoffice")
    assert a["question"] == "Send the reply to Order arrived twice?"
    assert a["summary"] == ["Send the reply «Dear customer, fixed.»"]
    assert a["details"] == {"draft": "Dear customer, fixed."} and a["subject"] == "cust-7"
    assert await _approvals(env, group="sales") == []
    assert (await _decide(env, a["id"], "approve", ["sales"])).status_code == 403
    assert env.mcp.calls == []
    r = await _decide(env, a["id"], "approve", ["backoffice"])
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (await _decide(env, a["id"], "reject", ["backoffice"])).status_code == 409
    await env.svc.workflows.drain()
    run = await _run(env, started["id"])
    assert run["status"] == "done", run
    assert run["output"] == {"sent": True, "category": "order"}
    assert env.mcp.calls == [
        ("order_refund", {"reply": "Dear customer, fixed.", "key": f"{run['id']}:send"})
    ]
    assert _steps(run)["approve"]["output"]["by"] == "Ada"
    audit = await env.client.get("/agent/v1/admin/audit", headers=_adm(env))
    assert "workflow.approval.decide" in {x["action"] for x in audit.json()["items"]}


async def test_the_other_branch_skips_the_approval(env: Env) -> None:
    await _setup(env)
    await _put(env, MAIL)
    env.script.add("function:primary", [Tool("final_result", {"category": "technical"})])
    started = await _start(env, "mail", input={"text": "Book missing"})
    await env.svc.workflows.drain()
    run = await _run(env, started["id"])
    assert run["status"] == "done"
    assert set(_steps(run)) == {"classify", "route", "skip"}
    assert run["output"] == {"sent": False, "category": "technical"}


async def test_a_rejection_ends_the_run_and_nothing_changes(env: Env) -> None:
    await _setup(env)
    await _put(env, MAIL)
    env.script.add("function:primary", [Tool("final_result", {"category": "order"})])
    started = await _start(env, "mail", input={"text": "x"})
    await env.svc.workflows.drain()
    (a,) = await _approvals(env)
    assert (await _decide(env, a["id"], "reject", ["backoffice"])).status_code == 200
    await env.svc.workflows.drain()
    run = await _run(env, started["id"])
    assert run["status"] == "rejected" and env.mcp.calls == []
    assert _steps(run).get("send") is None


async def test_the_input_is_checked_against_its_schema(env: Env) -> None:
    await _setup(env)
    await _put(env, MAIL)
    r = await env.client.post(
        "/agent/v1/workflows/mail/runs", json={"input": {}}, headers=_srv(env)
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_input"


def _mutating(**tool: Any) -> dict[str, Any]:
    return {
        "name": "close",
        "steps": [
            {"id": "close", "kind": "tool", "server": "back", "tool": "order_refund", **tool}
        ],
    }


async def test_a_tool_that_changes_data_needs_an_approval_unless_safe_and_granted(env: Env) -> None:
    await _setup(env)
    await _put(env, _mutating())
    run = await _start(env, "close")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert detail["status"] == "failed" and detail["reason"] == "approval_required"
    assert env.mcp.calls == []

    await _put(env, _mutating(safe=True))
    run = await _start(env, "close")
    await env.svc.workflows.drain()
    assert (await _run(env, run["id"]))["reason"] == "approval_required"

    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "workflow", "name": "autonomous", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    run = await _start(env, "close")
    await env.svc.workflows.drain()
    assert (await _run(env, run["id"]))["status"] == "done"
    assert [c[0] for c in env.mcp.calls] == ["order_refund"]

    # A read-only tool needs nothing.
    await _put(
        env,
        {
            "name": "read",
            "steps": [
                {
                    "id": "r",
                    "kind": "tool",
                    "server": "back",
                    "tool": "orders_month",
                    "arguments": {"period": "2026-10"},
                }
            ],
        },
    )
    run = await _start(env, "read")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert detail["status"] == "done" and detail["output"]["orders"] == 24


async def test_tool_steps_refuse_servers_that_act_as_a_user(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "u",
            "steps": [{"id": "r", "kind": "tool", "server": "app", "tool": "orders_month"}],
        },
    )
    run = await _start(env, "u")
    await env.svc.workflows.drain()
    assert (await _run(env, run["id"]))["reason"] == "forward_user"


async def test_an_approval_times_out_to_its_default(env: Env) -> None:
    await _setup(env)
    wf = {
        "name": "timeout",
        "steps": [
            {
                "id": "ask",
                "kind": "approval",
                "question": "Go?",
                "timeout_seconds": 60,
                "on_timeout": "approve",
            },
            {
                "id": "write",
                "kind": "tool",
                "server": "back",
                "tool": "order_refund",
                "approval": "ask",
            },
        ],
    }
    await _put(env, wf)
    run = await _start(env, "timeout")
    svc: WorkflowService = env.svc.workflows
    await svc.drain()
    assert (await _run(env, run["id"]))["status"] == "waiting"
    later = datetime.now(UTC) + timedelta(seconds=120)
    svc.now = lambda: later
    await svc.drain()
    detail = await _run(env, run["id"])
    # Approved by the clock is not a person's decision: changing data needs autonomous.
    assert _steps(detail)["ask"]["output"]["via"] == "timeout"
    assert detail["status"] == "failed" and detail["reason"] == "approval_required"
    (a,) = await _approvals(env, status="approved")
    assert a["decidedVia"] == "timeout"


async def test_parallel_branches_run_and_join(env: Env) -> None:
    await _setup(env)
    wf = {
        "name": "fan",
        "steps": [
            {
                "id": "fan",
                "kind": "parallel",
                "branches": [
                    {
                        "name": "month",
                        "steps": [
                            {
                                "id": "m",
                                "kind": "tool",
                                "server": "back",
                                "tool": "orders_month",
                                "arguments": {"period": "2026-10"},
                            }
                        ],
                    },
                    {
                        "name": "stock",
                        "steps": [
                            {
                                "id": "d",
                                "kind": "tool",
                                "server": "back",
                                "tool": "stock_report",
                            },
                            {
                                "id": "c",
                                "kind": "condition",
                                "if": "steps.d.output.orders > 10",
                                "then": "end",
                            },
                            {"id": "never", "kind": "wait", "seconds": 5},
                        ],
                    },
                ],
            },
            {"id": "after", "kind": "condition", "if": "steps.fan.output.month.orders == 24"},
        ],
    }
    await _put(env, wf)
    run = await _start(env, "fan")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert detail["status"] == "done", detail
    steps = _steps(detail)
    assert set(steps["fan"]["output"]) == {"month", "stock"}
    assert steps["fan"]["output"]["stock"] == {"value": True}
    assert "never" not in steps and steps["after"]["output"] == {"value": True}
    assert steps["m"]["branch"] == "fan/0"


async def test_a_wait_holds_the_run_until_its_time(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "w",
            "timezone": "Europe/Rome",
            "steps": [
                {"id": "pause", "kind": "wait", "until": "{{ input.day }}"},
                {"id": "go", "kind": "condition", "if": "true"},
            ],
        },
    )
    day = datetime.now(UTC) + timedelta(days=40)
    run = await _start(env, "w", input={"day": day.date().isoformat()})
    svc = env.svc.workflows
    await svc.drain()
    detail = await _run(env, run["id"])
    assert _steps(detail)["pause"]["status"] == "pending"
    expected = datetime.combine(day.date(), datetime.min.time(), tzinfo=ZoneInfo("Europe/Rome"))
    assert datetime.fromisoformat(_steps(detail)["pause"]["dueAt"]) == expected  # midnight in Rome
    svc.now = lambda: expected + timedelta(minutes=1)
    await svc.drain()
    assert (await _run(env, run["id"]))["status"] == "done"


async def test_failed_steps_are_retried_with_backoff_then_the_run_can_be_resumed(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "r",
            "steps": [
                {
                    "id": "read",
                    "kind": "tool",
                    "server": "back",
                    "tool": "orders_month",
                    "retry": {"max_attempts": 2, "backoff_seconds": 30},
                }
            ],
        },
    )
    env.mcp.down = True
    run = await _start(env, "r")
    svc = env.svc.workflows
    await svc.drain()
    step = _steps(await _run(env, run["id"]))["read"]
    assert step["status"] == "pending" and step["attempt"] == 1 and step["error"]
    await svc.drain()  # not due yet
    assert _steps(await _run(env, run["id"]))["read"]["attempt"] == 1
    t = datetime.now(UTC) + timedelta(seconds=31)
    svc.now = lambda: t
    await svc.drain()
    detail = await _run(env, run["id"])
    assert detail["status"] == "failed" and _steps(detail)["read"]["attempt"] == 2

    env.mcp.down = False
    r = await env.client.post(
        f"/agent/v1/admin/workflows/runs/{run['id']}/retry", headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    await svc.drain()
    assert (await _run(env, run["id"]))["status"] == "done"


async def test_a_step_left_running_by_a_dead_worker_is_resumed_by_another(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "d",
            "steps": [
                {"id": "read", "kind": "tool", "server": "back", "tool": "orders_month"},
                {"id": "check", "kind": "condition", "if": "steps.read.output.orders == 24"},
            ],
        },
    )
    run = await _start(env, "d")
    # The worker that leased the step died mid-call: the row stays running on its lease.
    async with env.svc.db.session() as s:
        await s.execute(
            sa
            .update(m.WorkflowStep)
            .where(m.WorkflowStep.run_id == run["id"])
            .values(
                status="running",
                lease_owner="dead:1",
                attempt=1,
                lease_until=datetime.now(UTC) + timedelta(seconds=30),
            )
        )
    restarted = WorkflowService(env.svc)  # a new process on the same database
    assert await restarted.drain() == 0  # the lease is still held
    restarted.now = lambda: datetime.now(UTC) + timedelta(seconds=31)
    assert await restarted.drain() == 2
    detail = await _run(env, run["id"])
    assert detail["status"] == "done"
    assert _steps(detail)["read"]["attempt"] == 2  # at least once: it ran again
    assert len(env.mcp.calls) == 1


async def test_two_workers_never_run_the_same_step(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "many",
            "steps": [{"id": "read", "kind": "tool", "server": "back", "tool": "orders_month"}],
        },
    )
    ids = [(await _start(env, "many"))["id"] for _ in range(8)]
    a, b = env.svc.workflows, WorkflowService(env.svc)
    assert a.owner != b.owner
    async with env.svc.db.session() as s:
        row_id = (
            await s.execute(sa.select(m.WorkflowStep.id).where(m.WorkflowStep.run_id == ids[0]))
        ).scalar_one()
    won = await asyncio.gather(a._lease(row_id), b._lease(row_id))
    assert sorted(won) == [False, True]
    winner = a if won[0] else b
    await winner._execute(row_id)
    await asyncio.gather(a.drain(), b.drain())
    assert len(env.mcp.calls) == 8
    for rid in ids:
        assert (await _run(env, rid))["status"] == "done"


async def test_a_schedule_fires_once_per_occurrence_across_instances(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "nightly",
            "timezone": "Europe/Rome",
            "triggers": [
                {"kind": "schedule", "cron": "0 2 * * *", "input": {"day": "{{ now.date }}"}}
            ],
            "steps": [{"id": "w", "kind": "wait", "seconds": 0}],
        },
    )
    a, b = env.svc.workflows, WorkflowService(env.svc)
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    at = tomorrow.replace(
        hour=2, minute=30, second=0, microsecond=0
    )  # after 02:00 in Rome (01:00 UTC)
    started = await asyncio.gather(a.schedule_tick(at), b.schedule_tick(at))
    assert sum(started) == 1
    assert await a.schedule_tick(at + timedelta(minutes=10)) == 0
    runs = (await env.client.get("/agent/v1/workflows/runs", headers=_srv(env))).json()["items"]
    assert len(runs) == 1 and runs[0]["trigger"] == "schedule"
    detail = await _run(env, runs[0]["id"])
    assert detail["input"] == {"day": at.date().isoformat()}
    # An occurrence older than a day is not caught up.
    assert await a.schedule_tick(at + timedelta(days=3, hours=-1)) == 1


async def test_an_agent_run_finishing_starts_a_workflow_but_its_own_steps_do_not(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "follow",
            "triggers": [
                {
                    "kind": "event",
                    "event": "agent_run.finished",
                    "agent": "triage",
                    "input": {"category": "{{ event.output.category }}"},
                }
            ],
            "steps": [
                {
                    "id": "again",
                    "kind": "agent",
                    "agent": "triage",
                    "input": "Again {{ input.category }}",
                }
            ],
        },
    )
    env.script.add(
        "function:primary",
        [Tool("final_result", {"category": "order"})],
        [Tool("final_result", {"category": "technical"})],
    )
    r = await env.client.post(
        "/agent/v1/agents/triage/run", json={"input": "Order"}, headers=_srv(env)
    )
    assert r.status_code == 200, r.text
    await env.svc.workflows.drain()
    runs = (await env.client.get("/agent/v1/workflows/runs", headers=_srv(env))).json()["items"]
    assert len(runs) == 1 and runs[0]["trigger"] == "event"
    detail = await _run(env, runs[0]["id"])
    assert detail["status"] == "done" and detail["input"] == {"category": "order"}
    # The workflow's own agent step did not start another run.
    runs = (await env.client.get("/agent/v1/workflows/runs", headers=_srv(env))).json()["items"]
    assert len(runs) == 1


async def test_a_signed_webhook_starts_a_run_once(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _setup(env)
    monkeypatch.setenv("CRM_HOOK_SECRET", "s3cret")
    await _put(
        env,
        {
            "name": "hooked",
            "triggers": [{"kind": "webhook", "secret_env": "CRM_HOOK_SECRET"}],
            "steps": [{"id": "w", "kind": "wait", "seconds": 0}],
        },
    )
    body = json.dumps({"customer": "c-9"}).encode()
    ts = str(int(time.time()))
    headers = {
        "X-Evokai-Timestamp": ts,
        "X-Evokai-Signature": sign("s3cret", int(ts), body),
        "X-Evokai-Delivery": "d1",
        "Content-Type": "application/json",
    }
    url = "/agent/v1/workflow-hooks/test/hooked"
    r = await env.client.post(url, content=body, headers=headers)
    assert r.status_code == 202 and r.json()["created"] is True, r.text
    again = await env.client.post(url, content=body, headers=headers)
    assert again.json() == {**r.json(), "created": False}
    bad = await env.client.post(
        url, content=body, headers={**headers, "X-Evokai-Signature": "v1=00"}
    )
    assert bad.status_code == 401
    old = str(int(time.time()) - 3600)
    stale = await env.client.post(
        url,
        content=body,
        headers={
            **headers,
            "X-Evokai-Timestamp": old,
            "X-Evokai-Signature": sign("s3cret", int(old), body),
        },
    )
    assert stale.status_code == 401
    detail = await _run(env, r.json()["runId"])
    assert detail["input"] == {"customer": "c-9"} and detail["trigger"] == "webhook"


async def test_notify_signs_its_call_and_refuses_addresses_off_the_list(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _setup(env)
    monkeypatch.setenv("NOTIFY_SECRET", "k")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async def resolver(host: str, _port: int) -> list[str]:  # ruff: ignore[unused-async]
        return {
            "hooks.example.com": ["93.184.216.34"],
            "evil.example.com": ["169.254.169.254"],
        }.get(host, ["10.0.0.1"])

    env.svc.workflows.notifier = Notifier(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), ["example.com"], resolver
    )
    await _put(
        env,
        {
            "name": "n",
            "steps": [
                {
                    "id": "tell",
                    "kind": "notify",
                    "url": "https://hooks.example.com/x",
                    "secret_env": "NOTIFY_SECRET",
                    "body": {"run": "{{ run.id }}"},
                }
            ],
        },
    )
    run = await _start(env, "n")
    await env.svc.workflows.drain()
    assert (await _run(env, run["id"]))["status"] == "done"
    (req,) = seen
    assert req.url.host == "93.184.216.34" and req.headers["host"] == "hooks.example.com"
    ts = req.headers["x-evokai-timestamp"]
    mac = hmac.new(b"k", f"{ts}.".encode() + req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-evokai-signature"] == f"v1={mac}"
    assert req.headers["idempotency-key"] == f"{run['id']}:tell"
    for url in [
        "https://evil.example.com/x",
        "https://other.org/x",
        "http://127.0.0.1/x",
        "https://hooks.example.com.attacker.net/",
    ]:
        await _put(env, {"name": "n", "steps": [{"id": "tell", "kind": "notify", "url": url}]})
        run = await _start(env, "n")
        await env.svc.workflows.drain()
        detail = await _run(env, run["id"])
        assert detail["status"] == "failed" and detail["reason"] == "refused", url
    assert len(seen) == 1


async def test_cancel_stops_the_run_and_its_approvals(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "c",
            "steps": [
                {"id": "ask", "kind": "approval", "question": "?"},
                {"id": "w", "kind": "wait", "seconds": 0},
            ],
        },
    )
    run = await _start(env, "c")
    await env.svc.workflows.drain()
    r = await env.client.post(f"/agent/v1/workflows/runs/{run['id']}/cancel", headers=_srv(env))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert await _approvals(env) == []
    assert (
        await env.client.post(f"/agent/v1/workflows/runs/{run['id']}/cancel", headers=_srv(env))
    ).status_code == 409
    detail = await _run(env, run["id"])
    assert _steps(detail)["ask"]["status"] == "cancelled"


async def test_a_run_keeps_the_version_it_started_with(env: Env) -> None:
    await _setup(env)
    v1 = await _put(
        env,
        {
            "name": "v",
            "steps": [
                {"id": "ask", "kind": "approval", "question": "?", "on_reject": "continue"},
                {"id": "a", "kind": "condition", "if": "true"},
            ],
        },
    )
    run = await _start(env, "v")
    await env.svc.workflows.drain()
    v2 = await _put(
        env,
        {
            "name": "v",
            "steps": [
                {"id": "ask", "kind": "approval", "question": "?"},
                {"id": "b", "kind": "condition", "if": "false"},
            ],
        },
    )
    assert v1["version"] != v2["version"]
    (a,) = await _approvals(env)
    await _decide(env, a["id"], "reject")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert detail["version"] == v1["version"] and detail["status"] == "done"
    assert set(_steps(detail)) == {"ask", "a"}


async def test_grants_caps_and_the_guard_apply(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "g",
            "steps": [{"id": "classify", "kind": "agent", "agent": "triage", "input": "x"}],
        },
    )
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "workflow", "name": "*", "value": False}]},
        headers=_adm(env),
    )
    assert r.status_code == 200
    r = await env.client.post("/agent/v1/workflows/g/runs", json={}, headers=_srv(env))
    assert r.status_code == 403 and r.json()["error"] == "workflow_not_granted"
    await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "workflow", "name": "*", "value": None}]},
        headers=_adm(env),
    )

    # The application's cap stops an agent step for good (no retries).
    await env.svc.store.applications.update(env.app_id, monthly_cap_usd=0.0)
    run = await _start(env, "g")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert detail["status"] == "failed" and detail["reason"] == "limit"
    assert _steps(detail)["classify"]["attempt"] == 1
    await env.svc.store.applications.update(env.app_id, monthly_cap_usd=5.0)

    # The security policy's tool rules hold for tool steps too.
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"tools": {"deny": ["orders_month"]}, "modes": {"tools.policy": "enforce"}},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    await _put(
        env,
        {
            "name": "t",
            "steps": [{"id": "r", "kind": "tool", "server": "back", "tool": "orders_month"}],
        },
    )
    run = await _start(env, "t")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert detail["reason"] == "blocked" and env.mcp.calls == []

    # Agent steps are usage like server runs: the console sees a share, never an amount.
    env.script.add("function:primary", [Tool("final_result", {"category": "order"})])
    run = await _start(env, "g")
    await env.svc.workflows.drain()
    console = (
        await env.client.get(f"/agent/v1/admin/workflows/runs/{run['id']}", headers=_adm(env))
    ).json()
    assert "capPct" in console and "cost" not in json.dumps(console).lower()


async def test_definitions_with_problems_are_refused_with_their_paths(env: Env) -> None:
    await _setup(env)
    r = await env.client.put(
        "/agent/v1/admin/workflows/bad",
        json={"steps": [{"id": "x", "kind": "agent", "agent": "nobody", "input": "hi"}]},
        headers=_adm(env),
    )
    assert r.status_code == 400 and r.json()["problems"] == ["steps[0].agent: unknown agent nobody"]
    r = await env.client.post(
        "/agent/v1/admin/workflows/validate",
        json={
            "text": (
                "name: v\nsteps:\n  - id: a\n    kind: agent\n    agent: helper\n    input: hi\n"
            )
        },
        headers=_adm(env),
    )
    body = r.json()
    assert body["ok"] is False and body["problems"][0]["line"] == 5  # helper takes no server runs
    assert body["outline"][0]["id"] == "a"


async def test_runs_of_a_subject_are_erased_and_kept_masked_when_pii_history_is_enforced(
    env: Env,
) -> None:
    await _setup(env)
    await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"pii.history": "enforce"}}, headers=_adm(env)
    )
    await _put(
        env,
        {
            "name": "p",
            "steps": [{"id": "w", "kind": "wait", "seconds": 0}],
            "output": "Mail {{ input.email }}",
        },
    )
    run = await _start(env, "p", input={"email": "mario.rossi@example.com"}, subject="cust-1")
    other = await _start(env, "p", input={"email": "x"}, subject="cust-2")
    await env.svc.workflows.drain()
    detail = await _run(env, run["id"])
    assert "mario.rossi" not in json.dumps(detail) and detail["masked"] is True
    exported = await env.client.post(
        "/agent/v1/privacy/export", json={"subject": "cust-1"}, headers=_srv(env)
    )
    assert [x["id"] for x in exported.json()["workflowRuns"]] == [run["id"]]
    r = await env.client.post(
        "/agent/v1/privacy/erase", json={"subject": "cust-1"}, headers=_srv(env)
    )
    assert r.json()["workflowRuns"] == 1
    assert (
        await env.client.get(f"/agent/v1/workflows/runs/{run['id']}", headers=_srv(env))
    ).status_code == 404
    assert (await _run(env, other["id"]))["status"] == "done"


async def test_finished_runs_go_after_their_retention(env: Env) -> None:
    await _setup(env)
    await _put(
        env,
        {
            "name": "short",
            "retention_days": 1,
            "steps": [{"id": "w", "kind": "wait", "seconds": 0}],
        },
    )
    run = await _start(env, "short")
    await env.svc.workflows.drain()
    assert await env.svc.store.workflows.purge(datetime.now(UTC) + timedelta(hours=12)) == 0
    assert await env.svc.store.workflows.purge(datetime.now(UTC) + timedelta(days=2)) == 1
    assert (
        await env.client.get(f"/agent/v1/workflows/runs/{run['id']}", headers=_srv(env))
    ).status_code == 404


async def test_each_run_is_a_trace_with_step_spans(env: Env) -> None:
    await _setup(env)
    await _put(env, MAIL)
    env.script.add("function:primary", [Tool("final_result", {"category": "technical"})])
    run = await _start(env, "mail", input={"text": "Book missing"})
    await env.svc.workflows.drain()
    r = await env.client.get(f"/agent/v1/admin/traces/{run['id']}", headers=_adm(env))
    assert r.status_code == 200, r.text
    trace = r.json()
    assert trace["kind"] == "workflow" and trace["status"] == "done"
    spans = trace["spans"]
    assert spans[0]["kind"] == "workflow" and spans[0]["name"] == "workflow mail"
    steps = [s for s in spans if s["kind"] == "step"]
    assert [s["attrs"]["step"] for s in steps] == ["classify", "route", "skip"]
    agent_run = steps[0]["attrs"]["runId"]
    # The agent step's own server run has its trace, linked from the step span.
    assert (await env.client.get(f"/agent/v1/admin/traces/{agent_run}", headers=_adm(env))).json()[
        "kind"
    ] == "run"


async def test_the_config_file_brings_workflows(env: Env, tmp_path: Path) -> None:
    from evokai.agents.bootstrap import bootstrap

    (tmp_path / "wf.yaml").write_text(
        """
application: {slug: test, name: Test App}
agents:
  - name: triage
    model: "function:primary"
    channels: [server]
    instructions: Classify.
workflows:
  - name: nightly-report
    triggers: [{kind: schedule, cron: "0 3 * * *"}]
    steps:
      - {id: classify, kind: agent, agent: triage, input: "Report of {{ now.date }}"}
""",
        encoding="utf-8",
    )
    await bootstrap(env.svc.store, env.svc.keyring, tmp_path / "wf.yaml", {})
    r = await env.client.get("/agent/v1/admin/workflows", headers=_adm(env))
    (item,) = r.json()["items"]
    assert item["name"] == "nightly-report" and item["triggers"][0]["nextAt"]
    assert item["createdBy"] == "config"
