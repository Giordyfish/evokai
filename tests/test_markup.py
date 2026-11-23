"""Inline markup: cues delivered as marks, speech tags for the voice, nothing left in subtitles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evokai.audio.speech.markup import Markup, MarkupDef, markup_of
from evokai.i18n import speech_rules

CUES = {"cue": {}}
BOTH = {"cue": {}, "speech": {}}


def mk(spec: dict | None, agent: MarkupDef | None = None, session: object = None) -> Markup:
    m = markup_of(spec, agent, session)
    assert m is not None
    return m


def test_cues_become_marks_at_their_offset() -> None:
    m = mk(CUES)
    f = m.forms("{wave} Hello {point:left}there.", speech_rules())
    assert f.text == "Hello there."
    assert f.spoken == "Hello there."
    assert f.marks == [{"name": "wave", "at": 0}, {"name": "point", "at": 6, "arg": "left"}]
    assert f.tagged is None and f.dropped == 0


def test_a_cue_before_punctuation_leaves_no_space() -> None:
    f = mk(CUES).forms("Hi {wave}! Done.", speech_rules())
    assert f.text == "Hi! Done."
    assert f.marks == [{"name": "wave", "at": 2}]


def test_unknown_names_are_dropped_and_counted() -> None:
    m = mk({"cue": {"names": ["wave"]}})
    f = m.forms("{wave}Hi {dance} there {jump:high}.", speech_rules())
    assert f.text == "Hi there."
    assert [x["name"] for x in f.marks] == ["wave"]
    assert f.dropped == 2


def test_names_from_a_session_module_narrow_the_list() -> None:
    session = SimpleNamespace(
        prompt_options={
            "modules": {"gestures": {"on": True, "params": {"allowed": ["nod", "wave"]}}}
        }
    )
    spec = {
        "cue": {
            "names": ["wave", "smile"],
            "names_from": {"module": "gestures", "param": "allowed"},
        }
    }
    m = mk(spec, session=session)
    f = m.forms("{nod}{wave}{smile}Ok.", speech_rules())
    assert [x["name"] for x in f.marks] == ["wave"]
    # Without the session's value, the static list applies.
    f = mk(spec).forms("{nod}{smile}Ok.", speech_rules())
    assert [x["name"] for x in f.marks] == ["smile"]


def test_speech_tags_are_kept_only_in_the_tagged_form() -> None:
    f = mk(BOTH).forms("[laughs] That is {wink}funny.", speech_rules())
    assert f.text == "That is funny."
    assert f.spoken == "That is funny."
    assert f.tagged == "[laughs] That is funny."
    assert f.marks == [{"name": "wink", "at": 8}]


def test_speech_tag_names_filter() -> None:
    m = mk({"speech": {"names": ["laughs"]}})
    f = m.forms("[sighs] Fine [laughs] then.", speech_rules())
    assert f.text == "Fine then."
    assert f.tagged == "Fine [laughs] then."
    assert f.dropped == 1


def test_links_and_citations_are_not_speech_tags() -> None:
    m = mk(BOTH)
    f = m.forms("See [the guide](https://x.example) and the rule [1].", speech_rules())
    assert f.text == "See the guide and the rule."
    assert f.tagged is None and f.marks == []


def test_numbers_beside_cues_keep_exact_offsets() -> None:
    f = mk(CUES).forms("Sono {wave}1.000 punti {nod}oggi.", speech_rules("it"))
    assert f.text == "Sono 1.000 punti oggi."
    assert f.spoken == "Sono mille punti oggi."
    assert f.marks == [
        {"name": "wave", "at": 5, "spokenAt": 5},
        {"name": "nod", "at": 17, "spokenAt": 17 - len("1.000") + len("mille")},
    ]


def test_custom_delimiters() -> None:
    m = mk({"cue": {"open": "<<", "close": ">>"}, "speech": {"open": "((", "close": "))"}})
    f = m.forms("<<wave>>Hi ((whispers)) there.", speech_rules())
    assert f.text == "Hi there."
    assert f.marks == [{"name": "wave", "at": 0}]
    assert f.tagged == "Hi [whispers] there."
    assert "{wave}" in "{wave}" and m.clean("a {wave} b") == "a {wave} b"


def test_markdown_rules_never_touch_cue_names() -> None:
    f = mk(CUES).forms("**Done** {look_left} and {nod_yes}.", speech_rules())
    assert f.text == "Done and."
    assert [x["name"] for x in f.marks] == ["look_left", "nod_yes"]


def test_neutralise_makes_markup_inert_and_leaves_json() -> None:
    m = mk(BOTH)
    assert m.neutralise('Do {wave} and [laughs], then {"a": 1}') == (
        'Do \uff5bwave} and \uff3blaughs], then {"a": 1}'
    )
    assert not m.has_markup(m.neutralise("{wave}"))


def test_clean_and_inside() -> None:
    m = mk(BOTH)
    assert m.clean("Your {wave} rules [laughs] are") == "Your  rules  are"
    assert m.inside("Hello {poi")
    assert not m.inside("Hello {point} there")


def test_an_agent_overrides_the_application_per_kind() -> None:
    agent = MarkupDef.model_validate({"cue": {"open": "<", "close": ">"}})
    m = mk(BOTH, agent)
    assert m.view == {
        "cue": {"open": "<", "close": ">"},
        "speech": {"open": "[", "close": "]"},
    }
    off = MarkupDef.model_validate({"cue": {"enabled": False}})
    assert markup_of(CUES, off) is None


def test_markup_off() -> None:
    assert markup_of(None) is None
    assert markup_of({}) is None
    assert markup_of({"cue": {"enabled": False}}) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"cue": {"open": "ab"}},
        {"cue": {"open": "{{{{"}},
        {"cue": {"open": " "}},
        {"cue": {"names": ["9lives"]}},
        {"cue": {}, "speech": {"open": "{"}},
        {"cue": {"colour": "red"}},
    ],
)
def test_invalid_definitions(bad: dict) -> None:
    with pytest.raises(ValidationError):
        MarkupDef.model_validate(bad)


@pytest.mark.parametrize(
    ("piece", "spoken", "after"),
    [
        ("Il 5{x}% in più.", "Il cinque per cento in più.", " in più."),
        ("Sono 1{x},5 litri.", "Sono uno virgola cinque litri.", " litri."),
        ("Il 1{x}° posto.", "Il primo posto.", " posto."),
        ("Il 5{x} % dopo.", "Il cinque per cento dopo.", " dopo."),
    ],
)
def test_a_cue_inside_a_number_does_not_split_it(piece: str, spoken: str, after: str) -> None:
    f = mk(CUES).forms(piece, speech_rules("it"))
    assert f.spoken == spoken
    assert f.spoken[f.marks[0]["spokenAt"] :] == after


def test_an_unclosed_bracket_that_cannot_be_a_token_does_not_hold_the_voice() -> None:
    m = mk(BOTH)
    assert not m.inside("The range [0, 1) is open")
    assert not m.inside("Use {} in code")
    assert not m.inside("Use { x } in code")
    assert m.inside("Look {poi")
    assert m.inside("Look {point:le")
    assert m.inside("Hear [lau")


def test_adjacent_cues_keep_their_order_and_tags_at_one_place_too() -> None:
    f = mk(BOTH).forms("a {x}{y} b [laughs][sighs] c", speech_rules())
    assert [(x["name"], x["at"]) for x in f.marks] == [("x", 1), ("y", 1)]
    assert f.tagged == "a b [laughs] [sighs] c"


def test_task_lists_are_not_speech_tags() -> None:
    m = mk(BOTH)
    assert m.clean("- [x] done") == "- [x] done"


def test_names_from_without_a_value_and_without_names_allows_no_cue() -> None:
    spec = {"cue": {"names_from": {"module": "gestures", "param": "allowed"}}}
    f = mk(spec).forms("{wave}Hi.", speech_rules())
    assert f.marks == [] and f.dropped == 1


def test_cue_names_are_ascii() -> None:
    assert mk(CUES).clean("{saluté} ok") == "{saluté} ok"
