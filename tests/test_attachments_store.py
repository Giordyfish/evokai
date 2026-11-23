"""Attachments: the grant, the agent's settings and where the files are kept."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from evokai.agents.definitions import AgentDef, AttachmentsDef
from evokai.attachments import Prepared
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.grants import Grants
from tests.conftest import Env

GRANTS = "/agent/v1/admin/grants"
READER: dict[str, Any] = {
    "name": "reader",
    "title": "Reader",
    "model": "function:primary",
    "instructions": "Read what people send.",
    "attachments": {"enabled": True, "types": ["png", "pdf"], "max_file_mb": 2},
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def grant(env: Env, value: bool = True) -> None:
    r = await env.client.patch(
        GRANTS,
        json={"changes": [{"kind": "attachments", "name": "*", "value": value}]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text


def test_the_settings_and_their_defaults() -> None:
    d = AttachmentsDef()
    assert (d.enabled, d.max_file_mb, d.max_files, d.max_conversation_mb) == (False, 10, 5, 50)
    assert "png" in d.types and "docx" in d.types
    agent = AgentDef(name="a", model="function:primary", instructions="x")
    # An agent saved before attachments keeps its version.
    assert "attachments" not in agent.content()


def test_off_until_granted_and_restricted() -> None:
    agent = AgentDef.model_validate({**READER})
    assert Grants().attachments() is False
    assert "attachments are not granted" in Grants().problems(agent)
    off = Grants().restrict(agent).attachments
    assert off is not None and off.enabled is False
    on = Grants(overrides={("attachments", "*"): True})
    kept = on.restrict(agent).attachments
    assert on.attachments() and kept is not None and kept.enabled
    assert "attachments are not granted" not in on.problems(agent)


async def test_an_agent_needs_the_grant_and_the_view_lists_it(env: Env) -> None:
    r = await env.client.put("/agent/v1/admin/agents/reader", json=READER, headers=_adm(env))
    assert (r.status_code, r.json()["error"]) == (400, "not_granted"), r.text
    view = (await env.client.get(GRANTS, headers=_adm(env))).json()
    (item,) = next(g["items"] for g in view["groups"] if g["id"] == "attachments")
    assert (item["name"], item["value"]) == ("*", False)
    await grant(env)
    r = await env.client.put("/agent/v1/admin/agents/reader", json=READER, headers=_adm(env))
    assert r.status_code == 200, r.text
    view = (await env.client.get(GRANTS, headers=_adm(env))).json()
    (item,) = next(g["items"] for g in view["groups"] if g["id"] == "attachments")
    assert item["value"] is True and item["usedBy"] == ["reader"]
    bad = {**READER, "attachments": {"enabled": True, "types": ["svg"]}}
    r = await env.client.put("/agent/v1/admin/agents/reader", json=bad, headers=_adm(env))
    assert r.status_code == 400


async def _conversation(env: Env, subject: str = "u1") -> tuple[str, Who]:
    tok = await env.token(subject)
    await env.chat(tok["token"], text="Hello")
    return tok["conversationId"], Who(env.app_id, subject)


async def test_the_repository_is_scoped_to_its_owner_and_conversation(env: Env) -> None:
    cid, who = await _conversation(env)
    other, _ = await _conversation(env)
    p = Prepared(name="a.png", kind="png", data=b"\x89PNG-bytes")
    await env.svc.store.attachments.add(who, cid, None, [p])
    store = env.svc.store.attachments
    assert (await store.get(who, cid, p.id)) is not None
    assert await store.get(who, other, p.id) is None
    assert await store.get(Who(env.app_id, "u2"), cid, p.id) is None
    assert await store.data_of(who, cid, [p.id, "nope"]) == {p.id: b"\x89PNG-bytes"}
    assert await store.total(who, cid) == len(b"\x89PNG-bytes")
    assert await store.total(who, other) == 0


async def _rows(env: Env) -> int:
    async with env.svc.db.session() as s:
        return (await s.execute(sa.select(sa.func.count()).select_from(m.Attachment))).scalar_one()


async def test_deletion_erase_and_export_follow_the_conversation(env: Env) -> None:
    cid, who = await _conversation(env)
    p = Prepared(name="a.pdf", kind="pdf", data=b"%PDF-1.4 tiny")
    await env.svc.store.attachments.add(who, cid, "m1", [p])
    srv = {"Authorization": f"Bearer {env.server_key}"}
    r = await env.client.post("/agent/v1/privacy/export", json={"subject": "u1"}, headers=srv)
    (conv,) = [c for c in r.json()["conversations"] if c["id"] == cid]
    (item,) = conv["attachments"]
    assert item["name"] == "a.pdf" and item["sha256"] == p.sha256 and item["size"] == p.size
    assert item["dataBase64"]
    assert await env.svc.store.sessions.delete(who, cid)
    assert await _rows(env) == 0

    cid, who = await _conversation(env)
    await env.svc.store.attachments.add(who, cid, None, [Prepared("b.png", "png", b"x")])
    r = await env.client.post("/agent/v1/privacy/erase", json={"subject": "u1"}, headers=srv)
    assert r.status_code == 200, r.text
    assert await _rows(env) == 0
