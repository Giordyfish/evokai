"""AI usage limits: Evokai decides (application caps, per-subject defaults); browsers see shares.

Caps have fixed calendar windows (day, week from a weekday, month from a day of the month) in the
application's time zone, and an amount in the application's currency, converted from the USD the
spend is recorded in with the application's exchange rate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from evokai.agents.bootstrap import bootstrap, bootstrap_apps
from evokai.core.caps import CapSpec, CapsSpec, caps_of, check_currency, window
from evokai.core.config import BootstrapApp, BootstrapCaps
from evokai.core.errors import ConfigError
from evokai.db.repositories import Who
from evokai.sessions.limits import CapState, CapUse, cap_state, limit_message
from tests.conftest import ORIGIN, Env

ADMIN = "/agent/v1/admin/application"
ROME = ZoneInfo("Europe/Rome")


async def _spent(env: Env, subject: str, usd: float, at: datetime | None = None) -> None:
    extra = {"created_at": at} if at else {}
    await env.svc.store.usage.record(
        Who(env.app_id, subject),
        [],
        kind="model",
        model="x",
        cost_micro_usd=round(usd * 1e6),
        **extra,
    )


async def _patch(env: Env, status: int = 200, **body: Any) -> dict[str, Any]:
    r = await env.client.patch(
        ADMIN, json=body, headers={"Authorization": f"Bearer {env.admin_key}"}
    )
    assert r.status_code == status, r.text
    return r.json()


async def _app(env: Env) -> dict[str, Any]:
    r = await env.client.get(ADMIN, headers={"Authorization": f"Bearer {env.admin_key}"})
    return r.json()


async def _limit(env: Env, token: str, **body: Any) -> dict[str, Any]:
    ev = await env.chat(token, text="Ciao", **body)
    return next(e for e in ev if e["type"] == "error")


def _local(d: str) -> datetime:
    return datetime.fromisoformat(d).replace(tzinfo=ROME)


# ── windows ─────────────────────────────────────────────────────────────────────────────────────


def test_a_week_resets_on_its_weekday_at_local_midnight_across_a_dst_change() -> None:
    saturday = 5
    # The week of Saturday 24 October 2026 starts in summer time in Rome (CEST, UTC+2); the clocks
    # go back on Sunday 25 October, so the next Saturday starts at 00:00 CET (UTC+1).
    start, end = window("week", saturday, _local("2026-10-28T15:00"), ROME)
    assert start == datetime(2026, 10, 23, 22, 0, tzinfo=UTC)  # Sat 24 Oct 00:00 CEST
    assert end == datetime(2026, 10, 30, 23, 0, tzinfo=UTC)  # Sat 31 Oct 00:00 CET
    assert end - start == timedelta(days=7, hours=1)
    assert start.astimezone(ROME).hour == end.astimezone(ROME).hour == 0
    # A second before midnight belongs to the old week, midnight to the new one.
    before = window("week", saturday, end - timedelta(seconds=1), ROME)
    assert before == (start, end)
    after = window("week", saturday, end, ROME)
    assert after[0] == end and after[1] == datetime(2026, 11, 6, 23, 0, tzinfo=UTC)
    # In spring the clocks go forward (Sunday 29 March 2026): that week is an hour shorter.
    spring = window("week", saturday, _local("2026-03-30T09:00"), ROME)
    assert spring[1] - spring[0] == timedelta(days=7, hours=-1)
    # On the reset day itself, after midnight, the window has just started.
    same = window("week", saturday, _local("2026-10-31T00:30"), ROME)
    assert same[0] == end


def test_days_and_months_reset_at_local_midnight() -> None:
    day = window("day", None, datetime(2026, 11, 16, 23, 30, tzinfo=UTC), ROME)
    # 23:30 UTC is already 00:30 of the 17th in Rome.
    assert day == (
        datetime(2026, 11, 16, 23, 0, tzinfo=UTC),
        datetime(2026, 11, 17, 23, 0, tzinfo=UTC),
    )
    month = window("month", 1, _local("2026-11-16T12:00"), ROME)
    assert month == (
        datetime(2026, 10, 31, 23, 0, tzinfo=UTC),
        datetime(2026, 11, 30, 23, 0, tzinfo=UTC),
    )
    # A reset day the month does not have is its last day.
    feb = window("month", 31, _local("2027-02-10T12:00"), ROME)
    assert feb[0].astimezone(ROME).date().isoformat() == "2027-01-31"
    assert feb[1].astimezone(ROME).date().isoformat() == "2027-02-28"
    mid = window("month", 15, _local("2026-12-20T12:00"), ROME)
    assert [x.astimezone(ROME).date().isoformat() for x in mid] == ["2026-12-15", "2027-01-15"]


def test_cap_specs_validate_periods_resets_and_one_cap_per_period() -> None:
    assert CapSpec(period="week", amount=5, resets="Saturday").resets == "saturday"
    assert CapSpec(period="month", amount=5, resets="15").resets == 15
    for bad in (
        {"period": "day", "amount": 1, "resets": "monday"},
        {"period": "week", "amount": 1, "resets": "someday"},
        {"period": "month", "amount": 1, "resets": 32},
        {"period": "year", "amount": 1},
        {"period": "day", "amount": -1},
    ):
        with pytest.raises(ValidationError):
            CapSpec.model_validate(bad)
    with pytest.raises(ValidationError):
        CapsSpec.model_validate({"subject": [{"period": "day", "amount": 1}] * 2})
    stored = CapsSpec.model_validate({"application": [{"period": "week", "amount": 3}]}).stored()
    assert stored == {
        "application": [{"period": "week", "amount": 3, "resets": "monday"}],
        "subject": [],
    }


# ── currency ────────────────────────────────────────────────────────────────────────────────────


def test_caps_in_another_currency_are_converted_to_usd_with_the_rate() -> None:
    app = SimpleNamespace(
        currency="EUR",
        exchange_rate=0.9,
        caps={
            "application": [{"period": "month", "amount": 9, "resets": 1}],
            "subject": [{"period": "week", "amount": 4.5, "resets": "saturday"}],
        },
        monthly_cap_usd=10.0,
        daily_cap_usd=2.0,
        defaults={"subject_daily_usd": 1},
    )
    caps = {c.kind: c for c in caps_of(app)}
    # The new month (9 EUR = 10 USD) replaces the old monthly_cap_usd; the old day stays in USD.
    assert caps["app_month"].usd == pytest.approx(10) and caps["app_month"].source == "caps"
    assert (caps["app_day"].usd, caps["app_day"].currency) == (2.0, "USD")
    assert caps["subject_week"].usd == pytest.approx(5) and caps["subject_week"].resets == 5
    assert caps["subject_day"].source == "legacy"
    # A session token overrides the subject's caps; a week without resets keeps the default's.
    token = {"subject_daily_usd": 3, "caps": [{"period": "week", "amount": 9}]}
    tok = {c.kind: c for c in caps_of(app, token)}
    assert tok["subject_day"].usd == 3 and tok["subject_day"].source == "token"
    assert tok["subject_week"].usd == pytest.approx(10) and tok["subject_week"].resets == 5
    # Without a rate a cap in another currency cannot be converted: it blocks.
    broken = SimpleNamespace(**{**app.__dict__, "exchange_rate": None})
    assert {c.kind: c for c in caps_of(broken)}["app_month"].usd == 0


def test_a_currency_needs_an_iso_code_and_a_positive_rate() -> None:
    check_currency("USD", None)
    check_currency("EUR", 0.92)
    for currency, rate in (("EUR", None), ("eur", 1.0), ("EURO", 1.0), ("EUR", 0), ("USD", -1)):
        with pytest.raises(ValueError):
            check_currency(currency, rate)


async def test_a_cap_in_euro_stops_at_its_converted_amount(env: Env) -> None:
    await _patch(
        env,
        currency="EUR",
        exchange_rate=0.5,
        caps={"application": [{"period": "month", "amount": 1}]},
    )
    # 1 EUR at 0.5 EUR per USD is 2 USD: 1.9 USD fits a small call, 2 USD do not.
    await _spent(env, "u1", 1.9)
    env.script.add("function:primary", ["Ok."])
    ev = await env.chat((await env.token())["token"], text="Ciao")
    assert ev[-1]["reason"] == "end_turn"
    await _spent(env, "u1", 0.2)
    err = await _limit(env, (await env.token())["token"])
    assert err["limit"] == "app_month"
    # The server-to-server report says which rate converted its amounts.
    srv = {"Authorization": f"Bearer {env.server_key}"}
    report = (await env.client.get("/agent/v1/usage", headers=srv)).json()["application"]
    assert (report["currency"], report["exchangeRate"]) == ("EUR", 0.5)
    month = next(c for c in report["caps"] if c["kind"] == "app_month")
    assert (
        month["amount"] == 1
        and month["capUsd"] == 2
        and month["spent"] == pytest.approx(month["spentUsd"] * 0.5)
    )


async def test_the_admin_api_refuses_a_currency_without_rate(env: Env) -> None:
    body = await _patch(env, 400, currency="EUR")
    assert body["error"] == "currency"
    await _patch(env, currency="EUR", exchange_rate=0.9048)
    await _patch(env, 400, exchange_rate=None)
    await _patch(env, 400, exchange_rate=0)
    await _patch(env, 400, timezone="Europe/Nowhere")
    app = await _app(env)
    assert (app["currency"], app["exchange_rate"]) == ("EUR", 0.9048)


# ── old fields and the new form ─────────────────────────────────────────────────────────────────


async def test_old_and_new_forms_of_the_same_cap_are_refused(env: Env) -> None:
    await _patch(
        env,
        400,
        daily_cap_usd=1,
        caps={"application": [{"period": "day", "amount": 1}]},
    )
    await _patch(
        env, 400, subject_monthly_usd=1, caps={"subject": [{"period": "month", "amount": 1}]}
    )
    # Different caps in the two forms coexist.
    await _patch(env, daily_cap_usd=1, caps={"subject": [{"period": "week", "amount": 2}]})
    kinds = {c["kind"]: c["source"] for c in (await _app(env))["effectiveCaps"]}
    assert kinds == {"app_month": "legacy", "app_day": "legacy", "subject_week": "caps"}
    # Moving a cap to the new form clears its old field.
    await _patch(env, caps={"application": [{"period": "day", "amount": 1}]})
    app = await _app(env)
    assert app["daily_cap_usd"] is None
    assert {c["kind"]: c["source"] for c in app["effectiveCaps"]} == {
        "app_month": "legacy",
        "app_day": "caps",
    }
    # An old field whose cap the stored caps set would not apply: refused.
    await _patch(env, 400, daily_cap_usd=3)
    # A token sets one form per period.
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={
            "subject": "u1",
            "agent": "helper",
            "limits": {"subject_daily_usd": 1, "caps": [{"period": "day", "amount": 1}]},
        },
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 400


async def test_a_config_file_that_sets_a_cap_twice_is_refused(env: Env, tmp_path: Any) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "application:\n  slug: test\n  name: Test App\n  monthly_cap_usd: 5\n"
        "  caps:\n    application: [{period: month, amount: 9}]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="monthly_cap_usd"):
        await bootstrap(env.svc.store, env.svc.keyring, bad, {})
    eur = tmp_path / "eur.yaml"
    eur.write_text(
        "application:\n  slug: test\n  name: Test App\n  currency: EUR\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="exchange_rate"):
        await bootstrap(env.svc.store, env.svc.keyring, eur, {})
    good = tmp_path / "good.yaml"
    good.write_text(
        "application:\n  slug: test\n  name: Test App\n  timezone: Europe/Rome\n"
        "  currency: EUR\n  exchange_rate: 0.9048\n  caps:\n"
        "    application: [{period: month, amount: 9}]\n"
        "    subject: [{period: week, amount: 5, resets: saturday}]\n",
        encoding="utf-8",
    )
    await bootstrap(env.svc.store, env.svc.keyring, good, {})
    row = await env.svc.store.applications.get(env.app_id)
    assert row is not None and (row.currency, row.exchange_rate) == ("EUR", 0.9048)
    assert row.caps == {
        "application": [{"period": "month", "amount": 9, "resets": 1}],
        "subject": [{"period": "week", "amount": 5, "resets": "saturday"}],
    }


async def test_application_month_comes_first_with_the_app_name(env: Env) -> None:
    await _patch(env, monthly_cap_usd=1.0, subject_monthly_usd=0.5, subject_daily_usd=0.1)
    await _spent(env, "u1", 1.0)
    err = await _limit(env, (await env.token())["token"])
    assert (err["code"], err["limit"]) == ("limit", "app_month")
    assert err["message"].startswith("The assistant's monthly limit for Test App is reached:")
    assert err["resetsAt"].endswith("+00:00")
    assert env.script.seen == []


async def test_subject_month_from_the_application_defaults(env: Env) -> None:
    await _patch(env, subject_monthly_usd=0.5, subject_daily_usd=5)
    await _spent(env, "u1", 0.5)
    err = await _limit(env, (await env.token())["token"])
    assert err["limit"] == "subject_month"
    assert err["message"].startswith("You have reached your monthly assistant limit: it resets on")
    # Another subject is not blocked.
    env.script.add("function:primary", ["Ciao."])
    ev = await env.chat((await env.token("u2"))["token"], text="Ciao")
    assert ev[-1]["reason"] == "end_turn"


async def test_subject_day_and_a_token_override(env: Env) -> None:
    await _patch(env, subject_daily_usd=0.2)
    await _spent(env, "u1", 0.2)
    err = await _limit(env, (await env.token())["token"])
    assert err["limit"] == "subject_day"
    assert (
        err["message"] == "You have reached today's assistant limit: it resets tomorrow at 00:00."
    )
    env.script.add("function:primary", ["Ok."])
    raised = await env.token(limits={"subject_daily_usd": 5})
    ev = await env.chat(raised["token"], text="Ciao")
    assert ev[-1]["reason"] == "end_turn"


async def test_a_weekly_subject_cap_says_when_it_resets_in_italian_and_aloud(env: Env) -> None:
    await _patch(
        env,
        locale="it",
        timezone="Europe/Rome",
        caps={"subject": [{"period": "week", "amount": 0.3, "resets": "saturday"}]},
    )
    await _spent(env, "u1", 0.3)
    expected = "Hai raggiunto il limite settimanale dell’assistente: si azzera sabato alle 00:00."
    err = await _limit(env, (await env.token())["token"])
    assert (err["limit"], err["message"]) == ("subject_week", expected)
    resets = datetime.fromisoformat(err["resetsAt"]).astimezone(ROME)
    assert (resets.weekday(), resets.hour, resets.minute) == (5, 0, 0)
    ev = await env.chat((await env.token())["token"], text="Ciao", mode="voice")
    said = " ".join(e["text"] for e in ev if e["type"] == "say")
    assert "settimanale" in said and "sabato" in said
    # A token's own weekly cap lifts it for its session; the reset weekday stays the default's.
    env.script.add("function:primary", ["Ok."])
    tok = await env.token(limits={"caps": [{"period": "week", "amount": 5}]})
    ev = await env.chat(tok["token"], text="Ciao")
    assert ev[-1]["reason"] == "end_turn"


def test_the_limit_message_names_the_cap_and_its_reset_never_an_amount() -> None:
    app = SimpleNamespace(
        name="Inkwell Books", defaults={"locale": "it", "timezone": "Europe/Rome"}, currency="EUR"
    )
    (month,) = caps_of(
        SimpleNamespace(**app.__dict__, exchange_rate=0.9, monthly_cap_usd=None, daily_cap_usd=None,
                        caps={"application": [{"period": "month", "amount": 9, "resets": 1}]}),
        subject=False,
    )  # fmt: skip
    start, end = window("month", 1, _local("2026-11-16T10:00"), ROME)
    text = limit_message(app, CapUse(month, start, end, 9.9))
    assert text == (
        "Il limite mensile dell’assistente per Inkwell Books è stato raggiunto: si azzera "
        "il 1º dicembre alle 00:00."
    )
    assert "9" not in text and "€" not in text and "EUR" not in text
    en = SimpleNamespace(name="Inkwell Books", defaults={"timezone": "Europe/Rome"})
    s, e = window("month", 15, _local("2026-11-16T10:00"), ROME)
    assert limit_message(en, CapUse(month, s, e, 1)).endswith("it resets on 15 December at 00:00.")


async def test_messages_are_localisable_per_application(env: Env) -> None:
    await _patch(env, subject_daily_usd=0, locale="en")
    assert (await _limit(env, (await env.token())["token"]))["message"].startswith("You have")
    await _patch(env, messages={"limit.subjectDay": "Basta per oggi, {appName}: {resets}."})
    assert (await _limit(env, (await env.token())["token"]))[
        "message"
    ] == "Basta per oggi, Test App: tomorrow at 00:00."


# ── what browsers see ───────────────────────────────────────────────────────────────────────────


async def test_browsers_get_shares_in_the_token_session_and_events(env: Env) -> None:
    await _patch(env, monthly_cap_usd=10, subject_monthly_usd=2, subject_daily_usd=1)
    await _spent(env, "u1", 0.5)
    tok = await env.token()
    usage = tok["usage"]
    assert {k: usage[k] for k in ("dayPct", "weekPct", "monthPct", "companyPct")} == {
        "dayPct": 50.0,
        "weekPct": None,
        "monthPct": 25.0,
        "companyPct": 5.0,
    }
    assert usage["monthOut"] is False and usage["out"] is False
    assert usage["binding"]["limit"] == "subject_day" and usage["binding"][
        "usedPct"
    ] == pytest.approx(50)
    assert usage["resetsAt"] == usage["binding"]["resetsAt"]
    assert [c["limit"] for c in usage["caps"]] == ["app_month", "subject_month", "subject_day"]
    env.script.add("function:primary", ["Ok."])
    ev = await env.chat(tok["token"], text="Ciao")
    event = next(e for e in ev if e["type"] == "usage")
    assert set(event) - {"type", "turnId", "seq"} == {
        "dayPct",
        "weekPct",
        "monthPct",
        "companyPct",
        "monthOut",
        "out",
        "binding",
        "resetsAt",
        "caps",
    }
    await _spent(env, "u1", 2)
    usage = (await env.token())["usage"]
    assert usage["monthOut"] is True and usage["out"] is True
    # Two caps are reached: the binding one is the one that resets last (the month).
    assert usage["binding"]["limit"] == "subject_month"


async def test_the_browser_view_is_shares_and_resets_only(env: Env) -> None:
    await _patch(
        env,
        currency="EUR",
        exchange_rate=0.9,
        timezone="Europe/Rome",
        caps={
            "application": [{"period": "month", "amount": 90}],
            "subject": [{"period": "week", "amount": 9, "resets": "saturday"}],
        },
    )
    await _spent(env, "u1", 2.5)
    tok = await env.token()
    usage = tok["usage"]
    assert usage["weekPct"] == pytest.approx(25) and usage["companyPct"] == pytest.approx(2.5)
    assert usage["binding"] == {
        "limit": "subject_week",
        "scope": "subject",
        "period": "week",
        "usedPct": 25.0,
        "leftPct": 75.0,
        "resetsAt": usage["resetsAt"],
    }
    headers = {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    info = (await env.client.get("/agent/v1/session", headers=headers)).json()["usage"]
    text = repr(info).lower()
    assert info["weekPct"] == pytest.approx(25)
    assert not any(word in text for word in ("eur", "usd", "amount", "0.9", "'9", "90"))


async def test_cap_state_windows_count_only_their_own_spend(env: Env) -> None:
    await _patch(
        env,
        timezone="Europe/Rome",
        caps={"subject": [{"period": "week", "amount": 1, "resets": "saturday"}]},
    )
    app = await env.svc.store.applications.get(env.app_id)
    assert app is not None
    now = datetime.now(UTC)
    start, _ = window("week", 5, now, ROME)
    await _spent(env, "u1", 0.7, at=start - timedelta(seconds=1))
    await _spent(env, "u1", 0.2, at=start)
    await _spent(env, "u2", 0.5, at=start + timedelta(seconds=1))
    state = await cap_state(env.svc.store, app, Who(env.app_id, "u1"), now)
    week = state.get("subject", "week")
    assert week is not None and week.spent_usd == pytest.approx(0.2)
    month = state.get("application", "month")
    assert month is not None
    assert isinstance(state, CapState) and state.hit(0.85) is week and state.hit(0.7) is None


async def test_bootstrap_sets_caps_messages_and_locale(env: Env) -> None:
    app = BootstrapApp(
        id="test",
        caps=BootstrapCaps(monthlyUsd=9, subjectMonthlyUsd=3, subjectDailyUsd=0.75),
        locale="it",
        messages={"limit.app": "Finito per {appName}."},
    )
    await bootstrap_apps(env.svc.store, [app])
    row = await env.svc.store.applications.get(env.app_id)
    assert row is not None and row.monthly_cap_usd == 9
    assert row.defaults["subject_monthly_usd"] == 3
    assert row.defaults["subject_daily_usd"] == pytest.approx(0.75)
    assert row.defaults["messages"] == {"limit.app": "Finito per {appName}."}
    # The new form from the environment, in euro; it clears the old subject month.
    moved = BootstrapApp(
        id="test",
        currency="EUR",
        exchangeRate=0.9,
        caps=BootstrapCaps(subject=[CapSpec(period="month", amount=2)]),
    )
    await bootstrap_apps(env.svc.store, [moved])
    row = await env.svc.store.applications.get(env.app_id)
    assert row is not None and row.currency == "EUR" and row.exchange_rate == pytest.approx(0.9)
    assert row.caps == {
        "application": [],
        "subject": [{"period": "month", "amount": 2, "resets": 1}],
    }
    assert row.defaults.get("subject_monthly_usd") is None
    with pytest.raises(ConfigError):
        await bootstrap_apps(env.svc.store, [BootstrapApp(id="test", currency="EUR")])


def _file(tmp_path: Any, name: str, body: str) -> Any:
    path = tmp_path / name
    path.write_text("application:\n  slug: test\n  name: Test App\n" + body, encoding="utf-8")
    return path


async def test_the_config_file_caps_replace_the_stored_ones_at_start(
    env: Env, tmp_path: Any
) -> None:
    store, keyring = env.svc.store, env.svc.keyring
    first = _file(
        tmp_path,
        "v1.yaml",
        "  monthly_cap_usd: 5\n  daily_cap_usd: 1\n  subject_daily_usd: 0.75\n"
        "  caps:\n    subject: [{period: week, amount: 2, resets: saturday}]\n",
    )
    await bootstrap(store, keyring, first, {})
    row = await store.applications.get(env.app_id)
    kinds = {c.kind for c in caps_of(row)}
    assert kinds == {"app_month", "app_day", "subject_week", "subject_day"}
    # The next start: the file dropped the subject's day, the week and the application's day.
    await bootstrap(store, keyring, _file(tmp_path, "v2.yaml", "  monthly_cap_usd: 5\n"), {})
    row = await store.applications.get(env.app_id)
    assert row is not None and row.caps is None and row.daily_cap_usd is None
    assert row.defaults.get("subject_daily_usd") is None
    assert {c.kind for c in caps_of(row)} == {"app_month"}
    # A removed cap is not enforced any more.
    await _spent(env, "u1", 0.8)
    env.script.add("function:primary", ["Ok."])
    ev = await env.chat((await env.token())["token"], text="Ciao")
    assert ev[-1]["reason"] == "end_turn"
    # Without any cap the file leaves the caps alone (set in the console, say).
    await _patch(env, subject_daily_usd=0.5)
    await bootstrap(store, keyring, _file(tmp_path, "v3.yaml", "  locale: en\n"), {})
    row = await store.applications.get(env.app_id)
    assert row is not None and row.defaults["subject_daily_usd"] == pytest.approx(0.5)
    # A file with only the new form replaces the old fields too (the month falls back to 10 USD).
    v4 = _file(tmp_path, "v4.yaml", "  caps:\n    subject: [{period: day, amount: 1}]\n")
    await bootstrap(store, keyring, v4, {})
    row = await store.applications.get(env.app_id)
    assert row is not None and row.monthly_cap_usd == pytest.approx(10)
    assert {c.kind: c.source for c in caps_of(row)} == {
        "app_month": "legacy",
        "subject_day": "caps",
    }
