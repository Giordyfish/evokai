"""Running an agent's eval cases: on demand, and when a new version is saved.

Each case is a real turn of the agent (the same engine, tools, guard and client tools a person gets)
for a dedicated test subject (``eval:<agent>``) in a throw-away conversation that keeps no content:
read-only tools run, a tool that changes data stops the turn at its confirmation and never runs. The
case's input goes with its page context, if it has one. Forwarded-credential MCP servers get the
test credential the application stored for its evals, if any.

Usage is recorded as kind ``eval``: it never counts against the caps of the application's users or
the application's monthly cap, and it has its own monthly budget per application
(``eval_monthly_usd``, 2 USD by default). A run that reaches it stops with status ``limit``.

A run compares two versions: the one under test and a baseline (on save, the version that was
active before). Results keep every check's outcome, the answer, the tools called, latency and
tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelRequest, SystemPromptPart, TextPart, UserPromptPart

from evokai.agents.prompts import PageContext
from evokai.core.security import SessionClaims
from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.evals.checks import Judge, json_in, parse_checks, run_checks
from evokai.identity.models import SessionPrincipal
from evokai.llm.catalog import bare
from evokai.llm.prices import call_cost_micro_usd
from evokai.sessions.limits import eval_cap_usd
from evokai.sessions.threads import HistoryMessage
from evokai.sessions.turns import TurnInput

if TYPE_CHECKING:
    from evokai.services import Services

log = logging.getLogger("evokai.evals")

MAX_CASES = 50
"""Most cases a run takes (enabled ones, by name)."""
TAGS = ["evokai:eval"]
TOKEN_TTL = timedelta(minutes=15)


def subject_of(agent: str) -> str:
    """The test subject of an agent's evals.

    Returns:
        ``eval:<agent>``.
    """
    return f"eval:{agent}"


class EvalLimit(Exception):
    """The application's eval budget is used up."""


@dataclass
class Conversed:
    """One test turn: what the person would have seen and what ran."""

    answer: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    reason: str = ""
    error: str | None = None
    blocked: bool = False
    """The guard refused the input before the model saw it."""
    latency_ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A version's figures over its results.

    Returns:
        total, passed, failed, errors, skippedChecks, passRate (0-100), latencyMs (median),
        inputTokens, outputTokens and costMicroUsd (server side only).
    """
    total = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "errors": sum(1 for r in rows if r.get("error")),
        "skippedChecks": sum(
            1 for r in rows for c in r.get("checks") or [] if c.get("outcome") == "skipped"
        ),
        "passRate": round(passed / total * 100, 1) if total else None,
        "latencyMs": round(statistics.median([r["latency_ms"] for r in rows])) if rows else None,
        "inputTokens": sum(r["input_tokens"] for r in rows),
        "outputTokens": sum(r["output_tokens"] for r in rows),
        "costMicroUsd": sum(r["cost_micro_usd"] for r in rows),
    }


class EvalService:
    """Starts runs in the background and keeps track of them."""

    def __init__(self, svc: Services) -> None:
        """Bind to the process services.

        Args:
            svc: Services (store, engine, registries, catalog).
        """
        self.svc = svc
        self.tasks: set[asyncio.Task[None]] = set()

    async def close(self) -> None:
        """Stop the runs in progress (they are marked interrupted)."""
        for t in list(self.tasks):
            t.cancel()
        for t in list(self.tasks):
            with contextlib.suppress(BaseException):
                await t

    # ── starting ─────────────────────────────────────────────────────────────────────────────────

    async def start(
        self,
        app: m.Application,
        agent: str,
        version: str,
        baseline: str | None,
        *,
        trigger: str,
        created_by: str | None,
    ) -> m.EvalRun:
        """Record a run and start it in the background.

        Returns:
            The run (status ``running``).
        """
        run = await self.svc.store.evals.start_run(
            app.id,
            agent,
            version=version,
            baseline=baseline if baseline != version else None,
            trigger=trigger,
            created_by=created_by,
            summary={},
        )
        task = asyncio.create_task(self._execute(app, run))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return run

    async def on_save(
        self, app: m.Application, agent: str, version: str, previous: str | None, by: str | None
    ) -> m.EvalRun | None:
        """A new version was saved: run its cases against the previous active version.

        Nothing happens without enabled cases, when the application turned it off
        (``eval_on_save: false``), or while another run of the agent is going.

        Returns:
            The run, or None.
        """
        if (app.defaults or {}).get("eval_on_save") is False:
            return None
        if not any(c.enabled for c in await self.svc.store.evals.cases(app.id, agent)):
            return None
        if await self.svc.store.evals.running(app.id, agent):
            log.info("eval_skipped_running", extra={"agent": agent, "version": version})
            return None
        return await self.start(app, agent, version, previous, trigger="save", created_by=by)

    # ── running ──────────────────────────────────────────────────────────────────────────────────

    async def _execute(self, app: m.Application, run: m.EvalRun) -> None:
        store = self.svc.store
        cases = [c for c in await store.evals.cases(app.id, run.agent_name) if c.enabled][
            :MAX_CASES
        ]
        versions = [run.version] + ([run.baseline] if run.baseline else [])
        rows: dict[str, list[dict[str, Any]]] = {v: [] for v in versions}
        status = "done"
        try:
            for case in cases:
                for version in versions:
                    row = await self.run_case(app, run.agent_name, version, case)
                    rows[version].append(row)
                    await store.evals.add_result(run.id, **row)
        except EvalLimit:
            status = "limit"
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        except Exception as e:
            log.exception("eval_failed", extra={"runId": run.id, "error_type": type(e).__name__})
            status = "failed"
        finally:
            summary = {"versions": {v: summarize(rows[v]) for v in versions}, "cases": len(cases)}
            with contextlib.suppress(Exception):
                await asyncio.shield(store.evals.finish_run(run.id, status, summary))
            log.info("eval_run", extra={"runId": run.id, "agent": run.agent_name, "status": status})

    async def _budget_left(self, app: m.Application) -> bool:
        used = await self.svc.store.usage.eval_spend(app.id, utcnow())
        return used < eval_cap_usd(app)

    async def run_case(
        self,
        app: m.Application,
        agent: str,
        version: str,
        case: m.EvalCase,
        *,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """One case on one version, as a real turn of the test subject.

        Args:
            app: The application.
            agent: The agent's name.
            version: The version under test.
            case: The case.
            model: Run the case on this model instead of the version's (a ``models`` run).
            tags: Usage tags (default ``evokai:eval``).

        Returns:
            The result's fields (``EvalLimit`` from the turn when the eval budget is used up).
        """
        t = await self.converse(
            app,
            agent,
            version,
            case.input,
            page=case.page,
            name=case.name,
            model=model,
            tags=tags,
        )
        checks = await run_checks(
            parse_checks(case.checks or []),
            answer=t.answer,
            calls=t.calls,
            question=case.input,
            judge=self.judge(app, agent),
        )
        passed = t.error is None and not any(c["outcome"] == "failed" for c in checks)
        return {
            "case_id": case.id,
            "case_name": case.name,
            "version": version,
            "passed": passed,
            "checks": checks,
            "answer": t.answer[:20_000],
            "tools": t.calls,
            "reason": t.reason,
            "latency_ms": t.latency_ms,
            "error": t.error,
            **t.usage,
        }

    async def converse(
        self,
        app: m.Application,
        agent: str,
        version: str,
        text: str,
        *,
        page: dict[str, Any] | None = None,
        history: list[HistoryMessage] | None = None,
        name: str = "eval",
        subject: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> Conversed:
        """One real turn of a test subject in a throw-away conversation that keeps nothing.

        Args:
            app: The application.
            agent: The agent's name.
            version: The version to talk to.
            text: The message.
            page: The page the person would have open.
            history: The conversation so far (e.g. an earlier tool result).
            name: The conversation's title while it lives.
            subject: The test subject (default ``eval:<agent>``).
            model: A model to answer instead of the version's own (within the allow-lists).
            tags: Usage tags (default ``evokai:eval``).

        Returns:
            The transcript: answer, tool calls asked for, tools that ran, reason, error, usage.

        Raises:
            EvalLimit: The eval budget is used up.
        """
        if not await self._budget_left(app):
            raise EvalLimit()
        svc, store = self.svc, self.svc.store
        tags = tags or TAGS
        who = Who(app.id, subject or subject_of(agent))
        conversation = new_id()
        await store.sessions.create(
            who,
            agent,
            version,
            name[:80],
            tags,
            session_id=conversation,
            storage_mode="external",
        )
        token_id = f"ev{new_id().replace('-', '')}"  # fits the 40 characters of a token id
        credential = (app.defaults or {}).get("eval_credential")
        if credential:
            await store.credentials.put(token_id, app.id, credential, utcnow() + TOKEN_TTL)
        claims = SessionClaims(
            application_id=app.id,
            subject=who.subject,
            agent=agent,
            conversation_id=conversation,
            token_id=token_id,
            expires_at=int(time.time() + TOKEN_TTL.total_seconds()),
            tags=tags,
            has_credential=bool(credential),
            storage_mode="external",
        )
        inp = TurnInput(
            principal=SessionPrincipal(claims=claims, origins=[]),
            text=text,
            page=PageContext.model_validate(page) if page else None,
            history=history or [],
            evaluation=True,
            model_override=model,
        )
        events: list[dict[str, Any]] = []
        started = time.perf_counter()
        error: str | None = None
        try:
            turn = await svc.engine.open(inp)
            stop = turn.channel.listen(
                lambda e: events.append(e) if e.get("turnId") == turn.turn_id else None
            )
            try:
                await svc.engine.execute(turn, inp)
            finally:
                stop()
        except Exception as e:
            error = f"{type(e).__name__}: {getattr(e, 'message', None) or str(e)}"[:300]
        latency = round((time.perf_counter() - started) * 1000)
        done = next((e for e in reversed(events) if e.get("type") == "done"), {})
        calls = [
            {"name": b["toolUse"]["name"], "args": b["toolUse"].get("input") or {}}
            for msg in done.get("messages") or []
            if msg.get("role") == "assistant"
            for b in msg.get("content") or []
            if isinstance(b, dict) and "toolUse" in b
        ]
        ran = [
            str(e.get("name"))
            for e in events
            if e.get("type") == "tool" and e.get("status") in ("done", "error")
        ]
        answer = "".join(str(e.get("delta") or "") for e in events if e.get("type") == "text")
        failure = next((e for e in events if e.get("type") == "error"), None)
        if failure and failure.get("limit") == "eval":
            raise EvalLimit()
        blocked = bool(failure and failure.get("code") == "guard")
        if failure and not error and not blocked:
            error = f"{failure.get('code')}: {failure.get('message')}"[:300]
        usage = await self._usage(conversation)
        svc.hub.forget(conversation)
        with contextlib.suppress(Exception):
            await store.sessions.delete(who, conversation)
        reason = str(done.get("reason") or ("error" if error else ""))
        return Conversed(
            answer=answer,
            calls=calls,
            ran=ran,
            reason=reason,
            error=error,
            blocked=blocked or (failure is None and reason == "guard" and not answer),
            latency_ms=latency,
            usage=usage,
        )

    async def _usage(self, session_id: str) -> dict[str, int]:
        u = m.UsageRecord
        async with self.svc.db.session() as s:
            row = (
                await s.execute(
                    sa.select(
                        sa.func.coalesce(sa.func.sum(u.input_tokens), 0),
                        sa.func.coalesce(sa.func.sum(u.output_tokens), 0),
                        sa.func.coalesce(sa.func.sum(u.cost_micro_usd), 0),
                    ).where(u.session_id == session_id)
                )
            ).one()
        return {
            "input_tokens": int(row[0]),
            "output_tokens": int(row[1]),
            "cost_micro_usd": int(row[2]),
        }

    def judge(self, app: m.Application, agent: str) -> Judge | None:
        """The rubric judge: the application's ``eval_judge_model``, off when it has none.

        Returns:
            The judge, or None.
        """
        name = (app.defaults or {}).get("eval_judge_model")
        if not name or not self.svc.catalog.allowed(str(name)):
            return None
        svc = self.svc

        async def judge(rubric: str, question: str, answer: str) -> tuple[bool, str]:
            if not await self._budget_left(app):
                raise EvalLimit()
            model = svc.catalog.get(str(name))
            prompt = (
                "You grade an assistant's answer against a rubric. Reply with JSON only: "
                '{"pass": true or false, "reason": "one sentence"}.'
            )
            content = f"Rubric:\n{rubric}\n\nQuestion:\n{question}\n\nAnswer:\n{answer}"
            response = await model_request(
                model,
                [
                    ModelRequest(
                        parts=[SystemPromptPart(content=prompt), UserPromptPart(content=content)]
                    )
                ],
                model_settings={"max_tokens": 300, "temperature": 0},
            )
            await svc.store.usage.record(
                Who(app.id, subject_of(agent)),
                TAGS,
                agent_name=agent,
                kind="eval",
                model=bare(str(name)),
                input_tokens=response.usage.input_tokens or 0,
                output_tokens=response.usage.output_tokens or 0,
                cost_micro_usd=call_cost_micro_usd(str(name), response.usage),
            )
            text = "".join(p.content for p in response.parts if isinstance(p, TextPart))
            verdict = json_in(text)
            return bool(verdict.get("pass")), str(verdict.get("reason") or "")

        return judge


def shares(summary: dict[str, Any], app: m.Application) -> dict[str, Any]:
    """A version's summary for people: the spend as a share of the eval budget, never an amount.

    Returns:
        The summary with ``capPct`` in place of ``costMicroUsd``.
    """
    out = {k: v for k, v in summary.items() if k != "costMicroUsd"}
    cap = eval_cap_usd(app)
    spent = (summary.get("costMicroUsd") or 0) / 1e6
    out["capPct"] = 100.0 if cap <= 0 else round(spent / cap * 100, 2)
    return out
