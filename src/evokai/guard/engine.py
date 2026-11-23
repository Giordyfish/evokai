"""The guard of turns and runs: every control in one place, with its security events.

``Guard`` lives for the process (rate windows, concurrency counts, the optional classifier);
``TurnGuard`` lives for one turn or one server run and is what the turn engine calls at each step:

```
admit, check_input, for_model (spotlight, tokens), output filter, check_call, tool_result
```
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from evokai.core.errors import AppError
from evokai.core.shared import MemoryState, SharedState
from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.guard import content, identifiers, injection, output, pii, secrets
from evokai.guard import tools as tp
from evokai.guard.ask import AskMatch, AskRules
from evokai.guard.classifiers import STRONG, Classifiers, Result, Source
from evokai.guard.config import Effective, resolve
from evokai.guard.logfilter import log_masking
from evokai.guard.text import normalize, strip_invisible
from evokai.knowledge.tool import TOOL_NAME as KNOWLEDGE_TOOL
from evokai.knowledge.tool import mark_sources

log = logging.getLogger("evokai.security")

SEVERITY = {
    "input.injection": "high",
    "input.encoding": "medium",
    "input.size": "low",
    "untrusted.injection": "high",
    "pii.model": "medium",
    "pii.history": "medium",
    "pii.logs": "low",
    "pii.browser": "medium",
    "output.secrets": "high",
    "output.system_prompt": "high",
    "output.links": "medium",
    "scope": "low",
    "memory.write": "medium",
    "tools.policy": "medium",
    "tools.args": "low",
    "tools.rate": "medium",
    "tools.egress": "high",
    "tools.loop": "low",
    "turn.budget": "low",
    "abuse.rate": "medium",
    "abuse.concurrency": "medium",
    "abuse.length": "low",
}
SNIPPET_CHARS = 200
RUNNING_TTL = 900
"""Seconds a subject's count of running turns lives after its last new turn."""


def safe_snippet(text: str, known: tuple[str, ...] = ()) -> str:
    """A short excerpt fit for an event: identifiers masked, secrets removed, clipped.

    Returns:
        The excerpt.
    """
    s = secrets.redact(pii.mask(text[: SNIPPET_CHARS * 3]), known=known)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:SNIPPET_CHARS] + ("…" if len(s) > SNIPPET_CHARS else "")


class GuardRefused(AppError):
    """A request refused by an abuse limit (429)."""

    status = 429

    def __init__(self, code: str, message: str) -> None:
        """Create the refusal.

        Args:
            code: Stable code (``rate_limited``, ``too_busy``, ``conversation_too_long``).
            message: Text for the client.
        """
        super().__init__(message, code=code, status=429)


@dataclass
class InputDecision:
    """What becomes of a user's input."""

    text: str
    blocked: str | None = None
    """The control that refused it (the turn ends with a ``guard`` error), or None."""
    refusal: str | None = None
    """The agent's own refusal text (scope), shown instead of an error."""


def _protection(app: Any) -> dict[str, Any] | None:
    from evokai.protection import protection

    return protection(app)


@dataclass
class TurnGuard:
    """The guard of one turn or run."""

    guard: Guard
    policy: Effective
    application_id: str
    agent: str
    subject: str | None
    session_id: str | None
    turn_id: str | None
    channel: str
    boundary: str
    origins: list[str] = field(default_factory=list)
    vault: pii.Vault | None = None
    declared: tuple[pii.Identifier, ...] = ()
    """The application's own identifiers (``application.identifiers``)."""
    instructions: str = ""
    tags: list[str] = field(default_factory=list)
    """The session's tags (for the usage records of a billed classifier)."""
    ask: AskRules = field(default_factory=AskRules)
    """The ask rules of this turn or run: the deployment's, the application's and the agent's, for
    the subject's tags (``evokai.guard.ask``). They apply whatever the controls' modes: they only
    add confirmations."""
    events: list[dict[str, Any]] = field(default_factory=list)
    trace: Any = None
    """The turn's trace (``evokai.observability.tracing.Trace``): every event is also a span."""
    calls: tp.TurnCalls = field(default_factory=tp.TurnCalls)
    _shingles: set[str] | None = None
    _pii: dict[str, dict[str, int]] = field(default_factory=dict)
    _classifier_left: int | None = None
    _classifier_noted: set[tuple[str, str]] = field(default_factory=set)
    protection: dict[str, Any] | None = None
    """The application's data protection profile and residency (``evokai.protection``): a
    processor it excludes (a classifier, the code sandbox) is never sent data."""
    checked: list[str] = field(default_factory=list)
    """Every input this guard checked, as it was given and as it went on (an input guardrail
    capability does not check them twice)."""

    # events

    def record(
        self,
        control: str,
        rule: str,
        action: str,
        *,
        snippet: str = "",
        details: dict[str, Any] | None = None,
        tool: str | None = None,
        direction: str | None = None,
        severity: str | None = None,
    ) -> None:
        """Record a security event (stored at the end of the turn, logged now)."""
        mode = self.policy.mode(control)
        row = {
            "id": new_id(),
            "application_id": self.application_id,
            "created_at": utcnow(),
            "control": control,
            "rule": rule[:60],
            "action": action,
            "mode": mode,
            "severity": severity or SEVERITY.get(control, "medium"),
            "direction": direction,
            "agent_name": self.agent[:80],
            "subject": self.subject,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "tool": tool[:120] if tool else None,
            "snippet": safe_snippet(snippet, self.guard.known_secrets) if snippet else "",
            "details": details or {},
        }
        self.events.append(row)
        if self.trace is not None:
            self.trace.event(
                "guard",
                f"guard {control}",
                control=control,
                rule=row["rule"],
                action=action,
                mode=mode,
                severity=row["severity"],
                direction=direction,
                tool=row["tool"],
            )
        log.log(
            logging.WARNING if action != "logged" else logging.INFO,
            "security_event",
            extra={
                "control": control,
                "rule": rule,
                "action": action,
                "mode": mode,
                "severity": row["severity"],
                "applicationId": self.application_id,
                "agent": self.agent,
                "turnId": self.turn_id,
                "tool": tool,
            },
        )

    async def flush(self) -> None:
        """Store the turn's events (failures are logged, never raised)."""
        self._pii_events()
        if not self.events:
            return
        rows, self.events = self.events, []
        await self.guard.store_events(rows)

    # input

    async def check_input(self, text: str) -> InputDecision:
        """Size, hidden text, injection and scope of a person's message.

        Returns:
            The (possibly cleaned) text and whether the turn may go on.
        """
        decision = await self._check_input(text)
        self.checked += [text, decision.text]
        return decision

    async def _check_input(self, text: str) -> InputDecision:
        p, s = self.policy, self.policy.settings
        limit = (s.input.max_chars if s.input else None) or 0
        if p.on("input.size") and limit and len(text) > limit:
            enforce = p.enforced("input.size")
            self.record(
                "input.size",
                "too_long",
                "blocked" if enforce else "logged",
                details={"chars": len(text), "limit": limit},
                direction="input",
            )
            if enforce:
                return InputDecision(text, blocked="input.size")
        norm = normalize(text)
        if p.on("input.encoding") and (norm.invisible or norm.mixed_scripts or norm.decoded):
            enforce = p.enforced("input.encoding") and bool(norm.invisible)
            self.record(
                "input.encoding",
                "zero_width"
                if norm.invisible
                else "homoglyphs"
                if norm.mixed_scripts
                else "encoded",
                "removed" if enforce else "logged",
                details={
                    "invisible": norm.invisible,
                    "mixedScripts": norm.mixed_scripts,
                    "decoded": len(norm.decoded),
                },
                direction="input",
            )
            if enforce:
                text = strip_invisible(text)
        if self.untrusted_input():
            return InputDecision(await self.untrusted(text, "input"))
        if p.on("input.moderation") and (moderated := await self._moderate(text)) is not None:
            return moderated
        if p.on("input.injection"):
            verdict = injection.scan(text, norm)
            if verdict.score < self._threshold():
                await self._add_classifier(verdict, text, "user", "input.injection")
            score = verdict.score
            if score >= self._threshold():
                enforce = p.enforced("input.injection")
                self.record(
                    "input.injection",
                    (verdict.rules or ["classifier"])[0],
                    "blocked" if enforce else "logged",
                    snippet=verdict.snippet or text,
                    details={
                        "score": score,
                        "rules": verdict.rules,
                        "obfuscation": verdict.obfuscation,
                        **(
                            {"classifier": verdict.classifier}
                            if verdict.classifier is not None
                            else {}
                        ),
                    },
                    direction="input",
                )
                if enforce:
                    return InputDecision(text, blocked="input.injection")
        scope = s.scope
        if p.on("scope") and scope and scope.deny:
            hit = next((d for d in scope.deny if _topic(d).search(norm.text)), None)
            if hit:
                enforce = p.enforced("scope")
                self.record(
                    "scope",
                    "denied_topic",
                    "blocked" if enforce else "logged",
                    snippet=text,
                    details={"topic": hit[:80]},
                    direction="input",
                )
                if enforce:
                    return InputDecision(text, blocked="scope", refusal=scope.refusal)
        return InputDecision(text)

    async def _add_classifier(
        self,
        verdict: injection.Verdict,
        text: str,
        source: Source,
        control: str,
        *,
        tool: str | None = None,
    ) -> None:
        """Ask the policy's classifier about a text and add its opinion to the verdict.

        A score of 0.8 or more adds one strong rule (``classifier``, 3 points). Asked only when the
        rules alone do not reach the threshold (its answer could not change the outcome otherwise,
        and it may be billed). Fails open: a missing, failing or slow classifier records
        ``classifier_unavailable`` once per turn and the verdict stays the rules' own.
        """
        s = self.policy.settings.injection
        cid = self.guard.classifiers.resolve(s.classifier if s else None)
        if cid is None:
            return
        clf = self.guard.classifiers.get(cid)
        status = clf.status() if clf is not None else None
        if clf is None or status is None or not status.available:
            reason = status.reason if status else "unknown classifier"
            self._classifier_event(control, cid, "classifier_unavailable", {"reason": reason}, tool)
            return
        if excluded := self._excluded(cid):
            self._classifier_event(
                control, cid, "classifier_unavailable", {"reason": excluded}, tool
            )
            return
        if self._classifier_left is None:
            self._classifier_left = (
                s.classifier_max_chars if s and s.classifier_max_chars is not None else 24_000
            )
        budget = self._classifier_left
        if budget <= 0:
            self._classifier_event(
                control,
                cid,
                "classifier_capped",
                {"limit": s.classifier_max_chars if s else None},
                tool,
            )
            return
        try:
            result = await asyncio.wait_for(
                clf.classify(text, source=source, budget=budget), clf.timeout + 0.5
            )
        except Exception as e:
            reason = "timeout" if isinstance(e, TimeoutError) else type(e).__name__
            if isinstance(e, RuntimeError) and str(e):
                reason = str(e)[:200]
            log.warning("guard_classifier_failed", extra={"classifier": cid, "error": reason})
            self._classifier_event(control, cid, "classifier_unavailable", {"reason": reason}, tool)
            return
        self._classifier_left = max(0, budget - result.chars)
        if clf.billed and (result.units or result.calls):
            await self.guard.record_classifier_usage(self, cid, result)
        if result.error:
            self._classifier_event(
                control, cid, "classifier_unavailable", {"reason": result.error}, tool
            )
        if result.capped:
            self._classifier_event(
                control,
                cid,
                "classifier_capped",
                {"limit": s.classifier_max_chars if s else None},
                tool,
            )
        verdict.classifier = round(result.score, 3)
        if result.score >= STRONG:
            verdict.score += 3
            verdict.rules.append("classifier")
            if not verdict.snippet:
                verdict.snippet = text[:200]

    def _excluded(self, cid: str) -> str | None:
        """Why the application's data protection profile keeps texts from a classifier.

        Returns:
            ``data_protection: <dimension> <reason>, ...``, or None when it may be used.
        """
        from evokai.guard.classifiers import PROCESSORS
        from evokai.protection import processor_verdict

        if not self.protection:
            return None
        region = None
        if cid == "bedrock_guardrails":
            region = getattr(self.guard.classifiers.get(cid), "region", None)
        v = processor_verdict(self.protection, PROCESSORS.get(cid, cid), region=region)
        if v.allowed:
            return None
        return "data_protection: " + ", ".join(f"{r.dimension} {r.code}" for r in v.reasons)

    async def _moderate(self, text: str) -> InputDecision | None:
        """The ``input.moderation`` control: a moderation classifier on the person's message.

        Fails open like the injection classifier: an unavailable, excluded or failing classifier
        records ``classifier_unavailable`` and the turn goes on.

        Returns:
            A blocking decision when the control enforces and the message is harmful, else None.
        """
        p = self.policy
        s = p.settings.moderation
        cid = s.classifier if s else None
        if not cid or cid == "none":
            return None
        control = "input.moderation"
        clf = self.guard.classifiers.get_moderation(cid)
        status = clf.status() if clf is not None else None
        if clf is None or status is None or not status.available:
            reason = status.reason if status else "unknown classifier"
            self._classifier_event(control, cid, "classifier_unavailable", {"reason": reason}, None)
            return None
        if excluded := self._excluded(cid):
            self._classifier_event(
                control, cid, "classifier_unavailable", {"reason": excluded}, None
            )
            return None
        try:
            result = await asyncio.wait_for(
                clf.classify(text, source="user", budget=len(text)), clf.timeout + 0.5
            )
        except Exception as e:
            reason = "timeout" if isinstance(e, TimeoutError) else type(e).__name__
            if isinstance(e, RuntimeError) and str(e):
                reason = str(e)[:200]
            self._classifier_event(control, cid, "classifier_unavailable", {"reason": reason}, None)
            return None
        if clf.billed and (result.units or result.calls):
            await self.guard.record_classifier_usage(self, cid, result)
        if result.error:
            self._classifier_event(
                control, cid, "classifier_unavailable", {"reason": result.error}, None
            )
        threshold = s.threshold if s and s.threshold is not None else STRONG
        if result.score < threshold:
            return None
        enforce = p.enforced(control)
        self.record(
            control,
            "harmful",
            "blocked" if enforce else "logged",
            snippet=text,
            details={"classifier": cid, "score": round(result.score, 3)},
            direction="input",
        )
        return InputDecision(text, blocked=control) if enforce else None

    def _classifier_event(
        self, control: str, cid: str, rule: str, details: dict[str, Any], tool: str | None
    ) -> None:
        """Note a classifier problem once per turn (low severity: the turn went on)."""
        if (rule, cid) in self._classifier_noted:
            return
        self._classifier_noted.add((rule, cid))
        self.record(
            control,
            rule,
            "logged",
            details={"classifier": cid, **details},
            tool=tool,
            direction="classifier",
            severity="low",
        )

    def _threshold(self) -> int:
        s = self.policy.settings.injection
        return (s.threshold if s else None) or 3

    def untrusted_input(self) -> bool:
        """Whether the user's own input is data (an e-mail given to a server run).

        Returns:
            True when it must be marked and scanned as untrusted content.
        """
        mode = (
            self.policy.settings.untrusted.input if self.policy.settings.untrusted else None
        ) or "auto"
        return mode == "untrusted" or (mode == "auto" and self.channel == "server")

    # untrusted content

    async def untrusted(self, text: str, source: str, *, tool: str | None = None) -> str:
        """Scan untrusted content (tool output, page text, documents, e-mails) for instructions.

        The rules run first; the policy's classifier, when there is one, is asked when they do not
        reach the threshold.

        Returns:
            The content; in enforce mode, instruction-like lines are replaced (all of it when the
            classifier flagged it and no line reads as an instruction).
        """
        p = self.policy
        if not p.on("untrusted.injection") or not text:
            return text
        verdict = injection.scan(text)
        if verdict.score < self._threshold():
            await self._add_classifier(verdict, text, "untrusted", "untrusted.injection", tool=tool)
        n = content.neutralize(
            text,
            enforce=p.enforced("untrusted.injection"),
            threshold=self._threshold(),
            verdict=verdict,
        )
        if n.verdict.flagged(self._threshold()):
            self.record(
                "untrusted.injection",
                n.verdict.rules[0] if n.verdict.rules else "flagged",
                "removed" if n.removed else "logged",
                snippet=n.verdict.snippet or text,
                details={
                    "source": source[:80],
                    "score": n.verdict.score,
                    "rules": n.verdict.rules,
                    "removedLines": n.removed,
                    **(
                        {"classifier": n.verdict.classifier}
                        if n.verdict.classifier is not None
                        else {}
                    ),
                },
                tool=tool,
                direction="untrusted",
            )
        return n.text

    @property
    def spotlighting(self) -> bool:
        """Whether untrusted content is marked for the model.

        Returns:
            True when ``untrusted.spotlight`` is enforced.
        """
        return self.policy.enforced("untrusted.spotlight")

    def mark(self, text: str, source: str) -> str:
        """Mark untrusted content as data, when spotlighting is on.

        Returns:
            The marked text.
        """
        return content.spotlight(text, source, self.boundary) if self.spotlighting else text

    def system_notes(self) -> str:
        """What the guard adds to the stable part of the system prompt.

        Returns:
            The notes (empty when none).
        """
        notes = []
        if self.spotlighting:
            notes.append(content.SPOTLIGHT_NOTE)
        scope = self.policy.settings.scope
        if self.policy.on("scope") and scope and scope.topics:
            notes.append(
                "Scope: answer only about "
                + "; ".join(scope.topics)
                + ". For anything else, say briefly that it is outside what you can help with"
                + (f" (for example: «{scope.refusal}»)." if scope.refusal else ".")
            )
        return "\n\n".join(notes)

    # personal data towards the model and at rest

    def for_model(self, messages: list[Any]) -> list[Any]:
        """The history as the model receives it: tool output marked, identifiers protected.

        Args:
            messages: ``Stored`` messages (role, content blocks), not changed.

        Returns:
            New ``Stored`` messages.
        """
        from evokai.sessions.history import Stored

        protect = self._model_pii()
        found: dict[str, int] = {}
        out = []
        for msg in messages:
            blocks = []
            for b in msg.content:
                if "toolResult" in b:
                    tr = dict(b["toolResult"])
                    if tr.get("name") == KNOWLEDGE_TOOL:
                        # Every passage is spotlighted under its own document.
                        tr["text"] = mark_sources(protect(tr.get("text") or "", found), self.mark)
                    else:
                        tr["text"] = self.mark(
                            protect(tr.get("text") or "", found), f"tool:{tr.get('name', '')}"
                        )
                    blocks.append({"toolResult": tr})
                elif "toolUse" in b:
                    tu = dict(b["toolUse"])
                    tu["input"] = pii.transform_any(
                        tu.get("input") or {}, lambda t: protect(t, found)
                    )
                    blocks.append({"toolUse": tu})
                elif "file" in b and b.get("text"):
                    # A document the person attached: read from a file, so it is data.
                    source = f"attachment:{(b.get('file') or {}).get('name', '')}"
                    blocks.append({**b, "text": self.mark(protect(b["text"], found), source)})
                elif b.get("text") and b.get("summary"):
                    # The summary of older messages (compaction): written by a model from the
                    # conversation and its tool results, so it is data, never the person's words.
                    blocks.append({**b, "text": self.mark(protect(b["text"], found), "summary")})
                elif b.get("text"):
                    text = protect(b["text"], found)
                    if msg.role == "user" and self.untrusted_input():
                        text = self.mark(text, "input")
                    blocks.append({**b, "text": text})
                else:
                    blocks.append(b)
            out.append(Stored(role=msg.role, content=blocks))
        self._pii_event("pii.model", found)
        return out

    def protect_text(self, text: str) -> str:
        """Protect one text on its way to the model (page text, a run's input).

        Returns:
            The text with identifiers tokenised or masked, as the policy says.
        """
        found: dict[str, int] = {}
        out = self._model_pii()(text, found)
        self._pii_event("pii.model", found, add=True)
        return out

    def _types(self) -> frozenset[str] | None:
        s = self.policy.settings.pii
        return frozenset(s.types) if s and s.types else None

    def _model_pii(self) -> Any:
        p = self.policy
        types = self._types()
        if not p.on("pii.model"):
            return lambda t, _found: t
        enforce = p.enforced("pii.model")
        vault = self.vault
        declared = self.declared

        def protect(text: str, found: dict[str, int]) -> str:
            if not text:
                return text
            if enforce and vault is not None:
                text, spans = vault.tokenize(text)
            else:
                spans = pii.find(text, types, declared)
                if enforce and spans:
                    text = pii.mask(text, types, declared)
            for t, n in pii.counts(spans).items():
                found[t] = found.get(t, 0) + n
            return text

        return protect

    def _pii_event(self, control: str, found: dict[str, int], *, add: bool = False) -> None:
        """Note identifiers seen in a direction; one event per direction is stored at the end.

        Args:
            control: ``pii.model``, ``pii.history`` or ``pii.browser``.
            found: type → count.
            add: Add to what was seen (each stored message counts) instead of keeping the largest
                count (each model call re-sends the whole history).
        """
        seen = self._pii.setdefault(control, {})
        for t, n in found.items():
            seen[t] = seen.get(t, 0) + n if add else max(seen.get(t, 0), n)

    def _pii_events(self) -> None:
        for control, found in self._pii.items():
            if not found:
                continue
            action = "logged"
            if self.policy.enforced(control):
                action = (
                    "tokenized" if control == "pii.model" and self.vault is not None else "redacted"
                )
            self.record(
                control,
                "personal_data",
                action,
                details={"types": found},
                direction=control.split(".", 1)[1],
            )
        self._pii = {}

    def for_storage(
        self, content_blocks: list[dict[str, Any]], text: str
    ) -> tuple[list[dict[str, Any]], str]:
        """What is stored of a message: identifiers masked when ``pii.history`` is enforced.

        Returns:
            The content blocks and the text to store.
        """
        p = self.policy
        if not p.on("pii.history"):
            return content_blocks, text
        types = self._types()
        found: dict[str, int] = {}

        def scan(t: str) -> str:
            spans = pii.find(t, types, self.declared)
            for k, n in pii.counts(spans).items():
                found[k] = found.get(k, 0) + n
            return pii.mask(t, types, self.declared) if spans and p.enforced("pii.history") else t

        blocks = [pii.transform_any(b, scan) for b in content_blocks]
        text = scan(text) if text else text
        self._pii_event("pii.history", found, add=True)
        return blocks, text

    def persisted(self, event: dict[str, Any]) -> dict[str, Any]:
        """An event as it is stored for replay: identifiers masked (``pii.history`` enforced).

        Returns:
            The stored copy.
        """
        types, declared = self._types(), self.declared
        if event.get("type") == "text":
            return {**event, "delta": pii.mask(str(event.get("delta") or ""), types, declared)}
        chart = event.get("chart")
        if (
            event.get("type") == "chart"
            and isinstance(chart, dict)
            and chart.get("type") == "image"
        ):
            # An image (``code_run``) is bytes in base64: masking would corrupt it, never protect.
            data = chart.get("data")
            masked = pii.transform_any(
                {**event, "chart": {**chart, "data": ""}}, lambda t: pii.mask(t, types, declared)
            )
            return {**masked, "chart": {**masked["chart"], "data": data}}
        return pii.transform_any(event, lambda t: pii.mask(t, types, declared))

    # output

    def output_filter(self) -> output.OutputFilter:
        """A filter for one model step's text.

        Returns:
            The filter.
        """
        p = self.policy
        rules = output.OutputRules(
            secrets=p.enforced("output.secrets"),
            pii=p.enforced("pii.browser") and self.channel == "browser",
            hold_pii=p.enforced("pii.history"),
            links=p.enforced("output.links") and self.channel == "browser",
            leak=p.enforced("output.system_prompt"),
            pii_types=self._types(),
            pii_declared=self.declared,
            link_domains=self._link_domains(),
            known_secrets=self.guard.known_secrets,
            prompt_shingles=self.prompt_shingles() if p.enforced("output.system_prompt") else set(),
            vault=self.vault,
        )
        return output.OutputFilter(rules)

    def link_allowed(self, url: str) -> bool:
        """Whether a link may reach the browser (``output.links``, as for links in answers).

        Returns:
            False only when the control is enforced, towards the browser, for a target outside the
            allowed domains.
        """
        if self.channel != "browser" or not self.policy.enforced("output.links"):
            return True
        return content.url_allowed(url, self._link_domains())

    def _link_domains(self) -> list[str]:
        links = self.policy.settings.links
        return content.origin_hosts(self.origins) + list((links.allow if links else None) or [])

    def prompt_shingles(self) -> set[str]:
        """Shingles of the agent's instructions (not of its skills, which it may quote).

        Returns:
            The shingles.
        """
        if self._shingles is None:
            self._shingles = output.shingles(self.instructions)
        return self._shingles

    def after_output(self, f: output.OutputFilter) -> None:
        """Record what the output checks found in a finished step."""
        p = self.policy
        c = f.changes
        if c.secrets:
            self.record(
                "output.secrets",
                next(iter(c.secrets)),
                "redacted",
                details={"rules": c.secrets},
                direction="output",
            )
        elif p.on("output.secrets") and (found := secrets.find(f.raw, self.guard.known_secrets)):
            self.record(
                "output.secrets",
                found[0].rule,
                "logged",
                details={"count": len(found)},
                direction="output",
            )
        if c.leak:
            self.record(
                "output.system_prompt",
                "instructions_repeated",
                "blocked",
                snippet=f.raw[-400:],
                direction="output",
            )
        elif p.on("output.system_prompt") and output.leaked(f.raw, self.prompt_shingles()):
            self.record(
                "output.system_prompt",
                "instructions_repeated",
                "logged",
                snippet=f.raw[-400:],
                direction="output",
            )
        if self.channel == "browser":
            if c.links:
                self.record(
                    "output.links",
                    "disallowed_target",
                    "redacted",
                    details={"links": c.links[:10]},
                    direction="output",
                )
            elif p.on("output.links") and (bad := content.bad_links(f.raw, self._link_domains())):
                self.record(
                    "output.links",
                    "disallowed_target",
                    "logged",
                    details={"links": [x.url[:200] for x in bad[:10]]},
                    direction="output",
                )
            if c.pii:
                self._pii_event("pii.browser", c.pii, add=True)
            elif p.on("pii.browser"):
                shown = self.vault.restore(f.raw) if self.vault else f.raw
                spans = pii.find(shown, self._types(), self.declared)
                self._pii_event("pii.browser", pii.counts(spans), add=True)

    def check_final(self, value: Any) -> Any:
        """A server run's answer: tokens put back, secrets removed when enforced.

        Returns:
            The answer.
        """
        value = output.restore_output(value, self.vault)
        p = self.policy
        if not p.on("output.secrets"):
            return value
        found: list[str] = []

        def scan(t: str) -> str:
            hits = secrets.find(t, self.guard.known_secrets)
            found.extend(h.rule for h in hits)
            return secrets.redact(t, hits) if hits and p.enforced("output.secrets") else t

        value = pii.transform_any(value, scan)
        if found:
            action = "redacted" if p.enforced("output.secrets") else "logged"
            self.record(
                "output.secrets",
                found[0],
                action,
                details={"count": len(found)},
                direction="output",
            )
        return value

    # tools

    def offered(self, name: str) -> bool:
        """Whether a tool may be offered to the model (deny and allow lists).

        Returns:
            False only when the tool policy is enforced and the tool is not allowed.
        """
        tools = self.policy.settings.tools
        if tools is None or not self.policy.enforced("tools.policy"):
            return True
        return tp.offered(name, tools)

    def forced_confirmation(self, name: str) -> bool:
        """Whether the policy makes a tool wait for the user's confirmation.

        Returns:
            True for tools in ``tools.confirm`` (tool policy enforced).
        """
        tools = self.policy.settings.tools
        return (
            bool(tools)
            and self.policy.enforced("tools.policy")
            and tp.needs_confirmation(name, tools)
        )

    def asks(self, name: str, args: dict[str, Any]) -> AskMatch | None:
        """The ask rule that makes this call wait for the person's confirmation, if any.

        Returns:
            The match (the last rule that matches says ``ask``), or None.
        """
        return self.ask.match(name, args)

    def askable(self, name: str) -> bool:
        """Whether a rule may make some call of this tool wait for the person.

        Such a tool is not read-only for Code Mode, subagents and server runs.

        Returns:
            True when it may ask.
        """
        return self.ask.askable(name)

    async def check_call(
        self, name: str, args: dict[str, Any], schema: dict[str, Any] | None
    ) -> tp.ToolDecision:
        """Every check of a tool call, in order: policy, arguments, egress, loop, rate.

        Returns:
            The first refusal of an enforced control, else allowed (monitor findings are recorded).
        """
        tools = self.policy.settings.tools
        if tools is None:
            return tp.ALLOW
        checks: list[tuple[str, tp.ToolDecision]] = []
        if self.policy.on("tools.policy") and not tp.offered(name, tools):
            checks.append((
                "tools.policy",
                tp.ToolDecision(False, "denied_tool", "this tool is not allowed"),
            ))
        if self.policy.on("tools.args"):
            checks.append(("tools.args", tp.check_args(args, schema)))
        if self.policy.on("tools.egress"):
            checks.append(("tools.egress", tp.check_egress(name, args, tools)))
        if self.policy.on("tools.loop"):
            checks.append(("tools.loop", self.calls.loop(name, args, tools.loop_threshold or 3)))
        if self.policy.on("tools.rate") and self.subject is not None:
            checks.append(("tools.rate", await self.guard.tool_rate(self, name, tools)))
        for control, d in checks:
            if d.allowed:
                continue
            enforce = self.policy.enforced(control)
            self.record(
                control,
                d.rule,
                "blocked" if enforce else "logged",
                details={"reason": d.reason[:300]},
                tool=name,
                direction="tool",
            )
            if enforce:
                return d
        return tp.ALLOW

    def call_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """A call's arguments as the tool receives them (tokens put back).

        Returns:
            The arguments.
        """
        return self.vault.restore_any(args) if self.vault is not None else args

    async def tool_result(self, name: str, text: str) -> str:
        """A tool's output as it is kept (scanned as untrusted content).

        Returns:
            The output.
        """
        return await self.untrusted(text, f"tool:{name}", tool=name)

    def budget(self, tokens: int) -> bool:
        """Count a model call's tokens; whether the turn's token budget is spent and enforced.

        Returns:
            True when the turn must stop.
        """
        self.calls.tokens += tokens
        limit = (
            self.policy.settings.tools.max_tokens_per_turn if self.policy.settings.tools else None
        )
        if not limit or self.calls.tokens <= limit or not self.policy.on("turn.budget"):
            return False
        enforce = self.policy.enforced("turn.budget")
        if not any(e["rule"] == "max_tokens" for e in self.events):
            self.record(
                "turn.budget",
                "max_tokens",
                "blocked" if enforce else "logged",
                details={"tokens": self.calls.tokens, "limit": limit},
            )
        return enforce

    def max_calls(self, default: int) -> int:
        """The turn's tool-call budget: the agent's, lowered by the policy when it is enforced.

        Returns:
            Calls allowed in the turn.
        """
        limit = (
            self.policy.settings.tools.max_calls_per_turn if self.policy.settings.tools else None
        )
        if limit and self.policy.enforced("turn.budget"):
            return min(default, limit)
        return default

    # memory

    def memory(self, fact: str) -> bool:
        """Whether a fact may be remembered about the user.

        Returns:
            False when ``memory.write`` is enforced and the fact carries instructions, secrets or
            identifiers.
        """
        p = self.policy
        if not p.on("memory.write"):
            return True
        problems = []
        if injection.scan(fact).score >= 2:
            problems.append("instructions")
        if secrets.find(fact, self.guard.known_secrets):
            problems.append("secret")
        if pii.find(fact, self._types(), self.declared):
            problems.append("personal_data")
        if not problems:
            return True
        enforce = p.enforced("memory.write")
        self.record(
            "memory.write",
            problems[0],
            "blocked" if enforce else "logged",
            snippet=fact,
            details={"problems": problems},
        )
        return not enforce


def _topic(word: str) -> re.Pattern[str]:
    """A denied topic: a regular expression when it looks like one, else a whole word or phrase.

    Returns:
        The pattern (over normalised, lower-case text).
    """
    if any(c in word for c in "\\[]()|?*+^$"):
        try:
            return re.compile(word, re.I)
        except re.error:
            pass
    return re.compile(r"\b" + re.escape(normalize(word, decode=False).text) + r"\b", re.I)


class Guard:
    """The process-wide guard: policies, abuse counters, the classifier, event storage."""

    def __init__(
        self, store: Any, settings: Any, keyring: Any, shared: SharedState | None = None
    ) -> None:
        """Wire the guard.

        Args:
            store: Repositories.
            settings: Deployment settings.
            keyring: For boundaries and tokens (keyed hashes) and the secrets to watch for.
            shared: Where rate windows and running turns are counted (every instance's Redis, or
                this process's memory).
        """
        self.store = store
        self.settings = settings
        self.enabled = bool(getattr(settings, "guard_enabled", True))
        self._key = hashlib.sha256(f"evokai-guard:{keyring.signing_key}".encode()).digest()
        known = [
            keyring.signing_key,
            getattr(settings, "encryption_key", ""),
            getattr(settings, "operator_key", "") or "",
        ]
        self.known_secrets: tuple[str, ...] = tuple(s for s in known if s and len(s) >= 12)
        self.classifiers = Classifiers(settings)
        self.shared: SharedState = shared or MemoryState()

    def policy(self, app: m.Application, agent: Any = None) -> Effective:
        """The effective policy of an agent of an application.

        Returns:
            The policy.
        """
        return resolve(
            getattr(app, "security", None),
            getattr(agent, "security", None) if agent is not None else None,
            disabled=not self.enabled,
        )

    def keyed(self, *parts: str) -> str:
        """A keyed hash (boundaries, token keys).

        Returns:
            Hex digest.
        """
        return hmac.new(self._key, "\x00".join(parts).encode(), hashlib.sha256).hexdigest()

    def turn(
        self,
        app: m.Application,
        agent: Any,
        *,
        subject: str | None,
        session_id: str | None,
        turn_id: str | None,
        channel: str,
        instructions: str = "",
        tags: list[str] | None = None,
    ) -> TurnGuard:
        """The guard of one turn (browser) or run (server).

        Returns:
            The turn guard.
        """
        policy = self.policy(app, agent)
        scope_id = session_id or turn_id or new_id()
        declared = identifiers.of_app(app)
        vault = None
        if (
            policy.enforced("pii.model")
            and policy.settings.pii
            and policy.settings.pii.model_action != "mask"
        ):
            types = frozenset(policy.settings.pii.types) if policy.settings.pii.types else None
            vault = pii.Vault(bytes.fromhex(self.keyed("pii", app.id, scope_id)), types, declared)
        log_masking.set(policy.enforced("pii.logs"))
        return TurnGuard(
            guard=self,
            policy=policy,
            application_id=app.id,
            agent=getattr(agent, "name", "") or "",
            subject=subject,
            session_id=session_id,
            turn_id=turn_id,
            channel=channel,
            boundary=self.keyed("boundary", app.id, scope_id)[:12],
            origins=list(app.allowed_origins or []),
            vault=vault,
            declared=declared,
            instructions=instructions or getattr(agent, "instructions", "") or "",
            tags=list(tags or []),
            protection=_protection(app),
            ask=AskRules.build(
                deployment=getattr(self.settings, "ask_rules", None),
                application=getattr(app, "ask_rules", None),
                agent=getattr(agent, "ask_rules", None) if agent is not None else None,
                tags=list(tags or []),
            ),
        )

    # abuse

    def _shared_key(self, *parts: str) -> str:
        """A shared-state key: a keyed hash, so no subject is ever written to it.

        Returns:
            The key.
        """
        return f"guard:{parts[0]}:{self.keyed(*parts)[:40]}"

    async def _window(self, *key: str) -> int:
        """Count one more in the current minute of a window (every instance together).

        Returns:
            The minute's count.
        """
        bucket = str(int(time.time() // 60))
        return await self.shared.incr(self._shared_key(*key, bucket), 120)

    async def _note(
        self, tg: TurnGuard, control: str, rule: str, action: str, details: dict[str, Any]
    ) -> None:
        key = self._shared_key("noted", tg.application_id, tg.subject or "", control)
        first = await self.shared.set_once(key, "1", 10)
        if not first and action == "logged":
            return
        tg.record(control, rule, action, details=details, direction="abuse")

    def _running_key(self, tg: TurnGuard) -> str:
        return self._shared_key("active", tg.application_id, tg.subject or "")

    async def admit(self, tg: TurnGuard, *, turns_so_far: int | None = None) -> None:
        """Abuse limits of a new turn or run: rate, concurrency, conversation length.

        An enforced limit raises ``GuardRefused`` (429); its event is stored at once.
        """
        p, a = tg.policy, tg.policy.settings.abuse
        if a is None:
            return
        refusal: GuardRefused | None = None
        kind = "run" if tg.channel == "server" else "turn"
        rate = a.runs_per_minute if kind == "run" else a.requests_per_minute
        if p.on("abuse.rate") and rate:
            n = await self._window("abuse", tg.application_id, tg.subject or "", kind)
            if n > rate:
                enforce = p.enforced("abuse.rate")
                await self._note(
                    tg,
                    "abuse.rate",
                    "requests_per_minute",
                    "blocked" if enforce else "logged",
                    {"count": n, "limit": rate},
                )
                if enforce:
                    refusal = GuardRefused(
                        "rate_limited", "Too many requests. Try again in a minute."
                    )
        if refusal is None and p.on("abuse.concurrency") and a.concurrent_turns and kind == "turn":
            running = await self.shared.count(self._running_key(tg))
            if running >= a.concurrent_turns:
                enforce = p.enforced("abuse.concurrency")
                await self._note(
                    tg,
                    "abuse.concurrency",
                    "concurrent_turns",
                    "blocked" if enforce else "logged",
                    {"running": running, "limit": a.concurrent_turns},
                )
                if enforce:
                    refusal = GuardRefused(
                        "too_busy", "Too many answers in progress. Wait for one to finish."
                    )
        limit = a.max_turns_per_conversation
        if (
            refusal is None
            and p.on("abuse.length")
            and limit
            and turns_so_far is not None
            and turns_so_far >= limit
        ):
            enforce = p.enforced("abuse.length")
            await self._note(
                tg,
                "abuse.length",
                "max_turns_per_conversation",
                "blocked" if enforce else "logged",
                {"turns": turns_so_far, "limit": limit},
            )
            if enforce:
                refusal = GuardRefused(
                    "conversation_too_long", "This conversation is too long. Start a new one."
                )
        if refusal is not None:
            await tg.flush()
            raise refusal

    async def started(self, tg: TurnGuard) -> None:
        """Count a running turn of the subject (concurrency, every instance together).

        The count expires after ``RUNNING_TTL`` seconds without a new turn, so the turns of an
        instance that died do not hold the subject back for good.
        """
        await self.shared.acquire(self._running_key(tg), RUNNING_TTL)

    async def finished(self, tg: TurnGuard) -> None:
        """A turn of the subject ended."""
        await self.shared.release(self._running_key(tg))

    async def tool_rate(self, tg: TurnGuard, name: str, settings: Any) -> tp.ToolDecision:
        """Count a tool call against its rate limit.

        Returns:
            The decision.
        """
        rate = tp.rate_for(name, settings)
        if rate is None:
            return tp.ALLOW
        pattern, limit = rate
        n = await self._window(
            "tool", tg.application_id, tg.subject or "", pattern if pattern != "*" else name
        )
        if n > limit:
            return tp.ToolDecision(
                False, "rate", f"{name} was called {n} times this minute (limit {limit})"
            )
        return tp.ALLOW

    async def record_classifier_usage(self, tg: TurnGuard, cid: str, result: Result) -> None:
        """Record a billed classification as usage (kind ``guard``), so caps and reports count it.

        ``input_tokens`` holds the billed text units, ``characters`` the characters sent. Never
        raises: accounting must not fail a turn.
        """
        from evokai.db.repositories import Who
        from evokai.guard.classifiers import PROCESSORS
        from evokai.llm.prices import classifier_cost_micro_usd, guardrail_cost_micro_usd

        try:
            await self.store.usage.record(
                Who(tg.application_id, tg.subject or ""),
                tg.tags,
                agent_name=tg.agent[:80],
                session_id=tg.session_id,
                turn_id=tg.turn_id,
                kind="guard",
                model=cid,
                input_tokens=result.units,
                characters=result.chars,
                cost_micro_usd=guardrail_cost_micro_usd(result.units)
                if cid == "bedrock_guardrails"
                else classifier_cost_micro_usd(PROCESSORS.get(cid, cid), result.units),
                latency_ms=result.latency_ms,
            )
        except Exception as e:
            log.warning("guard_usage_not_recorded", extra={"error": type(e).__name__})

    async def store_events(self, rows: Iterable[dict[str, Any]]) -> None:
        """Store events (never raises: a failing store must not fail a turn)."""
        try:
            await self.store.security_events.add_many(list(rows))
        except Exception as e:
            log.warning("security_events_not_stored", extra={"error": type(e).__name__})
