"""Confirmations over the OpenAI-compatible API (opt-in): a change waits for a yes in the next
request, and the stored arguments run, never regenerated."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import ORIGIN, Env, Tool
from tests.test_openai_compat import URL

CLERK = {
    "name": "clerk",
    "title": "Clerk",
    "model": "function:primary",
    "mcp_servers": ["app"],
    "channels": ["browser", "server"],
    "instructions": "Help with orders.",
    "openai_confirm": True,
    "confirm_labels": {"order_refund": ["Refund the orders of {{period}}"]},
}
REFUND = Tool("order_refund", {"period": "2026-10"}, "r1")
PROMPT = "Before I go ahead, please confirm: Refund the orders of 2026-10. Reply yes or no."


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _setup(env: Env, *, grant: bool = True, agent: dict[str, Any] | None = None) -> None:
    if grant:
        r = await env.client.patch(
            "/agent/v1/admin/grants",
            json={"changes": [{"kind": "openai", "name": "confirm", "value": True}]},
            headers=_adm(env),
        )
        assert r.status_code == 200, r.text
    r = await env.client.put("/agent/v1/admin/agents/clerk", json=agent or CLERK, headers=_adm(env))
    assert r.status_code == 200, r.text


async def _caller(env: Env, subject: str = "u1") -> dict[str, str]:
    tok = await env.token(subject, agent="clerk")
    return {"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}


async def _ask(env: Env, h: dict[str, str], messages: list[dict[str, str]]) -> dict[str, Any]:
    r = await env.client.post(URL, json={"model": "clerk", "messages": messages}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _content(body: dict[str, Any]) -> str:
    return body["choices"][0]["message"]["content"]


FIRST = [{"role": "user", "content": "Refund October, please"}]


async def test_off_by_default_changes_are_not_offered(env: Env) -> None:
    await _setup(env, grant=False, agent={**CLERK, "openai_confirm": False})
    env.script.add("function:primary", ["No."])
    await _ask(env, await _caller(env), FIRST)
    assert "order_refund" not in env.script.tools_seen


async def test_a_change_waits_for_a_yes_and_runs_the_stored_arguments(env: Env) -> None:
    await _setup(env)
    h = await _caller(env)
    env.script.add("function:primary", [REFUND])
    body = await _ask(env, h, FIRST)
    assert _content(body) == PROMPT
    confirm = body["evokai"]["confirm"]
    assert confirm["items"] == ["Refund the orders of 2026-10"] and confirm["actionId"]
    assert env.mcp.calls == []
    calls_before = len(env.script.seen)

    def answer(_messages: list[Any]) -> list[Any]:
        return ["Done: refunded."]

    env.script.add("function:primary", answer)
    yes = [*FIRST, {"role": "assistant", "content": PROMPT}, {"role": "user", "content": "Yes"}]
    body = await _ask(env, h, yes)
    assert env.mcp.calls == [("order_refund", {"period": "2026-10"})]
    assert _content(body) == "Done: refunded."
    assert len(env.script.seen) == calls_before + 1  # the call was not regenerated
    trace = (
        await env.client.get(f"/agent/v1/admin/traces/{body['evokai']['runId']}", headers=_adm(env))
    ).json()
    (span,) = [s for s in trace["spans"] if s["name"] == "execute_tool order_refund"]
    assert span["attrs"]["confirmed"] is True and span["attrs"]["confirmChannel"] == "openai"
    # One use: the same yes again runs nothing more.
    env.script.add("function:primary", ["Already done."])
    await _ask(env, h, yes)
    assert len(env.mcp.calls) == 1


@pytest.mark.parametrize("reply", ["no", "No, wait."])
async def test_a_no_cancels_without_calling_the_model(env: Env, reply: str) -> None:
    await _setup(env)
    h = await _caller(env)
    env.script.add("function:primary", [REFUND])
    await _ask(env, h, FIRST)
    before = len(env.script.seen)
    msgs = [*FIRST, {"role": "assistant", "content": PROMPT}, {"role": "user", "content": reply}]
    body = await _ask(env, h, msgs)
    assert _content(body) == "OK, I have not changed anything."
    assert len(env.script.seen) == before and env.mcp.calls == []


async def test_anything_else_cancels_and_is_answered_as_a_new_message(env: Env) -> None:
    await _setup(env)
    h = await _caller(env)
    env.script.add("function:primary", [REFUND], ["Which month then?"])
    await _ask(env, h, FIRST)
    msgs = [
        *FIRST,
        {"role": "assistant", "content": PROMPT},
        {"role": "user", "content": "Actually, what about November?"},
    ]
    body = await _ask(env, h, msgs)
    assert _content(body) == "Which month then?" and env.mcp.calls == []
    # The cancelled action cannot be confirmed afterwards.
    yes = [*FIRST, {"role": "assistant", "content": PROMPT}, {"role": "user", "content": "yes"}]
    env.script.add("function:primary", ["Hm."])
    await _ask(env, h, yes)
    assert env.mcp.calls == []


async def test_an_edited_question_or_another_subject_runs_nothing(env: Env) -> None:
    await _setup(env)
    h = await _caller(env)
    env.script.add("function:primary", [REFUND], ["?"], ["?"])
    await _ask(env, h, FIRST)
    edited = [
        *FIRST,
        {"role": "assistant", "content": PROMPT.replace("2026-10", "2026-09")},
        {"role": "user", "content": "yes"},
    ]
    await _ask(env, h, edited)
    other = await _caller(env, "u2")
    yes = [*FIRST, {"role": "assistant", "content": PROMPT}, {"role": "user", "content": "yes"}]
    await _ask(env, other, yes)
    assert env.mcp.calls == []


async def test_the_agent_switch_needs_the_grant(env: Env) -> None:
    r = await env.client.put("/agent/v1/admin/agents/clerk", json=CLERK, headers=_adm(env))
    assert (r.status_code, r.json()["error"]) == (400, "not_granted"), r.text
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": "openai", "name": "nope", "value": True}]},
        headers=_adm(env),
    )
    assert r.status_code == 400


async def test_a_streamed_answer_asks_in_its_last_content_chunk(env: Env) -> None:
    from tests.test_openai_compat import _chunks

    await _setup(env)
    h = await _caller(env)
    env.script.add("function:primary", ["Let me do it.", REFUND])
    r = await env.client.post(
        URL, json={"model": "clerk", "messages": FIRST, "stream": True}, headers=h
    )
    chunks = [c for c in _chunks(r.text) if c != "[DONE]"]
    content = "".join(
        c["choices"][0]["delta"].get("content", "") for c in chunks if c.get("choices")
    )
    assert content == f"Let me do it.\n\n{PROMPT}"
    (last,) = [c for c in chunks if c.get("choices") and c["choices"][0]["finish_reason"]]
    assert last["evokai"]["confirm"]["items"] == ["Refund the orders of 2026-10"]
    env.script.add("function:primary", ["Refunded."])
    yes = [*FIRST, {"role": "assistant", "content": content}, {"role": "user", "content": "yes"}]
    await _ask(env, h, yes)
    assert env.mcp.calls == [("order_refund", {"period": "2026-10"})]


async def test_a_streamed_text_ending_in_a_space_can_still_be_confirmed(env: Env) -> None:
    from tests.test_openai_compat import _chunks

    await _setup(env)
    h = await _caller(env)
    env.script.add("function:primary", ["I'll refund it. ", REFUND])
    r = await env.client.post(
        URL, json={"model": "clerk", "messages": FIRST, "stream": True}, headers=h
    )
    content = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in _chunks(r.text)
        if c != "[DONE]" and c.get("choices")
    )
    env.script.add("function:primary", ["Refunded."])
    yes = [*FIRST, {"role": "assistant", "content": content}, {"role": "user", "content": "yes"}]
    await _ask(env, h, yes)
    assert env.mcp.calls == [("order_refund", {"period": "2026-10"})]


async def test_two_conversations_of_one_subject_keep_their_own_actions(env: Env) -> None:
    # Without labels both questions read "Run order_refund": the conversation tells them apart.
    await _setup(env, agent={**CLERK, "confirm_labels": {}})
    h = await _caller(env)
    a = [{"role": "user", "content": "Refund October"}]
    b = [{"role": "user", "content": "Refund September"}]
    env.script.add(
        "function:primary",
        [Tool("order_refund", {"period": "2026-10"}, "ra")],
        [Tool("order_refund", {"period": "2026-09"}, "rb")],
        ["Done."],
    )
    asked_a = _content(await _ask(env, h, a))
    asked_b = _content(await _ask(env, h, b))
    assert asked_a == asked_b
    await _ask(
        env,
        h,
        [*a, {"role": "assistant", "content": asked_a}, {"role": "user", "content": "yes"}],
    )
    assert env.mcp.calls == [("order_refund", {"period": "2026-10"})]


def test_a_server_key_without_user_never_confirms() -> None:
    from evokai.agents.definitions import AgentDef
    from evokai.api.openai_confirm import allowed
    from evokai.grants import Grants

    agent = AgentDef.model_validate({**CLERK, "version": ""})
    granted = Grants(overrides={("openai", "confirm"): True})
    assert allowed(granted, agent, named=True)
    # Every call without a user is the same subject (system:<agent>): nobody in particular.
    assert not allowed(granted, agent, named=False)
    assert not allowed(Grants(), agent, named=True)
