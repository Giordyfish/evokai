"""Ask rules: ordered rules that only add confirmations (``evokai.guard.ask``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evokai.core.config import Settings
from evokai.core.errors import ConfigError
from evokai.guard.ask import AskRules, check_rules, overlap, parse_rules, wildcard
from tests.conftest import AGENTS_YAML, Env, Tool, make_env, types


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _app_rules(env: Env, rules: list[dict[str, Any]], status: int = 200) -> dict[str, Any]:
    r = await env.client.put("/agent/v1/admin/ask-rules", json={"rules": rules}, headers=_adm(env))
    assert r.status_code == status, r.text
    return r.json()


async def _helper(env: Env, status: int = 200, **update: Any) -> Any:
    cur = (await env.client.get("/agent/v1/admin/agents", headers=_adm(env))).json()["items"]
    helper = next(a for a in cur if a["name"] == "helper")
    helper.pop("version")
    helper.update(update)
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
    assert r.status_code == status, r.text
    return r


def _rules(*raw: dict[str, Any], tags: list[str] | None = None, level: str = "agent") -> AskRules:
    return AskRules.build(**{level: list(raw)}, tags=tags)


# the rules themselves


def test_patterns_match_the_whole_value_with_star_and_question_mark() -> None:
    assert wildcard("crm_*", "crm_lookup")
    assert not wildcard("crm_*", "x_crm_lookup")
    assert wildcard("C-1??", "C-123") and not wildcard("C-1??", "C-1234")
    assert wildcard("*", "") and wildcard("a.b", "a.b") and not wildcard("a.b", "axb")
    assert not wildcard("[ab]", "a")  # brackets are literal


def test_each_level_decides_on_its_own_and_a_lower_one_never_lifts_a_higher_ask() -> None:
    rules = AskRules.build(
        deployment=[{"action": "order*", "effect": "ask", "id": "months"}],
        # Not saveable (nothing above it to cancel in its own list); if it were stored, it does
        # nothing: the deployment's ask stands.
        application=[{"action": "orders_month", "effect": "default"}],
        agent=[
            {"action": "crm_*", "effect": "ask"},
            {"action": "crm_get", "argument": "code", "resource": "C-*", "effect": "default"},
            {
                "action": "crm_get",
                "argument": "code",
                "resource": "C-1?",
                "effect": "ask",
                "id": "late",
            },
        ],
    )
    hit = rules.match("orders_month", {"period": "2026-09"})
    assert hit is not None and (hit.rule, hit.level) == ("months", "deployment")
    assert rules.askable("orders_month")
    # Inside one level the last match wins: a narrower default cancels the agent's own ask.
    assert rules.match("crm_get", {"code": "C-7"}) is None
    late = rules.match("crm_get", {"code": "C-12"})
    assert late is not None and (late.rule, late.level) == ("late", "agent")
    assert rules.match("crm_list", {}) is not None
    assert not rules.askable("stock_report")


def test_a_lower_level_default_for_every_call_leaves_a_higher_ask_standing() -> None:
    rules = AskRules.build(
        application=[{"action": "order*", "effect": "ask", "id": "app"}],
        agent=[{"action": "x", "effect": "ask"}, {"action": "*", "effect": "default"}],
    )
    hit = rules.match("orders_month", {})
    assert hit is not None and hit.level == "application"
    assert rules.askable("orders_month") and not rules.askable("x")


def test_a_default_for_every_call_lifts_an_earlier_ask_but_not_a_narrower_later_one() -> None:
    lifted = _rules({"action": "crm_*", "effect": "ask"}, {"action": "*", "effect": "default"})
    assert not lifted.askable("crm_lookup") and lifted.match("crm_lookup", {}) is None
    narrow = _rules(
        {"action": "*", "effect": "default"},
        {"action": "crm_lookup", "argument": "code", "resource": "VIP-*", "effect": "ask"},
    )
    assert narrow.askable("crm_lookup")
    assert narrow.match("crm_lookup", {"code": "VIP-7"}) is not None
    assert narrow.match("crm_lookup", {"code": "C-7"}) is None
    assert narrow.match("crm_lookup", {}) is None


def test_resources_reach_nested_arguments_lists_and_url_hosts() -> None:
    rules = _rules(
        {"action": "fetch", "argument": "url", "part": "host", "resource": "*.internal"},
        {"action": "crm_bulk", "argument": "filters.codes", "resource": "VIP-*"},
    )
    assert rules.match("fetch", {"url": "https://db.internal/x"}) is not None
    assert rules.match("fetch", {"url": "db.INTERNAL/x"}) is not None
    assert rules.match("fetch", {"url": "https://example.com/db.internal"}) is None
    assert rules.match("crm_bulk", {"filters": {"codes": ["C-1", "VIP-2"]}}) is not None
    assert rules.match("crm_bulk", {"filters": {"codes": ["C-1"]}}) is None


def test_tags_scope_a_rule_to_some_people() -> None:
    rule = {"action": "*", "effect": "ask", "tags": ["team:ops*"]}
    assert _rules(rule, tags=["team:ops-north"]).match("x", {}) is not None
    assert _rules(rule, tags=["team:sales"]).match("x", {}) is None
    assert _rules(rule).match("x", {}) is None


@pytest.mark.parametrize("effect", ["allow", "deny", "skip"])
def test_a_rule_can_only_ask_or_do_nothing(effect: str) -> None:
    with pytest.raises(ValidationError, match="can only ask"):
        parse_rules([{"action": "order_refund", "effect": effect}])
    with pytest.raises(ValidationError, match="can only ask"):
        Settings(ask_rules=[{"action": "order_refund", "effect": effect}])


def test_a_default_that_names_a_tool_that_changes_data_is_refused() -> None:
    rules = parse_rules([
        {"action": "order*", "effect": "ask"},
        {"action": "order_refund", "effect": "default"},
        {"action": "order*", "effect": "default"},
    ])
    problems = check_rules(rules, {"order_refund"})
    assert len(problems) == 1 and problems[0].startswith("rule 2: order_refund changes data")


def test_a_default_with_no_ask_above_it_in_its_own_list_is_refused() -> None:
    rules = parse_rules([
        {"action": "*", "effect": "default"},
        {"action": "crm_*", "effect": "ask"},
        {"action": "crm_get", "effect": "default"},
        {"action": "order_?et", "effect": "default"},
        {"action": "*_get", "effect": "default"},
    ])
    problems = check_rules(rules, set())
    assert [p.split(":")[0] for p in problems] == ["rule 1", "rule 4"]
    assert "never lifts an ask of the deployment or the application" in problems[0]
    with pytest.raises(ValidationError, match="only cancels an ask above it"):
        Settings(ask_rules=[{"action": "x", "effect": "default"}])


@pytest.mark.parametrize(
    ("a", "b", "both"),
    [
        ("crm_*", "crm_get", True),
        ("crm_*", "*_get", True),
        ("order_?et", "crm_*", False),
        ("a?c", "*b*", True),
        ("abc", "abd", False),
        ("*", "", True),
    ],
)
def test_pattern_overlap(a: str, b: str, both: bool) -> None:
    assert overlap(a, b) is both and overlap(b, a) is both


def test_a_resource_needs_its_argument() -> None:
    with pytest.raises(ValidationError, match="needs the argument"):
        parse_rules([{"action": "x", "resource": "VIP-*"}])


# through the API


async def test_application_rules_are_checked_saved_and_shown_with_every_level(env: Env) -> None:
    env.svc.settings.ask_rules = [{"action": "stock_*", "effect": "ask"}]
    r = await env.client.put(
        "/agent/v1/admin/ask-rules",
        json={"rules": [{"action": "order_refund", "effect": "allow"}]},
        headers=_adm(env),
    )
    assert r.status_code == 400 and r.json()["error"] == "ask_rules"
    assert "rule 1 (effect): effect 'allow' is not allowed" in r.json()["message"]
    view = await _app_rules(
        env,
        [
            {
                "action": "orders_month",
                "argument": "period",
                "resource": "2026-1*",
                "label": {"en": "Recent months", "it": "Mesi recenti"},
            }
        ],
    )
    assert view["rules"] == [
        {
            "action": "orders_month",
            "argument": "period",
            "resource": "2026-1*",
            "effect": "ask",
            "label": {"en": "Recent months", "it": "Mesi recenti"},
        }
    ]
    assert [x["action"] for x in view["deployment"]] == ["stock_*"]
    audit = (await env.client.get("/agent/v1/admin/audit", headers=_adm(env))).json()
    assert any(x["action"] == "ask_rules.put" for x in audit["items"])


async def test_an_agent_rule_that_lifts_a_write_confirmation_is_refused_at_save(env: Env) -> None:
    await env.chat((await env.token())["token"], text="Ciao")  # lists the MCP tools
    lift = [{"action": "order*"}, {"action": "order_refund", "effect": "default"}]
    r = await _helper(env, 400, ask_rules=lift)
    assert r.json()["error"] == "ask_rules" and "cannot be removed" in r.json()["message"]
    r = await _helper(env, 400, ask_rules=[{"action": "orders_month", "effect": "default"}])
    assert "only cancels an ask above it" in r.json()["message"]
    r = await _helper(env, 400, ask_rules=[{"action": "order_refund", "effect": "allow"}])
    assert r.json()["error"] == "ask_rules" and "can only ask" in r.json()["message"]


async def test_a_rule_makes_a_read_only_call_wait_and_says_why(env: Env) -> None:
    await _app_rules(
        env,
        [
            {
                "id": "recent",
                "action": "orders_month",
                "argument": "period",
                "resource": "2026-1*",
                "label": {"en": "Recent months", "it": "Mesi recenti"},
            }
        ],
    )
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-09"}, "a")],
        [Tool("orders_month", {"period": "2026-10"}, "b")],
        ["Ottobre ha 24 ordini."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Settembre e ottobre?")
    assert env.mcp.calls == [("orders_month", {"period": "2026-09"})]
    confirm = next(e for e in ev if e["type"] == "confirm")
    assert confirm["why"] == [
        {
            "key": "confirm.why.rule",
            "rule": "recent",
            "level": "application",
            "label": "Recent months",
        }
    ]
    assert ev[-1]["reason"] == "confirm"
    pending = (
        await env.client.get(
            "/agent/v1/session/conversation", headers={"Authorization": f"Bearer {tok['token']}"}
        )
    ).json()["pending"]
    assert pending["why"][0]["rule"] == "recent"
    ev2 = await env.chat(tok["token"], confirm={"actionId": confirm["actionId"], "approve": True})
    assert env.mcp.calls[-1] == ("orders_month", {"period": "2026-10"})
    assert ev2[-1]["reason"] == "end_turn"
    # The trace says which rule asked, on the wait and on the approved call.
    for turn in (ev[0]["turnId"], ev2[0]["turnId"]):
        t = (await env.client.get(f"/agent/v1/admin/traces/{turn}", headers=_adm(env))).json()
        span = next(
            s
            for s in t["spans"]
            if s["attrs"].get("tool") == "orders_month" and s["attrs"].get("askRule") == "recent"
        )
        assert (span["attrs"]["askedBy"], span["attrs"]["askRuleLevel"]) == ("rule", "application")


async def test_a_write_still_asks_and_says_it_changes_data(env: Env) -> None:
    await _app_rules(env, [{"action": "order*"}, {"action": "*", "effect": "default"}])
    env.script.add("function:primary", [Tool("order_refund", {"period": "2026-10"})])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Rimborsa")
    confirm = next(e for e in ev if e["type"] == "confirm")
    assert confirm["why"] == [{"key": "confirm.why.writes"}]
    assert env.mcp.calls == []


async def test_agent_rules_add_to_the_applications_and_tags_scope_them(env: Env) -> None:
    await _app_rules(env, [{"action": "stock_*", "effect": "ask"}])
    await _helper(
        env,
        ask_rules=[
            {"action": "order*", "effect": "ask"},
            {"action": "orders_month", "effect": "default", "tags": ["team:ops"]},
            # Would lift the application's ask: refused at save, so it cannot be stored here.
        ],
    )
    for tags, asked in ((["team:ops"], False), (["team:sales"], True)):
        steps = [[Tool("orders_month", {"period": "2026-10"})]] + ([] if asked else [["Ok."]])
        env.script.add("function:primary", *steps)
        tok = await env.token(tags=tags)
        ev = await env.chat(tok["token"], text="Ottobre?")
        assert ("confirm" in types(ev)) is asked, tags
    # The agent's own default never lifts the application's ask on stock_report.
    env.script.add("function:primary", [Tool("stock_report", {})], ["Ok."])
    tok = await env.token(tags=["team:ops"])
    ev = await env.chat(tok["token"], text="Magazzino?")
    confirm = next(e for e in ev if e["type"] == "confirm")
    assert confirm["why"][0]["level"] == "application"


async def test_an_application_default_never_lifts_the_deployments_ask(env: Env) -> None:
    env.svc.settings.ask_rules = [{"action": "orders_month", "effect": "ask"}]
    r = await env.client.put(
        "/agent/v1/admin/ask-rules",
        json={"rules": [{"action": "orders_month", "effect": "default"}]},
        headers=_adm(env),
    )
    assert r.status_code == 400 and "only cancels an ask above it" in r.json()["message"]
    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["Ok."])
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Ottobre?")
    assert next(e for e in ev if e["type"] == "confirm")["why"][0]["level"] == "deployment"


# Code Mode and subagents


async def _enable(env: Env, name: str, **update: Any) -> None:
    env.svc.settings.capabilities = ["*"]
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "capability", "name": name, "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    await _helper(env, capabilities={name: {"enabled": True, "config": {}}}, **update)


async def test_a_tool_a_rule_may_ask_for_leaves_programs_and_stays_a_normal_call(
    env: Env,
) -> None:
    await _enable(env, "code_mode")
    await _app_rules(env, [{"action": "orders_month", "argument": "period", "resource": "2026-10"}])
    program = "orders_month(period='2026-09')"
    env.script.add(
        "function:primary",
        [Tool("run_code", {"code": program}, "p")],
        [Tool("orders_month", {"period": "2026-10"}, "n")],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Mesi?")
    # Not callable from the program (it is no longer read-only), still offered as a normal call
    # that waits for the person.
    assert env.mcp.calls == []
    assert "orders_month" in env.script.tools_seen
    assert ev[-1]["reason"] == "confirm"
    assert next(e for e in ev if e["type"] == "confirm")["why"][0]["key"] == "confirm.why.rule"


RESEARCHER = {
    "name": "researcher",
    "title": "Researcher",
    "model": "function:child",
    "mcp_servers": ["app"],
    "instructions": "You research the months.",
}


async def test_a_subagent_never_gets_a_tool_a_rule_may_ask_for(env: Env) -> None:
    r = await env.client.put(
        "/agent/v1/admin/agents/researcher",
        json={**RESEARCHER, "ask_rules": [{"action": "stock_report"}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    await _enable(env, "subagents", subagents=["researcher"])
    await _app_rules(env, [{"action": "orders_month"}])
    env.script.add(
        "function:primary",
        [Tool("delegate_task", {"agent_name": "researcher", "task": "Ottobre?"})],
        ["Fatto."],
    )
    env.script.add(
        "function:child",
        [
            Tool("orders_month", {"period": "2026-10"}, "c1"),
            Tool("stock_report", {}, "c2"),
        ],
        ["Non posso."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Chiedi al ricercatore")
    assert ev[-1]["reason"] == "end_turn" and "confirm" not in types(ev)
    assert env.mcp.calls == []
    child = [x for name, x in env.script.seen if name == "function:child"][-1]
    assert str(child).count("Unknown tool") == 2


async def test_a_server_run_is_never_offered_a_tool_a_rule_may_ask_for(env: Env) -> None:
    await _helper(env, channels=["browser", "server"])

    async def run() -> list[str]:
        env.script.tools_seen.clear()
        env.script.add("function:primary", ["Ok."])
        r = await env.client.post(
            "/agent/v1/agents/helper/run",
            json={"input": "Ottobre?"},
            headers={"Authorization": f"Bearer {env.server_key}"},
        )
        assert r.status_code == 200, r.text
        return list(env.script.tools_seen)

    assert "skill_load" in await run()
    await _app_rules(env, [{"action": "skill_*"}])
    assert "skill_load" not in await run()


# the config file

CAP = "  monthly_cap_usd: 5\n"


async def test_the_config_file_sets_the_applications_rules_and_refuses_a_bad_one(
    tmp_path: Path,
) -> None:
    rules = '  ask_rules: [{action: "order*", label: {en: Orders, it: Ordini}}]\n'
    async with make_env(tmp_path / "ok", agents_yaml=AGENTS_YAML.replace(CAP, CAP + rules)) as env:
        app = await env.svc.store.applications.get(env.app_id)
        assert app is not None
        assert app.ask_rules == [
            {"action": "order*", "effect": "ask", "label": {"en": "Orders", "it": "Ordini"}}
        ]
    bad = AGENTS_YAML.replace(CAP, CAP + "  ask_rules: [{action: x, effect: allow}]\n")
    with pytest.raises(ConfigError, match="can only ask"):
        async with make_env(tmp_path / "bad", agents_yaml=bad):
            pass
    lifts = AGENTS_YAML.replace(CAP, CAP + "  ask_rules: [{action: x, effect: default}]\n")
    with pytest.raises(ConfigError, match="only cancels an ask above it"):
        async with make_env(tmp_path / "lifts", agents_yaml=lifts):
            pass


def test_teams_and_slack_cards_say_why_a_rule_asked() -> None:
    from types import SimpleNamespace

    from evokai.channels.service import confirm_items

    app = SimpleNamespace(name="Inkwell Books", defaults={"locale": "it"})
    confirm = {
        "items": ["Refund order 1042", "Read VIP-1", "Read C-2", "Export"],
        "why": [
            {"key": "confirm.why.writes"},
            {"key": "confirm.why.rule", "label": "Clienti chiave"},
            {"key": "confirm.why.rule"},
            {"key": "confirm.why.policy"},
        ],
    }
    assert confirm_items(app, confirm) == [
        "Refund order 1042",
        "Read VIP-1 (Clienti chiave)",
        "Read C-2 (richiesto da una regola dell’assistente)",
        "Export (richiesto dalla politica di sicurezza)",
    ]
    assert confirm_items(app, {"items": ["Old"]}) == ["Old"]
