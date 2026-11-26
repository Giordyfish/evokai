"""Voice sessions on Pipecat, end to end with fake STT and TTS: STT → Evokai agent → TTS."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    SystemFrame,
    TTSAudioRawFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

from evokai.audio.voice.pipeline import agent_chain
from evokai.audio.voice.providers import FakeSTT, FakeTTS
from tests.conftest import ORIGIN, Env, Tool

SPEECH = b"\x01\x00" * 320
TURN_TIME = 0.6
START_TIMEOUT = 30.0


def _utterance() -> list[Frame]:
    """The user says something and stops."""
    return [
        InputAudioRawFrame(SPEECH, 16_000, 1),
        SleepFrame(0.05),
        UserStoppedSpeakingFrame(),
        SleepFrame(TURN_TIME),
    ]


@dataclass
class Until(SystemFrame):
    """Hold the frames that follow until ``event`` is set (instead of guessing a pause)."""

    event: asyncio.Event = field(default_factory=asyncio.Event)


class _Hold(FrameProcessor):
    """First in the test pipeline: an ``Until`` frame stops what follows until its event."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, Until):
            await asyncio.wait_for(frame.event.wait(), START_TIMEOUT)
            return
        await self.push_frame(frame, direction)


class _Done(FrameProcessor):
    """Last in the test pipeline: sets ``first`` when the agent reports its first finished turn."""

    def __init__(self) -> None:
        super().__init__()
        self.first = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, RTVIServerMessageFrame) and frame.data["evokai"]["type"] == "done":
            self.first.set()
        await self.push_frame(frame, direction)


async def _voice(
    env: Env,
    phrases: list[str],
    frames: list[Frame],
    *,
    tts: FakeTTS | None = None,
    done: _Done | None = None,
) -> tuple[list[dict[str, Any]], FakeTTS, list[Frame], str]:
    tok = await env.token()
    principal = await env.svc.auth.session(tok["token"])
    tts = tts or FakeTTS()
    chain = agent_chain(env.svc, principal, stt=FakeSTT(phrases), tts=tts, external=False)
    # Pipecat gives the pipeline 1 s to start; on a loaded CI runner (parallel workers, Postgres)
    # it can take longer, and the test failed before the voice turn even began.
    pipeline = Pipeline([_Hold(), *chain, done or _Done()])
    down, _ = await run_test(pipeline, frames_to_send=frames, start_timeout=START_TIMEOUT)
    events = [f.data["evokai"] for f in down if isinstance(f, RTVIServerMessageFrame)]
    return events, tts, list(down), tok["token"]


async def test_a_spoken_turn_is_an_evokai_turn_that_is_spoken_back(env: Env) -> None:
    env.script.add(
        "function:primary",
        [Tool("orders_month", {"period": "2026-10"})],
        ["Ottobre ha 24 ordini. ", "Vuoi vederli?"],
    )
    events, tts, down, token = await _voice(env, ["Com'è messo ottobre?"], _utterance())

    kinds = [e["type"] for e in events]
    assert kinds[0] == "conversation" and kinds[-1] == "done"
    assert {"tool", "text", "say", "usage"} <= set(kinds)
    assert any(isinstance(f, TTSAudioRawFrame) for f in down)
    assert "Ottobre ha 24 ordini." in tts.spoken
    assert env.mcp.calls == [("orders_month", {"period": "2026-10"})]

    r = await env.client.get(
        "/agent/v1/session/conversation",
        headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
    )
    texts = [(x["role"], x["text"]) for x in r.json()["messages"] if x["text"]]
    assert texts[0] == ("user", "Com'è messo ottobre?")
    assert texts[-1] == ("assistant", "Ottobre ha 24 ordini. Vuoi vederli?")


async def test_the_server_voice_speaks_numbers_in_words_and_shows_digits(env: Env) -> None:
    await env.svc.store.applications.update(env.app_id, defaults={"locale": "it"})
    env.script.add("function:primary", ["Sono 1.000 punti."])
    events, tts, _, _ = await _voice(env, ["Quanti punti?"], _utterance())

    assert "Sono mille punti." in tts.spoken
    assert "Sono 1.000 punti." not in tts.spoken
    say = next(e for e in events if e["type"] == "say")
    assert (say["text"], say["spoken"]) == ("Sono 1.000 punti.", "Sono mille punti.")


async def test_a_spoken_yes_confirms_the_pending_change(env: Env) -> None:
    env.script.add("function:primary", [Tool("order_refund", {})], ["Fatto: rimborsati."])
    # The "yes" comes once the first turn has asked: earlier, it would supersede that turn.
    done = _Done()
    frames = [*_utterance(), Until(done.first), *_utterance()]
    events, tts, _, _ = await _voice(env, ["Classify the rows", "yes, go ahead"], frames, done=done)

    reasons = [e["reason"] for e in events if e["type"] == "done"]
    assert reasons == ["confirm", "end_turn"]
    assert any(t.startswith("Before I go ahead, please confirm") for t in tts.spoken)
    assert env.mcp.calls == [("order_refund", {})]


async def test_speaking_over_the_agent_cancels_its_turn(env: Env) -> None:
    env.script.gate = asyncio.Event()
    env.script.add("function:primary", ["Sto guardando i dati del mese. ", "WAIT", "Ecco."])
    # The agent holds at its WAIT (the gate never opens) and the person talks over it once it is
    # there: a fixed pause raced slow runners, where the interruption came before the turn began.
    # The end of the session waits for the cancelled turn, so nothing follows the interruption.
    frames = [*_utterance()[:-1], Until(env.script.waiting), InterruptionFrame()]
    events, tts, _, _ = await _voice(env, ["Riassumi il mese"], frames)

    done = [e for e in events if e["type"] == "done"]
    assert [d["reason"] for d in done] == ["barge_in"]
    assert "Ecco." not in tts.spoken


async def test_a_failing_voice_degrades_to_text(env: Env) -> None:
    env.script.add("function:primary", ["Tutto a posto. ", "Nient'altro."])
    events, _, down, _ = await _voice(env, ["Novità?"], _utterance(), tts=FakeTTS(fail=True))

    off = [e for e in events if e["type"] == "speech"]
    assert off and off[0]["enabled"] is False
    assert not any(isinstance(f, TTSAudioRawFrame) for f in down)
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert text == "Tutto a posto. Nient'altro."


async def test_voice_routes_exist_only_when_enabled(env: Env) -> None:
    spec = (await env.client.get("/agent/openapi.json")).json()
    assert "/agent/v1/session/voice/offer" not in spec["paths"]


async def test_an_offer_gets_an_answer_and_connections_stay_with_their_session(env: Env) -> None:
    from aiortc import RTCPeerConnection

    from evokai.main import create_app

    settings = env.svc.settings.model_copy(
        update={"voice_enabled": True, "voice_smart_turn": False}
    )
    app = create_app(settings, env.svc)
    tok = (await env.token(voiceEngine="server"))["token"]
    other = (await env.token("u2", voiceEngine="server"))["token"]
    browser = (await env.token("u3"))["token"]
    pc = RTCPeerConnection()
    pc.addTransceiver("audio", direction="sendrecv")
    await pc.setLocalDescription(await pc.createOffer())
    offer = {"sdp": pc.localDescription.sdp, "type": "offer"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evokai"
    ) as client:

        async def post(token: str | None, body: dict[str, Any]) -> httpx.Response:
            headers = {"Origin": ORIGIN} | ({"Authorization": f"Bearer {token}"} if token else {})
            return await client.post("/agent/v1/session/voice/offer", json=body, headers=headers)

        assert (await post(None, offer)).status_code == 401
        refused = await post(browser, offer)
        assert (refused.status_code, refused.json()["error"]) == (409, "voice_engine")
        r = await post(tok, offer)
        assert r.status_code == 200, r.text
        answer = r.json()
        assert answer["type"] == "answer" and "a=fingerprint" in answer["sdp"]
        stolen = await post(other, {**offer, "pc_id": answer["pc_id"]})
        assert (stolen.status_code, stolen.json()["error"]) == (403, "forbidden")
        unknown = await post(tok, {**offer, "pc_id": "nope"})
        assert unknown.status_code == 404
    await pc.close()


async def test_an_offer_needs_server_voice_and_its_engine_granted(env: Env) -> None:
    from evokai.main import create_app

    app = create_app(env.svc.settings.model_copy(update={"voice_enabled": True}), env.svc)
    tok = (await env.token(voiceEngine="server", voice="elena"))["token"]
    offer = {"sdp": "v=0", "type": "offer"}
    headers = {"Origin": ORIGIN, "Authorization": f"Bearer {tok}"}

    async def grant(kind: str, name: str, value: Any) -> None:
        r = await env.client.patch(
            "/agent/v1/admin/grants",
            json={"changes": [{"kind": kind, "name": name, "value": value}]},
            headers={"Authorization": f"Bearer {env.admin_key}"},
        )
        assert r.status_code == 200

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evokai"
    ) as client:
        for kind, name, code in (
            ("voice", "server", "voice_engine_not_granted"),
            ("stt", "fake", "stt_not_granted"),
            ("tts", "server/elena", "voice_not_granted"),
        ):
            await grant(kind, name, False)
            r = await client.post("/agent/v1/session/voice/offer", json=offer, headers=headers)
            assert (r.status_code, r.json()["error"]) == (403, code)
            await grant(kind, name, None)


async def test_browser_voice_engine_is_the_default_and_the_application_can_change_it(
    env: Env,
) -> None:
    assert (await env.token())["voiceEngine"] == "browser"
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"voice_engine": "server"},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200
    assert (await env.token())["voiceEngine"] == "server"
    assert (await env.token(voiceEngine="browser"))["voiceEngine"] == "browser"


async def test_browser_voice_spoken_yes_confirms(env: Env) -> None:
    env.script.add("function:primary", [Tool("order_refund", {})], ["Fatto."])
    tok = (await env.token())["token"]
    first = await env.chat(tok, text="Rimborsa gli ordini", mode="voice")
    assert first[-1]["reason"] == "confirm"
    assert any(e["type"] == "say" for e in first)
    second = await env.chat(tok, text="Yes, go ahead", mode="voice")
    assert second[-1]["reason"] == "end_turn"
    assert env.mcp.calls == [("order_refund", {})]


async def test_browser_voice_spoken_option_answers_the_question(env: Env) -> None:
    ask = {"question": "Which month?", "options": ["September", "October"]}
    env.script.add("function:primary", [Tool("ui_ask", ask)], ["Ok, ottobre."])
    tok = (await env.token())["token"]
    first = await env.chat(tok, text="Fammi vedere un mese", mode="voice")
    assert first[-1]["reason"] == "ask"
    await env.chat(tok, text="the second one", mode="voice")
    _, messages = env.script.seen[-1]
    assert 'Answer to your question "Which month?": October' in str(messages)
