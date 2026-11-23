"""Server speech for a session: synthesis with timings, and recognition (mounted with voice).

- ``POST /v1/session/speech`` speaks a text or one ``say`` segment of the conversation and streams
  Server-Sent Events: ``start``, ``audio`` (base64 PCM or Ogg Opus), ``word``, ``mark``,
  ``viseme``, ``done`` (or ``error``).
- ``POST /v1/session/transcribe`` transcribes an audio file sent as the request body.
- ``WS /v1/session/transcribe/stream`` transcribes the microphone as it speaks.

The engines are the session's (token ``speech``), else the deployment's defaults; the application's
grants and data protection profile apply, and so do its caps: characters synthesised and seconds
transcribed are recorded as usage at the catalog's prices (browsers only ever see shares).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from evokai.api.deps import (
    SESSION_REQUESTS_PER_MINUTE,
    origin_allowed,
    same_origin,
    services,
    session,
)
from evokai.audio.choice import check_speech, speech_usd
from evokai.audio.speech.markup import markup_of
from evokai.audio.speech.numbers import spell_numbers
from evokai.audio.speech.speakable import speakable
from evokai.core.errors import AppError, BadRequest, NotFound
from evokai.core.util import utcnow
from evokai.grants import grants_of
from evokai.guard import identifiers
from evokai.i18n import locale_of, speech_rules
from evokai.identity.models import SessionPrincipal
from evokai.services import Services
from evokai.sessions.limits import cap_state, limit_message

log = logging.getLogger("evokai.speech")

router = APIRouter(prefix="/v1/session", tags=["voice"])
Svc = Annotated[Services, Depends(services)]
Sess = Annotated[SessionPrincipal, Depends(session)]

STREAM_RATES = (8_000, 16_000)
"""Sample rates of the streaming socket (those of its voice activity detection)."""


class SpeechRequest(BaseModel):
    """What to speak: a text, or a ``say`` segment of this conversation."""

    text: str | None = Field(default=None, max_length=20_000)
    """Markdown or plain text (made speakable and redacted like an answer)."""
    turnId: str | None = Field(default=None, max_length=64)
    index: int | None = Field(default=None, ge=0, le=100_000)
    """The ``say`` segment's index within ``turnId``."""
    format: Literal["pcm", "opus"] = "pcm"
    """``pcm``: 16-bit little-endian mono at ``sampleRate``; ``opus``: Ogg Opus, 48 kHz."""
    sampleRate: Literal[16_000, 22_050, 24_000, 44_100, 48_000] = 24_000


class Transcript(BaseModel):
    """What was heard."""

    text: str
    durationMs: int
    engine: str


async def _context(svc: Services, s: SessionPrincipal) -> tuple[Any, Any, Any, Any]:
    c = s.claims
    app = await svc.store.applications.get(c.application_id)
    if app is None:
        raise NotFound()
    sess = await svc.store.sessions.get(s.who, c.conversation_id)
    grants = await grants_of(svc.store, app)
    try:
        agent = await svc.agents.active(app.id, sess.agent_name if sess else c.agent)
    except AppError:
        agent = None
    return app, sess, grants, agent


async def _cap(svc: Services, app: Any, s: SessionPrincipal, sess: Any, usd: float) -> None:
    state = await cap_state(svc.store, app, s.who, utcnow(), s.claims.limits)
    hit = state.hit(usd)
    if hit is not None:
        raise AppError(limit_message(app, hit, session=sess), code="limit", status=429)


async def _record(
    svc: Services, s: SessionPrincipal, sess: Any, agent: Any, kind: str, engine: str, **fields: Any
) -> None:
    try:
        await svc.store.usage.record(
            s.who,
            s.claims.tags,
            kind=kind,
            model=f"{kind}:{engine}",
            session_id=sess.id if sess else None,
            agent_name=agent.name if agent else "",
            **fields,
        )
    except Exception:
        log.exception("speech_usage_failed")


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


async def _say(svc: Services, app: Any, sess: Any, turn_id: str, index: int) -> dict[str, Any]:
    if sess is None:
        raise NotFound("Segment not found.")
    storage = sess.storage_mode or app.storage_mode
    channel = await svc.hub.get(sess.id, persist=storage == "evokai")
    for e in reversed(await channel.replay(0)):
        if e.get("type") == "say" and e.get("turnId") == turn_id and e.get("index") == index:
            return e
    raise NotFound("Segment not found.")


@router.post(
    "/speech",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Audio and timings"}},
)
async def speech(body: SpeechRequest, svc: Svc, s: Sess) -> StreamingResponse:
    """Speak a text or a ``say`` segment with the session's speech engine.

    A segment that is not in this conversation is 404.

    Returns:
        The event stream (``start``, ``audio``, ``word``, ``mark``, ``viseme``, ``done``).

    Raises:
        BadRequest: Neither a text nor a segment, or a text over ``EVOKAI_SPEECH_MAX_CHARS``.
    """
    st = svc.settings
    app, sess, grants, agent = await _context(svc, s)
    engine = s.claims.speech_tts or st.voice_tts
    check_speech(st, app, grants, tts=engine, voice=s.claims.voice)
    if body.turnId is not None and body.index is not None:
        e = await _say(svc, app, sess, body.turnId, body.index)
        text = e["text"]
        spoken = e.get("spoken") or text
        tagged, marks = e.get("tagged"), list(e.get("marks") or [])
    elif body.text:
        if len(body.text) > st.speech_max_chars:
            raise BadRequest(
                f"A text to speak has at most {st.speech_max_chars} characters.",
                code="text_too_long",
            )
        rules = speech_rules(locale_of(app, sess), identifiers.of_app(app))
        markup = markup_of(
            (app.defaults or {}).get("markup"), agent.markup if agent else None, sess
        )
        if markup is not None:
            f = markup.forms(body.text, rules)
            text, spoken, tagged, marks = f.text, f.spoken, f.tagged, f.marks
        else:
            text = speakable(body.text, rules)
            spoken, tagged, marks = spell_numbers(text, rules.locale), None, []
    else:
        raise BadRequest("Send a text, or a turnId and an index.", code="speech_input")

    from evokai.audio.voice.providers import tts_caps

    caps = tts_caps(engine)
    said = tagged if caps.speech_tags and tagged else spoken
    await _cap(svc, app, s, sess, speech_usd(f"tts:{engine}", characters=len(said)))
    voice = s.claims.voice or (agent.voice.voice_id if agent else "") or None

    async def stream() -> AsyncIterator[bytes]:
        from evokai.audio.voice.oneshot import Aligner, OpusEncoder, estimate, synthesize
        from evokai.audio.voice.providers import make_tts

        rate = 48_000 if body.format == "opus" else body.sampleRate
        yield _sse("start", {
            "engine": engine, "voice": voice, "format": body.format, "sampleRate": rate,
            "channels": 1, "text": text, "spoken": spoken,
        })  # fmt: skip
        aligner = Aligner(spoken, marks)
        samples = 0
        failed = False
        asked = False
        if said.strip():
            try:
                service = make_tts(engine, voice=voice, sample_rate=body.sampleRate)
            except Exception as e:
                log.warning(
                    "speech_engine_failed", extra={"engine": engine, "error": type(e).__name__}
                )
                yield _sse(
                    "error",
                    {"code": "tts_unavailable", "message": "The speech engine is not available."},
                )
                return
            encoder = OpusEncoder(body.sampleRate) if body.format == "opus" else None
            asked = True
            try:
                async for p in synthesize(
                    service, said, sample_rate=body.sampleRate, timeout=st.speech_timeout_seconds
                ):
                    if p.kind == "audio":
                        ms = round(samples / body.sampleRate * 1000)
                        samples += len(p.audio) // 2
                        data = encoder.push(p.audio) if encoder else p.audio
                        if data:
                            yield _sse("audio", {"data": base64.b64encode(data).decode(), "ms": ms})
                    elif p.kind == "word" and caps.words:
                        for kind, event in aligner.add(p.word, p.ms):
                            yield _sse(kind, event)
                    elif p.kind == "viseme":
                        yield _sse("viseme", {"id": p.viseme, "time": round(p.ms)})
                    elif p.kind == "error":
                        failed = True
                        break
                if encoder:
                    tail = encoder.close()
                    if tail:
                        yield _sse("audio", {"data": base64.b64encode(tail).decode(), "ms": None})
            finally:
                # Once the engine was asked, the characters count, even if the client went away
                # (an aborted request must not get speech for free); an engine that failed
                # without audio is not charged.
                if asked and (samples or not failed):
                    usd = speech_usd(f"tts:{engine}", characters=len(said))
                    await asyncio.shield(
                        _record(svc, s, sess, agent, "tts", engine, characters=len(said),
                                cost_micro_usd=round(usd * 1e6))
                    )  # fmt: skip
        duration = round(samples / body.sampleRate * 1000)
        if failed:
            yield _sse("error", {"code": "tts_failed", "message": "The speech engine failed."})
            return
        if not caps.words:
            for word, ms in estimate(spoken, duration):
                for kind, event in aligner.add(word, ms):
                    yield _sse(kind, event)
        for kind, event in aligner.finish(duration):
            yield _sse(kind, event)
        yield _sse(
            "done", {"durationMs": duration, "characters": len(said), "estimated": not caps.words}
        )

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
    )


@router.post(
    "/transcribe",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"audio/*": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def transcribe(
    request: Request,
    svc: Svc,
    s: Sess,
    language: Annotated[str | None, Query(max_length=35)] = None,
) -> Transcript:
    """Transcribe an audio file (WAV, WebM, Ogg, MP3, M4A) sent as the request body.

    Returns:
        The text, the audio's length and the engine.

    Raises:
        AppError: Larger than ``EVOKAI_TRANSCRIBE_MAX_BYTES`` (413), or the engine failed (502).
        BadRequest: Not audio, empty, or longer than ``EVOKAI_TRANSCRIBE_MAX_SECONDS``.
    """
    st = svc.settings
    too_big = AppError(
        f"An audio file has at most {st.transcribe_max_bytes} bytes.",
        code="audio_too_large",
        status=413,
    )
    if int(request.headers.get("content-length") or 0) > st.transcribe_max_bytes:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > st.transcribe_max_bytes:
            raise too_big
    app, sess, grants, agent = await _context(svc, s)
    engine = s.claims.speech_stt or st.voice_stt
    check_speech(st, app, grants, stt=engine)

    from evokai.audio.voice.oneshot import decode_audio
    from evokai.audio.voice.oneshot import transcribe as run
    from evokai.audio.voice.providers import make_stt, stt_caps

    try:
        pcm = await asyncio.to_thread(decode_audio, bytes(data), 16_000, st.transcribe_max_seconds)
    except ValueError as e:
        if str(e) == "too long":
            raise BadRequest(
                f"An audio file lasts at most {st.transcribe_max_seconds} seconds.",
                code="audio_too_long",
            ) from e
        raise BadRequest(
            "The body is not an audio file this server can read.", code="audio_invalid"
        ) from e
    ms = round(len(pcm) / 2 / 16)
    if ms > st.transcribe_max_seconds * 1000:
        raise BadRequest(
            f"An audio file lasts at most {st.transcribe_max_seconds} seconds.",
            code="audio_too_long",
        )
    if not pcm:
        raise BadRequest("The audio is empty.", code="audio_invalid")
    usd = speech_usd(f"stt:{engine}", audio_ms=ms)
    await _cap(svc, app, s, sess, usd)
    lang = language or pack_tag(locale_of(app, sess))
    service = make_stt(engine, language=lang, sample_rate=16_000)
    heard = await run(service, pcm, sample_rate=16_000, segmented=stt_caps(engine).segmented)
    await _record(svc, s, sess, agent, "stt", engine, audio_ms=ms, cost_micro_usd=round(usd * 1e6))
    if heard.error and not heard.finals:
        raise AppError("The speech-to-text engine failed.", code="stt_failed", status=502)
    return Transcript(text=heard.text, durationMs=ms, engine=engine)


def pack_tag(locale: str) -> str:
    """The BCP 47 language of a pack (``it`` → ``it-IT``), for engines that want a region.

    Returns:
        The tag.
    """
    regions = {"en": "US", "it": "IT", "es": "ES", "fr": "FR", "de": "DE", "ro": "RO", "sv": "SE"}
    return f"{locale}-{regions[locale]}" if locale in regions else locale


# ── streaming recognition ───────────────────────────────────────────────────────────────────────

_TOKEN_PROTOCOL = "evokai.token."  # ruff: ignore[hardcoded-password-string] (a subprotocol prefix)
PROTOCOL = "evokai.v1"
"""The subprotocol the server accepts; the client also offers ``evokai.token.<session token>``."""
STREAM_MARGIN_SECONDS = 10.0
"""How long a streaming socket may stay open beyond its maximum length of audio (start-up, the
engine's last words): its wall-clock deadline is this plus ``EVOKAI_TRANSCRIBE_STREAM_MAX_SECONDS``,
or the session token's expiry when that comes first."""
_wall = time.time
"""The wall clock the token's expiry is read against (replaced in tests)."""
_ENDS = {
    "idle": (4408, "No audio was received for a while: the stream is closed."),
    "too_long": (4408, "The stream reached its maximum length."),
    "expired": (4401, "The session token expired: the stream is closed."),
}
"""Why a stream ended early: its close code and the message of its last ``error``."""


class _Refused(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


async def _ws_principal(svc: Services, ws: WebSocket) -> SessionPrincipal:
    if any(k in ws.query_params for k in ("token", "access_token", "authorization")):
        raise _Refused(4400, "token in the query string")
    offered = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",")]
    token = next(
        (p[len(_TOKEN_PROTOCOL) :] for p in offered if p.startswith(_TOKEN_PROTOCOL)), None
    )
    if not token or PROTOCOL not in offered:
        raise _Refused(4401, "unauthorized")
    try:
        principal = await svc.auth.session(token)
    except AppError as e:
        raise _Refused(4401, "unauthorized") from e
    if not await svc.auth.count(
        f"token:{principal.claims.token_id}", SESSION_REQUESTS_PER_MINUTE, 60
    ):
        raise _Refused(4429, "rate_limited")
    origin = ws.headers.get("origin")
    if not (origin and same_origin(ws, origin)) and not origin_allowed(origin, principal.origins):
        raise _Refused(4403, "origin")
    return principal


async def _refuse(ws: WebSocket, code: int, reason: str) -> None:
    """Accept, then close with a code.

    A socket closed before it is accepted reaches a browser as a failed handshake (1006), without
    the code or the reason.
    """
    offered = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",")]
    await ws.accept(subprotocol=PROTOCOL if PROTOCOL in offered else None)
    await ws.close(code=code, reason=reason)


@router.websocket("/transcribe/stream")
async def transcribe_stream(ws: WebSocket) -> None:
    """Transcribe audio as it is spoken.

    Authenticated by the session token in the subprotocol (``evokai.v1`` and
    ``evokai.token.<token>``), never in the query string; one socket per session. The client
    sends ``{"type": "start", "sampleRate": 16000, "language"?, "vad"?: {"confidence",
    "startSecs", "stopSecs", "minVolume"}}``, then binary 16-bit mono PCM, then
    ``{"type": "stop"}``; the server answers ``ready``, ``interim`` and ``final`` (``text``),
    ``end`` (``durationMs``) and ``error`` (``code``, ``message``).
    """
    svc: Services = ws.app.state.services
    st = svc.settings
    try:
        s = await _ws_principal(svc, ws)
        app, sess, grants, agent = await _context(svc, s)
        engine = s.claims.speech_stt or st.voice_stt
        check_speech(st, app, grants, stt=engine)
    except _Refused as r:
        await _refuse(ws, r.code, r.reason)
        return
    except AppError as e:
        await _refuse(ws, 4403, e.code)
        return
    key = (s.claims.application_id, s.claims.subject, s.claims.conversation_id)
    if key in svc.speech_streams:
        await _refuse(ws, 4409, "already streaming")
        return
    svc.speech_streams.add(key)
    try:
        await ws.accept(subprotocol=PROTOCOL)
        await _stream(svc, ws, s, app, sess, agent, engine)
    except WebSocketDisconnect:
        pass
    finally:
        svc.speech_streams.discard(key)


async def _stream(
    svc: Services, ws: WebSocket, s: SessionPrincipal, app: Any, sess: Any, agent: Any, engine: str
) -> None:
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        EndFrame,
        Frame,
        InputAudioRawFrame,
        InterimTranscriptionFrame,
        TranscriptionFrame,
    )
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.processors.frame_processor import FrameDirection

    from evokai.audio.voice.oneshot import Catch, run_pipeline
    from evokai.audio.voice.providers import close_service, make_stt, stt_caps

    st = svc.settings
    try:
        start = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        rate = int(start.get("sampleRate") or 16_000)
        vad = {k: float(v) for k, v in (start.get("vad") or {}).items()}
        params = VADParams(
            confidence=vad.get("confidence", 0.7),
            start_secs=vad.get("startSecs", 0.2),
            stop_secs=vad.get("stopSecs", 0.8),
            min_volume=vad.get("minVolume", 0.6),
        )
        language = str(start.get("language") or "")[:35] or pack_tag(locale_of(app, sess))
        if start.get("type") != "start" or rate not in STREAM_RATES:
            raise ValueError("start")
    except (TimeoutError, ValueError, TypeError, AttributeError):
        await ws.send_json({
            "type": "error",
            "code": "start",
            "message": "Send a start message first (sampleRate 8000 or 16000).",
        })
        await ws.close(code=4400)
        return
    usd_per_ms = speech_usd(f"stt:{engine}", audio_ms=1000) / 1000
    cap_ms = st.transcribe_stream_max_seconds * 1000
    caps = stt_caps(engine)
    try:
        await _cap(svc, app, s, sess, usd_per_ms * cap_ms)
        service = make_stt(caps.realtime or engine, language=language, sample_rate=rate)
    except AppError as e:
        await ws.send_json({"type": "error", "code": e.code, "message": e.message})
        await ws.close(code=4429 if e.code == "limit" else 4500)
        return
    except Exception as e:
        log.warning("speech_engine_failed", extra={"engine": engine, "error": type(e).__name__})
        await ws.send_json({
            "type": "error",
            "code": "stt_unavailable",
            "message": "The speech-to-text engine is not available.",
        })
        await ws.close(code=4500)
        return
    out: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def put(frame: Frame, direction: FrameDirection) -> None:
        if direction != FrameDirection.DOWNSTREAM:
            return
        if isinstance(frame, TranscriptionFrame):
            out.put_nowait({"type": "final", "text": frame.text})
        elif isinstance(frame, InterimTranscriptionFrame):
            out.put_nowait({"type": "interim", "text": frame.text})

    async def send() -> None:
        while (msg := await out.get()) is not None:
            with contextlib.suppress(Exception):
                await ws.send_json(msg)

    vad_processor = VADProcessor(vad_analyzer=SileroVADAnalyzer(sample_rate=rate, params=params))
    worker, task = await run_pipeline([vad_processor, service, Catch(put)], rate)
    sender = asyncio.create_task(send())
    received = 0
    ended: str | None = None
    # A wall-clock deadline (counting audio bytes alone never ends a socket sent only text) and an
    # idle timer that only audio resets.
    loop = asyncio.get_running_loop()
    for_token = s.claims.expires_at - _wall()
    allowed = st.transcribe_stream_max_seconds + STREAM_MARGIN_SECONDS
    deadline = loop.time() + min(allowed, for_token)
    late = "expired" if for_token < allowed else "too_long"
    idle_at = loop.time() + st.transcribe_stream_idle_seconds
    await ws.send_json({"type": "ready", "engine": engine})
    try:
        while True:
            now = loop.time()
            if now >= deadline:
                ended = late
                break
            if now >= idle_at:
                ended = "idle"
                break
            try:
                msg = await asyncio.wait_for(ws.receive(), min(deadline, idle_at) - now)
            except TimeoutError:
                continue
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes"):
                chunk = msg["bytes"]
                received += len(chunk)
                if received / 2 / rate * 1000 > cap_ms:
                    ended = "too_long"
                    break
                idle_at = loop.time() + st.transcribe_stream_idle_seconds
                await worker.queue_frame(InputAudioRawFrame(chunk, rate, 1))
            elif msg.get("text"):
                with contextlib.suppress(ValueError):
                    sent = json.loads(msg["text"])
                    if isinstance(sent, dict) and sent.get("type") == "stop":
                        break
    finally:
        await worker.queue_frame(EndFrame())
        with contextlib.suppress(Exception):
            await asyncio.wait_for(task, 15)
        if not task.done():
            task.cancel()
        await close_service(service)
        out.put_nowait(None)
        with contextlib.suppress(Exception):
            await sender
        ms = round(received / 2 / rate * 1000)
        if ms:
            await asyncio.shield(
                _record(svc, s, sess, agent, "stt", engine, audio_ms=ms,
                        cost_micro_usd=round(usd_per_ms * ms * 1e6))
            )  # fmt: skip
    with contextlib.suppress(Exception):
        if ended is not None:
            code, message = _ENDS[ended]
            await ws.send_json({"type": "error", "code": ended, "message": message})
            await ws.close(code=code, reason=ended)
            return
        await ws.send_json({"type": "end", "durationMs": round(received / 2 / rate * 1000)})
        await ws.close()
