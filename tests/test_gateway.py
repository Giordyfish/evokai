"""Gateways: pass-through fidelity, personal keys, the controls, usage, isolation, the scanner."""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import time
from collections.abc import AsyncIterator, MutableMapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from evokai import policies
from evokai.assistant import delegation
from evokai.core.security import generate_key
from evokai.gateway import checks, scan
from evokai.gateway.relay import Relay, Usage
from tests.conftest import Env

OPERATOR_KEY = "evk_op_gateway_tests_operator_key_0123456789"  # gitleaks:allow
OP = {"Authorization": f"Bearer {OPERATOR_KEY}"}
CONSOLE = {"X-Evokai-Console": "1"}
SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"
DOOR = "/agent/gw/anthropic/v1/messages"
BETA = "oauth-2025-04-20,interleaved-thinking-2025-05-14"  # gitleaks:allow (beta names)
GW = "/agent/v1/admin/gateways"

SSE = (
    b"event: message_start\n"
    b'data: {"type":"message_start","message":{"id":"msg_1","model":"claude-sonnet-4-5",'
    b'"usage":{"input_tokens":12,"cache_read_input_tokens":100,"cache_creation_input_tokens":30,'
    b'"output_tokens":1}}}\n\n',
    b"event: ping\ndata: {}\n\n",
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n',
    b"event: ping\ndata: {}\n\n",
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"ok"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
    b'"usage":{"output_tokens":7}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
)
UPSTREAM_HEADERS = {
    "content-type": "text/event-stream; charset=utf-8",
    "request-id": "req_123",
    "anthropic-ratelimit-unified-status": "allowed",
    "anthropic-ratelimit-unified-5h-utilization": "0.12",
    "x-should-retry": "false",
    "cache-control": "no-cache",
}


@dataclass
class Upstream:
    """A fake provider: records what reaches it, answers with a script."""

    calls: list[httpx.Request] = field(default_factory=list)
    bodies: list[bytes] = field(default_factory=list)
    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: dict(UPSTREAM_HEADERS))
    chunks: tuple[bytes, ...] = SSE
    fail: Exception | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        self.bodies.append(await request.aread())
        if self.fail is not None:
            raise self.fail

        async def stream() -> AsyncIterator[bytes]:
            for c in self.chunks:
                await asyncio.sleep(0)
                yield c

        return httpx.Response(self.status, headers=self.headers, content=stream())


@dataclass
class Gate:
    env: Env
    up: Upstream
    id: str
    key: str
    key_id: str

    def headers(self, **extra: str) -> dict[str, str]:
        return {
            "x-evokai-key": self.key,
            "authorization": "Bearer person-oauth-token",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": BETA,
            "content-type": "application/json",
            **extra,
        }

    async def send(self, body: dict[str, Any] | bytes, **extra: str) -> httpx.Response:
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        return await self.env.client.post(
            f"{DOOR}?beta=true", content=content, headers=self.headers(**extra)
        )

    async def policy(self, security: dict[str, Any], **more: Any) -> None:
        r = await self.env.client.patch(
            f"{GW}/{self.id}", json={"security": security, **more}, headers=OP
        )
        assert r.status_code == 200, r.text

    async def events(self) -> list[Any]:
        await self.env.svc.gateway.idle()
        return list(await self.env.svc.store.security_events.query(self.id))


def message(text: str, **more: Any) -> dict[str, Any]:
    return {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "stream": True,
        "system": [{"type": "text", "text": "You are Claude Code."}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        **more,
    }


async def _gateway(env: Env, up: Upstream, **body: Any) -> Gate:
    env.svc.settings.operator_key = OPERATOR_KEY
    env.svc.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(up.handler))
    payload = {"slug": "development", "name": "Development", "providers": ["anthropic"], **body}
    r = await env.client.post(GW, json=payload, headers=OP)
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    r = await env.client.post(f"{GW}/{gid}/people", json={"label": "ada@example.com"}, headers=OP)
    assert r.status_code == 201, r.text
    return Gate(env, up, gid, r.json()["key"], r.json()["id"])


@pytest.fixture
async def gate(env: Env) -> Gate:
    return await _gateway(env, Upstream())


# Pass-through fidelity


async def test_headers_body_and_stream_pass_through_unchanged(gate: Gate) -> None:
    body = json.dumps(message("hello"), separators=(",", ":"), ensure_ascii=False).encode()
    body += b"   "  # odd spacing must survive: the bytes are forwarded, not re-encoded
    r = await gate.send(body, **{"x-claude-code-session-id": "s1", "x-custom-thing": "kept"})
    assert r.status_code == 200
    # Out: the stream byte for byte (pings included) and the provider's headers.
    assert r.content == b"".join(SSE)
    for name, value in UPSTREAM_HEADERS.items():
        assert r.headers[name] == value
    # In: the same bytes, the same query, every header but the key and the hop-by-hop ones.
    (req,) = gate.up.calls
    assert str(req.url) == "https://api.anthropic.com/v1/messages?beta=true"
    assert gate.up.bodies[0] == body
    assert req.headers["authorization"] == "Bearer person-oauth-token"
    assert req.headers["anthropic-beta"] == BETA
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert req.headers["x-claude-code-session-id"] == "s1"
    assert req.headers["x-custom-thing"] == "kept"
    assert "x-evokai-key" not in req.headers
    assert req.headers["host"] == "api.anthropic.com"
    assert req.headers["content-length"] == str(len(body))


async def test_usage_is_read_from_the_stream_and_recorded_without_content(gate: Gate) -> None:
    await gate.send(message("hello"))
    (row,) = await gate.env.svc.store.gateway.recent(gate.id)
    assert (row.outcome, row.status, row.person, row.provider) == (
        "forwarded",
        200,
        "ada@example.com",
        "anthropic",
    )
    assert (row.input_tokens, row.output_tokens, row.cache_read_tokens) == (142, 7, 100)
    assert row.cache_write_tokens == 30
    assert row.request_bytes > 0 and row.response_bytes == len(b"".join(SSE))
    assert row.first_byte_ms is not None
    r = await gate.env.client.get(f"{GW}/{gate.id}/usage", headers=OP)
    usage = r.json()
    assert usage["people"][0]["person"] == "ada@example.com"
    assert usage["people"][0]["outputTokens"] == 7
    assert usage["models"][0]["model"] == "claude-sonnet-4-5"
    assert usage["outcomes"] == {"forwarded": 1}
    assert "hello" not in json.dumps(usage)
    assert not any("cost" in k.lower() for k in usage["people"][0])


async def test_errors_of_the_provider_pass_through_unchanged(gate: Gate) -> None:
    error = (
        b'{"type":"error","error":{"type":"invalid_request_error",'
        b'"message":"context_management: Extra inputs are not permitted"}}'
    )
    gate.up.status = 400
    gate.up.headers = {"content-type": "application/json", "x-should-retry": "false"}
    gate.up.chunks = (error,)
    r = await gate.send(message("hello"))
    assert (r.status_code, r.content, r.headers["x-should-retry"]) == (400, error, "false")
    gate.up.status = 429
    gate.up.headers = {
        "content-type": "application/json",
        "retry-after": "17",
        "anthropic-ratelimit-unified-status": "rejected",
    }
    r = await gate.send(message("hello"))
    assert (r.status_code, r.headers["retry-after"]) == (429, "17")
    assert r.headers["anthropic-ratelimit-unified-status"] == "rejected"
    rows = await gate.env.svc.store.gateway.recent(gate.id)
    assert [x.outcome for x in rows] == ["upstream_error", "upstream_error"]


async def test_an_unreachable_provider_answers_in_its_error_shape(gate: Gate) -> None:
    gate.up.fail = httpx.ConnectError("refused")
    r = await gate.send(message("hello"))
    assert r.status_code == 502
    assert r.json()["type"] == "error" and r.json()["error"]["type"] == "api_error"


async def test_count_tokens_is_forwarded(gate: Gate) -> None:
    gate.up.headers = {"content-type": "application/json"}
    gate.up.chunks = (b'{"input_tokens":42}',)
    r = await gate.env.client.post(
        f"{DOOR}/count_tokens?beta=true",
        content=json.dumps(message("hi")),
        headers=gate.headers(),
    )
    assert (r.status_code, r.json()) == (200, {"input_tokens": 42})
    assert str(gate.up.calls[0].url).endswith("/v1/messages/count_tokens?beta=true")


async def test_the_tools_disconnect_cancels_the_provider_stream() -> None:
    closed = asyncio.Event()

    async def endless() -> AsyncIterator[bytes]:
        try:
            while True:
                yield b"event: ping\ndata: {}\n\n"
                await asyncio.sleep(0.01)
        finally:
            closed.set()

    answer = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=endless())
    outcomes: list[str] = []

    async def done(outcome: str) -> None:
        await asyncio.sleep(0)
        outcomes.append(outcome)

    sent: list[MutableMapping[str, Any]] = []
    gone = asyncio.Event()

    async def receive() -> MutableMapping[str, Any]:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(msg: MutableMapping[str, Any]) -> None:
        await asyncio.sleep(0)
        sent.append(msg)
        if len(sent) == 3:
            gone.set()

    relay = Relay(answer, Usage("anthropic", "text/event-stream"), done)
    await asyncio.wait_for(relay({"type": "http"}, receive, send), timeout=5)
    assert outcomes == ["cancelled"]
    assert answer.is_closed
    assert sent[-1].get("more_body") is True  # never a clean end: the tool knows it was cut


def test_usage_survives_any_chunking() -> None:
    whole = b"".join(SSE)
    u = Usage("anthropic", "text/event-stream")
    for i in range(len(whole)):
        u.feed(whole[i : i + 1])
    u.finish()
    assert (u.input_tokens, u.output_tokens, u.cache_read, u.cache_write) == (142, 7, 100, 30)
    j = Usage("anthropic", "application/json")
    j.feed(b'{"usage":{"input_tokens":5,"output_tokens":2}}')
    j.finish()
    assert (j.input_tokens, j.output_tokens) == (5, 2)
    # A gzipped stream (the provider compresses its events) is read from a decompressed copy.
    packed = gzip.compress(whole)
    z = Usage("anthropic", "text/event-stream", "gzip")
    for i in range(0, len(packed), 7):
        z.feed(packed[i : i + 7])
    z.finish()
    assert (z.input_tokens, z.output_tokens, z.bytes) == (142, 7, len(packed))
    assert Usage("anthropic", "text/event-stream", "br").off
    assert u.complete


# Keys


async def test_a_request_needs_a_live_personal_key(gate: Gate) -> None:
    body = json.dumps(message("hi"))
    for key in (None, "evk_gw_nope_nope", gate.env.server_key, gate.key + "x"):
        headers = gate.headers()
        if key is None:
            del headers["x-evokai-key"]
        else:
            headers["x-evokai-key"] = key
        r = await gate.env.client.post(DOOR, content=body, headers=headers)
        assert r.status_code == 401, key
        assert r.json()["error"]["type"] == "authentication_error"
        assert "x-evokai-key" in r.json()["error"]["message"]
    assert gate.up.calls == []
    assert (await gate.send(message("hi"))).status_code == 200
    r = await gate.env.client.delete(f"{GW}/{gate.id}/people/{gate.key_id}", headers=OP)
    assert r.status_code == 200
    assert (await gate.send(message("hi"))).status_code == 401
    actions = [e.action for e in await gate.env.svc.store.audit.query(gate.id)]
    assert {"gateway.create", "gateway.key_create", "gateway.key_revoke"} <= set(actions)


async def test_a_disabled_gateway_refuses_its_people(gate: Gate) -> None:
    r = await gate.env.client.patch(f"{GW}/{gate.id}", json={"status": "disabled"}, headers=OP)
    assert r.status_code == 200
    r = await gate.send(message("hi"))
    assert r.status_code == 401 and "not active" in r.json()["error"]["message"]


async def test_gateway_keys_and_application_keys_never_cross(gate: Gate) -> None:
    env = gate.env
    # A person's key is no server key.
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u", "agent": "helper"},
        headers={"Authorization": f"Bearer {gate.key}"},
    )
    assert r.status_code == 401
    # A server key put on a gateway's row (it never has one) opens nothing.
    srv = generate_key("srv")
    await env.svc.store.applications.add_key(gate.id, "srv", srv.prefix, srv.secret_hash)
    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u", "agent": "helper"},
        headers={"Authorization": f"Bearer {srv.value}"},
    )
    assert r.status_code == 401
    r = await env.client.post(
        "/agent/v1/openai/chat/completions",
        json={"model": "helper", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {srv.value}"},
    )
    assert r.status_code == 401
    # An application's admin key does not manage gateways.
    r = await env.client.get(GW, headers={"Authorization": f"Bearer {env.admin_key}"})
    assert r.status_code == 401


# The controls


async def test_a_secret_is_recorded_by_default_and_refused_when_enforced(gate: Gate) -> None:
    r = await gate.send(message(f"my key is {SECRET}"))
    assert r.status_code == 200
    (e,) = await gate.events()
    assert (e.control, e.action, e.mode, e.rule) == (
        "input.secrets",
        "logged",
        "monitor",
        "aws_access_key",
    )
    assert e.snippet == "" and e.subject == "ada@example.com"
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    r = await gate.send(message(f"my key is {SECRET}"))
    assert r.status_code == 400
    assert r.headers["x-should-retry"] == "false"
    err = r.json()
    assert err["type"] == "error" and err["error"]["type"] == "invalid_request_error"
    assert "input.secrets" in err["error"]["message"] and SECRET not in err["error"]["message"]
    assert len(gate.up.calls) == 1
    assert {x.action for x in await gate.events()} == {"logged", "blocked"}
    # The tool sends the same refused request again: one more request, no new event.
    assert (await gate.send(message(f"my key is {SECRET}"))).status_code == 400
    assert len(await gate.events()) == 2
    rows = await gate.env.svc.store.gateway.recent(gate.id)
    assert (rows[0].outcome, rows[0].control) == ("blocked", "input.secrets")


async def test_only_the_new_text_is_checked(gate: Gate) -> None:
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    body = message("go on")
    body["system"] = [{"type": "text", "text": f"system {SECRET}"}]
    body["messages"] = [
        {"role": "user", "content": f"old {SECRET}"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t", "name": "Read", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t", "content": "file text"}],
        },
    ]
    assert (await gate.send(body)).status_code == 200
    result: dict[str, Any] = body["messages"][2]["content"][0]
    result["content"] = [{"type": "text", "text": f"key={SECRET}"}]
    assert (await gate.send(body)).status_code == 400


def test_the_new_text_of_each_provider() -> None:
    anthropic = {
        "messages": [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
            {"role": "system", "content": "a reminder"},
        ]
    }
    assert checks.anthropic_new_text(anthropic) == "second"
    openai = {
        "instructions": "system",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "a"}]},
            {"type": "function_call", "call_id": "c", "name": "shell", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c", "output": "tool says"},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "b"}]},
        ],
    }
    assert checks.openai_new_text(openai) == "tool says\nb"


async def test_personal_data_scope_and_size_block_when_enforced(gate: Gate) -> None:
    await gate.policy({
        "modes": {"pii.model": "enforce", "scope": "enforce", "input.size": "enforce"},
        "scope": {"deny": ["weapons"]},
        "input": {"max_chars": 50},
    })
    r = await gate.send(message("write to mario.rossi@example.com"))
    assert r.status_code == 400 and "pii.model" in r.json()["error"]["message"]
    assert "mario" not in r.json()["error"]["message"]
    r = await gate.send(message("about weapons"))
    assert r.status_code == 400 and "scope" in r.json()["error"]["message"]
    r = await gate.send(message("x" * 51))
    assert r.status_code == 400 and "input.size" in r.json()["error"]["message"]
    assert gate.up.calls == []
    assert (await gate.send(message("fine"))).status_code == 200


async def test_provider_and_model_must_be_allowed(env: Env) -> None:
    gate = await _gateway(env, Upstream(), allowed_models=["claude-sonnet-*"])
    r = await gate.send(message("hi", model="claude-opus-4-8"))
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_request_error"
    assert "claude-opus-4-8" in r.json()["error"]["message"]
    assert (await gate.send(message("hi"))).status_code == 200
    r = await env.client.patch(f"{GW}/{gate.id}", json={"providers": ["openai"]}, headers=OP)
    assert r.status_code == 200
    r = await gate.send(message("hi"))
    assert r.status_code == 400 and "Anthropic is not enabled" in r.json()["error"]["message"]
    assert [e.control for e in await gate.events()] == ["gateway.provider", "gateway.model"]


async def test_the_rate_and_the_token_caps_answer_429(gate: Gate) -> None:
    await gate.policy({"modes": {"abuse.rate": "enforce"}, "abuse": {"requests_per_minute": 2}})
    codes = [(await gate.send(message("hi"))).status_code for _ in range(3)]
    if codes[-1] == 200:  # the minute turned between two requests
        codes = [(await gate.send(message("hi"))).status_code for _ in range(3)]
    assert codes[-1] == 429
    r = await gate.send(message("hi"))
    assert r.json()["error"]["type"] == "rate_limit_error" and int(r.headers["retry-after"]) >= 1
    await gate.policy({"modes": {"abuse.rate": "off"}})
    r = await gate.env.client.patch(f"{GW}/{gate.id}", json={"tokens_per_day": 100}, headers=OP)
    assert r.status_code == 200
    gate.env.svc.gateway._used.clear()  # the cache would hide the new rows
    r = await gate.send(message("hi"))
    assert r.status_code == 429 and "daily token cap" in r.json()["error"]["message"]


async def test_data_protection_and_system_floors_apply(gate: Gate) -> None:
    r = await gate.env.client.patch(
        f"{GW}/{gate.id}", json={"data_protection": {"jurisdiction": ["eu"]}}, headers=OP
    )
    assert r.status_code == 200, r.text
    r = await gate.send(message("hi"))
    assert r.status_code == 400
    assert "data protection" in r.json()["error"]["message"]
    await gate.env.client.patch(f"{GW}/{gate.id}", json={"data_protection": {}}, headers=OP)
    assert (await gate.send(message("hi"))).status_code == 200
    # The system policy's minimum raises the gateway's mode.
    r = await gate.env.client.put(
        "/agent/v1/console/system-policy",
        json={"security": {"modes": {"input.secrets": "enforce"}}},
        headers=OP,
    )
    assert r.status_code == 200, r.text
    gate.env.svc.gateway.forget(gate.id)
    assert (await gate.send(message(f"k {SECRET}"))).status_code == 400


async def test_a_gateway_policy_has_versions_and_gateway_tests(gate: Gate) -> None:
    env = gate.env
    assert (
        await env.client.post("/agent/v1/console/login", json={"key": OPERATOR_KEY})
    ).status_code == 204
    pick = {**CONSOLE, "X-Evokai-Application": gate.id}
    r = await env.client.post(
        "/agent/v1/admin/policy/tests",
        json={"name": "email", "input": "mail a@example.com", "controls": {"pii.model": "blocked"}},
        headers=pick,
    )
    assert r.status_code == 201, r.text
    r = await env.client.post("/agent/v1/admin/policy/tests/run", headers=pick)
    assert r.json()["failed"] == 1 and r.json()["items"][0]["got"]["controls"] == {
        "pii.model": "flagged"
    }
    r = await env.client.put(
        "/agent/v1/admin/security", json={"modes": {"pii.model": "enforce"}}, headers=pick
    )
    assert r.status_code == 200, r.text
    r = await env.client.get("/agent/v1/admin/policy/versions", headers=pick)
    assert [v["number"] for v in r.json()["items"]] == [2, 1]
    assert await policies.is_gateway(env.svc, gate.id)


# Isolation from applications


async def test_gateways_are_never_applications(gate: Gate) -> None:
    env = gate.env
    r = await env.client.get("/agent/v1/operator/applications", headers=OP)
    assert gate.id not in {a["id"] for a in r.json()["items"]}
    assert (
        await env.client.post("/agent/v1/console/login", json={"key": OPERATOR_KEY})
    ).status_code == 204
    r = await env.client.get("/agent/v1/console/applications", headers=CONSOLE)
    assert gate.id not in {a["id"] for a in r.json()["items"]}
    pick = {**CONSOLE, "X-Evokai-Application": gate.id}
    for path in ("/agent/v1/admin/agents", "/agent/v1/admin/keys", "/agent/v1/admin/application"):
        assert (await env.client.get(path, headers=pick)).status_code == 404, path
    for path in (
        "/agent/v1/admin/security",
        "/agent/v1/admin/security/events",
        "/agent/v1/admin/audit",
    ):
        assert (await env.client.get(path, headers=pick)).status_code == 200, path
    # The console assistant and the admin MCP server's list_applications never see it.
    login = env.operator_login(600)
    token = delegation.mint_delegation(env.svc.keyring, login, delegation.ALL, "owner")
    names = [a["slug"] for a in await delegation.applications(env.svc, token)]
    assert "development" not in names and "test" in names
    with pytest.raises(Exception):  # ruff: ignore[assert-raises-exception] (any refusal)
        await delegation.principal(env.svc, token, "development")
    # Gateways are listed apart, and an application is not a gateway.
    r = await env.client.get(GW, headers=OP)
    assert [g["slug"] for g in r.json()["items"]] == ["development"]
    assert (await env.client.get(f"{GW}/{env.app_id}", headers=OP)).status_code == 404
    assert (await env.client.get(f"{GW}/development", headers=OP)).json()["id"] == gate.id


async def test_slugs_are_shared_and_only_owners_create_gateways(gate: Gate) -> None:
    env = gate.env
    r = await env.client.post(
        GW, json={"slug": "test", "name": "X", "providers": ["anthropic"]}, headers=OP
    )
    assert r.status_code == 409
    r = await env.client.post(
        "/agent/v1/operator/applications", json={"slug": "development", "name": "X"}, headers=OP
    )
    assert r.status_code == 409


# The scanner: linear time on adversarial input


@pytest.mark.parametrize(
    "unit",
    [
        "a",
        "1",
        "1 ",
        "0 12",
        "IT60 ",
        "3331234567 ",
        "a@b.",
        "+39 3",
        "evk_srv_",
        "-----BEGIN ",
        "123e4567-e89b-12d3-a456-426614174000 333 1234567 ",
    ],
)
def test_the_scanner_is_linear_on_one_megabyte(unit: str) -> None:
    text = (unit * (1_000_000 // len(unit) + 1))[:1_000_000]
    deny = ["weapons", "(?i)bomb(s)?", "foo(?=bar)"]
    started = time.perf_counter()
    scan.find_secrets(text)
    scan.find_pii(text)
    scan.find_pii(text, limit=checks.PII_LIMIT)
    scan.denied_topic(text, deny)
    assert time.perf_counter() - started < 3.0


def test_the_scanner_finds_what_the_guard_finds() -> None:
    from evokai.guard import pii, secrets

    texts = [
        "write to mario.rossi@example.com",
        "CF RSSMRA85T10A562S",
        "IBAN IT60 X054 2811 1010 0000 0123 456 del cliente",
        "card 4111 1111 1111 1111",
        "partita IVA 12345678903",
        "call +39 333 123 4567",
        "office 06 1234 5678",
        "on 02-11-2026",
        "id 123e4567-e89b-12d3-a456-426614174000",
        f"key {SECRET}",
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIB",
        "ghp_" + "a" * 36,
    ]
    for t in texts:
        assert scan.find_pii(t) == pii.counts(pii.find(t)), t
        assert set(scan.find_secrets(t)) >= {f.rule for f in secrets.find(t)}, t
    assert {r for r, _ in scan.SECRET_RULES} == {r for r, _ in secrets.RULES}


def test_roles_on_a_gateway() -> None:
    from evokai.identity.roles import required

    assert required("GET", "/v1/admin/gateways/{gateway_id}/usage") == "viewer"
    assert required("POST", "/v1/admin/gateways/{gateway_id}/people") == "operator"
    assert required("DELETE", "/v1/admin/gateways/{gateway_id}/people/{key_id}") == "operator"
    assert required("PATCH", "/v1/admin/gateways/{gateway_id}") == "owner"


async def test_a_broken_provider_stream_ends_the_tools_without_a_clean_end(gate: Gate) -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield SSE[0]
        await asyncio.sleep(0)
        raise httpx.ReadError("reset")

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=broken())

    gate.env.svc.gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # The test client sees a response that never completed (a real one sees the connection end).
    with contextlib.suppress(AssertionError, httpx.HTTPError):
        await gate.send(message("hi"))
    await gate.env.svc.gateway.idle()
    (row,) = await gate.env.svc.store.gateway.recent(gate.id)
    assert (row.outcome, row.input_tokens) == ("failed", 142)
