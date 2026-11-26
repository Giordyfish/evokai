"""Recognition: speech start and end, client commits, vocabulary hints, made-up phrases dropped."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pipecat")

from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.frames.frames import Frame, TranscriptionFrame, VADUserStoppedSpeakingFrame
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.utils.time import time_now_iso8601

from evokai.audio.speech.hallucinations import made_up, patterns
from evokai.audio.voice import providers
from evokai.i18n import known_locales
from tests.conftest import ORIGIN, Env, make_env
from tests.test_speech_routes import MARKUP_YAML, Socket, _app, _client, _h, _wav

PATH = "/agent/v1/session/transcribe/stream"
RATE = 16_000
LOUD = b"\x00\x10\x00\xf0" * (RATE // 100)  # 20 ms of a square wave
QUIET = b"\x00\x00" * (RATE // 50)  # 20 ms of silence


class Heard:
    """What the fake engines were built with."""

    kwargs: list[dict[str, Any]]

    def __init__(self) -> None:
        self.kwargs = []


class LoudVAD(VADAnalyzer):
    """Hears speech in every 10 ms that is not silence."""

    def num_frames_required(self) -> int:
        return self.sample_rate // 100

    def voice_confidence(self, buffer: bytes) -> float:
        return 1.0 if any(buffer) else 0.0


class Utterance(SegmentedSTTService):
    """A segmented engine that answers each utterance with its number."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.pop("language", None)
        kwargs.pop("hint", None)
        super().__init__(**kwargs)
        self.count = 0

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        assert audio  # a WAV of the utterance
        self.count += 1
        yield TranscriptionFrame(f"utterance {self.count}", "", time_now_iso8601(), finalized=True)


@pytest.fixture
async def renv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Env]:
    from evokai.api import speech_routes

    def loud(rate: int, params: Any) -> LoudVAD:
        return LoudVAD(sample_rate=rate, params=params)

    monkeypatch.setattr(speech_routes, "_vad_analyzer", loud)
    async with make_env(tmp_path, agents_yaml=MARKUP_YAML) as e:
        yield e


def _engine(monkeypatch: pytest.MonkeyPatch, make: Any) -> Heard:
    heard = Heard()

    def factory(**kw: Any) -> Any:
        heard.kwargs.append(kw)
        return make(**kw)

    monkeypatch.setitem(providers.STT, "fake", factory)
    return heard


async def _socket(env: Env, start: dict[str, Any], subject: str = "u1") -> Socket:
    tok = (await env.token(subject))["token"]
    ws = Socket(_app(env), PATH, ["evokai.v1", f"evokai.token.{tok}"], {"origin": ORIGIN})
    assert (await ws.open())["type"] == "websocket.accept"
    await ws.send_json({"type": "start", "sampleRate": RATE, **start})
    ready = json.loads((await ws.receive())["text"])
    assert ready["type"] == "ready", ready
    return ws


async def _audio(ws: Socket, chunk: bytes, seconds: float) -> None:
    for _ in range(round(seconds * 50)):
        await ws.send_bytes(chunk)


async def _stop(ws: Socket) -> list[dict[str, Any]]:
    await ws.send_json({"type": "stop"})
    got, closed = await ws.until_close()
    assert closed["code"] == 1000 and got[-1]["type"] == "end", got
    return got


async def _no_filter(env: Env) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"stt_filter": False},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


# ── made-up phrases ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", sorted(known_locales()))
def test_every_pack_lists_the_phrases_recognisers_make_up(code: str) -> None:
    from evokai.i18n import _load

    assert _load(code)["speech"]["hallucinations"], code
    assert patterns(code)


@pytest.mark.parametrize(
    ("text", "locale"),
    [
        ("Thanks for watching!", "en"),
        ("Thank you for watching.", "it"),  # English sign-offs come back whatever the language
        ("Sottotitoli creati dalla comunità Amara.org", "it"),
        ("Sottotitoli a cura di QTSS", "it"),
        ("Subtítulos realizados por la comunidad de Amara.org", "es"),
        ("Sous-titres réalisés par la communauté d'Amara.org", "fr"),
        ("Untertitel im Auftrag des ZDF für funk, 2017", "de"),
        ("Mulţumesc pentru vizionare!", "ro"),  # the cedilla form
        ("Tack för att du tittade!", "sv"),
        ("[Music]", "en"),
        ("(applause)", "fr"),
        ("...", "en"),
    ],
)
def test_phrases_recognisers_make_up_are_known(text: str, locale: str) -> None:
    assert made_up(text, locale)


@pytest.mark.parametrize(
    ("text", "locale"),
    [
        ("", "en"),
        ("hello", "en"),
        ("Thanks for watching the kids tonight", "en"),
        ("Thank you", "en"),
        ("Grazie per la visione del contratto", "it"),
        ("Grazie per la visione", "en"),  # another language's phrase is not dropped
        ("Ich möchte die Untertitel ändern", "de"),
    ],
)
def test_speech_is_not_mistaken_for_a_made_up_phrase(text: str, locale: str) -> None:
    assert not made_up(text, locale)


async def test_a_recording_loses_the_phrases_recognisers_make_up(
    renv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, lambda **kw: providers.FakeSTT(["Thanks for watching!"], **kw))
    app = _app(renv)
    tok = (await renv.token())["token"]
    headers = {**_h(tok), "Content-Type": "audio/wav"}
    async with _client(app) as c:
        r = await c.post("/agent/v1/session/transcribe", content=_wav(0.5), headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["text"] == ""
        await _no_filter(renv)
        r = await c.post("/agent/v1/session/transcribe", content=_wav(0.5), headers=headers)
    assert r.json()["text"] == "Thanks for watching!"


async def test_the_stream_drops_made_up_phrases_unless_the_application_keeps_them(
    renv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, lambda **kw: providers.FakeSTT(["Thanks for watching!", "hello"], **kw))
    ws = await _socket(renv, {"vad": False})
    await _audio(ws, LOUD, 0.04)
    got = await _stop(ws)
    assert [m["text"] for m in got if m["type"] == "final"] == ["hello"]
    await _no_filter(renv)
    ws = await _socket(renv, {"vad": False}, "u2")
    await _audio(ws, LOUD, 0.04)
    got = await _stop(ws)
    assert [m["text"] for m in got if m["type"] == "final"] == ["Thanks for watching!", "hello"]


# ── speech start and end ────────────────────────────────────────────────────────────────────────


async def test_the_stream_says_where_speech_starts_and_ends(
    renv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, Utterance)
    vad = {"startSecs": 0.1, "stopSecs": 0.3, "minVolume": 0, "confidence": 0.5}
    ws = await _socket(renv, {"vad": vad})
    await _audio(ws, QUIET, 0.2)
    await _audio(ws, LOUD, 0.5)
    await _audio(ws, QUIET, 0.6)
    got = await _stop(ws)
    kinds = [m["type"] for m in got]
    assert kinds == ["speech_start", "speech_end", "final", "end"], got
    start, end = got[0]["ms"], got[1]["ms"]
    assert abs(start - 200) <= 40 and abs(end - 700) <= 40, got
    assert got[2]["text"] == "utterance 1"


# ── client commits ──────────────────────────────────────────────────────────────────────────────


async def test_without_detection_the_client_ends_each_utterance(
    renv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, Utterance)
    ws = await _socket(renv, {"vad": False})
    await _audio(ws, LOUD, 0.5)
    await ws.send_json({"type": "commit"})
    first = json.loads((await ws.receive())["text"])
    assert first == {"type": "final", "text": "utterance 1"}
    await _audio(ws, LOUD, 0.04)
    await ws.send_json({"type": "commit"})
    warning = json.loads((await ws.receive())["text"])
    assert warning["type"] == "warning" and warning["code"] == "commit_too_short"
    await _audio(ws, QUIET, 0.3)
    got = await _stop(ws)  # stop ends the open utterance: nothing is lost
    assert [m for m in got if m["type"] != "end"] == [{"type": "final", "text": "utterance 2"}]
    assert not [m for m in got if m["type"].startswith("speech_")]


async def test_a_commit_while_the_person_speaks_starts_the_next_utterance(
    renv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, Utterance)
    vad = {"startSecs": 0.1, "stopSecs": 0.3, "minVolume": 0, "confidence": 0.5}
    ws = await _socket(renv, {"vad": vad})
    await _audio(ws, LOUD, 0.5)
    await ws.send_json({"type": "commit"})
    await _audio(ws, LOUD, 0.5)
    await _audio(ws, QUIET, 0.5)
    got = await _stop(ws)
    assert [m["type"] for m in got] == ["speech_start", "final", "speech_end", "final", "end"]
    assert [m["text"] for m in got if m["type"] == "final"] == ["utterance 1", "utterance 2"]


async def test_a_bad_start_is_refused(renv: Env) -> None:
    tok = (await renv.token())["token"]
    ws = Socket(_app(renv), PATH, ["evokai.v1", f"evokai.token.{tok}"], {"origin": ORIGIN})
    await ws.open()
    await ws.send_json({"type": "start", "sampleRate": RATE, "hint": "x" * 501})
    got, closed = await ws.until_close()
    assert got[-1]["code"] == "start" and closed["code"] == 4400


# ── vocabulary hints ────────────────────────────────────────────────────────────────────────────


async def test_a_hint_reaches_the_engine(renv: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    heard = _engine(monkeypatch, lambda **kw: providers.FakeSTT(["hello"], **kw))
    app = _app(renv)
    tok = (await renv.token())["token"]
    async with _client(app) as c:
        r = await c.post(
            "/agent/v1/session/transcribe",
            params={"hint": "Evokai, Pipecat"},
            content=_wav(0.2),
            headers={**_h(tok), "Content-Type": "audio/wav"},
        )
        assert r.status_code == 200, r.text
        too_long = await c.post(
            "/agent/v1/session/transcribe",
            params={"hint": "x" * 501},
            content=_wav(0.2),
            headers={**_h(tok), "Content-Type": "audio/wav"},
        )
        assert too_long.status_code in (400, 422)
    ws = await _socket(renv, {"hint": "Evokai, Silero"}, "u2")
    await _stop(ws)
    plain = await _socket(renv, {}, "u3")
    await _stop(plain)
    assert [k.get("hint") for k in heard.kwargs] == ["Evokai, Pipecat", "Evokai, Silero", None]
    assert "hint" not in heard.kwargs[-1]  # engines of your own are not given what was not sent


def test_a_hint_is_split_into_key_terms() -> None:
    assert providers.key_terms("Evokai, Pipecat;\nSilero , Evokai,,") == [
        "Evokai",
        "Pipecat",
        "Silero",
    ]
    assert providers.key_terms("x" * 60 + ", ok") == ["ok"]
    assert providers.key_terms(None) == []


async def test_engines_that_take_a_vocabulary_are_given_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    hint = "Evokai, Pipecat"
    for name in ("openai", "openai-realtime"):
        service = providers.make_stt(name, sample_rate=RATE, hint=hint)
        settings: Any = service._settings
        assert settings.prompt == hint, name
    for name in ("elevenlabs", "elevenlabs-realtime"):
        service = providers.make_stt(name, sample_rate=RATE, hint=hint)
        terms: Any = service._settings
        assert terms.keyterms == ["Evokai", "Pipecat"], name
        await providers.close_service(service)
    assert providers.stt_caps("elevenlabs-realtime").commit_ms == 300


# ── the application setting ─────────────────────────────────────────────────────────────────────


async def test_the_filter_setting_is_kept_exported_and_bootstrapped(
    env: Env, tmp_path: Path
) -> None:
    from evokai.agents.bootstrap import bootstrap

    adm = {"Authorization": f"Bearer {env.admin_key}"}
    got = (await env.client.get("/agent/v1/admin/application", headers=adm)).json()
    assert got["stt_filter"] is True
    await _no_filter(env)
    got = (await env.client.get("/agent/v1/admin/application", headers=adm)).json()
    assert got["stt_filter"] is False
    text = (await env.client.get("/agent/v1/admin/export", headers=adm)).json()["yaml"]
    assert "stt_filter: false" in text
    (tmp_path / "copy.yaml").write_text(text.replace("slug: test", "slug: copy"), "utf-8")
    copy_id = await bootstrap(env.svc.store, env.svc.keyring, tmp_path / "copy.yaml", {})
    copy = await env.svc.store.applications.get(copy_id)
    assert copy is not None and copy.defaults["stt_filter"] is False


class Committing(providers.FakeSTT):
    """A realtime engine with commits: it answers a commit later, over its own connection."""

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        del audio  # streamed to the engine's own connection; nothing comes back here
        yield None

    async def process_frame(self, frame: Frame, direction: Any) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            self.create_task(self._answer())

    async def _answer(self) -> None:
        await asyncio.sleep(0.3)
        await self.push_frame(TranscriptionFrame("late words", "", time_now_iso8601()))


async def test_stop_waits_for_the_last_words_of_an_engine_that_commits(
    renv: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine(monkeypatch, Committing)
    monkeypatch.setitem(providers.STT_CAPS, "fake", providers.SttCaps(commit_ms=100))
    ws = await _socket(renv, {"vad": False})
    await _audio(ws, LOUD, 0.3)
    got = await _stop(ws)
    assert {"type": "final", "text": "late words"} in got


# ── realtime engines with commits, through their adapters ───────────────────────────────────────


class Wire:
    """A realtime engine's connection, answered here instead of over the network.

    ``answer`` is given each message the adapter sends and returns what the engine replies.
    """

    def __init__(self, answer: Any, greeting: list[dict[str, Any]] | None = None) -> None:
        from websockets.protocol import State

        self.state = State.OPEN
        self.sent: list[dict[str, Any]] = []
        self._answer = answer
        self._in: asyncio.Queue[str | None] = asyncio.Queue()
        for msg in greeting or []:
            self._in.put_nowait(json.dumps(msg))

    async def send(self, text: str) -> None:
        msg = json.loads(text)
        self.sent.append(msg)
        for reply in self._answer(msg, self):
            self._in.put_nowait(json.dumps(reply))

    def __aiter__(self) -> Wire:
        return self

    async def __anext__(self) -> str:
        msg = await self._in.get()
        if msg is None:
            raise StopAsyncIteration
        return msg

    async def close(self) -> None:
        from websockets.protocol import State

        self.state = State.CLOSED
        self._in.put_nowait(None)


def _realtime(
    monkeypatch: pytest.MonkeyPatch, name: str, answer: Any, greeting: Any = None
) -> list[Wire]:
    """Stream with a registered realtime engine whose connection is a :class:`Wire`."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    wires: list[Wire] = []

    def factory(**kw: Any) -> Any:
        service: Any = providers.make_stt(name, **kw)

        async def connect(*_: Any, **__: Any) -> Wire:
            await asyncio.sleep(0)  # connecting takes a turn of the loop, as over the network
            wires.append(Wire(answer, greeting))
            return wires[-1]

        service._websocket_connect = connect
        return service

    monkeypatch.setitem(providers.STT, "fake", factory)
    monkeypatch.setitem(providers.STT_CAPS, "fake", providers.stt_caps(name))
    return wires


def _scribe(msg: dict[str, Any], wire: Wire) -> list[dict[str, Any]]:
    if not msg.get("commit"):
        return []
    n = sum(1 for m in wire.sent if m.get("commit"))
    return [{"message_type": "committed_transcript", "text": f"utterance {n}"}]


def _openai(msg: dict[str, Any], wire: Wire) -> list[dict[str, Any]]:
    if msg["type"] == "session.update":
        return [{"type": "session.updated"}]
    if msg["type"] != "input_audio_buffer.commit":
        return []
    n = sum(1 for m in wire.sent if m.get("type") == "input_audio_buffer.commit")
    return [
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": f"utterance {n}",
        }
    ]


def _commits(wire: Wire) -> int:
    return sum(
        1
        for m in wire.sent
        if m.get("commit") is True or m.get("type") == "input_audio_buffer.commit"
    )


@pytest.mark.parametrize(
    ("name", "answer", "greeting", "least"),
    [
        ("elevenlabs-realtime", _scribe, None, 0.3),
        ("openai-realtime", _openai, [{"type": "session.created"}], 0.1),
    ],
)
async def test_a_realtime_engine_transcribes_each_committed_utterance(
    renv: Env,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    answer: Any,
    greeting: Any,
    least: float,
) -> None:
    wires = _realtime(monkeypatch, name, answer, greeting)
    assert providers.stt_caps(name).commit_ms == round(least * 1000)
    ws = await _socket(renv, {"vad": False})
    await _audio(ws, LOUD, 0.5)
    await ws.send_json({"type": "commit"})
    assert json.loads((await ws.receive())["text"]) == {"type": "final", "text": "utterance 1"}
    # Less audio than the engine takes: never sent to it, the client is told, the socket goes on.
    await _audio(ws, LOUD, least / 2)
    await ws.send_json({"type": "commit"})
    warning = json.loads((await ws.receive())["text"])
    assert warning["type"] == "warning" and warning["code"] == "commit_too_short"
    assert _commits(wires[0]) == 1
    await _audio(ws, LOUD, 0.4)
    got = await _stop(ws)  # stop commits what is left and waits for its words
    assert [m for m in got if m["type"] != "end"] == [{"type": "final", "text": "utterance 2"}]
    assert _commits(wires[0]) == 2


def test_realtime_engines_leave_the_utterances_to_evokai(monkeypatch: pytest.MonkeyPatch) -> None:
    from pipecat.services.elevenlabs.stt import CommitStrategy

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    scribe: Any = providers.make_stt("elevenlabs-realtime", sample_rate=RATE)
    assert scribe._commit_strategy == CommitStrategy.MANUAL
    openai: Any = providers.make_stt("openai-realtime", sample_rate=RATE)
    assert openai._turn_detection is False
