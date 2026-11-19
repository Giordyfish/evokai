"""Amazon Bedrock Guardrails as a prompt-injection classifier (``bedrock_guardrails``).

Each text is sent to ``ApplyGuardrail`` of a guardrail that holds only the content filter for
``PROMPT_ATTACK`` (Standard tier, EU cross-Region guardrail profile), in pieces of at most 1,000
characters, as ``source=INPUT`` with the ``guard_content`` qualifier.

What the AWS documentation says, and why it is done this way:

- the prompt attack filter is an input filter (it has an input strength only), so every text goes
  as ``INPUT``, the person's message and untrusted content alike;
- inside ``Converse``/``InvokeModel`` it never evaluates tool results (``toolResult``) and needs
  input tags to evaluate anything; ``ApplyGuardrail`` evaluates exactly the text blocks it is given.
  Tool output, pages, documents and e-mails therefore reach it as their own text blocks, which is
  the only way they are checked at all; the ``guard_content`` qualifier marks each block as the
  content to guard;
- it is billed per text unit of up to 1,000 characters (the same for the Standard and Classic
  tiers), so pieces never exceed one unit, and a turn sends at most
  ``injection.classifier_max_chars`` characters.

The result maps to a score: a ``PROMPT_ATTACK`` filter that was detected (or blocked) with
confidence ``LOW``, ``MEDIUM`` or ``HIGH`` scores 0.5, 0.8 or 0.95; nothing detected is 0. The
highest score over the pieces is the text's. Calls run in threads (boto3 is synchronous), a few
at a time, each with short connect and read timeouts and no retries. The classification stops at
its timeout (1.5 s by default): what finished counts, requests already sent are counted as billed,
and the failure is reported (``Result.error``) so the turn records it and goes on.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Any

from evokai.guard.classifiers.base import Result, Source, Status, budgeted, windows

CONFIDENCE = {"NONE": 0.0, "LOW": 0.5, "MEDIUM": 0.8, "HIGH": 0.95}
"""A detected prompt attack's confidence as a probability-like score."""
UNIT_CHARS = 1_000
"""A billed text unit."""
OVERLAP = 100
"""Characters repeated between pieces, so a cut does not split an instruction unseen."""
CONCURRENCY = 8
"""Pieces of one text checked at once."""


def score_of(response: dict[str, Any]) -> float:
    """The prompt attack score of an ``ApplyGuardrail`` response.

    Returns:
        0..1 (0 when no prompt attack was detected).
    """
    best = 0.0
    for a in response.get("assessments") or []:
        for f in (a.get("contentPolicy") or {}).get("filters") or []:
            if f.get("type") != "PROMPT_ATTACK":
                continue
            hit = f.get("detected")
            if hit is None:
                hit = f.get("action") == "BLOCKED"
            if hit:
                best = max(best, CONFIDENCE.get(str(f.get("confidence") or "").upper(), 0.8))
    return best


def units_of(response: dict[str, Any], chars: int) -> int:
    """The text units a call was billed for.

    Returns:
        ``usage.contentPolicyUnits`` when the response has it, else one unit per 1,000 characters.
    """
    usage = response.get("usage") or {}
    units = usage.get("contentPolicyUnits")
    if isinstance(units, int) and units >= 0:
        return units
    return math.ceil(chars / UNIT_CHARS)


class BedrockGuardrails:
    """``ApplyGuardrail`` with a prompt-attack-only guardrail."""

    id = "bedrock_guardrails"
    billed = True

    def __init__(
        self,
        guardrail_id: str | None,
        version: str | None,
        region: str,
        *,
        timeout: float = 1.5,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """Configure the classifier (the client is made on first use).

        Args:
            guardrail_id: The guardrail's id or ARN (``EVOKAI_GUARDRAIL_ID``).
            version: Its published version (``EVOKAI_GUARDRAIL_VERSION``), never ``DRAFT`` in
                production.
            region: Where the guardrail lives (``EVOKAI_GUARDRAIL_REGION``, default
                ``EVOKAI_AWS_REGION``).
            timeout: Seconds a whole classification may take.
            client_factory: Makes the ``bedrock-runtime`` client (tests pass a fake).
        """
        self.guardrail_id = guardrail_id
        self.version = version
        self.region = region
        self.timeout = timeout
        self._factory = client_factory
        self._client: Any = None
        self.last_error: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> BedrockGuardrails:
        """From the deployment settings.

        Returns:
            The classifier.
        """
        return cls(
            getattr(settings, "guardrail_id", None),
            getattr(settings, "guardrail_version", None),
            getattr(settings, "guardrail_region", None)
            or getattr(settings, "aws_region", "eu-north-1"),
            timeout=getattr(settings, "guardrail_timeout_ms", 1500) / 1000,
        )

    def status(self) -> Status:
        """Available when a guardrail and its version are configured.

        Returns:
            The status (with the last failure, if any).
        """
        if not self.guardrail_id or not self.version:
            return Status(
                self.id, False, "EVOKAI_GUARDRAIL_ID and EVOKAI_GUARDRAIL_VERSION are not set"
            )
        if self._factory is None:
            try:
                import boto3  # ruff: ignore[unused-import]
            except ImportError:
                return Status(self.id, False, "boto3 is not installed")
        return Status(
            self.id, True, f"last call failed: {self.last_error}" if self.last_error else None
        )

    def _make_client(self) -> Any:
        if self._factory is not None:
            return self._factory()
        import boto3
        from botocore.config import Config

        return boto3.client(
            "bedrock-runtime",
            region_name=self.region,
            config=Config(
                connect_timeout=1,
                read_timeout=2,
                retries={"total_max_attempts": 1, "mode": "standard"},
                max_pool_connections=32,
            ),
        )

    @property
    def client(self) -> Any:
        """The ``bedrock-runtime`` client (thread-safe, one per process).

        Returns:
            The client.
        """
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _apply(self, piece: str) -> dict[str, Any]:
        return self.client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.version,
            source="INPUT",
            content=[{"text": {"text": piece, "qualifiers": ["guard_content"]}}],
        )

    async def classify(self, text: str, *, source: Source, budget: int) -> Result:
        """Check a text in pieces of one text unit, at most ``budget`` characters in all.

        Args:
            text: The person's message or untrusted content.
            source: Whose text it is (both go as ``INPUT``: the filter only looks at inputs).
            budget: Characters this turn may still send.

        Returns:
            The highest score, characters sent, units billed and calls made.

        Raises:
            RuntimeError: No guardrail configured. A failed or timed-out call is not raised: it is
                in ``Result.error``, with what the other pieces found and what was billed.
        """
        del source  # the same request either way; see the module docstring
        if not self.guardrail_id or not self.version:
            raise RuntimeError(self.status().reason)
        pieces, capped = budgeted(windows(text, UNIT_CHARS, OVERLAP), budget)
        result = Result(capped=capped)
        if not pieces:
            return result
        t0 = time.perf_counter()
        gate = asyncio.Semaphore(CONCURRENCY)
        sent: set[int] = set()

        async def one(i: int, piece: str) -> dict[str, Any]:
            async with gate:
                sent.add(i)
                return await asyncio.to_thread(self._apply, piece)

        tasks = [asyncio.create_task(one(i, p)) for i, p in enumerate(pieces)]
        _done, pending = await asyncio.wait(tasks, timeout=self.timeout)
        for t in pending:
            t.cancel()
        errors: list[str] = []
        for i, (piece, task) in enumerate(zip(pieces, tasks, strict=True)):
            if task in pending:
                if i in sent:
                    # The request left: count it as billed, its answer is lost.
                    result.units += units_of({}, len(piece))
                    result.chars += len(piece)
                    result.calls += 1
                continue
            if (e := task.exception()) is not None:
                errors.append(_error_name(e))
                continue
            r = task.result()
            result.score = max(result.score, score_of(r))
            result.units += units_of(r, len(piece))
            result.chars += len(piece)
            result.calls += 1
        if pending:
            errors.append("timeout")
        result.error = errors[0] if errors else None
        self.last_error = result.error
        result.latency_ms = round((time.perf_counter() - t0) * 1000)
        return result

    async def warm_up(self) -> None:
        """Make the client (no call: a call is billed)."""
        if self.guardrail_id and self.version:
            await asyncio.to_thread(lambda: self.client)


def _error_name(e: BaseException) -> str:
    code = (
        getattr(e, "response", {}).get("Error", {}).get("Code") if hasattr(e, "response") else None
    )
    return str(code or type(e).__name__)[:80]
