"""Where utterances begin and end on the streaming recognition socket.

An engine transcribes an utterance when it is told the utterance ended: a segmented engine
(one utterance at a time) sends the audio it buffered, a realtime engine with commits
(``SttCaps.commit_ms``) commits what it streamed. Two things end an
utterance: the voice activity detector, when the person stops speaking, and the client, with a
``commit`` (push-to-talk, a kiosk with its own voice detection, a noisy room where silence never
comes). :class:`Utterances` sits between the detector (when there is one) and the engine and keeps
the two consistent:

- an utterance is open from the first speech the detector hears, or, without a detector, from the
  first audio after the last one ended;
- a ``commit`` ends the open utterance now; with the detector still hearing speech, the next one
  opens at once, so the words after a commit are not lost;
- the detector's own end of speech ends the open utterance, unless a commit already did;
- a commit with less audio than the engine needs since the last end (``SttCaps.commit_ms``; some
  engines refuse a commit under a few hundred milliseconds) is not passed on: the client is told
  instead, and the socket stays open.

It also reports the detector's view as ``speech_start`` and ``speech_end`` events, with where in
the audio received they happened (the detector confirms speech ``startSecs`` after it began, and
silence ``stopSecs`` after it ended: both are taken off).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    SystemFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

MIN_COMMIT_MS = 100
"""The least audio a commit needs with any engine: under this there is nothing to transcribe."""


@dataclass
class CommitFrame(SystemFrame):
    """The client ended the current utterance (a system frame: it keeps its place among the audio).

    Parameters:
        closing: Sent with ``stop``: end an open utterance quietly, never warn.
        done: Resolved with whether an utterance was ended.
    """

    closing: bool = False
    done: asyncio.Future[bool] | None = field(default=None, compare=False)


class Utterances(FrameProcessor):
    """Opens and ends utterances for the engine, from the detector and the client's commits."""

    def __init__(
        self,
        *,
        sample_rate: int,
        detector: bool,
        commit_ms: int,
        emit: Callable[[dict[str, Any]], None],
    ) -> None:
        """Start with no utterance open.

        Args:
            sample_rate: The audio's sample rate.
            detector: A voice activity detector runs before this processor.
            commit_ms: The least audio the engine needs since the last end of an utterance.
            emit: Called with the events for the client (``speech_start``, ``speech_end``,
                ``warning``).
        """
        super().__init__()
        self._rate = sample_rate
        self._detector = detector
        self._commit_ms = max(MIN_COMMIT_MS, commit_ms)
        self._emit = emit
        self._samples = 0
        """Samples received so far."""
        self._uncommitted = 0
        """Samples received since the last end of an utterance."""
        self._open = False
        self._speaking = False
        """The detector hears speech."""

    def _ms(self, samples: int) -> int:
        return round(samples / self._rate * 1000)

    async def _start(self) -> None:
        self._open = True
        await self.push_frame(VADUserStartedSpeakingFrame())

    async def _end(self) -> None:
        self._open = False
        self._uncommitted = 0
        await self.push_frame(VADUserStoppedSpeakingFrame())

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Count the audio, turn commits into ends of utterances, keep the detector's in step.

        Args:
            frame: The frame.
            direction: Where it travels.
        """
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, InputAudioRawFrame):
            n = len(frame.audio) // 2
            self._samples += n
            self._uncommitted += n
            if not self._detector and not self._open:
                await self._start()
            await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._speaking = True
            at = self._samples - round(frame.start_secs * self._rate)
            self._emit({"type": "speech_start", "ms": max(0, self._ms(at))})
            if not self._open:
                self._open = True
                await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speaking = False
            at = self._samples - round(frame.stop_secs * self._rate)
            self._emit({"type": "speech_end", "ms": max(0, self._ms(at))})
            if self._open:
                self._open = False
                self._uncommitted = 0
                await self.push_frame(frame, direction)
        elif isinstance(frame, CommitFrame):
            ended = await self._commit(frame.closing)
            if frame.done is not None and not frame.done.done():
                frame.done.set_result(ended)
        else:
            await self.push_frame(frame, direction)

    async def _commit(self, closing: bool) -> bool:
        if closing and not self._open:
            return False
        if self._ms(self._uncommitted) < self._commit_ms:
            if not closing:
                self._emit({
                    "type": "warning",
                    "code": "commit_too_short",
                    "message": f"A commit needs at least {self._commit_ms} ms of new audio: "
                    "it was ignored.",
                })
            return False
        if not self._open:
            await self._start()
        await self._end()
        if self._speaking and not closing:
            await self._start()
        return True
