"""Tables.

Portable on purpose: no database-specific types (ids are 36-char strings, documents are sa.JSON,
timestamps are timezone-aware), so the same models and migrations run on Postgres, MySQL/MariaDB,
SQLite and SQL Server. The schema, where there is one, is applied with a schema translate map.

The unit is the **application** (a client app). Evokai knows no users: an application passes an
opaque **subject** (its own user id) and optional **tags** with every session it opens.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from evokai.core.caps import DEFAULT_MONTHLY_USD
from evokai.core.util import new_id, utcnow

ID = sa.String(36)
SUBJECT = sa.String(200)
TS = sa.DateTime(timezone=True)


class Base(DeclarativeBase):
    """Declarative base."""

    type_annotation_map = {dict[str, Any]: sa.JSON, list[Any]: sa.JSON}  # ruff: ignore[mutable-class-default]


class Application(Base):
    """A client application. Caps are hard limits enforced by Evokai."""

    __tablename__ = "evokai_applications"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(sa.String(60), unique=True)
    name: Mapped[str] = mapped_column(sa.String(200))
    status: Mapped[str] = mapped_column(sa.String(16), default="active")
    monthly_cap_usd: Mapped[float] = mapped_column(sa.Float, default=DEFAULT_MONTHLY_USD)
    """The old monthly cap, in USD: it applies while ``caps`` sets no application month."""
    daily_cap_usd: Mapped[float | None] = mapped_column(sa.Float)
    """The old daily cap, in USD: it applies while ``caps`` sets no application day."""
    currency: Mapped[str] = mapped_column(sa.String(3), default="USD", server_default="USD")
    """The currency of ``caps`` (an ISO code). Spend is recorded in USD and converted."""
    exchange_rate: Mapped[float | None] = mapped_column(sa.Float)
    """Units of ``currency`` per 1 USD; required when the currency is not USD."""
    caps: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """Caps per calendar window (``evokai.core.caps``): ``{"application": [...], "subject":
    [...]}``, each ``{period, amount, resets}`` in ``currency``. None = the old fields only."""
    conversation_ttl_days: Mapped[int | None] = mapped_column(sa.Integer)
    """Conversations disappear this many days after their last message (Evokai storage)."""
    allowed_origins: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    allowed_models: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    defaults: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """Application defaults a session token may override (e.g. voice)."""
    storage_mode: Mapped[str] = mapped_column(
        sa.String(16), default="evokai", server_default="evokai"
    )
    """Where conversations live: evokai (here) or external (the app sends the history each time)."""
    security: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """The application's security policy (``evokai.guard``); None = the defaults."""
    ask_rules: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    """The application's ask rules (``evokai.guard.ask``): calls that wait for the person's
    confirmation, read after the deployment's and before each agent's. None = none."""
    residency: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """Data residency: where its data lives and which inference geographies models may use
    (``evokai.privacy``); None = no declaration."""
    data_protection: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """The data protection profile (``evokai.protection``): residency, jurisdiction and retention
    rules every external processor must meet; None = none."""
    security_event_ttl_days: Mapped[int | None] = mapped_column(sa.Integer)
    """Security events are deleted this many days after they happened (None = the deployment's
    ``EVOKAI_SECURITY_EVENT_TTL_DAYS``)."""
    audit_ttl_days: Mapped[int | None] = mapped_column(sa.Integer)
    """Audit log entries are deleted this many days after the change (None = the deployment's
    ``EVOKAI_AUDIT_TTL_DAYS``)."""
    system: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    """Shipped with Evokai (the built-in assistant, ``evokai.assistant``)."""
    locked: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    """A locked system application refuses every change (409 ``locked``) until an owner unlocks
    it; shipped updates are applied only while it is locked."""
    system_version: Mapped[str | None] = mapped_column(sa.String(64))
    """The hash of the shipped definition a system application was last set from."""
    kind: Mapped[str] = mapped_column(sa.String(16), default="agents", server_default="agents")
    """``agents`` (an application) or ``gateway`` (a gateway for coding tools, ``evokai.gateway``):
    a gateway is stored as an application row so roles, audit, security events, retention and
    data protection apply to it unchanged, and every path that lists or serves applications
    leaves it out."""
    gateway: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """A gateway's own settings: ``providers`` (``anthropic``, ``openai``) and the optional token
    caps per person (``tokens_per_day``, ``tokens_per_month``). None on an application."""
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class ApiKey(Base):
    """A key of an application: `server` (backend-to-backend) or `admin` (manage agents, keys)."""

    __tablename__ = "evokai_api_keys"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID, index=True)
    kind: Mapped[str] = mapped_column(sa.String(8))
    prefix: Mapped[str] = mapped_column(sa.String(24), unique=True)
    secret_hash: Mapped[str] = mapped_column(sa.String(128))
    scopes: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    label: Mapped[str] = mapped_column(sa.String(120), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class AgentRecord(Base):
    """A version of an agent definition. Conversations pin the version they started with."""

    __tablename__ = "evokai_agents"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(80))
    version: Mapped[str] = mapped_column(sa.String(40))
    definition: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(sa.String(80))
    publish_state: Mapped[str | None] = mapped_column(sa.String(16))
    """None (published as usual), ``pending`` (saved, waiting for its red team run before it is
    published) or ``blocked`` (its security score is below the application's gate)."""
    """Who saved it: ``config`` (the config file), ``operator``, or the id of the admin key."""
    __table_args__ = (
        sa.UniqueConstraint("application_id", "name", "version", name="uq_evokai_agent_version"),
    )


class McpServerRecord(Base):
    """An MCP server registered by an application. A static secret, if any, is encrypted."""

    __tablename__ = "evokai_mcp_servers"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(80))
    url: Mapped[str] = mapped_column(sa.String(500))
    auth: Mapped[str] = mapped_column(sa.String(16), default="forward_user")
    secret_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    timeout_seconds: Mapped[float] = mapped_column(sa.Float, default=60.0)
    tool_prefix: Mapped[str] = mapped_column(sa.String(40), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    tools: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    """The tools it listed last time (name, description, readOnly, destructive), for the console."""
    checked_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str | None] = mapped_column(sa.String(16))
    """``ok``, ``slow`` or ``down`` at the last listing or test."""
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    last_error: Mapped[str | None] = mapped_column(sa.String(300))
    connector: Mapped[str | None] = mapped_column(sa.String(40))
    """A ready connector served by Evokai itself (``sql``, ``openapi``...) instead of a URL."""
    config: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    worker: Mapped[bool] = mapped_column(sa.Boolean, default=False, server_default=sa.false())
    """An Evokai capability worker (``workers/capability``): its calls carry the conversation,
    the person and the agent (``_meta``), and it may call models through Evokai."""
    """The connector's configuration (names of environment variables, never secrets)."""
    __table_args__ = (sa.UniqueConstraint("application_id", "name", name="uq_evokai_mcp_name"),)


class SkillRecord(Base):
    """A skill of an application.

    Only name and description go into prompts; the body loads on demand.
    """

    __tablename__ = "evokai_skills"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(80))
    description: Mapped[str] = mapped_column(sa.String(1024))
    body: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (sa.UniqueConstraint("application_id", "name", name="uq_evokai_skill_name"),)


class SkillFileRecord(Base):
    """A file of a skill besides its SKILL.md: a reference, a data file or template, a script.

    Keyed by the skill's name, so saving the skill's text again keeps its files. Listings read
    path, size and title only; the text is read when the agent opens the file or runs a script.
    """

    __tablename__ = "evokai_skill_files"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    skill_name: Mapped[str] = mapped_column(sa.String(80))
    path: Mapped[str] = mapped_column(sa.String(200))
    content: Mapped[str] = mapped_column(sa.Text)
    size: Mapped[int] = mapped_column(sa.Integer)
    """Characters of the text."""
    title: Mapped[str] = mapped_column(sa.String(120), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "skill_name", "path", name="uq_evokai_skill_file"),
    )


class Session(Base):
    """A conversation of a subject with one agent. The agent version is pinned at creation."""

    __tablename__ = "evokai_sessions"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    tags: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    actor_kind: Mapped[str] = mapped_column(sa.String(16), default="agent", server_default="agent")
    """Who answers: an agent."""
    voice_engine: Mapped[str] = mapped_column(
        sa.String(16), default="browser", server_default="browser"
    )
    """Who does speech: ``browser`` (the client's on-device STT/TTS) or ``server`` (Pipecat)."""
    agent_name: Mapped[str] = mapped_column(sa.String(80))
    agent_version: Mapped[str] = mapped_column(sa.String(40))
    storage_mode: Mapped[str | None] = mapped_column(sa.String(16))
    """Where this conversation lives (``evokai`` or ``external``), chosen when its first token was
    minted; None (older conversations) follows the application's storage mode."""
    title: Mapped[str] = mapped_column(sa.String(120), default="Conversation")
    consent: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """The consent the session started under (version, accepted at, notice hash)."""
    exit_code_hash: Mapped[str | None] = mapped_column(sa.String(200))
    """Kiosk sessions: a hashed code that closes the session."""
    exit_attempts: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    prompt_options: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """The prompt modules and locale chosen at token mint: ``{"modules": {name: {"on", "params",
    "version"}}, "locale"}`` (``evokai.agents.modules``); None follows the agent's defaults."""
    client_tools: Mapped[list[dict[str, Any]] | None] = mapped_column(sa.JSON)
    """Custom client tools the session token declared (``evokai.tools.custom``): too big for the
    token, kept with the conversation and replaced by a later mint that sends them."""
    status: Mapped[str] = mapped_column(sa.String(16), default="active")
    last_seq: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.Index("ix_evokai_sessions_owner", "application_id", "subject", "updated_at"),
    )


class Thread(Base):
    """The model-facing history of a session. One thread per session."""

    __tablename__ = "evokai_threads"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ID, unique=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Message(Base):
    """One message of a thread, in a provider-neutral block format (text, toolUse, toolResult)."""

    __tablename__ = "evokai_messages"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    thread_id: Mapped[str] = mapped_column(ID)
    session_id: Mapped[str] = mapped_column(ID)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    turn_id: Mapped[str | None] = mapped_column(ID)
    position: Mapped[int] = mapped_column(sa.Integer)
    role: Mapped[str] = mapped_column(sa.String(16))
    content: Mapped[list[Any]] = mapped_column(sa.JSON)
    text: Mapped[str] = mapped_column(sa.Text, default="")
    events: Mapped[list[Any] | None] = mapped_column(sa.JSON)
    interrupted: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    feedback: Mapped[int | None] = mapped_column(sa.SmallInteger)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (sa.Index("ix_evokai_messages_thread", "thread_id", "position"),)


class Attachment(Base):
    """A file a person attached to a chat message (``evokai.attachments``), Evokai storage only.

    Kept with its conversation: deleted with it (time to live, erase, deletion). A rewritten
    question keeps its attachments; they go when its rewrite never stores a new question. Images
    are stored without their metadata.
    """

    __tablename__ = "evokai_attachments"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    session_id: Mapped[str] = mapped_column(ID)
    message_id: Mapped[str | None] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(200))
    media_type: Mapped[str] = mapped_column(sa.String(100))
    size: Mapped[int] = mapped_column(sa.Integer)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    data: Mapped[bytes] = mapped_column(sa.LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (sa.Index("ix_evokai_attachments_session", "session_id"),)


class ConversationSummary(Base):
    """The summary that stands in for a conversation's older messages (context compaction).

    One per conversation, updated in place by every later compaction; it covers the messages up to
    ``up_to_position`` of the thread. Evokai's own data (``evokai.sessions.compaction``), never
    state kept at a model provider; only for conversations stored by Evokai.
    """

    __tablename__ = "evokai_conversation_summaries"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ID, unique=True)
    thread_id: Mapped[str] = mapped_column(ID)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    up_to_position: Mapped[int] = mapped_column(sa.Integer)
    """The position of the last message the summary covers."""
    up_to_message_id: Mapped[str] = mapped_column(ID)
    summary: Mapped[str] = mapped_column(sa.Text)
    messages: Mapped[int] = mapped_column(sa.Integer, default=0)
    """How many messages the summary covers in all."""
    compactions: Mapped[int] = mapped_column(sa.Integer, default=1)
    model: Mapped[str] = mapped_column(sa.String(200), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Turn(Base):
    """One user turn and its outcome, with latency timings."""

    __tablename__ = "evokai_turns"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ID, index=True)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    mode: Mapped[str] = mapped_column(sa.String(8), default="text")
    status: Mapped[str] = mapped_column(sa.String(16), default="running")
    reason: Mapped[str | None] = mapped_column(sa.String(40))
    timings: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TS)


class Event(Base):
    """Every event sent to clients, numbered per session, for replay after a reconnect."""

    __tablename__ = "evokai_events"
    id: Mapped[int] = mapped_column(
        sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    session_id: Mapped[str] = mapped_column(ID)
    turn_id: Mapped[str | None] = mapped_column(ID)
    seq: Mapped[int] = mapped_column(sa.Integer)
    type: Mapped[str] = mapped_column(sa.String(24))
    payload: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (sa.UniqueConstraint("session_id", "seq", name="uq_evokai_events_seq"),)


class PendingAction(Base):
    """Tool calls that change data, waiting for confirmation, plus the results already known."""

    __tablename__ = "evokai_pending_actions"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ID, index=True)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    status: Mapped[str] = mapped_column(sa.String(16), default="open")
    state: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    """Set when the application keeps conversations itself: the state is then short-lived."""
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class SessionCredential(Base):
    """The end user's credential for `forward_user` MCP servers.

    Encrypted, and kept only for the token's life.
    """

    __tablename__ = "evokai_session_credentials"
    token_id: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    application_id: Mapped[str] = mapped_column(ID)
    encrypted: Mapped[str] = mapped_column(sa.Text)
    expires_at: Mapped[datetime] = mapped_column(TS, index=True)


class Memory(Base):
    """Something an agent remembers about a subject (``evokai.memory``), never customer data.

    ``kind`` is ``preference`` (how the person likes to work), ``semantic`` (a fact about their
    work context: «looks after the poetry shelf») or ``episodic`` (a short summary of a finished
    conversation, for agents that opt in). A memory replaced by a newer, contradicting one keeps
    ``superseded_by`` until it is purged; it is never recalled.
    """

    __tablename__ = "evokai_memories"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    fact: Mapped[str] = mapped_column(sa.String(1000))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    kind: Mapped[str] = mapped_column(
        sa.String(16), default="preference", server_default="preference"
    )
    agent_name: Mapped[str] = mapped_column(sa.String(80), default="", server_default="")
    """The agent that wrote it (episodes are recalled by their own agent only)."""
    session_id: Mapped[str | None] = mapped_column(ID)
    """The conversation it came from (an episode: the conversation it summarises)."""
    source: Mapped[str] = mapped_column(sa.String(16), default="agent", server_default="agent")
    """``agent`` (memory_remember), ``summary`` (an episode) or ``person`` (edited by them)."""
    embedding: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    """float32 little endian, for recall by meaning (empty without an embedding model)."""
    embedding_model: Mapped[str | None] = mapped_column(sa.String(120))
    updated_at: Mapped[datetime | None] = mapped_column(TS)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    """When it was last recalled into a prompt (recency of use)."""
    uses: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    superseded_by: Mapped[str | None] = mapped_column(ID)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    """The application's ``memory_ttl_days`` after it was written or last confirmed."""
    __table_args__ = (sa.Index("ix_evokai_memories_owner", "application_id", "subject"),)


class MemoryConsent(Base):
    """Whether a subject agreed that agents remember things about them (``memory_mode: ask``)."""

    __tablename__ = "evokai_memory_consents"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    granted: Mapped[bool] = mapped_column(sa.Boolean)
    source: Mapped[str] = mapped_column(sa.String(16), default="person")
    """``token`` (the application's backend, at token mint), ``person`` (the chat panel) or
    ``server`` (the server API)."""
    decided_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "subject", name="uq_evokai_memory_consent"),
    )


class UsageRecord(Base):
    """What a call cost: model tokens, characters and audio for speech, or text units.

    Kind ``guard`` is the injection classifier (Bedrock Guardrails): ``input_tokens`` holds its
    billed text units (up to 1,000 characters each) and ``characters`` the characters sent.

    Source of truth for caps.
    """

    __tablename__ = "evokai_usage_records"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    tags: Mapped[str] = mapped_column(sa.String(500), default="")
    """Sorted, comma-separated, with leading and trailing commas (",a,b,") so LIKE ',x,' matches."""
    agent_name: Mapped[str] = mapped_column(sa.String(80), default="")
    session_id: Mapped[str | None] = mapped_column(ID)
    turn_id: Mapped[str | None] = mapped_column(ID)
    kind: Mapped[str] = mapped_column(sa.String(8), default="llm")
    model: Mapped[str] = mapped_column(sa.String(120))
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    """Every input token, the cached ones included."""
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    """Input tokens read from the provider's prompt cache (part of input_tokens)."""
    cache_write_tokens: Mapped[int] = mapped_column(sa.Integer, default=0, server_default="0")
    """Input tokens written to the provider's prompt cache (part of input_tokens)."""
    characters: Mapped[int] = mapped_column(sa.Integer, default=0)
    audio_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    tool_calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    dedup_key: Mapped[str | None] = mapped_column(sa.String(200))
    """Set for a call that may be presented twice (a durable step's model call replayed after a
    resume): the second record with the same key is not written."""
    __table_args__ = (
        sa.Index("ix_evokai_usage_app_time", "application_id", "created_at"),
        sa.Index("ix_evokai_usage_subject_time", "application_id", "subject", "created_at"),
        sa.Index("ux_evokai_usage_dedup", "dedup_key", unique=True),
    )


class GatewayRequest(Base):
    """One request through a gateway: who, which provider and model, tokens, timings, outcome.

    Never the content: no prompt, no answer, no header value. No amount either: gateway traffic
    is the person's own subscription.
    """

    __tablename__ = "evokai_gateway_requests"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    """The gateway."""
    key_id: Mapped[str | None] = mapped_column(ID)
    """The person's gateway key (None when no valid key was presented)."""
    person: Mapped[str] = mapped_column(sa.String(120), default="")
    """The key's label (the person's name or e-mail) when the request was made."""
    provider: Mapped[str] = mapped_column(sa.String(16))
    model: Mapped[str] = mapped_column(sa.String(120), default="")
    outcome: Mapped[str] = mapped_column(sa.String(16))
    """``forwarded`` (the provider answered 2xx), ``upstream_error`` (it answered an error),
    ``blocked`` (a control refused it), ``failed`` (the provider could not be reached or the
    stream broke)."""
    control: Mapped[str | None] = mapped_column(sa.String(40))
    """The control that refused it (``blocked``)."""
    status: Mapped[int] = mapped_column(sa.Integer, default=0)
    """The HTTP status the tool got."""
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    """Every input token, the cached ones included."""
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    request_bytes: Mapped[int] = mapped_column(sa.Integer, default=0)
    response_bytes: Mapped[int] = mapped_column(sa.Integer, default=0)
    first_byte_ms: Mapped[int | None] = mapped_column(sa.Integer)
    """From the request's arrival to the first byte of the provider's answer."""
    duration_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.Index("ix_evokai_gateway_requests_time", "application_id", "created_at"),
        sa.Index("ix_evokai_gateway_requests_key", "key_id", "created_at"),
    )


class PromptRecord(Base):
    """A version of a named prompt of an application. Agents reference prompts by name."""

    __tablename__ = "evokai_prompts"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(80))
    version: Mapped[int] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(sa.Text)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """A prompt module's parameter schema (``evokai.agents.modules``); None for a plain prompt."""
    description: Mapped[str] = mapped_column(sa.Text, default="", server_default="")
    data: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """A static lookup table of the version, readable in the template as ``data``."""
    template_syntax: Mapped[str] = mapped_column(
        sa.String(16), default="handlebars", server_default="legacy"
    )
    """``handlebars``, or ``legacy`` for the versions saved before Handlebars (they keep rendering
    as they always did, ``evokai.agents.modules.render_text``)."""
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "name", "version", name="uq_evokai_prompt_version"),
    )


class VoiceRecord(Base):
    """A text-to-speech voice of a provider, in the deployment's catalog."""

    __tablename__ = "evokai_voices"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(sa.String(40))
    voice_id: Mapped[str] = mapped_column(sa.String(120))
    language: Mapped[str] = mapped_column(sa.String(16))
    name: Mapped[str] = mapped_column(sa.String(120))
    gender: Mapped[str | None] = mapped_column(sa.String(16))
    region: Mapped[str | None] = mapped_column(sa.String(40))
    """Where synthesis runs (e.g. eu-north-1, local)."""
    __table_args__ = (sa.UniqueConstraint("provider", "voice_id", name="uq_evokai_voice"),)


class BugReport(Base):
    """A problem reported from a session, with the context the client chose to send."""

    __tablename__ = "evokai_bug_reports"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID, index=True)
    subject: Mapped[str] = mapped_column(SUBJECT)
    session_id: Mapped[str | None] = mapped_column(ID)
    description: Mapped[str] = mapped_column(sa.Text)
    context: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    status: Mapped[str] = mapped_column(sa.String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Grant(Base):
    """An explicit grant or revocation of a capability for an application.

    Without a row, the default applies (see ``evokai.grants``). ``kind`` is model, mcp, tool,
    skill, client, voice, stt, tts, storage or channel; ``value`` is a boolean, or for models
    ``any``, ``fallback`` or ``off``.
    """

    __tablename__ = "evokai_grants"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID, index=True)
    kind: Mapped[str] = mapped_column(sa.String(16))
    name: Mapped[str] = mapped_column(sa.String(300))
    value: Mapped[Any] = mapped_column(sa.JSON)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_by: Mapped[str] = mapped_column(sa.String(80), default="")
    __table_args__ = (
        sa.UniqueConstraint("application_id", "kind", "name", name="uq_evokai_grant"),
    )


class SecurityEvent(Base):
    """A block, a redaction or a finding of the security layer (``evokai.guard``).

    Never holds a secret or raw personal data: the snippet is masked and clipped.
    """

    __tablename__ = "evokai_security_events"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    control: Mapped[str] = mapped_column(sa.String(40))
    rule: Mapped[str] = mapped_column(sa.String(60))
    action: Mapped[str] = mapped_column(sa.String(16))
    """logged (monitor), blocked, redacted, removed, tokenized."""
    mode: Mapped[str] = mapped_column(sa.String(8))
    severity: Mapped[str] = mapped_column(sa.String(8))
    direction: Mapped[str | None] = mapped_column(sa.String(16))
    agent_name: Mapped[str] = mapped_column(sa.String(80), default="")
    subject: Mapped[str | None] = mapped_column(SUBJECT)
    session_id: Mapped[str | None] = mapped_column(ID)
    turn_id: Mapped[str | None] = mapped_column(ID)
    tool: Mapped[str | None] = mapped_column(sa.String(120))
    snippet: Mapped[str] = mapped_column(sa.String(400), default="")
    details: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    __table_args__ = (
        sa.Index("ix_evokai_security_events_app_time", "application_id", "created_at"),
        sa.Index("ix_evokai_security_events_subject", "application_id", "subject"),
    )


class SubjectRevocation(Base):
    """Every session token of a subject issued before ``revoked_at`` is refused.

    The subject is kept as a keyed hash only (it may have been erased). Rows older than the longest
    token life are purged.
    """

    __tablename__ = "evokai_subject_revocations"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject_key: Mapped[str] = mapped_column(sa.String(64))
    revoked_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.Index("ix_evokai_subject_revocations_key", "application_id", "subject_key"),
    )


class AuditEntry(Base):
    """A change through the console or the admin and server APIs: who, when, what, before, after.

    Secrets are never kept (masked before writing); a subject appears only as a keyed hash.
    """

    __tablename__ = "evokai_audit_log"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    application_id: Mapped[str | None] = mapped_column(ID)
    """None for changes to the deployment (applications, sign-in settings, console users)."""
    actor: Mapped[str] = mapped_column(sa.String(200))
    """``sso:<e-mail>``, ``key:<prefix>``, ``admin-key:<id>``, ``operator-key``, ``config``."""
    actor_kind: Mapped[str] = mapped_column(sa.String(16))
    role: Mapped[str | None] = mapped_column(sa.String(16))
    action: Mapped[str] = mapped_column(sa.String(60))
    target: Mapped[str] = mapped_column(sa.String(300), default="")
    before: Mapped[Any | None] = mapped_column(sa.JSON)
    after: Mapped[Any | None] = mapped_column(sa.JSON)
    ip: Mapped[str] = mapped_column(sa.String(64), default="")
    correlation_id: Mapped[str] = mapped_column(sa.String(64), default="")
    __table_args__ = (sa.Index("ix_evokai_audit_app_time", "application_id", "at"),)


class ConsoleUser(Base):
    """A person who signed in to the console with single sign-on."""

    __tablename__ = "evokai_console_users"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    issuer: Mapped[str] = mapped_column(sa.String(300))
    sub: Mapped[str] = mapped_column(sa.String(300))
    email: Mapped[str] = mapped_column(sa.String(320), default="")
    name: Mapped[str] = mapped_column(sa.String(200), default="")
    roles: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """The roles of the last sign-in: application slug (or ``*``) → owner, operator or viewer."""
    disabled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (sa.UniqueConstraint("issuer", "sub", name="uq_evokai_console_user"),)


class ConsoleSetting(Base):
    """A deployment setting changed from the console (the sign-in provider)."""

    __tablename__ = "evokai_console_settings"
    key: Mapped[str] = mapped_column(sa.String(60), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_by: Mapped[str] = mapped_column(sa.String(200), default="")


class PolicyVersion(Base):
    """A saved version of a policy: the system policy's or an application's (a gateway's too).

    Immutable: every save writes a new row, a restore writes the old content as a new one. The
    content is ``{"security": ..., "data_protection": ...}``. An application's current policy is
    on its row (and is its latest version); the system policy is its latest version.
    """

    __tablename__ = "evokai_policy_versions"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ID)
    """The application's id, or ``system`` for the deployment's system policy."""
    number: Mapped[int] = mapped_column(sa.Integer)
    content: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str] = mapped_column(sa.String(200), default="")
    """Who saved it, as the audit log names them (``config`` for the config file)."""
    note: Mapped[str | None] = mapped_column(sa.String(500))
    __table_args__ = (sa.UniqueConstraint("owner_id", "number", name="uq_evokai_policy_version"),)


class PolicyTest(Base):
    """A policy test: a text and the outcome the guard's input checks must give it."""

    __tablename__ = "evokai_policy_tests"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ID)
    """The application's id, or ``system`` for the system policy's tests."""
    name: Mapped[str] = mapped_column(sa.String(120))
    input: Mapped[str] = mapped_column(sa.Text)
    outcome: Mapped[str | None] = mapped_column(sa.String(8))
    """The expected outcome of the whole text: ``allowed``, ``flagged`` or ``blocked``."""
    controls: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """The expected outcome per control (control id to ``allowed``, ``flagged`` or ``blocked``)."""
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (sa.UniqueConstraint("owner_id", "name", name="uq_evokai_policy_test"),)


class ConsolePreferences(Base):
    """The console preferences of one console identity (language, appearance, accessibility).

    Keyed by the login's identity: ``sso:<user id>``, ``admin-key:<key id>`` or ``operator``. The
    value is a small validated document (``evokai.identity.preferences.Preferences``).
    """

    __tablename__ = "evokai_console_preferences"
    identity: Mapped[str] = mapped_column(sa.String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class EvalCase(Base):
    """A test case of an agent: an input, an optional page, and the checks its answer must pass."""

    __tablename__ = "evokai_eval_cases"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    agent_name: Mapped[str] = mapped_column(sa.String(80))
    name: Mapped[str] = mapped_column(sa.String(120))
    input: Mapped[str] = mapped_column(sa.Text)
    page: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    """The page context sent with the input (path, title, text), as a browser would."""
    checks: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "agent_name", "name", name="uq_evokai_eval_case"),
    )


class EvalRun(Base):
    """A run of an agent's cases on a version, compared with a baseline version."""

    __tablename__ = "evokai_eval_runs"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    agent_name: Mapped[str] = mapped_column(sa.String(80))
    version: Mapped[str] = mapped_column(sa.String(40))
    baseline: Mapped[str | None] = mapped_column(sa.String(40))
    trigger: Mapped[str] = mapped_column(sa.String(16), default="manual")
    """``manual`` (on demand) or ``save`` (a new version was saved)."""
    suite: Mapped[str] = mapped_column(sa.String(16), default="cases", server_default="cases")
    """``cases`` (the agent's eval cases), ``redteam`` (the built-in attack suite) or ``models``
    (the cases on each allowed model, for the cheapest model that passes)."""
    status: Mapped[str] = mapped_column(sa.String(16), default="running")
    """``running``, ``done``, ``limit`` (the eval cap stopped it), ``failed`` or ``interrupted``."""
    summary: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """Per version: total, passed, failed, skipped, latency, tokens (and spend, server side)."""
    created_by: Mapped[str | None] = mapped_column(sa.String(80))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        sa.Index("ix_evokai_eval_runs_agent", "application_id", "agent_name", "started_at"),
    )


class EvalResult(Base):
    """One case on one version within a run."""

    __tablename__ = "evokai_eval_results"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ID, index=True)
    case_id: Mapped[str] = mapped_column(ID)
    case_name: Mapped[str] = mapped_column(sa.String(120))
    version: Mapped[str] = mapped_column(sa.String(40))
    passed: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    checks: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    """Each check with its outcome: passed, failed or skipped, and why."""
    answer: Mapped[str] = mapped_column(sa.Text, default="")
    tools: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    reason: Mapped[str] = mapped_column(sa.String(40), default="")
    latency_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cost_micro_usd: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    error: Mapped[str | None] = mapped_column(sa.String(300))
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    variant: Mapped[str | None] = mapped_column(sa.String(120))
    """The model a ``models`` run tried (None: the version's own model)."""


class Trace(Base):
    """The trace of one turn or server run: a summary to filter on and its spans as a document.

    Spans (the turn, each model call, tool call, guard decision, retrieval and speech) carry
    timings, counts and attributes; content (messages, tool arguments and results) only as the
    application's ``tracing_content`` allows, and never with external storage. ``cost_micro_usd``
    stays on the server: the console sees shares.
    """

    __tablename__ = "evokai_traces"
    id: Mapped[str] = mapped_column(ID, primary_key=True)
    """The turn's id, or the server run's id."""
    application_id: Mapped[str] = mapped_column(ID)
    kind: Mapped[str] = mapped_column(sa.String(8), default="turn")
    """``turn`` (a browser or voice turn), ``run`` (a server run) or ``eval`` (an eval case)."""
    session_id: Mapped[str | None] = mapped_column(ID, index=True)
    subject: Mapped[str] = mapped_column(SUBJECT)
    subject_key: Mapped[str] = mapped_column(sa.String(16), default="")
    """A keyed hash of the subject, what the console shows and searches."""
    agent_name: Mapped[str] = mapped_column(sa.String(80), default="")
    agent_version: Mapped[str] = mapped_column(sa.String(40), default="")
    mode: Mapped[str] = mapped_column(sa.String(8), default="text")
    status: Mapped[str] = mapped_column(sa.String(16), default="done")
    reason: Mapped[str | None] = mapped_column(sa.String(40))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    duration_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    first_token_ms: Mapped[int | None] = mapped_column(sa.Integer)
    model: Mapped[str | None] = mapped_column(sa.String(120))
    fallback: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    input_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    model_calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(sa.Integer, default=0)
    tool_errors: Mapped[int] = mapped_column(sa.Integer, default=0)
    guard_events: Mapped[int] = mapped_column(sa.Integer, default=0)
    error: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    feedback: Mapped[int | None] = mapped_column(sa.SmallInteger)
    """The last thumbs on the turn's answer (1 or -1)."""
    cost_micro_usd: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    content: Mapped[str] = mapped_column(sa.String(8), default="none")
    """What content the spans hold: ``none``, ``masked`` or ``full``."""
    spans: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    __table_args__ = (
        sa.Index("ix_evokai_traces_app_time", "application_id", "started_at"),
        sa.Index(
            "ix_evokai_traces_agent", "application_id", "agent_name", "agent_version", "started_at"
        ),
        sa.Index("ix_evokai_traces_subject", "application_id", "subject"),
    )


class ReviewNote(Base):
    """A reviewer's note and label on a conversation or one of its turns (console review)."""

    __tablename__ = "evokai_review_notes"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    session_id: Mapped[str] = mapped_column(ID)
    turn_id: Mapped[str | None] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    """The conversation's subject: the note goes when the person's data is erased."""
    label: Mapped[str | None] = mapped_column(sa.String(40))
    note: Mapped[str] = mapped_column(sa.Text, default="")
    author: Mapped[str] = mapped_column(sa.String(200), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (sa.Index("ix_evokai_review_notes_session", "application_id", "session_id"),)


class KnowledgeBase(Base):
    """A knowledge base of an application: documents an agent can search (``evokai.knowledge``).

    Documents and chunks belong to the application, never to a subject.
    """

    __tablename__ = "evokai_knowledge_bases"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(80))
    title: Mapped[str] = mapped_column(sa.String(200), default="")
    description: Mapped[str] = mapped_column(sa.String(1000), default="")
    embedding_model: Mapped[str] = mapped_column(sa.String(120))
    """``provider:model`` of the embeddings (Titan Text Embeddings v2 on Bedrock in production)."""
    dimensions: Mapped[int] = mapped_column(sa.Integer, default=1024)
    language: Mapped[str] = mapped_column(sa.String(16), default="simple")
    """Full-text configuration: ``simple``, ``italian``, ``english``... (Postgres text search)."""
    chunking: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """Chunk size, overlap and table size (``evokai.knowledge.chunking.ChunkSettings``)."""
    retrieval: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """Top k, score floor, MMR (``evokai.knowledge.search.RetrievalSettings``)."""
    access: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    """Groups a caller needs (any of them) to read anything here; empty = everyone."""
    link_template: Mapped[str | None] = mapped_column(sa.String(500))
    """The application's address of a document, with placeholders; None = a signed Evokai link."""
    sources: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    """Where documents come from besides uploads: addresses and connectors, synced on demand."""
    retention_days: Mapped[int | None] = mapped_column(sa.Integer)
    """Documents not updated for this many days are deleted (None = kept)."""
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "name", name="uq_evokai_knowledge_base"),
    )


class KnowledgeDocument(Base):
    """A document of a knowledge base and the state of its ingestion.

    ``status`` is ``queued``, ``ingesting``, ``ready`` or ``failed`` (with a ``reason``); a queued
    or ingesting document is picked up again when the service restarts.
    """

    __tablename__ = "evokai_knowledge_documents"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    kb_id: Mapped[str] = mapped_column(ID, index=True)
    source_kind: Mapped[str] = mapped_column(sa.String(16), default="upload")
    """``upload``, ``url`` or ``connector``."""
    source: Mapped[str] = mapped_column(sa.String(500))
    """The file name, the address, or ``<server>:<file id>`` (unique in the base)."""
    title: Mapped[str] = mapped_column(sa.String(300), default="")
    mime: Mapped[str] = mapped_column(sa.String(120), default="")
    checksum: Mapped[str] = mapped_column(sa.String(64), default="")
    """SHA-256 of the file: an unchanged file is not ingested again."""
    size: Mapped[int] = mapped_column(sa.Integer, default=0)
    status: Mapped[str] = mapped_column(sa.String(16), default="queued")
    reason: Mapped[str | None] = mapped_column(sa.String(300))
    version: Mapped[int] = mapped_column(sa.Integer, default=0)
    """Raised at every successful ingestion of new content."""
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", sa.JSON, default=dict)
    """The application's own fields, for filters (``{"year": 2026, "kind": "policy"}``)."""
    access: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    """Groups a caller needs (any of them) to read this document; empty = whoever reads the base."""
    chunks: Mapped[int] = mapped_column(sa.Integer, default=0)
    pages: Mapped[int | None] = mapped_column(sa.Integer)
    tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    """Embedding tokens of its last ingestion."""
    modified: Mapped[str | None] = mapped_column(sa.String(64))
    """The source's own change marker (a connector's modified time), to skip unchanged files."""
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    ingested_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        sa.UniqueConstraint("kb_id", "source", name="uq_evokai_knowledge_document"),
        sa.Index("ix_evokai_knowledge_documents_checksum", "kb_id", "checksum"),
    )


class KnowledgeChunk(Base):
    """A passage of a document with its place in it and its embedding.

    On Postgres with pgvector the migration adds ``embedding_v`` (``vector``) and ``tsv``
    (``tsvector``) next to these columns; ``embedding`` (float32, little endian) is then empty.
    """

    __tablename__ = "evokai_knowledge_chunks"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    kb_id: Mapped[str] = mapped_column(ID, index=True)
    document_id: Mapped[str] = mapped_column(ID, index=True)
    position: Mapped[int] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(sa.Text)
    kind: Mapped[str] = mapped_column(sa.String(8), default="text")
    """``text`` or ``table``."""
    page: Mapped[int | None] = mapped_column(sa.Integer)
    page_end: Mapped[int | None] = mapped_column(sa.Integer)
    section: Mapped[str] = mapped_column(sa.String(500), default="")
    """The heading path, joined with `` > ``."""
    headings: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    char_start: Mapped[int] = mapped_column(sa.Integer, default=0)
    char_end: Mapped[int] = mapped_column(sa.Integer, default=0)
    tokens: Mapped[int] = mapped_column(sa.Integer, default=0)
    dims: Mapped[int] = mapped_column(sa.Integer, default=0)
    """The embedding's dimensions (the partial vector indexes on Postgres select by it)."""
    embedding: Mapped[bytes | None] = mapped_column(sa.LargeBinary)


class KnowledgeFile(Base):
    """The original file of an uploaded or fetched document (for re-ingestion and links)."""

    __tablename__ = "evokai_knowledge_files"
    document_id: Mapped[str] = mapped_column(ID, primary_key=True)
    application_id: Mapped[str] = mapped_column(ID)
    data: Mapped[bytes] = mapped_column(sa.LargeBinary)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class CostAlert(Base):
    """A forecast that crossed a threshold of a cap, once per scope, month and threshold.

    Shares only: the forecast and the use so far as percentages of the cap, never amounts.
    """

    __tablename__ = "evokai_cost_alerts"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    scope: Mapped[str] = mapped_column(sa.String(80))
    """``application``, or ``subject:<keyed hash>`` for a person's monthly cap."""
    period: Mapped[str] = mapped_column(sa.String(16))
    """The cap's window (``evokai.core.caps.window_key``): ``2026-11`` for a month from the 1st,
    else its local start date and period letter, ``2026-11-14w``."""
    threshold: Mapped[int] = mapped_column(sa.Integer)
    forecast_pct: Mapped[float] = mapped_column(sa.Float)
    used_pct: Mapped[float] = mapped_column(sa.Float)
    fired_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    delivery: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """How the webhook went (status, attempts), when one is set."""
    acknowledged_at: Mapped[datetime | None] = mapped_column(TS)
    acknowledged_by: Mapped[str | None] = mapped_column(sa.String(200))
    __table_args__ = (
        sa.UniqueConstraint(
            "application_id", "scope", "period", "threshold", name="uq_evokai_cost_alert"
        ),
    )


class WorkflowRecord(Base):
    """A version of a workflow definition (``evokai.workflows``).

    Runs keep the version they started with.
    """

    __tablename__ = "evokai_workflows"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    name: Mapped[str] = mapped_column(sa.String(80))
    version: Mapped[str] = mapped_column(sa.String(40))
    definition: Mapped[dict[str, Any]] = mapped_column(sa.JSON)
    active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(sa.String(200))
    __table_args__ = (
        sa.UniqueConstraint("application_id", "name", "version", name="uq_evokai_workflow_version"),
    )


class WorkflowRun(Base):
    """A run of a workflow: its input, status and output.

    ``trigger_key`` makes a start idempotent: a schedule's occurrence, a webhook delivery, an
    event or a caller's idempotency key starts at most one run.
    """

    __tablename__ = "evokai_workflow_runs"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    workflow_name: Mapped[str] = mapped_column(sa.String(80))
    workflow_version: Mapped[str] = mapped_column(sa.String(40))
    status: Mapped[str] = mapped_column(sa.String(16), default="running")
    """``running``, ``waiting`` (an approval), ``done``, ``failed``, ``rejected``, ``cancelled``."""
    reason: Mapped[str | None] = mapped_column(sa.String(60))
    subject: Mapped[str | None] = mapped_column(SUBJECT)
    """Whose data the run handles, when it has one (erased with them)."""
    tags: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    trigger: Mapped[str] = mapped_column(sa.String(16), default="manual")
    """``manual``, ``api``, ``schedule``, ``webhook`` or ``event``."""
    trigger_key: Mapped[str | None] = mapped_column(sa.String(200))
    context: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """What started it (the trigger's payload), as the templates read it."""
    input: Mapped[Any] = mapped_column(sa.JSON, nullable=True)
    output: Mapped[Any] = mapped_column(sa.JSON, nullable=True)
    masked: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    """Inputs and outputs are stored with personal data masked (``pii.history`` enforced)."""
    error: Mapped[str | None] = mapped_column(sa.String(300))
    retention_days: Mapped[int] = mapped_column(sa.Integer, default=90)
    cost_micro_usd: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    """Its agent steps' usage (server side only: people see a share of the cap)."""
    created_by: Mapped[str | None] = mapped_column(sa.String(200))
    started_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        sa.UniqueConstraint(
            "application_id", "workflow_name", "trigger_key", name="uq_evokai_workflow_run_trigger"
        ),
        sa.Index("ix_evokai_workflow_runs_app_time", "application_id", "started_at"),
        sa.Index("ix_evokai_workflow_runs_subject", "application_id", "subject"),
    )


class WorkflowStep(Base):
    """The state of one step of a run: what a worker leases, runs, retries and resumes.

    A step is due when it is ``pending`` and ``due_at`` has come; a ``running`` step whose lease
    has expired (its worker stopped) is due again. ``path`` is the list it belongs to: empty at
    the top, ``<parallel id>/<branch>`` in a branch.
    """

    __tablename__ = "evokai_workflow_steps"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ID, index=True)
    application_id: Mapped[str] = mapped_column(ID)
    step_id: Mapped[str] = mapped_column(sa.String(48))
    kind: Mapped[str] = mapped_column(sa.String(16))
    path: Mapped[str] = mapped_column(sa.String(120), default="")
    status: Mapped[str] = mapped_column(sa.String(16), default="pending")
    """``pending``, ``running``, ``waiting``, ``done``, ``failed``, ``skipped``, ``cancelled``."""
    attempt: Mapped[int] = mapped_column(sa.Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(sa.String(64))
    lease_until: Mapped[datetime | None] = mapped_column(TS)
    idempotency_key: Mapped[str] = mapped_column(sa.String(120))
    """Stable across attempts and restarts: tools and webhooks can deduplicate with it."""
    input: Mapped[Any] = mapped_column(sa.JSON, nullable=True)
    output: Mapped[Any] = mapped_column(sa.JSON, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    """What the trace shows: the agent run's id, the model, tokens, the tool, the next step."""
    error: Mapped[str | None] = mapped_column(sa.String(300))
    cost_micro_usd: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("run_id", "step_id", name="uq_evokai_workflow_step"),
        sa.Index("ix_evokai_workflow_steps_due", "status", "due_at"),
    )


class WorkflowApproval(Base):
    """A decision a person owes a run: the question, what will happen, who may decide."""

    __tablename__ = "evokai_workflow_approvals"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    run_id: Mapped[str] = mapped_column(ID, index=True)
    step_id: Mapped[str] = mapped_column(sa.String(48))
    workflow_name: Mapped[str] = mapped_column(sa.String(80))
    subject: Mapped[str | None] = mapped_column(SUBJECT)
    status: Mapped[str] = mapped_column(sa.String(16), default="pending")
    """``pending``, ``approved``, ``rejected``, ``cancelled``."""
    question: Mapped[str] = mapped_column(sa.String(1000))
    summary: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    approvers: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    on_timeout: Mapped[str] = mapped_column(sa.String(8), default="reject")
    expires_at: Mapped[datetime] = mapped_column(TS)
    decided_by: Mapped[str | None] = mapped_column(sa.String(200))
    decided_via: Mapped[str | None] = mapped_column(sa.String(16))
    """``console``, ``api`` or ``timeout``."""
    comment: Mapped[str | None] = mapped_column(sa.String(2000))
    decided_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.Index("ix_evokai_workflow_approvals_queue", "application_id", "status", "created_at"),
    )


class OAuthConnection(Base):
    """A person's connection to a third-party system (``evokai.connections``).

    The agent acts as the person there: tools of a delegated connector call the provider with this
    connection's access token. Tokens are encrypted with the deployment's key, like MCP secrets;
    the connection is deleted by «disconnect», by the application revoking all of them and by the
    subject's erasure.
    """

    __tablename__ = "evokai_oauth_connections"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    provider: Mapped[str] = mapped_column(sa.String(32))
    """``microsoft365`` or ``google``."""
    account: Mapped[str | None] = mapped_column(sa.String(320))
    """The account at the provider (its e-mail or user name), shown to the person."""
    scopes: Mapped[list[Any]] = mapped_column(sa.JSON, default=list)
    """The scopes the provider granted."""
    access_token_encrypted: Mapped[str] = mapped_column(sa.Text)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(sa.Text)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    status: Mapped[str] = mapped_column(sa.String(16), default="active")
    """``active``, or ``expired`` when a refresh was refused (the person connects again)."""
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        sa.UniqueConstraint(
            "application_id", "subject", "provider", name="uq_evokai_oauth_connection"
        ),
        sa.Index("ix_evokai_oauth_connections_provider", "application_id", "provider"),
    )


class OAuthApp(Base):
    """An application's OAuth client at a provider people connect (``evokai.connections``).

    For the providers no delegated connector configures (GitHub, Linear, Notion, Slack): the
    client id, the client secret encrypted with the deployment's key (never shown again), and
    whether write scopes are asked.
    """

    __tablename__ = "evokai_oauth_apps"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    provider: Mapped[str] = mapped_column(sa.String(32))
    client_id: Mapped[str] = mapped_column(sa.String(300))
    client_secret_encrypted: Mapped[str] = mapped_column(sa.Text)
    writes: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "provider", name="uq_evokai_oauth_app"),
    )


class ChannelRecord(Base):
    """A way in besides the web panel: an e-mail mailbox, a Teams bot or a Slack app.

    ``config`` names environment variables for secrets, never the secrets themselves; ``state``
    is what the channel keeps between deliveries (the last IMAP message seen).
    """

    __tablename__ = "evokai_channels"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    kind: Mapped[str] = mapped_column(sa.String(16))
    """``email``, ``teams`` or ``slack``."""
    name: Mapped[str] = mapped_column(sa.String(80))
    agent: Mapped[str] = mapped_column(sa.String(80))
    enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    status: Mapped[str | None] = mapped_column(sa.String(16))
    """``ok`` or ``error`` at the last delivery or test."""
    last_error: Mapped[str | None] = mapped_column(sa.String(300))
    last_event_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "kind", "name", name="uq_evokai_channel_name"),
    )


class ChannelThread(Base):
    """A conversation reached through a channel, bound to its subject and an Evokai conversation.

    An e-mail thread, a Teams conversation or a Slack thread.

    ``thread_key`` is a hash of the channel's own thread id (e-mail Message-IDs, Slack
    channel and thread time, Teams conversation id), so no address is kept in clear here.
    ``reference`` is what a reply needs (where to post it), kept until the thread is forgotten.
    """

    __tablename__ = "evokai_channel_threads"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    channel_id: Mapped[str] = mapped_column(ID)
    thread_key: Mapped[str] = mapped_column(sa.String(64))
    subject: Mapped[str] = mapped_column(SUBJECT)
    conversation_id: Mapped[str] = mapped_column(ID)
    reference: Mapped[dict[str, Any]] = mapped_column(sa.JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("channel_id", "thread_key", name="uq_evokai_channel_thread"),
        sa.Index("ix_evokai_channel_threads_subject", "application_id", "subject"),
    )


class CapabilityRecord(Base):
    """What a harness capability keeps for a person or a conversation (``evokai.capabilities``).

    One row per item: a memory file (``memory``, kept per person and agent), a plan
    (``planning``), a step-log record (``step_persistence``), a spilled tool result
    (``tool_output_limits``), a spend counter (``spend_limits``). Rows with a ``session_id`` go with
    their conversation (deleted with it, by its time to live and by erasure); the others are the
    person's (exported and erased with them, ``expires_at`` for retention). Conversation content is
    kept only with Evokai storage.
    """

    __tablename__ = "evokai_capability_records"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    subject: Mapped[str] = mapped_column(SUBJECT)
    """The person; empty for an application-wide record (a spend counter of the application)."""
    session_id: Mapped[str | None] = mapped_column(ID)
    capability: Mapped[str] = mapped_column(sa.String(40))
    scope: Mapped[str] = mapped_column(sa.String(120))
    """Inside the capability: the agent of a memory file, the conversation of a plan."""
    key: Mapped[str] = mapped_column(sa.String(300))
    value: Mapped[str] = mapped_column(sa.Text)
    version: Mapped[str] = mapped_column(sa.String(36))
    """Changes on every write (compare-and-set of memory files)."""
    meta: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        sa.UniqueConstraint(
            "application_id",
            "subject",
            "capability",
            "scope",
            "key",
            name="uq_evokai_capability_record",
        ),
        sa.Index("ix_evokai_capability_records_session", "session_id"),
        sa.Index("ix_evokai_capability_records_expires", "expires_at"),
    )


class CapabilityCredential(Base):
    """An application's credentials for a capability's provider, encrypted like MCP secrets.

    A search API, an integration or a remote sandbox; Fernet with the deployment's key.

    ``values_encrypted`` holds every value as one encrypted JSON object; ``names`` says which are
    set (the console shows that, never a secret); ``plain`` keeps the values that are not secrets
    (a workspace id, a server domain) for the console.
    """

    __tablename__ = "evokai_capability_credentials"
    id: Mapped[str] = mapped_column(ID, primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ID)
    provider: Mapped[str] = mapped_column(sa.String(40))
    values_encrypted: Mapped[str] = mapped_column(sa.Text)
    names: Mapped[list[str]] = mapped_column(sa.JSON, default=list)
    plain: Mapped[dict[str, str] | None] = mapped_column(sa.JSON)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    __table_args__ = (
        sa.UniqueConstraint("application_id", "provider", name="uq_evokai_capability_credential"),
    )
