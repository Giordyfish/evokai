"""Long-term memory: consolidation, recall, consent, the person's view, episodes, privacy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa

from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.memory.consolidate import Candidate, decide, normalize_fact
from evokai.memory.recall import Recallable, rank
from evokai.memory.service import MemoryState
from tests.conftest import ORIGIN, Env, Tool, system_text

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


def _srv(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.server_key}"}


def _sess(tok: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


async def _settings(env: Env, **fields: Any) -> None:
    r = await env.client.patch("/agent/v1/admin/application", json=fields, headers=_adm(env))
    assert r.status_code == 200, r.text


def _who(env: Env, subject: str = "u1") -> Who:
    return Who(env.app_id, subject)


# ── consolidation ────────────────────────────────────────────────────────────────────────────────


def test_a_restatement_is_a_duplicate_and_keeps_the_richer_wording() -> None:
    old = [Candidate("a", "Prefers rounded amounts")]
    d = decide(normalize_fact("prefers rounded amounts in reports."), None, old)
    assert d.action == "duplicate" and d.target == "a"
    assert d.text == "prefers rounded amounts in reports"


def test_a_changed_detail_or_a_flipped_negation_supersedes() -> None:
    old = [
        Candidate("north", "Looks after the poetry shelf"),
        Candidate("charts", "Likes charts in answers"),
    ]
    moved = decide("Now looks after the travel shelf", None, old)
    assert moved.action == "supersede" and moved.target == "north"
    flipped = decide("Does not like charts in answers", None, old)
    assert flipped.action == "supersede" and flipped.target == "charts"
    number = decide(
        "Closes the month on day 5", None, [Candidate("d", "Closes the month on day 3")]
    )
    assert number.action == "supersede" and number.target == "d"


def test_an_unrelated_fact_is_new_and_vectors_decide_when_both_have_one() -> None:
    old = [Candidate("a", "Looks after the poetry shelf", [1.0, 0.0, 0.0])]
    assert decide("Prefers e-mail summaries", None, old).action == "new"
    assert decide("Segue lo scaffale di poesia", [0.99, 0.1, 0.0], old).action == "duplicate"
    assert decide("Segue lo scaffale di viaggi", [0.8, 0.6, 0.0], old).action == "supersede"
    assert decide("Ama il caffè", [0.0, 0.0, 1.0], old).action == "new"


# ── recall ───────────────────────────────────────────────────────────────────────────────────────


def _mem(
    i: str, kind: str, text: str, days: float, vector: list[float] | None = None
) -> Recallable:
    return Recallable(i, kind, text, NOW - timedelta(days=days), vector)


def test_recall_keeps_preferences_and_ranks_facts_by_relevance_then_recency() -> None:
    items = [
        _mem("p1", "preference", "Prefers rounded amounts", 300),
        _mem("f-old", "semantic", "Looks after the poetry shelf", 200),
        _mem("f-new", "semantic", "Looks after the poetry shelf for the sale", 2),
        _mem("f-other", "semantic", "Works on gift orders", 1),
        _mem("e1", "episodic", "Checked the October orders of the poetry shelf", 5),
        _mem("noise", "semantic", "Likes short meetings", 0),
    ]
    picked = rank(items, query="How is the poetry shelf doing?", query_vector=None, now=NOW, k=2)
    assert picked[0].id == "p1"
    rest = [p.id for p in picked[1:]]
    assert len(rest) == 2 and "noise" not in rest and "f-other" not in rest
    # The recent fact outranks the old one on the same words.
    assert rest[0] == "f-new"


def test_recall_skips_near_duplicates_and_irrelevant_vectors() -> None:
    items = [
        _mem("a", "semantic", "Poetry shelf", 1, [1.0, 0.0]),
        _mem("b", "semantic", "The poetry shelf", 1, [0.99, 0.05]),
        _mem("c", "semantic", "Gift orders", 1, [0.0, 1.0]),
        _mem("d", "semantic", "Something else", 1, [0.7, 0.7]),
    ]
    picked = rank(items, query="poetry", query_vector=[1.0, 0.0], now=NOW, k=3)
    ids = [p.id for p in picked]
    assert ids[0] == "a" and "b" not in ids and "c" not in ids and "d" in ids


def test_few_memories_are_all_recalled_without_a_query_embedding() -> None:
    items = [
        _mem("a", "semantic", "Works on gifts", 30),
        _mem("b", "episodic", "Closed October", 1),
    ]
    picked = rank(items, query="anything", query_vector=None, now=NOW, k=6)
    assert [p.id for p in picked] == ["b", "a"]


# ── consent ──────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "consent", "active", "ask"),
    [
        ("on", None, True, False),
        ("on", True, True, False),
        ("on", False, False, False),
        ("ask", None, False, True),
        ("ask", True, True, False),
        ("ask", False, False, False),
        ("off", None, False, False),
    ],
)
def test_the_consent_states(mode: str, consent: bool | None, active: bool, ask: bool) -> None:
    state = MemoryState(mode, consent)
    assert (state.active, state.ask) == (active, ask)


async def test_ask_mode_offers_no_memory_until_the_person_says_yes(env: Env) -> None:
    await _settings(env, memory_mode="ask")
    tok = await env.token()
    assert tok["memory"] == {"mode": "ask", "consent": "unknown", "active": False, "ask": True}
    await env.chat(tok["token"], text="Ciao")
    assert "memory_remember" not in env.script.tools_seen
    r = await env.client.post(
        "/agent/v1/session/memory-consent", json={"granted": True}, headers=_sess(tok)
    )
    assert r.status_code == 200 and r.json()["memory"]["active"] is True
    env.script.add(
        "function:primary",
        [Tool("memory_remember", {"fact": "Segue lo scaffale di poesia", "kind": "semantic"})],
        ["Ricordato."],
    )
    await env.chat(tok["token"], text="Seguo lo scaffale di poesia")
    items = (await env.client.get("/agent/v1/session/memories", headers=_sess(tok))).json()
    assert [(x["text"], x["kind"]) for x in items["items"]] == [
        ("Segue lo scaffale di poesia", "semantic")
    ]


async def test_consent_at_mint_and_a_no_deletes_what_was_remembered(env: Env) -> None:
    await _settings(env, memory_mode="ask")
    tok = await env.token(memoryConsent=True)
    assert tok["memory"]["active"] is True
    await env.svc.store.memories.add(_who(env), "Preferisce i grafici")
    again = await env.token(memoryConsent=False)
    assert again["memory"]["consent"] == "declined" and again["memory"]["active"] is False
    assert await env.svc.store.memories.facts(_who(env)) == []
    r = await env.client.post(
        "/agent/v1/memories/consent", json={"subject": "u1", "granted": True}, headers=_srv(env)
    )
    assert r.json()["memory"]["active"] is True
    listed = (await env.client.get("/agent/v1/memories?subject=u1", headers=_srv(env))).json()
    assert listed["memory"]["consent"] == "granted"


async def test_on_mode_still_respects_a_person_who_said_no(env: Env) -> None:
    await env.token(memoryConsent=False)
    tok = await env.token()
    await env.chat(tok["token"], text="Ciao")
    assert "memory_remember" not in env.script.tools_seen
    info = (await env.client.get("/agent/v1/session", headers=_sess(tok))).json()
    assert info["memory"]["mode"] == "on" and info["memory"]["active"] is False


async def test_off_mode_neither_stores_nor_recalls(env: Env) -> None:
    await env.svc.store.memories.add(_who(env), "Preferisce i grafici")
    await _settings(env, memory_mode="off")
    tok = await env.token()
    await env.chat(tok["token"], text="Ciao")
    assert "Preferisce i grafici" not in system_text(env.script.seen[-1][1])
    assert "memory_remember" not in env.script.tools_seen
    r = await env.client.post(
        "/agent/v1/session/memory-consent", json={"granted": True}, headers=_sess(tok)
    )
    assert r.status_code == 409


# ── writing, recalling, editing ──────────────────────────────────────────────────────────────────


async def test_memories_are_consolidated_and_recalled_spotlighted_in_a_new_conversation(
    env: Env,
) -> None:
    env.script.add(
        "function:primary",
        [Tool("memory_remember", {"fact": "Segue lo scaffale di poesia", "kind": "semantic"}, "a")],
        [
            Tool(
                "memory_remember", {"fact": "segue lo scaffale di poesia.", "kind": "semantic"}, "b"
            )
        ],
        [
            Tool(
                "memory_remember",
                {"fact": "Non segue più lo scaffale di poesia", "kind": "semantic"},
                "c",
            )
        ],
        ["Fatto."],
    )
    tok = await env.token()
    await env.chat(tok["token"], text="Ricorda dove lavoro")
    rows = await env.svc.store.memories.items(_who(env))
    assert [r.fact for r in rows] == ["Non segue più lo scaffale di poesia"]
    async with env.svc.db.session() as s:
        everything = (await s.execute(sa.select(m.Memory))).scalars().all()
    old = next(x for x in everything if x.fact == "Segue lo scaffale di poesia")
    assert old.superseded_by == rows[0].id and old.embedding is not None

    tok2 = await env.token()
    await env.chat(tok2["token"], text="Come va lo scaffale di poesia?")
    system = system_text(env.script.seen[-1][1])
    assert "Non segue più lo scaffale di poesia" in system
    assert "<<untrusted:" in system and "source=memory" in system
    assert "Segue lo scaffale di poesia\n" not in system


async def test_the_person_lists_edits_and_deletes_and_the_guard_still_applies(env: Env) -> None:
    await env.svc.memory.remember(
        await env.svc.store.applications.get(env.app_id), _who(env), "Preferisce i grafici"
    )
    tok = await env.token()
    items = (await env.client.get("/agent/v1/session/memories", headers=_sess(tok))).json()["items"]
    (item,) = items
    r = await env.client.patch(
        f"/agent/v1/session/memories/{item['id']}",
        json={"text": "Preferisce le tabelle"},
        headers=_sess(tok),
    )
    assert r.status_code == 200 and r.json()["source"] == "person"
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"memory.write": "enforce"}}, headers=_adm(env)
    )
    assert r.status_code == 200
    r = await env.client.patch(
        f"/agent/v1/session/memories/{item['id']}",
        json={"text": "Il mio CF è RSSMRA85T10A562S"},
        headers=_sess(tok),
    )
    assert r.status_code == 400 and r.json()["error"] == "memory_refused"
    r = await env.client.delete(f"/agent/v1/session/memories/{item['id']}", headers=_sess(tok))
    assert r.json() == {"deleted": 1}
    r = await env.client.delete(f"/agent/v1/session/memories/{item['id']}", headers=_sess(tok))
    assert r.status_code == 404
    await env.svc.store.memories.add(_who(env), "A")
    await env.svc.store.memories.add(_who(env), "B")
    r = await env.client.delete("/agent/v1/session/memories", headers=_sess(tok))
    assert r.json() == {"deleted": 2}


async def test_a_session_never_reaches_another_persons_memory(env: Env) -> None:
    other = await env.svc.store.memories.add(_who(env, "u2"), "Segreto di u2")
    tok = await env.token()
    r = await env.client.delete(f"/agent/v1/session/memories/{other}", headers=_sess(tok))
    assert r.status_code == 404
    r = await env.client.patch(
        f"/agent/v1/session/memories/{other}", json={"text": "x"}, headers=_sess(tok)
    )
    assert r.status_code == 404


async def test_memories_expire_with_the_applications_ttl(env: Env) -> None:
    await _settings(env, memory_ttl_days=30)
    app = await env.svc.store.applications.get(env.app_id)
    done = await env.svc.memory.remember(app, _who(env), "Preferisce i grafici")
    row = await env.svc.store.memories.get(_who(env), done.id)
    assert row is not None and row.expires_at is not None
    later = datetime.now(UTC) + timedelta(days=31)
    assert await env.svc.store.memories.purge(later) == 1
    assert await env.svc.store.memories.facts(_who(env)) == []


# ── episodes ─────────────────────────────────────────────────────────────────────────────────────


async def test_a_finished_conversation_leaves_an_episode_for_agents_that_keep_them(
    env: Env,
) -> None:
    r = await env.client.get("/agent/v1/admin/agents", headers=_adm(env))
    helper = next(a for a in r.json()["items"] if a["name"] == "helper")
    helper = {k: v for k, v in helper.items() if k != "version"}
    helper["memory"] = {"episodes": True, "recall": 4}
    r = await env.client.put("/agent/v1/admin/agents/helper", json=helper, headers=_adm(env))
    assert r.status_code == 200, r.text
    tok = await env.token()
    await env.chat(tok["token"], text="Controlla gli ordini di ottobre")
    await env.chat(tok["token"], text="Rimborsali")
    env.script.add("function:primary", ["Ha controllato e rimborsato gli ordini di ottobre."])
    later = datetime.now(UTC) + timedelta(hours=1)
    assert await env.svc.memory.summarize_finished(later) == 1
    assert await env.svc.memory.summarize_finished(later) == 0  # once per conversation
    (episode,) = await env.svc.store.memories.items(_who(env))
    assert episode.kind == "episodic" and episode.session_id == tok["conversationId"]
    assert episode.source == "summary" and episode.agent_name == "helper"
    usage = await env.svc.store.usage.report(
        env.app_id, start=later - timedelta(days=1), end=later, group_by=["tags"]
    )
    assert any("evokai:memory" in r["tags"] for r in usage)

    tok2 = await env.token()
    await env.chat(tok2["token"], text="Cosa abbiamo fatto con ottobre?")
    assert "(episodic) Ha controllato e rimborsato" in system_text(env.script.seen[-1][1])


async def test_no_episode_without_opt_in_or_with_nothing_worth_remembering(env: Env) -> None:
    tok = await env.token()
    await env.chat(tok["token"], text="Uno")
    await env.chat(tok["token"], text="Due")
    later = datetime.now(UTC) + timedelta(hours=1)
    assert await env.svc.memory.summarize_finished(later) == 0  # helper keeps no episodes


# ── privacy ──────────────────────────────────────────────────────────────────────────────────────


async def test_export_and_erase_include_memories_and_consent(env: Env) -> None:
    app = await env.svc.store.applications.get(env.app_id)
    await env.svc.memory.set_consent(app, _who(env), True, "person")
    first = await env.svc.memory.remember(
        app, _who(env), "Looks after the poetry shelf", kind="semantic"
    )
    await env.svc.memory.remember(
        app, _who(env), "Now looks after the travel shelf", kind="semantic"
    )
    r = await env.client.post("/agent/v1/privacy/export", json={"subject": "u1"}, headers=_srv(env))
    data = r.json()
    assert data["memoryConsent"]["granted"] is True and data["memoryConsent"]["source"] == "person"
    by_text = {x["text"]: x for x in data["memories"]}
    assert by_text["Looks after the poetry shelf"]["supersededBy"]
    assert by_text["Looks after the poetry shelf"]["id"] == first.id
    r = await env.client.post("/agent/v1/privacy/erase", json={"subject": "u1"}, headers=_srv(env))
    assert r.json()["memories"] == 2
    data = (
        await env.client.post("/agent/v1/privacy/export", json={"subject": "u1"}, headers=_srv(env))
    ).json()
    assert data["memories"] == [] and data["memoryConsent"] is None
