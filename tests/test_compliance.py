"""Compliance packs: generation, traceability, no personal data, classification, snapshots, diff."""

from __future__ import annotations

import string
from typing import Any

import pytest

from evokai.compliance.classify import (
    EXEMPTED,
    HIGH_RISK,
    LIMITED,
    MINIMAL,
    PROHIBITED,
    classify,
)
from evokai.compliance.packs import PACKS
from evokai.compliance.texts import SOURCES, T
from evokai.guard import pii, secrets
from tests.conftest import Env, Tool

EMAIL = "mario.rossi@example.com"
CF = "RSSMRA85T10A562S"
IBAN = "IT60X0542811101000000123456"
BASE = "/agent/v1/admin/compliance"

PROFILE: dict[str, Any] = {
    "system_name": "Assistente Inkwell Books",
    "system_description": "Answers the bookshop staff about orders and returns.",
    "intended_purpose": "Help staff read order data; it decides nothing on its own.",
    "controller": "Inkwell Books S.r.l.",
    "processor": "Example Hosting S.r.l.",
    "ai_act": {"role": "provider_deployer", "disclosure_shown": True},
    "gdpr": {
        "purposes": ["Support to the bookshop staff"],
        "legal_basis": "legitimate",
        "dpo_appointed": True,
        "large_scale": False,
    },
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _put_profile(env: Env, profile: dict[str, Any]) -> Any:
    return await env.client.put(f"{BASE}/profile", json=profile, headers=_adm(env))


async def _pack(env: Env, pack: str, lang: str = "en", fmt: str = "json") -> Any:
    r = await env.client.get(
        f"{BASE}/{pack}", params={"lang": lang, "format": fmt}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    return r.json() if fmt == "json" else r


async def _activity_with_personal_data(env: Env) -> None:
    """A conversation full of identifiers, and a tool listing (mutating tools known)."""
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        [f"Il cliente {EMAIL} ha CF {CF} e IBAN {IBAN}."],
    )
    tok = await env.token(subject=EMAIL)
    await env.chat(tok["token"], text=f"Cerca {CF} e scrivi a {EMAIL}, IBAN {IBAN}")


def _walk_items(pack: dict[str, Any]):
    for s in pack["sections"]:
        for b in s["blocks"]:
            yield b
            yield from b.get("items", [])


async def test_every_pack_generates_in_both_languages(env: Env) -> None:
    assert (await _put_profile(env, PROFILE)).status_code == 200
    await _activity_with_personal_data(env)
    for pack in PACKS:
        en, it = await _pack(env, pack, "en"), await _pack(env, pack, "it")
        assert en["lang"] == "en" and it["lang"] == "it"
        assert en["sections"] and len(en["sections"]) == len(it["sections"])
        # The same statements, by id, in both languages.
        ids_en = [x["id"] for x in _walk_items(en)]
        ids_it = [x["id"] for x in _walk_items(it)]
        assert ids_en == ids_it
        assert en["disclaimer"] != it["disclaimer"] and "legal advice" in en["disclaimer"]
        assert "consulenza legale" in it["disclaimer"]
    gdpr_it = await _pack(env, "gdpr", "it")
    text = str(gdpr_it)
    assert "Titolare del trattamento" in text and "Responsabile del trattamento" in text
    assert "Inkwell Books S.r.l." in text and "Legittimo interesse" in text
    gdpr_en = await _pack(env, "gdpr", "en")
    assert "Controller" in str(gdpr_en) and "Record of processing activities" in str(gdpr_en)
    act = await _pack(env, "ai-act", "it")
    assert "Classificazione dichiarata" in str(act)
    controls = await _pack(env, "controls", "en")
    assert "not a certification" in str(controls)
    assert controls["counts"]["iso27001"]["supported"] > 0
    # ISO/IEC 42001 Annex A: its 38 controls, most of them the organisation's.
    iso42001 = controls["counts"]["iso42001"]
    assert sum(iso42001.values()) == 38 and iso42001["organisation"] >= 12


async def test_every_statement_is_traceable_to_a_source(env: Env) -> None:
    await _put_profile(env, PROFILE)
    await _activity_with_personal_data(env)
    for pack in PACKS:
        for lang in ("en", "it"):
            p = await _pack(env, pack, lang)
            notes = {f["key"]: f for f in p["footnotes"]}
            for s in p["sections"]:
                for b in s["blocks"]:
                    if b["kind"] in ("p",):
                        assert b["sources"], f"{pack} {b['id']} has no source"
                    for i in b.get("items", []):
                        assert i["sources"], f"{pack} {b['id']}/{i['id']} has no source"
                        assert all(k in SOURCES for k in i["sources"]), i["sources"]
                        assert all(k in notes for k in i["sources"])
                    assert all(k in SOURCES for k in b["sources"])
            # Footnotes are numbered from 1 and each has a label in the pack's language.
            assert [f["n"] for f in p["footnotes"]] == list(range(1, len(p["footnotes"]) + 1))
            assert all(f["text"] != f["key"] for f in p["footnotes"])
            md = (await _pack(env, pack, lang, "md")).text
            assert "[^1]" in md and "[^1]: " in md
            html = (await _pack(env, pack, lang, "html")).text
            assert 'id="fn1"' in html and 'href="#fn1"' in html


async def test_no_personal_data_in_any_output(env: Env) -> None:
    await _put_profile(env, PROFILE)
    await _activity_with_personal_data(env)
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"conversation_ttl_days": 30}, headers=_adm(env)
    )
    assert r.status_code == 200
    r = await env.client.post(
        "/agent/v1/privacy/export",
        json={"subject": EMAIL},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert r.status_code == 200
    snap = (await env.client.post(f"{BASE}/snapshots", json={}, headers=_adm(env))).json()
    outputs: list[str] = []
    for pack in PACKS:
        for lang in ("en", "it"):
            for fmt in ("json", "md", "html"):
                r = await env.client.get(
                    f"{BASE}/{pack}", params={"lang": lang, "format": fmt}, headers=_adm(env)
                )
                outputs.append(r.text)
                r = await env.client.get(
                    f"{BASE}/snapshots/{snap['id']}/{pack}",
                    params={"lang": lang, "format": fmt},
                    headers=_adm(env),
                )
                outputs.append(r.text)
    full = await env.client.get(f"{BASE}/snapshots", headers=_adm(env))
    outputs.append(full.text)
    for text in outputs:
        found = pii.find(text)
        assert not found, [text[s.start : s.end] for s in found][:5]
        assert not secrets.find(text)
        assert EMAIL not in text and CF not in text and "mario" not in text.lower()


async def test_the_profile_refuses_personal_data_and_needs_the_owner(env: Env) -> None:
    bad = {**PROFILE, "notes": f"Ask {EMAIL}"}
    r = await _put_profile(env, bad)
    assert r.status_code == 400 and r.json()["error"] == "personal_data"
    assert "notes" in r.json()["message"]
    bad = {**PROFILE, "gdpr": {**PROFILE["gdpr"], "recipients": [f"Banca {IBAN}"]}}
    assert (await _put_profile(env, bad)).status_code == 400
    assert (await _put_profile(env, {"unknown": 1})).status_code == 400
    assert (await _put_profile(env, PROFILE)).status_code == 200
    got = (await env.client.get(f"{BASE}/profile", headers=_adm(env))).json()
    assert got["saved"] and got["profile"]["controller"] == "Inkwell Books S.r.l."
    audit = (await env.client.get("/agent/v1/admin/audit", headers=_adm(env))).json()
    assert any(e["action"] == "compliance.profile" for e in audit["items"])


async def test_html_is_printable_and_self_contained(env: Env) -> None:
    r = await _pack(env, "ai-act", "it", "html")
    assert r.headers["content-type"].startswith("text/html")
    assert "style-src 'unsafe-inline'" in r.headers["content-security-policy"]
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert "@media print" in r.text and "@page" in r.text and '<html lang="it">' in r.text
    assert "<script" not in r.text and "http://" not in r.text.replace("http://evokai", "")
    assert ".html" in r.headers["content-disposition"]
    md = await _pack(env, "gdpr", "en", "md")
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.text.startswith("# GDPR pack")


async def test_snapshots_keep_what_was_true_and_diff_both_languages(env: Env) -> None:
    await _put_profile(env, PROFILE)
    first = (
        await env.client.post(f"{BASE}/snapshots", json={"label": "baseline"}, headers=_adm(env))
    ).json()
    assert first["classification"] == LIMITED and "facts" not in first
    bad = await env.client.post(f"{BASE}/snapshots", json={"label": EMAIL}, headers=_adm(env))
    assert bad.status_code == 400
    await env.client.patch(
        "/agent/v1/admin/application",
        json={"conversation_ttl_days": 90, "redteam_gate": "enforce"},
        headers=_adm(env),
    )
    await env.client.put(
        "/agent/v1/admin/agents/triage",
        json={"name": "triage", "model": "function:primary", "channels": ["server"],
              "instructions": "Classify."},
        headers=_adm(env),
    )  # fmt: skip
    high = {**PROFILE, "ai_act": {**PROFILE["ai_act"], "annex_iii": ["employment"]}}
    await _put_profile(env, high)
    second = (await env.client.post(f"{BASE}/snapshots", json={}, headers=_adm(env))).json()
    assert second["classification"] == HIGH_RISK
    items = (await env.client.get(f"{BASE}/snapshots", headers=_adm(env))).json()["items"]
    assert [x["id"] for x in items] == [second["id"], first["id"]]

    # The first snapshot still renders what was true then.
    old = (await env.client.get(f"{BASE}/snapshots/{first['id']}/ai-act", headers=_adm(env))).json()
    assert old["classification"]["level"] == LIMITED
    assert any(m["value"].startswith(first["id"]) for m in old["meta"])

    for lang, word in (("en", "Logs are kept 90 days"), ("it", "I log sono conservati 90")):
        d = (
            await env.client.get(
                f"{BASE}/snapshots/diff",
                params={"from": second["id"], "to": first["id"], "lang": lang},
                headers=_adm(env),
            )
        ).json()
        assert d["from"]["id"] == first["id"]  # ordered by time whatever the query says
        act = d["packs"]["ai-act"]
        assert act["classification"] == {"before": LIMITED, "after": HIGH_RISK}
        assert any("triage" in x["text"] for x in act["added"])
        # Audit rows keep their ids: older changes are never reported as removed.
        assert not [x for x in act["removed"] if x["id"].startswith("changes.log/")]
        assert any(word in x["text"] for x in act["added"])
        paths = {f["path"] for f in d["facts"]}
        assert "application.conversationTtlDays" in paths
        assert "application.redteamGate" in paths
        assert d["counts"]["statements"] > 3
    missing = await env.client.get(
        f"{BASE}/snapshots/diff", params={"from": first["id"], "to": "nope"}, headers=_adm(env)
    )
    assert missing.status_code == 404


async def test_code_execution_shows_up_as_a_sub_processor(env: Env) -> None:
    from evokai.sandbox.tool import CodeService
    from tests.test_code_run import FakeInterpreter

    env.svc.code = env.svc.engine.code = CodeService(env.svc.settings, FakeInterpreter())
    gdpr = str(await _pack(env, "gdpr"))
    assert "AgentCore" not in gdpr
    await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "code", "name": "*", "value": True}]},
        headers=_adm(env),
    )
    gdpr = str(await _pack(env, "gdpr"))
    assert "Amazon Bedrock AgentCore (AWS)" in gdpr and "eu-north-1" in gdpr


def _facts(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "profile": {"ai_act": {}},
        "agents": [{"name": "a", "channels": ["browser"], "code": False}],
        "grants": {"browser": True, "voiceServer": False, "code": False},
        "application": {"conversationTtlDays": 30, "auditTtlDays": None},
        "deployment": {"auditTtlDays": 400},
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    ("answers", "agents", "level"),
    [
        ({}, None, LIMITED),
        ({}, [{"name": "a", "channels": ["server"], "code": False}], MINIMAL),
        ({"prohibited": ["social_scoring"], "annex_iii": ["employment"]}, None, PROHIBITED),
        ({"safety_component": True}, None, HIGH_RISK),
        ({"annex_iii": ["education"]}, None, HIGH_RISK),
        ({"annex_iii": ["education"], "exceptions": ["preparatory"]}, None, EXEMPTED),
        (
            {"annex_iii": ["education"], "exceptions": ["preparatory"], "profiling": True},
            None,
            HIGH_RISK,
        ),
        (
            {"emotion_recognition": True},
            [{"name": "a", "channels": ["server"], "code": False}],
            LIMITED,
        ),
    ],
)
def test_classification(answers: dict[str, Any], agents: Any, level: str) -> None:
    facts = _facts(profile={"ai_act": answers})
    if agents is not None:
        facts["agents"] = agents
    assert classify(facts).level == level


def test_obligations_follow_the_classification() -> None:
    limited = classify(_facts())
    keys = [o.key for o in limited.obligations]
    assert "o.art50_1" in keys and "o.art4" in keys and "o.art9" not in keys
    assert next(o for o in limited.obligations if o.key == "o.art50_1").warn == "w.disclosure"
    high = classify(_facts(profile={"ai_act": {"annex_iii": ["employment"]}}))
    keys = [o.key for o in high.obligations]
    assert {"o.art9", "o.art12", "o.art14", "o.art26", "o.art49", "o.art73"} <= set(keys)
    logs = next(o for o in high.obligations if o.key == "o.art12")
    assert logs.warn == "w.logs6m" and logs.warn_params == {"days": 30}
    stop = classify(_facts(profile={"ai_act": {"prohibited": ["manipulation"]}}))
    assert [o.key for o in stop.obligations] == ["o.stop"]
    voice = classify(
        _facts(
            grants={"browser": True, "voiceServer": True, "code": True},
            agents=[{"name": "a", "channels": ["browser"], "code": True}],
        )
    )
    assert voice.synthetic == ["text", "audio", "image"]


def test_texts_exist_in_both_languages_with_the_same_placeholders() -> None:
    fmt = string.Formatter()
    for key, (en, it) in {**T, **SOURCES}.items():
        assert en.strip() and it.strip(), key
        a = {f for _, f, _, _ in fmt.parse(en) if f}
        b = {f for _, f, _, _ in fmt.parse(it) if f}
        assert a == b, key
        assert "\u2014" not in en + it, key
    # Italian quotes are typographic and no exclamation marks.
    assert not any("!" in it for _en, it in T.values())


async def test_connections_show_up_in_both_packs(env: Env) -> None:
    gdpr = str(await _pack(env, "gdpr"))
    assert "Microsoft Graph" not in gdpr
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "connection", "name": "microsoft365", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text
    gdpr = await _pack(env, "gdpr", "it")
    assert "Microsoft 365 (Microsoft Graph)" in str(gdpr)
    assert "Posta, calendario e file degli account" in str(gdpr)
    act = str(await _pack(env, "ai-act"))
    assert "the person's own OAuth connection" in act
