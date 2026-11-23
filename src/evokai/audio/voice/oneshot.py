"""One synthesis or one transcription with a Pipecat service, outside a voice session.

A speech engine is a Pipecat service built for a pipeline; here it runs in a pipeline of its own
for one request: the text in, its audio and word timestamps out as they come (synthesis), or the
audio in, its transcript out (recognition). The same services serve the WebRTC voice sessions, so
an engine is configured once.

Word timestamps are caught where the service reports them (``add_word_timestamps``, seconds from
the start of its audio); engines that report none get word times estimated from the length of the
audio. Visemes come from engines that give them through a ``viseme_sink``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from pipecat.frames.frames import (
    EndFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from evokai.audio.voice.providers import close_service

_MARKERS = {"TTSStoppedFrame", "Reset", "LLMFullResponseEndFrame"}
"""Names some Pipecat services pass as words to reset their own word clock."""


@dataclass
class Piece:
    """One thing a synthesis produced."""

    kind: str
    """``audio``, ``word``, ``viseme`` or ``error``."""
    audio: bytes = b""
    word: str = ""
    ms: float = 0.0
    """When it happens, from the start of the audio."""
    viseme: int = 0
    error: str = ""


class Catch(FrameProcessor):
    """Collects what one direction of the pipeline carries."""

    def __init__(self, put: Callable[[Frame, FrameDirection], None]) -> None:
        """Collect with a callback.

        Args:
            put: Called with every frame and its direction.
        """
        super().__init__()
        self._put = put

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Hand a frame to the callback and pass it on.

        Args:
            frame: The frame.
            direction: Where it travels.
        """
        await super().process_frame(frame, direction)
        self._put(frame, direction)
        await self.push_frame(frame, direction)


async def run_pipeline(
    processors: list[FrameProcessor], rate: int, frames: list[Frame] | None = None
) -> tuple[PipelineWorker, asyncio.Task]:
    """Start a pipeline of its own for one request.

    Args:
        processors: The processors, in order.
        rate: The audio sample rate (in and out).
        frames: Frames queued before it starts.

    Returns:
        The worker (to queue more frames) and the task that ends with it.
    """
    worker = PipelineWorker(
        Pipeline(processors),
        params=PipelineParams(audio_in_sample_rate=rate, audio_out_sample_rate=rate),
        enable_rtvi=False,
        idle_timeout_secs=None,
        check_dangling_tasks=False,
    )
    if frames:
        await worker.queue_frames(frames)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    return worker, asyncio.create_task(runner.run())


async def synthesize(service: Any, text: str, *, sample_rate: int) -> AsyncIterator[Piece]:
    """Speak a text with a TTS service.

    Args:
        service: The Pipecat TTS service (closed afterwards).
        text: What to say.
        sample_rate: PCM sample rate of the audio.

    Yields:
        Audio, word timestamps and visemes as the engine gives them; an ``error`` piece when it
        fails.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Piece | None] = asyncio.Queue()
    original = service.add_word_timestamps

    async def words(word_times: list[tuple[str, float]], *args: Any, **kwargs: Any) -> None:
        for word, seconds in word_times:
            if word not in _MARKERS:
                queue.put_nowait(Piece("word", word=word, ms=seconds * 1000))
        await original(word_times, *args, **kwargs)

    service.add_word_timestamps = words
    if hasattr(service, "viseme_sink"):
        service.viseme_sink = lambda vid, ms: loop.call_soon_threadsafe(
            queue.put_nowait, Piece("viseme", viseme=vid, ms=ms)
        )

    def put(frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, TTSAudioRawFrame) and direction == FrameDirection.DOWNSTREAM:
            queue.put_nowait(Piece("audio", audio=frame.audio))
        elif isinstance(frame, ErrorFrame) and direction == FrameDirection.UPSTREAM:
            queue.put_nowait(Piece("error", error=str(frame.error)))

    _, task = await run_pipeline(
        [Catch(put), service, Catch(put)], sample_rate, [TTSSpeakFrame(text), EndFrame()]
    )
    task.add_done_callback(lambda _t: queue.put_nowait(None))
    try:
        while (piece := await queue.get()) is not None:
            yield piece
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await close_service(service)


@dataclass
class Transcript:
    """What a recognition heard."""

    finals: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def text(self) -> str:
        """Every final phrase, in order.

        Returns:
            The text.
        """
        return " ".join(t.strip() for t in self.finals if t.strip())


async def transcribe(
    service: Any, pcm: bytes, *, sample_rate: int, segmented: bool, timeout: float = 120.0
) -> Transcript:
    """Transcribe a whole recording with an STT service.

    Args:
        service: The Pipecat STT service (closed afterwards).
        pcm: 16-bit mono PCM.
        sample_rate: Its sample rate.
        segmented: The engine transcribes whole utterances: the recording is one.
        timeout: The most it may take.

    Returns:
        The transcript.
    """
    out = Transcript()
    done = asyncio.Event()

    def put(frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, TranscriptionFrame) and direction == FrameDirection.DOWNSTREAM:
            out.finals.append(frame.text)
        elif isinstance(frame, ErrorFrame) and direction == FrameDirection.UPSTREAM:
            out.error = str(frame.error)

    step = sample_rate // 50 * 2  # 20 ms
    frames: list[Frame] = [VADUserStartedSpeakingFrame()] if segmented else []
    frames += [
        InputAudioRawFrame(pcm[i : i + step], sample_rate, 1) for i in range(0, len(pcm), step)
    ]
    if segmented:
        frames.append(VADUserStoppedSpeakingFrame())
    frames.append(EndFrame())
    _, task = await run_pipeline([Catch(put), service, Catch(put)], sample_rate, frames)
    task.add_done_callback(lambda _t: done.set())
    try:
        await asyncio.wait_for(done.wait(), timeout)
    except TimeoutError:
        out.error = out.error or "timeout"
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await close_service(service)
    return out


# ── timings ─────────────────────────────────────────────────────────────────────────────────────

_WORD = re.compile(r"\S+")
_EDGES = re.compile(r"^\W+|\W+$")


class Aligner:
    """Word and cue events over a spoken text, as the engine reports its words.

    Each word is found in order in the text (its offset and length); speech tags the engine reports
    as words (``[laughs]``) and words with no match are skipped. A cue fires with the first word at
    or after its offset (``spokenAt`` when the spoken form differs, else ``at``); cues after the
    last word fire when the audio ends.
    """

    def __init__(self, text: str, marks: list[dict[str, Any]] | None = None) -> None:
        """Start.

        Args:
            text: The text as spoken (no tags).
            marks: The ``say`` marks.
        """
        self.text = text
        self._lower = text.lower()
        self._cursor = 0
        self._marks = sorted(marks or [], key=lambda m: m.get("spokenAt", m["at"]))

    def _fire(self, upto: int | None, time: float) -> list[tuple[str, dict[str, Any]]]:
        out = []
        while self._marks and (
            upto is None or self._marks[0].get("spokenAt", self._marks[0]["at"]) <= upto
        ):
            m = self._marks.pop(0)
            out.append((
                "mark",
                {k: v for k, v in m.items() if k != "spokenAt"} | {"time": round(time)},
            ))
        return out

    def add(self, word: str, ms: float) -> list[tuple[str, dict[str, Any]]]:
        """One word the engine reported.

        Returns:
            The events it gives: ``mark`` events due before it, then its ``word``
            (``{text, at, length, start}``).
        """
        if "[" in word or "]" in word:
            return []
        core = _EDGES.sub("", word).lower() or word.lower()
        at = self._lower.find(core, self._cursor)
        if at < 0:
            return []
        self._cursor = at + len(core)
        out = self._fire(at, ms)
        out.append((
            "word",
            {
                "text": self.text[at : at + len(core)],
                "at": at,
                "length": len(core),
                "start": round(ms),
            },
        ))
        return out

    def finish(self, duration_ms: float) -> list[tuple[str, dict[str, Any]]]:
        """The end of the audio: the cues still waiting.

        Returns:
            Their ``mark`` events.
        """
        return self._fire(None, duration_ms)


def estimate(text: str, duration_ms: float) -> list[tuple[str, float]]:
    """Word times spread over the audio by the length of the text before each word.

    Returns:
        (word, ms) per word.
    """
    size = max(1, len(text))
    return [(m.group(0), duration_ms * m.start() / size) for m in _WORD.finditer(text)]


# ── audio formats ───────────────────────────────────────────────────────────────────────────────


class OpusEncoder:
    """PCM in, Ogg Opus pages out, as they are ready (PyAV, part of the voice extra)."""

    def __init__(self, sample_rate: int) -> None:
        """Open the stream.

        Args:
            sample_rate: The PCM's sample rate (resampled to 48 kHz for Opus).
        """
        import av

        self._av = av
        self._buf = io.BytesIO()
        self._out = av.open(self._buf, mode="w", format="ogg")
        self._stream = self._out.add_stream("libopus", rate=48_000, layout="mono")
        self._rate = sample_rate
        self._resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
        self._pts = 0
        self._sent = 0

    def _drain(self) -> bytes:
        data = self._buf.getvalue()
        chunk, self._sent = data[self._sent :], len(data)
        return chunk

    def push(self, pcm: bytes) -> bytes:
        """Encode some PCM.

        Returns:
            The Ogg bytes ready so far (may be empty).
        """
        import numpy as np

        if not pcm:
            return b""
        frame = self._av.AudioFrame.from_ndarray(
            np.frombuffer(pcm, dtype=np.int16).reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = self._rate
        frame.pts = self._pts
        self._pts += frame.samples
        for f in self._resampler.resample(frame):
            for packet in self._stream.encode(f):
                self._out.mux(packet)
        return self._drain()

    def close(self) -> bytes:
        """Flush the encoder and close the stream.

        Returns:
            The last Ogg bytes.
        """
        for f in self._resampler.resample(None):
            for packet in self._stream.encode(f):
                self._out.mux(packet)
        for packet in self._stream.encode(None):
            self._out.mux(packet)
        self._out.close()
        return self._drain()


def decode_audio(data: bytes, sample_rate: int = 16_000) -> bytes:
    """Any audio file PyAV reads (WAV, WebM, Ogg, MP3, M4A), as 16-bit mono PCM.

    Returns:
        The PCM.

    Raises:
        ValueError: Not an audio file PyAV can read.
    """
    import av

    out = bytearray()
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            if not container.streams.audio:
                raise ValueError("no audio stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
            for frame in container.decode(audio=0):
                for f in resampler.resample(frame):
                    out += bytes(f.planes[0])[: f.samples * 2]
            for f in resampler.resample(None):
                out += bytes(f.planes[0])[: f.samples * 2]
    except av.error.FFmpegError as e:
        raise ValueError(str(e)) from e
    return bytes(out)
