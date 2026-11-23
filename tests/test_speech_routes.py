"""Server speech for a session: synthesis with timings, transcription, the streaming socket."""

from __future__ import annotations

import asyncio
import base64
import io
import itertools
import json
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("pipecat")

from evokai.main import create_app
from tests.conftest import AGENTS_YAML, ORIGIN, Env, make_env

MARKUP_YAML = AGENTS_YAML.replace(
    '  allowed_origins: ["http://app.test"]\n',
    '  allowed_origins: ["http://app.test"]\n  markup:\n    cue: {}\n',
)


@pytest.fixture
async def senv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    from evokai.audio.voice import providers

    # The fake recognition engine hears nothing unless scripted: these tests script «hello».
    monkeypatch.setitem(providers.STT, "fake", lambda **kw: providers.FakeSTT(["hello"], **kw))
    async with make_env(tmp_path, agents_yaml=MARKUP_YAML) as e:
        yield e


def _app(env: Env, **settings: Any) -> Any:
    env.svc.settings = env.svc.settings.model_copy(update={"voice_enabled": True, **settings})
    return create_app(env.svc.settings, env.svc)


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://evokai")


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Origin": ORIGIN}


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.split("\n\n"):
        name = next((x[7:] for x in block.splitlines() if x.startswith("event: ")), None)
        data = next((x[6:] for x in block.splitlines() if x.startswith("data: ")), None)
        if name and data:
            out.append((name, json.loads(data)))
    return out


def _wav(seconds: float, rate: int = 16_000, loud: bool = True) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        sample = b"\x00\x10" if loud else b"\x00\x00"
        w.writeframes(sample * int(seconds * rate))
    return buf.getvalue()


async def _usage(env: Env, kind: str) -> list[Any]:
    import sqlalchemy as sa

    from evokai.db import models as m

    async with env.svc.store.db.session() as s:
        rows = (
            await s.execute(sa.select(m.UsageRecord).where(m.UsageRecord.kind == kind))
        ).scalars()
        return list(rows)


async def test_a_text_is_spoken_with_word_times(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "**Hello** world, write to ada@example.com today."},
            headers=_h(tok),
        )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    names = [n for n, _ in ev]
    assert names[0] == "start" and names[-1] == "done" and "audio" in names
    start = ev[0][1]
    assert start["engine"] == "fake" and start["format"] == "pcm" and start["sampleRate"] == 24_000
    assert start["text"] == "Hello world, write to the email address today."
    words = [d for n, d in ev if n == "word"]
    assert [w["text"] for w in words][:3] == ["Hello", "world", "write"]
    assert words[1]["at"] == start["spoken"].index("world")
    assert all(a["start"] <= b["start"] for a, b in itertools.pairwise(words))
    visemes = [d for n, d in ev if n == "viseme"]
    assert visemes and all("id" in v and "time" in v for v in visemes)
    audio = b"".join(base64.b64decode(d["data"]) for n, d in ev if n == "audio")
    done = ev[-1][1]
    assert done["durationMs"] == round(len(audio) / 2 / 24_000 * 1000)
    assert done["estimated"] is False
    (row,) = await _usage(senv, "tts")
    assert row.characters == len(start["spoken"]) and row.model == "tts:fake"


async def test_a_say_segment_is_spoken_with_its_cues_as_times(senv: Env) -> None:
    app = _app(senv)
    cue = "{" + "wave}"  # a cue the model writes, not a format field
    senv.script.add("function:primary", [f"Hello {cue}there, it is fine."])
    tok = (await senv.token())["token"]
    events = await senv.chat(tok, text="hi", mode="voice")
    say = next(e for e in events if e["type"] == "say")
    assert say["marks"] == [{"name": "wave", "at": 6}]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/speech",
            json={"turnId": say["turnId"], "index": 0},
            headers=_h(tok),
        )
        ev = _events(r.text)
        (mark,) = [d for n, d in ev if n == "mark"]
        there = next(d for n, d in ev if n == "word" and d["text"] == "there")
        assert mark == {"name": "wave", "at": 6, "time": there["start"]}
        other = (await senv.token("u2"))["token"]
        stolen = await c.post(
            "/agent/v1/session/speech",
            json={"turnId": say["turnId"], "index": 0},
            headers=_h(other),
        )
        assert stolen.status_code == 404


async def test_speech_refusals(senv: Env) -> None:
    app = _app(senv, speech_max_chars=10)
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        long = await c.post("/agent/v1/session/speech", json={"text": "x" * 11}, headers=_h(tok))
        assert (long.status_code, long.json()["error"]) == (400, "text_too_long")
        none = await c.post("/agent/v1/session/speech", json={}, headers=_h(tok))
        assert (none.status_code, none.json()["error"]) == (400, "speech_input")
        far = await c.post(
            "/agent/v1/session/speech",
            json={"text": "hi"},
            headers={**_h(tok), "Origin": "http://evil.test"},
        )
        assert far.status_code == 403


async def test_speech_stops_at_a_reached_cap(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token(limits={"subject_daily_usd": 0.01}))["token"]
    who = (await senv.svc.auth.session(tok)).who
    await senv.svc.store.usage.record(who, [], kind="llm", model="x", cost_micro_usd=50_000)
    async with _client(app) as c:
        r = await c.post("/agent/v1/session/speech", json={"text": "hi"}, headers=_h(tok))
    assert (r.status_code, r.json()["error"]) == (429, "limit")
    assert "$" not in r.json()["message"]


async def test_opus_output_is_ogg(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Hello there, this is a longer sentence.", "format": "opus"},
            headers=_h(tok),
        )
    ev = _events(r.text)
    assert ev[0][1]["format"] == "opus" and ev[0][1]["sampleRate"] == 48_000
    audio = b"".join(base64.b64decode(d["data"]) for n, d in ev if n == "audio")
    assert audio[:4] == b"OggS"


async def test_a_recording_is_transcribed(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/transcribe",
            content=_wav(0.5),
            headers={**_h(tok), "Content-Type": "audio/wav"},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"text": "hello", "durationMs": 500, "engine": "fake"}
    (row,) = await _usage(senv, "stt")
    assert row.audio_ms == 500 and row.model == "stt:fake"


async def test_a_recording_over_the_session_body_limit_is_transcribed(senv: Env) -> None:
    # 70 s of 16 kHz mono WAV is 2.24 MB: over the session API's 2 MB, under
    # EVOKAI_TRANSCRIBE_MAX_BYTES (10 MB). The door applies the setting, not the session limit.
    app = _app(senv)
    tok = (await senv.token())["token"]
    body = _wav(70)
    limit = senv.svc.settings.transcribe_max_bytes
    assert 2_000_000 < len(body) < limit
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/transcribe",
            content=body,
            headers={**_h(tok), "Content-Type": "audio/wav"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["durationMs"] == 70_000
        over = await c.post(
            "/agent/v1/session/transcribe",
            content=b"\x00" * (limit + 1),
            headers={**_h(tok), "Content-Type": "audio/wav"},
        )
        assert over.status_code == 413


async def test_speech_and_tool_results_take_small_bodies(senv: Env) -> None:
    # Both carry a bounded JSON body (a text of at most 20,000 characters, a result of 32 KB):
    # anything far larger is refused at the door, before it is parsed.
    from evokai.api.hardening import SMALL_BODY_LIMIT

    app = _app(senv)
    tok = (await senv.token())["token"]
    h = {**_h(tok), "Content-Type": "application/json"}
    big = "x" * (SMALL_BODY_LIMIT + 1)
    async with _client(app) as c:
        for path in ("/agent/v1/session/speech", "/agent/v1/session/tool-results"):
            r = await c.post(path, content=f'{{"text": "{big}"}}', headers=h)
            assert (r.status_code, r.json()["error"]) == (413, "too_large"), path
        # A text at the route's own maximum, every character escaped (6 bytes), still reaches it.
        text = "\\u00e8" * 20_000
        r = await c.post("/agent/v1/session/speech", content=f'{{"text": "{text}"}}', headers=h)
        assert r.status_code != 413, r.text


async def test_transcription_refusals(senv: Env) -> None:
    app = _app(senv, transcribe_max_bytes=40_000, transcribe_max_seconds=1)
    tok = (await senv.token())["token"]
    h = {**_h(tok), "Content-Type": "audio/wav"}
    async with _client(app) as c:
        big = await c.post("/agent/v1/session/transcribe", content=b"\x00" * 50_000, headers=h)
        assert (big.status_code, big.json()["error"]) == (413, "audio_too_large")
        bad = await c.post("/agent/v1/session/transcribe", content=b"not audio", headers=h)
        assert (bad.status_code, bad.json()["error"]) == (400, "audio_invalid")
        long = await c.post("/agent/v1/session/transcribe", content=_wav(1.2, 8_000), headers=h)
        assert (long.status_code, long.json()["error"]) == (400, "audio_too_long")


async def test_engines_are_chosen_by_the_token_within_the_grants(senv: Env) -> None:
    _app(senv, voice_tts_engines=["openai"], voice_stt_engines=["whisper"])
    r = await senv.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper", "speech": {"tts": "nope"}},
        headers={"Authorization": f"Bearer {senv.server_key}"},
    )
    assert (r.status_code, r.json()["error"]) == (400, "speech_engine")
    tok = await senv.token(speech={"tts": "openai", "stt": "whisper"})
    assert tok["speech"] == {"tts": "openai", "stt": "whisper"}
    claims = (await senv.svc.auth.session(tok["token"])).claims
    assert (claims.speech_tts, claims.speech_stt) == ("openai", "whisper")
    info = await senv.client.get("/agent/v1/session", headers=_h(tok["token"]))
    assert info.json()["speech"] == {"tts": "openai", "stt": "whisper"}
    admin = {"Authorization": f"Bearer {senv.admin_key}"}
    change = {"changes": [{"kind": "stt", "name": "whisper", "value": False}]}
    assert (
        await senv.client.patch("/agent/v1/admin/grants", json=change, headers=admin)
    ).status_code == 200
    r = await senv.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper", "speech": {"stt": "whisper"}},
        headers={"Authorization": f"Bearer {senv.server_key}"},
    )
    assert (r.status_code, r.json()["error"]) == (403, "stt_not_granted")


# ── the streaming socket, driven over ASGI ──────────────────────────────────────────────────────


class Socket:
    """A WebSocket client over an ASGI app, without a network."""

    def __init__(
        self, app: Any, path: str, subprotocols: list[str], headers: dict[str, str], query: str = ""
    ) -> None:
        self.app, self.inbox, self.outbox = app, asyncio.Queue(), asyncio.Queue()
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]
            + [(b"host", b"evokai"), (b"sec-websocket-protocol", ", ".join(subprotocols).encode())],
            "subprotocols": subprotocols,
            "server": ("evokai", 80),
            "client": ("127.0.0.1", 1234),
        }
        self.task: asyncio.Task | None = None

    async def open(self) -> dict[str, Any]:
        async def receive() -> dict[str, Any]:
            return await self.inbox.get()

        async def send(message: dict[str, Any]) -> None:
            await self.outbox.put(message)

        self.task = asyncio.create_task(self.app(self.scope, receive, send))
        await self.inbox.put({"type": "websocket.connect"})
        return await asyncio.wait_for(self.outbox.get(), 10)

    async def refused(self) -> int:
        """Open, expecting the server to accept and close at once (so browsers see the code).

        Returns:
            The close code.
        """
        assert (await self.open())["type"] == "websocket.accept"
        _, closed = await self.until_close()
        return closed["code"]

    async def send_json(self, data: dict[str, Any]) -> None:
        await self.inbox.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def send_bytes(self, data: bytes) -> None:
        await self.inbox.put({"type": "websocket.receive", "bytes": data})

    async def receive(self) -> dict[str, Any]:
        return await asyncio.wait_for(self.outbox.get(), 20)

    async def until_close(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        got = []
        while True:
            m = await self.receive()
            if m["type"] == "websocket.close":
                return got, m
            if m["type"] == "websocket.send" and m.get("text"):
                got.append(json.loads(m["text"]))


async def test_the_stream_transcribes_as_it_listens(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    ws = Socket(
        app,
        "/agent/v1/session/transcribe/stream",
        ["evokai.v1", f"evokai.token.{tok}"],
        {"origin": ORIGIN},
    )
    accepted = await ws.open()
    assert accepted == {"type": "websocket.accept", "subprotocol": "evokai.v1", "headers": []}
    await ws.send_json({"type": "start", "sampleRate": 16_000, "vad": {"stopSecs": 0.3}})
    ready = await ws.receive()
    assert json.loads(ready["text"])["type"] == "ready"
    await ws.send_bytes(b"\x00\x10" * 16_000)
    await ws.send_json({"type": "stop"})
    got, closed = await ws.until_close()
    assert {"type": "final", "text": "hello"} in got
    assert got[-1]["type"] == "end" and got[-1]["durationMs"] == 1000
    assert closed["code"] == 1000
    (row,) = await _usage(senv, "stt")
    assert row.audio_ms == 1000


async def test_the_stream_refuses_tokens_in_the_url_foreign_origins_and_a_second_socket(
    senv: Env,
) -> None:
    app = _app(senv, transcribe_stream_max_seconds=1)
    tok = (await senv.token())["token"]
    path = "/agent/v1/session/transcribe/stream"
    protocols = ["evokai.v1", f"evokai.token.{tok}"]
    in_url = Socket(app, path, ["evokai.v1"], {"origin": ORIGIN}, query=f"token={tok}")
    assert await in_url.refused() == 4400
    foreign = Socket(app, path, protocols, {"origin": "http://evil.test"})
    assert await foreign.refused() == 4403
    nobody = Socket(app, path, ["evokai.v1"], {"origin": ORIGIN})
    assert await nobody.refused() == 4401
    first = Socket(app, path, protocols, {"origin": ORIGIN})
    assert (await first.open())["type"] == "websocket.accept"
    second = Socket(app, path, protocols, {"origin": ORIGIN})
    assert await second.refused() == 4409
    await first.send_json({"type": "start", "sampleRate": 16_000})
    await first.receive()
    await first.send_bytes(b"\x00\x10" * 40_000)  # 1.25 s: over the cap
    got, closed = await first.until_close()
    assert closed["code"] == 4408 and got[-1]["code"] == "too_long"


async def test_the_webrtc_voice_uses_the_session_voice(senv: Env) -> None:
    from evokai.audio.voice.pipeline import VoiceServer

    server = VoiceServer(senv.svc)
    try:
        chosen = await senv.svc.auth.session((await senv.token(voice="elena"))["token"])
        assert await server._voice(chosen, None) == "elena"
        default = await senv.svc.auth.session((await senv.token())["token"])
        assert await server._voice(default, None) is None
    finally:
        await server.close()


async def test_engines_that_perform_speech_tags_get_the_tagged_form(senv: Env) -> None:
    from types import SimpleNamespace

    from pipecat.frames.frames import TTSSpeakFrame

    from evokai.audio.voice.agent import EvokaiAgentProcessor

    principal = await senv.svc.auth.session((await senv.token())["token"])
    sent: list[str] = []

    async def push(frame: Any, *_a: Any, **_k: Any) -> None:  # ruff: ignore[unused-async] (replaces an async method)
        if isinstance(frame, TTSSpeakFrame):
            sent.append(frame.text)

    for tags, expected in ((True, "[laughs] Hi."), (False, "Hi.")):
        agent = EvokaiAgentProcessor(engine=senv.svc.engine, principal=principal, speech_tags=tags)
        agent.push_frame = push  # type: ignore[method-assign]
        run: Any = SimpleNamespace(turn_id="t1")
        say = {"type": "say", "index": 0, "text": "Hi.", "tagged": "[laughs] Hi.", "turnId": "t1"}
        await agent._deliver(run, say)
        await agent._deliver(
            run, {**say, "index": 1, "text": "", "marks": [{"name": "x", "at": 0}]}
        )
        assert sent.pop() == expected and not sent


async def test_the_stream_from_the_service_own_origin_is_accepted(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    own = Socket(
        app, "/agent/v1/session/transcribe/stream", ["evokai.v1", f"evokai.token.{tok}"],
        {"origin": "http://evokai"},
    )  # fmt: skip
    assert (await own.open())["type"] == "websocket.accept"
    await own.send_json({"type": "start", "sampleRate": 16_000})
    assert json.loads((await own.receive())["text"])["type"] == "ready"
    await own.send_json({"type": "stop"})
    await own.until_close()


async def test_the_stream_says_why_it_stops_a_reached_cap_or_silence(senv: Env) -> None:
    app = _app(senv, transcribe_stream_idle_seconds=0.3)
    path = "/agent/v1/session/transcribe/stream"
    tok = (await senv.token(limits={"subject_daily_usd": 0.01}))["token"]
    who = (await senv.svc.auth.session(tok)).who
    await senv.svc.store.usage.record(who, [], kind="llm", model="x", cost_micro_usd=50_000)
    capped = Socket(app, path, ["evokai.v1", f"evokai.token.{tok}"], {"origin": ORIGIN})
    await capped.open()
    await capped.send_json({"type": "start", "sampleRate": 16_000})
    got, closed = await capped.until_close()
    assert got[-1]["type"] == "error" and got[-1]["code"] == "limit" and closed["code"] == 4429
    other = (await senv.token("u2"))["token"]
    idle = Socket(app, path, ["evokai.v1", f"evokai.token.{other}"], {"origin": ORIGIN})
    await idle.open()
    await idle.send_json({"type": "start", "sampleRate": 16_000})
    got, closed = await idle.until_close()
    assert got[-1]["code"] == "idle" and closed["code"] == 4408


async def test_a_long_recording_is_refused_before_it_is_all_decoded(senv: Env) -> None:
    from evokai.audio.voice.oneshot import OpusEncoder

    app = _app(senv, transcribe_max_seconds=1)
    enc = OpusEncoder(16_000)
    opus = enc.push(b"\x00\x10" * 16_000 * 3) + enc.close()
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/transcribe",
            content=opus,
            headers={**_h(tok), "Content-Type": "audio/ogg"},
        )
    assert (r.status_code, r.json()["error"]) == (400, "audio_too_long")
