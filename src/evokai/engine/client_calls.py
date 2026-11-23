"""Client tool calls that wait for the client application, inside the turn.

A custom client tool with ``wait: true`` raises ``CallDeferred`` after its ``client_tool`` event.
``ClientToolWait`` (a pydantic-ai capability) answers those deferred calls in the run: it opens a
wait with ``pydantic_ai.external_events.wait_for_external_event`` and registers each call here;
``POST /v1/session/tool-results`` answers a call with ``ClientCalls.answer``, and the run goes on
once every call of the step has its result, or the timeout gives the model an error instead.

The wait lives in this process, like a turn's cancellation: the result must reach the task that
runs the turn (sticky sessions, ``docs/deployment.md``). A call id is bound to its session and turn
and answers once.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import UserError
from pydantic_ai.external_events import (
    ExternalEventError,
    resolve_external_event,
    wait_for_external_event,
)
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, RunContext

from evokai.engine.state import RunState

log = logging.getLogger("evokai.client_tools")

TIMEOUT_TEXT = (
    "No answer from the client application in time: the action may not have happened. Tell "
    "the user briefly; do not retry on your own."
)
NOT_NOW_TEXT = (
    "Not run: a change in the same step waits for the user's confirmation. Call this tool again "
    "after the confirmation if it is still needed."
)

Accept = Callable[[str, Any, str | None], Awaitable[str]]
"""Turns a client's answer into what the model reads: ``(call id, result, error) -> text``."""


@dataclass
class _Batch:
    """The waiting calls of one model step."""

    token: str
    session_id: str
    turn_id: str
    accept: Accept
    pending: set[str]
    results: dict[str, str] = field(default_factory=dict)


class ClientCalls:
    """The client calls waiting in this process, by call id."""

    def __init__(self) -> None:
        """Start empty."""
        self._calls: dict[str, _Batch] = {}
        self._batches: dict[str, _Batch] = {}

    def open(
        self, token: str, session_id: str, turn_id: str, call_ids: list[str], accept: Accept
    ) -> None:
        """Register the calls of a step under the wait's resume token."""
        batch = self._batches[token] = _Batch(token, session_id, turn_id, accept, set(call_ids))
        for cid in call_ids:
            self._calls[cid] = batch

    def close(self, token: str) -> None:
        """Forget the calls of a wait that ended (answered, timed out or cancelled)."""
        self._batches.pop(token, None)
        for cid in [c for c, b in self._calls.items() if b.token == token]:
            self._calls.pop(cid, None)

    def waiting(self, session_id: str | None = None) -> list[str]:
        """The call ids still waiting (of one session, when given).

        Returns:
            Call ids.
        """
        return [c for c, b in self._calls.items() if session_id in (None, b.session_id)]

    def answered(self, token: str) -> dict[str, str]:
        """What a wait already received (some calls of a step may be answered, others not).

        Returns:
            Call id to the text the model reads.
        """
        batch = self._batches.get(token)
        return dict(batch.results) if batch is not None else {}

    async def answer(self, session_id: str, call_id: str, result: Any, error: str | None) -> bool:
        """Answer one waiting call of a session (once).

        Returns:
            False when no call of this session waits under that id.
        """
        batch = self._calls.get(call_id)
        if batch is None or batch.session_id != session_id or call_id not in batch.pending:
            return False
        self._calls.pop(call_id, None)
        batch.pending.discard(call_id)
        try:
            batch.results[call_id] = await batch.accept(call_id, result, error)
        except Exception as e:
            log.warning("client_result_failed", extra={"turnId": batch.turn_id, "error": str(e)})
            batch.results[call_id] = "The client's result could not be read."
        if not batch.pending:
            try:
                resolve_external_event(batch.token, batch.results)
            except UserError:
                # The wait ended meanwhile (timeout or cancellation): the model already has its
                # answer.
                log.info("client_result_late", extra={"turnId": batch.turn_id})
        return True


@dataclass
class ClientToolWait(AbstractCapability[RunState]):
    """Answers a turn's waiting client calls with the client's results, in the run."""

    calls: ClientCalls
    session_id: str
    turn_id: str
    names: frozenset[str]
    """Model-facing names of the client tools that wait."""
    accept: Accept
    timed_out: Callable[[list[str]], None]
    """Called with the calls nobody answered in time (they read ``TIMEOUT_TEXT``)."""
    timeout_seconds: float

    async def handle_deferred_tool_calls(
        self,
        ctx: RunContext[RunState],  # ruff: ignore[unused-method-argument]
        *,
        requests: DeferredToolRequests,
    ) -> DeferredToolResults | None:
        """Wait for the client's results of this step's client calls.

        Returns:
            Their results, or None when the step deferred no client call.
        """
        mine = [c.tool_call_id for c in requests.calls if c.tool_name in self.names]
        if not mine:
            return None
        if requests.approvals:
            # The turn ends on a confirmation: nobody would read these results now.
            return requests.build_results(calls=dict.fromkeys(mine, NOT_NOW_TEXT))
        token: str | None = None

        def ask(t: str) -> None:
            nonlocal token
            token = t
            self.calls.open(t, self.session_id, self.turn_id, mine, self.accept)

        try:
            payload = await wait_for_external_event(
                "client_tool", ask=ask, timeout=timedelta(seconds=self.timeout_seconds)
            )
        except ExternalEventError:
            answered = self.calls.answered(token) if token is not None else {}
            self.timed_out([c for c in mine if c not in answered])
            return requests.build_results(calls={c: answered.get(c, TIMEOUT_TEXT) for c in mine})
        finally:
            if token is not None:
                self.calls.close(token)
        results = payload if isinstance(payload, dict) else {}
        return requests.build_results(calls={c: str(results.get(c, TIMEOUT_TEXT)) for c in mine})
