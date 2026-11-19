"""The security policy: controls, their modes and their settings, per application and per agent.

Every control is ``off``, ``monitor`` (detect and record a security event, change nothing) or
``enforce`` (block, redact or mark, and record). The mode of a control is the first one set, from
the most specific scope to the least: the agent's mode for that control, the agent's ``mode``, the
application's mode for that control, the application's ``mode``, then the defaults below.

The defaults keep existing agents working: everything in monitor, except what cannot break an
answer and closes a real hole: secrets never leave in an answer, the tool policy (allow, deny,
forced confirmation) applies, egress allow-lists apply when one is set, and tool output, documents
and e-mails always reach the model marked as data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["off", "monitor", "enforce"]
ClassifierChoice = Literal[
    "none",
    "default",
    "bedrock_guardrails",
    "prompt_guard",
    "azure_prompt_shields",
    "model_armor",
    "mistral_moderation",
    "custom",
]
"""What ``injection.classifier`` may say (see ``evokai.guard.classifiers``)."""
MODES: tuple[Mode, ...] = ("off", "monitor", "enforce")


@dataclass(frozen=True)
class Control:
    """A control of the security layer."""

    id: str
    label: str
    owasp: tuple[str, ...]
    """OWASP Top 10 for LLM Applications 2025 ids (LLM01..LLM10)."""
    default: Mode
    enforce: str
    """What enforce does."""


CONTROLS: tuple[Control, ...] = (
    Control(
        "input.injection",
        "Prompt injection and jailbreaks in the user's input",
        ("LLM01",),
        "monitor",
        "The turn is refused before the model sees it.",
    ),
    Control(
        "input.moderation",
        "Harmful content in the user's input (a moderation classifier)",
        ("LLM01",),
        "off",
        "The turn is refused before the model sees it.",
    ),
    Control(
        "input.encoding",
        "Hidden text: zero-width characters, homoglyphs, encoded blobs",
        ("LLM01",),
        "monitor",
        "Invisible characters are removed from the input.",
    ),
    Control(
        "input.size", "Input size", ("LLM10",), "monitor", "Inputs over the limit are refused."
    ),
    Control(
        "untrusted.spotlight",
        "Tool output, documents and e-mails marked as data",
        ("LLM01",),
        "enforce",
        "Untrusted content reaches the model between data markers it is told never to obey.",
    ),
    Control(
        "untrusted.injection",
        "Instructions hidden in tool output, documents and e-mails",
        ("LLM01",),
        "monitor",
        "Instruction-like lines are removed from the content before the model sees it.",
    ),
    Control(
        "pii.model",
        "Personal data sent to the model",
        ("LLM02",),
        "monitor",
        "Identifiers are tokenised (tools still get the real value) or masked.",
    ),
    Control(
        "pii.history",
        "Personal data stored in conversations",
        ("LLM02",),
        "monitor",
        "Identifiers are masked in what is stored.",
    ),
    Control(
        "pii.logs",
        "Personal data in logs",
        ("LLM02",),
        "enforce",
        "Identifiers are masked in every log line.",
    ),
    Control(
        "pii.browser",
        "Personal data in answers to the browser",
        ("LLM02",),
        "monitor",
        "Identifiers are masked in the answer.",
    ),
    Control(
        "output.secrets",
        "Secrets and credentials in answers",
        ("LLM02",),
        "enforce",
        "Secrets are replaced with [SECRET] before they leave.",
    ),
    Control(
        "output.system_prompt",
        "System prompt leakage",
        ("LLM07",),
        "monitor",
        "The answer stops and is replaced by a refusal.",
    ),
    Control(
        "output.links",
        "Links and images outside the allowed targets",
        ("LLM05", "LLM02"),
        "monitor",
        "Links to other targets keep their text and lose the address; such images are dropped.",
    ),
    Control(
        "scope",
        "Topics outside the agent's scope",
        ("LLM09", "LLM10"),
        "monitor",
        "Requests on a denied topic get the agent's refusal without a model call.",
    ),
    Control(
        "memory.write",
        "What agents remember about a user",
        ("LLM04",),
        "monitor",
        "Memories with instructions, secrets or identifiers are not stored.",
    ),
    Control(
        "tools.policy",
        "Tool allow and deny lists, forced confirmation",
        ("LLM06",),
        "enforce",
        "Denied tools are not offered and their calls are refused.",
    ),
    Control(
        "tools.args",
        "Tool arguments against the tool's schema",
        ("LLM05", "LLM06"),
        "monitor",
        "Invalid calls are returned to the model as errors, never run.",
    ),
    Control(
        "tools.rate",
        "Calls per tool per user per minute",
        ("LLM06", "LLM10"),
        "monitor",
        "Calls over the rate are refused.",
    ),
    Control(
        "tools.egress",
        "Addresses a tool may reach (when an allow-list is set)",
        ("LLM06", "LLM02"),
        "enforce",
        "Calls with an address outside the allow-list are refused.",
    ),
    Control(
        "tools.loop",
        "The same tool call repeated in a turn",
        ("LLM10", "LLM06"),
        "monitor",
        "The repeated call is refused.",
    ),
    Control(
        "turn.budget",
        "Tool calls and tokens per turn",
        ("LLM10",),
        "monitor",
        "The turn ends when a budget is spent.",
    ),
    Control(
        "abuse.rate",
        "Requests per user per minute",
        ("LLM10",),
        "monitor",
        "Requests over the rate get 429.",
    ),
    Control(
        "abuse.concurrency",
        "Simultaneous turns per user",
        ("LLM10",),
        "monitor",
        "Requests over the limit get 429.",
    ),
    Control(
        "abuse.length",
        "Turns per conversation",
        ("LLM10",),
        "monitor",
        "Longer conversations get 429; the user starts a new one.",
    ),
)
CONTROL_IDS = frozenset(c.id for c in CONTROLS)
DEFAULT_MODES: dict[str, Mode] = {c.id: c.default for c in CONTROLS}


class InjectionSettings(BaseModel):
    """Prompt-injection heuristics."""

    model_config = ConfigDict(extra="forbid")
    threshold: int | None = Field(default=None, ge=1, le=20)
    """Score that flags a text (one strong rule is 3)."""
    classifier: ClassifierChoice | None = None
    """Also ask a classifier model: ``none``, ``bedrock_guardrails`` (Amazon Bedrock Guardrails),
    ``prompt_guard`` (Llama Prompt Guard 2 on the service's CPU), ``custom``
    (``EVOKAI_GUARD_CLASSIFIER``) or ``default`` (the deployment's choice). ``true`` and ``false``
    are still read, as ``default`` and ``none``. A score of 0.8 or more counts as a strong rule."""
    classifier_max_chars: int | None = Field(default=None, ge=0, le=1_000_000)
    """Characters a turn or a run may send to the classifier, the person's message and untrusted
    content together (Bedrock bills per 1,000 characters); what is over is not classified."""

    @field_validator("classifier", mode="before")
    @classmethod
    def _old_boolean(cls, v: Any) -> Any:
        # The policy used to say classifier: true/false.
        if v is True:
            return "default"
        if v is False:
            return "none"
        return v


class ModerationSettings(BaseModel):
    """Harmful content in the person's message (control ``input.moderation``).

    Moderation is not prompt injection detection: it judges hate, violence, sexual content,
    self-harm and the like, while an injection is usually polite text.
    """

    model_config = ConfigDict(extra="forbid")
    classifier: (
        Literal[
            "none",
            "azure_content_safety",
            "openai_moderation",
            "mistral_moderation",
            "model_armor",
            "llama_guard",
        ]
        | None
    ) = None
    """The moderation classifier (``evokai.guard.classifiers.http``), or ``none``."""
    threshold: float | None = Field(default=None, ge=0.05, le=1)
    """The score that flags a message (default 0.8)."""


class InputSettings(BaseModel):
    """Input limits."""

    model_config = ConfigDict(extra="forbid")
    max_chars: int | None = Field(default=None, ge=1, le=1_000_000)
    """Longest user message (browser turns)."""
    max_run_chars: int | None = Field(default=None, ge=1, le=5_000_000)
    """Longest input of a server run, attachments included."""


class UntrustedSettings(BaseModel):
    """Untrusted content."""

    model_config = ConfigDict(extra="forbid")
    input: Literal["auto", "trusted", "untrusted"] | None = None
    """Whether the user's own input is data: ``auto`` treats server-run inputs (e-mails, tickets)
    as data and a person's chat messages as instructions."""


class PiiSettings(BaseModel):
    """Personal data."""

    model_config = ConfigDict(extra="forbid")
    types: list[str] | None = None
    """Which identifiers (default all): codice_fiscale, partita_iva, iban, email, phone,
    card."""
    model_action: Literal["tokenize", "mask"] | None = None
    """What ``pii.model: enforce`` does: reversible tokens (tools get the real value) or masks."""

    @field_validator("types")
    @classmethod
    def _types(cls, v: list[str] | None) -> list[str] | None:
        from evokai.guard.pii import PII_TYPES

        if v is not None and (bad := [t for t in v if t not in PII_TYPES]):
            raise ValueError(f"unknown personal data types: {bad}")
        return v


class LinkSettings(BaseModel):
    """Links in answers."""

    model_config = ConfigDict(extra="forbid")
    allow: list[str] | None = None
    """Domains links may point to (subdomains included), besides relative paths and the
    application's own origins."""


class ScopeSettings(BaseModel):
    """What the agent talks about."""

    model_config = ConfigDict(extra="forbid")
    topics: list[str] | None = None
    """Topics the agent covers (added to its prompt)."""
    deny: list[str] | None = None
    """Words or regular expressions of topics it must refuse."""
    refusal: str | None = Field(default=None, max_length=500)
    """What it answers on a denied topic."""


class ToolSettings(BaseModel):
    """The tool policy."""

    model_config = ConfigDict(extra="forbid")
    allow: list[str] | None = None
    """Tool names or globs that may be used (default all)."""
    deny: list[str] | None = None
    """Tool names or globs that may never be used (wins over allow)."""
    confirm: list[str] | None = None
    """Tools that always need the user's confirmation, whatever their annotations say."""
    rate_per_minute: dict[str, int] | None = None
    """Tool glob → calls per user per minute (``"*"`` for every tool)."""
    egress_allow: list[str] | None = None
    """Domains a tool argument that is an address may point to (none set: no check)."""
    egress_tools: list[str] | None = None
    """Which tools the egress check looks at (default every tool)."""
    max_calls_per_turn: int | None = Field(default=None, ge=1, le=100)
    max_tokens_per_turn: int | None = Field(default=None, ge=100)
    """Input plus output tokens of every model call in a turn or a run."""
    loop_threshold: int | None = Field(default=None, ge=2, le=20)
    """The same call (tool and arguments) this many times in a turn is a loop."""


class AbuseSettings(BaseModel):
    """Per-user limits."""

    model_config = ConfigDict(extra="forbid")
    requests_per_minute: int | None = Field(default=None, ge=1)
    """Browser turns per user per minute."""
    runs_per_minute: int | None = Field(default=None, ge=1)
    """Server runs per subject per minute."""
    concurrent_turns: int | None = Field(default=None, ge=1)
    max_turns_per_conversation: int | None = Field(default=None, ge=1)


class SecurityPolicy(BaseModel):
    """A security policy of an application or of an agent (every field optional: unset inherits)."""

    model_config = ConfigDict(extra="forbid")
    mode: Mode | None = None
    """The mode of every control not set in ``modes``."""
    modes: dict[str, Mode] | None = None
    """Control id → mode."""
    injection: InjectionSettings | None = None
    moderation: ModerationSettings | None = None
    input: InputSettings | None = None
    untrusted: UntrustedSettings | None = None
    pii: PiiSettings | None = None
    links: LinkSettings | None = None
    scope: ScopeSettings | None = None
    tools: ToolSettings | None = None
    abuse: AbuseSettings | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _bare_off(cls, v: Any) -> Any:
        # YAML reads a bare off as false (and on as true).
        return "off" if v is False else v

    @field_validator("modes", mode="before")
    @classmethod
    def _modes(cls, v: Any) -> Any:
        if isinstance(v, dict):
            v = {k: ("off" if x is False else x) for k, x in v.items()}
            if bad := [k for k in v if k not in CONTROL_IDS]:
                raise ValueError(f"unknown security controls: {bad}")
        return v

    def stored(self) -> dict[str, Any] | None:
        """The policy as stored (unset fields left out).

        Returns:
            A JSON-able dict, or None when nothing is set.
        """
        out = self.model_dump(mode="json", exclude_none=True)
        return out or None


DEFAULT_SETTINGS = SecurityPolicy(
    injection=InjectionSettings(threshold=3, classifier="none", classifier_max_chars=24_000),
    moderation=ModerationSettings(classifier="none", threshold=0.8),
    input=InputSettings(max_chars=8_000, max_run_chars=400_000),
    untrusted=UntrustedSettings(input="auto"),
    pii=PiiSettings(types=None, model_action="tokenize"),
    links=LinkSettings(allow=[]),
    scope=ScopeSettings(topics=[], deny=[], refusal=None),
    tools=ToolSettings(
        allow=None,
        deny=[],
        confirm=[],
        rate_per_minute={"*": 30},
        egress_allow=None,
        egress_tools=["*"],
        max_calls_per_turn=None,
        max_tokens_per_turn=None,
        loop_threshold=3,
    ),
    abuse=AbuseSettings(
        requests_per_minute=30,
        runs_per_minute=120,
        concurrent_turns=3,
        max_turns_per_conversation=200,
    ),
)
"""The settings every policy starts from."""


def parse(raw: dict[str, Any] | SecurityPolicy | None) -> SecurityPolicy:
    """A stored or written policy.

    Returns:
        The policy (empty when none).
    """
    if isinstance(raw, SecurityPolicy):
        return raw
    return SecurityPolicy.model_validate(raw or {})


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "rate_per_minute":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass(frozen=True)
class Effective:
    """The policy that applies to one agent of one application."""

    settings: SecurityPolicy
    modes: dict[str, Mode]

    def mode(self, control: str) -> Mode:
        """The mode of a control.

        Returns:
            off, monitor or enforce.
        """
        return self.modes.get(control, "off")

    def on(self, control: str) -> bool:
        """Whether a control does anything (monitor or enforce).

        Returns:
            True unless off.
        """
        return self.mode(control) != "off"

    def enforced(self, control: str) -> bool:
        """Whether a control blocks or changes things.

        Returns:
            True in enforce mode.
        """
        return self.mode(control) == "enforce"

    def view(self) -> dict[str, Any]:
        """The effective policy, for the console.

        Returns:
            modes and settings.
        """
        settings = self.settings.model_dump(mode="json", exclude={"mode", "modes"})
        return {"modes": dict(self.modes), "settings": settings}


def resolve(
    app: dict[str, Any] | SecurityPolicy | None,
    agent: dict[str, Any] | SecurityPolicy | None = None,
    *,
    disabled: bool = False,
) -> Effective:
    """The effective policy of an agent: defaults, then the application's, then the agent's.

    Args:
        app: The application's policy.
        agent: The agent's policy.
        disabled: The deployment's kill switch (``EVOKAI_GUARD_ENABLED=false``): every control off.

    Returns:
        The effective policy.
    """
    a, g = parse(app), parse(agent)
    merged = _merge(
        _merge(DEFAULT_SETTINGS.model_dump(exclude_none=True), a.model_dump(exclude_none=True)),
        g.model_dump(exclude_none=True),
    )
    merged.pop("mode", None)
    merged.pop("modes", None)
    settings = SecurityPolicy.model_validate(merged)
    modes: dict[str, Mode] = {}
    for c in CONTROLS:
        chain = (
            (g.modes or {}).get(c.id),
            g.mode,
            (a.modes or {}).get(c.id),
            a.mode,
            c.default,
        )
        modes[c.id] = "off" if disabled else next(m for m in chain if m is not None)
    return Effective(settings, modes)


def catalogue() -> list[dict[str, Any]]:
    """The controls, for the console and the docs.

    Returns:
        id, label, owasp, default, enforce of each control.
    """
    return [
        {
            "id": c.id,
            "label": c.label,
            "owasp": list(c.owasp),
            "default": c.default,
            "enforce": c.enforce,
        }
        for c in CONTROLS
    ]
