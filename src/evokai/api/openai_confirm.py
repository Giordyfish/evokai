"""Confirmations over the OpenAI-compatible API, opt-in.

They need the grant ``openai:confirm`` and the agent's ``openai_confirm``.

The protocol is stateless and carries no buttons, so a change works like this:

1. the model calls a tool that changes data: the run stops before it runs, Evokai keeps the pending
   action (subject, agent, the exact arguments, the hash of the assistant message it answers with,
   10 minutes, one use) and answers in plain text in the application's language, plus
   ``evokai.confirm`` ``{actionId, items}`` for clients that draw buttons;
2. on the next request the last assistant message must be that text, unmodified, and the same
   subject must answer: a yes (the locale's recogniser, or ``evokai.confirm.approve``) runs the
   stored arguments, never regenerated, and the turn goes on; a no cancels without a model call;
   anything else cancels the action and is answered as a new message.

The protocol cannot prove that a person typed the yes: a client could send it on its own. Audited in
the run's trace with ``confirmChannel: openai``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from evokai.audio.speech.replies import spoken_decision
from evokai.core.util import utcnow
from evokai.db.repositories import Who
from evokai.grants import Grants
from evokai.guard.ask import REASON_WRITES
from evokai.i18n import locale_of, message, speech_rules
from evokai.sessions.runs import Resume, RunResult

TTL_SECONDS = 600
"""How long a pending action waits for its yes."""


def allowed(grants: Grants, agent: Any, *, named: bool) -> bool:
    """Whether this agent asks for confirmations over the OpenAI-compatible API.

    Args:
        grants: The application's grants.
        agent: The agent.
        named: The caller names the person (a session token, or the server key's ``user``):
            without one every call is the same subject, ``system:<agent>``, and nobody in
            particular could confirm.

    Returns:
        True with the grant, the agent's switch and a named subject.
    """
    return named and grants.openai("confirm") and bool(getattr(agent, "openai_confirm", False))


def conversation(text: str | None, messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """A request's conversation as ``role`` and ``text`` (one text is one user message).

    Returns:
        The messages.
    """
    if messages is not None:
        return [{"role": m.get("role"), "text": str(m.get("text") or "")} for m in messages]
    return [{"role": "user", "text": text or ""}]


def pending_key(agent: str, convo: list[dict[str, Any]]) -> str:
    """The key of a pending action: the agent and the conversation that asked for it.

    The protocol has no conversation id: the conversation the question followed is it, so a yes in
    one conversation never runs what another conversation of the same subject asked for.

    Returns:
        ``openai:`` and a hash of both.
    """
    raw = json.dumps([agent, [[m["role"], m["text"].strip()] for m in convo]], ensure_ascii=False)
    return "openai:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def text_hash(text: str) -> str:
    """The hash of the assistant message a confirmation is (whitespace at the ends ignored).

    Returns:
        A hex digest.
    """
    return hashlib.sha256(text.strip().encode()).hexdigest()


@dataclass
class Decision:
    """The person's answer to a pending action."""

    resume: Resume
    text: str | None
    messages: list[dict[str, Any]] | None
    """The request's input before the confirmation and the answer (what the asking run had)."""


async def decide(
    store: Any,
    app: Any,
    agent: Any,
    subject: str,
    messages: list[dict[str, Any]] | None,
    explicit: dict[str, Any] | None,
) -> Decision | None:
    """Whether the request answers a pending action, and how.

    A pending action is taken (closed) once, whatever the answer.

    Returns:
        The decision to carry out, or None when the request is a new message (also when it
        cancelled an action by saying something else).
    """
    if not messages or len(messages) < 3:
        return None
    *before, asked, reply = messages
    if asked.get("role") != "assistant" or reply.get("role") != "user":
        return None
    who = Who(app.id, subject)
    pending = await store.pending.open(who, pending_key(agent.name, conversation(None, before)))
    if pending is None or (pending.state or {}).get("textHash") != text_hash(str(asked["text"])):
        return None
    approve: bool | None
    if explicit is not None and explicit.get("actionId") == pending.id:
        approve = bool(explicit.get("approve"))
    else:
        approve = spoken_decision(str(reply.get("text") or ""), speech_rules(locale_of(app)))
    status = {True: "approved", False: "rejected", None: "superseded"}[approve]
    if not await store.pending.take(who, pending.id, status):
        return None
    if approve is None:
        return None
    created = pending.created_at
    waited = utcnow() - (created if created.tzinfo else created.replace(tzinfo=utcnow().tzinfo))
    state = pending.state
    resume = Resume(
        calls=list(state.get("calls") or []),
        ready=list(state.get("ready") or []),
        approve=approve,
        waited_ms=max(0, round(waited.total_seconds() * 1000)),
    )
    if len(before) == 1:
        return Decision(resume, str(before[0]["text"]), None)
    return Decision(resume, None, before)


async def ask(
    store: Any,
    app: Any,
    agent: Any,
    subject: str,
    result: RunResult,
    shown: str,
    convo: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep the run's waiting calls and build the question.

    Args:
        store: Repositories.
        app: The application.
        agent: The agent.
        subject: Who answers.
        result: The run that stopped on calls that change data.
        shown: What the client already received of this answer (a streamed text).
        convo: The request's conversation (``conversation``), which the question follows.

    Returns:
        ``content`` (the whole assistant message, question included) and ``confirm`` (the
        ``evokai.confirm`` extension).
    """
    items = [str(w["label"]) for w in result.waiting]
    question = message(app, "openai.confirmPrompt", items="; ".join(items))
    # Exactly what the client receives: its copy of the message must hash the same.
    content = f"{shown}\n\n{question}" if shown.strip() else question
    state = {
        "kind": "openai",
        "agent": agent.name,
        "calls": result.waiting,
        "ready": result.ready,
        "textHash": text_hash(content),
    }
    action = await store.pending.create(
        Who(app.id, subject), pending_key(agent.name, convo), state, ttl_seconds=TTL_SECONDS
    )
    why = [w.get("why") or {"key": REASON_WRITES} for w in result.waiting]
    return {
        "content": content,
        "question": question,
        "confirm": {"actionId": action, "items": items, "why": why},
    }
