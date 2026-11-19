"""Speech providers for voice sessions: a registry of STT and TTS factories.

Providers are Pipecat services. ``fake`` ones are built in (tests and local runs without a
vendor); real ones are registered by name or given as ``module:factory``, so an EU-hosted STT or TTS
is a configuration change, not a code change in the voice pipeline.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncGenerator, Callable
from typing import Any

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSAudioRawFrame
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

from evokai.core.errors import ConfigError

Factory = Callable[..., Any]


class FakeSTT(STTService):
    """Hears scripted phrases: one per audio frame that is not silence."""

    def __init__(self, phrases: list[str] | None = None, **kwargs: Any) -> None:
        """Script the phrases.

        Args:
            phrases: What the user will have said, in order.
            **kwargs: Passed to STTService.
        """
        super().__init__(**kwargs)
        self.phrases = list(phrases or [])

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None]:
        """Transcribe a chunk.

        Args:
            audio: PCM audio.

        Yields:
            A final transcription for a non-silent chunk while phrases are left.
        """
        if self.phrases and any(audio):
            yield TranscriptionFrame(self.phrases.pop(0), "", time_now_iso8601(), finalized=True)


class FakeTTS(TTSService):
    """Speaks silence: 20 ms of audio per 4 characters. Can be told to fail (quota, outage)."""

    def __init__(self, *, fail: bool = False, **kwargs: Any) -> None:
        """Configure.

        Args:
            fail: Report an error instead of speaking.
            **kwargs: Passed to TTSService.
        """
        super().__init__(push_text_frames=False, **kwargs)
        self.fail = fail
        self.spoken: list[str] = []

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None]:
        """Synthesize.

        Args:
            text: The sentence.
            context_id: The TTS context.

        Yields:
            Audio frames.
        """
        if self.fail:
            await self.push_error("tts quota exceeded")
            return
        self.spoken.append(text)
        rate = self.sample_rate or 16_000
        chunk = b"\x00\x00" * (rate // 50)
        for _ in range(max(1, len(text) // 4)):
            yield TTSAudioRawFrame(chunk, rate, 1, context_id=context_id)


STT: dict[str, Factory] = {"fake": FakeSTT}
TTS: dict[str, Factory] = {"fake": FakeTTS}


def register_stt(name: str, factory: Factory) -> None:
    """Make an STT provider available by name."""
    STT[name] = factory


def register_tts(name: str, factory: Factory) -> None:
    """Make a TTS provider available by name."""
    TTS[name] = factory


def _resolve(kind: str, ref: str, table: dict[str, Factory]) -> Factory:
    if ref in table:
        return table[ref]
    if ":" in ref:
        module, _, attr = ref.partition(":")
        try:
            return getattr(importlib.import_module(module), attr)
        except (ImportError, AttributeError) as e:
            raise ConfigError(f"{kind} provider {ref!r} cannot be loaded") from e
    raise ConfigError(f"unknown {kind} provider {ref!r}")


def make_stt(ref: str, **kwargs: Any) -> STTService:
    """Build an STT service.

    Args:
        ref: A registered name or ``module:factory``.
        **kwargs: Passed to the factory.

    Returns:
        The service.
    """
    return _resolve("stt", ref, STT)(**kwargs)


def make_tts(ref: str, **kwargs: Any) -> TTSService:
    """Build a TTS service.

    Args:
        ref: A registered name or ``module:factory``.
        **kwargs: Passed to the factory.

    Returns:
        The service.
    """
    return _resolve("tts", ref, TTS)(**kwargs)
