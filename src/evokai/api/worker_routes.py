"""Model calls of a capability worker, made through Evokai so its rules hold.

A capability worker (``workers/capability``) whose capability calls a model itself (browser-use's
own agent) holds no provider key and no Evokai key. With each MCP call of a turn, Evokai gives a
worker server (``worker: true``) a call token it signs for that turn: bound to the application,
the conversation, the person, the agent, the tags and the turn, valid a few minutes at most and
only while the turn runs, for at most ``EVOKAI_WORKER_MODEL_CALLS`` model calls. The worker sends
it as the bearer of ``POST /v1/workers/model``, which accepts nothing else (a session token, a
server or admin key are refused). The call goes to a model the agent may use (allow-lists,
grants), after the caps (a spent cap answers 429 ``limit``), with the no-provider-storage policy on
its settings; it is recorded as the token's person's usage (kind ``cap``, agent
``worker:<agent>``, the token's tags), never as anything the worker says, and its text passes the
guard's output checks.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, TypeAdapter

from evokai.api.deps import services
from evokai.core.errors import AppError, Forbidden, NotFound, Unauthorized
from evokai.services import Services

router = APIRouter(prefix="/v1/workers", tags=["server"])
Svc = Annotated[Services, Depends(services)]


class WorkerModelCall(BaseModel):
    """One model request of a worker, in pydantic-ai's own JSON form.

    Who it is for comes from its token, never from the body.
    """

    messages: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    """``ModelMessagesTypeAdapter`` JSON."""
    parameters: dict[str, Any] = Field(default_factory=dict)
    """``ModelRequestParameters`` JSON (the tools and output the request offers)."""
    settings: dict[str, Any] = Field(default_factory=dict)


class LimitReached(AppError):
    """A cap stops the worker's call."""

    status, code = 429, "limit"


async def worker_claims(request: Request, svc: Svc) -> dict[str, Any]:
    """The worker token of a request, whose turn still runs (checked before the body is read).

    Returns:
        Its claims.

    Raises:
        Unauthorized: No worker token, an invalid or expired one, or its turn is over.
    """
    from evokai.mcp.registry import worker_run_key

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise Unauthorized("A worker token is required.", code="token")
    claims = svc.keyring.verify_worker(auth[7:].strip())
    # Valid only while its turn runs: the turn's end removes the mark.
    if await svc.shared.get(worker_run_key(str(claims["tid"]))) != str(claims["aud"]):
        raise Unauthorized("The turn of this worker token is over.", code="token")
    return claims


Claims = Annotated[dict[str, Any], Depends(worker_claims)]


@router.post("/model")
async def worker_model(body: WorkerModelCall, svc: Svc, claims: Claims) -> dict[str, Any]:
    """Make one model request for a worker, as the token's person, under Evokai's rules.

    Returns:
        ``response``: the ``ModelResponse`` JSON.

    Raises:
        Forbidden: The worker is no longer registered, or no model of the agent is allowed.
        NotFound: The agent is unknown.
        LimitReached: A cap is spent, or the turn's worker model calls are used up.
    """
    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
    from pydantic_ai.models import ModelRequestParameters

    from evokai.db.repositories import Who
    from evokai.grants import grants_of
    from evokai.llm.catalog import bare
    from evokai.llm.policy import enforce_settings
    from evokai.llm.prices import call_cost_micro_usd, estimate_call_usd
    from evokai.mcp.registry import WORKER_TOKEN_TTL
    from evokai.sessions.limits import cap_state, limit_message

    app_id, run_id = str(claims["aud"]), str(claims["tid"])
    used = await svc.shared.incr(f"worker-calls:{claims['jti']}", WORKER_TOKEN_TTL)
    if used > int(claims.get("mcl") or 0):
        raise LimitReached("This turn's worker model calls are used up.")
    eng = svc.engine
    app = await svc.store.applications.get(app_id)
    if app is None:
        raise NotFound()
    servers = {s.name: s for s in await svc.store.mcp_servers.all(app.id)}
    server = servers.get(str(claims.get("srv") or ""))
    if server is None or not server.worker:
        raise Forbidden("This capability worker is not registered.", code="worker")
    subject, tags = str(claims["sub"]), [str(x) for x in claims.get("tags") or []]
    try:
        agent = await svc.agents.active(app.id, str(claims["agt"]))
    except NotFound as e:
        raise NotFound("Unknown agent.", code="unknown_agent") from e
    grants = await grants_of(svc.store, app)
    candidates = eng._candidates(agent, app, None, grants)
    if not candidates:
        raise Forbidden("No model of this agent is allowed.", code="model")
    name = candidates[0]
    who = Who(app.id, subject)
    caps = await cap_state(svc.store, app, who, eng.now(), None)
    hit = caps.hit(estimate_call_usd(name))
    if hit:
        raise LimitReached(limit_message(app, hit))
    messages = ModelMessagesTypeAdapter.validate_python(body.messages)
    params = TypeAdapter(ModelRequestParameters).validate_python(body.parameters)
    settings = enforce_settings(dict(body.settings))
    model = eng.catalog.get(name)
    response = await model.request(messages, settings, params)  # ty: ignore[invalid-argument-type]
    u = response.usage
    await svc.store.usage.record(
        who,
        tags,
        session_id=str(claims["cid"]),
        turn_id=run_id,
        agent_name=f"worker:{agent.name}"[:80],
        kind="cap",
        model=bare(name),
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_tokens=u.cache_read_tokens or 0,
        cache_write_tokens=u.cache_write_tokens or 0,
        cost_micro_usd=call_cost_micro_usd(name, u),
        tool_calls=0,
        latency_ms=0,
    )
    guard = eng.guard.turn(
        app,
        agent,
        subject=subject,
        session_id=None,
        turn_id=run_id,
        channel="server",
        instructions=agent.instructions,
        tags=tags,
    )
    parts = [
        TextPart(content=str(guard.check_final(p.content))) if isinstance(p, TextPart) else p
        for p in response.parts
    ]
    await guard.flush()
    out = ModelResponse(
        parts=parts,
        usage=response.usage,
        model_name=response.model_name,
        timestamp=response.timestamp,
    )
    return {"response": TypeAdapter(ModelResponse).dump_python(out, mode="json")}
