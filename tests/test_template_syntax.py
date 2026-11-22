"""Template syntax: every agent version and prompt version says how its text is rendered.

Versions saved before Handlebars are ``legacy`` and keep rendering exactly as they did; what is
saved now is Handlebars (``evokai.agents.modules.render_text``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from evokai.agents.definitions import AgentDef
from evokai.agents.modules import render_text
from evokai.agents.prompts import Instructions, long_date, system_prompt
from tests.conftest import Env

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
AGENTS = "/agent/v1/admin/agents"
LEGACY_TEXT = (
    "You help the readers of {app}. Today is {date}.\n"
    'Answer as JSON: {"ok": true, "items": [{"id": 1}]}.\n'
    "Escaped braces: {{date}} and {{literal}}; a lone { or } stays; {name} is not a placeholder."
)


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


def _stable(agent: AgentDef, **kw: Any) -> str:
    return system_prompt(
        agent,
        now=NOW,
        app_name="Bookshop",
        page=None,
        memories=[],
        skills=[],
        page_text_chars=1000,
        voice=False,
        **kw,
    ).stable


def test_legacy_version_renders_identically() -> None:
    # A definition stored before template_syntax existed (no key) is legacy, and its text renders
    # byte for byte as before: {date} and {app} replaced, {{ and }} single braces, the rest kept.
    stored = {"name": "helper", "instructions": LEGACY_TEXT, "model": "function:x"}
    agent = AgentDef.model_validate(stored)
    assert agent.template_syntax == "legacy"
    expected = (
        f"You help the readers of Bookshop. Today is {long_date(NOW)}.\n"
        'Answer as JSON: {"ok": true, "items": [{"id": 1}]}.\n'
        "Escaped braces: {date} and {literal}; a lone { or } stays; {name} is not a placeholder."
    )
    assert _stable(agent) == expected
    # The same text through a versioned prompt row saved before (legacy) renders the same.
    ref = AgentDef.model_validate({"name": "helper", "prompt": {"name": "p"}, "model": "f:x"})
    assert _stable(ref, instructions=Instructions(LEGACY_TEXT, "legacy")) == expected


def test_handlebars_instructions_render_date_and_app() -> None:
    agent = AgentDef.model_validate({
        "name": "helper",
        "instructions": 'Readers of {{app}}, today {{date}}. JSON: {"ok": true}.',
        "model": "function:x",
        "template_syntax": "handlebars",
    })
    assert _stable(agent) == f'Readers of Bookshop, today {long_date(NOW)}. JSON: {{"ok": true}}.'
    ref = AgentDef.model_validate({"name": "helper", "prompt": {"name": "p"}, "model": "f:x"})
    text = Instructions("Shop {{app}}.", "handlebars")
    assert _stable(ref, instructions=text) == "Shop Bookshop."


async def test_an_empty_referenced_prompt_falls_back_to_the_instructions(env: Env) -> None:
    # As before: a referenced prompt whose text is empty leaves the inline instructions.
    await env.svc.store.prompts.put(env.app_id, "empty", "")
    agent = AgentDef.model_validate({
        "name": "helper",
        "instructions": "Inline {{app}}.",
        "prompt": {"name": "empty"},
        "model": "f:x",
        "template_syntax": "handlebars",
    })
    resolved = await env.svc.engine._instructions(env.app_id, agent)
    assert _stable(agent, instructions=resolved) == "Inline Bookshop."


def test_handlebars_escape_keeps_literal_braces() -> None:
    values = {"date": "D", "app": "A"}
    assert render_text(r"Write \{{name}} literally on {{date}}.", "handlebars", values) == (
        "Write {{name}} literally on D."
    )
    assert render_text("{{date}} {date}", "legacy", values) == "{date} D"


def test_old_definition_hash_unchanged() -> None:
    # Literal hashes computed before template_syntax existed.
    stored = {"name": "helper", "instructions": "Help with books.", "model": "function:x"}
    assert AgentDef.model_validate(stored).with_version().version == "fb266d5fe85b"
    legacy = AgentDef.model_validate({**stored, "template_syntax": "legacy"})
    assert "template_syntax" not in legacy.content()
    assert legacy.with_version().version == "fb266d5fe85b"
    new = AgentDef.model_validate({**stored, "template_syntax": "handlebars"})
    assert new.content()["template_syntax"] == "handlebars"
    assert new.with_version().version != "fb266d5fe85b"


async def test_new_agent_defaults_to_handlebars(env: Env) -> None:
    body = {"name": "fresh", "instructions": "Today is {{date}}.", "model": "function:primary"}
    r = await env.client.put(f"{AGENTS}/fresh", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text
    row = await env.svc.store.agents.active(env.app_id, "fresh")
    assert row is not None and row.definition["template_syntax"] == "handlebars"
    listed = (await env.client.get(AGENTS, headers=_adm(env))).json()["items"]
    assert next(x for x in listed if x["name"] == "fresh")["template_syntax"] == "handlebars"


async def test_prompt_versions_carry_their_syntax(env: Env) -> None:
    prompts = env.svc.store.prompts
    assert await prompts.put(env.app_id, "base", "Shop {app}.", template_syntax="legacy") == 1
    # The same text saved as Handlebars is a new version.
    assert await prompts.put(env.app_id, "base", "Shop {app}.") == 2
    first, second = [await prompts.get(env.app_id, "base", v) for v in (1, 2)]
    assert first is not None and first.template_syntax == "legacy"
    assert second is not None and second.template_syntax == "handlebars"
