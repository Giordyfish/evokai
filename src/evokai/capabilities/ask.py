"""``ask_user`` on Evokai: each question ends the turn, each answer is the person's next message.

The harness ``AskUser`` waits for an answerer inside the run. Evokai never waits inside a turn: a
question is shown like ``ui_ask`` (an ``ask`` event, spoken in voice) and the turn ends with reason
``ask``; the person's choice is their next message. A call with several questions shows them one at
a time: the questions still to ask wait with the conversation (``evokai_capability_records``, an
hour with external storage), each answer shows the next question without a model call, and after
the last one the model gets every question with its answer. The question schema and its
validation stay the harness's own. With nobody to ask (a server run, an application not granted
questions) the model is told to choose and say which choice it made.

Imported only when a run builds the capability (listing the catalog imports nothing).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai_harness.ask_user import AskUser, AskUserToolset
from pydantic_ai_harness.ask_user._types import Questions

from evokai.core.util import utcnow
from evokai.engine.state import RunState, StopRun

NO_ONE_TO_ASK = (
    "No person can answer questions in this run: make a reasonable choice and say which one you "
    "made."
)
"""The tool result when nobody can answer."""

ASKED = (
    "Question shown. The user's answer will come as their next message: do not answer for them "
    "and do not go on with the task until it comes."
)
"""The tool result in a turn: the turn ends on the question, as with ``ui_ask``."""

KEY = "queue"
"""The record of the questions still to ask in a conversation."""

ANSWER_PREFIX = 'Answer to your question "'
"""How the turn marks the answer to the question its last message showed."""


def event_of(q: Any, call_id: str, index: int) -> dict[str, Any]:
    """An Evokai ``ask`` event from a harness question.

    Returns:
        The event.
    """
    labels: list[str] = []
    for o in q["options"] if isinstance(q, dict) else q.options:
        text = o["label"] if isinstance(o, dict) else o.label
        label = " ".join(str(text).split())[:40]
        if label and label.lower() not in {x.lower() for x in labels}:
            labels.append(label)
    question = q["question"] if isinstance(q, dict) else q.question
    multi = q.get("multi_select") if isinstance(q, dict) else q.multi_select
    return {
        "type": "ask",
        "askId": f"ask_{call_id}" if index == 0 else f"ask_{call_id}_{index}",
        "question": " ".join(str(question).split())[:140],
        "options": labels[:5],
        "multiple": bool(multi),
        "other": True,
    }


@dataclass
class Queue:
    """The questions of one call still to ask, kept with the conversation.

    ``owner`` is ``evokai.capabilities.stores.Owner`` with the conversation's session id.
    """

    owner: Any
    ttl: timedelta

    async def load(self) -> dict[str, Any] | None:
        """The waiting questions.

        Returns:
            ``{callId, asked: [{question, answer}], pending: [question...]}``, or None.
        """
        o = self.owner
        row = await o.repo.get(o.app_id, o.subject, "ask_user", o.session_id, KEY)
        return json.loads(row.value) if row is not None else None

    async def save(self, state: dict[str, Any]) -> None:
        """Keep the waiting questions."""
        o = self.owner
        await o.repo.put(
            o.app_id,
            o.subject,
            "ask_user",
            o.session_id,
            KEY,
            json.dumps(state),
            session_id=o.session_id,
            expires_at=utcnow() + self.ttl,
        )

    async def clear(self) -> None:
        """Forget them (all answered)."""
        o = self.owner
        await o.repo.delete(o.app_id, o.subject, "ask_user", o.session_id, KEY)


class EvokaiAskToolset(AskUserToolset[RunState]):
    """``ask_user_question`` shown as Evokai ``ask`` events instead of a deferred call."""

    def __init__(
        self, *, capability_id: str | None, can_ask: bool, queue: Queue | None = None
    ) -> None:
        """Register the tool.

        Args:
            capability_id: The capability's id.
            can_ask: Whether a person can answer (a turn of an application granted questions).
            queue: Where the questions after the first wait (None: one question per call).
        """
        self.can_ask = can_ask
        self.queue = queue
        super().__init__(capability_id=capability_id)

    async def ask_user_question(self, ctx: RunContext[RunState], questions: Questions) -> str:
        """Ask the user multiple-choice questions; each one ends the turn and its answer comes next.

        Use it only when the request is ambiguous and the answer is nowhere else; never to confirm
        a change to data (the application asks for that). The questions are shown one at a time;
        you get every answer together once the last one is answered.

        Args:
            ctx: The run.
            questions: One or more questions, two to five short options each.

        Returns:
            What the model reads.
        """
        st = ctx.deps
        if not self.can_ask:
            return NO_ONE_TO_ASK
        call_id = ctx.tool_call_id or ""
        first, rest = questions[0], list(questions[1:])
        event = event_of(first, call_id, 0)
        st.show(event)
        if st.say is not None:
            st.say(event["question"])
        st.trace.event(
            "capability",
            "ask_user asked",
            capability="ask_user",
            questions=len(questions),
            options=len(event["options"]),
        )
        if not rest:
            return ASKED
        if self.queue is None:
            return f"{ASKED} Only the first question was shown: ask the others after it."
        await self.queue.save({
            "callId": call_id,
            "asked": [{"question": event["question"], "event": event}],
            "pending": [q.model_dump(mode="json") for q in rest],
        })
        return (
            f"Question 1 of {len(questions)} shown. The person answers each question as a message "
            "and the next one is shown to them; you will get every answer together after the "
            "last. Do not answer for them and do not go on with the task until then."
        )


@dataclass(kw_only=True)
class EvokaiAskUser(AskUser[RunState]):
    """``AskUser`` whose questions end the turn, one at a time (see the module)."""

    can_ask: bool = False
    """Whether a person can answer (a turn of an application granted questions)."""
    queue: Queue | None = field(default=None, repr=False)
    """Where the questions after the first wait."""

    def get_toolset(self) -> Any:
        """The ``ask_user_question`` tool.

        Returns:
            The toolset.
        """
        return EvokaiAskToolset(capability_id=self.id, can_ask=self.can_ask, queue=self.queue)

    async def before_model_request(self, ctx: RunContext[RunState], request_context: Any) -> Any:
        """At a turn's first request: record the answer, then ask the next question or hand over.

        A rewritten answer (the person edited it, nothing was written since) replaces the answer
        it rewrites: the question after it is shown again, or the model gets the answers again.

        When the next question is shown the turn ends on it (``StopRun``, reason ``ask``) and the
        question is kept as the turn's answer, so the next message answers it.

        Returns:
            The request (with every question and its answer once the last one is answered).
        """
        st = ctx.deps
        if self.queue is None or st.extra.get("askQueue"):
            return request_context
        st.extra["askQueue"] = True
        state = await self.queue.load()
        if not state:
            return request_context
        prompts = _prompts(list(ctx.messages))
        answer = prompts[-1] if prompts else ""
        before = prompts[-2] if len(prompts) > 1 else None
        asked = state["asked"]
        if state.get("done"):
            if before == asked[-1].get("answer"):
                # A new message after the answers: the questions are over.
                await self.queue.clear()
                return request_context
            asked[-1]["answer"] = answer
            await self.queue.save(state)
            return _with_answers(request_context, asked)
        if len(asked) > 1 and before != asked[-2].get("answer"):
            # The previous answer was rewritten: it replaces that answer, and the question it led
            # to is shown again.
            asked[-2]["answer"] = answer
            await self.queue.save(state)
            self._show(st, asked[-1]["event"])
        asked[-1]["answer"] = answer
        if state["pending"]:
            q = state["pending"].pop(0)
            event = event_of(q, state["callId"], len(asked))
            asked.append({"question": event["question"], "event": event})
            await self.queue.save(state)
            self._show(st, event)
        state["done"] = True
        await self.queue.save(state)
        return _with_answers(request_context, asked)

    @staticmethod
    def _show(st: RunState, event: dict[str, Any]) -> None:
        st.show(event)
        if st.say is not None:
            st.say(event["question"])
        # Kept as the turn's answer, like ``ui_ask``: the next message answers it.
        raise StopRun("ask", detail={"store": [{"text": event["question"]}]})


def _with_answers(request_context: Any, asked: list[dict[str, Any]]) -> Any:
    lines = "\n".join(f'- "{a["question"]}": {a.get("answer") or ""}' for a in asked)
    note = UserPromptPart(content=f"My answers to your questions:\n{lines}")
    messages = list(request_context.messages)
    if messages and isinstance(messages[-1], ModelRequest):
        messages[-1] = replace(messages[-1], parts=[*messages[-1].parts, note])
        request_context.messages = messages
    return request_context


def _prompts(messages: list[Any]) -> list[str]:
    """What the person wrote, oldest first (an answer without the turn's «Answer to» prefix).

    Returns:
        Texts.
    """
    out: list[str] = []
    for m in messages:
        if not isinstance(m, ModelRequest):
            continue
        for p in m.parts:
            if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                text = p.content
                if text.startswith(ANSWER_PREFIX) and '": ' in text:
                    text = text.split('": ', 1)[1]
                out.append(text[:2000])
    return out
