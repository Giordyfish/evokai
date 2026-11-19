"""Conversations disappear a set number of days after their last message (Evokai storage)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import sqlalchemy as sa

from evokai.core.util import utcnow
from evokai.db import models as m
from tests.conftest import ORIGIN, Env, Tool

ADMIN = "/agent/v1/admin/application"


async def _ttl(env: Env, days: int | None) -> None:
    r = await env.client.patch(
        ADMIN,
        json={"conversation_ttl_days": days},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


async def _age(env: Env, conversation_id: str, days: int) -> None:
    """Moves the conversation's last activity back.

    The turn may still be writing its last event (which touches updated_at) right after the stream
    ends, so the change is repeated until it sticks.
    """
    old = utcnow() - timedelta(days=days)
    for _ in range(40):
        async with env.svc.db.session() as s:
            q = sa.update(m.Session).where(m.Session.id == conversation_id)
            await s.execute(q.values(updated_at=old))
        await asyncio.sleep(0.05)
        async with env.svc.db.session() as s:
            q = sa.select(m.Session.updated_at).where(m.Session.id == conversation_id)
            current = (await s.execute(q)).scalar_one()
        if current.replace(tzinfo=None) <= old.replace(tzinfo=None) + timedelta(seconds=1):
            return
    raise AssertionError("the conversation kept being updated")


async def _count(env: Env, model: type, conversation_id: str) -> int:
    column = getattr(model, "id" if model is m.Session else "session_id")
    async with env.svc.db.session() as s:
        q = sa.select(sa.func.count()).select_from(model).where(column == conversation_id)
        return (await s.execute(q)).scalar_one()


async def _conversation(env: Env) -> tuple[str, str]:
    env.script.add("function:primary", [Tool("orders_month", {"period": "2026-10"})], ["Ok."])
    tok = await env.token()
    await env.chat(tok["token"], text="October?")
    return tok["conversationId"], tok["token"]


def _server(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.server_key}"}


async def test_the_setting_is_per_application_and_shown(env: Env) -> None:
    await _ttl(env, 7)
    r = await env.client.get(ADMIN, headers={"Authorization": f"Bearer {env.admin_key}"})
    assert r.json()["conversation_ttl_days"] == 7


async def test_an_expired_conversation_is_gone_on_reads_before_the_purge(env: Env) -> None:
    await _ttl(env, 7)
    cid, token = await _conversation(env)
    listed = await env.client.get("/agent/v1/conversations?subject=u1", headers=_server(env))
    assert [x["id"] for x in listed.json()["items"]] == [cid]

    await _age(env, cid, 8)
    listed = await env.client.get("/agent/v1/conversations?subject=u1", headers=_server(env))
    assert listed.json()["items"] == []
    one = await env.client.get(f"/agent/v1/conversations/{cid}?subject=u1", headers=_server(env))
    assert one.status_code == 404
    again = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper", "conversationId": cid},
        headers=_server(env),
    )
    assert again.status_code == 404
    chat = await env.client.post(
        "/agent/v1/session/chat",
        json={"text": "hello"},
        headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
    )
    assert (chat.status_code, chat.json()["error"]) == (404, "expired_conversation")


async def test_the_purge_deletes_content_and_keeps_usage_unlinked(env: Env) -> None:
    await _ttl(env, 7)
    cid, _ = await _conversation(env)
    young, _ = await _conversation(env)
    await _age(env, cid, 8)
    assert await env.svc.store.sessions.purge_expired() == 1

    for model in (m.Session, m.Message, m.Event, m.Turn, m.Thread, m.PendingAction):
        assert await _count(env, model, cid) == 0, model.__name__
    assert await _count(env, m.Message, young) > 0
    async with env.svc.db.session() as s:
        usage = (await s.execute(sa.select(m.UsageRecord))).scalars().all()
    # Two model calls per conversation (the tool step and the answer): all four stay.
    assert sorted(str(u.session_id) for u in usage) == sorted(["None", "None", young, young])
    assert all(u.turn_id is None for u in usage if u.session_id is None)
    assert all(u.cost_micro_usd >= 0 for u in usage)


async def test_without_a_time_to_live_nothing_expires(env: Env) -> None:
    cid, _ = await _conversation(env)
    await _age(env, cid, 400)
    assert await env.svc.store.sessions.purge_expired() == 0
    listed = await env.client.get("/agent/v1/conversations?subject=u1", headers=_server(env))
    assert [x["id"] for x in listed.json()["items"]] == [cid]


async def test_the_time_to_live_follows_each_conversations_own_storage(env: Env) -> None:
    await _ttl(env, 7)
    await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "storage", "name": "external", "value": True}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    stored, _ = await _conversation(env)
    kept_by_app = (await env.token("u2", storageMode="external"))["conversationId"]
    await _age(env, stored, 8)
    await _age(env, kept_by_app, 8)
    assert await env.svc.store.sessions.purge_expired() == 1
    assert await _count(env, m.Session, stored) == 0
    assert await _count(env, m.Session, kept_by_app) == 1

    # In an application that keeps conversations itself, one stored in Evokai still expires.
    await env.client.patch(
        ADMIN,
        json={"storage_mode": "external"},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "storage", "name": "evokai", "value": True}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    here = (await env.token("u3", storageMode="evokai"))["conversationId"]
    await _age(env, here, 8)
    assert await env.svc.store.sessions.purge_expired() == 1
    assert await _count(env, m.Session, here) == 0
