"""Kiosk exit, consent, bug reports, versioned prompts, voices, sources never spoken."""

from __future__ import annotations

import sqlalchemy as sa

from evokai.audio.speech.speakable import SentenceSplitter
from evokai.db import models as m
from tests.conftest import ORIGIN, Env, system_text


def _h(tok: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


async def test_kiosk_exit_code_closes_the_session_with_proper_statuses(env: Env) -> None:
    tok = await env.token(exitCode="4321")
    assert (
        await env.client.post("/agent/v1/session/exit", json={"code": "0000"}, headers=_h(tok))
    ).status_code == 403
    assert (
        await env.client.post("/agent/v1/session/exit", json={"code": "4321"}, headers=_h(tok))
    ).status_code == 204
    again = await env.client.post("/agent/v1/session/exit", json={"code": "4321"}, headers=_h(tok))
    assert (again.status_code, again.json()["error"]) == (409, "closed")
    chat = await env.client.post("/agent/v1/session/chat", json={"text": "ciao"}, headers=_h(tok))
    assert (chat.status_code, chat.json()["error"]) == (409, "closed")
    no_code = await env.token()
    assert (
        await env.client.post("/agent/v1/session/exit", json={"code": "1"}, headers=_h(no_code))
    ).status_code == 404


async def test_too_many_exit_attempts_is_429(env: Env) -> None:
    tok = await env.token(exitCode="4321")
    for _ in range(5):
        await env.client.post("/agent/v1/session/exit", json={"code": "0000"}, headers=_h(tok))
    r = await env.client.post("/agent/v1/session/exit", json={"code": "4321"}, headers=_h(tok))
    assert r.status_code == 429


async def test_consent_is_recorded_with_the_session(env: Env) -> None:
    tok = await env.token(consent={"version": "2026-11", "acceptedAt": "2026-11-14T10:00:00Z"})
    async with env.svc.db.session() as s:
        row = (
            await s.execute(sa.select(m.Session).where(m.Session.id == tok["conversationId"]))
        ).scalar_one()
    assert row.consent and row.consent["version"] == "2026-11"
    assert row.exit_code_hash is None


async def test_bug_reports_reach_the_admin(env: Env) -> None:
    tok = await env.token()
    r = await env.client.post(
        "/agent/v1/session/bug-reports",
        json={"description": "Il grafico è vuoto", "context": {"page": "/x"}},
        headers=_h(tok),
    )
    assert r.status_code == 201
    items = (
        await env.client.get(
            "/agent/v1/admin/bug-reports", headers={"Authorization": f"Bearer {env.admin_key}"}
        )
    ).json()["items"]
    assert (
        items[0]["description"] == "Il grafico è vuoto"
        and items[0]["sessionId"] == tok["conversationId"]
    )


async def test_agents_can_use_versioned_prompts(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    assert (
        await env.client.put(
            "/agent/v1/admin/prompts/base", json={"text": "Prima versione."}, headers=adm
        )
    ).json()["version"] == 1
    assert (
        await env.client.put(
            "/agent/v1/admin/prompts/base", json={"text": "Seconda versione."}, headers=adm
        )
    ).json()["version"] == 2
    agent = (await env.client.get("/agent/v1/admin/agents", headers=adm)).json()["items"][0]
    agent.pop("version")
    agent.update(instructions="", prompt={"name": "base", "version": 1})
    assert (
        await env.client.put("/agent/v1/admin/agents/helper", json=agent, headers=adm)
    ).status_code == 200
    env.script.add("function:primary", ["ok"])
    tok = await env.token()
    await env.chat(tok["token"], text="ciao")
    assert system_text(env.script.seen[-1][1]).startswith("Prima versione.")


async def test_voice_catalog(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    await env.client.put(
        "/agent/v1/admin/voices/polly/Bianca",
        json={"language": "it-IT", "name": "Bianca", "region": "eu-north-1"},
        headers=adm,
    )
    items = (
        await env.client.get(
            "/agent/v1/voices?language=it-IT", headers={"Authorization": f"Bearer {env.server_key}"}
        )
    ).json()["items"]
    assert [v["voiceId"] for v in items] == ["Bianca"]


def test_web_sources_are_shown_but_never_spoken() -> None:
    s = SentenceSplitter()
    md = "\n".join([
        "The price changed in October. See https://example.test/x for more.",
        "",
        "Sources:",
        "- [Regulator](https://regulator.example/x)",
        "- [Exchange](https://exchange.example)",
        "",
    ])
    out = [*s.push(md), *s.flush()]
    assert out == ["The price changed in October.", "See for more."]
