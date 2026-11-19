"""Durable agent steps on pgtask: a crashed agent step resumes from its last model call.

A workflow's ``agent`` step with ``durable: true`` runs as a pgtask task (``evokai.agent_step`` on
the ``evokai`` queue) in Evokai's own Postgres, with the harness ``PGTaskDurability`` capability:
each model request and each harness tool call is checkpointed in pgtask's tables, and a task whose
worker died is run again by pgtask on any instance, getting its finished calls back instead of
paying for them twice. Evokai's own tools (the application's MCP tools, read-only in a server run)
run again, as they would after Evokai's own step retry.

The step keeps its idempotency key as the task's: a retried step (Evokai's lease expired) finds
the same task and waits for it. pgtask's tables are created by migration ``0026`` (Postgres only);
the workers run in every instance with ``EVOKAI_WORKFLOW_DURABLE_AGENTS=true``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

log = logging.getLogger("evokai.workflows")

QUEUE = "evokai"
TASK = "evokai.agent_step"
AGENT_NAME = "evokai"
"""The prefix of every checkpoint (``evokai__model.request``): never rename it while tasks run."""


def dsn(database_url: str) -> str | None:
    """The libpq URL of Evokai's database, for pgtask.

    Returns:
        The URL, or None when the database is not Postgres.
    """
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        return None
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class DurableAgents:
    """pgtask's client and worker for durable agent steps, one per process."""

    def __init__(self, svc: Any) -> None:
        """Bind to the process services.

        Args:
            svc: The services.
        """
        self.svc = svc
        self.url = dsn(svc.settings.database_url) if svc.settings.workflow_durable_agents else None
        self._client: Any = None
        self._worker: Any = None
        self._task: asyncio.Task[None] | None = None
        self._registry: Any = None

    @property
    def enabled(self) -> bool:
        """Whether durable agent steps can run here (Postgres, and the setting on).

        Returns:
            True when they can.
        """
        return self.url is not None

    def registry(self) -> Any:
        """The task registry (one handler: an agent step).

        Returns:
            ``pgtask.TaskRegistry``.
        """
        if self._registry is None:
            from pgtask import TaskRegistry

            reg = TaskRegistry(queue_name=QUEUE)
            reg.task(TASK)(self._handle)
            self._registry = reg
        return self._registry

    async def start(self) -> None:
        """Start this instance's pgtask worker (when enabled and workflows run here)."""
        if not self.enabled or self._task is not None or not self.svc.settings.workflows_enabled:
            return
        from pgtask import Worker

        self._worker = Worker(
            self.url or "",
            self.registry(),
            concurrency=self.svc.settings.workflow_workers,
            poll_interval=self.svc.settings.workflow_poll_seconds,
            lease_duration=float(self.svc.settings.workflow_lease_seconds),
        )
        self._task = asyncio.create_task(self._worker.run())

    async def close(self) -> None:
        """Stop the worker (pgtask runs its tasks again elsewhere once their lease expires)."""
        if self._worker is not None:
            self._worker.shutdown()
        if self._task is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(self._task, timeout=10)
        self._task = self._worker = None

    async def client(self) -> Any:
        """The pgtask client on Evokai's database.

        Returns:
            ``pgtask.Client``.
        """
        if self._client is None:
            from pgtask import Client

            self._client = await Client.connect(self.url or "")
        return self._client

    async def run(self, payload: dict[str, Any], *, key: str, timeout: float) -> dict[str, Any]:
        """Run an agent step as a pgtask task and wait for its result.

        The same key finds the same task: a step Evokai retries waits for the task already there.

        Returns:
            The task's result (``output``, ``costMicroUsd``, ``meta``).

        Raises:
            DurableFailed: The task failed, was cancelled or did not finish in time.
        """
        from pgtask import EnqueueRequest

        client = await self.client()
        handle = await client.enqueue(
            EnqueueRequest(
                task_name=TASK,
                payload=payload,
                queue_name=QUEUE,
                idempotency_key=key,
                max_attempts=3,
            )
        )
        result = await handle.result(timeout=timeout)
        if result is None:
            raise DurableFailed("The durable agent step did not finish in time.", retry=True)
        if result.state != "succeeded" or not isinstance(result.result, dict):
            raise DurableFailed(
                f"The durable agent step {result.state}: {str(result.error)[:200]}", retry=False
            )
        return result.result

    async def _handle(self, task: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the agent step inside the pgtask task, its calls checkpointed.

        Returns:
            The step's result.

        Raises:
            ValueError: The application is gone.
        """
        from pydantic_ai_harness.pgtask import PGTaskDurability

        from evokai.sessions.runs import AgentRunner, Attachment

        svc = self.svc
        app = await svc.store.applications.get(payload["applicationId"])
        if app is None:
            raise ValueError("unknown application")
        agent = await svc.agents.active(app.id, payload["agent"])
        if payload.get("outputSchema") is not None:
            agent = agent.model_copy(update={"output_schema": payload["outputSchema"]})
        result = await AgentRunner(svc.engine).run(
            app,
            agent,
            text=payload.get("text"),
            messages=payload.get("messages"),
            attachments=[Attachment(**a) for a in payload.get("attachments") or []],
            subject=payload["subject"],
            tags=list(payload.get("tags") or []),
            groups=list(payload.get("groups") or []),
            origin="workflow",
            extra_capabilities=[PGTaskDurability(name=AGENT_NAME)],
            # A resumed task replays its finished model calls: their usage is recorded once.
            usage_key=f"pgtask:{task.id}",
        )
        return {
            "output": result.output,
            "costMicroUsd": result.cost_micro_usd,
            "meta": {
                "agent": agent.name,
                "agentVersion": agent.version,
                "runId": result.run_id,
                "model": result.model,
                "inputTokens": result.input_tokens,
                "outputTokens": result.output_tokens,
                "toolCalls": result.tool_calls,
                "durable": "pgtask",
            },
        }


class DurableFailed(Exception):
    """A durable agent step that did not succeed."""

    def __init__(self, text: str, *, retry: bool) -> None:
        """Keep why.

        Args:
            text: What happened.
            retry: Whether Evokai's step retry may help.
        """
        super().__init__(text)
        self.text = text
        self.retry = retry
