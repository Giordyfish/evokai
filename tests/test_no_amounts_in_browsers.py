"""Browsers see usage as percentages only: no amounts or currencies in session data or events."""

from __future__ import annotations

import json
import re

from tests.conftest import ORIGIN, Env, Tool

MONEY = re.compile(r"usd|eur|cost|price|amount|spend|€|\$", re.IGNORECASE)


def _keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [*value, *(k2 for v in value.values() for k2 in _keys(v))]
    if isinstance(value, list):
        return [k for v in value for k in _keys(v)]
    return []


async def test_session_events_and_info_carry_shares_not_amounts(env: Env) -> None:
    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["Ok."])
    tok = await env.token(limits={"subject_daily_usd": 0.5, "subject_monthly_usd": 5})
    events = await env.chat(tok["token"], text="ottobre?")
    usage = next(e for e in events if e["type"] == "usage")
    assert set(usage) >= {"dayPct", "monthPct", "companyPct", "monthOut"}
    headers = {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    info = (await env.client.get("/agent/v1/session", headers=headers)).json()
    replay = (await env.client.get("/agent/v1/session/events?after=0", headers=headers)).json()
    conversation = (await env.client.get("/agent/v1/session/conversation", headers=headers)).json()
    for payload in (events, info, replay, conversation):
        assert not [k for k in _keys(payload) if MONEY.search(k)], json.dumps(payload)[:400]


async def test_the_console_gets_shares_of_the_caps_never_amounts(env: Env) -> None:
    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["Ok."])
    tok = await env.token()
    await env.chat(tok["token"], text="ottobre?")
    console = {"X-Evokai-Console": "1"}
    login = await env.client.post("/agent/v1/console/login", json={"key": env.admin_key})
    assert login.status_code == 204
    payloads = [(await env.client.get("/agent/v1/console/applications", headers=console)).json()]
    for group in ("day", "subject,tags", "agent", "model", "kind"):
        r = await env.client.get("/agent/v1/usage", params={"groupBy": group}, headers=console)
        assert r.status_code == 200, r.text
        payloads.append(r.json())
    summary = await env.client.get("/agent/v1/admin/usage/summary", headers=console)
    payloads.append(summary.json())
    items = (await env.client.get("/agent/v1/admin/conversations", headers=console)).json()
    detail = await env.client.get(
        f"/agent/v1/admin/conversations/{items['items'][0]['id']}", headers=console
    )
    payloads.append(detail.json())
    for payload in payloads:
        assert not [k for k in _keys(payload) if MONEY.search(k)], json.dumps(payload)[:400]

    by_subject = payloads[2]
    (row,) = by_subject["rows"]
    assert row["subject"] == "u1" and row["capPct"] > 0 and row["inputTokens"] > 0
    assert "subjectTodayPct" in row and "subjectCapPct" in row
    app = by_subject["application"]
    assert app["monthPct"] > 0 and abs(app["monthPct"] + app["monthLeftPct"] - 100) < 0.01
    assert by_subject["caps"]["monthly"] is True
    turn = detail.json()["turns"][0]
    assert "capPct" in turn["timings"]

    # The application's backend, with its server key, still reads amounts (server to server).
    srv = {"Authorization": f"Bearer {env.server_key}"}
    amounts = (await env.client.get("/agent/v1/usage", headers=srv)).json()
    assert amounts["rows"][0]["costUsd"] > 0 and "monthUsd" in amounts["application"]
