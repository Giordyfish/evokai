"""Recorded HTTP cassettes: real provider and MCP traffic, replayed without a network.

Every test here drives Evokai through its public HTTP API (a session token, ``/v1/session/chat``)
and asserts on the events a client receives, while the traffic Evokai sends out is recorded at the
HTTP boundary (botocore's urllib3 for Bedrock, httpx for MCP). Nothing depends on how the turn
engine runs a turn inside.

- Replay is the default (``--record-mode=none``): a request that is not in its cassette fails, so
  CI never reaches the network. A test whose cassette has not been recorded yet is skipped.
- Recording is an explicit opt-in (``scripts/record-cassettes.sh``, docs/deployment.md): it
  needs real credentials for Bedrock and, for MCP, the reference server in
  ``scripts/cassettes/mcp_reference_server.py``.
- Authorization, cookies, ``x-amz-*`` headers, keys and session tokens never reach a cassette
  (``VCR_CONFIG``); ``test_cassettes_carry_no_secrets`` checks every cassette in the repository.

The first two tests need no cassette: they record against local stand-ins (a Bedrock endpoint
that speaks the event stream, the fake MCP server over real HTTP) into a temporary cassette and
replay it with the stand-ins stopped, to prove the recording works with both HTTP stacks.
"""

from __future__ import annotations

import binascii
import contextlib
import json
import re
import shutil
import struct
import threading
from collections.abc import Generator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
import vcr

from evokai.llm.catalog import bare
from tests.conftest import AGENTS_YAML, FakeMcp, Tool, make_env

CASSETTES = Path(__file__).parent / "cassettes"
SEEDS = CASSETTES / "seeds"
"""Interactions a provider cannot be made to send on demand (a throttling answer), written by
hand and copied into a cassette before it is recorded."""

HAIKU = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
"""The model the Bedrock cassettes are recorded with: the cheapest Haiku the deployment uses."""
THROTTLED = "eu.anthropic.claude-sonnet-4-6"
"""The primary of the fallback cassette: its answer is the seeded throttling, never a real call."""

SECRET_HEADERS = ("authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization")
SECRET_PARAMS = [
    "X-Amz-Signature",
    "X-Amz-Credential",
    "X-Amz-Security-Token",
    "key",
    "api_key",
    "access_token",
    "token",
]
MONEY = re.compile(r"usd|eur|cost|price|amount|spend", re.IGNORECASE)


def _drop_headers(headers: dict[str, Any]) -> None:
    for name in list(headers):
        low = name.lower()
        if low in SECRET_HEADERS or low.startswith(("x-amz-", "amz-sdk-")) or low == "user-agent":
            del headers[name]


def scrub_request(request: Any) -> Any:
    """What a cassette keeps of a request: no credentials, signatures or session tokens.

    Returns:
        The request, scrubbed.
    """
    _drop_headers(request.headers)
    return request


def scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    """What a cassette keeps of a response: no cookies.

    Returns:
        The response, scrubbed.
    """
    _drop_headers(response.get("headers") or {})
    return response


VCR_CONFIG: dict[str, Any] = {
    # The system prompt carries the date, so bodies are not matched: requests to the same model
    # and server are answered in the order they were recorded.
    "match_on": ["method", "scheme", "host", "port", "path"],
    "filter_query_parameters": SECRET_PARAMS,
    "before_record_request": scrub_request,
    "before_record_response": scrub_response,
    "decode_compressed_response": True,
    # Evokai's own API (the test client) and the in-memory fake MCP server are not recorded.
    "ignore_hosts": ["evokai", "app.test"],
}


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return VCR_CONFIG


@pytest.fixture
def cassette(record_mode: str, vcr_cassette_dir: str, default_cassette_name: str) -> Iterator[Path]:
    """The test's cassette: skipped when it was never recorded and the run only replays.

    While recording, a seeded cassette starts from its seed.

    Yields:
        Its path.
    """
    path = Path(vcr_cassette_dir) / f"{default_cassette_name}.yaml"
    if record_mode == "none" and not path.exists():
        pytest.skip(f"cassette not recorded yet: {path.name} (scripts/record-cassettes.sh)")
    seed = SEEDS / path.name
    if record_mode != "none" and not path.exists() and seed.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(seed, path)
    yield path


@pytest.fixture
def aws(record_mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AWS settings of a Bedrock test: real credentials only while recording.

    Replaying signs with throw-away keys and no profile (the signature never reaches the
    cassette). Either way botocore makes one attempt: a throttling answer goes straight to
    Evokai's fallback.
    """
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("AWS_RETRY_MODE", "standard")
    # The cassettes hold eu-north-1's endpoint: the region is fixed, whatever the shell says.
    monkeypatch.setenv("EVOKAI_AWS_REGION", "eu-north-1")
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    if record_mode != "none":
        return
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-aws-credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")


def _yaml(model: str, *, fallbacks: list[str] | None = None, mcp_url: str | None = None) -> str:
    """The test application with one small agent: no client tools, no skills, short answers.

    Returns:
        The application YAML.
    """
    out = AGENTS_YAML.split("agents:\n")[0] + "agents:\n"
    if mcp_url:
        out = out.replace('url: "http://app.test/mcp"', f'url: "{mcp_url}"')
    return out + (
        "  - name: helper\n"
        f'    model: "{model}"\n'
        f"    fallbacks: {json.dumps(fallbacks or [])}\n"
        "    mcp_servers: [app]\n"
        "    skills: []\n"
        "    max_tool_calls_per_turn: 3\n"
        "    client_tools: {chart: false, diagram: false, ask: false, memory: false,"
        " navigate: {enabled: false}}\n"
        '    instructions: "Answer in one short sentence. For the orders of a month,'
        ' always call orders_month first."\n'
    )


def _tools(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(e["name"], e["status"]) for e in events if e["type"] == "tool"]


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in events if e["type"] == "text")


def _keys(value: object) -> list[str]:
    if isinstance(value, dict):
        return [*value, *(k for v in value.values() for k in _keys(v))]
    if isinstance(value, list):
        return [k for v in value for k in _keys(v)]
    return []


def _assert_answered(events: list[dict[str, Any]]) -> None:
    assert events[-1]["type"] == "done" and events[-1]["reason"] == "end_turn", events[-3:]
    assert not [e for e in events if e["type"] == "error"]
    assert _text(events).strip()
    usage = [e for e in events if e["type"] == "usage"]
    assert usage and not [k for e in usage for k in _keys(e) if MONEY.search(k)]


# Recorded against the real services


@pytest.mark.vcr
@pytest.mark.usefixtures("cassette", "aws")
async def test_bedrock_converse_stream_turn_with_a_tool_call(tmp_path: Path) -> None:
    async with make_env(tmp_path, agents_yaml=_yaml(HAIKU), models=None) as env:
        tok = await env.token()
        events = await env.chat(tok["token"], text="How many orders in 2026-10?")
    _assert_answered(events)
    assert _tools(events) == [("orders_month", "start"), ("orders_month", "done")]
    ((name, args),) = env.mcp.calls
    assert name == "orders_month" and args.get("period") == "2026-10"
    # The fake MCP server answers 24: the model's answer carries it.
    assert "24" in _text(events)


@pytest.mark.vcr
@pytest.mark.usefixtures("cassette", "aws")
async def test_bedrock_fallback_after_a_throttled_primary(tmp_path: Path) -> None:
    yaml = _yaml(THROTTLED, fallbacks=[HAIKU])
    async with make_env(tmp_path, agents_yaml=yaml, models=None) as env:
        tok = await env.token()
        events = await env.chat(tok["token"], text="Say hello in one word.")
        usage = await env.client.get(
            "/agent/v1/usage",
            params={"groupBy": "model"},
            headers={"Authorization": f"Bearer {env.server_key}"},
        )
    _assert_answered(events)
    assert _tools(events) == []
    models = {row["model"] for row in usage.json()["rows"]}
    assert models == {HAIKU}


@pytest.mark.vcr
@pytest.mark.usefixtures("cassette")
async def test_mcp_streamable_http_tools_list_and_call(tmp_path: Path) -> None:
    def answer(messages: list[Any]) -> list[Any]:
        # The scripted model repeats what the tool returned: the server's answer reaches the user.
        returned = [
            str(p.content)
            for m in messages
            for p in getattr(m, "parts", [])
            if type(p).__name__ == "ToolReturnPart"
        ]
        return [f"The server said: {returned[-1]}"]

    yaml = _yaml("function:primary", mcp_url="http://127.0.0.1:8765/mcp")
    async with (
        httpx.AsyncClient() as http,
        make_env(tmp_path, agents_yaml=yaml, http=http) as env,
    ):
        env.script.add(
            "function:primary", [Tool("orders_month", {"period": "2026-10"}, "c1")], answer
        )
        tok = await env.token()
        events = await env.chat(tok["token"], text="October?")
    _assert_answered(events)
    assert _tools(events) == [("orders_month", "start"), ("orders_month", "done")]
    # tools/list told the reference server's read-only tool from the one that changes data.
    assert "orders_month" in env.script.tools_seen and "order_refund" in env.script.tools_seen
    assert '"orders": 24' in _text(events) or '"orders":24' in _text(events)


def test_cassettes_carry_no_secrets() -> None:
    for path in CASSETTES.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        low = text.lower()
        for word in ("authorization", "x-amz-security-token", "x-amz-date", "cookie"):
            assert word not in low, f"{path.name}: {word}"
        assert not re.search(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b", text), path.name
        assert not re.search(r"Bearer\s+\S", text), path.name


# The recording itself, against local stand-ins (no cassette in the repository)


def _event(kind: str, payload: dict[str, Any]) -> bytes:
    """One message of the AWS event stream (``application/vnd.amazon.eventstream``).

    Returns:
        Its bytes: prelude with its CRC, string headers, the JSON payload, the message CRC.
    """
    headers = b""
    for name, value in (
        (":event-type", kind),
        (":content-type", "application/json"),
        (":message-type", "event"),
    ):
        n, v = name.encode(), value.encode()
        headers += bytes([len(n)]) + n + bytes([7]) + struct.pack(">H", len(v)) + v
    body = json.dumps(payload).encode()
    prelude = struct.pack(">II", 16 + len(headers) + len(body), len(headers))
    prelude += struct.pack(">I", binascii.crc32(prelude))
    message = prelude + headers + body
    return message + struct.pack(">I", binascii.crc32(message))


def _converse_stream(after_tool: bool) -> bytes:
    usage = {"usage": {"inputTokens": 40, "outputTokens": 9, "totalTokens": 49}}
    if after_tool:
        blocks = [
            _event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "24 in Oct."}}),
            _event("contentBlockStop", {"contentBlockIndex": 0}),
            _event("messageStop", {"stopReason": "end_turn"}),
        ]
    else:
        tool = {"toolUseId": "tooluse_local", "name": "orders_month"}
        args = {"toolUse": {"input": json.dumps({"period": "2026-10"})}}
        blocks = [
            _event("contentBlockStart", {"contentBlockIndex": 0, "start": {"toolUse": tool}}),
            _event("contentBlockDelta", {"contentBlockIndex": 0, "delta": args}),
            _event("contentBlockStop", {"contentBlockIndex": 0}),
            _event("messageStop", {"stopReason": "tool_use"}),
        ]
    start = _event("messageStart", {"role": "assistant"})
    return start + b"".join(blocks) + _event("metadata", {**usage, "metrics": {"latencyMs": 5}})


@contextlib.contextmanager
def _stand_ins() -> Generator[str]:
    """A local Bedrock Runtime (ConverseStream) and the fake MCP server, over real HTTP.

    Yields:
        Their base URL.
    """
    mcp = FakeMcp()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            if self.path.startswith("/model/"):
                status, ctype = 200, "application/vnd.amazon.eventstream"
                # A tool call for a question about 2026-10, then (or otherwise) an answer.
                payload = _converse_stream(b"toolResult" in body or b"2026-10" not in body)
            else:
                res = mcp.handler(
                    httpx.Request("POST", self.path, headers=dict(self.headers), content=body)
                )
                status, ctype, payload = res.status_code, "application/json", res.content
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # ruff: ignore[unused-method-argument]
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


async def _local_turn(tmp_path: Path, base: str) -> list[dict[str, Any]]:
    """A turn on Bedrock and MCP stand-ins at ``base``, with a signed (throw-away) AWS client.

    Returns:
        The turn's events.
    """
    client = boto3.client(
        "bedrock-runtime",
        region_name="eu-north-1",
        endpoint_url=base,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        aws_session_token="local-session-token",
    )

    def models(name: str) -> Any:
        from pydantic_ai.models.bedrock import BedrockConverseModel
        from pydantic_ai.providers.bedrock import BedrockProvider

        return BedrockConverseModel(bare(name), provider=BedrockProvider(bedrock_client=client))

    yaml = _yaml(HAIKU, mcp_url=f"{base}/mcp")
    async with (
        httpx.AsyncClient() as http,
        make_env(tmp_path, agents_yaml=yaml, http=http, models=models) as env,
    ):
        tok = await env.token()
        return await env.chat(tok["token"], text="How many orders in 2026-10?")


async def test_a_cassette_records_and_replays_botocore_event_streams_and_httpx(
    tmp_path: Path,
) -> None:
    path = tmp_path / "local.yaml"
    with _stand_ins() as base:
        with vcr.VCR(**VCR_CONFIG, record_mode="once").use_cassette(str(path)):
            recorded = await _local_turn(tmp_path / "record", base)
    text = path.read_text(encoding="utf-8")
    assert "converse-stream" in text and "tools/call" in text
    # The signed headers, the session token and the forwarded credential are not kept.
    for secret in ("Authorization", "X-Amz-", "local-session-token", "user-token", "Credential="):
        assert secret not in text, secret

    # The stand-ins are gone: the replay answers from the cassette alone.
    with vcr.VCR(**VCR_CONFIG, record_mode="none").use_cassette(str(path)) as tape:
        replayed = await _local_turn(tmp_path / "replay", base)
        assert tape.all_played
    for events in (recorded, replayed):
        _assert_answered(events)
        assert _tools(events) == [("orders_month", "start"), ("orders_month", "done")]
        assert _text(events) == "24 in Oct."
