"""What gets spoken, spoken replies, sanitisers and history repair (pure functions)."""

from __future__ import annotations

import math

from evokai.audio.speech.replies import is_echo, spoken_decision, spoken_option
from evokai.audio.speech.speakable import SentenceSplitter, speakable
from evokai.i18n import speech_rules
from evokai.sessions.history import INTERRUPTED, Stored, repair, window
from evokai.tools.visuals import OTHER_LABEL, numbers_in, to_ask, to_chart, to_diagram, ungrounded


def test_sentences_come_out_as_soon_as_they_end_and_numbers_do_not_split() -> None:
    s = SentenceSplitter()
    assert s.push("Due **4,572.50 €**, −1.2% on Septem") == []
    assert s.push("ber. See e.g. the poetry shelf") == ["Due 4,572.50 €, −1.2% on September."]
    assert s.flush() == ["See e.g. the poetry shelf"]


def test_italian_rules_are_data_in_the_italian_pack() -> None:
    s = SentenceSplitter(rules=speech_rules("it"))
    assert s.push("Rimborsati 4.572 €, su settembre. Vedi es. lo scaffale Poesia") == [
        "Rimborsati 4.572 €, su settembre."
    ]
    assert s.flush() == ["Vedi es. lo scaffale Poesia"]
    assert "il codice fiscale" in speakable("Cliente RSSMRA80A01H501U", speech_rules("it"))
    assert spoken_decision("Sì, procedi", speech_rules("it")) is True
    assert spoken_option("la seconda", ["A b c", "D e f"], speech_rules("it")) == "D e f"


def test_tables_links_headings_and_list_marks_are_not_read() -> None:
    s = SentenceSplitter()
    md = (
        "### October\n\n| Title | € |\n|---|---|\n| Poems | 1590 |\n\n"
        "- [Open the dashboard](/orders) for the details.\n"
    )
    assert " ".join([*s.push(md), *s.flush()]) == "October Open the dashboard for the details."


def test_the_first_clause_can_be_released_early() -> None:
    s = SentenceSplitter(first_clause_min_chars=20)
    assert s.push("In October the shop refunded orders, ") == [
        "In October the shop refunded orders,"
    ]
    assert s.push("in total, four thousand euros. More, later.") == [
        "in total, four thousand euros."
    ]


def test_personal_identifiers_are_never_spoken() -> None:
    parts = [
        "Customer RSSMRA80A01H501U, IBAN IT60X0542811101000000123456,",
        "VAT 00743110157, write to mario@example.test or call 333 1234567.",
    ]
    t = speakable(" ".join(parts))
    assert not any(x in t for x in ("RSSMRA", "IT60X", "00743110157", "mario@", "1234567"))
    assert "the tax code" in t and "the VAT number" in t


def test_spoken_replies() -> None:
    assert spoken_decision("Yes, go ahead") is True
    assert spoken_decision("No, never mind") is False
    assert spoken_decision("show me October") is None
    options = ["Six-month trend", "By agent", "Compare with September"]
    assert spoken_option("the second one", options) == "By agent"
    assert spoken_option("3", options) == "Compare with September"
    assert spoken_option("show it by agent", options) == "By agent"
    assert spoken_option("dunno", options) is None
    assert is_echo(
        "due four thousand euros", "In October due four thousand euros, less than before."
    )
    assert not is_echo("wait show me September", "In October due four thousand euros.")


def test_charts_are_sanitised_per_form() -> None:
    c = to_chart({
        "type": "line",
        "title": "Da pagare",
        "name": "2026",
        "highlight": "Ott",
        "points": [{"label": "Set", "value": 4626}, {"label": "Ott", "value": "4572"}],
        "compare": {
            "name": "2025",
            "points": [
                {"label": "Ott", "value": 4000},
                {"label": "Dic", "value": 1},
                {"label": "Set", "value": 3900},
            ],
        },
    })
    assert (
        c
        and c["highlight"] == "Ott"
        and c["compare"]["points"]
        == [{"label": "Set", "value": 3900}, {"label": "Ott", "value": 4000}]
    )
    big = to_chart({
        "type": "column",
        "title": "t" * 200,
        "points": [{"label": "x" * 60 + str(i), "value": i} for i in range(40)]
        + [{"label": "inf", "value": math.inf}],
    })
    assert (
        big
        and len(big["points"]) == 24
        and len(big["title"]) == 80
        and len(big["points"][0]["label"]) == 40
    )
    assert (
        to_chart({
            "type": "line",
            "title": "x",
            "points": [{"label": "a", "value": None}, {"label": "b", "value": True}],
        })
        is None
    )
    donut = to_chart({
        "type": "donut",
        "title": "Q",
        "points": [
            {"label": k, "value": v}
            for k, v in [
                ("A", 1),
                ("B", 7),
                ("C", 3),
                ("D", 4),
                ("E", 5),
                ("F", 6),
                ("G", 2),
                ("Neg", -9),
            ]
        ],
    })
    assert (
        donut
        and [p["label"] for p in donut["points"]] == ["B", "F", "E", "D", "C", OTHER_LABEL]
        and donut["points"][-1]["value"] == 3
    )
    kpi = to_chart({
        "type": "kpi",
        "title": "Oct",
        "items": [
            {"label": "Revenue", "value": 4572, "unit": "eur", "delta": -1.2},
            {"label": "Orders", "value": 54, "unit": "count"},
            {"label": "c", "value": 1},
            {"label": "d", "value": 2},
            {"label": "e", "value": 3},
        ],
    })
    assert (
        kpi
        and len(kpi["items"]) == 4
        and kpi["items"][0]["unit"] == "eur"
        and kpi["items"][1]["unit"] == "count"
        and kpi["items"][1]["delta"] is None
        # No unit is a plain number, never euros: «Orders shipped 52» is not 52 €.
        and kpi["items"][2]["unit"] == "count"
    )


def test_diagrams_and_questions_are_sanitised() -> None:
    d = to_diagram({
        "kind": "steps",
        "title": "Return",
        "steps": [
            {"label": "Requested", "state": "done"},
            {"label": "Received", "state": "weird"},
            {"label": ""},
        ],
    })
    assert d == {
        "kind": "steps",
        "title": "Return",
        "steps": [
            {"label": "Requested", "note": "", "state": "done"},
            {"label": "Received", "note": "", "state": "todo"},
        ],
    }
    cmp_ = to_diagram({
        "kind": "compare",
        "title": "Order 1042",
        "columns": ["Shop", "Carrier"],
        "rows": [
            {"label": "City", "left": "Dublin", "right": "DUBLIN"},
            {"label": "Postcode", "left": "X", "right": "Y"},
        ],
    })
    assert cmp_ and [r["differs"] for r in cmp_["rows"]] == [False, True]
    assert (
        to_diagram({"kind": "tree", "title": "t", "root": {"label": "x"}, "children": []}) is None
    )
    assert to_ask({
        "question": "Quale?",
        "options": [
            "Poesia",
            {"label": "Viaggi"},
            "poesia",
            "",
            "Storia",
            "Arte",
            "Scienza",
            "Altra",
        ],
        "multiple": True,
    }) == {
        "question": "Quale?",
        "options": ["Poesia", "Viaggi", "Storia", "Arte", "Scienza"],
        "multiple": True,
        "other": False,
    }
    assert to_ask({"question": "Quale?", "options": ["Una"]}) is None


def test_history_repair_and_window() -> None:
    msgs = [
        Stored("user", [{"text": "a"}]),
        Stored("assistant", [{"toolUse": {"id": "1", "name": "x", "input": {}}}]),
        Stored("user", [{"text": "b"}]),
        Stored("user", [{"text": "c"}]),
    ]
    fixed = repair(msgs)
    assert [m.role for m in fixed] == ["user", "assistant", "user"]
    assert fixed[2].content[0]["toolResult"]["text"] == INTERRUPTED
    win = window(
        [Stored("user", [{"toolResult": {"id": "z", "name": "x", "text": "", "ok": True}}]), *msgs],
        10,
        10_000,
    )
    assert win[0].content[0] == {"text": "a"}


def test_a_duration_is_its_own_unit_and_a_chart_is_checked_against_the_tools() -> None:
    kpi = to_chart({
        "type": "kpi",
        "title": "Uso",
        "items": [
            {"label": "Turni", "value": 11},
            {"label": "Prima parola", "value": 4338, "unit": "ms"},
            {"label": "Quota", "value": 33.3, "unit": "pct", "delta": 7},
        ],
    })
    assert kpi
    assert [it["unit"] for it in kpi["items"]] == ["count", "ms", "pct"]
    seen = numbers_in(
        '{"turns": 11, "medianFirstTokenMs": 4338, "share": 33.3333, "at": "2026-11-25"}'
    )
    # Rounded as the chart writes it is still the tool's number; a delta is not a figure.
    assert ungrounded(kpi, seen) == []
    assert ungrounded(kpi, numbers_in('{"turns": 11, "medianFirstTokenMs": 7762}')) == [4338, 33.3]
    bars = to_chart({"type": "column", "title": "Mesi", "points": [{"label": "Ott", "value": 3}]})
    assert bars
    assert ungrounded(bars, set()) == [3]
