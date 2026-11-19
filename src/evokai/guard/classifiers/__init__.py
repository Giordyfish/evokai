"""Prompt-injection classifiers the policy can choose (``injection.classifier``).

The deterministic rules of ``guard.injection`` are bound to the languages and phrasings they know.
A classifier model adds a language-independent opinion. Three are built in:

- ``bedrock_guardrails``: the prompt attack filter of Amazon Bedrock Guardrails
  (``ApplyGuardrail``), billed per text unit of 1,000 characters (see :mod:`.bedrock`);
- ``prompt_guard``: Meta Llama Prompt Guard 2 (86M, multilingual) run on the service's own CPU, an
  optional extra (see :mod:`.prompt_guard`);
- ``custom``: a third-party factory loaded from ``EVOKAI_GUARD_CLASSIFIER=module:factory`` (the
  :class:`evokai.guard.injection.Classifier` protocol: ``async score(text) -> float``).

and over HTTP (:mod:`.http`): Azure AI Content Safety Prompt Shields (``azure_prompt_shields``),
Google Model Armor (``model_armor``) and the ``jailbreaking`` category of Mistral's moderation model
(``mistral_moderation``). Moderation classifiers (``moderation.classifier``, control
``input.moderation``) judge harmful content instead: Azure Content Safety, OpenAI and Mistral
moderation, Model Armor's responsible AI filters, a self-hosted Llama Guard. Each has an entry in
the provider catalog, and the application's data protection profile decides whether a text may
reach it.

``none`` turns the classifier off; ``default`` (and the old ``true``) means the deployment's own
choice. A classifier's score of 0.8 or more counts as one strong rule (3 points) in the verdict.

Every classifier fails open: a timeout, an error or a missing model never blocks a turn, it records
a ``classifier_unavailable`` security event instead.
"""

from evokai.guard.classifiers.base import (
    CHOICES,
    CLASSIFIER_IDS,
    MODERATION_IDS,
    PROCESSORS,
    STRONG,
    Custom,
    InjectionClassifier,
    Result,
    Source,
    Status,
    budgeted,
    windows,
)
from evokai.guard.classifiers.registry import Classifiers

__all__ = [
    "CHOICES",
    "CLASSIFIER_IDS",
    "MODERATION_IDS",
    "PROCESSORS",
    "STRONG",
    "Classifiers",
    "Custom",
    "InjectionClassifier",
    "Result",
    "Source",
    "Status",
    "budgeted",
    "windows",
]
