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

Every factory takes ``voice`` (the session's voice, else the engine's default), ``sample_rate``,
``language`` (BCP 47) and, for speech, ``settings`` (the catalogue voice's settings, already
checked by ``check_voice_settings``), and ignores what it does not use.
"""

from __future__ import annotations

import functools
import importlib
import os
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSAudioRawFrame
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

from evokai.audio.speech.voice_settings import TTS_SETTINGS, VoiceSettingsRule
from evokai.core.errors import ConfigError

Factory = Callable[..., Any]
VisemeSink = Callable[[int, float], None]
"""Called with a viseme id and its time in milliseconds (from any thread)."""
CharSink = Callable[[list[str], list[float], list[float]], None]
"""Called with characters of the text the engine was given, in order, with their start and end
times in milliseconds (once per piece of alignment the engine reports)."""

FOLLOW_REFS = 3
"""The most earlier syntheses one synthesis follows by reference."""
CONTEXT_CHARS = 1_000
"""The most characters of the text around a synthesis given to the engine as context."""


@dataclass(frozen=True)
class TtsCaps:
    """What a speech engine gives besides audio."""

    words: bool = False
    """Word timestamps from the engine (else they are estimated from the audio's length)."""
    visemes: bool = False
    """Viseme events (mouth shapes) with their times."""
    speech_tags: bool = False
    """It performs speech tags written ``[words]`` (``say.tagged``)."""
    characters: bool = False
    """Character times from the engine (``characters`` on ``POST /v1/session/speech``)."""


@dataclass(frozen=True)
class SttCaps:
    """How a recognition engine listens."""

    segmented: bool = False
    """It transcribes whole utterances (it needs voice activity detection to cut them)."""
    realtime: str | None = None
    """The engine streaming uses instead (``WS /v1/session/transcribe/stream``), if another."""


def voice_model(engine: str, settings: Mapping[str, Any] | None) -> str | None:
    """The model a voice speaks with: its own, else the engine's default where Evokai knows it.

    Returns:
        The model id, or None.
    """
    own = (settings or {}).get("model")
    if own:
        return str(own)
    return elevenlabs_model() if engine == "elevenlabs" else None


class FakeSTT(STTService):
    """Hears scripted phrases: one per audio frame that is not silence."""

    def __init__(self, phrases: list[str] | None = None, **kwargs: Any) -> None:
        """Script the phrases.

        Args:
            phrases: What the user will have said, in order.
            **kwargs: Passed to STTService (``language`` and ``voice`` are ignored).
        """
        kwargs.pop("language", None)
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
    """Speaks silence: 20 ms of audio per 4 characters, with word and character times and visemes.

    Can be told to fail (quota, outage). It keeps what it was given (voice, language, settings, the
    text around it) and gives each synthesis a reference, like an engine that can follow earlier
    syntheses.
    """

    def __init__(
        self,
        *,
        fail: bool = False,
        voice: str | None = None,
        language: str | None = None,
        settings: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Configure.

        Args:
            fail: Report an error instead of speaking.
            voice: The voice it was asked for (kept, for tests).
            language: The language it was asked for (kept, for tests).
            settings: The catalogue voice's settings (kept, for tests).
            **kwargs: Passed to TTSService.
        """
        super().__init__(push_text_frames=False, **kwargs)
        self.fail = fail
        self.voice = voice
        self.language = language
        self.voice_settings = dict(settings or {})
        self.spoken: list[str] = []
        self.followed: tuple[list[str], str, str] | None = None
        self.speech_ref: str | None = None
        self.viseme_sink: VisemeSink | None = None
        self.char_sink: CharSink | None = None

    def follow(self, refs: list[str], before: str, after: str) -> None:
        """Speak in continuity with the text around.

        Args:
            refs: The references of the earlier syntheses, oldest first.
            before: The text spoken before (used when there are no references).
            after: The text that comes next.
        """
        self.followed = (refs, before, after)

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
        self.speech_ref = f"fake-{len(self.spoken)}"
        rate = self.sample_rate or 16_000
        chunks = max(1, len(text) // 4)
        seconds = chunks * 0.02
        if self.char_sink is not None:
            # Two pieces of alignment, as engines report them while they speak.
            step = seconds * 1000 / max(1, len(text))
            half = len(text) // 2
            for a, b in ((0, half), (half, len(text))):
                if a < b:
                    self.char_sink(
                        list(text[a:b]),
                        [round(i * step, 3) for i in range(a, b)],
                        [round((i + 1) * step, 3) for i in range(a, b)],
                    )
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


def _http() -> Any:
    import aiohttp

    return aiohttp.ClientSession()


def elevenlabs_model() -> str:
    """The ElevenLabs speech model (``EVOKAI_ELEVENLABS_TTS_MODEL``).

    Returns:
        The model id.
    """
    return os.environ.get("EVOKAI_ELEVENLABS_TTS_MODEL") or "eleven_flash_v2_5"


class _ElevenLabsSession:
    """The HTTP session of an ElevenLabs service, around the real one.

    It adds the text around a synthesis (or the ids of the earlier ones) to the request the Pipecat
    service builds, and keeps the id ElevenLabs answers with (``request-id``), so a later synthesis
    can follow this one. The service only calls ``post``.
    """

    def __init__(self, session: Any) -> None:
        self.session = session
        self.fields: dict[str, Any] = {}
        self.ref: str | None = None

    def post(self, url: str, *, json: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        if json is not None and self.fields:
            json = {**json, **self.fields}
        return _KeepRef(self, self.session.post(url, json=json, **kwargs))

    async def close(self) -> None:
        await self.session.close()


class _KeepRef:
    def __init__(self, owner: _ElevenLabsSession, request: Any) -> None:
        self._owner, self._request = owner, request

    async def __aenter__(self) -> Any:
        response = await self._request.__aenter__()
        if response.status == 200:
            self._owner.ref = response.headers.get("request-id") or None
        return response

    async def __aexit__(self, *exc: object) -> Any:
        return await self._request.__aexit__(*exc)


@functools.cache
def _elevenlabs_service() -> type:
    from pipecat.services.elevenlabs.tts import (
        ELEVENLABS_CONTEXT_UNSUPPORTED_MODELS,
        ElevenLabsHttpTTSService,
    )

    class ElevenLabsSpeech(ElevenLabsHttpTTSService):
        """ElevenLabs over HTTP that follows earlier syntheses and reports character times."""

        char_sink: CharSink | None = None
        http: _ElevenLabsSession

        @property
        def speech_ref(self) -> str | None:
            return self.http.ref

        def follow(self, refs: list[str], before: str, after: str) -> None:
            """Speak in continuity with the text around (not with the models that refuse it)."""
            self.http.fields.clear()
            if self._settings.model in ELEVENLABS_CONTEXT_UNSUPPORTED_MODELS:
                return
            if refs:
                self.http.fields["previous_request_ids"] = refs[-FOLLOW_REFS:]
            elif before:
                self.http.fields["previous_text"] = before[-CONTEXT_CHARS:]
            if after:
                self.http.fields["next_text"] = after[:CONTEXT_CHARS]

        def calculate_word_times(
            self, alignment_info: Mapping[str, Any], *, audio_tags: bool = False
        ) -> list[tuple[str, float]]:
            # Pipecat hands every piece of character alignment here to make words of it.
            sink = self.char_sink
            chars = list(alignment_info.get("characters") or [])
            starts = list(alignment_info.get("character_start_times_seconds") or [])
            ends = list(alignment_info.get("character_end_times_seconds") or [])
            if sink is not None and chars and len(chars) == len(starts) == len(ends):
                base = float(getattr(self, "_cumulative_time", 0) or 0)
                sink(
                    chars,
                    [(base + float(t)) * 1000 for t in starts],
                    [(base + float(t)) * 1000 for t in ends],
                )
            return super().calculate_word_times(alignment_info, audio_tags=audio_tags)

    return ElevenLabsSpeech


def elevenlabs_tts(
    *,
    voice: str | None = None,
    sample_rate: int | None = None,
    language: str | None = None,
    settings: Mapping[str, Any] | None = None,
    **_: Any,
) -> Any:
    """ElevenLabs over HTTP with timestamps (word and character times).

    The voice's settings choose the model and the voice options; the language goes to the models
    that take one.

    Returns:
        The service.
    """
    from pipecat.services.elevenlabs.tts import (
        ELEVENLABS_MODEL_LANGUAGES,
        ElevenLabsHttpTTSService,
    )

    own = dict(settings or {})
    options = dict(own.get("elevenlabs") or {})
    model = own.get("model") or elevenlabs_model()
    lang = _language(language) if model in ELEVENLABS_MODEL_LANGUAGES else None
    http = _ElevenLabsSession(_http())
    service = _elevenlabs_service()(
        api_key=_env("ELEVENLABS_API_KEY"),
        aiohttp_session=cast(Any, http),
        settings=ElevenLabsHttpTTSService.Settings(
            voice=voice or os.environ.get("EVOKAI_ELEVENLABS_VOICE") or "21m00Tcm4TlvDq8ikWAM",
            model=model,
            language=lang,
            stability=options.get("stability"),
            similarity_boost=options.get("similarityBoost"),
            style=options.get("style"),
            use_speaker_boost=options.get("speakerBoost"),
            speed=own.get("speed"),
        ),
        base_url=os.environ.get("EVOKAI_ELEVENLABS_URL") or "https://api.elevenlabs.io",
        sample_rate=sample_rate,
    )
    service.http = http
    _CLOSERS[service] = http.close
    return service


def openai_tts(
    *,
    voice: str | None = None,
    sample_rate: int | None = None,
    settings: Mapping[str, Any] | None = None,
    **_: Any,
) -> Any:
    """OpenAI speech (no word times: they are estimated).

    The voice's settings choose the model, the speed and the instructions.

    Returns:
        The service.
    """
    from pipecat.services.openai.tts import OpenAITTSService

    own = dict(settings or {})
    options = dict(own.get("openai") or {})
    model = own.get("model") or os.environ.get("EVOKAI_OPENAI_TTS_MODEL") or "gpt-4o-mini-tts"
    return OpenAITTSService(
        api_key=_env("OPENAI_API_KEY"),
        base_url=os.environ.get("EVOKAI_OPENAI_SPEECH_URL") or None,
        sample_rate=sample_rate,
        settings=OpenAITTSService.Settings(
            voice=voice or "alloy",
            model=model,
            speed=own.get("speed"),
            instructions=options.get("instructions"),
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


def elevenlabs_stt(*, sample_rate: int | None = None, **_: Any) -> Any:
    """ElevenLabs Scribe, one utterance at a time.

    Returns:
        The service.
    """
    from pipecat.services.elevenlabs.stt import ElevenLabsSTTService

    session = _http()
    service = ElevenLabsSTTService(
        api_key=_env("ELEVENLABS_API_KEY"), aiohttp_session=session, sample_rate=sample_rate
    )
    _CLOSERS[service] = session.close
    return service


def elevenlabs_realtime_stt(*, sample_rate: int | None = None, **_: Any) -> Any:
    """ElevenLabs Scribe realtime (streaming).

    Returns:
        The service.
    """
    from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService

    return ElevenLabsRealtimeSTTService(api_key=_env("ELEVENLABS_API_KEY"), sample_rate=sample_rate)


def openai_stt(*, language: str | None = None, sample_rate: int | None = None, **_: Any) -> Any:
    """OpenAI transcription, one utterance at a time.

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
        ),
    )


def openai_realtime_stt(
    *, language: str | None = None, sample_rate: int | None = None, **_: Any
) -> Any:
    """OpenAI realtime transcription (streaming).

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
        ),
    )


def whisper_stt(*, sample_rate: int | None = None, **_: Any) -> Any:
    """faster-whisper in this process (``pipecat-ai[whisper]``), one utterance at a time.

    Returns:
        The service.
    """
    from pipecat.services.whisper.stt import WhisperSTTService

    return WhisperSTTService(
        model=os.environ.get("EVOKAI_WHISPER_MODEL") or "distil-medium.en", sample_rate=sample_rate
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
    "fake": TtsCaps(words=True, visemes=True, characters=True),
    "elevenlabs": TtsCaps(words=True, characters=True),
    "azure": TtsCaps(words=True, visemes=True),
}
"""Engines not listed give audio only (their word times are estimated)."""
STT_CAPS: dict[str, SttCaps] = {
    "elevenlabs": SttCaps(segmented=True, realtime="elevenlabs-realtime"),
    "openai": SttCaps(segmented=True, realtime="openai-realtime"),
    "whisper": SttCaps(segmented=True),
}
"""Engines not listed stream (they need no voice activity detection to cut utterances)."""


ELEVENLABS_TAG_MODELS = ("eleven_v3", "eleven_v4")
"""The ElevenLabs models that perform speech tags (by prefix)."""


def tts_caps(ref: str, model: str | None = None) -> TtsCaps:
    """What a speech engine gives with a model (ElevenLabs performs speech tags with v3 and v4).

    Args:
        ref: The engine.
        model: The voice's own model (``voice_model``), else the engine's default.

    Returns:
        The capabilities.
    """
    caps = TTS_CAPS.get(ref, TtsCaps())
    if ref == "elevenlabs" and (model or elevenlabs_model()).startswith(ELEVENLABS_TAG_MODELS):
        return replace(caps, speech_tags=True)
    return caps


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


def register_tts(
    name: str,
    factory: Factory,
    caps: TtsCaps | None = None,
    settings: VoiceSettingsRule | None = None,
) -> None:
    """Make a TTS provider available by name (with the settings its catalogue voices may carry)."""
    TTS[name] = factory
    if caps is not None:
        TTS_CAPS[name] = caps
    if settings is not None:
        TTS_SETTINGS[name] = settings


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
