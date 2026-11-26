"""Prompt modules: what is stored (parameter schemas per prompt version, options per session)."""

from __future__ import annotations

from typing import Any

from evokai.db.repositories import Who
from tests.conftest import Env

S1: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "maxLength": 40}},
}
S2: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "maxLength": 60}},
    "required": ["name"],
}


async def test_prompt_put_keeps_parameters_per_version(env: Env) -> None:
    prompts = env.svc.store.prompts
    assert await prompts.put(env.app_id, "greet", "Hi {{name}}", parameters=S1) == 1
    assert await prompts.put(env.app_id, "greet", "Hi {{name}}", parameters=S1) == 1
    assert await prompts.put(env.app_id, "greet", "Hi {{name}}", parameters=S2) == 2
    v3 = await prompts.put(env.app_id, "greet", "Hi {{name}}", parameters=S2, description="Hello")
    assert v3 == 3
    data = {"a": "First entry.", "b": "Second entry."}
    assert await prompts.put(env.app_id, "greet", "Hi {{name}}", parameters=S2, data=data) == 4
    first = await prompts.get(env.app_id, "greet", 1)
    assert first is not None and first.parameters == S1 and first.description == ""
    third = await prompts.get(env.app_id, "greet", 3)
    assert third is not None and third.parameters == S2 and third.description == "Hello"
    latest = await prompts.get(env.app_id, "greet")
    assert latest is not None and latest.version == 4 and latest.data == data
    assert [r.version for r in await prompts.all(env.app_id) if r.name == "greet"] == [1, 2, 3, 4]
    plain = await prompts.get(env.app_id, "greet", 1)
    assert plain is not None and plain.data is None


async def test_session_prompt_options_roundtrip(env: Env) -> None:
    w = Who(env.app_id, "u1")
    options = {"modules": {"tone": {"on": True, "params": {"style": "brief"}}}, "locale": "it"}
    sess, _ = await env.svc.store.sessions.create(
        w, "helper", "v1", "Books", [], prompt_options=options
    )
    got = await env.svc.store.sessions.get(w, sess.id)
    assert got is not None and got.prompt_options == options
    other = {"modules": {"tone": {"on": False, "params": {}}}, "locale": None}
    await env.svc.store.sessions.set_prompt_options(sess.id, other)
    got = await env.svc.store.sessions.get(w, sess.id)
    assert got is not None and got.prompt_options == other
    plain, _ = await env.svc.store.sessions.create(w, "helper", "v1", "Books", [])
    got = await env.svc.store.sessions.get(w, plain.id)
    assert got is not None and got.prompt_options is None


async def test_the_audit_of_a_prompt_save_holds_its_description_and_data(env: Env) -> None:
    body = {
        "text": "{{lookup data shelf}}",
        "parameters": {
            "type": "object",
            "properties": {"shelf": {"type": "string", "enum": ["a", "b"]}},
            "required": ["shelf"],
        },
        "description": "Which shelf the clerk looks after",
        "data": {"a": "You look after crime.", "b": "You look after poetry."},
    }
    r = await env.client.put(
        "/agent/v1/admin/prompts/shelf",
        json=body,
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text
    (entry,) = await env.svc.store.audit.query(env.app_id, action="prompt.put")
    after = entry.after or {}
    assert after["description"] == "Which shelf the clerk looks after"
    assert after["data"] == body["data"]
