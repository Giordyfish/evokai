"""Voice pipelines and the WebRTC sessions that run them.

One pipeline per connection::

    WebRTC in → Silero VAD → STT → user turn (Smart Turn v3) → Evokai agent → TTS → WebRTC out

Interruptions are Pipecat's: the user turn processor interrupts the bot when the user speaks over
it, the TTS and the output drop what was queued, and the agent cancels its turn.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import ErrorFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import IceServer, SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from evokai.audio.voice.agent import EvokaiAgentProcessor
from evokai.audio.voice.providers import (
    close_service,
    make_stt,
    make_tts,
    stt_caps,
    tts_caps,
    voice_model,
)
from evokai.core.errors import AppError, Forbidden, NotFound
from evokai.i18n import speech_language
from evokai.identity.models import SessionPrincipal
from evokai.sessions.threads import HistoryMessage

if TYPE_CHECKING:
    from evokai.services import Services

log = logging.getLogger("evokai.voice")


def agent_chain(
    svc: Services,
    principal: SessionPrincipal,
    *,
    stt: STTService,
    tts: TTSService,
    external: bool,
    history: list[HistoryMessage] | None = None,
    app: Any = None,
    speech_tags: bool = False,
) -> list[FrameProcessor]:
    """STT → agent → TTS: the part of a voice pipeline that does not depend on the transport.

    Args:
        svc: Services.
        principal: The session.
        stt: Speech-to-text.
        tts: Text-to-speech.
        external: The application keeps the conversation.
        history: External storage: the conversation so far.
        app: The application (locale and messages).
        speech_tags: The TTS performs speech tags: segments go to it in their ``tagged`` form.

    Returns:
        The processors, in order.
    """

    def from_tts(frame: ErrorFrame) -> bool:
        return frame.processor is tts

    agent = EvokaiAgentProcessor(
        engine=svc.engine,
        principal=principal,
        history=history,
        external=external,
        is_speech_error=from_tts,
        app=app,
        speech_tags=speech_tags,
    )
    return [stt, agent, tts]


def user_turns(smart_turn: bool) -> UserTurnProcessor:
    """When a user turn starts and ends.

    Args:
        smart_turn: End of turn by Smart Turn v3 (else by silence).

    Returns:
        The processor.
    """
    stop = (
        TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())
        if smart_turn
        else SpeechTimeoutUserTurnStopStrategy()
    )
    return UserTurnProcessor(
        user_turn_strategies=UserTurnStrategies(
            start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
            stop=[stop],
        )
    )


class VoiceServer:
    """WebRTC voice sessions in this process."""

    def __init__(self, svc: Services) -> None:
        """Create the server.

        Args:
            svc: Services.
        """
        self.svc = svc
        ice = [IceServer(urls=u) for u in svc.settings.voice_ice_servers] or None
        self.handler = SmallWebRTCRequestHandler(ice_servers=ice)
        self._owners: dict[str, tuple[str, str, str]] = {}
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def _owner(principal: SessionPrincipal) -> tuple[str, str, str]:
        c = principal.claims
        return (c.application_id, c.subject, c.conversation_id)

    def _check(self, principal: SessionPrincipal, pc_id: str | None) -> None:
        if pc_id is None:
            return
        owner = self._owners.get(pc_id)
        if owner is None:
            raise NotFound("Voice connection not found.")
        if owner != self._owner(principal):
            raise Forbidden("This voice connection belongs to another session.")

    async def offer(
        self,
        principal: SessionPrincipal,
        *,
        sdp: str,
        type_: str,
        pc_id: str | None = None,
        restart_pc: bool = False,
        history: list[HistoryMessage] | None = None,
    ) -> dict[str, Any]:
        """Answer a WebRTC offer, starting the session's pipeline on a new connection.

        Args:
            principal: The session.
            sdp: The offer.
            type_: SDP type.
            pc_id: An existing connection to renegotiate (must be this session's).
            restart_pc: Restart ICE.
            history: External storage: the conversation so far.

        Returns:
            The answer (``sdp``, ``type``, ``pc_id``).

        Raises:
            RuntimeError: The connection produced no answer.
        """
        self._check(principal, pc_id)

        async def started(connection: SmallWebRTCConnection) -> None:  # ruff: ignore[unused-async] (Pipecat awaits it)
            task = asyncio.create_task(self._run(connection, principal, history))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

        answer = await self.handler.handle_web_request(
            SmallWebRTCRequest(sdp=sdp, type=type_, pc_id=pc_id, restart_pc=restart_pc),
            started,
        )
        if answer is None:
            raise RuntimeError("no SDP answer")
        self._owners[answer["pc_id"]] = self._owner(principal)
        return answer

    async def candidates(
        self, principal: SessionPrincipal, pc_id: str, candidates: list[dict[str, Any]]
    ) -> None:
        """Add trickled ICE candidates to this session's connection.

        Args:
            principal: The session.
            pc_id: The connection.
            candidates: ``candidate``, ``sdp_mid``, ``sdp_mline_index``.
        """
        self._check(principal, pc_id)
        await self.handler.handle_patch_request(
            SmallWebRTCPatchRequest(pc_id=pc_id, candidates=[IceCandidate(**c) for c in candidates])
        )

    async def _run(
        self,
        connection: SmallWebRTCConnection,
        principal: SessionPrincipal,
        history: list[HistoryMessage] | None,
    ) -> None:
        settings = self.svc.settings
        app = await self.svc.store.applications.get(principal.claims.application_id)
        sess = await self.svc.store.sessions.get(principal.who, principal.claims.conversation_id)
        storage = (sess.storage_mode if sess else None) or (app.storage_mode if app else None)
        external = storage == "external"
        transport = SmallWebRTCTransport(
            connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
        )
        c = principal.claims
        stt_name = c.speech_stt or settings.voice_stt
        tts_name = c.speech_tts or settings.voice_tts
        language = speech_language(app, sess)
        stt = make_stt(stt_caps(stt_name).realtime or stt_name, language=language)
        voice = await self._voice(principal, sess)
        record = await self.svc.store.voices.get(tts_name, voice) if voice else None
        tuning = record.settings if record else None
        own = {"settings": tuning} if tuning else {}
        tts = make_tts(tts_name, voice=voice, language=language, **own)
        chain = agent_chain(
            self.svc,
            principal,
            stt=stt,
            tts=tts,
            external=external,
            history=history,
            app=app,
            speech_tags=tts_caps(tts_name, voice_model(tts_name, tuning)).speech_tags,
        )
        worker = PipelineWorker(
            Pipeline([
                transport.input(),
                VADProcessor(vad_analyzer=SileroVADAnalyzer()),
                chain[0],
                user_turns(settings.voice_smart_turn),
                *chain[1:],
                transport.output(),
            ]),
            enable_rtvi=True,
        )

        @transport.event_handler("on_client_disconnected")
        async def _gone(_transport: Any, _client: Any) -> None:
            await worker.cancel(reason="disconnected")

        log.info("voice_session", extra={"conversationId": principal.claims.conversation_id})
        try:
            runner = WorkerRunner(handle_sigint=False)
            await runner.add_workers(worker)
            await runner.run()
        finally:
            self._owners.pop(connection.pc_id, None)
            await close_service(tts)
            await close_service(stt)

    async def _voice(self, principal: SessionPrincipal, sess: Any) -> str | None:
        """The session's voice: the token's, else its agent's ``voice.voice_id``, else the engine's.

        Returns:
            The voice, or None for the engine's default.
        """
        c = principal.claims
        if c.voice:
            return c.voice
        try:
            agent = await self.svc.agents.active(
                c.application_id, sess.agent_name if sess else c.agent
            )
        except AppError:
            return None
        return agent.voice.voice_id or None

    async def close(self) -> None:
        """Close every connection."""
        for task in list(self._tasks):
            task.cancel()
        await self.handler.close()
