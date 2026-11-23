"""Service settings, from the environment (prefix ``EVOKAI_``) or a ``.env`` file."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from evokai.core.caps import CapSpec


class BootstrapCaps(BaseModel):
    """Spending caps of an application (never shown to browsers).

    The ``*Usd`` fields are the old form, in USD; ``application`` and ``subject`` are the new one,
    caps per calendar window in the application's currency (``evokai.core.caps``).
    """

    monthlyUsd: float | None = Field(default=None, ge=0)
    dailyUsd: float | None = Field(default=None, ge=0)
    subjectMonthlyUsd: float | None = Field(default=None, ge=0)
    subjectDailyUsd: float | None = Field(default=None, ge=0)
    application: list[CapSpec] | None = None
    subject: list[CapSpec] | None = None


class BootstrapApp(BaseModel):
    """An application provisioned from the environment (keys, origins), e.g. by infrastructure.

    ``id`` is the application's slug; it is created if the config file did not define it.
    """

    id: str = Field(min_length=1, max_length=80)
    name: str | None = None
    serverKey: str | None = None
    adminKey: str | None = None
    origins: list[str] | None = None
    storageMode: str | None = None
    conversationTtlDays: int | None = Field(default=None, ge=1)
    securityEventTtlDays: int | None = Field(default=None, ge=1)
    residency: dict[str, Any] | None = None
    """Data residency, as ``residency:`` in an application YAML."""
    dataProtection: dict[str, Any] | None = None
    """The data protection profile, as ``data_protection:`` in an application YAML."""
    security: dict[str, Any] | None = None
    """The security policy, as the ``security:`` section of an application YAML."""
    voiceEngine: str | None = None
    tracingContent: Literal["none", "masked", "full"] | None = None
    """What content traces keep (``tracing: {content: ...}`` in an application YAML)."""
    caps: BootstrapCaps | None = None
    currency: str | None = None
    """The currency of the caps (an ISO code, USD by default)."""
    exchangeRate: float | None = Field(default=None, gt=0)
    """Units of ``currency`` per 1 USD (required when the currency is not USD)."""
    locale: str | None = None
    timezone: str | None = None
    """IANA time zone for dates in prompts and for the caps' windows (default UTC)."""
    messages: dict[str, str] | None = None
    """Overrides: limit.app, limit.appWeek, limit.appDay, limit.subjectMonth, limit.subjectWeek,
    limit.subjectDay."""
    grants: dict[str, dict[str, Any]] | None = None
    """Capability grants, as the ``grants:`` section of an application YAML (replaces every
    override of the application when given)."""


class Settings(BaseSettings):
    """Deployment settings. Everything per client application lives in the database instead."""

    model_config = SettingsConfigDict(
        env_prefix="EVOKAI_",
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        validate_by_name=True,
    )

    # HTTP
    port: int = 8787
    base_path: str = "/agent"
    """Where the API is mounted (for example /agent, behind the application's proxy)."""

    # Security
    signing_key: str = ""
    """HMAC key for session tokens (JWT HS256). Required in production; random in dev."""
    encryption_key: str = ""
    """Fernet key for MCP secrets and forwarded end-user credentials. Required in production."""
    session_token_default_ttl: int = 300
    session_token_max_ttl: int = 900
    production: bool = False
    """Refuse to start without explicit signing and encryption keys."""

    # Models
    model_allow: list[str] = Field(default_factory=lambda: ["bedrock:eu.*", "test:*", "function:*"])
    """Glob patterns over ``provider:model``. Anything else is refused (e.g. Bedrock global.*)."""
    aws_region: str = "eu-north-1"
    providers_file: str | None = None
    """``EVOKAI_PROVIDERS_FILE``: a YAML file that adds to or overrides the built-in provider
    catalog (``evokai.llm.providers``): the deployment's own endpoints (an Azure resource, a Vertex
    location, a self-hosted server with the residency and jurisdiction its operator declares),
    facts its contracts change, its own prices."""
    price_eur_usd: float = Field(default=1.2, gt=0, le=10)
    """``EVOKAI_PRICE_EUR_USD``: dollars per euro for the prices the catalog states in euros (EU
    providers), so every cost is recorded in USD. A deployment's assumption, not a quote: set it
    from your own rate; the default errs high so a call is never priced below what it costs."""

    # Bootstrap at start (idempotent): applications, keys, MCP servers, skills and agents
    bootstrap_file: str | None = Field(
        default=None, validation_alias=AliasChoices("EVOKAI_CONFIG_FILE", "EVOKAI_BOOTSTRAP_FILE")
    )
    """The YAML file (``EVOKAI_CONFIG_FILE``) with the application and its agents."""
    bootstrap_server_key: str | None = None
    bootstrap_admin_key: str | None = None
    bootstrap_apps: list[BootstrapApp] = Field(default_factory=list)
    """JSON (``EVOKAI_BOOTSTRAP_APPS``): applications and their keys provisioned by infrastructure,
    e.g. ``[{"id": "bookshop", "serverKey": "evk_srv_…", "origins": ["https://…"]}]``."""

    # Database: any SQLAlchemy async URL (postgresql+asyncpg, mysql+aiomysql, sqlite+aiosqlite…)
    database_url: str = "sqlite+aiosqlite:///./evokai.db"
    database_schema: str | None = None
    """Schema for the tables where the database has schemas. None = the default schema."""
    database_ssl: bool = False
    """TLS without a ``sslmode`` in the URL (verified against the system CAs or the CA file)."""
    db_ca_file: str | None = Field(
        default=None, validation_alias=AliasChoices("EVOKAI_DB_CA_FILE", "EVOKAI_DATABASE_CA_FILE")
    )
    """CA bundle for ``sslmode=verify-full``/``verify-ca`` (e.g. the RDS global bundle)."""
    app_db_role: str = "evokai_app"
    """``evokai db-setup``: the runtime role it creates and grants."""
    app_db_password: str | None = None
    """``evokai db-setup``: the runtime role's password (``EVOKAI_APP_DB_PASSWORD``)."""

    # Turns
    history_messages: int = 30
    history_chars: int = 60_000
    page_text_chars: int = 6_000
    tool_result_chars: int = 12_000
    max_output_tokens: int = 1_200
    client_tool_timeout_seconds: float = Field(default=120.0, gt=0)
    """How long a turn waits for the client's result of a custom client tool with ``wait``; then
    the model reads that no answer came."""

    # Voice
    voice_enabled: bool = False
    """Server voice sessions over WebRTC (needs the ``voice`` extra). Off by default."""
    voice_stt: str = "fake"
    """Speech-to-text provider: a registered name or ``module:factory``."""
    voice_tts: str = "fake"
    """Text-to-speech provider: a registered name or ``module:factory``."""
    voice_tts_engines: list[str] = Field(default_factory=list)
    """More speech engines a session token may choose (``speech.tts``) besides ``voice_tts``."""
    voice_stt_engines: list[str] = Field(default_factory=list)
    """More recognition engines a session token may choose (``speech.stt``), besides
    ``voice_stt``."""
    speech_max_chars: int = 2_000
    """The longest text ``POST /v1/session/speech`` speaks."""
    transcribe_max_bytes: int = 10_000_000
    """The largest audio file ``POST /v1/session/transcribe`` takes."""
    transcribe_max_seconds: int = 120
    """The longest audio file ``POST /v1/session/transcribe`` takes."""
    transcribe_stream_max_seconds: int = 300
    transcribe_stream_idle_seconds: float = 30
    """A streaming recognition socket that receives nothing for this long is closed."""
    speech_timeout_seconds: float = 60
    """The longest one synthesis may take before it ends with an error."""
    """The longest streaming recognition (``WS /v1/session/transcribe/stream``)."""
    voice_smart_turn: bool = True
    """End of turn decided by Smart Turn v3 on top of Silero VAD (else VAD silence only)."""
    voice_ice_servers: list[str] = Field(default_factory=list)
    """STUN/TURN URLs for WebRTC. Empty = host candidates only (same network, or a public IP)."""
    filler_after_ms: int = 700

    # Admin console (the SPA in admin/, served at {base}/admin)
    admin_ui_enabled: bool = True
    admin_dist_dir: str | None = None
    """Built console files. None = ``admin/dist`` next to the source tree."""
    console_session_ttl: int = 1_800
    """Lifetime of a console login (seconds)."""
    operator_key: str | None = None
    """Deployment-wide key (``EVOKAI_OPERATOR_KEY``): creates and lists applications, and logs in
    to the console over every application. Application admin keys see only their application."""

    state_dir: str | None = None
    """``EVOKAI_STATE_DIR``: where generated keys live (``keys.json``), for installs without a
    secret store (Docker Compose). Keys given in the environment win over the file."""
    auto_migrate: bool = False
    """``EVOKAI_AUTO_MIGRATE``: apply the database migrations when ``serve`` starts."""
    public_url: str | None = None
    """``EVOKAI_PUBLIC_URL``: where people reach the API (base path included), for messages that
    point to the console. Default ``http://localhost:{port}{base_path}``."""

    internal_url: str | None = None
    """``EVOKAI_INTERNAL_URL``: where this service reaches itself (base path included), for the
    built-in assistant's admin MCP server. Default ``http://127.0.0.1:{port}{base_path}``."""
    assistant: Literal["on", "off"] = "on"
    """``EVOKAI_ASSISTANT``: the built-in console assistant (``evokai.assistant``), a locked system
    application created at start and inactive until an owner picks its model. ``off`` creates
    nothing (an existing one stays as it is)."""

    @property
    def internal_base(self) -> str:
        """Where this service reaches itself, base path included.

        Returns:
            ``EVOKAI_INTERNAL_URL``, else ``http://127.0.0.1:{port}{base_path}``.
        """
        url = self.internal_url or f"http://127.0.0.1:{self.port}{self.base_path}"
        return url.rstrip("/")

    build_commit: str = ""
    """The commit the image was built from (``EVOKAI_BUILD_COMMIT``, set by the image build)."""

    # Console sign-in (single sign-on) and the audit log
    sso: dict[str, Any] | None = None
    """``EVOKAI_SSO``: the console's OpenID Connect provider as JSON (issuer, client_id, roles...,
    see ``evokai.identity.sso.SsoConfig``). The console can override it."""
    sso_client_secret: str | None = None
    """``EVOKAI_SSO_CLIENT_SECRET``: the client secret of ``EVOKAI_SSO``."""
    audit_ttl_days: int | None = 400
    """Audit log entries are deleted after this many days unless the application sets its own
    (deployment-wide entries always follow this)."""

    # Security layer (evokai.guard)
    guard_enabled: bool = True
    """Kill switch of the security layer: false turns every control off, whatever the policies."""
    ask_rules: list[dict[str, Any]] = Field(default_factory=list)
    """``EVOKAI_ASK_RULES``: the deployment's default ask rules (a JSON list of
    ``{action, argument?, part?, resource?, effect, id?, label?, tags?}``, ``evokai.guard.ask``),
    read before every application's and agent's. They only add confirmations; checked at start."""
    guard_classifier: str | None = None
    """Optional third-party injection classifier, ``module:factory`` (the ``custom`` choice)."""
    guard_classifier_timeout_ms: int = Field(default=1_500, ge=50, le=30_000)
    """How long the ``custom`` classifier may take before a turn goes on without it."""
    guard_classifier_default: (
        Literal["bedrock_guardrails", "prompt_guard", "custom", "none"] | None
    ) = None
    """``EVOKAI_GUARD_CLASSIFIER_DEFAULT``: what a policy's ``classifier: default`` (or ``true``)
    means: ``bedrock_guardrails``, ``prompt_guard``, ``custom`` or ``none``. Unset: ``custom`` when
    configured, else the first available of Bedrock Guardrails and Prompt Guard."""
    guardrail_id: str | None = None
    """``EVOKAI_GUARDRAIL_ID``: the Bedrock guardrail (prompt attack filter only) for
    ``bedrock_guardrails``."""
    guardrail_version: str | None = None
    """``EVOKAI_GUARDRAIL_VERSION``: its published version (a number, not DRAFT)."""
    guardrail_region: str | None = None
    """``EVOKAI_GUARDRAIL_REGION``: where the guardrail lives (default ``EVOKAI_AWS_REGION``)."""
    guardrail_timeout_ms: int = Field(default=1_500, ge=100, le=30_000)
    """How long a Bedrock Guardrails classification may take before a turn goes on without it."""
    prompt_guard_model: str | None = None
    """``EVOKAI_PROMPT_GUARD_MODEL``: a folder with the ONNX export of Llama Prompt Guard 2 86M (or
    the official files, or the official repo id with transformers, torch and HF_TOKEN) for
    ``prompt_guard``."""
    prompt_guard_threads: int = Field(default=2, ge=1, le=64)
    """CPU threads of a Prompt Guard inference."""
    prompt_guard_timeout_ms: int = Field(default=3_000, ge=50, le=60_000)
    """How long a Prompt Guard classification may take before a turn goes on without it."""
    azure_content_safety_endpoint: str | None = None
    """``EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT``: an Azure AI Content Safety resource
    (``https://<name>.cognitiveservices.azure.com``) for ``azure_prompt_shields`` and
    ``azure_content_safety``; declare its region in ``EVOKAI_PROVIDERS_FILE`` for the data
    protection profile."""
    azure_content_safety_key: str | None = None
    """``EVOKAI_AZURE_CONTENT_SAFETY_KEY``: its key."""
    model_armor_template: str | None = None
    """``EVOKAI_MODEL_ARMOR_TEMPLATE``: ``projects/{p}/locations/{l}/templates/{t}`` of a Google
    Model Armor template (prompt injection and jailbreak filter, responsible AI filters) for
    ``model_armor``; the token comes from ``EVOKAI_MODEL_ARMOR_TOKEN`` or Application Default
    Credentials."""
    model_armor_endpoint: str | None = None
    """``EVOKAI_MODEL_ARMOR_ENDPOINT``: the API host (default the template location's regional host,
    ``https://modelarmor.{location}.rep.googleapis.com``)."""
    mistral_moderation_url: str | None = None
    """``EVOKAI_MISTRAL_MODERATION_URL``: Mistral's API for ``mistral_moderation`` (default
    ``https://api.mistral.ai``; ``https://api.eu.mistral.ai`` for its EU endpoint); the key is
    ``MISTRAL_API_KEY``."""
    llama_guard_url: str | None = None
    """``EVOKAI_LLAMA_GUARD_URL``: the OpenAI-compatible API of a self-hosted Llama Guard
    (``http://guard:8000/v1``) for ``llama_guard``; ``EVOKAI_LLAMA_GUARD_KEY`` if it needs one."""
    llama_guard_model: str | None = None
    """``EVOKAI_LLAMA_GUARD_MODEL``: its model name (e.g. ``meta-llama/Llama-Guard-4-12B``)."""
    security_event_ttl_days: int | None = 180
    """Security events are deleted after this many days unless the application sets its own."""

    # State shared by every instance (rate counters, concurrency, sign-in codes)
    redis_url: str | None = None
    """``EVOKAI_REDIS_URL``: Redis or Valkey (``redis://host:6379/0``, ``rediss://`` for TLS) for
    the state every instance must share. Unset: each instance keeps it in its own memory."""
    redis_prefix: str = "evokai:"
    """Put before every key, so several deployments can share one Redis."""

    # Knowledge bases (evokai.knowledge)
    embedding_allow: list[str] = Field(
        default_factory=lambda: ["bedrock:amazon.titan-embed-text-v2:0", "test:*"]
    )
    """Glob patterns over ``provider:model`` of the embedding models knowledge bases may use (the
    application's data residency applies on top)."""
    embedding_model: str = "bedrock:amazon.titan-embed-text-v2:0"
    """The embedding model of a new knowledge base that does not choose one."""
    knowledge_max_file_mb: int = Field(default=20, ge=1, le=200)
    """Largest document accepted (upload, address or connector), in megabytes."""
    knowledge_fetch_allow: list[str] = Field(default_factory=list)
    """Domains documents may be fetched from by address (``example.org`` also allows its
    subdomains). Empty: fetching by address is off."""
    knowledge_link_ttl: int = Field(default=600, ge=30, le=86_400)
    """Lifetime in seconds of the signed links to a document that citations carry."""
    knowledge_workers: int = Field(default=2, ge=1, le=16)
    """Documents ingested at the same time by one process."""

    # Long-term memory (evokai.memory)
    memory_embedding_model: str = "bedrock:amazon.titan-embed-text-v2:0"
    """Embeds memories for recall by meaning (within ``EVOKAI_EMBEDDING_ALLOW`` and the data
    residency; without it memories are recalled by their words)."""
    memory_embedding_dimensions: int = Field(default=512, ge=16, le=4096)
    memory_summary_model: str | None = None
    """``EVOKAI_MEMORY_SUMMARY_MODEL``: writes the episodes of agents that keep them (default: the
    agent's own model). Within the allow-lists and the residency like any model."""
    memory_episode_idle_minutes: int = Field(default=30, ge=1, le=10_080)
    """A conversation with no new message for this long is finished: its episode is written."""

    # Red team (evokai.evals.redteam)
    redteam_on_save: bool = True
    """``EVOKAI_REDTEAM_ON_SAVE``: run the attack suite on every saved agent version (an
    application can turn it off with ``redteam_on_save: false``; the gate always runs it)."""
    redteam_judge_model: str | None = "bedrock:eu.amazon.nova-2-lite-v1:0"
    """``EVOKAI_REDTEAM_JUDGE_MODEL``: the cheap model that grades the few attacks no rule can
    grade (an application's ``eval_judge_model`` comes first). Empty: those graders are skipped."""

    # Cost alerts (evokai.costs)
    alert_webhook_allow: list[str] = Field(default_factory=list)
    """``EVOKAI_ALERT_WEBHOOK_ALLOW``: domains a cost alert webhook may call (``example.org`` also
    allows its subdomains), https only. Empty: no webhook is called."""
    # Workflows (evokai.workflows)
    workflows_enabled: bool = True
    """Run workflow steps and schedules in this process (the routes stay on when off)."""
    workflow_workers: int = Field(default=2, ge=1, le=32)
    """Steps one process runs at the same time."""
    workflow_poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    """How often an idle worker looks for due steps (new steps in this process wake it at once)."""
    workflow_lease_seconds: int = Field(default=60, ge=5, le=3_600)
    """How long a worker holds a step beyond its timeout before another instance may take it over;
    a running step's lease is renewed while it runs."""
    workflow_schedule_seconds: float = Field(default=20, ge=1, le=600)
    """How often schedules are checked (each occurrence starts one run, whatever the instances)."""
    workflow_notify_allow: list[str] = Field(default_factory=list)
    """Domains ``notify`` steps may call (a domain allows its subdomains). Empty: notify is off."""
    workflow_hook_tolerance: int = Field(default=300, ge=30, le=3_600)
    """Seconds an inbound webhook's signed timestamp may differ from now."""
    workflow_durable_agents: bool = False
    """Run ``agent`` steps marked ``durable`` as pgtask tasks on Evokai's Postgres, their model
    calls checkpointed (``evokai.workflows.durable``); needs Postgres and migration 0026."""

    # Isolated code execution (evokai.sandbox)
    code_enabled: bool = False
    """``EVOKAI_CODE_ENABLED``: offer the ``code_run`` tool (Amazon Bedrock AgentCore Code
    Interpreter) to agents that ask for it, when their application is granted ``code``. Off by
    default."""
    code_region: str | None = None
    """``EVOKAI_CODE_REGION``: where the code interpreter runs (default ``EVOKAI_AWS_REGION``). Only
    EU regions are accepted: any other region leaves the tool unavailable."""
    code_interpreter_id: str = "aws.codeinterpreter.v1"
    """``EVOKAI_CODE_INTERPRETER_ID``: the AWS-managed interpreter (sandbox network mode: no
    internet) or a custom one the deployment created."""
    code_timeout_seconds: int = Field(default=60, ge=5, le=900)
    """``EVOKAI_CODE_TIMEOUT_SECONDS``: what one ``code_run`` call may take, sandbox start and file
    transfers included; past it the sandbox session is stopped."""
    code_usd_per_second: float = Field(default=0.00003, ge=0, le=1)
    """``EVOKAI_CODE_USD_PER_SECOND``: the estimated cost of one sandbox session second, recorded
    with each call so it counts against the caps (1 vCPU and 2 GB at the AgentCore list prices,
    read 16/11/2026). Never shown to people."""

    # Capabilities (evokai.capabilities): what the deployment lets applications and agents turn on
    image_flavour: str | None = None
    """``lite`` or ``full``: the image this process runs in (set by the Dockerfile)."""
    capabilities: list[str] = Field(default_factory=list)
    """``EVOKAI_CAPABILITIES``: ids of the catalog this deployment offers (JSON list, ``["*"]`` for
    all of them). Each also needs the application's ``capability`` grant and the agent's own
    switch. Empty by default: nothing changes for an agent until all three say yes."""
    code_mode_timeout_seconds: float = Field(default=10, ge=1, le=120)
    """``EVOKAI_CODE_MODE_TIMEOUT_SECONDS``: execution time of one ``run_code`` program (time
    awaiting tools is not counted)."""
    code_mode_memory_mb: int = Field(default=128, ge=16, le=1_024)
    """``EVOKAI_CODE_MODE_MEMORY_MB``: heap of the in-process sandbox of one program."""
    code_mode_tool_calls: int = Field(default=50, ge=1, le=500)
    """``EVOKAI_CODE_MODE_TOOL_CALLS``: tool calls one program may make."""
    compaction_keep_tokens: int = Field(default=15_000, ge=500, le=500_000)
    """``EVOKAI_COMPACTION_KEEP_TOKENS``: tokens of the latest messages kept word for word beside
    the summary (at most half of what the history may hold); an agent may set its own."""
    compaction_buffer: float = Field(default=0.15, ge=0.0, le=0.5)
    """``EVOKAI_COMPACTION_BUFFER``: share of the context window left free for the answer and the
    estimate's error (at least 16,000 tokens, at most half the window)."""
    compaction_model: str | None = None
    """``EVOKAI_COMPACTION_MODEL``: writes the summaries (default: the agent's own model), within
    the allow-lists, the residency and the grants like any model."""
    context_windows: dict[str, int] = Field(default_factory=dict)
    """``EVOKAI_CONTEXT_WINDOWS``: input tokens per model id, over the vendored models.dev snapshot
    (``evokai.llm.context``), e.g. ``{"ollama:llama3.2": 8192}``."""
    worker_model_calls: int = Field(default=50, ge=1, le=500)
    """Model calls a capability worker may make through Evokai in one turn or run (its token)."""
    subagent_calls: int = Field(default=4, ge=1, le=20)
    """``EVOKAI_SUBAGENT_CALLS``: subagent calls one turn or run may make."""
    subagent_timeout_seconds: float = Field(default=120, ge=5, le=900)
    """``EVOKAI_SUBAGENT_TIMEOUT_SECONDS``: what one subagent call may take."""

    # Observability
    otel_enabled: bool = False
    """OpenTelemetry traces, metrics and logs. Off by default; exporters read OTEL_* variables."""
    service_name: str = "evokai"

    @field_validator("ask_rules")
    @classmethod
    def _ask_rules(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from evokai.guard.ask import check_rules, parse_rules, stored

        rules = parse_rules(v)
        if problems := check_rules(rules, set()):
            raise ValueError("; ".join(problems))
        return stored(rules)


@lru_cache
def get_settings() -> Settings:
    """Settings from the environment, read once.

    Returns:
        The process settings.
    """
    return Settings()
