"""Which speech engines a session may use, and what speech costs.

Loaded by the core (the token mint) as well as by the voice routes: nothing here imports Pipecat.

A deployment offers the engines in ``EVOKAI_VOICE_TTS`` and ``EVOKAI_VOICE_STT`` (the defaults)
plus ``EVOKAI_VOICE_TTS_ENGINES`` and ``EVOKAI_VOICE_STT_ENGINES``; a session token may choose
among them (``speech: {tts, stt}``). Any server engine needs the application's ``voice: server``
grant; a recognition engine its ``stt`` grant; the session's voice its ``tts`` grant
(``server/<voice>``); and every engine must pass the application's data protection profile
(``tts:<engine>``, ``stt:<engine>`` in the provider catalog).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evokai.core.errors import BadRequest, Forbidden
from evokai.grants import Grants, NotGranted
from evokai.llm import providers
from evokai.protection import processor_allows, protection


class SpeechChoice(BaseModel):
    """The server speech engines a session token chooses."""

    tts: str | None = Field(default=None, max_length=120)
    """Speech synthesis (``POST /v1/session/speech``, server voice)."""
    stt: str | None = Field(default=None, max_length=120)
    """Recognition (``POST /v1/session/transcribe``, the streaming socket, server voice)."""


def speech_view(tts: str | None, stt: str | None) -> dict[str, str | None] | None:
    """The engines a session chose, as clients read them.

    Returns:
        ``{tts, stt}``, or None when it chose none.
    """
    return {"tts": tts, "stt": stt} if tts or stt else None


def tts_engines(settings: Any) -> list[str]:
    """The speech engines this deployment offers (its default first).

    Returns:
        Their names.
    """
    return list(dict.fromkeys([settings.voice_tts, *settings.voice_tts_engines]))


def stt_engines(settings: Any) -> list[str]:
    """The recognition engines this deployment offers (its default first).

    Returns:
        Their names.
    """
    return list(dict.fromkeys([settings.voice_stt, *settings.voice_stt_engines]))


def check_speech(
    settings: Any,
    app: Any,
    grants: Grants,
    *,
    tts: str | None = None,
    stt: str | None = None,
    voice: str | None = None,
) -> None:
    """Refuse a speech choice the deployment does not offer or the application may not use.

    Raises:
        BadRequest: An engine the deployment does not offer (``speech_engine``).
        NotGranted: Server speech, the recognition engine or the voice is not granted.
        Forbidden: An engine outside the application's data protection profile.
    """
    if tts is not None and tts not in tts_engines(settings):
        raise BadRequest(f"The speech engine {tts} is not offered here.", code="speech_engine")
    if stt is not None and stt not in stt_engines(settings):
        raise BadRequest(
            f"The speech-to-text engine {stt} is not offered here.", code="speech_engine"
        )
    if not grants.voice("server"):
        raise NotGranted("voice_engine", "Server speech is not granted to this application.")
    if stt is not None and not grants.stt(stt):
        raise NotGranted("stt", f"The speech-to-text engine {stt} is not granted.")
    if tts is not None and voice and not grants.tts("server", voice):
        raise NotGranted("voice", f"The voice {voice} is not granted for server speech.")
    for kind, engine in (("tts", tts), ("stt", stt)):
        if engine is not None and not processor_allows(protection(app), f"{kind}:{engine}"):
            raise Forbidden(
                f"The {kind} engine {engine} is outside the application's data protection profile.",
                code="data_protection",
            )


def speech_usd(pid: str, *, characters: int = 0, audio_ms: int = 0) -> float:
    """What speech costs at the catalog's price of an engine (0 without one: self-hosted, fake).

    Args:
        pid: ``tts:<engine>`` or ``stt:<engine>``.
        characters: Characters synthesised (priced ``per_1000_characters``).
        audio_ms: Audio transcribed (priced ``per_minute`` or ``per_hour``).

    Returns:
        USD.
    """
    entry = providers.current().processors.get(pid)
    price = entry.prices[0] if entry is not None and entry.prices else None
    if price is None:
        return 0.0
    if price.unit == "per_1000_characters":
        return characters / 1000 * price.input
    if price.unit == "per_million_characters":
        return characters / 1_000_000 * price.input
    if price.unit == "per_minute":
        return audio_ms / 60_000 * price.input
    if price.unit == "per_hour":
        return audio_ms / 3_600_000 * price.input
    return 0.0
