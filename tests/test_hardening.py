"""HTTP hardening: security headers, CORS for the session API, body limits, per-token rate, the
session API's scoping, and the supply-chain scripts."""

from __future__ import annotations

import asyncio
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] (the repository's own scripts)
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

import evokai.api.deps as deps
from tests.conftest import ORIGIN, Env, parse_sse

ROOT = Path(__file__).resolve().parents[1]


def _sess(tok: dict, origin: str = ORIGIN) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['token']}", "Origin": origin}


async def test_api_responses_carry_security_headers(env: Env) -> None:
    tok = await env.token()
    r = await env.client.get("/agent/v1/session", headers=_sess(tok))
    assert r.status_code == 200
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert "default-src 'none'" in h["content-security-policy"]
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    assert "strict-transport-security" not in h
    r = await env.client.get("/agent/health", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")
    # The mint response holds a token: never cached.
    mint = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper"},
        headers={"Authorization": f"Bearer {env.server_key}"},
    )
    assert mint.headers["cache-control"] == "no-store"
    # Errors too.
    r = await env.client.get("/agent/v1/session")
    assert r.status_code == 401 and r.headers["x-content-type-options"] == "nosniff"


async def test_the_console_keeps_its_own_policy(env: Env) -> None:
    r = await env.client.get("/agent/admin/")
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" not in r.headers.get("content-security-policy", "")
    assert r.headers["referrer-policy"] == "same-origin"


async def test_cors_answers_allowed_origins_only_and_never_with_credentials(env: Env) -> None:
    pre = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization",
    }
    ok = await env.client.options("/agent/v1/session/chat", headers={**pre, "Origin": ORIGIN})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == ORIGIN
    assert "authorization" in ok.headers["access-control-allow-headers"]
    assert "access-control-allow-credentials" not in ok.headers
    bad = await env.client.options(
        "/agent/v1/session/chat", headers={**pre, "Origin": "https://evil.example"}
    )
    assert bad.status_code == 403 and "access-control-allow-origin" not in bad.headers
    tok = await env.token()
    r = await env.client.get("/agent/v1/session", headers=_sess(tok))
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "x-turn-id" in r.headers["access-control-expose-headers"]
    # Only the browser session API speaks CORS.
    srv = await env.client.options("/agent/v1/session-tokens", headers={**pre, "Origin": ORIGIN})
    assert "access-control-allow-origin" not in srv.headers
    # The origin check per application still applies.
    r = await env.client.get("/agent/v1/session", headers=_sess(tok, "https://evil.example"))
    assert r.status_code == 403 and "access-control-allow-origin" not in r.headers


async def test_a_chat_message_without_attachments_keeps_the_session_limit(env: Env) -> None:
    # The larger limit needs the attachments grant and an agent that takes files
    # (tests/test_attachments_turns.py).
    tok = await env.token()
    big = "x" * 1_900_000
    r = await env.client.post(
        "/agent/v1/session/chat",
        content=f'{{"text": "{big}"}}',
        headers={**_sess(tok), "Content-Type": "application/json"},
    )
    assert r.status_code == 400  # read by the route (the text is too long), not refused at the door
    over = "x" * 2_100_000
    r = await env.client.post(
        "/agent/v1/session/chat",
        content=f'{{"text": "{over}"}}',
        headers={**_sess(tok), "Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["error"] == "too_large"


async def test_oversized_bodies_are_refused(env: Env) -> None:
    tok = await env.token()
    big = "x" * 2_100_000
    r = await env.client.post(
        "/agent/v1/session/bug-reports",
        content=f'{{"text": "{big}"}}',
        headers={**_sess(tok), "Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["error"] == "too_large"

    async def chunks() -> AsyncIterator[bytes]:
        await asyncio.sleep(0)
        yield b'{"text": "'
        for _ in range(30):
            yield b"y" * 100_000
        yield b'"}'

    r = await env.client.post(
        "/agent/v1/session/bug-reports",
        content=chunks(),
        headers={**_sess(tok), "Content-Type": "application/json"},
    )
    assert r.status_code == 413


async def test_a_session_token_has_a_request_rate(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deps, "SESSION_REQUESTS_PER_MINUTE", 3)
    tok = await env.token()
    codes = [
        (await env.client.get("/agent/v1/session", headers=_sess(tok))).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    other = await env.token()
    assert (await env.client.get("/agent/v1/session", headers=_sess(other))).status_code == 200


async def test_a_token_cancels_only_its_own_conversation(env: Env) -> None:
    env.script.gate = asyncio.Event()
    env.script.add("function:primary", ["Sto ", "WAIT", "rispondendo"])
    mine, other = await env.token(), await env.token()

    async def start() -> list[dict]:
        r = await env.client.post(
            "/agent/v1/session/chat", json={"text": "Lungo"}, headers=_sess(mine)
        )
        return parse_sse(r.text)

    task = asyncio.create_task(start())
    running = None
    for _ in range(100):
        running = env.svc.turns.running(mine["conversationId"])
        if running:
            break
        await asyncio.sleep(0.01)
    assert running is not None
    r = await env.client.post(
        f"/agent/v1/session/turns/{running.turn_id}/cancel", headers=_sess(other)
    )
    assert r.json()["cancelled"] is False
    r = await env.client.post(
        f"/agent/v1/session/turns/{running.turn_id}/cancel", headers=_sess(mine)
    )
    assert r.json()["cancelled"] is True
    env.script.gate.set()
    await task


async def test_bug_reports_have_an_hourly_limit(env: Env) -> None:
    tok = await env.token()
    codes = []
    for _ in range(21):
        r = await env.client.post(
            "/agent/v1/session/bug-reports", json={"description": "Non va"}, headers=_sess(tok)
        )
        codes.append(r.status_code)
    assert codes[:20] == [201] * 20 and codes[20] == 429


def _bash() -> str:
    return shutil.which("bash") or "/bin/bash"


def test_signing_is_a_no_op_without_credentials() -> None:
    r = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] (fixed command)
        [
            _bash(),
            str(ROOT / "scripts/sign-image.sh"),
            "registry.example/evokai@sha256:" + "0" * 64,
        ],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0 and "skipped" in r.stdout
    tag = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] (fixed command)
        [_bash(), str(ROOT / "scripts/sign-image.sh"), "registry.example/evokai:latest"],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert tag.returncode == 2  # a tag is never signed, only a digest


def test_the_supply_chain_scripts_parse() -> None:
    for script in ("sbom.sh", "sign-image.sh"):
        r = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] (fixed command)
            [_bash(), "-n", str(ROOT / "scripts" / script)], capture_output=True, check=False
        )
        assert r.returncode == 0, r.stderr
