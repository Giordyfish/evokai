"""Server speech per voice: catalogue settings, language, continuity, character times.

The fake engine covers the routes; ElevenLabs is driven through its Pipecat service against an
in-memory HTTP API (no network), which also pins the hooks Evokai puts on that service, so a Pipecat
upgrade that moves them fails here.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("pipecat")

from evokai.audio.speech.continuity import SpeechMemory
from evokai.audio.speech.voice_settings import check_voice_settings
from evokai.audio.voice import providers
from evokai.audio.voice.oneshot import CharTimes, spoken_offsets, synthesize
from evokai.audio.voice.providers import make_tts, tts_caps, voice_model
from evokai.main import create_app
from tests.conftest import AGENTS_YAML, ORIGIN, Env, make_env

SPEECH_YAML = AGENTS_YAML.replace(
    '  allowed_origins: ["http://app.test"]\n',
    '  allowed_origins: ["http://app.test"]\n  markup:\n    cue: {}\n    speech: {}\n',
).replace("    voice:\n", "    voice:\n      voice_id: elena\n")


# ── an in-memory ElevenLabs HTTP API ────────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status: int, headers: dict[str, str], lines: list[bytes]) -> None:
        self.status, self.headers, self._lines = status, headers, lines

    @property
    def content(self) -> AsyncIterator[bytes]:
        return _lines(self._lines)

    async def text(self) -> str:
        return "refused"


async def _lines(lines: list[bytes]) -> AsyncIterator[bytes]:
    for line in lines:
        await asyncio.sleep(0)
        yield line


class _Request:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeElevenLabs:
    """Answers each synthesis with its text's characters, 50 ms each, in two pieces of alignment."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def post(self, url: str, *, json: dict[str, Any], **kwargs: Any) -> _Request:
        self.requests.append({"url": url, "json": json, **kwargs})
        chars = list(json["text"])
        half = len(chars) // 2
        lines = []
        for a, b in ((0, half), (half, len(chars))):
            piece = {
                "audio_base64": base64.b64encode(b"\x00\x00" * 1200).decode(),
                "alignment": {
                    "characters": chars[a:b],
                    "character_start_times_seconds": [i * 0.05 for i in range(a, b)],
                    "character_end_times_seconds": [(i + 1) * 0.05 for i in range(a, b)],
                },
            }
            lines.append((_dumps(piece) + "\n").encode())
        headers = {"request-id": f"el-{len(self.requests)}"}
        return _Request(_Response(200, headers, lines))

    async def close(self) -> None:
        self.closed = True


def _dumps(x: Any) -> str:
    return json.dumps(x)


@pytest.fixture
def eleven(monkeypatch: pytest.MonkeyPatch) -> FakeElevenLabs:
    api = FakeElevenLabs()
    monkeypatch.setattr(providers, "_http", lambda: api)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.delenv("EVOKAI_ELEVENLABS_TTS_MODEL", raising=False)
    return api


async def _speak(service: Any, text: str) -> list[Any]:
    return [p async for p in synthesize(service, text, sample_rate=24_000, characters=True)]


# ── the ElevenLabs adapter (pins the Pipecat hooks) ─────────────────────────────────────────────


def test_the_pipecat_elevenlabs_hooks_are_where_evokai_expects_them() -> None:
    """Pinned on pipecat 1.16.0: continuity and character times hook into
    ``ElevenLabsHttpTTSService.run_tts``. If this fails after a pipecat upgrade, review
    ``providers._elevenlabs_service`` and ``_ElevenLabsSession`` against the new ``run_tts``
    before changing this test."""
    import inspect

    from pipecat.services.elevenlabs import tts as eleven_tts

    service = eleven_tts.ElevenLabsHttpTTSService
    source = inspect.getsource(inspect.unwrap(service.run_tts))
    expected = {
        # The request goes through the session Evokai hands in, as one JSON body it can extend.
        "self._session.post(": "the request is no longer sent with the given aiohttp session",
        "json=payload": "the request body is no longer a JSON payload Evokai can add to",
        # Every piece of alignment is handed to the method Evokai overrides.
        "self.calculate_word_times(": "the alignment no longer reaches calculate_word_times",
        "self._cumulative_time": "the alignment times are no longer offset by _cumulative_time",
    }
    missing = [why for needle, why in expected.items() if needle not in source]
    assert not missing, f"pipecat's ElevenLabsHttpTTSService.run_tts changed: {missing}"
    params = list(inspect.signature(service.calculate_word_times).parameters)
    assert params == ["self", "alignment_info", "audio_tags"], params
    assert "eleven_v3" in eleven_tts.ELEVENLABS_CONTEXT_UNSUPPORTED_MODELS
    assert "eleven_flash_v2_5" in eleven_tts.ELEVENLABS_MODEL_LANGUAGES


async def test_elevenlabs_applies_the_voice_settings_and_the_language(
    eleven: FakeElevenLabs,
) -> None:
    settings = {
        "model": "eleven_turbo_v2_5",
        "speed": 1.1,
        "elevenlabs": {
            "stability": 0.4,
            "similarityBoost": 0.8,
            "style": 0.1,
            "speakerBoost": True,
        },
    }
    tts = make_tts(
        "elevenlabs", voice="v1", sample_rate=24_000, language="it-IT", settings=settings
    )
    pieces = await _speak(tts, "Ciao a tutti.")
    assert not [p for p in pieces if p.kind == "error"]
    (req,) = eleven.requests
    assert req["url"].endswith("/v1/text-to-speech/v1/stream/with-timestamps")
    body = req["json"]
    assert body["model_id"] == "eleven_turbo_v2_5" and body["language_code"] == "it"
    assert body["voice_settings"] == {
        "stability": 0.4, "similarity_boost": 0.8, "style": 0.1, "use_speaker_boost": True,
        "speed": 1.1,
    }  # fmt: skip
    assert "previous_request_ids" not in body and "previous_text" not in body
    assert eleven.closed


async def test_elevenlabs_without_settings_speaks_as_before(eleven: FakeElevenLabs) -> None:
    tts = make_tts("elevenlabs", voice="v1", sample_rate=24_000)
    await _speak(tts, "Hello.")
    body = eleven.requests[0]["json"]
    assert body == {"text": "Hello.", "model_id": "eleven_flash_v2_5"}


async def test_elevenlabs_follows_earlier_syntheses_and_keeps_its_reference(
    eleven: FakeElevenLabs,
) -> None:
    tts: Any = make_tts("elevenlabs", voice="v1", sample_rate=24_000)
    tts.follow(["a", "b", "c", "d"], "Before.", "After.")
    await _speak(tts, "Now.")
    body = eleven.requests[0]["json"]
    assert body["previous_request_ids"] == ["b", "c", "d"] and body["next_text"] == "After."
    assert "previous_text" not in body
    assert tts.speech_ref == "el-1"

    by_text: Any = make_tts("elevenlabs", voice="v1", sample_rate=24_000)
    by_text.follow([], "x" * 5_000, "")
    await _speak(by_text, "Now.")
    body = eleven.requests[1]["json"]
    assert body["previous_text"] == "x" * providers.CONTEXT_CHARS and "next_text" not in body

    # Models that refuse the text around (eleven_v3) get none of it.
    v3: Any = make_tts(
        "elevenlabs", voice="v1", sample_rate=24_000, settings={"model": "eleven_v3"}
    )
    v3.follow(["a"], "Before.", "After.")
    await _speak(v3, "[laughs] Now.")
    body = eleven.requests[2]["json"]
    assert not {"previous_request_ids", "previous_text", "next_text"} & set(body)


@pytest.mark.usefixtures("eleven")
async def test_elevenlabs_reports_character_times() -> None:
    tts = make_tts("elevenlabs", voice="v1", sample_rate=24_000)
    pieces = await _speak(tts, "Hi there")
    chars = [p for p in pieces if p.kind == "chars"]
    assert len(chars) == 2
    assert "".join(c for p in chars for c in p.chars) == "Hi there"
    starts = [round(t) for p in chars for t in p.starts]
    ends = [round(t) for p in chars for t in p.ends]
    assert starts == [i * 50 for i in range(8)] and ends == [(i + 1) * 50 for i in range(8)]
    words = [p.word for p in pieces if p.kind == "word"]
    assert words == ["Hi", "there"]


# ── settings, caps, character mapping ───────────────────────────────────────────────────────────


def test_voice_settings_are_checked_per_engine() -> None:
    assert check_voice_settings("elevenlabs", None) is None
    assert check_voice_settings("openai", {"model": "gpt-4o-mini-tts", "speed": 1.5}) == {
        "model": "gpt-4o-mini-tts",
        "speed": 1.5,
    }
    ok = {"model": "eleven_v3", "elevenlabs": {"stability": 0.5, "style": None}}
    assert check_voice_settings("elevenlabs", ok) == {
        "model": "eleven_v3",
        "elevenlabs": {"stability": 0.5},
    }
    for engine, bad, why in [
        ("elevenlabs", {"stability": 0.5}, "does not take stability"),
        ("elevenlabs", {"openai": {"instructions": "x"}}, "does not take openai"),
        ("elevenlabs", {"elevenlabs": {"pitch": 1}}, "elevenlabs.pitch"),
        ("elevenlabs", {"speed": 2.0}, "0.7 to 1.2"),
        ("elevenlabs", {"elevenlabs": {"stability": 2}}, "elevenlabs.stability"),
        ("openai", {"openai": {"instructions": "x" * 4_001}}, "openai.instructions"),
        ("openai", {"model": "bad model"}, "model"),
        ("azure", {"model": "x"}, "takes no voice settings"),
    ]:
        with pytest.raises(ValueError, match=why):
            check_voice_settings(engine, bad)


def test_speech_tags_follow_the_voice_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVOKAI_ELEVENLABS_TTS_MODEL", raising=False)
    assert not tts_caps("elevenlabs").speech_tags
    for model in ("eleven_v3", "eleven_v4"):
        assert tts_caps("elevenlabs", voice_model("elevenlabs", {"model": model})).speech_tags
    assert voice_model("elevenlabs", None) == "eleven_flash_v2_5"
    assert voice_model("openai", None) is None
    monkeypatch.setenv("EVOKAI_ELEVENLABS_TTS_MODEL", "eleven_v3")
    model = voice_model("elevenlabs", {"model": "eleven_flash_v2_5"})
    assert not tts_caps("elevenlabs", model).speech_tags
    assert tts_caps("elevenlabs").characters and not tts_caps("openai").characters


def test_tags_never_shift_the_spoken_offsets() -> None:
    spoken = "x yes, it is."
    tagged = "x [yes] yes, [laughs] it is."
    offsets = spoken_offsets(tagged, spoken)
    assert [spoken[o] for o in offsets if o >= 0] == list(spoken)
    assert offsets[tagged.index("[yes]")] == -1 and offsets[tagged.index("[laughs]") + 3] == -1
    times = CharTimes(tagged, spoken)
    half = len(tagged) // 2
    ms = [float(i * 10) for i in range(len(tagged))]
    first = times.add(list(tagged[:half]), ms[:half], [m + 10 for m in ms[:half]])
    second = times.add(list(tagged[half:]), ms[half:], [m + 10 for m in ms[half:]])
    assert first is not None and second is not None and first["at"] == 0
    covered = first["times"] + second["times"]
    assert second["at"] == len(first["times"]) and len(covered) == len(spoken)
    # Every spoken code point has the time of its own character in the tagged form.
    for i, (start, _) in enumerate(covered):
        assert tagged[int(start / 10)] == spoken[i]
    assert times.finish() is None


def test_character_times_fill_what_the_engine_skipped() -> None:
    times = CharTimes("abc", "abc")
    assert times.add(["a", "c"], [0, 20], [10, 30]) == {
        "at": 0,
        "times": [[0, 10], [-1, -1], [20, 30]],
    }
    times = CharTimes("abcd", "abcd")
    assert times.add(["a"], [0], [10]) == {"at": 0, "times": [[0, 10]]}
    assert times.finish() == {"at": 1, "times": [[-1, -1]] * 3}
    assert CharTimes("ab", "ab").finish() is None


def test_speech_memory_is_bounded_in_time_and_size() -> None:
    now = [0.0]
    mem = SpeechMemory(ttl_seconds=100, conversations=2, per_conversation=2, clock=lambda: now[0])
    a, b, c = ("app", "u", "c1"), ("app", "u", "c2"), ("app", "u", "c3")
    mem.remember(a, "1", "r1", "one")
    mem.remember(a, "2", "r2", "two")
    mem.remember(a, "3", "r3", "three")
    third = mem.get(a, "3")
    assert mem.get(a, "1") is None and third is not None and third.ref == "r3"
    mem.remember(b, "1", None, "x")
    mem.remember(c, "1", None, "y")
    assert len(mem) == 2 and mem.get(a, "3") is None
    assert mem.get(b, "1") is not None and mem.get(("app", "other", "c2"), "1") is None
    now[0] = 101
    assert mem.get(b, "1") is None and len(mem) == 0


# ── the route ───────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def senv(tmp_path: Path) -> AsyncIterator[Env]:
    async with make_env(tmp_path, agents_yaml=SPEECH_YAML) as e:
        yield e


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> list[providers.FakeTTS]:
    made: list[providers.FakeTTS] = []

    def factory(**kw: Any) -> providers.FakeTTS:
        made.append(providers.FakeTTS(**kw))
        return made[-1]

    monkeypatch.setitem(providers.TTS, "fake", factory)
    return made


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


async def _put_voice(env: Env, provider: str, voice: str, settings: Any) -> httpx.Response:
    return await env.client.put(
        f"/agent/v1/admin/voices/{provider}/{voice}",
        json={"language": "en-US", "name": voice.title(), "settings": settings},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )


async def test_the_catalogue_refuses_settings_its_engine_does_not_take(senv: Env) -> None:
    r = await _put_voice(senv, "elevenlabs", "elena", {"elevenlabs": {"pitch": 1}})
    assert (r.status_code, r.json()["error"]) == (400, "settings")
    assert "elevenlabs.pitch" in r.json()["message"]
    r = await _put_voice(senv, "azure", "elena", {"speed": 1})
    assert (r.status_code, r.json()["error"]) == (400, "settings")
    r = await _put_voice(senv, "elevenlabs", "elena", {"model": "eleven_v3", "speed": 1.1})
    assert r.status_code == 200, r.text
    record = await senv.svc.store.voices.get("elevenlabs", "elena")
    assert record is not None and record.settings == {"model": "eleven_v3", "speed": 1.1}
    assert await senv.svc.store.voices.get("elevenlabs", "other") is None
    listed = await senv.client.get(
        "/agent/v1/voices", headers={"Authorization": f"Bearer {senv.server_key}"}
    )
    assert "settings" not in listed.json()["items"][0]


async def test_a_catalogue_voice_speaks_with_its_settings_and_the_language(
    senv: Env, fakes: list[providers.FakeTTS]
) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        r = await c.post("/agent/v1/session/speech", json={"text": "Hello."}, headers=_h(tok))
        assert r.status_code == 200, r.text
        assert fakes[-1].voice == "elena" and fakes[-1].voice_settings == {}
        assert fakes[-1].language == "en-US"
        assert _events(r.text)[0][1]["language"] == "en-US"
        assert (await _put_voice(senv, "fake", "elena", {"model": "m2", "speed": 1.2})).is_success
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Hello.", "language": "it"},
            headers=_h(tok),
        )
        assert fakes[-1].voice_settings == {"model": "m2", "speed": 1.2}
        assert fakes[-1].language == "it"
        bad = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Hello.", "language": "not a tag"},
            headers=_h(tok),
        )
        assert (bad.status_code, bad.json()["error"]) == (400, "bad_request")


async def test_say_segments_follow_the_earlier_ones_of_their_turn(
    senv: Env, fakes: list[providers.FakeTTS]
) -> None:
    app = _app(senv)
    senv.script.add("function:primary", ["First one here. Second one there. Third one last."])
    tok = (await senv.token())["token"]
    events = await senv.chat(tok, text="hi", mode="voice")
    says = [e for e in events if e["type"] == "say" and e["text"]]
    assert len(says) == 3
    turn = says[0]["turnId"]

    async def speak(index: int) -> list[tuple[str, dict[str, Any]]]:
        r = await c.post(
            "/agent/v1/session/speech", json={"turnId": turn, "index": index}, headers=_h(tok)
        )
        assert r.status_code == 200, r.text
        return _events(r.text)

    async with _client(app) as c:
        first = await speak(0)
        assert first[0][1]["speechId"] == f"{turn}.0"
        assert fakes[-1].followed == ([], "", says[1]["text"])
        await speak(1)
        assert fakes[-1].followed == (["fake-1"], says[0]["text"], says[2]["text"])
        await speak(2)
        assert fakes[-1].followed == (
            ["fake-1", "fake-1"],
            f"{says[0]['text']} {says[1]['text']}",
            "",
        )
        # Another process (or two hours later): the text before stands in for the references.
        senv.svc.speech_memory = SpeechMemory()
        await speak(2)
        assert fakes[-1].followed == ([], f"{says[0]['text']} {says[1]['text']}", "")


async def test_a_text_follows_the_speech_it_names(
    senv: Env, fakes: list[providers.FakeTTS]
) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    other = (await senv.token("u2"))["token"]
    async with _client(app) as c:
        r = await c.post("/agent/v1/session/speech", json={"text": "One."}, headers=_h(tok))
        first = _events(r.text)[0][1]["speechId"]
        assert first.startswith("sp_") and fakes[-1].followed is None
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Two.", "after": [first, "unknown"]},
            headers=_h(tok),
        )
        assert r.status_code == 200, r.text
        # An unknown id ends the references; the text it names still stands in for them.
        assert fakes[-1].followed == ([], "One.", "")
        r = await c.post(
            "/agent/v1/session/speech", json={"text": "Two.", "after": [first]}, headers=_h(tok)
        )
        assert fakes[-1].followed == (["fake-1"], "One.", "")
        # Another person's conversation never sees it.
        r = await c.post(
            "/agent/v1/session/speech", json={"text": "Two.", "after": [first]}, headers=_h(other)
        )
        assert r.status_code == 200 and fakes[-1].followed is None
        too_many = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Two.", "after": [first] * 9},
            headers=_h(tok),
        )
        assert too_many.status_code == 400


@pytest.mark.usefixtures("fakes")
async def test_character_times_cover_the_spoken_text(senv: Env) -> None:
    app = _app(senv)
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        plain = await c.post(
            "/agent/v1/session/speech", json={"text": "Hello there, my friend."}, headers=_h(tok)
        )
        assert not [n for n, _ in _events(plain.text) if n == "chars"]
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Hello there, my friend.", "characters": True},
            headers=_h(tok),
        )
    ev = _events(r.text)
    spoken = ev[0][1]["spoken"]
    assert spoken == "Hello there, my friend."
    chars = [d for n, d in ev if n == "chars"]
    assert len(chars) == 2 and chars[0]["at"] == 0
    times = [t for d in chars for t in d["times"]]
    assert len(times) == len(spoken) and all(0 <= s < e for s, e in times)
    assert all(a[0] <= b[0] for a, b in itertools.pairwise(times))
    names = [n for n, _ in ev]
    assert names.index("chars") < names.index("done")
    (row,) = list(await _tts_usage(senv))[-1:]
    assert row.characters == len(spoken)


async def _tts_usage(env: Env) -> list[Any]:
    import sqlalchemy as sa

    from evokai.db import models as m

    async with env.svc.store.db.session() as s:
        rows = (
            await s.execute(sa.select(m.UsageRecord).where(m.UsageRecord.kind == "tts"))
        ).scalars()
        return list(rows)


async def test_an_elevenlabs_voice_on_a_tag_model_speaks_tags_without_shifting_times(
    senv: Env, eleven: FakeElevenLabs
) -> None:
    app = _app(senv, voice_tts="elevenlabs")
    assert (await _put_voice(senv, "elevenlabs", "elena", {"model": "eleven_v3"})).is_success
    tok = (await senv.token())["token"]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Well [laughs] that is fine.", "characters": True, "language": "it"},
            headers=_h(tok),
        )
        assert r.status_code == 200, r.text
        ev = _events(r.text)
        start = ev[0][1]
        spoken = start["spoken"]
        assert "[" not in spoken
        body = eleven.requests[-1]["json"]
        assert body["model_id"] == "eleven_v3" and "[laughs]" in body["text"]
        assert body["language_code"] == "it" and "previous_text" not in body
        times = [t for n, d in ev if n == "chars" for t in d["times"]]
        assert len(times) == len(spoken)
        said = body["text"]
        # Each spoken code point carries the time of its own character in the tagged text.
        cursor = 0
        for i, (begin, _) in enumerate(times):
            at = round(begin / 50)
            assert said[at] == spoken[i] and at >= cursor
            cursor = at
        done = ev[-1][1]
        assert done["characters"] == len(said)
        # Retuned to a model that follows earlier speech: a reference made by another model serves
        # no other, so the text spoken before stands in for it; the next synthesis follows by
        # reference.
        assert (await _put_voice(senv, "elevenlabs", "elena", None)).is_success
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "And then.", "after": [start["speechId"]]},
            headers=_h(tok),
        )
        assert r.status_code == 200, r.text
        body = eleven.requests[-1]["json"]
        assert body["model_id"] == "eleven_flash_v2_5" and "previous_request_ids" not in body
        assert body["previous_text"] == spoken
        then = _events(r.text)[0][1]["speechId"]
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Done.", "after": [start["speechId"], then]},
            headers=_h(tok),
        )
        body = eleven.requests[-1]["json"]
        assert body["previous_request_ids"] == ["el-2"] and "previous_text" not in body


async def test_an_admin_reads_a_catalogue_voice_with_its_settings(senv: Env) -> None:
    tuned = {"model": "eleven_flash_v2_5", "speed": 1.1, "elevenlabs": {"stability": 0.4}}
    assert (await _put_voice(senv, "elevenlabs", "elena", tuned)).is_success
    admin = {"Authorization": f"Bearer {senv.admin_key}"}
    r = await senv.client.get("/agent/v1/admin/voices/elevenlabs/elena", headers=admin)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["voiceId"] == "elena" and got["name"] == "Elena"
    assert got["settings"]["model"] == "eleven_flash_v2_5"
    assert got["settings"]["elevenlabs"] == {"stability": pytest.approx(0.4)}
    r = await senv.client.get("/agent/v1/admin/voices/elevenlabs/nobody", headers=admin)
    assert r.status_code == 404


async def test_a_refused_reference_is_dropped_and_the_earlier_text_followed(
    senv: Env, eleven: FakeElevenLabs
) -> None:
    app = _app(senv, voice_tts="elevenlabs")
    tok = (await senv.token())["token"]
    post = eleven.post

    def refuse_references(url: str, *, json: dict[str, Any], **kwargs: Any) -> _Request:
        if "previous_request_ids" in json:
            eleven.requests.append({"url": url, "json": json, **kwargs})
            return _Request(_Response(400, {}, [b'{"detail": "unknown request id"}\n']))
        return post(url, json=json, **kwargs)

    async with _client(app) as c:
        r = await c.post("/agent/v1/session/speech", json={"text": "First."}, headers=_h(tok))
        first = _events(r.text)[0][1]
        eleven.post = refuse_references  # type: ignore[method-assign]
        r = await c.post(
            "/agent/v1/session/speech",
            json={"text": "Second.", "after": [first["speechId"]]},
            headers=_h(tok),
        )
        assert r.status_code == 200, r.text
        ev = _events(r.text)
        assert "error" not in [n for n, _ in ev]
        assert any(n == "audio" for n, _ in ev) and ev[-1][0] == "done"
        refused, retried = eleven.requests[-2]["json"], eleven.requests[-1]["json"]
        assert refused["previous_request_ids"] == ["el-1"]
        assert "previous_request_ids" not in retried and retried["previous_text"] == first["spoken"]
