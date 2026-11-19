"""What a person states about an application for its compliance packs, stored per application.

The configuration says what the system does; only the controller can say what it is for, who
uses it and what it decides. This profile holds those statements: the system's description and
intended purpose, the answers to the AI Act risk questionnaire, and the GDPR overrides (purposes,
categories, legal basis). It is checked on save: free text that carries a personal identifier or
a secret is refused, so a pack never contains one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evokai.guard import pii, secrets

Role = Literal["provider", "deployer", "provider_deployer"]
ANNEX_III = (
    "biometrics",
    "critical_infrastructure",
    "education",
    "employment",
    "essential_services",
    "law_enforcement",
    "migration",
    "justice_democracy",
)
"""The eight areas of Annex III of Regulation (EU) 2024/1689."""
AnnexArea = Literal[
    "biometrics",
    "critical_infrastructure",
    "education",
    "employment",
    "essential_services",
    "law_enforcement",
    "migration",
    "justice_democracy",
]
PROHIBITED = (
    "manipulation",
    "exploitation",
    "social_scoring",
    "crime_prediction",
    "facial_scraping",
    "emotion_workplace",
    "biometric_categorisation_sensitive",
    "realtime_biometric_identification",
)
"""The practices of Article 5(1)(a) to (h)."""
Prohibited = Literal[
    "manipulation",
    "exploitation",
    "social_scoring",
    "crime_prediction",
    "facial_scraping",
    "emotion_workplace",
    "biometric_categorisation_sensitive",
    "realtime_biometric_identification",
]
EXCEPTIONS = ("narrow_procedural", "improve_human_result", "detect_patterns", "preparatory")
"""The conditions of Article 6(3)(a) to (d)."""
Exception6 = Literal["narrow_procedural", "improve_human_result", "detect_patterns", "preparatory"]
LegalBasis = Literal[
    "", "consent", "contract", "legal_obligation", "vital_interests", "public_task", "legitimate"
]

TEXT_MAX = 4_000
ITEM_MAX = 200


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AiActAnswers(_Model):
    """Answers to the AI Act questionnaire."""

    role: Role = "provider_deployer"
    """The organisation's role for this system (Article 3(3) provider, 3(4) deployer)."""
    prohibited: list[Prohibited] = Field(default_factory=list)
    """Article 5 practices the system engages in (any one makes it prohibited)."""
    annex_iii: list[AnnexArea] = Field(default_factory=list)
    """Annex III areas the intended purpose falls in."""
    safety_component: bool = False
    """A safety component of a product under the Annex I harmonisation legislation (Art. 6(1))."""
    profiling: bool = False
    """It profiles natural persons (Article 6(3), last subparagraph: always high-risk)."""
    exceptions: list[Exception6] = Field(default_factory=list)
    """Article 6(3) conditions that hold, for a system in an Annex III area."""
    emotion_recognition: bool = False
    biometric_categorisation: bool = False
    deep_fake: bool = False
    """It generates or manipulates image, audio or video that is a deep fake (Art. 50(4))."""
    public_interest_text: bool = False
    """It publishes text to inform the public on matters of public interest (Art. 50(4))."""
    disclosure_shown: bool | None = None
    """People are told they are talking to an AI system (the application's interface)."""


class GdprAnswers(_Model):
    """GDPR statements and overrides of what the configuration suggests."""

    purposes: list[str] = Field(default_factory=list, max_length=20)
    data_subjects: list[str] = Field(default_factory=list, max_length=20)
    """Replace the inferred categories of data subjects when set."""
    data_categories: list[str] = Field(default_factory=list, max_length=30)
    """Added to the inferred categories of personal data."""
    special_categories: bool = False
    """Article 9 data is processed (health, beliefs, biometrics...)."""
    legal_basis: LegalBasis = ""
    legal_basis_note: str = Field(default="", max_length=TEXT_MAX)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    """Recipients besides the sub-processors the deployment shows."""
    transfers_note: str = Field(default="", max_length=TEXT_MAX)
    large_scale: bool = False
    vulnerable_subjects: bool = False
    systematic_monitoring: bool = False
    automated_decisions: bool = False
    """Decisions with legal or similarly significant effects without human involvement (Art. 22)."""
    dpo_appointed: bool | None = None


class ComplianceProfile(_Model):
    """The statements of one application."""

    system_name: str = Field(default="", max_length=ITEM_MAX)
    system_description: str = Field(default="", max_length=TEXT_MAX)
    intended_purpose: str = Field(default="", max_length=TEXT_MAX)
    controller: str = Field(default="", max_length=ITEM_MAX)
    """The organisation that decides why the data is processed (a company, not a person)."""
    processor: str = Field(default="", max_length=ITEM_MAX)
    """The organisation that runs this Evokai deployment for the controller."""
    ai_act: AiActAnswers = Field(default_factory=AiActAnswers)
    gdpr: GdprAnswers = Field(default_factory=GdprAnswers)
    notes: str = Field(default="", max_length=TEXT_MAX)


def _texts(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [x for k, v in value.items() for x in _texts(v, f"{path}.{k}" if path else k)]
    if isinstance(value, list):
        return [x for i, v in enumerate(value) for x in _texts(v, f"{path}[{i}]")]
    return []


def problems(profile: ComplianceProfile) -> list[str]:
    """Fields whose text carries a personal identifier or a secret.

    Returns:
        The field paths (empty when the profile is clean).
    """
    out = []
    for path, text in _texts(profile.model_dump()):
        if text and (pii.find(text) or secrets.find(text)):
            out.append(path)
    return out


def load(raw: dict[str, Any] | None) -> ComplianceProfile:
    """A stored profile (an unreadable one counts as empty).

    Returns:
        The profile.
    """
    try:
        return ComplianceProfile.model_validate(raw or {})
    except ValueError:
        return ComplianceProfile()
