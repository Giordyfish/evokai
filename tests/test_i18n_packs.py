"""Every shipped language pack is complete: messages, speech rules, calendar."""

from __future__ import annotations

import json
import re
from importlib import resources
from types import SimpleNamespace

import pytest

from evokai.audio.speech.replies import spoken_decision, spoken_option
from evokai.audio.speech.speakable import SentenceSplitter, redact
from evokai.i18n import known_locales, message, pack_language, speech_rules

SHIPPED = ("en", "it", "es", "fr", "de", "ro", "sv")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _raw(code: str) -> dict:
    return json.loads((resources.files("evokai.locales") / f"{code}.json").read_text("utf-8"))


def test_every_pack_is_shipped() -> None:
    assert set(SHIPPED) <= known_locales()


@pytest.mark.parametrize("code", SHIPPED[1:])
def test_a_pack_has_every_message_with_the_same_placeholders(code: str) -> None:
    en, own = _raw("en"), _raw(code)
    assert set(own["messages"]) == set(en["messages"])
    for key, text in en["messages"].items():
        assert set(PLACEHOLDER.findall(own["messages"][key])) == set(PLACEHOLDER.findall(text)), key
        assert chr(0x2014) not in own["messages"][key]


@pytest.mark.parametrize("code", SHIPPED[1:])
def test_a_pack_has_its_own_speech_rules_and_calendar(code: str) -> None:
    en, own = _raw("en"), _raw(code)
    for key in ("redactions", "sourcesHeadings", "abbreviations", "yes", "no", "ordinals"):
        assert own["speech"][key], key
    assert set(own["speech"]["redactions"]) == set(en["speech"]["redactions"])
    assert sorted(set(own["speech"]["ordinals"].values())) == [1, 2, 3, 4, 5]
    assert len(own["calendar"]["weekdays"]) == 7
    assert len(own["calendar"]["months"]) == 12
    assert own["speech"]["numbers"]["decimal"] == ","


@pytest.mark.parametrize(
    ("tag", "language"),
    [("es-ES", "es"), ("fr-CA", "fr"), ("de", "de"), ("ro-RO", "ro"), ("sv-SE", "sv")],
)
def test_tags_read_their_pack(tag: str, language: str) -> None:
    assert pack_language(tag) == language


@pytest.mark.parametrize(
    ("code", "yes", "no", "second"),
    [
        ("es", "sí, adelante", "no, déjalo", "la segunda"),
        ("fr", "oui, vas-y", "non merci", "la deuxième"),
        ("de", "ja, mach weiter", "nein danke", "die zweite"),
        ("ro", "da, continuă", "nu, mulțumesc", "a doua"),
        ("sv", "ja, kör", "nej tack", "den andra"),
    ],
)
def test_spoken_replies_in_each_language(code: str, yes: str, no: str, second: str) -> None:
    rules = speech_rules(code)
    assert spoken_decision(yes, rules) is True
    assert spoken_decision(no, rules) is False
    assert spoken_option(second, ["Rome", "Milan", "Turin"], rules) == "Milan"


def test_redactions_and_abbreviations_follow_the_language() -> None:
    assert redact("Scrivi a ada@example.com", speech_rules("de")) == "Scrivi a die E-Mail-Adresse"
    splitter = SentenceSplitter(rules=speech_rules("de"))
    assert splitter.push("Das gilt z. B. heute. Und morgen ") == ["Das gilt z. B. heute."]


def test_messages_of_a_session_locale() -> None:
    app = SimpleNamespace(defaults={"locale": "en"}, name="Shop")
    session = SimpleNamespace(prompt_options={"locale": "fr-FR"})
    assert message(app, "turn.empty", session=session) == "Écrivez quelque chose."
