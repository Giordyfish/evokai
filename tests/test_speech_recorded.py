"""Real speech engines, replayed from recorded HTTP cassettes.

See docs/deployment.md#recorded-http-cassettes.

Recorded with ``scripts/record-cassettes.sh speech`` (ElevenLabs and OpenAI keys, tiny texts); a
plain run only replays, and a test whose cassette was never recorded is skipped and says so.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pipecat")

from evokai.audio.voice.oneshot import Aligner, synthesize, transcribe
from evokai.audio.voice.providers import make_stt, make_tts
from tests.test_recorded import VCR_CONFIG

KEYS = ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {**VCR_CONFIG, "ignore_hosts": [*VCR_CONFIG["ignore_hosts"]]}


@pytest.fixture
def cassette(record_mode: str, vcr_cassette_dir: str, default_cassette_name: str) -> Iterator[Path]:
    path = Path(vcr_cassette_dir) / f"{default_cassette_name}.yaml"
    if record_mode == "none" and not path.exists():
        pytest.skip(f"cassette not recorded yet: {path.name} (scripts/record-cassettes.sh speech)")
    yield path


@pytest.fixture
def keys(record_mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if record_mode == "none":
        for k in KEYS:
            monkeypatch.setenv(k, "replayed")


async def _speak(engine: str, text: str) -> tuple[bytes, list[dict[str, Any]]]:
    audio = b""
    aligner = Aligner(text)
    words: list[dict[str, Any]] = []
    async for p in synthesize(make_tts(engine, sample_rate=24_000), text, sample_rate=24_000):
        assert p.kind != "error", p.error
        if p.kind == "audio":
            audio += p.audio
        elif p.kind == "word":
            words += [e for k, e in aligner.add(p.word, p.ms) if k == "word"]
    return audio, words


@pytest.mark.vcr
@pytest.mark.usefixtures("cassette", "keys")
async def test_elevenlabs_speaks_with_word_times() -> None:
    audio, words = await _speak("elevenlabs", "Hello from Evokai.")
    assert len(audio) > 4_800
    assert [w["text"] for w in words] == ["Hello", "from", "Evokai"]


@pytest.mark.vcr
@pytest.mark.usefixtures("cassette", "keys")
async def test_openai_speaks() -> None:
    audio, _ = await _speak("openai", "Hello from Evokai.")
    assert len(audio) > 4_800


@pytest.mark.vcr
@pytest.mark.usefixtures("cassette", "keys")
async def test_openai_transcribes_what_it_said() -> None:
    audio, _ = await _speak("openai", "Hello from Evokai.")
    stt = make_stt("openai", language="en", sample_rate=24_000)
    heard = await transcribe(stt, audio, sample_rate=24_000, segmented=True)
    assert "hello" in heard.text.lower()
