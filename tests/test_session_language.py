"""A session's language: any BCP 47 tag, read by prompts and speech; packs only for messages."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from evokai.agents.definitions import AgentDef
from evokai.agents.prompts import system_prompt
from evokai.i18n import language_name, locale_of, session_locale, speech_language


def _app(locale: str | None = "it") -> SimpleNamespace:
    return SimpleNamespace(defaults={"locale": locale} if locale else {})


def _session(tag: str | None) -> SimpleNamespace:
    return SimpleNamespace(prompt_options={"locale": tag})


def test_a_language_is_named_in_english_or_by_its_tag() -> None:
    assert language_name("it-IT") == "Italian"
    assert language_name("zh-Hant") == "Chinese"
    assert language_name("xx") == "xx"
    assert language_name("") == ""


def test_the_session_language_beats_the_application_and_needs_no_pack() -> None:
    assert session_locale(_app(), _session("ja-JP")) == "ja-JP"
    assert session_locale(_app(), _session(None)) == "it"
    assert session_locale(_app(None), None) == "en"
    assert session_locale(_app(), _session("not a tag")) == "it"
    # Messages and speech rules keep a shipped pack; speech engines take the language itself.
    assert locale_of(_app(), _session("ja-JP")) == "it"
    assert speech_language(_app(), _session("ja-JP")) == "ja"
    assert speech_language(_app(), _session("en-GB")) == "en"
    assert speech_language(_app(), None) == "it"


def test_instructions_read_the_session_language() -> None:
    agent = AgentDef(
        name="a",
        instructions="Reply in {{session.language}}, tag {{session.locale}}.",
        model="function:x",
        template_syntax="handlebars",
    )
    prompt = system_prompt(
        agent,
        now=datetime(2026, 11, 28, tzinfo=UTC),
        app_name="App",
        page=None,
        memories=[],
        skills=[],
        page_text_chars=0,
        voice=False,
        locale="pt-BR",
    )
    assert "Reply in Portuguese, tag pt-BR." in prompt.text()
