"""OpenAI-compatible Chat Completions: any OpenAI client can talk to an Evokai agent.

``POST /v1/openai/chat/completions`` and ``GET /v1/openai/models``. The model is the agent's name.

- **Auth**: the application's server key (the ``user`` field is the subject, default
  ``system:<agent>``), or a session token (its subject, agent, forwarded credential and caps; the
  request must come from an allowed origin, like every session call).
- **Semantics of a server run** (``evokai.sessions.runs``): the agent's read-only tools run on the
  server and the client gets the final answer; tools that change data are not offered, since nobody
  could confirm them, unless the application is granted ``openai:confirm`` and the agent turns
  ``openai_confirm`` on (``evokai.api.openai_confirm``: the change waits for a yes in the next
  request). Client tools (``tools`` in the request) are not supported. The application's
  grants, its security policy (guard), its caps and its abuse limits apply exactly as to a run.
- **Stateless**: the client sends the conversation (``messages``) every time and nothing is stored.
  ``system`` and ``developer`` messages are ignored: the agent's own instructions are the system
  prompt. Sampling parameters (``temperature``, ``max_tokens``...) are the agent's, not the
  request's.
- **Usage** is returned in tokens, never as an amount. A session-token caller also gets its usage
  shares (``evokai.usage``: percentages of its caps).
- **Errors** use OpenAI's shape: ``{"error": {"message", "type", "code", "param"}}``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from evokai.api import deps, openai_confirm
from evokai.api.deps import services
from evokai.core.errors import AppError, BadRequest, Forbidden, NotFound, Unauthorized
from evokai.core.security import parse_key
from evokai.core.util import new_id
from evokai.db import models as m
from evokai.grants import grants_of
from evokai.identity.auth import bearer
from evokai.identity.models import SessionPrincipal
from evokai.llm.catalog import bare
from evokai.services import Services
from evokai.sessions.limits import cap_state
from evokai.sessions.runs import AgentRunner, Resume, RunChannel, RunResult, check_channel

log = logging.getLogger("evokai.openai")

router = APIRouter(prefix="/v1/openai", tags=["openai"])
Svc = Annotated[Services, Depends(services)]

MAX_MESSAGES = 200
ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "invalid_request_error",
    429: "rate_limit_error",
}


def error_body(status: int, code: str, message: str) -> dict[str, Any]:
    """An error in OpenAI's shape (used by the application's error handlers for this prefix).

    Returns:
        ``{"error": {"message", "type", "code", "param"}}``.
    """
    return {
        "error": {
            "message": message,
            "type": ERROR_TYPES.get(status, "api_error"),
            "code": code,
            "param": None,
        }
    }


@dataclass
class Caller:
    """Who calls: an application with its server key, or a browser session."""

    app: m.Application
    session: SessionPrincipal | None = None

    @property
    def channel(self) -> RunChannel:
        """``browser`` for a session token, else ``server``."""
        return "browser" if self.session else "server"


async def caller(request: Request) -> Caller:
    """The caller of an OpenAI-compatible route.

    Returns:
        The caller.

    Raises:
        Unauthorized: No key or token, or an invalid one.
    """
    svc = services(request)
    token = bearer(request.headers.get("authorization"))
    if not token:
        raise Unauthorized("Give a server key or a session token as a bearer token.")
    if parse_key(token):
        principal = await svc.auth.application(token)
        app = await svc.store.applications.get(principal.application_id)
        if app is None:
            raise Unauthorized()
        return Caller(app=app)
    s = await deps.session(request)
    app = await svc.store.applications.get(s.claims.application_id)
    if app is None:
        raise Unauthorized()
    return Caller(app=app, session=s)


Call = Annotated[Caller, Depends(caller)]


class ContentPart(BaseModel):
    """A part of a message's content (only text is supported)."""

    model_config = ConfigDict(extra="allow")

    type: str
    text: str | None = None


class ChatMessage(BaseModel):
    """A message of the conversation."""

    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[ContentPart] | None = None
    name: str | None = None


class StreamOptions(BaseModel):
    """Streaming options."""

    model_config = ConfigDict(extra="allow")

    include_usage: bool = False


class ConfirmAnswer(BaseModel):
    """A button's answer to a pending confirmation (instead of typing yes or no)."""

    actionId: str = Field(max_length=200)
    approve: bool


class EvokaiOptions(BaseModel):
    """Evokai's extension of the request (``tags`` and ``groups``: server key only)."""

    tags: list[str] = Field(default_factory=list, max_length=20)
    """Usage tags of the run."""
    groups: list[str] = Field(default_factory=list, max_length=50)
    """The subject's groups, for knowledge bases restricted to some groups."""
    confirm: ConfirmAnswer | None = None
    """The answer to the confirmation the last assistant message asked for (``openai:confirm``):
    the message must still be sent unmodified."""


class ChatCompletionRequest(BaseModel):
    """An OpenAI Chat Completions request; unknown fields are accepted and ignored."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(max_length=200)
    """The agent's name."""
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    stream: bool = False
    stream_options: StreamOptions | None = None
    user: str | None = Field(default=None, max_length=200)
    """The subject (server key only; a session token has its own)."""
    n: int | None = None
    tools: list[dict[str, Any]] | None = None
    evokai: EvokaiOptions | None = None


def _text(msg: ChatMessage) -> str:
    """A message's text.

    Returns:
        The text.

    Raises:
        BadRequest: A part that is not text.
    """
    if msg.content is None:
        return ""
    if isinstance(msg.content, str):
        return msg.content
    out = []
    for p in msg.content:
        if p.type != "text" or p.text is None:
            raise BadRequest("Only text content is supported.", code="unsupported_content")
        out.append(p.text)
    return "\n".join(out)


def _input(body: ChatCompletionRequest) -> tuple[str | None, list[dict[str, Any]] | None]:
    """The run's input: one text, or the conversation of user and assistant messages.

    Returns:
        (text, messages), one of them set.

    Raises:
        BadRequest: No user message last, client tools, or ``n`` above 1.
    """
    if body.tools:
        raise BadRequest(
            "Client tools are not supported: the agent's own tools run on the server.",
            code="tools_not_supported",
        )
    if body.n not in {None, 1}:
        raise BadRequest("Only one choice (n=1) is supported.", code="n_not_supported")
    convo = [
        {"role": x.role, "text": _text(x)}
        for x in body.messages
        if x.role in {"user", "assistant"} and (x.content is not None)
    ]
    convo = [x for x in convo if x["text"]]
    if not convo or convo[-1]["role"] != "user":
        raise BadRequest("The last message must be the user's.", code="empty")
    if len(convo) == 1:
        return convo[0]["text"], None
    return None, convo


@dataclass
class _Prepared:
    """A request ready to run."""

    caller: Caller
    agent: Any
    text: str | None
    messages: list[dict[str, Any]] | None
    subject: str
    tags: list[str]
    groups: list[str]
    confirm: bool = False
    """Changes may wait for a yes (``openai:confirm`` and the agent's ``openai_confirm``)."""
    resume: Resume | None = None
    """The person's answer to a pending change, being carried out."""
    convo: list[dict[str, Any]] | None = None
    """The request's whole conversation (a question the run asks follows it)."""


async def _prepare(svc: Services, who: Caller, body: ChatCompletionRequest) -> _Prepared:
    """Check what can be checked before anything runs (so a refusal is an HTTP error).

    Returns:
        The prepared run.

    Raises:
        Forbidden: A session token for another agent.
        NotFound: An unknown agent, or one this caller cannot reach.
    """
    text, messages = _input(body)
    s = who.session
    if s is not None and body.model != s.claims.agent:
        raise Forbidden("This session token is for another agent.", code="agent")
    agent = await svc.agents.active(who.app.id, body.model)
    grants = await grants_of(svc.store, who.app)
    try:
        check_channel(agent, grants, who.channel)
    except Forbidden as e:
        raise NotFound(f"Unknown model: {body.model}", code="model_not_found") from e
    ext = body.evokai or EvokaiOptions()
    if s is not None:
        claims = s.claims
        p = _Prepared(
            who, agent, text, messages, claims.subject, list(claims.tags), list(claims.groups)
        )
    else:
        subject = body.user or f"system:{agent.name}"
        p = _Prepared(who, agent, text, messages, subject, ext.tags, ext.groups)
    p.convo = openai_confirm.conversation(text, messages)
    named = s is not None or bool(body.user)
    if openai_confirm.allowed(grants, agent, named=named):
        p.confirm = True
        explicit = ext.confirm.model_dump() if ext.confirm else None
        decision = await openai_confirm.decide(
            svc.store, who.app, agent, p.subject, messages, explicit
        )
        if decision is not None:
            p.resume, p.text, p.messages = decision.resume, decision.text, decision.messages
    return p


async def _run(svc: Services, p: _Prepared, on_event: Any = None, on_text: Any = None) -> RunResult:
    """Run the prepared request.

    Returns:
        The run's result.
    """
    s = p.caller.session
    app = p.caller.app
    return await AgentRunner(svc.engine).run(
        app,
        p.agent,
        text=p.text,
        messages=p.messages,
        attachments=[],
        subject=p.subject,
        tags=p.tags,
        channel=p.caller.channel,
        credential=await svc.engine._credential(s.claims) if s else None,
        limits=s.claims.limits if s else None,
        model=s.claims.model if s else None,
        on_event=on_event,
        groups=p.groups,
        on_text=on_text,
        confirm=p.confirm,
        resume=p.resume,
    )


def _content(result: RunResult) -> str:
    out = result.output
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def _usage(result: RunResult) -> dict[str, Any]:
    """Tokens of the run (never an amount).

    Returns:
        OpenAI's usage object.
    """
    return {
        "prompt_tokens": result.input_tokens,
        "completion_tokens": result.output_tokens,
        "total_tokens": result.input_tokens + result.output_tokens,
        "prompt_tokens_details": {"cached_tokens": result.cache_read_tokens},
    }


async def _extension(svc: Services, p: _Prepared, result: RunResult) -> dict[str, Any]:
    """Evokai's extension of the answer.

    The run, the model that answered, its tool calls and, for a session, its usage shares.

    Returns:
        The ``evokai`` object.
    """
    out: dict[str, Any] = {
        "runId": result.run_id,
        "model": bare(result.model),
        "toolCalls": result.tool_calls,
    }
    if result.citations:
        out["citations"] = result.citations
    s = p.caller.session
    if s is not None:
        state = await cap_state(svc.store, p.caller.app, s.who, svc.engine.now(), s.claims.limits)
        out["usage"] = state.view()
    return out


@router.get("/models")
async def models(svc: Svc, who: Call) -> dict[str, Any]:
    """The agents this caller can use, as OpenAI models.

    Returns:
        ``{"object": "list", "data": [{"id", "object", "created", "owned_by"}]}``.
    """
    s = who.session
    grants = await grants_of(svc.store, who.app)
    data = []
    for row in await svc.store.agents.active_all(who.app.id):
        if s is not None and row.name != s.claims.agent:
            continue
        try:
            check_channel(await svc.agents.active(who.app.id, row.name), grants, who.channel)
        except AppError:
            continue
        created = getattr(row, "created_at", None)
        data.append({
            "id": row.name,
            "object": "model",
            "created": int(created.timestamp()) if created else 0,
            "owned_by": "evokai",
        })
    return {"object": "list", "data": data}


@router.post(
    "/chat/completions",
    response_model=None,
    responses={200: {"content": {"application/json": {}, "text/event-stream": {}}}},
)
async def chat_completions(
    body: ChatCompletionRequest, svc: Svc, who: Call
) -> StreamingResponse | dict[str, Any]:
    """Run the agent named by ``model`` on the conversation and answer like OpenAI.

    Returns:
        A ``chat.completion``, or with ``stream`` a stream of ``chat.completion.chunk``.
    """
    p = await _prepare(svc, who, body)
    created = int(time.time())
    if not body.stream:
        result = await _run(svc, p)
        extension = await _extension(svc, p, result)
        content = _content(result)
        if result.waiting:
            asked = await openai_confirm.ask(
                svc.store, p.caller.app, p.agent, p.subject, result, "", p.convo or []
            )
            content, extension["confirm"] = asked["content"], asked["confirm"]
        return {
            "id": f"chatcmpl-{result.run_id}",
            "object": "chat.completion",
            "created": created,
            "model": p.agent.name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content, "refusal": None},
                    "finish_reason": "stop",
                    "logprobs": None,
                }
            ],
            "usage": _usage(result),
            "evokai": extension,
        }
    include_usage = bool(body.stream_options and body.stream_options.include_usage)
    return StreamingResponse(
        _stream(svc, p, created, include_usage),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _stream(
    svc: Services, p: _Prepared, created: int, include_usage: bool
) -> AsyncIterator[str]:
    """The chunks of a streamed answer.

    The agent's progress (tool calls) comes as chunks with an empty delta and an ``evokai.tool``
    extension. The answer streams as it is written: token by token when no output control of the
    security policy rewrites text, else sentence by sentence, each sentence through the output
    guard first (``evokai.guard.output.SentenceGate``). A sentence the guard stops (the agent's
    instructions repeated) ends the stream with an error chunk. A structured answer
    (``output_schema``) comes in one chunk at the end; while the model writes it, chunks with an
    empty delta carry the object so far as ``evokai.output`` (through the output checks, not
    validated yet: each replaces the previous one).

    Yields:
        Server-sent events.
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_text(piece: str) -> None:
        queue.put_nowait({"type": "text", "delta": piece})

    task = asyncio.create_task(_run(svc, p, queue.put_nowait, on_text))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    cid = f"chatcmpl-{new_id()}"  # one id for every chunk (the run's id is in ``evokai``)

    def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
        body = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": p.agent.name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}],
            **extra,
        }
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    streamed = False
    shown = ""
    try:
        yield chunk({"role": "assistant", "content": ""})
        while True:
            try:
                e = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if e is None:
                break
            if e["type"] == "text":
                streamed = True
                shown += e["delta"]
                yield chunk({"content": e["delta"]})
                continue
            if e["type"] == "output":
                # A structured answer as it is written: the object so far (``evokai.output``),
                # replaced by the next; the final content chunk carries the validated one.
                yield chunk({}, evokai={"output": e["partial"]})
                continue
            yield chunk({}, evokai={"tool": {"name": e["name"], "status": e["status"]}})
        try:
            result = task.result()
        except AppError as err:
            yield f"data: {json.dumps(error_body(err.status, err.code, err.message))}\n\n"
            yield "data: [DONE]\n\n"
            return
        except Exception as err:
            log.warning("openai_stream_failed", extra={"error": type(err).__name__})
            yield f"data: {json.dumps(error_body(500, 'internal', 'Something went wrong.'))}\n\n"
            yield "data: [DONE]\n\n"
            return
        extension = await _extension(svc, p, result)
        if result.waiting:
            app = p.caller.app
            asked = await openai_confirm.ask(
                svc.store, app, p.agent, p.subject, result, shown, p.convo or []
            )
            extension["confirm"] = asked["confirm"]
            question = asked["question"]
            yield chunk({"content": f"\n\n{question}" if shown.strip() else question})
        elif not streamed:
            yield chunk({"content": _content(result)})
        yield chunk({}, "stop", evokai=extension)
        if include_usage:
            yield (
                "data: "
                + json.dumps({
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": p.agent.name,
                    "choices": [],
                    "usage": _usage(result),
                })
                + "\n\n"
            )
        yield "data: [DONE]\n\n"
    finally:
        if not task.done():
            # The client went away: stop the run (its trace is still stored).
            task.cancel()
