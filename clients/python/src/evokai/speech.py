"""Server speech for a session: synthesis with timings, and transcription.

``speak`` asks ``POST /v1/session/speech`` for a text or one ``say`` segment and collects its
event stream into a :class:`Speech`: the audio (16-bit mono PCM or Ogg Opus) and its timings
(words, the segment's cues as marks, visemes). ``transcribe`` sends a recording to
``POST /v1/session/transcribe``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from evokai.errors import EvokaiError
from evokai.sse import SseMessage

SPEECH_PATH = "/v1/session/speech"
TRANSCRIBE_PATH = "/v1/session/transcribe"

SpeechFormat = Literal["pcm", "opus"]


def speech_body(
    text: str | None,
    turn_id: str | None,
    index: int | None,
    format: SpeechFormat,
    sample_rate: int,
) -> dict[str, Any]:
    """The request body of a speech call.

    Returns:
        The body.

    Raises:
        ValueError: Neither a text nor a segment.
    """
    if text is None and (turn_id is None or index is None):
        raise ValueError("speak needs a text, or a turn_id and an index")
    body: dict[str, Any] = {"format": format, "sampleRate": sample_rate}
    if text is not None:
        body["text"] = text
    else:
        body.update(turnId=turn_id, index=index)
    return body


@dataclass
class Speech:
    """A spoken text: its audio and timings."""

    engine: str = ""
    voice: str | None = None
    format: SpeechFormat = "pcm"
    sample_rate: int = 24_000
    text: str = ""
    """What is shown (the subtitle)."""
    spoken: str = ""
    """What the voice says (numbers in words)."""
    chunks: list[bytes] = field(default_factory=list, repr=False)
    """The audio as it arrived (``audio`` joins it)."""
    words: list[dict[str, Any]] = field(default_factory=list)
    """``{text, at, length, start}``: offset and length in ``spoken``, start in ms."""
    marks: list[dict[str, Any]] = field(default_factory=list)
    """The segment's cues: ``{name, arg?, at, time}``."""
    visemes: list[dict[str, Any]] = field(default_factory=list)
    """``{id, time}``."""
    duration_ms: int = 0
    estimated: bool = False
    """The engine gave no word times: they are spread by the length of the text."""
    complete: bool = False
    """The stream reached its ``done`` event."""

    @property
    def audio(self) -> bytes:
        """The whole audio, in ``format``.

        Returns:
            The bytes.
        """
        return b"".join(self.chunks)


def collect(messages: Iterable[SseMessage], speech: Speech | None = None) -> Speech:
    """Fold speech events into a :class:`Speech`.

    Returns:
        The speech so far.

    Raises:
        EvokaiError: The engine failed (``error`` event).
    """
    out = speech or Speech()
    for msg in messages:
        data = json.loads(msg.data)
        if msg.event == "start":
            out.engine, out.voice = data["engine"], data.get("voice")
            out.format, out.sample_rate = data["format"], data["sampleRate"]
            out.text, out.spoken = data["text"], data["spoken"]
        elif msg.event == "audio":
            out.chunks.append(base64.b64decode(data["data"]))
        elif msg.event == "word":
            out.words.append(data)
        elif msg.event == "mark":
            out.marks.append(data)
        elif msg.event == "viseme":
            out.visemes.append(data)
        elif msg.event == "done":
            out.duration_ms, out.estimated = data["durationMs"], bool(data.get("estimated"))
            out.complete = True
        elif msg.event == "error":
            raise EvokaiError(
                502, data.get("code", "tts_failed"), data.get("message", "Speech failed")
            )
    return out


def finished(speech: Speech) -> Speech:
    """The speech, once its stream is over.

    Returns:
        It.

    Raises:
        EvokaiError: The stream ended before its ``done`` event.
    """
    if not speech.complete:
        raise EvokaiError(502, "tts_failed", "The speech stream ended early")
    return speech


class Transcript(BaseModel):
    """What a recording says."""

    text: str
    durationMs: int
    engine: str
