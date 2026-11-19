"""Cost governance: forecasts, alerts that fire once, webhooks, model recommendations, caching."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from evokai.core.caps import window
from evokai.costs import alerts as al
from evokai.costs.forecast import fit_line, forecast, weekday_factors
from evokai.costs.recommend import ModelResult, cache_suggestion, pick_candidates, recommend
from evokai.db.repositories import Who
from tests.conftest import Env

MONEY = re.compile(r"usd|eur|cost(?!Pct)|price|amount|spend|€|\$", re.IGNORECASE)


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _settings(env: Env, **fields: Any) -> None:
    r = await env.client.patch("/agent/v1/admin/application", json=fields, headers=_adm(env))
    assert r.status_code == 200, r.text


def _keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [*value, *(k for v in value.values() for k in _keys(v))]
    if isinstance(value, list):
        return [k for v in value for k in _keys(v)]
    return []


# ── forecast math ────────────────────────────────────────────────────────────────────────────────


def _series(start: date, values: list[float]) -> dict[date, float]:
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def test_a_flat_series_forecasts_the_same_rate_for_the_days_left() -> None:
    now = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)  # the 16th has just begun: 15 days left
    daily = _series(date(2026, 10, 19), [1.0] * 28)
    f = forecast(daily, now)
    assert f.so_far == pytest.approx(15.0)
    assert f.days_left == 15
    assert f.total == pytest.approx(30.0)
    assert f.trend == "flat" and f.low == pytest.approx(f.high)


def test_the_part_of_today_already_gone_is_not_projected_again() -> None:
    noon = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    daily = _series(date(2026, 10, 19), [1.0] * 28) | {date(2026, 11, 16): 0.5}
    f = forecast(daily, noon)
    assert f.so_far == pytest.approx(15.5)
    assert f.total == pytest.approx(15.5 + 0.5 + 14.0)


def test_a_linear_trend_is_extrapolated() -> None:
    now = datetime(2026, 11, 29, 0, 0, tzinfo=UTC)
    start = date(2026, 11, 1)
    daily = _series(start, [float(i + 1) for i in range(28)])  # 1, 2, ... 28 on the 28th
    f = forecast(daily, now)
    # Weekday factors of a series with no seasonality stay close to 1.
    assert f.trend == "rising" and f.slope == pytest.approx(1.0, rel=0.05)
    # 29th and 30th continue the line: about 29 + 30.
    assert f.total == pytest.approx(sum(range(1, 29)) + 29 + 30, rel=0.03)


def test_weekday_seasonality_keeps_quiet_weekends_quiet() -> None:
    start = date(2026, 10, 19)  # a Monday
    values = [0.0 if (start + timedelta(days=i)).weekday() >= 5 else 2.0 for i in range(28)]
    factors = weekday_factors(_series(start, values))
    assert factors[5] < 0.5 < 1.0 < factors[0]
    now = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)  # Monday
    f = forecast(_series(start, values), now)
    saturday = date(2026, 11, 21)
    assert f.projection[saturday] < f.projection[date(2026, 11, 16)] / 2
    weekdays_left = sum(1 for d in f.projection if d.weekday() < 5)
    assert (
        f.total - f.so_far == pytest.approx(sum(f.projection.values()))
        and f.total - f.so_far < weekdays_left * 2.0 + 1
    )


def test_few_days_fit_a_flat_line_and_nothing_gives_nothing() -> None:
    assert fit_line([(0, 1.0), (1, 3.0)])[1] == pytest.approx(0)
    f = forecast({}, datetime(2026, 11, 16, tzinfo=UTC))
    assert f.total == pytest.approx(0) and f.so_far == pytest.approx(0)


# ── alert thresholds ─────────────────────────────────────────────────────────────────────────────


def test_thresholds_cross_once_and_in_order() -> None:
    assert al.crossed(92.0, [75, 90, 100], set()) == [75, 90]
    assert al.crossed(92.0, [75, 90, 100], {75, 90}) == []
    assert al.crossed(None, [75], set()) == []
    assert al.thresholds_of({"cost_alert_thresholds": [100, 50, 50]}) == [50, 100]
    assert al.thresholds_of({}) == [75, 90, 100]


def test_webhook_addresses_must_be_https_and_allowed() -> None:
    allow = ["hooks.example.org"]
    assert al.check_webhook("https://x.hooks.example.org/a", allow)
    for bad in (
        "http://hooks.example.org/a",
        "https://evil.example.net/a",
        "https://u:p@hooks.example.org",
    ):
        with pytest.raises(Exception):  # ruff: ignore[assert-raises-exception]
            al.check_webhook(bad, allow)
    with pytest.raises(Exception):  # ruff: ignore[assert-raises-exception]
        al.check_webhook("https://hooks.example.org/a", [])


async def test_a_delivery_is_signed_and_never_reaches_a_private_address() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def public_dns(_host: str, _port: int) -> list[str]:
        await asyncio.sleep(0)
        return ["93.184.216.34"]

    async def private_dns(_host: str, _port: int) -> list[str]:
        await asyncio.sleep(0)
        return ["10.0.0.5"]

    url, allow = "https://hooks.example.org/evokai", ["example.org"]
    payload = {"threshold": 90, "forecastPct": 93.0}
    out = await al.deliver(http, url, "s3cret", payload, allow, resolver=public_dns)
    assert out["status"] == 204
    (req,) = seen
    ts = req.headers["X-Evokai-Timestamp"]
    expected = hmac.new(b"s3cret", f"{ts}.".encode() + req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-Evokai-Signature"] == f"sha256={expected}"
    assert json.loads(req.content) == payload
    assert (await al.deliver(http, url, None, payload, allow, resolver=public_dns))[
        "status"
    ] == "no_secret"
    refused = await al.deliver(http, url, "s3cret", payload, allow, resolver=private_dns)
    assert refused["status"] == "refused" and len(seen) == 1
    await http.aclose()


async def _usage(env: Env, subject: str, usd: float, when: datetime) -> None:
    await env.svc.store.usage.record(
        Who(env.app_id, subject),
        [],
        kind="llm",
        model="m",
        cost_micro_usd=int(usd * 1e6),
        created_at=when,
    )


async def test_alerts_fire_once_per_threshold_and_month_and_carry_shares_only(env: Env) -> None:
    await _settings(env, monthly_cap_usd=10, subject_monthly_usd=1, cost_alert_thresholds=[50, 90])
    now = datetime.now(UTC)
    for i in range(1, 15):
        await _usage(env, "u1", 0.40, now - timedelta(days=i))
    app = await env.svc.store.applications.get(env.app_id)
    view = await env.svc.costs.forecast(app)
    assert view["application"]["forecastPct"] > 90
    first = await env.svc.costs.evaluate(app, view)
    scopes = sorted((a.scope.split(":")[0], a.threshold) for a in first)
    assert ("application", 50) in scopes and ("application", 90) in scopes
    assert ("subject", 90) in scopes  # u1 against their own monthly cap
    assert await env.svc.costs.evaluate(app, view) == []  # never twice
    assert await env.svc.costs.check_alerts() == 0
    r = await env.client.get("/agent/v1/admin/costs/forecast", headers=_adm(env))
    body = r.json()
    assert r.status_code == 200 and len(body["alerts"]) == len(first)
    assert body["subjects"][0]["cap"] == "subject"
    assert not [k for k in _keys(body) if MONEY.search(k)]
    open_ = await env.client.get("/agent/v1/admin/costs/alerts?open=true", headers=_adm(env))
    alert_id = open_.json()["items"][0]["id"]
    ack = await env.client.post(f"/agent/v1/admin/costs/alerts/{alert_id}/ack", headers=_adm(env))
    assert ack.status_code == 200
    left = (
        await env.client.get("/agent/v1/admin/costs/alerts?open=true", headers=_adm(env))
    ).json()
    assert alert_id not in {a["id"] for a in left["items"]}


async def test_alerts_follow_weekly_caps_in_another_currency_once_per_window(env: Env) -> None:
    await _settings(
        env,
        timezone="Europe/Rome",
        currency="EUR",
        exchange_rate=0.5,
        cost_alert_thresholds=[50],
        caps={
            "application": [{"period": "week", "amount": 2, "resets": "saturday"}],
            "subject": [{"period": "week", "amount": 0.5, "resets": "saturday"}],
        },
    )
    app = await env.svc.store.applications.get(env.app_id)
    assert app is not None
    now = datetime.now(UTC)
    # 2 EUR at 0.5 EUR per USD is 4 USD a week; 2.5 USD spent since the reset is 62.5%.
    start, _ = window("week", 5, now, ZoneInfo("Europe/Rome"))
    await _usage(env, "u1", 2.5, max(start + timedelta(hours=2), now - timedelta(minutes=1)))
    await _usage(env, "u1", 9.0, start - timedelta(days=2))  # the week before: not counted
    view = await env.svc.costs.forecast(app, now)
    week = next(c for c in view["caps"] if c["limit"] == "app_week")
    assert week["usedPct"] == pytest.approx(62.5)
    assert week["window"].endswith("w") and week["resets"] == "saturday"
    fired = await env.svc.costs.evaluate(app, view, now)
    assert {(a.scope.split(":")[0], a.period) for a in fired} >= {("application", week["window"])}
    assert any(a.scope.startswith("subject:") for a in fired)  # 2.5 USD of a 1 USD week
    assert await env.svc.costs.evaluate(app, view, now) == []
    body = (await env.client.get("/agent/v1/admin/costs/forecast", headers=_adm(env))).json()
    assert "week" in {a["cap"] for a in body["alerts"]}
    assert not [k for k in _keys(body) if MONEY.search(k)]


async def test_webhook_settings_are_checked_and_the_secret_never_comes_back(env: Env) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"cost_alert_webhook": "https://hooks.example.org/x"},
        headers=_adm(env),
    )
    assert r.status_code == 400 and r.json()["error"] == "webhook_domain"
    env.svc.settings.alert_webhook_allow = ["example.org"]
    await _settings(
        env, cost_alert_webhook="https://hooks.example.org/x", cost_alert_secret="s3cret"
    )
    app = (await env.client.get("/agent/v1/admin/application", headers=_adm(env))).json()
    assert app["cost_alert_webhook"] == "https://hooks.example.org/x" and app["hasCostAlertSecret"]
    assert "s3cret" not in json.dumps(app)


# ── the cheapest model that passes ───────────────────────────────────────────────────────────────


def test_the_recommendation_is_the_cheapest_model_within_the_tolerance() -> None:
    current = ModelResult("bedrock:eu.anthropic.claude-sonnet-4-6", 90.0, 10_000, 1800, 10, 0)
    others = [
        ModelResult("bedrock:eu.anthropic.claude-haiku-5-5", 80.0, 800, 900, 10, 0),  # too weak
        ModelResult("bedrock:eu.amazon.nova-2-lite-v1:0", 86.0, 1_500, 700, 10, 0),  # passes
        ModelResult(
            "bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0", 90.0, 4_000, 1000, 10, 0
        ),
    ]
    out = recommend(current, others, tolerance=5)
    assert out["recommended"] == "bedrock:eu.amazon.nova-2-lite-v1:0"
    rows = {r["model"]: r for r in out["models"]}
    assert rows["bedrock:eu.amazon.nova-2-lite-v1:0"]["relativeCostPct"] == pytest.approx(15)
    assert rows["bedrock:eu.anthropic.claude-haiku-5-5"]["passes"] is False
    assert (
        rows[current.model]["relativeCostPct"] == pytest.approx(100)
        and rows[current.model]["current"]
    )
    assert recommend(current, others, tolerance=0)["recommended"] == (
        "bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    assert recommend(current, [], tolerance=5)["recommended"] is None
    broken = ModelResult("bedrock:eu.x", 0.0, 0, 100, 10, 10)
    assert recommend(current, [broken])["models"][-1]["relativeCostPct"] is None
    assert not [k for k in _keys(out) if MONEY.search(k)]


def test_candidates_are_the_models_cheaper_by_list_price() -> None:
    pool = [
        "bedrock:eu.anthropic.claude-sonnet-4-6",
        "bedrock:eu.anthropic.claude-haiku-5-5",
        "bedrock:eu.amazon.nova-2-lite-v1:0",
        "bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    ]
    out = pick_candidates("bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0", pool)
    assert out == ["bedrock:eu.anthropic.claude-haiku-5-5", "bedrock:eu.amazon.nova-2-lite-v1:0"]


async def _wait_models(env: Env) -> dict[str, Any]:
    for _ in range(500):
        r = await env.client.get(
            "/agent/v1/admin/agents/helper/model-recommendations", headers=_adm(env)
        )
        latest = r.json()["latest"]
        if latest and latest["status"] != "running":
            return r.json()
        await asyncio.sleep(0.02)
    raise AssertionError("the comparison did not end")


async def test_a_comparison_runs_the_cases_on_each_model_and_one_click_switches(env: Env) -> None:
    r = await env.client.put(
        "/agent/v1/admin/agents/helper/evals/cases/hello",
        json={"input": "Say hello", "checks": [{"type": "contains", "value": "hello"}]},
        headers=_adm(env),
    )
    assert r.status_code == 200
    env.script.add("function:primary", *([["hello there, a long and costly answer " * 5]] * 5))
    env.script.add("bedrock:eu.anthropic.claude-haiku-5-5", *([["hello"]] * 5))
    env.script.add("bedrock:eu.amazon.nova-2-lite-v1:0", *([["no idea"]] * 5))
    r = await env.client.post(
        "/agent/v1/admin/agents/helper/model-recommendations", json={}, headers=_adm(env)
    )
    assert r.status_code == 202, r.text
    assert r.json()["current"] == "function:primary" and r.json()["candidates"]
    body = await _wait_models(env)
    latest = body["latest"]
    assert latest["status"] == "done"
    rows = {x["model"]: x for x in latest["models"]}
    assert rows["bedrock:eu.anthropic.claude-haiku-5-5"]["passes"] is True
    assert rows["bedrock:eu.amazon.nova-2-lite-v1:0"]["passes"] is False
    assert latest["recommended"] == "bedrock:eu.anthropic.claude-haiku-5-5"
    assert rows["bedrock:eu.anthropic.claude-haiku-5-5"]["relativeCostPct"] < 100
    assert not [k for k in _keys(body) if MONEY.search(k)]
    switched = await env.client.post(
        "/agent/v1/admin/agents/helper/switch-model",
        json={"model": latest["recommended"]},
        headers=_adm(env),
    )
    assert switched.status_code == 200 and switched.json()["created"]
    active = await env.svc.store.agents.active(env.app_id, "helper")
    assert (
        active is not None and active.definition["model"] == "bedrock:eu.anthropic.claude-haiku-5-5"
    )
    refused = await env.client.post(
        "/agent/v1/admin/agents/helper/switch-model",
        json={"model": "openai:gpt-5"},
        headers=_adm(env),
    )
    assert refused.status_code == 400


async def test_a_comparison_needs_cases(env: Env) -> None:
    r = await env.client.post(
        "/agent/v1/admin/agents/helper/model-recommendations", json={}, headers=_adm(env)
    )
    assert r.status_code == 400 and r.json()["error"] == "no_cases"


# ── prompt cache ─────────────────────────────────────────────────────────────────────────────────


def test_a_long_stable_prompt_without_cache_is_flagged() -> None:
    hint = cache_suggestion(
        agent="a",
        model="bedrock:eu.anthropic.claude-sonnet-4-6",
        prompt_cache="off",
        stable_chars=7_000,
        avg_input_tokens=4_000,
    )
    assert (
        hint is not None
        and hint["stableTokens"] == 2000
        and hint["inputSharePct"] == pytest.approx(50)
    )
    assert (
        cache_suggestion(
            agent="a",
            model="bedrock:eu.anthropic.claude-haiku-5-5",
            prompt_cache="off",
            stable_chars=7_000,
            avg_input_tokens=None,
        )
        is None
    )  # Haiku caches 4,096 tokens or more
    assert (
        cache_suggestion(
            agent="a",
            model="bedrock:eu.anthropic.claude-sonnet-4-6",
            prompt_cache="5m",
            stable_chars=70_000,
            avg_input_tokens=None,
        )
        is None
    )
    assert (
        cache_suggestion(
            agent="a",
            model="bedrock:eu.amazon.nova-2-lite-v1:0",
            prompt_cache="off",
            stable_chars=70_000,
            avg_input_tokens=None,
        )
        is None
    )


async def test_cache_suggestions_list_the_agents_that_would_gain(env: Env) -> None:
    body = {
        "name": "long",
        "model": "bedrock:eu.anthropic.claude-sonnet-4-6",
        "instructions": "Explain the procedure carefully. " * 300,
        "skills": [],
    }
    r = await env.client.put("/agent/v1/admin/agents/long", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text
    r = await env.client.get("/agent/v1/admin/costs/cache-suggestions", headers=_adm(env))
    assert [x["agent"] for x in r.json()["items"]] == ["long"]
