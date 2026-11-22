"""Old placeholders are refused when saved: what is saved now is Handlebars.

Versions saved before keep rendering as they did (``tests/test_template_syntax.py``); a new save
that still writes ``{date}`` or a label's ``{field|count}`` gets 400 ``legacy_syntax`` naming the
placeholder and its Handlebars form, and a YAML file with them stops the start-up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evokai.agents.bootstrap import bootstrap
from evokai.agents.templates import legacy_placeholder
from evokai.core.errors import ConfigError
from tests.conftest import Env

AGENTS = "/agent/v1/admin/agents"
PROMPTS = "/agent/v1/admin/prompts"


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _helper(env: Env) -> dict[str, Any]:
    r = await env.client.get(AGENTS, headers=_adm(env))
    agent = next(x for x in r.json()["items"] if x["name"] == "helper")
    agent.pop("version", None)
    agent.pop("template_syntax", None)
    return agent


def test_only_the_old_placeholder_grammar_counts() -> None:
    assert legacy_placeholder("Today is {date}.") == ("{date}", "{{date}}")
    assert legacy_placeholder("Shop {app}") == ("{app}", "{{app}}")
    for text in [
        'Answer as JSON: {"ok": true, "items": [{"id": 1}]}',
        "Today is {{date}} at {{app}}; a lone { or } and {name} stay.",
        r"Write \{{date}} literally.",
        "{{{date}}}",
    ]:
        assert legacy_placeholder(text) is None, text
    label = "Refund {orderIds|count} as {reason|map:reasons}"
    assert legacy_placeholder(label, labels=True) == ("{orderIds|count}", "{{count orderIds}}")
    assert legacy_placeholder("as {reason|map:reasons}", labels=True) == (
        "{reason|map:reasons}",
        '{{map reason "reasons"}}',
    )
    assert legacy_placeholder("Close {ret.id}", labels=True) == ("{ret.id}", "{{ret.id}}")
    assert legacy_placeholder('{"id": 1} {{returnId}}', labels=True) is None


async def test_api_refuses_legacy_instructions(env: Env) -> None:
    body = {**await _helper(env), "instructions": 'Today is {date}. Reply {"ok": true}.'}
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "legacy_syntax", r.text
    assert "{date}" in r.json()["message"] and "{{date}}" in r.json()["message"]
    body["instructions"] = 'Today is {{date}}. Reply {"ok": true}.'
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text


async def test_api_refuses_legacy_labels(env: Env) -> None:
    body = await _helper(env)
    body["confirm_labels"] = {
        "order_refund": ["Refund {{period}}", "Refund as {reason|map:reasons}"]
    }
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "legacy_syntax", r.text
    assert '{{map reason "reasons"}}' in r.json()["message"]


async def test_api_refuses_handlebars_that_does_not_fit(env: Env) -> None:
    body = {**await _helper(env), "instructions": "Hello {{nmae}}."}
    r = await env.client.put(f"{AGENTS}/helper", json=body, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "prompt_template", r.text
    r = await env.client.put(f"{PROMPTS}/base", json={"text": "Shop {{#if}}"}, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "prompt_template", r.text


async def test_api_refuses_legacy_prompts(env: Env) -> None:
    r = await env.client.put(f"{PROMPTS}/base", json={"text": "Shop {app}."}, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "legacy_syntax", r.text
    assert "{{app}}" in r.json()["message"]
    ok = {"text": 'Shop {{app}}, reply {"ok": true}.'}
    r = await env.client.put(f"{PROMPTS}/base", json=ok, headers=_adm(env))
    assert r.status_code == 200, r.text
    row = await env.svc.store.prompts.get(env.app_id, "base")
    assert row is not None and row.template_syntax == "handlebars"


async def test_an_unchanged_legacy_agent_can_still_be_saved(env: Env) -> None:
    # A version stored before Handlebars (no template_syntax): changing its model keeps its text.
    legacy = {"name": "old", "instructions": "Today is {date}.", "model": "function:primary"}
    from evokai.agents.definitions import AgentDef

    agent = AgentDef.model_validate(legacy).with_version()
    await env.svc.store.agents.put(env.app_id, "old", agent.version, agent.content())
    listed = (await env.client.get(AGENTS, headers=_adm(env))).json()["items"]
    body = next(x for x in listed if x["name"] == "old")
    assert body.pop("template_syntax") == "legacy" and body.pop("version") == agent.version
    switched = {**body, "template_syntax": "legacy", "model": "function:other"}
    r = await env.client.put(f"{AGENTS}/old", json=switched, headers=_adm(env))
    assert r.status_code == 200, r.text
    # Changing the text needs Handlebars.
    changed = {**switched, "instructions": "Now it is {date}."}
    r = await env.client.put(f"{AGENTS}/old", json=changed, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "legacy_syntax", r.text
    # A new agent cannot start as legacy.
    fresh = {**switched, "name": "fresh"}
    r = await env.client.put(f"{AGENTS}/fresh", json=fresh, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "legacy_syntax", r.text


CONFIG = """
application:
  slug: legacy
  name: Legacy App
prompts:
  - name: base
    text: "Shop {{app}}."
agents:
  - name: helper
    model: "function:primary"
    instructions: "You are an assistant."
    confirm_labels:
      order_refund: "Refund {orderIds|count} orders"
"""


async def test_yaml_with_old_placeholders_stops_the_start(env: Env, tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
    message = e.value.message
    assert str(path) in message and "agent helper" in message
    assert "{orderIds|count}" in message and "{{count orderIds}}" in message
    path.write_text(CONFIG.replace("Shop {{app}}.", "Shop {app}."), encoding="utf-8")
    with pytest.raises(ConfigError, match=r"prompt base.*\{\{app\}\}"):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
    fixed = CONFIG.replace("{orderIds|count}", "{{count orderIds}}")
    path.write_text(fixed, encoding="utf-8")
    app_id = await bootstrap(env.svc.store, env.svc.keyring, path, {})
    row = await env.svc.store.agents.active(app_id, "helper")
    assert row is not None and row.definition["template_syntax"] == "handlebars"


async def test_restoring_a_stored_legacy_version_keeps_it_legacy(env: Env) -> None:
    from evokai.agents.definitions import AgentDef

    legacy = {"name": "old", "instructions": "Today is {date}.", "model": "function:primary"}
    agent = AgentDef.model_validate(legacy).with_version()
    await env.svc.store.agents.put(env.app_id, "old", agent.version, agent.content())
    new = {**legacy, "instructions": "Today is {{date}}."}
    r = await env.client.put(f"{AGENTS}/old", json=new, headers=_adm(env))
    assert r.status_code == 200, r.text
    # The console restores a version by saving its stored definition (no template_syntax).
    r = await env.client.put(f"{AGENTS}/old", json=agent.content(), headers=_adm(env))
    assert r.status_code == 200 and r.json()["version"] == agent.version, r.text
    row = await env.svc.store.agents.active(env.app_id, "old")
    assert row is not None and row.version == agent.version
    assert "template_syntax" not in row.definition


async def test_yaml_cannot_ask_for_legacy(env: Env, tmp_path: Path) -> None:
    path = tmp_path / "app.yaml"
    body = CONFIG.replace("{orderIds|count}", "{{count orderIds}}")
    legacy = body.replace("    instructions:", "    template_syntax: legacy\n    instructions:")
    path.write_text(legacy, encoding="utf-8")
    with pytest.raises(ConfigError, match="template_syntax: legacy"):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
