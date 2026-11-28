"""The gateway proxy's end-to-end harness: a test CA, a local fake upstream on TLS, the proxy.

The fake upstream answers for every catalogue host it is asked (the proxy's ``overrides`` send
them all to it) with a certificate from its own test CA, which the proxy trusts instead of the
public roots. PCs are httpx and websockets clients that trust the proxy's test root.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import gzip
import json
import socket
import ssl
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from evokai.gateway.proxy import ca
from tests import proxy_fixtures as fx

UPSTREAM_HOSTS = (
    "api.anthropic.com",
    "claude.ai",
    "bridge.claudeusercontent.com",
    "claude.com",
    "platform.claude.com",
    "chatgpt.com",
    "api.openai.com",
    "api.githubcopilot.com",
    "api.business.githubcopilot.com",
    "copilot-proxy.githubusercontent.com",
    "api.github.com",
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class TestCa:
    root: Path
    key: str
    cert: str
    dir: Path


def proxy_ca(tmp: Path) -> TestCa:
    """A root and a constrained intermediate, as ``evokai proxy init-ca`` writes them."""
    files = ca.init(tmp / "ca")
    return TestCa(
        root=files["root_cert"],
        key=files["key"].read_text(),
        cert=files["cert"].read_text(),
        dir=tmp / "ca",
    )


def upstream_cert(tmp: Path) -> tuple[Path, Path, Path]:
    """The fake upstream's own CA and leaf (for every catalogue host it plays)."""
    now = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake upstream CA")])
    ca_cert = (
        x509
        .CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509
        .CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "api.anthropic.com")]))
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in UPSTREAM_HOSTS]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    d = tmp / "upstream"
    d.mkdir(parents=True, exist_ok=True)
    (d / "ca.pem").write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    (d / "leaf.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (d / "leaf.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return d / "ca.pem", d / "leaf.pem", d / "leaf.key"


@dataclass
class Seen:
    """One request the fake upstream received."""

    method: str
    host: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class FakeUpstream:
    """The providers, played locally: records what reaches them."""

    seen: list[Seen] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=fx.oauth_profile)
    account: dict[str, Any] = field(default_factory=fx.claude_account)
    delay: float = 0.0
    """Seconds between SSE events."""
    stream_events: int = 0
    """Events of the never-ending Cowork stream sent so far."""

    def app(self) -> Starlette:
        async def record(request: Request) -> bytes:
            body = await request.body()
            if request.headers.get("content-encoding") == "gzip":
                body = gzip.decompress(body)
            self.seen.append(
                Seen(
                    request.method,
                    request.headers.get("host", "").split(":")[0],
                    request.url.path,
                    dict(request.headers),
                    body,
                )
            )
            return body

        async def messages(request: Request) -> Response:
            await record(request)

            async def gen() -> AsyncIterator[bytes]:
                for c in fx.ANTHROPIC_SSE:
                    yield c
                    if self.delay:
                        await asyncio.sleep(self.delay)

            return StreamingResponse(gen(), media_type="text/event-stream")

        async def count(request: Request) -> Response:
            await record(request)
            return JSONResponse({"input_tokens": 42})

        async def profile(request: Request) -> Response:
            await record(request)
            return JSONResponse(self.profile)

        async def account(request: Request) -> Response:
            await record(request)
            return JSONResponse(self.account)

        async def token(request: Request) -> Response:
            await record(request)
            return JSONResponse(fx.token_answer())

        async def completion(request: Request) -> Response:
            await record(request)
            body = gzip.compress(b"".join(fx.CHAT_SSE))
            return Response(
                body,
                media_type="text/event-stream",
                headers={"content-encoding": "gzip"},
            )

        async def events(request: Request) -> Response:
            await record(request)
            return JSONResponse({"ok": True})

        async def stream(request: Request) -> Response:
            await record(request)

            async def gen() -> AsyncIterator[bytes]:
                n = 0
                while True:
                    n += 1
                    self.stream_events = n
                    data = {"event_id": str(n), "sequence_num": n, "event_type": "assistant"}
                    yield f"event: client_event\ndata: {json.dumps(data)}\n\n".encode()
                    await asyncio.sleep(0.05)

            return StreamingResponse(gen(), media_type="text/event-stream")

        async def other(request: Request) -> Response:
            await record(request)
            return JSONResponse({"ok": True})

        async def bridge(ws: WebSocket) -> None:
            await ws.accept()
            with contextlib.suppress(Exception):
                while True:
                    text = await ws.receive_text()
                    self.frames.append(text)
                    await ws.send_text(text)

        async def codex_socket(ws: WebSocket) -> None:
            # Codex's Responses WebSocket: a warm-up gets an empty answer, a request the events.
            await ws.accept()
            with contextlib.suppress(Exception):
                while True:
                    text = await ws.receive_text()
                    self.frames.append(text)
                    if json.loads(text).get("generate") is False:
                        done = {"type": "response.completed", "response": {"id": "resp_warm"}}
                        await ws.send_text(json.dumps(done))
                        continue
                    for event in fx.responses_events():
                        await ws.send_text(json.dumps(event))

        async def responses(request: Request) -> Response:
            await record(request)
            return Response(fx.responses_sse())  # no content type, as ChatGPT's backend

        async def chat_completions(request: Request) -> Response:
            await record(request)
            return Response(b"".join(fx.COPILOT_CHAT_SSE), media_type="text/event-stream")

        async def completions(request: Request) -> Response:
            await record(request)
            chunk = {"object": "text_completion", "choices": [{"text": "x = 1", "index": 0}]}
            body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            return Response(body, media_type="text/event-stream")

        async def github_answer(request: Request) -> Response:
            await record(request)
            path = request.url.path
            if path == "/user":
                return JSONResponse(fx.github_user())
            if path == "/copilot_internal/user":
                return JSONResponse(fx.copilot_user())
            return JSONResponse(fx.copilot_token_answer())

        return Starlette(
            routes=[
                WebSocketRoute("/backend-api/codex/responses", codex_socket),
                Route("/backend-api/codex/responses", responses, methods=["POST"]),
                Route("/v1/responses", responses, methods=["POST"]),
                Route("/chat/completions", chat_completions, methods=["POST"]),
                Route("/v1/engines/{engine}/completions", completions, methods=["POST"]),
                Route("/user", github_answer, methods=["GET"]),
                Route("/copilot_internal/user", github_answer, methods=["GET"]),
                Route("/copilot_internal/v2/token", github_answer, methods=["GET"]),
                Route("/v1/messages", messages, methods=["POST"]),
                Route("/v1/messages/count_tokens", count, methods=["POST"]),
                Route("/api/oauth/profile", profile, methods=["GET"]),
                Route("/api/account", account, methods=["GET"]),
                Route("/v1/oauth/token", token, methods=["POST"]),
                Route(
                    "/api/organizations/{org}/chat_conversations/{conv}/completion",
                    completion,
                    methods=["POST"],
                ),
                Route("/v1/code/sessions/{sid}/events", events, methods=["POST"]),
                Route("/v1/code/sessions/{sid}/events/stream", stream, methods=["GET"]),
                WebSocketRoute("/bridge", bridge),
                Route("/{path:path}", other, methods=["GET", "POST", "PUT", "PATCH"]),
            ]
        )


@contextlib.asynccontextmanager
async def serve_upstream(tmp: Path, up: FakeUpstream) -> AsyncGenerator[tuple[int, Path]]:
    """Serve the fake upstream on TLS.

    Yields:
        (port, its CA's PEM file).
    """
    ca_pem, leaf, key = upstream_cert(tmp)
    port = free_port()
    config = uvicorn.Config(
        up.app(),
        host="127.0.0.1",
        port=port,
        ssl_certfile=str(leaf),
        ssl_keyfile=str(key),
        log_level="warning",
        lifespan="off",
        ws="websockets",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    try:
        yield port, ca_pem
    finally:
        server.should_exit = True
        server.force_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 5)


def client_ssl(root: Path) -> ssl.SSLContext:
    """A PC that trusts the proxy's root (and nothing else)."""
    ctx = ssl.create_default_context(cafile=str(root))
    ctx.verify_flags |= ssl.VERIFY_X509_STRICT
    return ctx


async def h2_exchange(
    proxy_port: int,
    ctx: ssl.SSLContext,
    host: str,
    requests: list[tuple[str, str, list[tuple[str, str]], bytes]],
) -> list[tuple[int, dict[str, str], bytes]]:
    """Requests over HTTP/2 inside a CONNECT tunnel, as Electron sends them (cookies split).

    Args:
        proxy_port: The proxy's port.
        ctx: The PC's TLS context.
        host: The host to tunnel to.
        requests: (method, path, headers, body), sent one after the other on one connection.

    Returns:
        (status, headers, body) of each.
    """
    import h2.config
    import h2.connection
    import h2.events

    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
    await writer.drain()
    head = await reader.readuntil(b"\r\n\r\n")
    if b" 200 " not in head.split(b"\r\n")[0]:
        raise RuntimeError(head.decode(errors="replace"))
    ctx.set_alpn_protocols(["h2"])
    await writer.start_tls(ctx, server_hostname=host)
    conn = h2.connection.H2Connection(h2.config.H2Configuration(client_side=True))
    conn.initiate_connection()
    writer.write(conn.data_to_send())
    out = []
    for method, path, headers, body in requests:
        sid = conn.get_next_available_stream_id()
        conn.send_headers(
            sid,
            [
                (":method", method),
                (":path", path),
                (":scheme", "https"),
                (":authority", host),
                *headers,
            ],
            end_stream=not body,
        )
        if body:
            conn.send_data(sid, body, end_stream=True)
        writer.write(conn.data_to_send())
        await writer.drain()
        status, rheaders, data, done = 0, {}, b"", False
        while not done:
            chunk = await asyncio.wait_for(reader.read(65536), 10)
            if not chunk:
                raise RuntimeError("the proxy closed the connection")
            for ev in conn.receive_data(chunk):
                if isinstance(ev, h2.events.ResponseReceived) and ev.stream_id == sid:
                    rheaders = {k.decode(): v.decode() for k, v in ev.headers}
                    status = int(rheaders[":status"])
                elif isinstance(ev, h2.events.DataReceived) and ev.stream_id == sid:
                    data += ev.data
                    conn.acknowledge_received_data(ev.flow_controlled_length, sid)
                elif isinstance(ev, h2.events.StreamEnded) and ev.stream_id == sid:
                    done = True
            writer.write(conn.data_to_send())
            await writer.drain()
        out.append((status, rheaders, data))
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return out
