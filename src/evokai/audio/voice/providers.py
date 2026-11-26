"""Speech engines: a registry of STT and TTS factories over Pipecat services, with capabilities.

Engines are Pipecat services. ``fake`` ones are built in (tests and local runs without a vendor);
the real ones (``elevenlabs``, ``openai``, ``azure``, ``piper``, ``kokoro`` for speech, ``azure``,
``elevenlabs``, ``openai``, ``whisper`` for recognition) are imported only when built, so a
deployment installs the Pipecat extra of the engines it uses (``pipecat-ai[azure]``,
``pipecat-ai[kokoro]``, ``pipecat-ai[whisper]``, ``pipecat-ai[piper]``; ElevenLabs and OpenAI need
nothing more). Any other engine is given as ``module:factory``, so an EU-hosted engine is a
configuration change, not a code change. Credentials come from the vendors' usual variables
(``ELEVENLABS_API_KEY``, ``OPENAI_API_KEY``, ``AZURE_SPEECH_KEY`` and ``AZURE_SPEECH_REGION``) and
the engine's own settings from ``EVOKAI_*`` variables (docs/voice.md).

Every factory takes ``voice`` (the session's voice, else the engine's default), ``sample_rate``
and ``language`` (BCP 47), and ignores what it does not use. A recognition factory is also given
``hint`` when the client sent one: names and terms the person is likely to say, which engines
that take a vocabulary use (OpenAI and Whisper as a prompt, ElevenLabs as key terms).
"""

from __future__ import annotations

import importlib
import os
import re
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSAudioRawFrame
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

from evokai.core.errors import ConfigError

Factory = Callable[..., Any]
VisemeSink = Callable[[int, float], None]
"""Called with a viseme id and its time in milliseconds (from any thread)."""


@dataclass(frozen=True)
class TtsCaps:
    """What a speech engine gives besides audio."""

    words: bool = False
    """Word timestamps from the engine (else they are estimated from the audio's length)."""
    visemes: bool = False
    """Viseme events (mouth shapes) with their times."""
    speech_tags: bool = False
    """It performs speech tags written ``[words]`` (``say.tagged``)."""


@dataclass(frozen=True)
class SttCaps:
    """How a recognition engine listens."""

    segmented: bool = False
    """It transcribes whole utterances (it needs voice activity detection to cut them)."""
    realtime: str | None = None
    """The engine streaming uses instead (``WS /v1/session/transcribe/stream``), if another."""
    commit_ms: int = 0
    """It transcribes what it was sent when an utterance ends (a realtime engine with commits),
    and needs at least this much audio since the last one; 0 for engines that do not commit."""


class FakeSTT(STTService):
    """Hears scripted phrases: one per audio frame that is not silence."""

    def __init__(self, phrases: list[str] | None = None, **kwargs: Any) -> None:
        """Script the phrases.

        Args:
            phrases: What the user will have said, in order.
            **kwargs: Passed to STTService (``language`` is ignored, ``hint`` kept for tests).
        """
        kwargs.pop("language", None)
        self.hint: str | None = kwargs.pop("hint", None)
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
    """Speaks silence: 20 ms of audio per 4 characters, with word timestamps and visemes.

    Can be told to fail (quota, outage).
    """

    def __init__(self, *, fail: bool = False, voice: str | None = None, **kwargs: Any) -> None:
        """Configure.

        Args:
            fail: Report an error instead of speaking.
            voice: The voice it was asked for (kept, for tests).
            **kwargs: Passed to TTSService (``language`` is ignored).
        """
        kwargs.pop("language", None)
        super().__init__(push_text_frames=False, **kwargs)
        self.fail = fail
        self.voice = voice
        self.spoken: list[str] = []
        self.viseme_sink: VisemeSink | None = None

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
        chunks = max(1, len(text) // 4)
        seconds = chunks * 0.02
        words, at = [], 0
        for word in text.split():
            at = text.index(word, at)
            words.append((word, round(seconds * at / max(1, len(text)), 3)))
            at += len(word)
        await self.start_word_timestamps()
        await self.add_word_timestamps(words, context_id)
        if self.viseme_sink is not None:
            for i, (_, t) in enumerate(words):
                self.viseme_sink(i % 22, t * 1000)
        chunk = b"\x00\x00" * (rate // 50)
        for _ in range(chunks):
            yield TTSAudioRawFrame(chunk, rate, 1, context_id=context_id)


# ── real engines ────────────────────────────────────────────────────────────────────────────────

_CLOSERS: weakref.WeakKeyDictionary[Any, Callable[[], Awaitable[None]]] = (
    weakref.WeakKeyDictionary()
)


async def close_service(service: Any) -> None:
    """Close what a factory opened for a service (an HTTP session), once it is done."""
    closer = _CLOSERS.pop(service, None)
    if closer is not None:
        await closer()


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if not value:
        raise ConfigError(f"{name} is not set")
    return value


def _language(tag: str | None) -> Any:
    from pipecat.transcriptions.language import Language

    if not tag:
        return None
    for code in (tag, tag.split("-")[0]):
        try:
            return Language(code)
        except ValueError:
            continue
    return None


KEYTERMS_MAX = 50
"""The most key terms a hint gives an engine that takes a list (each under 50 characters)."""


def key_terms(hint: str | None) -> list[str]:
    """A hint as a list of terms: split at commas, semicolons and new lines.

    Returns:
        The terms, in order, without repeats; those of 50 characters or more are left out.
    """
    terms = [t.strip() for t in re.split(r"[,;\n]", hint or "")]
    return list(dict.fromkeys(t for t in terms if t and len(t) < 50))[:KEYTERMS_MAX]


def _http() -> Any:
    import aiohttp

    return aiohttp.ClientSession()


def elevenlabs_model() -> str:
    """The ElevenLabs speech model (``EVOKAI_ELEVENLABS_TTS_MODEL``).

    Returns:
        The model id.
    """
    return os.environ.get("EVOKAI_ELEVENLABS_TTS_MODEL") or "eleven_flash_v2_5"


def elevenlabs_tts(*, voice: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """ElevenLabs over HTTP with timestamps (word times).

    Returns:
        The service.
    """
    from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService

    session = _http()
    service = ElevenLabsHttpTTSService(
        api_key=_env("ELEVENLABS_API_KEY"),
        aiohttp_session=session,
        settings=ElevenLabsHttpTTSService.Settings(
            voice=voice or os.environ.get("EVOKAI_ELEVENLABS_VOICE") or "21m00Tcm4TlvDq8ikWAM",
            model=elevenlabs_model(),
        ),
        base_url=os.environ.get("EVOKAI_ELEVENLABS_URL") or "https://api.elevenlabs.io",
        sample_rate=sample_rate,
    )
    _CLOSERS[service] = session.close
    return service


def openai_tts(*, voice: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """OpenAI speech (no word times: they are estimated).

    Returns:
        The service.
    """
    from pipecat.services.openai.tts import OpenAITTSService

    return OpenAITTSService(
        api_key=_env("OPENAI_API_KEY"),
        base_url=os.environ.get("EVOKAI_OPENAI_SPEECH_URL") or None,
        sample_rate=sample_rate,
        settings=OpenAITTSService.Settings(
            voice=voice or "alloy",
            model=os.environ.get("EVOKAI_OPENAI_TTS_MODEL") or "gpt-4o-mini-tts",
        ),
    )


def azure_tts(*, voice: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """Azure AI Speech (word boundaries, visemes).

    Returns:
        The service.
    """
    from pipecat.services.azure.tts import AzureTTSService

    class AzureWithVisemes(AzureTTSService):
        viseme_sink: VisemeSink | None = None

        async def start(self, frame: Any) -> None:
            await super().start(frame)
            synth = getattr(self, "_speech_synthesizer", None)
            if synth is not None:
                synth.viseme_received.connect(self._viseme)

        def _viseme(self, evt: Any) -> None:
            if self.viseme_sink is not None:
                self.viseme_sink(int(evt.viseme_id), azure_offset_ms(evt.audio_offset))

    return AzureWithVisemes(
        api_key=_env("AZURE_SPEECH_KEY"),
        region=_env("AZURE_SPEECH_REGION"),
        voice=voice or "en-US-AvaMultilingualNeural",
        sample_rate=sample_rate,
    )


def azure_offset_ms(ticks: int) -> float:
    """An Azure audio offset (100-nanosecond ticks) in milliseconds.

    Returns:
        Milliseconds.
    """
    return ticks / 10_000


def piper_tts(*, voice: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """A Piper server of your own (``EVOKAI_PIPER_URL``; times estimated).

    Returns:
        The service.
    """
    from pipecat.services.piper.tts import PiperHttpTTSService

    session = _http()
    service = PiperHttpTTSService(
        base_url=_env("EVOKAI_PIPER_URL"), aiohttp_session=session, voice_id=voice,
        sample_rate=sample_rate,
    )  # fmt: skip
    _CLOSERS[service] = session.close
    return service


def kokoro_tts(*, voice: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """Kokoro in this process (``pipecat-ai[kokoro]``; times estimated).

    Returns:
        The service.
    """
    from pipecat.services.kokoro.tts import KokoroTTSService

    return KokoroTTSService(voice_id=voice or "af_heart", sample_rate=sample_rate)


def azure_stt(*, language: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """Azure AI Speech recognition (streaming).

    Returns:
        The service.
    """
    from pipecat.services.azure.stt import AzureSTTService

    return AzureSTTService(
        api_key=_env("AZURE_SPEECH_KEY"),
        region=_env("AZURE_SPEECH_REGION"),
        language=_language(language),
        sample_rate=sample_rate,
    )


def elevenlabs_stt(*, sample_rate: int | None = None, hint: str | None = None, **_: Any) -> Any:
    """ElevenLabs Scribe, one utterance at a time (a hint becomes key terms).

    Returns:
        The service.
    """
    from pipecat.services.elevenlabs.stt import ElevenLabsSTTService

    session = _http()
    terms = key_terms(hint)
    service = ElevenLabsSTTService(
        api_key=_env("ELEVENLABS_API_KEY"),
        aiohttp_session=session,
        sample_rate=sample_rate,
        settings=ElevenLabsSTTService.Settings(keyterms=terms) if terms else None,
    )
    _CLOSERS[service] = session.close
    return service


def elevenlabs_realtime_stt(
    *, sample_rate: int | None = None, hint: str | None = None, **_: Any
) -> Any:
    """ElevenLabs Scribe realtime (streaming; a hint becomes key terms).

    Returns:
        The service.
    """
    from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService

    terms = key_terms(hint)
    return ElevenLabsRealtimeSTTService(
        api_key=_env("ELEVENLABS_API_KEY"),
        sample_rate=sample_rate,
        settings=ElevenLabsRealtimeSTTService.Settings(keyterms=terms) if terms else None,
    )


def openai_stt(
    *,
    language: str | None = None,
    sample_rate: int | None = None,
    hint: str | None = None,
    **_: Any,
) -> Any:
    """OpenAI transcription, one utterance at a time (a hint is its prompt).

    Returns:
        The service.
    """
    from pipecat.services.openai.stt import OpenAISTTService

    return OpenAISTTService(
        api_key=_env("OPENAI_API_KEY"),
        sample_rate=sample_rate,
        settings=OpenAISTTService.Settings(
            model=os.environ.get("EVOKAI_OPENAI_STT_MODEL") or "gpt-4o-mini-transcribe",
            language=_language(language),
            prompt=hint or None,
        ),
    )


def openai_realtime_stt(
    *,
    language: str | None = None,
    sample_rate: int | None = None,
    hint: str | None = None,
    **_: Any,
) -> Any:
    """OpenAI realtime transcription (streaming; a hint is its prompt).

    Returns:
        The service.
    """
    from pipecat.services.openai.stt import OpenAIRealtimeSTTService

    return OpenAIRealtimeSTTService(
        api_key=_env("OPENAI_API_KEY"),
        sample_rate=sample_rate,
        settings=OpenAIRealtimeSTTService.Settings(
            model=os.environ.get("EVOKAI_OPENAI_STT_MODEL") or "gpt-4o-mini-transcribe",
            language=_language(language),
            prompt=hint or None,
        ),
    )


def whisper_stt(*, sample_rate: int | None = None, hint: str | None = None, **_: Any) -> Any:
    """faster-whisper in this process (``pipecat-ai[whisper]``), one utterance at a time.

    A hint is its initial prompt.

    Returns:
        The service.
    """
    from pipecat.services.whisper.stt import WhisperSTTService

    return WhisperSTTService(
        model=os.environ.get("EVOKAI_WHISPER_MODEL") or "distil-medium.en",
        sample_rate=sample_rate,
        settings=WhisperSTTService.Settings(initial_prompt=hint) if hint else None,
    )


STT: dict[str, Factory] = {
    "fake": FakeSTT,
    "azure": azure_stt,
    "elevenlabs": elevenlabs_stt,
    "elevenlabs-realtime": elevenlabs_realtime_stt,
    "openai": openai_stt,
    "openai-realtime": openai_realtime_stt,
    "whisper": whisper_stt,
}
TTS: dict[str, Factory] = {
    "fake": FakeTTS,
    "elevenlabs": elevenlabs_tts,
    "openai": openai_tts,
    "azure": azure_tts,
    "piper": piper_tts,
    "kokoro": kokoro_tts,
}
TTS_CAPS: dict[str, TtsCaps] = {
    "fake": TtsCaps(words=True, visemes=True),
    "elevenlabs": TtsCaps(words=True),
    "azure": TtsCaps(words=True, visemes=True),
}
"""Engines not listed give audio only (their word times are estimated)."""
STT_CAPS: dict[str, SttCaps] = {
    "elevenlabs": SttCaps(segmented=True, realtime="elevenlabs-realtime"),
    "openai": SttCaps(segmented=True, realtime="openai-realtime"),
    "whisper": SttCaps(segmented=True),
    "elevenlabs-realtime": SttCaps(commit_ms=300),
    "openai-realtime": SttCaps(commit_ms=100),
}
"""Engines not listed stream (they need no voice activity detection to cut utterances, and
transcribe as they hear)."""


def tts_caps(ref: str) -> TtsCaps:
    """What a speech engine gives (ElevenLabs performs speech tags with its v3 models).

    Returns:
        The capabilities.
    """
    if ref == "elevenlabs" and elevenlabs_model().startswith("eleven_v3"):
        return TtsCaps(words=True, speech_tags=True)
    return TTS_CAPS.get(ref, TtsCaps())


def stt_caps(ref: str) -> SttCaps:
    """How a recognition engine listens.

    Returns:
        The capabilities.
    """
    return STT_CAPS.get(ref, SttCaps())


def register_stt(name: str, factory: Factory, caps: SttCaps | None = None) -> None:
    """Make an STT provider available by name."""
    STT[name] = factory
    if caps is not None:
        STT_CAPS[name] = caps


def register_tts(name: str, factory: Factory, caps: TtsCaps | None = None) -> None:
    """Make a TTS provider available by name."""
    TTS[name] = factory
    if caps is not None:
        TTS_CAPS[name] = caps


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
        **kwargs: Passed to the factory (``language``, ``sample_rate``).

    Returns:
        The service.

    Raises:
        ConfigError: Unknown, not installed, or missing its credentials.
    """
    try:
        return _resolve("stt", ref, STT)(**kwargs)
    except ImportError as e:
        raise ConfigError(f"stt provider {ref!r} needs its Pipecat extra: {e.name}") from e


def make_tts(ref: str, **kwargs: Any) -> TTSService:
    """Build a TTS service.

    Args:
        ref: A registered name or ``module:factory``.
        **kwargs: Passed to the factory (``voice``, ``sample_rate``, ``language``).

    Returns:
        The service.

    Raises:
        ConfigError: Unknown, not installed, or missing its credentials.
    """
    try:
        return _resolve("tts", ref, TTS)(**kwargs)
    except ImportError as e:
        raise ConfigError(f"tts provider {ref!r} needs its Pipecat extra: {e.name}") from e
