"""Server runs: an application's backend runs an agent (no browser, no user in the loop)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from evokai.api.deps import server_app, services
from evokai.core.errors import NotFound
from evokai.identity.models import AppPrincipal
from evokai.services import Services
from evokai.sessions.runs import AgentRunner, Attachment

router = APIRouter(prefix="/v1/agents", tags=["runs"])
Svc = Annotated[Services, Depends(services)]
App = Annotated[AppPrincipal, Depends(server_app)]


class RunMessage(BaseModel):
    """A message of the input conversation."""

    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=100_000)


class RunAttachment(BaseModel):
    """A text attachment (a ticket body, an email, an extracted document)."""

    name: str = Field(max_length=200)
    mediaType: str = Field(default="text/plain", max_length=100)
    text: str = Field(max_length=200_000)


class RunRequest(BaseModel):
    """What to run."""

    input: str | list[RunMessage]
    subject: str | None = Field(default=None, max_length=200)
    """Who the run is for (usage is recorded under it); default ``system:<agent>``."""
    tags: list[str] = Field(default_factory=list, max_length=20)
    attachments: list[RunAttachment] = Field(default_factory=list, max_length=20)
    store: bool = False
    """Keep the run as a conversation of the subject (Evokai storage)."""
    groups: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=50
    )
    """The subject's groups or roles: knowledge documents with access groups are found only when
    one of them matches (documents without access groups always are)."""


class RunUsage(BaseModel):
    """Tokens and cost of the run (server to server only)."""

    inputTokens: int
    """Every input token, cached ones included."""
    outputTokens: int
    cacheReadTokens: int = 0
    """Input tokens read from the prompt cache (``prompt_cache``), billed at a tenth."""
    cacheWriteTokens: int = 0
    """Input tokens written to the prompt cache, billed at 1.25x (5 minutes) or 2x (1 hour)."""
    costUsd: float
    toolCalls: int


class RunResponse(BaseModel):
    """The run's answer."""

    runId: str
    output: Any
    """The schema-valid JSON when the agent has an output_schema, else the answer text."""
    model: str
    usage: RunUsage
    conversationId: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    """Knowledge passages found by the run, as ``citation`` events (the answer cites them as
    ``[n]``)."""


@router.post("/{name}/run")
async def run_agent(name: str, body: RunRequest, svc: Svc, app: App) -> RunResponse:
    """Run an agent that allows the ``server`` channel and return its (structured) answer.

    Returns:
        The output, the model that answered and the usage.

    Raises:
        NotFound: Unknown application.
    """
    application = await svc.store.applications.get(app.application_id)
    if application is None:
        raise NotFound()
    agent = await svc.agents.active(app.application_id, name)
    text = body.input if isinstance(body.input, str) else None
    messages = None if isinstance(body.input, str) else [x.model_dump() for x in body.input]
    result = await AgentRunner(svc.engine).run(
        application,
        agent,
        text=text,
        messages=messages,
        attachments=[Attachment(a.name, a.mediaType, a.text) for a in body.attachments],
        subject=body.subject or f"system:{agent.name}",
        tags=body.tags,
        store_conversation=body.store,
        groups=body.groups,
    )
    return RunResponse(
        runId=result.run_id,
        output=result.output,
        model=result.model,
        usage=RunUsage(
            inputTokens=result.input_tokens,
            outputTokens=result.output_tokens,
            cacheReadTokens=result.cache_read_tokens,
            cacheWriteTokens=result.cache_write_tokens,
            costUsd=result.cost_micro_usd / 1e6,
            toolCalls=result.tool_calls,
        ),
        conversationId=result.conversation_id,
        citations=result.citations,
    )
