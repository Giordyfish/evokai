"""Identifiers an application declares (``application.identifiers``): found, masked, tokenised and
spoken like the built-in types, from the config file to a turn."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from evokai.agents.bootstrap import bootstrap
from evokai.audio.speech.speakable import redact, speakable
from evokai.core.errors import ConfigError
from evokai.guard import identifiers, pii
from evokai.i18n import speech_rules
from tests.conftest import Env, Tool

ORDER = "ORD-123456"
CARD = "4111 1111 1111 1111"
LIBRARY = "4111111111111111"  # a library card number that also passes as a payment card
MOBILE = "+39 333 1234567"

DECLARED: list[dict[str, Any]] = [
    {
        "name": "order_number",
        "label": "ORDER_NUMBER",
        "match": [{"pattern": r"ORD-\d{6}"}],
        "speech": {"words": {"en": "the order number", "it": "il numero d’ordine"}},
    },
    {
        "name": "library_card",
        "match": [
            {"digits": 12, "grouped": False},
            {"digits": [14, 16], "context": r"library card|tessera"},
            {"digits": 10, "check": "luhn"},
        ],
        "speech": {"read": "spell"},
    },
]


def _ids(raw: list[dict[str, Any]] | None = None) -> tuple[pii.Identifier, ...]:
    return tuple(s.compiled() for s in identifiers.parse(raw or DECLARED))


def _types(text: str, ids: tuple[pii.Identifier, ...] | None = None) -> list[str]:
    return [s.type for s in pii.find(text, None, _ids() if ids is None else ids)]


def test_a_pattern_is_found_masked_and_never_inside_a_longer_word() -> None:
    ids = _ids()
    text = f"Order {ORDER} and ord-654321, not XORD-111111 nor ORD-1234567."
    assert [(s.type, s.value) for s in pii.find(text, None, ids)] == [
        ("order_number", ORDER),
        ("order_number", "ord-654321"),
    ]
    assert pii.mask(text, None, ids) == (
        "Order [ORDER_NUMBER] and [ORDER_NUMBER], not XORD-111111 nor ORD-1234567."
    )
    exact = _ids([
        {"name": "order_number", "match": [{"pattern": "ORD-\\d{6}", "ignore_case": False}]}
    ])
    assert _types("ord-654321", exact) == []
    assert pii.mask(ORDER, None, exact) == "[ORDER_NUMBER]"  # the label is the name in upper case


def test_a_number_is_found_like_the_built_in_numbers() -> None:
    together = _ids([{"name": "member_id", "match": [{"digits": 12, "grouped": False}]}])
    grouped = _ids([{"name": "member_id", "match": [{"digits": 12}]}])
    assert _types("member 123456789012", together) == ["member_id"]
    assert _types("member 1234 5678 9012", together) == []
    assert _types("member 1234 5678 9012", grouped) == ["member_id"]
    assert _types("member 1234-5678-9012", grouped) == ["member_id"]
    # Never a piece of a longer number, a decimal, or a number written after IT.
    assert _types("member 1234567890123", grouped) == []
    assert _types("total 123456789012.50", grouped) == []
    assert _types("VAT IT 123456789012", grouped) == []
    short = _ids([{"name": "pin_code", "match": [{"digits": [5, 6]}]}])
    assert _types("code 12345 or 123456, not 1234", short) == ["pin_code", "pin_code"]


def test_a_checksum_and_a_context_narrow_what_counts() -> None:
    assert _types("number 1234567897") == ["library_card"]  # 10 digits, Luhn
    assert _types("number 1234567898") == []
    assert _types("library card 12345678901234") == ["library_card"]
    assert _types("reference 12345678901234") == []
    far = "library card " + "x" * 40 + " 12345678901234"
    assert _types(far) == []  # the context is looked for in the 30 characters before
    wide = _ids([
        {
            "name": "library_card",
            "match": [{"digits": 14, "context": "library card", "context_chars": 80}],
        }
    ])
    assert _types(far, wide) == ["library_card"]


def test_on_the_same_text_built_in_types_win_unless_the_context_confirms() -> None:
    ids = _ids()
    assert _types(f"paid with {LIBRARY}", ids) == ["card"]
    assert _types(f"library card {LIBRARY}", ids) == ["library_card"]
    # A declared identifier wins over a phone number.
    phone_like = _ids([{"name": "ticket", "match": [{"digits": 12, "grouped": False}]}])
    assert _types("0039333123456", ()) == ["phone"]
    assert _types("ticket 003933312345", phone_like) == ["ticket"]
    # Built-in detection is unchanged by declarations that do not match.
    text = f"card {CARD}, call {MOBILE}"
    assert _types(text, ids) == _types(text, ()) == ["card", "phone"]


def test_types_select_declared_identifiers_too() -> None:
    ids = _ids()
    text = f"{ORDER}, {CARD}"
    assert [s.type for s in pii.find(text, {"order_number"}, ids)] == ["order_number"]
    assert [s.type for s in pii.find(text, {"card"}, ids)] == ["card"]


def test_tokens_of_declared_identifiers_are_reversible() -> None:
    vault = pii.Vault(b"k" * 32, None, _ids())
    text, spans = vault.tokenize(f"Status of {ORDER}?")
    assert ORDER not in text and len(spans) == 1
    assert re.fullmatch(r"Status of \[ORDER_NUMBER_[0-9a-f]{6}\]\?", text)
    assert vault.restore(text) == f"Status of {ORDER}?"
    assert vault.restore_any({"q": [text]}) == {"q": [f"Status of {ORDER}?"]}
    assert vault.restore("[ORDER_NUMBER_000000]") == "[ORDER_NUMBER_000000]"


def test_masking_outside_a_turn_knows_every_declared_identifier() -> None:
    pii.declare("app-under-test", _ids())
    try:
        assert pii.mask(f"log line about {ORDER}") == "log line about [ORDER_NUMBER]"
    finally:
        pii.declare("app-under-test", ())
    assert pii.mask(f"log line about {ORDER}") == f"log line about {ORDER}"


@pytest.mark.parametrize(
    ("read", "spoken"),
    [
        ("word", "Your order is the order number."),
        ("spell", "Your order is O R D 1 2 3 4 5 6."),
        ("group", "Your order is ORD 123 456."),
        ("omit", "Your order is ."),
        ("verbatim", f"Your order is {ORDER}."),
    ],
)
def test_speech_says_a_declared_identifier_the_way_it_asks(read: str, spoken: str) -> None:
    raw = [{**DECLARED[0], "speech": {**DECLARED[0]["speech"], "read": read}}]
    rules = speech_rules("en", _ids(raw))
    assert redact(f"Your order is {ORDER}.", rules) == spoken


def test_speech_words_follow_the_locale_and_built_ins_still_apply() -> None:
    ids = _ids()
    it = speakable(
        f"L'ordine **{ORDER}**, IBAN IT60X0542811101000000123456.", speech_rules("it", ids)
    )
    assert it == "L'ordine il numero d’ordine, IBAN l’IBAN."
    assert "the order number" in redact(ORDER, speech_rules("pt-BR", ids))  # English fallback
    unnamed = _ids([{"name": "order_number", "match": [{"pattern": r"ORD-\d{6}"}]}])
    assert redact(ORDER, speech_rules("en", unnamed)) == "order number"
    assert speech_rules("en", ids) is speech_rules("en", _ids())  # one cached instance
    assert redact(ORDER, speech_rules("en")) == ORDER  # no declarations, nothing said differently


def test_speech_can_recognise_a_looser_shape_than_the_guard() -> None:
    raw = [
        {
            "name": "member_id",
            "match": [{"digits": 12, "grouped": False}],
            "speech": {"words": {"en": "the member id"}, "match": [{"pattern": r"M\d{12}"}]},
        }
    ]
    ids = _ids(raw)
    assert _types("M123456789012", ids) == []  # the guard keeps to its own match
    assert redact("Id M123456789012 or 123456789012.", speech_rules("en", ids)) == (
        "Id the member id or the member id."
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"name": "iban", "match": [{"pattern": "X"}]}, "built-in type"),
        ({"name": "Order", "match": [{"pattern": "X"}]}, "lower case"),
        ({"name": "order", "match": []}, "at least 1"),
        ({"name": "order", "match": [{"pattern": "("}]}, "not a valid regular expression"),
        ({"name": "order", "match": [{"pattern": "x*"}]}, "matches an empty text"),
        ({"name": "order", "match": [{"pattern": "X", "digits": 4}]}, "either a pattern or digits"),
        ({"name": "order", "match": [{"pattern": "X", "grouped": False}]}, "grouped applies"),
        ({"name": "order", "match": [{"digits": [9, 4]}]}, "shortest first"),
        ({"name": "order", "match": [{"digits": 4, "check": "mod11"}]}, "luhn"),
        ({"name": "order", "label": "IBAN", "match": [{"digits": 4}]}, "built-in type"),
        ({"name": "order", "match": [{"digits": 4}], "speech": {"read": "sing"}}, "word"),
        (
            {"name": "order", "match": [{"digits": 4}], "speech": {"words": {"english": "x"}}},
            "locale",
        ),
        ({"name": "order", "match": [{"digits": 4}], "extra": 1}, "Extra inputs"),
    ],
)
def test_invalid_declarations_are_refused(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        identifiers.parse([raw])


def test_a_name_or_a_label_is_declared_once() -> None:
    one = {"name": "order", "match": [{"digits": 4}]}
    with pytest.raises(ValueError, match="declared twice"):
        identifiers.parse([one, one])
    with pytest.raises(ValueError, match="labels used twice"):
        identifiers.parse([one, {"name": "other", "label": "ORDER", "match": [{"digits": 5}]}])
    with pytest.raises(ValueError, match="must be a list"):
        identifiers.parse({"name": "order"})


def test_invalid_stored_declarations_are_ignored() -> None:
    assert identifiers.compile_all([{"name": "iban"}]) == ()
    assert identifiers.compile_all(None) == ()


CONFIG = """
application:
  slug: shop
  name: Shop
  identifiers:
    - name: order_number
      label: ORDER_NUMBER
      match:
        - pattern: 'ORD-\\d{6}'
      speech: {words: {en: the order number}}
  security:
    pii: {types: [email, order_number]}
agents:
  - name: helper
    model: "function:primary"
    instructions: "You are an assistant."
"""
WITHOUT = """
application:
  slug: shop
  name: Shop
agents:
  - name: helper
    model: "function:primary"
    instructions: "You are an assistant."
"""


async def test_a_config_file_declares_identifiers_and_may_name_them_in_pii_types(
    env: Env, tmp_path: Path
) -> None:
    path = tmp_path / "app.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    store, keyring = env.svc.store, env.svc.keyring
    app_id = await bootstrap(store, keyring, path, {})
    app = await store.applications.get(app_id)
    assert app is not None
    assert app.defaults["identifiers"] == [
        {
            "name": "order_number",
            "label": "ORDER_NUMBER",
            "match": [{"pattern": "ORD-\\d{6}"}],
            "speech": {"words": {"en": "the order number"}},
        }
    ]
    assert [i.name for i in identifiers.of_app(app)] == ["order_number"]
    assert (app.security or {})["pii"]["types"] == ["email", "order_number"]

    # The file is the whole set: identifiers left out of it are removed.
    path.write_text(WITHOUT, encoding="utf-8")
    await bootstrap(store, keyring, path, {})
    app = await store.applications.get(app_id)
    assert app is not None and "identifiers" not in app.defaults
    assert identifiers.of_app(app) == ()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (CONFIG.replace("[email, order_number]", "[email, ticket_id]"), "ticket_id"),
        (
            CONFIG.replace("[email, order_number]", "[email]").replace(
                "    instructions:",
                "    security:\n      pii:\n        types: [ticket_id]\n    instructions:",
            ),
            "agent helper",
        ),
        (CONFIG.replace("name: order_number", "name: iban"), "application.identifiers"),
    ],
)
async def test_a_config_file_with_unknown_or_invalid_identifiers_is_refused(
    env: Env, tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "app.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})


async def _declare(env: Env) -> None:
    app = await env.svc.store.applications.get(env.app_id)
    assert app is not None
    defaults = {**(app.defaults or {}), "identifiers": DECLARED}
    await env.svc.store.applications.update(env.app_id, defaults=defaults)


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def test_a_turn_tokenises_declared_identifiers_for_the_model_only(env: Env) -> None:
    await _declare(env)
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"pii.model": "enforce"}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    token = re.compile(r"\[ORDER_NUMBER_[0-9a-f]{6}\]")

    def call(messages: list[Any]) -> list[Any]:
        (found,) = token.findall(str(messages))
        return [Tool("orders_month", {"period": found})]

    env.script.add("function:primary", call, lambda m: [f"Done: {token.findall(str(m))[-1]}."])
    tok = await env.token()
    events = await env.chat(tok["token"], text=f"Where is {ORDER}?")
    for _, messages in env.script.seen:
        assert ORDER not in str(messages)
    assert env.mcp.calls == [("orders_month", {"period": ORDER})]
    assert "".join(e.get("delta", "") for e in events if e["type"] == "text") == f"Done: {ORDER}."
    r = await env.client.get(
        "/agent/v1/admin/security/events", params={"control": "pii.model"}, headers=_adm(env)
    )
    (e,) = r.json()["items"]
    assert set(e["details"]["types"]) == {"order_number"}


async def test_the_console_names_only_known_types(env: Env) -> None:
    policy = {"pii": {"types": ["iban", "order_number"]}}
    r = await env.client.put("/agent/v1/admin/security", json=policy, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "unknown_pii_types"
    await _declare(env)
    r = await env.client.put("/agent/v1/admin/security", json=policy, headers=_adm(env))
    assert r.status_code == 200, r.text


async def test_a_voice_turn_says_declared_identifiers_the_way_they_ask(env: Env) -> None:
    await _declare(env)
    env.script.add("function:primary", [f"Order {ORDER} has shipped."])
    tok = await env.token()
    events = await env.chat(tok["token"], text="Where is my order?", mode="voice")
    said = [e["text"] for e in events if e["type"] == "say"]
    assert said == ["Order the order number has shipped."]
    assert ORDER in "".join(e.get("delta", "") for e in events if e["type"] == "text")
