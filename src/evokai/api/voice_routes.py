"""Voice sessions over WebRTC (mounted only when voice is enabled).

The browser (Pipecat's JS client with the SmallWebRTC transport) posts its offer with the session
token; the answer comes back and the session's voice pipeline starts on that connection. Events
travel as RTVI server messages ``{"evokai": event}``, the same events as the text stream.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from evokai.api.deps import services, session
from evokai.core.errors import Conflict, Forbidden, NotFound
from evokai.grants import NotGranted, grants_of
from evokai.identity.models import SessionPrincipal
from evokai.protection import processor_allows, protection
from evokai.services import Services
from evokai.sessions.threads import MAX_HISTORY_MESSAGES, HistoryMessage

router = APIRouter(prefix="/v1/session/voice", tags=["voice"])
Svc = Annotated[Services, Depends(services)]
Sess = Annotated[SessionPrincipal, Depends(session)]


class OfferData(BaseModel):
    """What a client may attach to its offer."""

    history: list[HistoryMessage] | None = Field(default=None, max_length=MAX_HISTORY_MESSAGES)


class Offer(BaseModel):
    """A WebRTC offer (or a renegotiation of this session's connection)."""

    sdp: str = Field(max_length=100_000)
    type: str = Field(max_length=16)
    pc_id: str | None = Field(default=None, max_length=128)
    restart_pc: bool = False
    history: list[HistoryMessage] | None = Field(default=None, max_length=MAX_HISTORY_MESSAGES)
    """External storage: the conversation so far."""
    requestData: OfferData | None = None
    """Extra data as Pipecat's JS transport sends it (``history`` may come here instead)."""


class Answer(BaseModel):
    """The WebRTC answer."""

    sdp: str
    type: str
    pc_id: str


class Candidate(BaseModel):
    """A trickled ICE candidate."""

    candidate: str = Field(max_length=2_000)
    sdp_mid: str = Field(max_length=64)
    sdp_mline_index: int


class Candidates(BaseModel):
    """ICE candidates for this session's connection."""

    pc_id: str = Field(max_length=128)
    candidates: list[Candidate] = Field(max_length=50)


async def _server(svc: Services, s: SessionPrincipal) -> Any:
    if s.claims.voice_engine != "server":
        raise Conflict("This session uses browser voice.", code="voice_engine")
    app = await svc.store.applications.get(s.claims.application_id)
    if app is None:
        raise NotFound()
    grants = await grants_of(svc.store, app)
    if not grants.voice("server"):
        raise NotGranted("voice_engine", "Server speech is not granted to this application.")
    if not grants.stt(svc.settings.voice_stt):
        raise NotGranted(
            "stt", f"The speech-to-text engine {svc.settings.voice_stt} is not granted."
        )
    if s.claims.voice and not grants.tts("server", s.claims.voice):
        raise NotGranted("voice", f"The voice {s.claims.voice} is not granted for server speech.")
    for kind, engine in (("stt", svc.settings.voice_stt), ("tts", svc.settings.voice_tts)):
        if not processor_allows(protection(app), f"{kind}:{engine}"):
            raise Forbidden(
                f"The {kind} engine {engine} is outside the application's data protection profile.",
                code="data_protection",
            )
    if svc.voice is None:
        from evokai.audio.voice.pipeline import VoiceServer

        svc.voice = VoiceServer(svc)
    return svc.voice


@router.post("/offer")
async def offer(body: Offer, svc: Svc, s: Sess) -> Answer:
    """Answer an offer; a new connection starts the session's voice pipeline.

    Returns:
        The SDP answer and the connection id.
    """
    answer = await (await _server(svc, s)).offer(
        s,
        sdp=body.sdp,
        type_=body.type,
        pc_id=body.pc_id,
        restart_pc=body.restart_pc,
        history=body.history or (body.requestData.history if body.requestData else None),
    )
    return Answer.model_validate(answer)


@router.patch("/offer", status_code=204)
async def candidates(body: Candidates, svc: Svc, s: Sess) -> None:
    """Add ICE candidates to this session's connection."""
    await (await _server(svc, s)).candidates(
        s, body.pc_id, [c.model_dump() for c in body.candidates]
    )
