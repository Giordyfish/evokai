"""Speech engines: the catalog over Pipecat services, the one-shot harness and the timings."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pipecat")

from evokai.audio.choice import speech_usd, stt_engines, tts_engines
from evokai.audio.voice.oneshot import (
    Aligner,
    OpusEncoder,
    decode_audio,
    estimate,
    synthesize,
    transcribe,
)
from evokai.audio.voice.providers import (
    FakeSTT,
    FakeTTS,
    azure_offset_ms,
    close_service,
    make_stt,
    make_tts,
    stt_caps,
    tts_caps,
)
from evokai.core.config import Settings
from evokai.core.errors import ConfigError
from evokai.llm import providers


def test_factories_take_the_session_voice() -> None:
    tts = make_tts("fake", voice="elena", language="it")
    assert isinstance(tts, FakeTTS) and tts.voice == "elena"
    assert isinstance(make_stt("fake", language="it"), FakeSTT)


def test_unknown_and_unconfigured_engines(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigError, match="unknown tts"):
        make_tts("nope")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        make_tts("openai")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")
    import importlib.util

    if importlib.util.find_spec("azure.cognitiveservices") is None:
        with pytest.raises(ConfigError, match="Pipecat extra"):
            make_tts("azure")


async def test_real_engines_are_pipecat_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    assert type(make_tts("openai", voice="nova", sample_rate=24_000)).__name__ == "OpenAITTSService"
    assert (
        type(make_stt("openai-realtime", language="it-IT")).__name__ == "OpenAIRealtimeSTTService"
    )
    el = make_tts("elevenlabs", voice="v1", sample_rate=24_000)
    assert type(el).__name__ == "ElevenLabsHttpTTSService"
    await close_service(el)
    stt = make_stt("elevenlabs", sample_rate=16_000)
    assert type(stt).__name__ == "ElevenLabsSTTService"
    await close_service(stt)


def test_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tts_caps("fake").words and tts_caps("fake").visemes
    assert tts_caps("openai").words is False
    assert tts_caps("elevenlabs").words and not tts_caps("elevenlabs").speech_tags
    monkeypatch.setenv("EVOKAI_ELEVENLABS_TTS_MODEL", "eleven_v3")
    assert tts_caps("elevenlabs").speech_tags
    assert stt_caps("openai").realtime == "openai-realtime" and stt_caps("openai").segmented
    assert not stt_caps("azure").segmented
    assert azure_offset_ms(12_500_000) == 1250


def test_the_deployment_offers_its_defaults_and_its_extra_engines() -> None:
    st = Settings(
        voice_tts="elevenlabs",
        voice_tts_engines=["openai", "elevenlabs"],
        voice_stt_engines=["whisper"],
    )
    assert tts_engines(st) == ["elevenlabs", "openai"]
    assert stt_engines(st) == ["fake", "whisper"]


def test_every_engine_is_in_the_provider_catalog_with_its_terms() -> None:
    cat = providers.current()
    for pid in ("tts:elevenlabs", "tts:openai", "tts:azure", "tts:piper", "tts:kokoro",
                "stt:elevenlabs", "stt:openai", "stt:azure", "stt:whisper"):  # fmt: skip
        assert cat.processor_facts(pid) is not None, pid
    piper = cat.processor_facts("tts:piper")
    assert piper is not None and piper.self_hosted
    assert speech_usd("tts:openai", characters=1_000_000) == pytest.approx(15.0)
    assert speech_usd("stt:openai", audio_ms=60_000) > 0
    assert speech_usd("tts:fake", characters=1_000) == 0


async def test_a_synthesis_streams_audio_words_and_visemes() -> None:
    pieces = [
        p
        async for p in synthesize(
            FakeTTS(sample_rate=16_000), "Hello there world", sample_rate=16_000
        )
    ]
    kinds = [p.kind for p in pieces]
    assert "audio" in kinds and kinds.count("word") == 3 and kinds.count("viseme") == 3
    words = [(p.word, p.ms) for p in pieces if p.kind == "word"]
    assert [w for w, _ in words] == ["Hello", "there", "world"]
    assert words[0][1] == 0 and words[1][1] > 0


async def test_a_failing_engine_reports_an_error() -> None:
    pieces = [
        p
        async for p in synthesize(FakeTTS(fail=True, sample_rate=16_000), "Hi", sample_rate=16_000)
    ]
    assert [p.kind for p in pieces] == ["error"]


async def test_a_recording_is_transcribed() -> None:
    heard = await transcribe(
        FakeSTT(["good morning"]), b"\x00\x10" * 8_000, sample_rate=16_000, segmented=False
    )
    assert heard.text == "good morning" and not heard.error


def test_words_and_cues_are_aligned_on_the_spoken_text() -> None:
    a = Aligner("Hello, big world.", [{"name": "wave", "at": 7}, {"name": "end", "at": 17}])
    assert a.add("Hello,", 0) == [("word", {"text": "Hello", "at": 0, "length": 5, "start": 0})]
    assert a.add("[laughs]", 100) == []
    out = a.add("big", 300)
    assert out[0] == ("mark", {"name": "wave", "at": 7, "time": 300})
    assert out[1][1]["at"] == 7
    assert a.add("missing", 400) == []
    a.add("world.", 500)
    assert a.finish(900) == [("mark", {"name": "end", "at": 17, "time": 900})]


def test_cues_use_their_spoken_offset_when_numbers_were_spelled() -> None:
    a = Aligner("Sono mille punti", [{"name": "nod", "at": 11, "spokenAt": 11}])
    a.add("Sono", 0)
    a.add("mille", 200)
    assert a.add("punti", 400)[0] == ("mark", {"name": "nod", "at": 11, "time": 400})


def test_estimated_words_follow_the_length_of_the_text() -> None:
    assert estimate("ab cd", 1000) == [("ab", 0.0), ("cd", 600.0)]


def test_opus_round_trip() -> None:
    enc = OpusEncoder(16_000)
    data = enc.push(b"\x00\x10" * 16_000) + enc.close()
    assert data[:4] == b"OggS"
    pcm = decode_audio(data, 16_000)
    assert abs(len(pcm) / 2 - 16_000) < 2_000
    with pytest.raises(ValueError):
        decode_audio(b"not audio")


def test_decoding_stops_past_the_longest_recording() -> None:
    enc = OpusEncoder(16_000)
    data = enc.push(b"\x00\x10" * 16_000 * 3) + enc.close()
    with pytest.raises(ValueError, match="too long"):
        decode_audio(data, 16_000, max_seconds=1)
    assert len(decode_audio(data, 16_000, max_seconds=5)) > 16_000 * 2


def test_the_fake_recognition_hears_nothing_unless_scripted() -> None:
    assert FakeSTT().phrases == []


async def test_a_stuck_engine_ends_with_an_error() -> None:
    class Stuck(FakeTTS):
        async def run_tts(self, text: str, context_id: str):  # type: ignore[override]
            del text, context_id
            await asyncio.sleep(10)
            yield None

    pieces = [
        p
        async for p in synthesize(Stuck(sample_rate=16_000), "Hi", sample_rate=16_000, timeout=0.5)
    ]
    assert [p.kind for p in pieces] == ["error"] and pieces[0].error == "timeout"
