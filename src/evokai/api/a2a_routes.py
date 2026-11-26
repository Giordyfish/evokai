"""A2A (Agent2Agent): Evokai agents published to other agents.

Off by default. An agent is published when its definition sets ``a2a: true`` **and** the
application is granted the ``a2a`` channel (``evokai.grants``). Then:

- ``GET {base}/v1/a2a/{application}/{agent}/.well-known/agent-card.json`` is its Agent Card (public,
  like any Agent Card: it names the agent, its skill and how to authenticate, never a secret);
- ``POST {base}/v1/a2a/{application}/{agent}`` is its JSON-RPC endpoint, authenticated with the
  application's server key (declared in the card as an HTTP bearer scheme).

Each message is one server run (``evokai.sessions.runs``, channel ``a2a``): read-only tools, the
security policy, the caps and the abuse limits apply as to any run, and nothing is stored. Both
protocol versions are spoken, chosen by the method name: 1.0 (``SendMessage``,
``SendStreamingMessage``, ``GetTask``, ``CancelTask``; ``ROLE_*`` and ``TASK_STATE_*`` enums,
parts without ``kind``) and 0.3 (``message/send``, ``message/stream``, ``tasks/get``,
``tasks/cancel``; ``kind`` discriminators, lowercase states).

Tasks: a task runs to a terminal state within the call (``completed``, ``failed`` or
``rejected``); there is no ``input-required`` (a server run asks nothing). Evokai keeps only the
task's id, context and state for an hour (never its content), so ``GetTask`` returns the state
without history or artifacts. ``CancelTask`` stops a task still streaming on this instance.
Push notifications, resubscription and task lists are not supported.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from evokai.agents.definitions import AgentDef
from evokai.api.deps import services
from evokai.core.errors import AppError, NotFound, Unauthorized
from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.grants import grants_of
from evokai.guard import GuardRefused
from evokai.identity.auth import bearer
from evokai.services import Services
from evokai.sessions.runs import AgentRunner, Attachment, LimitReached, RunResult, check_channel

log = logging.getLogger("evokai.a2a")

router = APIRouter(prefix="/v1/a2a", tags=["a2a"])
Svc = Annotated[Services, Depends(services)]

TASK_TTL = 3600
SCHEME = "evokaiServerKey"
MAX_TEXT = 100_000

V1_METHODS = {
    "SendMessage": "send",
    "SendStreamingMessage": "stream",
    "GetTask": "get",
    "CancelTask": "cancel",
    "SubscribeToTask": "unsupported",
    "ListTasks": "unsupported",
    "GetExtendedAgentCard": "unsupported",
}
V03_METHODS = {
    "message/send": "send",
    "message/stream": "stream",
    "tasks/get": "get",
    "tasks/cancel": "cancel",
    "tasks/resubscribe": "unsupported",
    "agent/getAuthenticatedExtendedCard": "unsupported",
}
SUPPORTED_VERSIONS = ("0.3", "1.0")

# JSON-RPC and A2A error codes.
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)
TASK_NOT_FOUND, NOT_CANCELABLE, PUSH_NOT_SUPPORTED, UNSUPPORTED = -32001, -32002, -32003, -32004
CONTENT_TYPE_NOT_SUPPORTED, VERSION_NOT_SUPPORTED = -32005, -32009

_running: dict[str, asyncio.Task] = {}
"""Tasks streaming on this instance (to cancel them)."""


class RpcError(Exception):
    """A JSON-RPC error."""

    def __init__(self, code: int, message: str) -> None:
        """Create the error.

        Args:
            code: The JSON-RPC or A2A code.
            message: For the caller.
        """
        super().__init__(message)
        self.code, self.message = code, message


async def _published(
    svc: Services, application_id: str, name: str
) -> tuple[m.Application, AgentDef]:
    """The application and agent, when the agent is published to A2A.

    Returns:
        (application, agent).

    Raises:
        NotFound: Unknown, inactive, not published or not granted (one answer for all).
    """
    app = await svc.store.applications.get(application_id)
    if app is None or app.status != "active":
        raise NotFound("Unknown agent.", code="not_found")
    try:
        agent = await svc.agents.active(app.id, name)
        check_channel(agent, await grants_of(svc.store, app), "a2a")
    except AppError as e:
        raise NotFound("Unknown agent.", code="not_found") from e
    return app, agent


def agent_card(
    app: m.Application, agent: AgentDef, url: str, version: str | None = None
) -> dict[str, Any]:
    """The agent's Agent Card.

    Args:
        app: The application.
        agent: The published agent.
        url: The agent's JSON-RPC URL.
        version: ``0.3`` or ``1.0`` for a card of one version only; None for a card both read
            (the 1.0 fields plus the 0.3 ones that do not collide).

    Returns:
        The card.
    """
    output = ["application/json"] if agent.output_schema else ["text/plain"]
    description = agent.title or f"The {agent.name} agent of {app.name}, run by Evokai."
    scheme_03 = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "Evokai server key",
        "description": "The application's Evokai server key (evk_srv_...).",
    }
    scheme_1 = {
        "httpAuthSecurityScheme": {
            "scheme": "Bearer",
            "bearerFormat": "Evokai server key",
            "description": "The application's Evokai server key (evk_srv_...).",
        }
    }
    skill = {
        "id": agent.name,
        "name": agent.title or agent.name,
        "description": description,
        "tags": ["evokai", app.name],
        "examples": list(agent.suggestions),
        "inputModes": ["text/plain", "application/json"],
        "outputModes": output,
    }
    common = {
        "name": agent.title or agent.name,
        "description": description,
        "version": agent.version,
        "capabilities": {"streaming": True, "pushNotifications": False},
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": output,
        "skills": [skill],
    }
    v03 = {
        "protocolVersion": "0.3.0",
        "url": url,
        "preferredTransport": "JSONRPC",
        "security": [{SCHEME: []}],
    }
    v1 = {
        "supportedInterfaces": [
            {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
            {"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "0.3"},
        ],
        "securityRequirements": [{"schemes": {SCHEME: {"list": []}}}],
    }
    if version == "0.3":
        return {**common, **v03, "securitySchemes": {SCHEME: scheme_03}}
    if version == "1.0":
        return {**common, **v1, "securitySchemes": {SCHEME: scheme_1}}
    return {**common, **v1, **v03, "securitySchemes": {SCHEME: {**scheme_03, **scheme_1}}}


@router.get("/{application_id}/{agent}/.well-known/agent-card.json")
async def card(
    application_id: str, agent: str, svc: Svc, request: Request, version: str | None = None
) -> dict[str, Any]:
    """The Agent Card of a published agent (``?version=0.3`` or ``1.0`` for one version only).

    Returns:
        The card.
    """
    app, a = await _published(svc, application_id, agent)
    url = str(request.url).split("/.well-known/", 1)[0]
    return agent_card(app, a, url, version if version in SUPPORTED_VERSIONS else None)


@dataclass
class Dialect:
    """How to write the answer: A2A 1.0 or 0.3."""

    v1: bool

    def state(self, s: str) -> str:
        """A task state (internal names are 0.3's).

        Returns:
            ``completed`` or ``TASK_STATE_COMPLETED``.
        """
        return "TASK_STATE_" + s.upper().replace("-", "_") if self.v1 else s

    def role(self, r: str) -> str:
        """A role (``user`` or ``agent``).

        Returns:
            The role as this version writes it.
        """
        return f"ROLE_{r.upper()}" if self.v1 else r

    def part(self, value: Any) -> dict[str, Any]:
        """A text part, or a data part for structured output.

        Returns:
            The part.
        """
        if isinstance(value, str):
            return {"text": value} if self.v1 else {"kind": "text", "text": value}
        if self.v1:
            return {"data": value, "mediaType": "application/json"}
        return {"kind": "data", "data": value}

    def tagged(self, kind: str, obj: dict[str, Any]) -> dict[str, Any]:
        """An object with 0.3's ``kind`` discriminator (1.0 has none).

        Returns:
            The object.
        """
        return obj if self.v1 else {**obj, "kind": kind}

    def wrap(self, kind: str, obj: dict[str, Any]) -> dict[str, Any]:
        """A result or stream item: 1.0 wraps it by field name, 0.3 tags it with ``kind``.

        Returns:
            The item.
        """
        if self.v1:
            key = {
                "task": "task",
                "status-update": "statusUpdate",
                "artifact-update": "artifactUpdate",
            }
            return {key[kind]: obj}
        return {**obj, "kind": kind}


@dataclass
class TaskState:
    """A task, as kept (no content) and answered."""

    id: str
    context_id: str
    state: str = "submitted"
    timestamp: str = field(default_factory=lambda: utcnow().isoformat())

    def status(self, d: Dialect, message: dict[str, Any] | None = None) -> dict[str, Any]:
        """The task's status.

        Returns:
            ``{state, timestamp, message?}``.
        """
        out: dict[str, Any] = {"state": d.state(self.state), "timestamp": self.timestamp}
        if message is not None:
            out["message"] = message
        return out


def _key(application_id: str, task_id: str) -> str:
    return f"a2a:task:{application_id}:{task_id}"


async def _save(svc: Services, application_id: str, t: TaskState) -> None:
    t.timestamp = utcnow().isoformat()
    await svc.shared.set(
        _key(application_id, t.id),
        json.dumps({"contextId": t.context_id, "state": t.state, "timestamp": t.timestamp}),
        TASK_TTL,
    )


@dataclass
class Incoming:
    """A message sent to the agent."""

    text: str
    attachments: list[Attachment]
    message: dict[str, Any]
    context_id: str


def _incoming(params: Any) -> Incoming:
    """Read the message of SendMessage / message/send (either version's parts).

    Returns:
        The message.

    Raises:
        RpcError: No message, no text, or a part type that is not supported.
    """
    msg = params.get("message") if isinstance(params, dict) else None
    if not isinstance(msg, dict) or not isinstance(msg.get("parts"), list):
        raise RpcError(INVALID_PARAMS, "params.message with parts is required.")
    texts: list[str] = []
    attachments: list[Attachment] = []
    for i, p in enumerate(msg["parts"]):
        if not isinstance(p, dict):
            raise RpcError(INVALID_PARAMS, "A part must be an object.")
        if isinstance(p.get("text"), str):
            texts.append(p["text"])
        elif "data" in p:
            attachments.append(
                Attachment(f"data-{i + 1}", "application/json", json.dumps(p["data"]))
            )
        else:
            attachments.append(_file(p, i))
    text = "\n\n".join(t for t in texts if t.strip())
    if not text and not attachments:
        raise RpcError(INVALID_PARAMS, "The message has no text.")
    if len(text) + sum(len(a.text) for a in attachments) > MAX_TEXT * 2:
        raise RpcError(INVALID_PARAMS, "The message is too large.")
    return Incoming(
        text=text or "See the attachments.",
        attachments=attachments,
        message=msg,
        context_id=str(msg.get("contextId") or new_id()),
    )


def _file(p: dict[str, Any], i: int) -> Attachment:
    """A file part with text content (inline bytes only).

    Returns:
        The attachment.

    Raises:
        RpcError: A URL, binary content or an unknown part.
    """
    inner = p.get("file")
    f: dict[str, Any] = inner if isinstance(inner, dict) else p
    raw = f.get("bytes") or f.get("raw")
    media = str(f.get("mimeType") or f.get("mediaType") or "text/plain")
    name = str(f.get("name") or f.get("filename") or f"file-{i + 1}")[:200]
    if not isinstance(raw, str) or not (media.startswith("text/") or media.endswith("json")):
        raise RpcError(
            CONTENT_TYPE_NOT_SUPPORTED, "Only text parts, data parts and inline text files."
        )
    try:
        return Attachment(name, media, base64.b64decode(raw).decode("utf-8")[:MAX_TEXT])
    except (binascii.Error, UnicodeDecodeError) as e:
        raise RpcError(CONTENT_TYPE_NOT_SUPPORTED, "A file part is not UTF-8 text.") from e


def _agent_message(d: Dialect, t: TaskState, output: Any) -> dict[str, Any]:
    return d.tagged(
        "message",
        {
            "messageId": new_id(),
            "role": d.role("agent"),
            "parts": [d.part(output)],
            "contextId": t.context_id,
            "taskId": t.id,
        },
    )


def _failure(e: BaseException) -> tuple[str, str]:
    """The terminal state and message of a run that raised.

    Returns:
        (state, message).
    """
    if isinstance(e, GuardRefused):
        return "rejected", e.message
    if isinstance(e, LimitReached):
        return "failed", e.message
    if isinstance(e, AppError):
        return ("rejected" if e.status == 403 else "failed"), e.message
    return "failed", "Something went wrong."


@router.post("/{application_id}/{agent}", response_model=None)
async def rpc(
    application_id: str, agent: str, svc: Svc, request: Request
) -> JSONResponse | StreamingResponse:
    """A2A JSON-RPC: send a message (blocking or streamed), get or cancel a task.

    Returns:
        The JSON-RPC response, or an SSE stream of them.

    Raises:
        Unauthorized: No server key of this application.
        RpcError: Internally only: every protocol error becomes the JSON-RPC error answer.
    """
    token = bearer(request.headers.get("authorization"))
    if not token:
        raise Unauthorized("The application's server key is required.", code="key")
    principal = await svc.auth.application(token)
    if principal.application_id != application_id:
        raise Unauthorized("The application's server key is required.", code="key")
    app, a = await _published(svc, application_id, agent)

    rid: Any = None
    try:
        try:
            req = json.loads(await request.body())
        except ValueError as e:
            raise RpcError(PARSE_ERROR, "Parse error.") from e
        if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or "method" not in req:
            raise RpcError(INVALID_REQUEST, "Not a JSON-RPC 2.0 request.")
        rid = req.get("id")
        method = str(req["method"])
        version = request.headers.get("a2a-version")
        if version and version not in SUPPORTED_VERSIONS:
            raise RpcError(VERSION_NOT_SUPPORTED, f"Supported A2A versions: {SUPPORTED_VERSIONS}.")
        d = Dialect(v1=method in V1_METHODS)
        op = V1_METHODS.get(method) or V03_METHODS.get(method)
        if op is None and "pushnotificationconfig" in method.lower():
            raise RpcError(PUSH_NOT_SUPPORTED, "Push notifications are not supported.")
        if op is None:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")
        if op == "unsupported":
            raise RpcError(UNSUPPORTED, f"{method} is not supported.")
        params = req.get("params") or {}
        if op == "get":
            return _ok(rid, await _get(svc, application_id, params, d))
        if op == "cancel":
            return _ok(rid, await _cancel(svc, application_id, params, d))
        inc = _incoming(params)
        tags = ["a2a"]
        if op == "send":
            return _ok(rid, await _send(svc, app, a, inc, d, tags))
        return StreamingResponse(
            _stream(svc, app, a, inc, d, tags, rid),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    except RpcError as e:
        return _err(rid, e.code, e.message)


def _ok(rid: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def _err(rid: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _task_id(params: Any) -> str:
    if not isinstance(params, dict):
        raise RpcError(INVALID_PARAMS, "params.id is required.")
    value = params.get("id") or str(params.get("name") or "").removeprefix("tasks/")
    if not value:
        raise RpcError(INVALID_PARAMS, "params.id is required.")
    return str(value)


async def _get(svc: Services, application_id: str, params: Any, d: Dialect) -> dict[str, Any]:
    """GetTask / tasks/get: the task's state (Evokai keeps no content).

    Returns:
        The task.

    Raises:
        RpcError: Unknown (or expired) task.
    """
    task_id = _task_id(params)
    raw = await svc.shared.get(_key(application_id, task_id))
    if raw is None:
        raise RpcError(TASK_NOT_FOUND, "Task not found.")
    data = json.loads(raw)
    t = TaskState(task_id, data["contextId"], data["state"], data["timestamp"])
    return d.tagged("task", {"id": t.id, "contextId": t.context_id, "status": t.status(d)})


async def _cancel(svc: Services, application_id: str, params: Any, d: Dialect) -> dict[str, Any]:
    """CancelTask / tasks/cancel: stop a task streaming on this instance.

    Returns:
        The canceled task.

    Raises:
        RpcError: Unknown task, or already finished.
    """
    task_id = _task_id(params)
    raw = await svc.shared.get(_key(application_id, task_id))
    if raw is None:
        raise RpcError(TASK_NOT_FOUND, "Task not found.")
    running = _running.get(task_id)
    if running is None or running.done():
        raise RpcError(NOT_CANCELABLE, "The task is no longer running.")
    running.cancel()
    t = TaskState(task_id, json.loads(raw)["contextId"], "canceled")
    await _save(svc, application_id, t)
    return d.tagged("task", {"id": t.id, "contextId": t.context_id, "status": t.status(d)})


def _run(
    svc: Services,
    app: m.Application,
    agent: AgentDef,
    inc: Incoming,
    tags: list[str],
    on_event: Any = None,
) -> Any:
    return AgentRunner(svc.engine).run(
        app,
        agent,
        text=inc.text,
        messages=None,
        attachments=inc.attachments,
        subject=f"a2a:{agent.name}",
        tags=tags,
        channel="a2a",
        on_event=on_event,
    )


def _meta(result: RunResult) -> dict[str, Any]:
    """Evokai's metadata of a finished task: the run, its tool calls, the passages it cites.

    Returns:
        The ``evokai`` metadata.
    """
    out: dict[str, Any] = {"runId": result.run_id, "toolCalls": result.tool_calls}
    if result.citations:
        out["citations"] = result.citations
    return out


def _artifact(d: Dialect, result: RunResult) -> dict[str, Any]:
    return {
        "artifactId": f"answer-{result.run_id}",
        "name": "answer",
        "parts": [d.part(result.output)],
    }


def _user_message(d: Dialect, inc: Incoming, t: TaskState) -> dict[str, Any]:
    return d.tagged(
        "message",
        {
            **{k: v for k, v in inc.message.items() if k != "kind"},
            "role": d.role("user"),
            "contextId": t.context_id,
            "taskId": t.id,
        },
    )


async def _send(
    svc: Services, app: m.Application, agent: AgentDef, inc: Incoming, d: Dialect, tags: list[str]
) -> dict[str, Any]:
    """SendMessage / message/send: run to the end and answer with the task.

    Returns:
        The task (wrapped as 1.0 or tagged as 0.3).
    """
    t = TaskState(new_id(), inc.context_id, "working")
    await _save(svc, app.id, t)
    task: dict[str, Any] = {"id": t.id, "contextId": t.context_id}
    history = [_user_message(d, inc, t)]
    try:
        result = await _run(svc, app, agent, inc, tags)
    except Exception as e:
        t.state, text = _failure(e)
        await _save(svc, app.id, t)
        if not isinstance(e, AppError):
            log.warning("a2a_run_failed", extra={"error": type(e).__name__})
        return d.wrap(
            "task",
            {**task, "status": t.status(d, _agent_message(d, t, text)), "history": history},
        )
    t.state = "completed"
    await _save(svc, app.id, t)
    answer = _agent_message(d, t, result.output)
    return d.wrap(
        "task",
        {
            **task,
            "status": t.status(d),
            "artifacts": [_artifact(d, result)],
            "history": [*history, answer],
            "metadata": {"evokai": _meta(result)},
        },
    )


async def _stream(
    svc: Services,
    app: m.Application,
    agent: AgentDef,
    inc: Incoming,
    d: Dialect,
    tags: list[str],
    rid: Any,
) -> AsyncIterator[str]:
    """SendStreamingMessage / message/stream: the task, its progress, the answer, the end.

    Yields:
        SSE frames, each a JSON-RPC response.
    """
    t = TaskState(new_id(), inc.context_id, "submitted")
    await _save(svc, app.id, t)

    def frame(kind: str, obj: dict[str, Any]) -> str:
        body = {"jsonrpc": "2.0", "id": rid, "result": d.wrap(kind, obj)}
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    def status(final: bool, message: dict[str, Any] | None = None, **meta: Any) -> str:
        obj: dict[str, Any] = {
            "taskId": t.id,
            "contextId": t.context_id,
            "status": t.status(d, message),
        }
        if not d.v1:
            obj["final"] = final
        if meta:
            obj["metadata"] = {"evokai": meta}
        return frame("status-update", obj)

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    task = asyncio.create_task(_run(svc, app, agent, inc, tags, queue.put_nowait))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    _running[t.id] = task
    try:
        yield frame(
            "task",
            {
                "id": t.id,
                "contextId": t.context_id,
                "status": t.status(d),
                "history": [_user_message(d, inc, t)],
            },
        )
        t.state = "working"
        await _save(svc, app.id, t)
        yield status(False)
        while True:
            try:
                e = await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if e is None:
                break
            if e["type"] == "output":
                # The structured answer as it is written: the artifact so far, replaced by the
                # next update (``append: false``) and by the validated one at the end.
                yield frame(
                    "artifact-update",
                    {
                        "taskId": t.id,
                        "contextId": t.context_id,
                        "artifact": {
                            "artifactId": f"answer-{e['runId']}",
                            "name": "answer",
                            "parts": [d.part(e["partial"])],
                        },
                        "append": False,
                        "lastChunk": False,
                    },
                )
                continue
            yield status(False, tool={"name": e["name"], "status": e["status"]})
        try:
            result = task.result()
        except asyncio.CancelledError:
            t.state = "canceled"
            await _save(svc, app.id, t)
            yield status(True)
            return
        except Exception as err:
            t.state, text = _failure(err)
            await _save(svc, app.id, t)
            yield status(True, _agent_message(d, t, text))
            return
        yield frame(
            "artifact-update",
            {
                "taskId": t.id,
                "contextId": t.context_id,
                "artifact": _artifact(d, result),
                "append": False,
                "lastChunk": True,
            },
        )
        t.state = "completed"
        await _save(svc, app.id, t)
        yield status(True, **_meta(result))
    finally:
        _running.pop(t.id, None)
        if not task.done():
            task.cancel()
