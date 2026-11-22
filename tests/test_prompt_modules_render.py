"""Prompt modules: rendering with Handlebars after the agent's instructions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from evokai.agents.definitions import AgentDef
from evokai.agents.modules import RenderedModule, render_template
from evokai.agents.prompts import PageContext, SystemPrompt, long_date, system_prompt

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


def _agent(instructions: str = "You help readers of the bookshop.") -> AgentDef:
    return AgentDef.model_validate({"name": "helper", "instructions": instructions, "model": "f:x"})


def _prompt(*modules: RenderedModule, agent: AgentDef | None = None, **kw: Any) -> SystemPrompt:
    return system_prompt(
        agent or _agent(),
        now=NOW,
        app_name="Bookshop",
        page=kw.pop("page", None),
        memories=[],
        skills=[],
        page_text_chars=1000,
        voice=False,
        modules=list(modules),
        **kw,
    )


def _mod(name: str, text: str, data: dict[str, str] | None = None, **params: Any) -> RenderedModule:
    return RenderedModule(name=name, version=1, text=text, params=params, data=data)


def test_lookup_picks_data_entry_by_enum_param() -> None:
    data = {"a": "You are the poetry clerk.", "b": "You are the crime fiction clerk."}
    prompt = _prompt(_mod("character", "{{lookup data character}}", data, character="b"))
    assert "You are the crime fiction clerk." in prompt.stable
    assert "poetry clerk" not in prompt.stable


def test_modules_follow_instructions_in_agent_order() -> None:
    prompt = _prompt(_mod("first", "First module."), _mod("second", "Second module."))
    assert prompt.stable == "You help readers of the bookshop.\n\nFirst module.\n\nSecond module."


def test_module_off_is_absent() -> None:
    # Modules switched off never reach system_prompt (resolve_modules drops them).
    prompt = _prompt(_mod("first", "First module."))
    assert "Second module." not in prompt.stable
    assert _prompt().stable == "You help readers of the bookshop."


def test_handlebars_conditionals_on_boolean_param() -> None:
    text = "{{#if formal}}Use formal address.{{else}}Be relaxed.{{/if}}"
    assert "Use formal address." in _prompt(_mod("tone", text, formal=True)).stable
    informal = _prompt(_mod("tone", text, formal=False)).stable
    assert "Be relaxed." in informal and "formal address" not in informal


def test_no_html_escaping() -> None:
    prompt = _prompt(_mod("team", "Sign as {{team}}.", team="R&D <team>"))
    assert "Sign as R&D <team>." in prompt.stable
    assert render_template("{{x}} and {{{x}}}", {"x": "a<b"}) == "a<b and a<b"


def test_legacy_single_brace_still_renders() -> None:
    prompt = _prompt(agent=_agent("Today is {date} at {app}."))
    assert prompt.stable == f"Today is {long_date(NOW)} at Bookshop."
    module = _prompt(_mod("day", "It is {{date}} at {{app}}.")).stable
    assert f"It is {long_date(NOW)} at Bookshop." in module


def test_literal_json_braces_untouched() -> None:
    for text in ['Reply as {"ok": true}', 'Reply as {"ok":1} or {name}', "a { b } c", "{0} {}"]:
        assert _prompt(agent=_agent(text)).stable == text
        assert render_template(text, {"date": "D", "app": "A"}) == text


def test_modules_are_in_stable_part() -> None:
    page = PageContext(path="/books/1", title="A book", text="Some text")
    prompt = _prompt(_mod("tone", "Answer briefly."), page=page)
    assert "Answer briefly." in prompt.stable
    assert "Answer briefly." not in prompt.volatile
    assert "/books/1" in prompt.volatile
