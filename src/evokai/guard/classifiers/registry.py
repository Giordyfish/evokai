"""The deployment's injection classifiers, built once per process."""

from __future__ import annotations

import logging
from typing import Any

from evokai.guard.classifiers.base import (
    CLASSIFIER_IDS,
    MODERATION_IDS,
    Custom,
    InjectionClassifier,
)

log = logging.getLogger("evokai.security")


class Classifiers:
    """The deployment's classifiers, built once per process."""

    def __init__(self, settings: Any) -> None:
        """Build every classifier (models load lazily or at warm-up).

        Args:
            settings: Deployment settings.
        """
        from evokai.guard.classifiers import http
        from evokai.guard.classifiers.bedrock import BedrockGuardrails
        from evokai.guard.classifiers.prompt_guard import PromptGuard

        self.settings = settings
        self.by_id: dict[str, InjectionClassifier] = {
            "bedrock_guardrails": BedrockGuardrails.from_settings(settings),
            "prompt_guard": PromptGuard.from_settings(settings),
            "custom": Custom(
                getattr(settings, "guard_classifier", None),
                getattr(settings, "guard_classifier_timeout_ms", 1500) / 1000,
            ),
        }
        for c in http.from_settings(settings):
            self.by_id[c.id] = c  # ty: ignore[invalid-assignment]
        self.moderation: dict[str, Any] = {
            c.id: c for c in http.from_settings(settings) if c.detects in ("moderation", "both")
        }
        for c in self.moderation.values():
            c.mode = "moderation"

    def default_id(self) -> str | None:
        """What ``default`` (or the old ``true``) means here.

        ``EVOKAI_GUARD_CLASSIFIER_DEFAULT`` when set; else the third-party classifier when one is
        configured (what ``true`` always meant), then Bedrock Guardrails, then Prompt Guard, the
        first that is available.

        Returns:
            A classifier id, or None when none is available.
        """
        chosen = getattr(self.settings, "guard_classifier_default", None)
        if chosen:
            return None if chosen == "none" else chosen
        for cid in ("custom", "bedrock_guardrails", "prompt_guard"):
            if self.by_id[cid].status().available:
                return cid
        return None

    def resolve(self, choice: str | None) -> str | None:
        """The classifier a policy's choice names.

        Returns:
            Its id, or None for none.
        """
        if choice in (None, "none"):
            return None
        if choice == "default":
            return self.default_id()
        return choice

    def get(self, cid: str) -> InjectionClassifier | None:
        """A classifier by id.

        Returns:
            The classifier, or None for an unknown id.
        """
        return self.by_id.get(cid)

    def register(self, classifier: InjectionClassifier, *, moderation: bool = False) -> None:
        """Replace a classifier (tests, embedders)."""
        if moderation:
            self.moderation[classifier.id] = classifier
        else:
            self.by_id[classifier.id] = classifier

    def view(self) -> list[dict[str, Any]]:
        """Which classifiers this deployment can use, for the console.

        Returns:
            id, available, reason of each, plus the meaning of ``default``.
        """
        return [self.by_id[cid].status().view() for cid in CLASSIFIER_IDS]

    def moderation_view(self) -> list[dict[str, Any]]:
        """Which moderation classifiers this deployment can use, for the console.

        Returns:
            id, available, reason of each.
        """
        return [self.moderation[cid].status().view() for cid in MODERATION_IDS]

    def get_moderation(self, cid: str) -> InjectionClassifier | None:
        """A moderation classifier by id.

        Returns:
            The classifier, or None for an unknown id.
        """
        return self.moderation.get(cid)

    async def warm_up(self) -> None:
        """Load the local model when one is configured (never raises)."""
        for c in self.by_id.values():
            try:
                await c.warm_up()
            except Exception as e:
                log.warning(
                    "guard_classifier_warm_up_failed",
                    extra={"classifier": c.id, "error": type(e).__name__},
                )

    def close(self) -> None:
        """Stop the classifiers' threads."""
        for c in self.by_id.values():
            close = getattr(c, "close", None)
            if callable(close):
                close()
