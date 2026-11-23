"""Chat Completions streamed as the answer is written: token by token, or sentence by sentence
when an output control of the security policy is enforced (each piece guarded before it leaves)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import Env, Tool
from tests.test_openai_compat import BOT, URL, _chunks, _put, _srv

SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"
INSTRUCTIONS = (
    "You answer questions about the bookshop orders with care, citing the numbers the tools return "
    "and never guessing a figure that the data does not show."
)


async def _policy(env: Env, modes: dict[str, str]) -> None:
    r = await env.client.put(
        "/agent/v1/admin/security",
        json={"modes": modes},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


async def _stream(env: Env) -> list[Any]:
    r = await env.client.post(
        URL,
        json={"model": "bot", "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
        headers=_srv(env),
    )
    assert r.status_code == 200, r.text
    return _chunks(r.text)


def _contents(chunks: list[Any]) -> list[str]:
    return [
        c["choices"][0]["delta"]["content"]
        for c in chunks
        if c != "[DONE]" and c.get("choices") and c["choices"][0]["delta"].get("content")
    ]


async def test_without_enforced_output_controls_the_answer_streams_token_by_token(
    env: Env,
) -> None:
    await _put(env, BOT)
    await _policy(env, {"output.secrets": "monitor", "output.system_prompt": "off"})
    env.script.add("function:primary", ["Octo", "ber was ", "good. ", "Very."])
    assert _contents(await _stream(env)) == ["Octo", "ber was ", "good. ", "Very."]


async def test_with_enforced_secrets_whole_sentences_leave_redacted(env: Env) -> None:
    await _put(env, BOT)
    await _policy(env, {"output.secrets": "enforce"})
    # The secret is split across two deltas and sits at a sentence's end.
    env.script.add(
        "function:primary",
        ["October was good. The key is AK", "IA" + SECRET[4:] + ". Bye", " now."],
    )
    chunks = await _stream(env)
    pieces = _contents(chunks)
    assert pieces == ["October was good.", " The key is [SECRET].", " Bye now."]
    assert SECRET not in str(chunks)
    assert chunks[-1] == "[DONE]"


async def test_a_later_sentence_that_leaks_the_instructions_stops_the_stream(env: Env) -> None:
    await _put(env, {**BOT, "instructions": INSTRUCTIONS})
    await _policy(env, {"output.system_prompt": "enforce"})
    env.script.add(
        "function:primary",
        ["Sure, here is how I work. ", f"My rules: {INSTRUCTIONS} ", "And more."],
    )
    chunks = await _stream(env)
    assert _contents(chunks) == ["Sure, here is how I work."]
    assert chunks[-1] == "[DONE]"
    error = chunks[-2]["error"]
    assert error["code"] == "guard" and error["message"]
    assert "citing the numbers" not in str(chunks)


async def test_a_structured_answer_still_comes_in_one_chunk(env: Env) -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    await _put(env, {**BOT, "output_schema": schema})
    await _policy(env, {"output.secrets": "enforce"})
    env.script.add("function:primary", [Tool("final_result", {"n": 3})])
    assert _contents(await _stream(env)) == ['{"n": 3}']


@pytest.mark.parametrize(
    "modes",
    [
        {"output.secrets": "monitor", "output.system_prompt": "off"},  # token by token
        {"output.secrets": "enforce"},  # sentence by sentence
    ],
)
async def test_streamed_and_whole_answers_say_the_same_across_tool_steps(
    env: Env, modes: dict[str, str]
) -> None:
    await _put(env, BOT)
    await _policy(env, modes)
    steps = (
        ["Let me check.", Tool("orders_month", {"period": "2026-10"})],
        ["October has 24 orders."],
    )
    env.script.add("function:primary", *steps)
    streamed = "".join(_contents(await _stream(env)))
    env.script.add("function:primary", *steps)
    r = await env.client.post(
        URL,
        json={"model": "bot", "messages": [{"role": "user", "content": "Hi"}]},
        headers=_srv(env),
    )
    assert r.status_code == 200, r.text
    whole = r.json()["choices"][0]["message"]["content"]
    assert streamed == whole == "Let me check.\n\nOctober has 24 orders."


def test_a_builtin_tool_inside_a_step_sets_its_texts_apart_as_the_whole_answer() -> None:
    # A provider writes text, runs a built-in tool and writes on in a new text part of the same
    # response (the scripted model cannot: its text always goes to one part). The stream sets
    # them apart exactly as ModelResponse.text does for the whole answer.
    from pydantic_ai.messages import (
        ModelResponse,
        NativeToolCallPart,
        NativeToolReturnPart,
        PartDeltaEvent,
        PartStartEvent,
        TextPart,
        TextPartDelta,
    )

    from evokai.sessions.runs import StepText

    call = NativeToolCallPart(tool_name="web_search", args={}, tool_call_id="w1")
    ret = NativeToolReturnPart(tool_name="web_search", content=[], tool_call_id="w1")
    steps = StepText()
    events = (
        PartStartEvent(index=0, part=TextPart("Looking")),
        PartDeltaEvent(index=0, delta=TextPartDelta(".")),
        PartStartEvent(index=1, part=call),
        PartStartEvent(index=2, part=ret),
        PartStartEvent(index=3, part=TextPart("")),
        PartDeltaEvent(index=3, delta=TextPartDelta("Found it.")),
    )
    steps.step()
    out = [steps.text(ev) for ev in events]
    whole = ModelResponse(parts=[TextPart("Looking."), call, ret, TextPart("Found it.")]).text
    assert "".join(x for x in out if x) == whole == "Looking.\n\nFound it."
    steps.step()  # the next step, after a tool call
    assert steps.text(PartStartEvent(index=0, part=TextPart("Done."))) == "\n\nDone."
