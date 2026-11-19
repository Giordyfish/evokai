"""The common face of the injection classifiers: results, status, text windows, ``custom``."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

log = logging.getLogger("evokai.security")

CLASSIFIER_IDS: tuple[str, ...] = (
    "bedrock_guardrails",
    "prompt_guard",
    "azure_prompt_shields",
    "model_armor",
    "mistral_moderation",
    "custom",
)
"""The injection classifiers a policy may name (``injection.classifier``), besides ``none`` and
``default``."""
MODERATION_IDS: tuple[str, ...] = (
    "azure_content_safety",
    "openai_moderation",
    "mistral_moderation",
    "model_armor",
    "llama_guard",
)
"""The moderation classifiers a policy may name (``moderation.classifier``): harmful content, which
is not prompt injection."""
CHOICES: tuple[str, ...] = ("none", "default", *CLASSIFIER_IDS)
PROCESSORS: dict[str, str] = {
    "bedrock_guardrails": "bedrock-guardrails",
    "prompt_guard": "prompt-guard",
    "custom": "custom-classifier",
    "azure_prompt_shields": "azure-prompt-shields",
    "azure_content_safety": "azure-content-safety-text",
    "model_armor": "google-model-armor",
    "mistral_moderation": "mistral-moderation",
    "openai_moderation": "openai-moderation",
    "llama_guard": "llama-guard",
}
"""Each classifier's entry in the provider catalog (``processors``), which the data protection
profile checks before a text is sent."""
STRONG = 0.8
"""A score at or above this counts as one strong rule."""

Source = Literal["user", "untrusted"]
"""Whose text it is: the person's own message, or content from tools, pages, documents, e-mails."""


@dataclass
class Result:
    """What a classifier said about one text."""

    score: float = 0.0
    """The highest probability of an injection over the parts that were checked (0..1)."""
    chars: int = 0
    """Characters checked (sent to the service, or run through the model)."""
    units: int = 0
    """Billed units (Bedrock text units of up to 1,000 characters; 0 for a local model)."""
    calls: int = 0
    """Requests made to the service."""
    capped: bool = False
    """Part of the text was not checked because the turn's budget was spent."""
    latency_ms: int = 0
    error: str | None = None
    """Why part of the check failed (``timeout``, an error code); the rest still counts."""


@dataclass
class Status:
    """Whether a classifier can be used in this deployment."""

    id: str
    available: bool
    reason: str | None = None
    """Why not (or the last failure of an available one)."""

    def view(self) -> dict[str, Any]:
        """For the console.

        Returns:
            id, available, reason.
        """
        return {"id": self.id, "available": self.available, "reason": self.reason}


class InjectionClassifier(Protocol):
    """A built-in or adapted classifier."""

    id: str
    timeout: float
    """Seconds a whole classification may take before the turn goes on without it."""
    billed: bool
    """Whether its calls are recorded as usage (a paid service)."""

    def status(self) -> Status:
        """Whether it can be used, and why not.

        Returns:
            The status.
        """
        ...

    async def classify(self, text: str, *, source: Source, budget: int) -> Result:
        """Score a text, checking at most ``budget`` characters of it.

        Raises:
            Exception: Any failure (the caller fails open).
        """
        ...

    async def warm_up(self) -> None:
        """Load what the first call would load (a model), so the first turn is not slow."""
        ...


def windows(text: str, size: int, overlap: int) -> list[str]:
    """Cut a text into pieces of at most ``size`` characters that overlap by ``overlap``.

    Cuts fall on whitespace when there is some in the last fifth of a piece, so a word (or an
    instruction) is rarely split; the overlap catches what a cut would still separate.

    Returns:
        The pieces in order (one when the text fits).
    """
    if size <= 0:
        return []
    if len(text) <= size:
        return [text] if text else []
    overlap = max(0, min(overlap, size // 2))
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(
                text.rfind(" ", start + size * 4 // 5, end),
                text.rfind("\n", start + size * 4 // 5, end),
            )
            if cut > start:
                end = cut + 1
        out.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


def budgeted(pieces: list[str], budget: int) -> tuple[list[str], bool]:
    """The pieces that fit a character budget (the last one cut to fit).

    Returns:
        The pieces kept and whether anything was left out.
    """
    kept: list[str] = []
    left = max(budget, 0)
    for p in pieces:
        if left <= 0:
            return kept, True
        if len(p) > left:
            kept.append(p[:left])
            return kept, True
        kept.append(p)
        left -= len(p)
    return kept, False


class Custom:
    """A third-party classifier (``EVOKAI_GUARD_CLASSIFIER=module:factory``), same interface."""

    id = "custom"
    billed = False

    def __init__(self, spec: str | None, timeout: float) -> None:
        """Load the factory, when one is configured.

        Args:
            spec: ``module:factory``, or None.
            timeout: Seconds a classification may take.
        """
        from evokai.guard import injection

        self.spec = spec
        self.timeout = timeout
        self.impl: injection.Classifier | None = None
        self._error: str | None = None
        if spec:
            try:
                self.impl = injection.load_classifier(spec)
            except Exception as e:
                self._error = f"EVOKAI_GUARD_CLASSIFIER could not be loaded: {type(e).__name__}"
                log.warning("guard_classifier_not_loaded", extra={"error": type(e).__name__})

    def status(self) -> Status:
        """Available when a factory is configured and loaded.

        Returns:
            The status.
        """
        if self.impl is not None:
            return Status(self.id, True)
        return Status(self.id, False, self._error or "EVOKAI_GUARD_CLASSIFIER is not set")

    async def classify(self, text: str, *, source: Source, budget: int) -> Result:
        """Ask the third-party classifier about the text (cut to the budget).

        Returns:
            Its score.

        Raises:
            RuntimeError: No classifier configured.
        """
        del source  # the protocol takes the text only
        if self.impl is None:
            raise RuntimeError(self.status().reason)
        sent = text[: max(budget, 0)]
        if not sent:
            return Result(capped=bool(text))
        score = float(await self.impl.score(sent))
        return Result(score=score, chars=len(sent), capped=len(sent) < len(text))

    async def warm_up(self) -> None:
        """Nothing to load."""
