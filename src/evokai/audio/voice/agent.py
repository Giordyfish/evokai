"""The Evokai agent as a Pipecat processor: a finished user turn becomes an Evokai turn.

Where Pipecat would put an LLM service, this processor runs the session's agent through
:class:`~evokai.sessions.turns.TurnEngine` in voice mode, so a spoken turn and a typed one are the
same thing: same conversation, tools as the user, confirmations, budgets, events and storage.

- Transcripts are gathered until the user turn ends (``UserStoppedSpeakingFrame``).
- Spoken replies to an open confirmation or question are matched by the engine (voice mode), the
  same rules as for a browser voice session.
- ``say`` events are spoken (``TTSSpeakFrame``); every event goes to the client as an RTVI server
  message, so the panel shows text, charts and confirmations while it listens.
- Barge-in: an ``InterruptionFrame`` cancels the running turn; what was said stays, marked as
  interrupted, and the rest of that turn is not spoken.
- If the speech provider fails (quota, outage) the session goes on in text only, and says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame

from evokai.audio.speech.replies import is_echo
from evokai.i18n import message
from evokai.identity.models import SessionPrincipal
from evokai.sessions.threads import HistoryMessage
from evokai.sessions.turns import TurnEngine, TurnInput, TurnRun

log = logging.getLogger("evokai.voice")

END_WAIT_SECONDS = 30.0
"""How long the end of a voice session waits for the turn in progress."""


class EvokaiAgentProcessor(FrameProcessor):
    """Runs Evokai turns for one voice session."""

    def __init__(
        self,
        *,
        engine: TurnEngine,
        principal: SessionPrincipal,
        history: list[HistoryMessage] | None = None,
        external: bool = False,
        is_speech_error: Callable[[ErrorFrame], bool] | None = None,
        app: Any = None,
        speech_tags: bool = False,
        **kwargs: Any,
    ) -> None:
        """Bind the processor to a session.

        Args:
            engine: The turn engine.
            principal: The session (application, subject, agent, conversation).
            history: External storage: the conversation so far, sent with the offer.
            external: The application keeps the conversation (history goes with every turn).
            is_speech_error: Whether an upstream error comes from the speech provider.
            app: The application (its locale and messages).
            speech_tags: The TTS performs speech tags (it gets a segment's ``tagged`` form).
            **kwargs: Passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self.engine = engine
        self.app = app
        self.principal = principal
        self.external = external
        self.history: list[HistoryMessage] = list(history or [])
        self.speech_tags = speech_tags
        self._is_speech_error = is_speech_error or (lambda _f: False)
        self.speech = True
        self._heard: list[str] = []
        self._ended = False
        self._task: asyncio.Task | None = None
        self._run: TurnRun | None = None
        self._muted_turn: str | None = None
        self._last_said = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Handle a frame.

        Args:
            frame: The frame.
            direction: Where it travels.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            text = frame.text.strip()
            if text and not (self._last_said and is_echo(text, self._last_said)):
                self._heard.append(text)
            if self._ended:
                self._start()
            return
        if isinstance(frame, UserStoppedSpeakingFrame):
            self._ended = True
            self._start()
        elif isinstance(frame, InterruptionFrame):
            self._interrupt()
        elif isinstance(frame, ErrorFrame) and self.speech and self._is_speech_error(frame):
            await self._speech_off()
        elif isinstance(frame, EndFrame):
            await self._wait()
        elif isinstance(frame, CancelFrame):
            self._interrupt("closed")
        await self.push_frame(frame, direction)

    # turns

    def _start(self) -> None:
        if not self._heard:
            return
        text, self._heard, self._ended = " ".join(self._heard), [], False
        previous = self._task
        self._task = asyncio.create_task(self._turn(text, previous))

    def _interrupt(self, reason: str = "barge_in") -> None:
        if self._run is not None:
            self._muted_turn = self._run.turn_id
            self._run.handle.cancel(reason)

    async def _wait(self) -> None:
        if self._task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self._task), END_WAIT_SECONDS)

    def _input(self, text: str) -> TurnInput:
        history = list(self.history) if self.external else None
        return TurnInput(self.principal, text=text, mode="voice", history=history)

    async def _turn(self, text: str, previous: asyncio.Task | None) -> None:
        if previous is not None and not previous.done():
            self._interrupt("superseded")
            with contextlib.suppress(Exception):
                await previous
        inp = self._input(text)
        try:
            run = await self.engine.open(inp)
        except Exception as e:
            log.warning("voice_turn_refused", extra={"error": type(e).__name__})
            message = getattr(e, "message", "Non posso rispondere ora.")
            await self._event({"type": "error", "code": getattr(e, "code", "internal"),
                               "message": message})  # fmt: skip
            return
        self._run = run
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        stop = run.channel.listen(
            lambda e: queue.put_nowait(e) if e.get("turnId") == run.turn_id else None
        )
        execute = asyncio.create_task(self.engine.execute(run, inp))
        try:
            while True:
                event = await queue.get()
                await self._deliver(run, event)
                if event["type"] == "done":
                    break
        finally:
            stop()
            await execute
            if self._run is run:
                self._run = None

    async def _deliver(self, run: TurnRun, event: dict[str, Any]) -> None:
        kind = event["type"]
        if kind == "say" and event["text"] and self.speech and self._muted_turn != run.turn_id:
            # Numbers in words for the engine; the microphone may hear either form back.
            spoken = event.get("spoken") or event["text"]
            self._last_said = f"{event['text']} {spoken}" if spoken != event["text"] else spoken
            said = (event.get("tagged") if self.speech_tags else None) or spoken
            await self.push_frame(TTSSpeakFrame(said))
        elif kind == "done" and self.external:
            self.history += [HistoryMessage.model_validate(x) for x in event["messages"]]
        await self._event(event)

    async def _event(self, event: dict[str, Any]) -> None:
        await self.push_frame(RTVIServerMessageFrame(data={"evokai": event}))

    async def _speech_off(self) -> None:
        self.speech = False
        log.warning(
            "voice_speech_off", extra={"conversationId": self.principal.claims.conversation_id}
        )
        await self._event({
            "type": "speech",
            "enabled": False,
            "reason": "tts_unavailable",
            "message": message(self.app, "voice.speechOff"),
        })
