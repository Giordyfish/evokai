"""Typed request and response models.

Attributes are snake_case; on the wire each model keeps the service's own names (camelCase for the
session and server routes, snake_case for the admin settings). Every model accepts fields it does
not know yet (``extra="allow"``), so a newer service never breaks an older client: unknown fields
are kept in ``model_extra``.

Request models mirror the service's OpenAPI schemas, and a test checks them against
``clients/typescript/openapi.json``. Most responses have no schema of their own; their models follow
the routes and the TypeScript client's types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

__all__ = [
    "AbuseSettings",
    "ActivityItem",
    "AgentDef",
    "AgentPutResult",
    "AgentSummary",
    "AgentVersion",
    "ApiKeyInfo",
    "AppPatch",
    "Application",
    "Attachment",
    "AttachmentOffer",
    "BugReport",
    "BuiltinTool",
    "BuiltinToolBlock",
    "CapConfig",
    "CapPeriod",
    "CapShare",
    "CapsConfig",
    "ClientToolsDef",
    "ConnectorKind",
    "Consent",
    "ConversationSummary",
    "EraseResult",
    "ExportedConfig",
    "FileBlock",
    "FileRef",
    "GrantChange",
    "GrantChanges",
    "GrantGroup",
    "GrantItem",
    "GrantView",
    "HistoryMessage",
    "InjectionSettings",
    "InputSettings",
    "KeyRequest",
    "LimitKind",
    "LinkSettings",
    "McpServerBody",
    "McpServerInfo",
    "McpTestResult",
    "McpTool",
    "Memory",
    "MemoryConsentResult",
    "MemoryFact",
    "MemoryKind",
    "MemoryList",
    "MemorySource",
    "MemoryState",
    "NavigateDef",
    "NewKey",
    "PageContext",
    "PendingConfirmation",
    "PiiSettings",
    "PromptBody",
    "PromptRef",
    "PromptVersion",
    "Residency",
    "RunAttachment",
    "RunMessage",
    "RunRequest",
    "RunResult",
    "RunUsage",
    "ScopeSettings",
    "SecurityEvent",
    "SecurityEventPage",
    "SecurityPolicy",
    "SecuritySummary",
    "SecurityView",
    "SessionInfo",
    "SessionToken",
    "SkillBody",
    "SkillDetail",
    "SkillFile",
    "SkillFileBody",
    "SkillFileInfo",
    "SkillInfo",
    "SkillScripts",
    "SubjectExport",
    "SubjectLimits",
    "SubjectRequest",
    "TextBlock",
    "TokenRequest",
    "ToolResult",
    "ToolResultBlock",
    "ToolSettings",
    "ToolUse",
    "ToolUseBlock",
    "UntrustedSettings",
    "UsageApplication",
    "UsageReport",
    "UsageRow",
    "UsageShareReport",
    "UsageShareRow",
    "UsageShares",
    "UsageSummary",
    "VoiceBody",
    "VoiceDef",
    "VoiceInfo",
    "to_usage_shares",
]

VoiceEngine = Literal["browser", "server"]
StorageMode = Literal["evokai", "external"]


class WireModel(BaseModel):
    """A model whose wire names are camelCase (``conversation_id`` is ``conversationId``)."""

    model_config = ConfigDict(
        extra="allow",
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        protected_namespaces=(),
    )


class SnakeModel(BaseModel):
    """A model whose wire names are its attribute names (the admin settings)."""

    model_config = ConfigDict(
        extra="allow",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        protected_namespaces=(),
    )


# Shared


LimitKind = Literal[
    "app_month", "app_week", "app_day", "subject_month", "subject_week", "subject_day"
]
"""Which cap blocked a turn, in the order the service checks them."""

CapPeriod = Literal["day", "week", "month"]
"""A cap's calendar window (00:00 in the application's time zone)."""


class CapShare(WireModel):
    """One cap as a browser sees it: its share and when it resets, never an amount."""

    limit: LimitKind
    scope: Literal["application", "subject"]
    period: CapPeriod
    used_pct: float
    left_pct: float
    resets_at: str
    """The next reset, an ISO time."""


class UsageShares(WireModel):
    """What a browser may know about spending: shares of the caps, never amounts.

    ``day_pct``, ``week_pct`` and ``month_pct`` are the subject's (None without such a cap),
    ``company_pct`` the application's month; ``month_out`` is true when a monthly cap is reached,
    ``out`` when any cap is (nothing more until ``resets_at``); ``binding`` is the cap that limits
    most and ``caps`` every cap.
    """

    day_pct: float | None = None
    week_pct: float | None = None
    month_pct: float | None = None
    company_pct: float | None = None
    month_out: bool = False
    out: bool = False
    binding: CapShare | None = None
    resets_at: str | None = None
    caps: list[CapShare] = Field(default_factory=list)


MemoryKind = Literal["preference", "semantic", "episodic"]
"""How the person likes answers, a fact about their work, or a finished conversation."""

MemorySource = Literal["agent", "summary", "person"]
"""Who wrote a memory: the agent, the summary of a finished conversation, or the person."""


class MemoryState(WireModel):
    """Whether agents may remember things about the person: the application's mode and their answer.

    ``active`` is true when memory is on for the person now; ``ask`` when they should be asked once
    (mode ``ask``, no answer yet).
    """

    mode: Literal["off", "ask", "on"]
    consent: Literal["granted", "declined", "unknown"] = "unknown"
    active: bool = False
    ask: bool = False


class Memory(WireModel):
    """One memory, as the person sees and edits it."""

    id: str
    text: str
    kind: MemoryKind = "semantic"
    agent: str | None = None
    source: MemorySource = "agent"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    uses: int = 0


class MemoryFact(WireModel):
    """Something an agent remembers about a subject (``fact`` is its text).

    The server list (``GET /v1/memories``) also carries the fields of :class:`Memory`.
    """

    id: str
    fact: str
    text: str | None = None
    kind: MemoryKind | None = None
    agent: str | None = None
    source: MemorySource | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    uses: int | None = None


class MemoryList(WireModel):
    """``GET /v1/session/memories``: the person's memory state and what is remembered."""

    memory: MemoryState
    items: list[Memory] = Field(default_factory=list)


class MemoryConsentResult(WireModel):
    """The answer to a consent: the new memory state and how many memories were deleted (a no)."""

    memory: MemoryState
    deleted: int = 0


class Connection(WireModel):
    """A person's connection to a provider (Microsoft 365, Google Workspace)."""

    provider: str
    title: str
    status: Literal["connected", "expired", "not_connected"]
    account: str | None = None
    scopes: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    connected_at: str | None = None
    last_used_at: str | None = None


class ConnectionStart(WireModel):
    """Start connecting a provider."""

    return_to: str | None = None


class ConnectionStarted(WireModel):
    """Where to send the person to connect a provider."""

    provider: str
    authorize_url: str
    expires_at: int


class PageContext(WireModel):
    """The page the user is looking at, sent with a message."""

    path: str | None = None
    title: str | None = None
    text: str | None = None


class ConfirmReason(WireModel):
    """Why one call of a confirmation waits for the person.

    ``key`` is an i18n key: ``confirm.why.writes`` (the tool changes data), ``confirm.why.policy``
    (the security policy forces it) or ``confirm.why.rule`` (an ask rule asked: ``rule`` is its
    id, ``level`` where it was set, ``label`` why, in the application's language).
    """

    key: str
    rule: str | None = None
    level: str | None = None
    label: str | None = None


class PendingConfirmation(WireModel):
    """A confirmation waiting for the user's decision (``why[i]`` says why ``items[i]`` waits)."""

    action_id: str
    items: list[str] = Field(default_factory=list)
    why: list[ConfirmReason] = Field(default_factory=list)


class TextBlock(WireModel):
    """Text the model sees."""

    text: str


class FileRef(WireModel):
    """An attachment as a message keeps it: never its bytes."""

    id: str
    name: str
    media_type: str
    size: int = 0
    sha256: str = ""


class FileBlock(WireModel):
    """An attachment of a question, with the text the model read of it (a document).

    Without text, the model read the file itself.
    """

    file: FileRef
    text: str | None = None


@dataclass(frozen=True)
class Attachment:
    """A file to send with a message (``chat(..., attachments=[...])``).

    A path or a ``(name, bytes)`` pair works too. The service reads the kind from the bytes.
    """

    name: str
    data: bytes
    media_type: str | None = None


class ToolUse(WireModel):
    """A tool call of the model."""

    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolUseBlock(WireModel):
    """A content block holding a tool call."""

    tool_use: ToolUse


class ToolResult(WireModel):
    """The result of a tool call."""

    id: str
    name: str
    ok: bool = True
    text: str = ""
    wrote: bool | None = None
    """A tool that changes data ran: the question of that turn can no longer be rewritten. Keep it
    as the service returns it."""


class ToolResultBlock(WireModel):
    """A content block holding a tool result."""

    tool_result: ToolResult


class BuiltinTool(WireModel):
    """A provider built-in tool that can change data ran during the answer."""

    id: str
    name: str
    changes: bool = True


class BuiltinToolBlock(WireModel):
    """A content block marking a provider built-in tool that can change data.

    The answer cannot be regenerated: keep it as the service returns it.
    """

    builtin_tool: BuiltinTool


ContentBlock = FileBlock | TextBlock | ToolUseBlock | ToolResultBlock | BuiltinToolBlock


class HistoryMessage(WireModel):
    """One message of a conversation kept by the application (external storage).

    ``content`` is what the model sees; ``text`` and ``events`` are what the user saw.
    """

    role: Literal["user", "assistant"]
    content: list[ContentBlock] = Field(default_factory=list)
    text: str = ""
    events: list[dict[str, Any]] = Field(default_factory=list)
    interrupted: bool = False


# Session tokens


class CapConfig(SnakeModel):
    """A cap in the application's currency: a period, its maximum and when it resets.

    ``resets``: a weekday for a week (``saturday``, Monday by default), a day of the month for a
    month (1 by default); a day resets at 00:00.
    """

    period: CapPeriod
    amount: float
    resets: str | int | None = None


class CapsConfig(SnakeModel):
    """The application's caps per calendar window: its own and each subject's."""

    application: list[CapConfig] = Field(default_factory=list)
    subject: list[CapConfig] = Field(default_factory=list)


class SubjectLimits(SnakeModel):
    """Per-subject caps the application decides for a session (amounts, server side only).

    The old fields are in USD; ``caps`` are in the application's currency. Each replaces the
    application's default subject cap of its period; one form per period.
    """

    subject_daily_usd: float | None = None
    subject_monthly_usd: float | None = None
    caps: list[CapConfig] | None = None


class Consent(WireModel):
    """The consent the end user gave: which notice, which version, when."""

    version: str
    accepted_at: datetime
    notice_hash: str | None = None


class ModuleChoice(WireModel):
    """A session's choice for one prompt module of the agent (only with the server key)."""

    on: bool | None = None
    """Switch the module on or off; omitted keeps the agent's default."""
    params: dict[str, Any] | None = None
    """Parameter values over the agent's defaults."""


class ClientToolDef(SnakeModel):
    """A custom client tool: your client application runs it (``client_tool`` events)."""

    name: str
    """``^[a-z][a-z0-9_]{1,47}$``; the model sees it as ``client_<name>``."""
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    """The JSON Schema of the arguments (at most 16 KB)."""
    wait: bool = False
    """True: the turn waits for the client's result (``tool_result``)."""


class TokenRequest(WireModel):
    """The body of ``POST /v1/session-tokens``."""

    subject: str
    agent: str
    conversation_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    ttl_seconds: int | None = None
    forward_credential: str | None = None
    limits: SubjectLimits | None = None
    model: str | None = None
    voice: str | None = None
    voice_engine: VoiceEngine | None = None
    storage_mode: StorageMode | None = None
    actor: Literal["agent"] = "agent"
    consent: Consent | None = None
    exit_code: str | None = None
    groups: list[str] | None = None
    memory_consent: bool | None = None
    """The person's answer to long-term memory, asked in the application's own interface."""
    modules: dict[str, ModuleChoice] | None = None
    """Prompt modules of the agent to switch and fill for this session, by name."""
    locale: str | None = None
    """The session's language (a BCP 47 tag with a shipped pack: ``en``, ``it``)."""
    client_tools: list[ClientToolDef] | None = None
    """Custom client tools of this session (granted by ``client:session``)."""


class SessionToken(WireModel):
    """A minted session token, for the browser."""

    token: str
    expires_at: int
    """Unix seconds."""
    conversation_id: str
    voice_engine: VoiceEngine = "browser"
    storage_mode: StorageMode = "evokai"
    usage: UsageShares = Field(default_factory=UsageShares)
    suggestions: list[str] = Field(default_factory=list)
    """The agent's starter questions, to show before the first message."""
    memory: MemoryState | None = None
    """Whether memory is on for the person, and whether to ask them."""


class AttachmentOffer(WireModel):
    """The files a session accepts: kinds read from the bytes, sizes in bytes."""

    kinds: list[str] = Field(default_factory=list)
    media_types: list[str] = Field(default_factory=list)
    max_file_bytes: int = 0
    max_files: int = 0


class SessionInfo(WireModel):
    """``GET /v1/session``: conversation, agent, usage shares and memories."""

    conversation_id: str
    agent: str
    expires_at: int
    usage: UsageShares = Field(default_factory=UsageShares)
    memories: list[MemoryFact] = Field(default_factory=list)
    memory: MemoryState | None = None
    """Whether memory is on for the person and whether to ask them (None: no application)."""
    suggestions: list[str] = Field(default_factory=list)
    attachments: AttachmentOffer | None = None
    """What the person may attach to a message (None: nothing)."""
    markup: dict[str, Any] | None = None
    """The delimiters of the answers' inline markup (``cue``, ``speech``), None without."""


# Server runs


class RunMessage(WireModel):
    """A message of a run's input conversation."""

    role: Literal["user", "assistant"]
    text: str


class RunAttachment(WireModel):
    """A text attachment of a run (a ticket body, an email, an extracted document)."""

    name: str
    media_type: str = "text/plain"
    text: str


class RunRequest(WireModel):
    """The body of ``POST /v1/agents/{name}/run``."""

    input: str | list[RunMessage]
    subject: str | None = None
    tags: list[str] = Field(default_factory=list)
    attachments: list[RunAttachment] = Field(default_factory=list)
    store: bool = False
    groups: list[str] = Field(default_factory=list)
    client_tools: list[ClientToolDef] = Field(default_factory=list)
    """Custom client tools of this run; none may wait (their calls come back in the result)."""


class RunUsage(WireModel):
    """Tokens and cost of a run (server to server only)."""

    input_tokens: int
    """Every input token, cached ones included."""
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float
    tool_calls: int


class RunResult(WireModel):
    """A run's answer (``RunResponse`` in the OpenAPI schema)."""

    run_id: str
    output: Any
    """The schema-valid JSON when the agent has an ``output_schema``, else the answer text."""
    model: str
    usage: RunUsage
    conversation_id: str | None = None
    client_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    """Calls of custom client tools the run made (``callId``, ``name``, ``args``), to carry out."""


# Conversations, usage, catalogue, privacy


class ConversationSummary(WireModel):
    """One of a subject's conversations."""

    id: str
    title: str = ""
    agent: str = ""
    updated_at: str | None = None


class UsageRow(WireModel):
    """A usage group with its amount (server key only)."""

    subject: str | None = None
    agent: str | None = None
    kind: str | None = None
    model: str | None = None
    day: str | None = None
    tags: list[str] | None = None
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    characters: int = 0
    audio_ms: int = 0
    text_units: int = 0
    calls: int = 0


class UsageApplication(WireModel):
    """The application's caps and spend in a usage report.

    ``currency`` and ``exchange_rate`` (units per 1 USD) are what amounts in the application's
    currency were converted with; ``caps`` lists every cap with its window (``window_start``,
    ``resets_at``), its maximum (``amount``, ``cap_usd``) and, for the application's own,
    ``spent_usd`` and ``spent``.
    """

    currency: str = "USD"
    exchange_rate: float | None = None
    monthly_cap_usd: float | None = None
    daily_cap_usd: float | None = None
    month_usd: float = 0.0
    day_usd: float = 0.0
    caps: list[dict[str, Any]] = Field(default_factory=list)


class UsageReport(WireModel):
    """``GET /v1/usage`` with a server key: rows with amounts, and the application's spend."""

    rows: list[UsageRow] = Field(default_factory=list)
    application: UsageApplication = Field(default_factory=UsageApplication)


class UsageShareRow(WireModel):
    """A usage row without its amount: tokens, calls and the share of the monthly cap."""

    subject: str | None = None
    agent: str | None = None
    kind: str | None = None
    model: str | None = None
    day: str | None = None
    tags: list[str] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    characters: int = 0
    audio_ms: int = 0
    text_units: int = 0
    calls: int = 0
    cap_pct: float | None = None
    """This group's spend as a share of the application's monthly cap (None without a cap)."""


class UsageShareReport(WireModel):
    """A usage report with shares only, safe to hand to an interface."""

    rows: list[UsageShareRow] = Field(default_factory=list)
    month_pct: float | None = None
    day_pct: float | None = None


def _share(spent: float, cap: float | None) -> float | None:
    if cap is None:
        return None
    if cap <= 0:
        return 100.0
    return round(spent / cap * 100, 1)


def to_usage_shares(report: UsageReport) -> UsageShareReport:
    """Turn a usage report into percentages and drop every amount.

    For interfaces that must not show money: convert where the data is fetched, so amounts never
    reach a browser.

    Args:
        report: A report from ``usage()``.

    Returns:
        The same rows with ``cap_pct`` instead of ``cost_usd``, and the month and day shares.
    """
    cap = report.application.monthly_cap_usd
    rows = [
        UsageShareRow.model_validate({
            **r.model_dump(exclude={"cost_usd"}),
            "capPct": _share(r.cost_usd, cap),
        })
        for r in report.rows
    ]
    return UsageShareReport(
        rows=rows,
        month_pct=_share(report.application.month_usd, cap),
        day_pct=_share(report.application.day_usd, report.application.daily_cap_usd),
    )


class AgentSummary(WireModel):
    """An active agent of the application."""

    name: str
    version: str
    title: str = ""


class VoiceInfo(WireModel):
    """A text-to-speech voice of the deployment."""

    provider: str
    voice_id: str
    language: str
    name: str
    gender: str | None = None
    region: str | None = None


class SubjectRequest(WireModel):
    """A data protection request about one subject (the subject travels in the body)."""

    subject: str


class SubjectExport(WireModel):
    """Everything Evokai keeps about a subject (amounts only with a server key)."""

    subject: str
    exported_at: str | None = None
    conversations: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[MemoryFact] = Field(default_factory=list)
    usage: list[dict[str, Any]] = Field(default_factory=list)
    security_events: list[dict[str, Any]] = Field(default_factory=list)
    bug_reports: list[dict[str, Any]] = Field(default_factory=list)


class EraseResult(WireModel):
    """What an erase deleted or de-identified."""

    ok: bool = True
    conversations: int = 0
    memories: int = 0
    bug_reports: int = 0
    usage_deidentified: int = 0
    """Usage rows kept for the caps, without the subject."""
    security_events_deidentified: int = 0


# Admin: application and keys


class Residency(SnakeModel):
    """Data residency: where data lives and which inference geographies or models are allowed."""

    region: str | None = None
    model_regions: list[str] | None = None
    models: list[str] | None = None


class ResidencyRule(SnakeModel):
    """Where data may be processed: geographies (``eu``...) or ISO countries (either matches)."""

    geographies: list[str] | None = None
    countries: list[str] | None = None


class BrowserDeclaration(SnakeModel):
    """What the application lets the browser send to its vendor's online services (reported)."""

    online_voices: bool | None = None
    online_recognition: bool | None = None
    vendors: list[str] | None = None


class DataProtection(SnakeModel):
    """The data protection profile: every rule set must hold for every external processor."""

    residency: ResidencyRule | None = None
    jurisdiction: list[str] | None = None
    retention: Literal["any", "no_training", "zero_retention"] | None = None
    browser: BrowserDeclaration | None = None


class AppPatch(SnakeModel):
    """Changeable application settings; only the fields you set are sent (None clears a default)."""

    name: str | None = None
    monthly_cap_usd: float | None = None
    daily_cap_usd: float | None = None
    currency: str | None = None
    """The currency of ``caps`` (an ISO code); spend stays recorded in USD."""
    exchange_rate: float | None = None
    """Units of ``currency`` per 1 USD (required when it is not USD)."""
    caps: CapsConfig | None = None
    """Caps per calendar window, in ``currency``."""
    allowed_origins: list[str] | None = None
    allowed_models: list[str] | None = None
    storage_mode: StorageMode | None = None
    conversation_ttl_days: int | None = None
    residency: Residency | None = None
    data_protection: DataProtection | None = None
    audit_ttl_days: int | None = None
    security_event_ttl_days: int | None = None
    voice_engine: VoiceEngine | None = None
    subject_monthly_usd: float | None = None
    subject_daily_usd: float | None = None
    locale: str | None = None
    timezone: str | None = None
    messages: dict[str, str] | None = None
    eval_monthly_usd: float | None = None
    eval_judge_model: str | None = None
    eval_on_save: bool | None = None
    eval_credential: str | None = None
    tracing_content: Literal["none", "masked", "full"] | None = None


class Application(SnakeModel):
    """The application's settings (``GET /v1/admin/application``)."""

    id: str
    slug: str
    name: str
    monthly_cap_usd: float | None = None
    daily_cap_usd: float | None = None
    currency: str = "USD"
    exchange_rate: float | None = None
    caps: CapsConfig | None = None
    effective_caps: list[dict[str, Any]] = Field(default_factory=list, alias="effectiveCaps")
    """Every cap in effect: kind, scope, period, amount, currency, resets, source."""
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_models: list[str] = Field(default_factory=list)
    storage_mode: StorageMode = "evokai"
    conversation_ttl_days: int | None = None
    security_event_ttl_days: int | None = None
    audit_ttl_days: int | None = None
    residency: Residency | None = None
    data_protection: dict[str, Any] | None = None
    voice_engine: VoiceEngine = "browser"
    subject_monthly_usd: float | None = None
    subject_daily_usd: float | None = None
    locale: str = "en"
    timezone: str = "UTC"
    messages: dict[str, str] = Field(default_factory=dict)
    eval_monthly_usd: float | None = None
    eval_judge_model: str | None = None
    eval_on_save: bool = True
    has_eval_credential: bool = Field(default=False, alias="hasEvalCredential")
    tracing_content: str = "masked"
    message_defaults: dict[str, str] = Field(default_factory=dict, alias="messageDefaults")


class KeyRequest(SnakeModel):
    """A new key: server (``srv``) or admin (``adm``)."""

    kind: Literal["srv", "adm"] = "srv"
    label: str = ""


class ApiKeyInfo(WireModel):
    """A key of the application (never its secret)."""

    id: str
    kind: Literal["srv", "adm"]
    prefix: str
    label: str = ""
    created_at: str | None = None
    last_used_at: str | None = None
    revoked: bool = False
    current: bool = False
    """The key making this request (it cannot revoke itself)."""


class NewKey(WireModel):
    """A created or rotated key: ``key`` is shown only now."""

    id: str
    key: str
    replaces: str | None = None


# Admin: agents


class PromptRef(SnakeModel):
    """A versioned prompt an agent uses instead of inline instructions."""

    name: str
    version: int | None = None


class VoiceDef(SnakeModel):
    """An agent's voice settings."""

    voice_id: str = ""
    prompt_rule: str | None = None
    first_clause_min_chars: int | None = None
    fillers: dict[str, Any] | None = None


class NavigateDef(SnakeModel):
    """The navigate client tool."""

    enabled: bool = True
    description: str | None = None
    path_pattern: str | None = None


class ClientToolsDef(SnakeModel):
    """Which client tools an agent may use."""

    chart: bool = True
    diagram: bool = True
    ask: bool = True
    memory: bool = True
    navigate: NavigateDef | None = None
    custom: list[ClientToolDef] = Field(default_factory=list)
    """Client tools of the application's own."""


class CodeDef(SnakeModel):
    """Isolated code execution for an agent: the ``code_run`` tool (needs the ``code`` grant)."""

    enabled: bool = False


class AskRule(SnakeModel):
    """An ask rule: calls of ``action`` that wait for the person's confirmation.

    With ``argument``, only calls whose value (or URL host, with ``part="host"``) matches
    ``resource``. Patterns are whole values with ``*`` and ``?``; in each list (deployment,
    application, agent) the last rule that matches wins, and a call asks when any list says so.
    ``effect`` is ``ask`` or ``default`` (cancels an ``ask`` above it in the same list) only.
    """

    action: str
    argument: str | None = None
    part: Literal["value", "host"] = "value"
    resource: str = "*"
    effect: Literal["ask", "default"] = "ask"
    id: str | None = None
    label: dict[str, str] = Field(default_factory=dict)
    """Why the person is asked, per language (``en``, ``it``)."""
    tags: list[str] = Field(default_factory=list)


class AskRulesView(SnakeModel):
    """The application's ask rules, the deployment's before them, each agent's after them."""

    rules: list[AskRule] = Field(default_factory=list)
    deployment: list[dict[str, Any]] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    """The MCP tools the rules can name: name, server, mutating, description."""


class AskRulesBody(SnakeModel):
    """The application's rules, in order (they replace the current ones)."""

    rules: list[AskRule] = Field(default_factory=list)


class AgentDef(SnakeModel):
    """A versioned agent definition (``PUT /v1/admin/agents/{name}``).

    Fields left unset are not sent, so the service's defaults apply.
    """

    name: str
    model: str
    title: str = ""
    version: str = ""
    instructions: str = ""
    prompt: PromptRef | None = None
    models: list[str] = Field(default_factory=list)
    fallbacks: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    skills: list[str] | None = None
    preload_skills: list[str] = Field(default_factory=list)
    client_tools: ClientToolsDef | None = None
    channels: list[Literal["browser", "server"]] = Field(default_factory=list)
    max_tool_calls_per_turn: int = 6
    temperature: float | None = None
    model_settings: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    prompt_cache: Literal["off", "5m", "1h"] = "off"
    confirm_labels: dict[str, str | list[str]] = Field(default_factory=dict)
    """Tool name to label template(s) of its confirmation card, in Handlebars."""
    confirm_values: dict[str, dict[str, str]] = Field(default_factory=dict)
    template_syntax: Literal["legacy", "handlebars"] = "handlebars"
    """The syntax of the instructions and labels: what is saved is ``handlebars``; ``legacy``
    marks a version saved before Handlebars."""
    suggestions: list[str] = Field(default_factory=list)
    voice: VoiceDef | None = None
    security: dict[str, Any] | None = None
    code: CodeDef | None = None
    ask_rules: list[AskRule] = Field(default_factory=list)
    """Calls that wait for the person's confirmation (read after the deployment's and the
    application's; they only add confirmations)."""
    show_reasoning: bool = False
    """Stream a summary of the model's reasoning to the browser (``reasoning`` events)."""
    show_tool_args: bool = False
    """Send the tools' arguments to the browser as the model writes them (``tool`` events with
    ``status: "args"``)."""


class AgentPutResult(WireModel):
    """The version an agent definition got, and whether it is new."""

    version: str
    created: bool
    eval_run_id: str | None = None
    """The eval run started for the new version, if the application runs evals on save."""


class AgentVersion(WireModel):
    """One version of an agent."""

    version: str
    active: bool = False
    created_at: str | None = None
    created_by: str | None = None
    definition: AgentDef | dict[str, Any]
    """The definition as saved (a plain dict when it does not parse as an ``AgentDef``)."""


# Admin: MCP servers, skills, prompts, voices


class McpServerBody(SnakeModel):
    """An MCP server: a URL, or a ready connector served by Evokai (``connector``, ``config``)."""

    url: str | None = None
    auth: Literal["forward_user", "secret", "none"] = "forward_user"
    secret: str | None = None
    timeout_seconds: float = 60
    tool_prefix: str = ""
    connector: str | None = None
    config: dict[str, Any] | None = None


class McpTool(WireModel):
    """A tool an MCP server listed."""

    name: str
    description: str = ""
    read_only: bool = False
    destructive: bool = False


class McpServerInfo(WireModel):
    """A registered MCP server (secrets never leave the service)."""

    name: str
    url: str | None = None
    auth: str = "forward_user"
    has_secret: bool = False
    timeout_seconds: float = Field(default=60, alias="timeout_seconds")
    tool_prefix: str = Field(default="", alias="tool_prefix")
    status: str | None = None
    latency_ms: float | None = None
    checked_at: str | None = None
    last_error: str | None = None
    tools: list[McpTool] | None = None
    connector: str | None = None
    config: dict[str, Any] | None = None


class McpTestResult(WireModel):
    """A connection test of an MCP server."""

    ok: bool
    latency_ms: float | None = None
    tools: list[McpTool] | None = None
    error: str | None = None


class ConnectorKind(WireModel):
    """A ready connector an MCP server can be made of."""

    kind: str
    title: str = ""
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")


class SkillFileBody(SnakeModel):
    """A file of a skill besides SKILL.md, like ``references/terms.md`` or ``scripts/x.py``."""

    path: str
    content: str


class SkillBody(SnakeModel):
    """A skill: its description, its body (the SKILL.md text without front matter) and files.

    ``files`` replaces all the stored files; left out (None), they are kept.
    """

    description: str
    body: str
    files: list[SkillFileBody] | None = None


class SkillInfo(WireModel):
    """A skill without its body."""

    name: str
    description: str = ""
    size: int = 0
    files: int = 0
    scripts: int = 0
    updated_at: str | None = None


class SkillFileInfo(WireModel):
    """A file of a skill: ``kind`` is ``script``, ``data`` or ``reference``."""

    path: str
    kind: str = "reference"
    size: int = 0
    title: str = ""


class SkillFile(SkillFileInfo):
    """A file of a skill with its text."""

    content: str = ""


class SkillScripts(WireModel):
    """Whether a skill's scripts can run; ``reason`` is ``deployment``, ``grant`` or ``agent``."""

    runnable: bool = False
    reason: str | None = None
    agents: list[str] = Field(default_factory=list)


class SkillDetail(WireModel):
    """A skill with its body, its files (without their texts) and whether its scripts can run."""

    name: str
    description: str = ""
    size: int = 0
    updated_at: str | None = None
    body: str = ""
    files: list[SkillFileInfo] = Field(default_factory=list)
    scripts: SkillScripts = Field(default_factory=SkillScripts)
    limits: dict[str, int] = Field(default_factory=dict)


class PromptBody(SnakeModel):
    """A prompt; a changed text, schema, description or data becomes the next version."""

    text: str
    parameters: dict[str, Any] | None = None
    """A prompt module's parameter schema (its text is then a Handlebars template)."""
    description: str | None = None
    data: dict[str, str] | None = None
    """A module's lookup table, read in its template with ``{{lookup data <parameter>}}``."""


class PreviewBody(SnakeModel):
    """Sample values for a prompt preview, and optionally a draft to render instead."""

    params: dict[str, Any] | None = None
    version: int | None = None
    text: str | None = None
    parameters: dict[str, Any] | None = None
    data: dict[str, str] | None = None


class PromptVersion(WireModel):
    """One version of a prompt."""

    name: str
    version: int
    text: str | None = None
    description: str | None = None
    parameters: dict[str, Any] | None = None
    """A module's parameter schema, or None for a plain prompt."""
    data: dict[str, str] | None = None
    template_syntax: Literal["legacy", "handlebars"] | None = None
    """``handlebars``, or ``legacy`` for a version saved before Handlebars (it keeps rendering as
    it did)."""
    created_at: str | None = None


class VoiceBody(SnakeModel):
    """A voice of a text-to-speech provider."""

    language: str
    name: str
    gender: str | None = None
    region: str | None = None


class BugReport(WireModel):
    """A problem a user reported from a session."""

    id: str
    subject: str = ""
    session_id: str | None = None
    description: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    created_at: str | None = None


# Admin: grants


class GrantChange(SnakeModel):
    """Grant or revoke one item, or reset it to its default (``value=None``)."""

    kind: str
    """model, mcp, tool, skill, client, voice, stt, tts, storage or channel."""
    name: str
    value: bool | str | None = None
    """True or False; for models ``any``, ``fallback`` or ``off``; None resets."""


class GrantChanges(SnakeModel):
    """Changes applied together."""

    changes: list[GrantChange]


class GrantItem(WireModel):
    """One grantable item with its default, override and effective value."""

    kind: str
    name: str
    value: bool | str | None = None
    default: bool | str | None = None
    override: bool | str | None = None
    children: list[GrantItem] = Field(default_factory=list)


class GrantGroup(WireModel):
    """A group of grantable items (models, mcp, skills, client, voice, storage, channels)."""

    id: str
    items: list[GrantItem] = Field(default_factory=list)


class GrantView(WireModel):
    """What the application may use."""

    groups: list[GrantGroup] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    overrides: int = 0
    model_allow: list[str] = Field(default_factory=list)
    application_models: list[str] = Field(default_factory=list)
    storage_mode: str | None = None


# Admin: security


class AbuseSettings(SnakeModel):
    """Abuse limits."""

    requests_per_minute: int | None = None
    runs_per_minute: int | None = None
    concurrent_turns: int | None = None
    max_turns_per_conversation: int | None = None


class InjectionSettings(SnakeModel):
    """Prompt-injection detection."""

    classifier: str | None = None
    threshold: int | None = None
    classifier_max_chars: int | None = None


class ModerationSettings(SnakeModel):
    """Harmful content in the person's message (control ``input.moderation``)."""

    classifier: str | None = None
    threshold: float | None = None


class InputSettings(SnakeModel):
    """Input size limits."""

    max_chars: int | None = None
    max_run_chars: int | None = None


class LinkSettings(SnakeModel):
    """Links the output may carry."""

    allow: list[str] | None = None


class PiiSettings(SnakeModel):
    """Personal data detection."""

    types: list[str] | None = None
    model_action: str | None = None


class ScopeSettings(SnakeModel):
    """Topics the agent keeps to."""

    topics: list[str] | None = None
    deny: list[str] | None = None
    refusal: str | None = None


class ToolSettings(SnakeModel):
    """Tool policy."""

    allow: list[str] | None = None
    deny: list[str] | None = None
    confirm: list[str] | None = None
    egress_allow: list[str] | None = None
    egress_tools: list[str] | None = None
    loop_threshold: int | None = None
    max_calls_per_turn: int | None = None
    max_tokens_per_turn: int | None = None
    rate_per_minute: dict[str, int] | None = None


class UntrustedSettings(SnakeModel):
    """How untrusted input is marked."""

    input: str | None = None


class SecurityPolicy(SnakeModel):
    """A security policy (every field optional: unset inherits the defaults)."""

    mode: Literal["off", "monitor", "enforce"] | None = None
    modes: dict[str, Literal["off", "monitor", "enforce"]] | None = None
    abuse: AbuseSettings | None = None
    injection: InjectionSettings | None = None
    moderation: ModerationSettings | None = None
    input: InputSettings | None = None
    links: LinkSettings | None = None
    pii: PiiSettings | None = None
    scope: ScopeSettings | None = None
    tools: ToolSettings | None = None
    untrusted: UntrustedSettings | None = None


class SecurityView(WireModel):
    """The application's policy, what applies, the controls and each agent's own policy."""

    enabled: bool = True
    policy: dict[str, Any] = Field(default_factory=dict)
    effective: dict[str, Any] = Field(default_factory=dict)
    controls: list[dict[str, Any]] = Field(default_factory=list)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    event_ttl_days: int | None = None
    default_event_ttl_days: int | None = None
    classifiers: list[dict[str, Any]] = Field(default_factory=list)
    default_classifier: str | None = None
    moderation_classifiers: list[dict[str, Any]] = Field(default_factory=list)


class SecurityEvent(WireModel):
    """A security event (snippets are always masked)."""

    id: str
    at: str
    control: str = ""
    rule: str = ""
    action: str = ""
    mode: str | None = None
    severity: str | None = None
    direction: str | None = None
    agent: str | None = None
    subject: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    tool: str | None = None
    snippet: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SecurityEventPage(WireModel):
    """A page of security events; pass ``next`` back as ``before`` for the following one."""

    items: list[SecurityEvent] = Field(default_factory=list)
    next: str | None = None


class SecuritySummary(WireModel):
    """Counts of a period by rule, control, action and severity."""

    total: int = 0
    by_rule: list[dict[str, Any]] = Field(default_factory=list)
    by_control: Any = None
    by_action: Any = None
    by_severity: Any = None


# Admin: console views


class ActivityItem(WireModel):
    """Something that changed lately: an agent version, a key, a grant."""

    at: str
    kind: str
    name: str
    detail: Any = None
    by: str | None = None


class UsageSummary(WireModel):
    """Turns, fallback answers, time to the first word, subjects and tags of a period."""

    turns: int = 0
    fallback_turns: int = 0
    median_first_token_ms: float | None = None
    subjects: int = 0
    tags: list[str] = Field(default_factory=list)
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class ExportedConfig(WireModel):
    """The application as a config file (secrets left out)."""

    yaml: str
    filename: str = ""


GrantItem.model_rebuild()


# Compliance packs


class AiActAnswers(SnakeModel):
    """Answers to the AI Act questionnaire of a compliance profile."""

    role: Literal["provider", "deployer", "provider_deployer"] = "provider_deployer"
    prohibited: list[
        Literal[
            "manipulation",
            "exploitation",
            "social_scoring",
            "crime_prediction",
            "facial_scraping",
            "emotion_workplace",
            "biometric_categorisation_sensitive",
            "realtime_biometric_identification",
        ]
    ] = Field(default_factory=list)
    annex_iii: list[
        Literal[
            "biometrics",
            "critical_infrastructure",
            "education",
            "employment",
            "essential_services",
            "law_enforcement",
            "migration",
            "justice_democracy",
        ]
    ] = Field(default_factory=list)
    safety_component: bool = False
    profiling: bool = False
    exceptions: list[
        Literal["narrow_procedural", "improve_human_result", "detect_patterns", "preparatory"]
    ] = Field(default_factory=list)
    emotion_recognition: bool = False
    biometric_categorisation: bool = False
    deep_fake: bool = False
    public_interest_text: bool = False
    disclosure_shown: bool | None = None


class GdprAnswers(SnakeModel):
    """GDPR statements of a compliance profile (overrides of what the configuration suggests)."""

    purposes: list[str] = Field(default_factory=list)
    data_subjects: list[str] = Field(default_factory=list)
    data_categories: list[str] = Field(default_factory=list)
    special_categories: bool = False
    legal_basis: Literal[
        "",
        "consent",
        "contract",
        "legal_obligation",
        "vital_interests",
        "public_task",
        "legitimate",
    ] = ""
    legal_basis_note: str = ""
    recipients: list[str] = Field(default_factory=list)
    transfers_note: str = ""
    large_scale: bool = False
    vulnerable_subjects: bool = False
    systematic_monitoring: bool = False
    automated_decisions: bool = False
    dpo_appointed: bool | None = None


class ComplianceProfile(SnakeModel):
    """What only the controller can state about an application (no personal data)."""

    system_name: str = ""
    system_description: str = ""
    intended_purpose: str = ""
    controller: str = ""
    processor: str = ""
    ai_act: AiActAnswers = Field(default_factory=AiActAnswers)
    gdpr: GdprAnswers = Field(default_factory=GdprAnswers)
    notes: str = ""


class SnapshotBody(SnakeModel):
    """A compliance snapshot to take now."""

    label: str = ""


class ComplianceSnapshot(WireModel):
    """A stored compliance snapshot (without its facts)."""

    id: str
    created_at: str
    label: str = ""
    by_kind: str = ""
    classification: str = ""
    agents: int = 0


class CompliancePack(WireModel):
    """A generated pack as JSON: sections of blocks, every statement with its sources."""

    pack: str
    lang: str
    title: str
    subtitle: str = ""
    disclaimer: str
    meta: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    footnotes: list[dict[str, Any]] = Field(default_factory=list)
