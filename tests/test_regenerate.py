"""Regenerate the last answer: the last question runs again and its new answer replaces the old one,
never after a turn that changed data or acted through a client tool."""

from __future__ import annotations

from typing import Any

from evokai.sessions.rewrite import regenerable_index
from tests.conftest import ORIGIN, Env, Tool, parse_sse, types

URL = "/agent/v1/session/regenerate"


def _h(tok: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e.get("delta", "") for e in events if e["type"] == "text")


def test_the_rules_of_a_regeneration() -> None:
    q = {"role": "user", "text": "Ciao", "content": [{"text": "Ciao"}]}
    a = {"role": "assistant", "text": "Ciao!", "content": [{"text": "Ciao!"}]}
    client = {
        "role": "user",
        "content": [{"toolResult": {"id": "t", "name": "client_kiosk_light", "text": "ok"}}],
    }
    assert regenerable_index([q, a]) == 0
    assert regenerable_index([q, a, client, a]) is None
    assert regenerable_index([a]) is None


async def test_the_last_answer_is_replaced(env: Env) -> None:
    env.script.add("function:primary", ["First."], ["Second, better."])
    tok = await env.token()
    first = await env.chat(tok["token"], text="Hello")
    assert first[-1]["regenerable"] is True
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    assert [m.get("regenerable") for m in conv["messages"]] == [None, True]
    r = await env.client.post(URL, json={}, headers=_h(tok))
    assert r.status_code == 200, r.text
    ev = parse_sse(r.text)
    assert types(ev)[:2] == ["conversation", "rewritten"]
    assert ev[1]["replaced"] == [first[0]["turnId"]]
    assert _text(ev) == "Second, better."
    conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
    assert [(m["role"], m["text"]) for m in conv["messages"]] == [
        ("user", "Hello"),
        ("assistant", "Second, better."),
    ]
    # The model saw the same question again.
    assert "Hello" in str(env.script.seen[-1][1][-1])


async def test_not_after_a_change_nor_a_client_tool(env: Env) -> None:
    env.script.add(
        "function:primary",
        ["Refund.", Tool("order_refund", {"period": "2026-10", "reason": "damaged"})],
        ["Done."],
    )
    tok = await env.token()
    ev = await env.chat(tok["token"], text="Refund")
    action = next(e for e in ev if e["type"] == "confirm")["actionId"]
    done = await env.chat(tok["token"], confirm={"actionId": action, "approve": True})
    assert done[-1]["regenerable"] is False
    r = await env.client.post(URL, json={}, headers=_h(tok))
    assert (r.status_code, r.json()["error"]) == (409, "not_regenerable"), r.text


async def test_nothing_to_regenerate_in_a_new_conversation(env: Env) -> None:
    tok = await env.token()
    r = await env.client.post(URL, json={}, headers=_h(tok))
    assert (r.status_code, r.json()["error"]) == (409, "not_regenerable")


async def test_external_storage_regenerates_from_the_sent_history(env: Env) -> None:
    from tests.test_rewrite import _external

    await _external(env)
    env.script.add("function:primary", ["Again."])
    tok = await env.token()
    history = [
        {"role": "user", "text": "Hi", "content": [{"text": "Hi"}]},
        {"role": "assistant", "text": "Hello.", "content": [{"text": "Hello."}]},
    ]
    r = await env.client.post(URL, json={"history": history}, headers=_h(tok))
    assert r.status_code == 200, r.text
    ev = parse_sse(r.text)
    done = ev[-1]
    assert done["replacedFrom"] == 0 and _text(ev) == "Again."
    assert [m["role"] for m in done["messages"]] == ["user", "assistant"]


async def test_external_storage_cannot_regenerate_a_question_whose_files_are_gone(
    env: Env,
) -> None:
    # Nothing is kept with external storage: the bytes of an image or a PDF the model read as a
    # file live only during its turn. A document read as text is in the history and stays fine.
    from tests.test_attachments_files import jpeg
    from tests.test_attachments_store import grant
    from tests.test_attachments_turns import READER, files

    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "storage", "name": "external", "value": True}]},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text
    await grant(env)
    r = await env.client.put(
        "/agent/v1/admin/agents/reader",
        json=READER,
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text
    tok = await env.token(agent="reader", storageMode="external")
    chat = "/agent/v1/session/chat"
    for attached, regenerable in ((("me.jpg", jpeg()),), False), ((("n.txt", b"A note"),), True):
        env.script.add("function:primary", ["Seen."])
        r = await env.client.post(
            chat,
            json={"text": "Look", "attachments": files(*attached), "history": []},
            headers={**_h(tok), "Accept": "application/json"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["regenerable"] is regenerable
        env.script.add("function:primary", ["Again."])
        history = r.json()["messages"]
        r = await env.client.post(URL, json={"history": history}, headers=_h(tok))
        assert (r.status_code == 409) is not regenerable, r.text


async def test_a_question_stored_masked_is_not_regenerated(env: Env) -> None:
    # pii.history keeps the question with its identifiers masked: running it again would ask a
    # different question, so the answer is not regenerable. A question without any keeps it.
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"modes": {"pii.history": "enforce"}},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text
    for text, regenerable in (("Write to mario.rossi@example.com", False), ("Hello", True)):
        env.script.add("function:primary", ["Sure."])
        tok = await env.token()
        ev = await env.chat(tok["token"], text=text)
        assert ev[-1]["regenerable"] is regenerable
        conv = (await env.client.get("/agent/v1/session/conversation", headers=_h(tok))).json()
        assert conv["messages"][-1].get("regenerable") is (True if regenerable else None)
        env.script.add("function:primary", ["Again."])
        r = await env.client.post(URL, json={}, headers=_h(tok))
        assert (r.status_code == 409) is not regenerable, r.text
