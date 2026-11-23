"""Attachments: files sent with a chat message, and how they come back in the history."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from evokai import AsyncEvokaiSession, Attachment, EvokaiSession, HistoryMessage
from evokai.models import FileBlock, SessionInfo
from tests.support import BASE, TOKEN, frame, sent_json, sse_response

DONE = [frame({"type": "done", "conversationId": "c1", "reason": "end_turn", "turnId": "t1"})]


def test_chat_sends_paths_bytes_and_attachments(router: respx.MockRouter, tmp_path: Path) -> None:
    route = router.post("/v1/session/chat").mock(return_value=sse_response(DONE)[0])
    photo = tmp_path / "me.png"
    photo.write_bytes(b"\x89PNG")
    with EvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        list(
            s.chat(
                "Look",
                attachments=[photo, ("note.txt", b"hello"), Attachment("cv.pdf", b"%PDF-")],
            )
        )
    assert sent_json(route)["attachments"] == [
        {"name": "me.png", "data": "iVBORw=="},
        {"name": "note.txt", "data": "aGVsbG8="},
        {"name": "cv.pdf", "data": "JVBERi0="},
    ]


async def test_async_chat_json_sends_attachments(router: respx.MockRouter) -> None:
    route = router.post("/v1/session/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "conversationId": "c1",
                "turnId": "t1",
                "reason": "end_turn",
                "text": "",
                "events": [],
            },
        )
    )
    async with AsyncEvokaiSession(BASE, TOKEN, replay_delay=0) as s:
        await s.chat_json("Look", attachments=[("a.txt", b"x")])
    assert sent_json(route)["attachments"] == [{"name": "a.txt", "data": "eA=="}]


def test_history_keeps_attachment_blocks() -> None:
    h = HistoryMessage.model_validate({
        "role": "user",
        "text": "Look",
        "content": [
            {"file": {"id": "a1", "name": "me.png", "mediaType": "image/png", "size": 4}},
            {
                "file": {"id": "a2", "name": "n.txt", "mediaType": "text/plain", "size": 1},
                "text": "x",
            },
            {"text": "Look"},
        ],
    })
    assert isinstance(h.content[0], FileBlock) and h.content[0].file.media_type == "image/png"
    assert isinstance(h.content[1], FileBlock) and h.content[1].text == "x"
    dumped = h.model_dump(by_alias=True, exclude_none=True)
    assert dumped["content"][0] == {
        "file": {"id": "a1", "name": "me.png", "mediaType": "image/png", "size": 4, "sha256": ""}
    }
    info = SessionInfo.model_validate({
        "conversationId": "c1",
        "agent": "a",
        "expiresAt": 1,
        "attachments": {
            "kinds": ["png"],
            "mediaTypes": ["image/png"],
            "maxFileBytes": 10,
            "maxFiles": 2,
        },
    })
    assert info.attachments is not None and info.attachments.max_files == 2
