"""The gateway proxy's OpenAI (Codex, ChatGPT) and GitHub Copilot adapters.

Codex's fixtures are sanitised shapes of measured traffic (Codex CLI 0.167.2); ChatGPT's and
Copilot's are built from the public formats and are unmeasured (``tests/proxy_fixtures.py``). The
end to end tests run the real engine against the local fake upstream (``tests/proxy_harness.py``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import websockets

from evokai.gateway.proxy import catalogue, identity
from evokai.gateway.proxy.adapters import Answer, FormatUnknown, Rewrite, providers_with_adapters
from evokai.gateway.proxy.adapters import copilot as a_copilot
from evokai.gateway.proxy.adapters import openai as a_openai
from evokai.gateway.proxy.texts import Texts
from evokai.gateway.relay import Usage
from tests import proxy_fixtures as fx
from tests.conftest import Env
from tests.proxy_harness import client_ssl
from tests.test_gateway_proxy import GW, OP, Gate, _gate, req, stack

CODEX = "/backend-api/codex/responses"


def bearer(token: str) -> str:
    return f"Bearer {token}"


def codex_upgrade(token: str | None = None) -> Any:
    return req(
        "GET",
        "chatgpt.com",
        CODEX,
        authorization=bearer(token or fx.chatgpt_jwt()),
        user_agent=fx.CODEX_UA,
    )


def frame(data: dict) -> bytes:
    return json.dumps(data).encode()


def sse_events(body: bytes) -> list[Any]:
    out = []
    for line in body.decode().splitlines():
        if line.startswith("data: "):
            data = line[6:]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


# Identity


def test_a_chatgpt_login_names_the_user_the_email_and_the_account() -> None:
    found = identity.openai_identity(bearer(fx.chatgpt_jwt(email="Ada@Example.com")))
    assert found is not None
    key, claims = found
    assert key == identity.Key("openai", "user", fx.CHATGPT_USER, fx.CHATGPT_ACCOUNT)
    assert claims == identity.Claims("ada@example.com", fx.CHATGPT_USER, fx.CHATGPT_ACCOUNT)
    # The token is never the key's value: only the user it names (stored as a digest).
    assert fx.chatgpt_jwt() not in key.value
    # An API key names nobody; no token, no key.
    api = identity.openai_identity("Bearer sk-fake")
    assert api is not None and api[0].kind == "api_key" and api[1] is None
    assert identity.openai_identity("") is None


def test_a_copilot_token_is_keyed_on_its_tid() -> None:
    assert identity.copilot_tid(fx.copilot_token()) == fx.COPILOT_TID
    assert identity.copilot_key(bearer(fx.copilot_token())) == identity.Key(
        "github", "tid", fx.COPILOT_TID
    )
    assert identity.copilot_key("Bearer opaque") == identity.Key("github", "token", "opaque")
    assert identity.github_key(f"token {fx.GITHUB_TOKEN}") == identity.Key(
        "github", "token", fx.GITHUB_TOKEN
    )
    assert identity.from_github_user(fx.github_user()) == (None, fx.GITHUB_LOGIN)
    assert identity.from_copilot_user(fx.copilot_user(orgs=("a", "b"))) == (
        fx.EMAIL,
        fx.GITHUB_LOGIN,
        "a,b",
        fx.COPILOT_TID,
    )
    assert identity.from_copilot_token(fx.copilot_token_answer()) == fx.COPILOT_TID


# Codex over WebSocket


def test_codex_reads_the_new_items_and_skips_the_warm_up() -> None:
    a = a_openai.CodexSocket()
    assert a.matches("chatgpt.com", CODEX) and a.matches("api.openai.com", "/v1/responses")
    assert not a.matches("chatgpt.com", "/backend-api/other")
    assert a.inspect(frame(fx.codex_warmup())) is None
    assert a.inspect(b'{"type":"session.update"}') is None
    found = a.inspect(frame(fx.codex_create("newest")))
    assert found is not None and found.surface == "codex" and found.usage and found.record
    assert found.model == "gpt-fake-codex"
    assert "newest" in found.text and "developer instructions" not in found.text
    assert found.parts == ["<environment_context>fake</environment_context>", "newest"]
    tool = a.inspect(frame(fx.codex_create(tool_output="file text")))
    assert tool is not None and tool.text == "file text"
    with pytest.raises(FormatUnknown):
        a.inspect(b'{"type": "response.create", "model": "m"}')
    with pytest.raises(FormatUnknown):
        a.inspect(b"response.create but not json")


def test_codex_rewrites_only_the_offending_items() -> None:
    a = a_openai.CodexSocket()
    found = a.inspect(frame(fx.codex_create(f"key {fx.SECRET}")))
    assert found is not None
    found.offending = {1}
    new = json.loads(a.refuse(b"", found, Texts(None, locale="it"), "BLOCCATO"))
    texts = [item["content"][0]["text"] for item in new["input"]]
    assert texts[1] == "<environment_context>fake</environment_context>"
    assert texts[2].startswith("Rispondi ripetendo esattamente") and "BLOCCATO" in texts[2]
    assert fx.SECRET not in json.dumps(new)
    assert new["previous_response_id"] == "resp_fake_warmup"  # the conversation goes on
    tool = a.inspect(frame(fx.codex_create(tool_output=f"key {fx.SECRET}")))
    assert tool is not None
    out = json.loads(a.refuse(b"", tool, Texts(), "NO"))
    assert out["input"][0]["output"].startswith("[Output removed by the company policy")
    assert '"NO"' in out["input"][0]["output"] and fx.SECRET not in json.dumps(out)


def test_codex_refusals_before_the_checks_are_a_synthetic_answer() -> None:
    a = a_openai.CodexSocket()
    found = a.inspect(frame(fx.codex_create("x")))
    verdict = a.failed(b"", found, Texts(), "not recognised")
    assert verdict.drop and verdict.replace is None
    events = [json.loads(m) for m in verdict.reply]
    assert next(e["type"] for e in events) == "response.created"
    assert events[-1]["type"] == "response.completed"
    assert [e["sequence_number"] for e in events] == list(range(len(events)))
    delta = next(e for e in events if e["type"] == "response.output_text.delta")
    assert delta["delta"] == "not recognised"
    assert events[-1]["response"]["output"][0]["content"][0]["text"] == "not recognised"


def test_codex_server_messages_end_an_answer() -> None:
    a = a_openai.CodexSocket()
    ends = [a.ends(frame(e)) for e in fx.responses_events()]
    assert ends == [None] * 11 + ["forwarded"]
    assert a.ends(frame({"type": "response.failed", "response": {}})) == "upstream_error"
    assert (
        a.ends(frame({"type": "response.output_text.delta", "delta": "response.completed"})) is None
    )
    assert a.ends(b"not json response.completed") is None


# Responses over HTTP


def test_responses_http_refuses_with_a_synthetic_answer_in_both_shapes() -> None:
    a = a_openai.Responses()
    assert a.matches("POST", "chatgpt.com", CODEX)
    assert a.matches("POST", "api.openai.com", "/v1/responses")
    assert not a.matches("GET", "chatgpt.com", CODEX)
    r = req("POST", "chatgpt.com", CODEX, fx.responses_body("hello"))
    found = a.inspect(r)
    assert found is not None and found.text.endswith("hello") and found.usage
    sse = a.refuse(r, found, Texts(), "no")
    assert isinstance(sse, Answer) and sse.headers["content-type"].startswith("text/event-stream")
    events = sse_events(sse.body)
    assert events[-1]["type"] == "response.completed"
    assert next(e for e in events if e["type"] == "response.output_text.delta")["delta"] == "no"
    plain = req(
        "POST", "api.openai.com", "/v1/responses", {**fx.responses_body("x"), "stream": False}
    )
    found = a.inspect(plain)
    assert found is not None
    full = json.loads(a.refuse(plain, found, Texts(), "no").body)
    assert full["status"] == "completed" and full["output"][0]["content"][0]["text"] == "no"
    with pytest.raises(FormatUnknown):
        a.inspect(req("POST", "chatgpt.com", CODEX, {"model": "m", "prompt": "old"}))


# ChatGPT on the web (best effort)


def test_chatgpt_rewrites_the_newest_message_and_drops_its_attachments() -> None:
    a = a_openai.ChatGPT()
    path = "/backend-api/conversation"
    assert a.matches("POST", "chatgpt.com", path)
    assert a.matches("POST", "chatgpt.com", "/backend-api/f/conversation")
    r = req("POST", "chatgpt.com", path, fx.chatgpt_conversation(f"key {fx.SECRET}"))
    found = a.inspect(r)
    assert found is not None and fx.SECRET in found.text and found.surface == "chatgpt"
    assert found.model == "auto"
    out = a.refuse(r, found, Texts(), "NO")
    assert isinstance(out, Rewrite)
    new = json.loads(out.body)
    msg = new["messages"][0]
    assert msg["content"]["parts"][0].startswith("Reply by repeating exactly")
    assert "attachments" not in msg["metadata"] and fx.SECRET not in out.body.decode()
    assert new["conversation_id"] == fx.chatgpt_conversation("x")["conversation_id"]
    # A regenerate sends no new message; a changed shape is refused.
    assert a.inspect(req("POST", "chatgpt.com", path, {"action": "variant"})) is None
    with pytest.raises(FormatUnknown):
        a.inspect(req("POST", "chatgpt.com", path, {"action": "next", "messages": "x"}))
    bad = fx.chatgpt_conversation("x")
    bad["messages"][0]["content"] = {"text": "renamed"}
    with pytest.raises(FormatUnknown):
        a.inspect(req("POST", "chatgpt.com", path, bad))


# GitHub Copilot (best effort)


def test_copilot_chat_reads_user_and_tool_messages_and_answers_in_chunks() -> None:
    a = a_copilot.Chat()
    host = "api.business.githubcopilot.com"
    assert a.matches("POST", host, "/chat/completions")
    assert not a.matches("POST", "api.github.com", "/chat/completions")
    r = req("POST", host, "/chat/completions", fx.copilot_chat("newest"))
    found = a.inspect(r)
    assert found is not None and found.text == "tool result\nnewest" and found.surface == "copilot"
    assert a.key(req("POST", host, "/x", authorization=bearer(fx.copilot_token()))) == (
        identity.Key("github", "tid", fx.COPILOT_TID)
    )
    events = sse_events(a.refuse(r, found, Texts(), "no").body)
    assert events[0]["object"] == "chat.completion.chunk"
    assert events[0]["choices"][0]["delta"]["content"] == "no" and events[-1] == "[DONE]"
    plain = req("POST", host, "/chat/completions", fx.copilot_chat("x", stream=False))
    found = a.inspect(plain)
    assert found is not None
    full = json.loads(a.refuse(plain, found, Texts(), "no").body)
    assert full["choices"][0]["message"]["content"] == "no"
    # The other formats Copilot serves some models in answer in their own format.
    resp = req("POST", host, "/responses", fx.responses_body("x"))
    found = a.inspect(resp)
    assert found is not None
    assert sse_events(a.refuse(resp, found, Texts(), "no").body)[-1]["type"] == "response.completed"
    msgs = req("POST", host, "/v1/messages", fx.messages_body("x"))
    found = a.inspect(msgs)
    assert found is not None and found.text == "x"
    assert b"message_stop" in a.refuse(msgs, found, Texts(), "no").body
    with pytest.raises(FormatUnknown):
        a.inspect(req("POST", host, "/chat/completions", {"model": "m", "prompt": "x"}))


def test_copilot_completions_check_the_code_and_answer_nothing() -> None:
    a = a_copilot.Completions()
    path = "/v1/engines/copilot-codex/completions"
    assert a.matches("POST", "copilot-proxy.githubusercontent.com", path)
    assert a.matches("POST", "api.githubcopilot.com", "/completions")
    assert not a.matches("POST", "api.githubcopilot.com", "/chat/completions")
    r = req("POST", "api.githubcopilot.com", path, fx.copilot_completion("key = ", fx.SECRET))
    found = a.inspect(r)
    assert found is not None and fx.SECRET in found.text
    assert found.count and not found.record and not found.check_model
    again = a.inspect(req("POST", "x", path, fx.copilot_completion("key = more", fx.SECRET)))
    assert again is not None and again.fingerprint == found.fingerprint  # one event per file
    events = sse_events(a.refuse(r, found, Texts(), "no").body)
    assert events[0]["choices"][0]["text"] == "" and events[-1] == "[DONE]"
    plain = req("POST", "x", path, fx.copilot_completion("a", stream=False))
    found = a.inspect(plain)
    assert found is not None
    assert json.loads(a.refuse(plain, found, Texts(), "no").body)["choices"][0]["text"] == ""
    with pytest.raises(FormatUnknown):
        a.inspect(req("POST", "x", path, {"suffix": "only"}))


def test_usage_is_read_from_chat_completion_chunks() -> None:
    u = Usage("github", "text/event-stream")
    for chunk in fx.COPILOT_CHAT_SSE:
        u.feed(chunk)
    u.finish()
    assert (u.input_tokens, u.output_tokens, u.cache_read, u.complete) == (321, 4, 64, True)
    j = Usage("github", "application/json")
    j.feed(b'{"object":"chat.completion","usage":{"prompt_tokens":9,"completion_tokens":2}}')
    j.finish()
    assert (j.input_tokens, j.output_tokens) == (9, 2)
    r = Usage("openai", "")
    r.feed(fx.responses_sse())
    r.finish()
    assert (r.input_tokens, r.output_tokens, r.cache_read, r.complete) == (20314, 5, 12288, True)


def test_the_catalogue_serves_openai_and_github_now() -> None:
    have = providers_with_adapters()
    assert {"anthropic", "openai", "github"} <= have
    assert catalogue.served({"openai", "github"}, have) == ["openai", "github"]
    assert "api.github.com" in catalogue.IDENTITY_ONLY


# Decisions (no network)


@pytest.fixture
async def gate(env: Env) -> Gate:
    return await _gate(env, ("openai", "github"))


def codex_http(text: str, token: str | None = None, **body: Any) -> Any:
    return req(
        "POST",
        "chatgpt.com",
        CODEX,
        {**fx.responses_body(text), **body},
        authorization=bearer(token or fx.chatgpt_jwt()),
        user_agent=fx.CODEX_UA,
    )


async def test_a_gateway_can_enable_github_copilot(gate: Gate) -> None:
    r = await gate.env.client.post(
        GW, json={"slug": "copilot", "name": "Copilot", "providers": ["github"]}, headers=OP
    )
    assert r.status_code == 201 and r.json()["providers"] == ["github"]
    r = await gate.env.client.post(
        GW, json={"slug": "bad", "name": "Bad", "providers": ["gitlab"]}, headers=OP
    )
    assert r.status_code == 400
    assert gate.proxy.providers == ["openai", "github"]
    assert gate.proxy.connect("chatgpt.com", 443) == "intercept"
    assert gate.proxy.connect("api.githubcopilot.com", 443) == "intercept"
    assert gate.proxy.connect("claude.ai", 443) is None  # no gateway enables Anthropic


async def test_codex_is_identified_from_its_own_login_and_its_secret_refused(gate: Gate) -> None:
    ok = await gate.send(codex_http("hello"))
    assert ok.verdict is None and ok.pending is not None
    assert ok.pending.surface == "codex" and ok.pending.person.label == "Ada"
    assert ok.pending.usage and ok.pending.provider == "openai"
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    refused = await gate.send(codex_http(f"key {fx.SECRET}"))
    assert isinstance(refused.verdict, Answer) and refused.verdict.status == 200
    assert b"a secret or credential" in refused.verdict.body
    assert fx.SECRET.encode() not in refused.verdict.body
    rows = await gate.records()
    assert [(r.outcome, r.control, r.surface) for r in rows] == [
        ("blocked", "input.secrets", "codex")
    ]


async def test_a_chatgpt_account_outside_the_organisation_is_refused(env: Env) -> None:
    gate = await _gate(env, ("openai",), proxy_allowed_orgs={"openai": [fx.CHATGPT_ACCOUNT]})
    assert (await gate.send(codex_http("hello"))).verdict is None
    other = fx.chatgpt_jwt(account=fx.OTHER_CHATGPT_ACCOUNT)
    d = await gate.send(codex_http("hello", token=other))
    assert d.verdict is not None and b"Account outside the company" in d.verdict.body
    events = await gate.events()
    assert [(e.control, e.rule) for e in events] == [("proxy.org_refused", "openai")]
    # A write on chatgpt.com (mask) is held to OpenAI's organisations, not Anthropic's.
    analytics = req(
        "POST",
        "chatgpt.com",
        "/backend-api/codex/analytics-events/events",
        {"events": []},
        authorization=bearer(other),
        content_type="application/json",
    )
    masked = await gate.send(analytics)
    assert masked.verdict is not None and masked.verdict.status == 403


async def test_a_codex_frame_is_recorded_rewritten_or_answered(gate: Gate) -> None:
    proxy = gate.proxy
    adapter = proxy.frame_adapter("chatgpt.com", CODEX)
    assert adapter is not None
    up = codex_upgrade()
    passed = await proxy.frame(up, adapter, frame(fx.codex_create("hello")))
    assert passed.replace is None and not passed.drop and passed.pending is not None
    assert passed.pending.surface == "codex" and passed.pending.person.label == "Ada"
    assert (await proxy.frame(up, adapter, frame(fx.codex_warmup()))).pending is None
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    refused = await proxy.frame(up, adapter, frame(fx.codex_create(f"key {fx.SECRET}")))
    assert refused.replace is not None and refused.pending is None
    assert fx.SECRET.encode() not in refused.replace
    assert b"a secret or credential" in refused.replace
    stranger = codex_upgrade(fx.chatgpt_jwt(email="stranger@example.com"))
    unknown = await proxy.frame(stranger, adapter, frame(fx.codex_create("hello")))
    assert unknown.drop and unknown.reply
    assert b"does not recognise this account yet" in unknown.reply[4]
    rows = await gate.records()
    assert [(r.outcome, r.surface, r.status) for r in rows] == [("blocked", "codex", 101)]


async def test_chatgpt_web_is_rewritten(gate: Gate) -> None:
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    r = req(
        "POST",
        "chatgpt.com",
        "/backend-api/conversation",
        fx.chatgpt_conversation(f"key {fx.SECRET}"),
        authorization=bearer(fx.chatgpt_jwt()),
    )
    d = await gate.send(r)
    assert isinstance(d.verdict, Rewrite) and fx.SECRET.encode() not in d.verdict.body


async def learn_github(gate: Gate, path: str, data: Any) -> None:
    kind = gate.proxy.learns("GET", "api.github.com", path)
    assert kind is not None
    r = req("GET", "api.github.com", path, authorization=f"token {fx.GITHUB_TOKEN}")
    await gate.proxy.learn(kind, r, 200, json.dumps(data).encode())


def copilot_chat(text: str) -> Any:
    return req(
        "POST",
        "api.business.githubcopilot.com",
        "/chat/completions",
        fx.copilot_chat(text),
        authorization=bearer(fx.copilot_token()),
        user_agent=fx.COPILOT_UA,
    )


async def test_copilot_is_identified_through_the_github_token_it_was_minted_from(
    gate: Gate,
) -> None:
    proxy = gate.proxy
    assert proxy.adapter("POST", "api.github.com", "/repos/x/y/issues") is None  # unread
    assert proxy.learns("GET", "api.github.com", "/repos/x/y") is None
    # No binding yet: blocked with the restart message.
    d = await gate.send(copilot_chat("hello"))
    assert d.verdict is not None and b"does not recognise this account yet" in d.verdict.body
    # The token answer comes before the user answer: the tid waits for the account.
    await learn_github(gate, "/copilot_internal/v2/token", fx.copilot_token_answer())
    await learn_github(gate, "/user", fx.github_user(email=None))
    # No public e-mail and no person labelled with the login: still unidentified.
    proxy._people._items.clear()
    assert (await gate.send(copilot_chat("hello"))).verdict is not None
    # The Copilot user answer names the e-mail: the person is found.
    await learn_github(gate, "/copilot_internal/user", fx.copilot_user())
    ok = await gate.send(copilot_chat("hello"))
    assert ok.verdict is None and ok.pending is not None
    assert ok.pending.surface == "copilot" and ok.pending.provider == "github"
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    refused = await gate.send(copilot_chat(f"key {fx.SECRET}"))
    assert isinstance(refused.verdict, Answer)
    assert b"chat.completion.chunk" in refused.verdict.body
    assert fx.SECRET.encode() not in refused.verdict.body


async def test_a_github_login_without_email_finds_a_person_labelled_with_it(env: Env) -> None:
    gate = await _gate(env, ("github",))
    r = await env.client.post(f"{GW}/{gate.id}/people", json={"label": fx.GITHUB_LOGIN}, headers=OP)
    assert r.status_code == 201, r.text
    await learn_github(gate, "/user", fx.github_user(email=None))
    await learn_github(gate, "/copilot_internal/v2/token", fx.copilot_token_answer())
    ok = await gate.send(copilot_chat("hello"))
    assert ok.verdict is None and ok.pending is not None
    assert ok.pending.person.label == fx.GITHUB_LOGIN


async def test_copilot_organisations_are_checked(env: Env) -> None:
    gate = await _gate(env, ("github",), proxy_allowed_orgs={"github": ["example-org"]})
    await learn_github(gate, "/copilot_internal/user", fx.copilot_user(orgs=("x", "example-org")))
    assert (await gate.send(copilot_chat("hello"))).verdict is None
    await learn_github(gate, "/copilot_internal/user", fx.copilot_user(orgs=("other-org",)))
    gate.proxy.book._hits._items.clear()
    d = await gate.send(copilot_chat("hello"))
    assert d.verdict is not None and b"Account outside the company" in d.verdict.body


async def test_a_completion_with_a_secret_gets_no_suggestion_and_one_event(gate: Gate) -> None:
    await learn_github(gate, "/copilot_internal/user", fx.copilot_user())
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    for prompt in ("aws = ", "aws = boto3"):
        r = req(
            "POST",
            "copilot-proxy.githubusercontent.com",
            "/v1/engines/copilot-codex/completions",
            fx.copilot_completion(prompt, f"key = '{fx.SECRET}'"),
            authorization=bearer(fx.copilot_token()),
        )
        d = await gate.send(r)
        assert isinstance(d.verdict, Answer) and d.pending is None
        assert sse_events(d.verdict.body)[0]["choices"][0]["text"] == ""
    events = await gate.events()
    assert [(e.control, e.action, e.details["surface"]) for e in events] == [
        ("input.secrets", "blocked", "copilot")
    ]
    assert await gate.records() == []  # suggestions are not usage rows


# End to end


async def test_end_to_end_codex_websocket_records_rewrites_and_answers(tmp_path: Path) -> None:
    async with stack(tmp_path, ("openai",)) as s:
        ctx = client_ssl(s.ca.root)
        headers = {"authorization": bearer(fx.chatgpt_jwt()), "user-agent": fx.CODEX_UA}
        async with websockets.connect(
            f"wss://chatgpt.com{CODEX}", proxy=s.url, ssl=ctx, additional_headers=headers
        ) as ws:
            await ws.send(json.dumps(fx.codex_warmup()))
            assert json.loads(await asyncio.wait_for(ws.recv(), 5))["type"] == "response.completed"
            await ws.send(json.dumps(fx.codex_create("hello")))
            got = [json.loads(await asyncio.wait_for(ws.recv(), 5)) for _ in range(12)]
            assert got[-1]["type"] == "response.completed"
            await s.gate.policy({"modes": {"input.secrets": "enforce"}})
            await ws.send(json.dumps(fx.codex_create(f"key {fx.SECRET}")))
            for _ in range(12):
                await asyncio.wait_for(ws.recv(), 5)
        assert all(fx.SECRET not in f for f in s.up.frames)
        assert "a secret or credential" in s.up.frames[-1]
        stranger = {"authorization": bearer(fx.chatgpt_jwt(email="stranger@example.com"))}
        sent = len(s.up.frames)
        async with websockets.connect(
            f"wss://chatgpt.com{CODEX}", proxy=s.url, ssl=ctx, additional_headers=stranger
        ) as ws:
            await ws.send(json.dumps(fx.codex_create("hello")))
            answer = [json.loads(await asyncio.wait_for(ws.recv(), 5)) for _ in range(9)]
        assert answer[-1]["type"] == "response.completed"
        delta = next(e for e in answer if e["type"] == "response.output_text.delta")
        assert "does not recognise this account yet" in delta["delta"]
        assert len(s.up.frames) == sent  # never reached the server
        rows = await s.gate.records()
        assert sorted(
            (r.outcome, r.input_tokens, r.output_tokens, r.cache_read_tokens) for r in rows
        ) == [
            ("blocked", 0, 0, 0),
            ("forwarded", 20314, 5, 12288),
        ]


async def test_end_to_end_codex_http_and_copilot(tmp_path: Path) -> None:
    async with stack(tmp_path, ("openai", "github")) as s, s.client() as pc:
        auth = {"authorization": bearer(fx.chatgpt_jwt()), "user-agent": fx.CODEX_UA}
        r = await pc.post(f"https://chatgpt.com{CODEX}", json=fx.responses_body("hi"), headers=auth)
        assert r.status_code == 200 and r.content == fx.responses_sse()
        await s.gate.policy({"modes": {"input.secrets": "enforce"}})
        body = fx.responses_body(f"key {fx.SECRET}")
        r = await pc.post(f"https://chatgpt.com{CODEX}", json=body, headers=auth)
        assert r.headers["x-evokai-proxy"] == "refused" and b"a secret or credential" in r.content
        # Copilot: the GitHub answers bind the account; the rest of api.github.com is unread.
        gh = {"authorization": f"token {fx.GITHUB_TOKEN}"}
        for path in ("/copilot_internal/v2/token", "/copilot_internal/user"):
            r = await pc.get(f"https://api.github.com{path}", headers=gh)
            assert r.status_code == 200
        r = await pc.post(
            "https://api.github.com/repos/x/y/issues", json={"body": f"k {fx.SECRET}"}, headers=gh
        )
        assert r.status_code == 200
        issue = next(x for x in s.up.seen if x.path == "/repos/x/y/issues")
        assert fx.SECRET in issue.body.decode()  # passed unread, never masked
        cp = {"authorization": bearer(fx.copilot_token()), "user-agent": fx.COPILOT_UA}
        url = "https://api.githubcopilot.com/chat/completions"
        r = await pc.post(url, json=fx.copilot_chat("hello"), headers=cp)
        assert r.content == b"".join(fx.COPILOT_CHAT_SSE)
        r = await pc.post(url, json=fx.copilot_chat(f"key {fx.SECRET}"), headers=cp)
        assert r.headers["x-evokai-proxy"] == "refused" and b"[DONE]" in r.content
        sent = [x for x in s.up.seen if x.path in (CODEX, "/chat/completions")]
        assert len(sent) == 2 and all(fx.SECRET not in x.body.decode() for x in sent)
        rows = await s.gate.records()
        assert sorted((r.surface, r.outcome, r.input_tokens, r.output_tokens) for r in rows) == [
            ("codex", "blocked", 0, 0),
            ("codex", "forwarded", 20314, 5),
            ("copilot", "blocked", 0, 0),
            ("copilot", "forwarded", 321, 4),
        ]
