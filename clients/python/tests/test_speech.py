from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from evokai import AsyncEvokaiSession, EvokaiError, EvokaiSession
from tests.support import BASE, TOKEN, sent_json, sse_response


def _ev(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


PCM = b"\x00\x01" * 240
SPEECH = [
    _ev(
        "start",
        {
            "engine": "fake",
            "voice": None,
            "format": "pcm",
            "sampleRate": 24000,
            "channels": 1,
            "text": "Hi there.",
            "spoken": "Hi there.",
        },
    ),
    _ev("audio", {"data": base64.b64encode(PCM[:200]).decode(), "ms": 0}),
    _ev("word", {"text": "Hi", "at": 0, "length": 2, "start": 0}),
    _ev("mark", {"name": "wave", "at": 3, "time": 120}),
    _ev("audio", {"data": base64.b64encode(PCM[200:]).decode(), "ms": 4}),
    _ev("viseme", {"id": 3, "time": 40}),
    _ev("done", {"durationMs": 10, "characters": 9, "estimated": False}),
]


def test_speak_collects_audio_and_timings(router: respx.MockRouter) -> None:
    response, _ = sse_response(SPEECH)
    route = router.post("/v1/session/speech").mock(return_value=response)
    with EvokaiSession(BASE, TOKEN) as s:
        out = s.speak(turn_id="t1", index=0)
    assert sent_json(route) == {"format": "pcm", "sampleRate": 24000, "turnId": "t1", "index": 0}
    assert out.audio == PCM and out.engine == "fake" and out.text == "Hi there."
    assert out.words == [{"text": "Hi", "at": 0, "length": 2, "start": 0}]
    assert out.marks == [{"name": "wave", "at": 3, "time": 120}]
    assert out.visemes == [{"id": 3, "time": 40}] and out.duration_ms == 10


async def test_async_speak_and_an_engine_failure(router: respx.MockRouter) -> None:
    response, _ = sse_response([SPEECH[0], _ev("error", {"code": "tts_failed", "message": "no"})])
    router.post("/v1/session/speech").mock(return_value=response)
    async with AsyncEvokaiSession(BASE, TOKEN) as s:
        with pytest.raises(EvokaiError) as err:
            await s.speak("Hi there.")
    assert err.value.code == "tts_failed"


def test_speak_needs_a_text_or_a_segment() -> None:
    with EvokaiSession(BASE, TOKEN) as s, pytest.raises(ValueError):
        s.speak()


def test_transcribe_posts_the_recording(router: respx.MockRouter) -> None:
    route = router.post("/v1/session/transcribe").mock(
        return_value=httpx.Response(
            200, json={"text": "hello", "durationMs": 500, "engine": "fake"}
        )
    )
    with EvokaiSession(BASE, TOKEN) as s:
        heard = s.transcribe(b"RIFF....", content_type="audio/wav", language="it-IT")
    assert heard.text == "hello" and heard.durationMs == 500
    request = route.calls.last.request
    assert request.headers["content-type"] == "audio/wav"
    assert request.url.params["language"] == "it-IT"
    assert request.content == b"RIFF...."


async def test_async_transcribe_refused(router: respx.MockRouter) -> None:
    router.post("/v1/session/transcribe").mock(
        return_value=httpx.Response(413, json={"error": "audio_too_large", "message": "too big"})
    )
    async with AsyncEvokaiSession(BASE, TOKEN) as s:
        with pytest.raises(EvokaiError) as err:
            await s.transcribe(b"x")
    assert err.value.status == 413


def test_a_stream_without_its_end_is_an_error(router: respx.MockRouter) -> None:
    response, _ = sse_response(SPEECH[:3])
    router.post("/v1/session/speech").mock(return_value=response)
    with EvokaiSession(BASE, TOKEN) as s, pytest.raises(EvokaiError) as err:
        s.speak("Hi there.")
    assert err.value.code == "tts_failed"
