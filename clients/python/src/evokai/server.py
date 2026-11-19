"""The application's backend: server API (server key) and admin API (admin key).

Keys never belong in a browser. Amounts (``usage()``, a run's ``cost_usd``) are server to server:
convert them to shares (:func:`evokai.to_usage_shares`) before anything reaches a person.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, TypeVar

import httpx
from typing_extensions import Self

from evokai import _calls as calls
from evokai._http import AsyncTransport, Call, SyncTransport
from evokai.errors import EvokaiError
from evokai.events import ConversationDetail
from evokai.models import (
    ActivityItem,
    AgentDef,
    AgentPutResult,
    AgentSummary,
    AgentVersion,
    ApiKeyInfo,
    Application,
    AppPatch,
    AskRule,
    AskRulesView,
    BugReport,
    CompliancePack,
    ComplianceProfile,
    ComplianceSnapshot,
    Connection,
    ConnectorKind,
    Consent,
    ConversationSummary,
    EraseResult,
    ExportedConfig,
    GrantChange,
    GrantView,
    KeyRequest,
    McpServerBody,
    McpServerInfo,
    McpTestResult,
    MemoryConsentResult,
    MemoryFact,
    NewKey,
    PromptVersion,
    RunAttachment,
    RunMessage,
    RunRequest,
    RunResult,
    SecurityEventPage,
    SecurityPolicy,
    SecuritySummary,
    SecurityView,
    SessionToken,
    SkillBody,
    SkillDetail,
    SkillFile,
    SkillInfo,
    SubjectExport,
    SubjectLimits,
    TokenRequest,
    UsageReport,
    UsageSummary,
    VoiceBody,
    VoiceInfo,
)
from evokai.session import AsyncEvokaiSession, EvokaiSession

__all__ = ["AsyncEvokaiServer", "EvokaiServer"]

T = TypeVar("T")
When = datetime | str | None
Obj = Mapping[str, Any]


def _token_request(subject: str, agent: str, options: dict[str, Any]) -> TokenRequest:
    fields = {k: v for k, v in options.items() if v is not None}
    return TokenRequest.model_validate({"subject": subject, "agent": agent, **fields})


def _run_request(
    input: str | Sequence[RunMessage | Obj],
    subject: str | None,
    tags: Sequence[str] | None,
    attachments: Sequence[RunAttachment | Obj] | None,
    store: bool | None,
    groups: Sequence[str] | None = None,
) -> RunRequest:
    data: dict[str, Any] = {"input": input if isinstance(input, str) else list(input)}
    if subject is not None:
        data["subject"] = subject
    if tags is not None:
        data["tags"] = list(tags)
    if attachments is not None:
        data["attachments"] = list(attachments)
    if store is not None:
        data["store"] = store
    if groups is not None:
        data["groups"] = list(groups)
    return RunRequest.model_validate(data)


def _security_query(**params: Any) -> dict[str, Any]:
    return params


class _Keys:
    """Holds the keys and picks the one a call needs."""

    def __init__(self, server_key: str | None, admin_key: str | None) -> None:
        if not server_key and not admin_key:
            raise ValueError("server_key or admin_key is required")
        self.server_key = server_key
        self.admin_key = admin_key

    def credential(self, call: Call[Any]) -> str:
        if call.admin:
            if not self.admin_key:
                raise EvokaiError(0, "no_admin_key", "An admin key is required for admin calls.")
            return self.admin_key
        key = self.server_key or self.admin_key
        return key or ""


# Sync


class EvokaiServer:
    """The server and admin API (sync).

    Args:
        base_url: Where the service is mounted, base path included
            (``https://agents.example.com/agent``).
        server_key: The application's server key (``evk_srv_...``).
        admin_key: The application's admin key (``evk_adm_...``), needed only for ``admin``.
        http_client: An ``httpx.Client`` to use (not closed by this client).
        timeout: Timeout of each request when this client creates its own HTTP client.
    """

    def __init__(
        self,
        base_url: str,
        server_key: str | None = None,
        *,
        admin_key: str | None = None,
        http_client: httpx.Client | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> None:
        self._keys = _Keys(server_key, admin_key)
        self._transport = SyncTransport(base_url, http_client=http_client, timeout=timeout)
        self.conversations = Conversations(self)
        self.memories = Memories(self)
        self.connections = Connections(self)
        self.privacy = Privacy(self)
        self.admin = Admin(self)

    def _send(self, call: Call[T]) -> T:
        return self._transport.send(call, self._keys.credential(call))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client, if this client created it."""
        self._transport.close()

    def mint_session_token(
        self,
        subject: str,
        agent: str,
        *,
        conversation_id: str | None = None,
        tags: Sequence[str] | None = None,
        ttl_seconds: int | None = None,
        forward_credential: str | None = None,
        limits: SubjectLimits | Obj | None = None,
        model: str | None = None,
        voice: str | None = None,
        voice_engine: Literal["browser", "server"] | None = None,
        storage_mode: Literal["evokai", "external"] | None = None,
        consent: Consent | Obj | None = None,
        exit_code: str | None = None,
        groups: Sequence[str] | None = None,
        memory_consent: bool | None = None,
    ) -> SessionToken:
        """Mint a short-lived session token for the browser (it creates the conversation).

        Args:
            subject: Your own opaque id of the end user.
            agent: The agent to talk to.
            conversation_id: Continue a conversation of this subject (default: a new one).
            tags: Tags that group usage (``team:ops``).
            ttl_seconds: The token's life (at least 30, at most the deployment's maximum).
            forward_credential: The user's credential for ``forward_user`` MCP servers.
            limits: Per-subject caps for this session.
            model: The primary model, among those the agent offers.
            voice: The voice of this session.
            voice_engine: ``browser`` (on-device speech) or ``server`` (Pipecat over WebRTC).
            storage_mode: Where a new conversation lives: ``evokai`` or ``external``.
            consent: The consent the user gave (version, accepted_at, notice_hash).
            exit_code: Kiosk sessions: a code that closes the session.
            groups: The subject's groups, for knowledge bases restricted to some groups.
            memory_consent: The person's answer to long-term memory, when the application asked it
                in its own interface (``memory_mode: ask``); False deletes what was remembered.

        Returns:
            The token, its expiry, the conversation, the voice engine, the storage mode, the usage
            shares, the agent's starter questions and the person's memory state.
        """
        req = _token_request(
            subject,
            agent,
            {
                "conversation_id": conversation_id,
                "tags": list(tags) if tags is not None else None,
                "ttl_seconds": ttl_seconds,
                "forward_credential": forward_credential,
                "limits": limits,
                "model": model,
                "voice": voice,
                "voice_engine": voice_engine,
                "storage_mode": storage_mode,
                "consent": consent,
                "exit_code": exit_code,
                "groups": list(groups) if groups is not None else None,
                "memory_consent": memory_consent,
            },
        )
        return self._send(calls.mint(req))

    def run(
        self,
        agent: str,
        input: str | Sequence[RunMessage | Obj],
        *,
        subject: str | None = None,
        tags: Sequence[str] | None = None,
        attachments: Sequence[RunAttachment | Obj] | None = None,
        store: bool | None = None,
        groups: Sequence[str] | None = None,
    ) -> RunResult:
        """Run an agent that allows the ``server`` channel and wait for its answer.

        Args:
            agent: The agent's name.
            input: The user's message, or a conversation (``RunMessage`` or ``{role, text}``).
            subject: Who the run is for (default ``system:<agent>``).
            tags: Tags that group usage.
            attachments: Text attachments (``RunAttachment`` or ``{name, text, mediaType}``).
            store: Keep the run as a conversation of the subject.
            groups: The subject's groups, for knowledge bases restricted to some groups.

        Returns:
            The output (JSON when the agent has an ``output_schema``, else text), the model and
            the usage with its cost.
        """
        return self._send(
            calls.run(agent, _run_request(input, subject, tags, attachments, store, groups))
        )

    def usage(
        self,
        *,
        from_: When = None,
        to: When = None,
        group_by: str | Sequence[str] | None = None,
        subject: str | None = None,
        tag: str | None = None,
        agent: str | None = None,
    ) -> UsageReport:
        """Usage of the application in a period (default: this month), with amounts.

        Args:
            from_: Start of the period.
            to: End of the period.
            group_by: One or more of subject, agent, kind, model, day, tags.
            subject: Only this subject.
            tag: Only sessions with this tag.
            agent: Only this agent.

        Returns:
            The rows and the application's caps and spend (server to server only).
        """
        return self._send(calls.usage(from_, to, group_by, subject, tag, agent))

    def agents(self) -> builtins.list[AgentSummary]:
        """The application's active agents."""
        return self._send(calls.agents())

    def voices(self, language: str | None = None) -> builtins.list[VoiceInfo]:
        """The text-to-speech voices of the deployment."""
        return self._send(calls.voices(language))

    def session(self, token: str | SessionToken, **options: Any) -> EvokaiSession:
        """A session client on the same service and HTTP client.

        Args:
            token: A session token or a minted ``SessionToken``.
            **options: ``replay_attempts``, ``replay_delay``.

        Returns:
            The session.
        """
        return EvokaiSession(
            self._transport.base_url, token, http_client=self._transport.client, **options
        )


class Conversations:
    """A subject's conversations."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self, subject: str, *, limit: int | None = None) -> builtins.list[ConversationSummary]:
        """A subject's recent conversations, newest first."""
        return self._s._send(calls.conversations_list(subject, limit))

    def get(self, conversation_id: str, subject: str) -> ConversationDetail:
        """A subject's conversation with its messages and open confirmation."""
        return self._s._send(calls.conversations_get(conversation_id, subject))

    def delete(self, conversation_id: str, subject: str) -> None:
        """Delete a subject's conversation."""
        self._s._send(calls.conversations_delete(conversation_id, subject))


class Memories:
    """What the agents remember about a subject."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self, subject: str) -> builtins.list[MemoryFact]:
        """The subject's memories."""
        return self._s._send(calls.memories_list(subject))

    def forget(self, subject: str, memory_id: str | None = None) -> int:
        """Forget one fact, or everything about the subject.

        Returns:
            How many facts were deleted.
        """
        return self._s._send(calls.memories_forget(subject, memory_id))

    def consent(self, subject: str, granted: bool) -> MemoryConsentResult:
        """Record the subject's answer to long-term memory, asked in your own interface.

        Args:
            subject: Your own opaque id of the end user.
            granted: True to allow memory; False deletes what was remembered (the latest answer
                counts).

        Returns:
            The subject's new memory state and how many memories were deleted.
        """
        return self._s._send(calls.memories_consent(subject, granted))


class Connections:
    """A subject's connections to Microsoft 365 or Google Workspace (tokens never leave Evokai)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self, subject: str) -> builtins.list[Connection]:
        """The subject's connections."""
        return self._s._send(calls.connections_list(subject))

    def disconnect(self, provider: str, subject: str) -> None:
        """Disconnect the subject from a provider (their tokens are deleted)."""
        self._s._send(calls.connections_disconnect(provider, subject))


class Privacy:
    """Data protection requests about one subject (the subject travels in the body)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def export(self, subject: str) -> SubjectExport:
        """Everything Evokai keeps about the subject (right of access and portability)."""
        return self._s._send(calls.privacy_export(subject))

    def erase(self, subject: str) -> EraseResult:
        """Erase the subject (right to be forgotten); idempotent."""
        return self._s._send(calls.privacy_erase(subject))

    def revoke_sessions(self, subject: str) -> None:
        """Refuse every session token of the subject minted until now."""
        self._s._send(calls.privacy_revoke_sessions(subject))


class Admin:
    """The admin API (admin key)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server
        self.application = AdminApplication(server)
        self.keys = AdminKeys(server)
        self.grants = AdminGrants(server)
        self.ask_rules = AdminAskRules(server)
        self.agents = AdminAgents(server)
        self.mcp_servers = AdminMcpServers(server)
        self.skills = AdminSkills(server)
        self.prompts = AdminPrompts(server)
        self.voices = AdminVoices(server)
        self.security = AdminSecurity(server)
        self.compliance = AdminCompliance(server)

    def providers(self) -> dict[str, Any]:
        """The provider catalog: who processes data, where, under whose control (no prices)."""
        return self._s._send(calls.providers())

    def data_protection(self) -> dict[str, Any]:
        """The data protection profile and every external processor under it, with the reasons."""
        return self._s._send(calls.data_protection())

    def bug_reports(self) -> builtins.list[BugReport]:
        """Recent problem reports from sessions."""
        return self._s._send(calls.bug_reports())

    def activity(self, *, limit: int | None = None) -> builtins.list[ActivityItem]:
        """What changed lately: agent versions, keys and grants, newest first."""
        return self._s._send(calls.activity(limit))

    def usage_summary(
        self,
        *,
        from_: When = None,
        to: When = None,
        tag: str | None = None,
        agent: str | None = None,
    ) -> UsageSummary:
        """Turns, fallback answers, time to the first word, subjects and tags of a period."""
        return self._s._send(calls.usage_summary(from_, to, tag, agent))

    def export(self) -> ExportedConfig:
        """The application as a config file (secrets left out)."""
        return self._s._send(calls.export_config())


class AdminApplication:
    """The application's settings."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def get(self) -> Application:
        """The application's settings."""
        return self._s._send(calls.application_get())

    def patch(self, patch: AppPatch | Obj | None = None, /, **fields: Any) -> None:
        """Change settings: only the fields given are sent (None clears a default).

        Args:
            patch: An ``AppPatch`` or a mapping.
            **fields: Or the fields as keyword arguments.
        """
        self._s._send(calls.application_patch(patch if patch is not None else fields))


class AdminKeys:
    """The application's keys (rotation: create, deploy, revoke)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self) -> builtins.list[ApiKeyInfo]:
        """The keys (never their secrets)."""
        return self._s._send(calls.keys_list())

    def create(self, kind: Literal["srv", "adm"] = "srv", label: str = "") -> NewKey:
        """Create a key; the key is returned only now."""
        return self._s._send(calls.keys_create(KeyRequest(kind=kind, label=label)))

    def revoke(self, key_id: str) -> None:
        """Revoke a key (not the one making the request)."""
        self._s._send(calls.keys_revoke(key_id))

    def rotate(self, key_id: str) -> NewKey:
        """A successor with the same kind and label; the old key stays valid until revoked."""
        return self._s._send(calls.keys_rotate(key_id))


class AdminAskRules:
    """Ask rules: tool calls that wait for the person's confirmation (only ever added)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def get(self) -> AskRulesView:
        """The application's rules, the deployment's before them, each agent's after them."""
        return self._s._send(calls.ask_rules_get())

    def put(self, rules: Sequence[AskRule | Obj]) -> AskRulesView:
        """Replace the application's rules (400 ``ask_rules`` explains a refused one).

        Returns:
            The new view.
        """
        return self._s._send(calls.ask_rules_put(rules))


class AdminGrants:
    """What the application may use."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def get(self) -> GrantView:
        """Every item with its default, override and value."""
        return self._s._send(calls.grants_get())

    def change(self, changes: Sequence[GrantChange | Obj]) -> GrantView:
        """Grant, revoke or reset (value None) items; effective from the next turn.

        Returns:
            The new view.
        """
        return self._s._send(calls.grants_change(changes))


class AdminAgents:
    """Agent definitions and their versions."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self) -> builtins.list[AgentDef]:
        """The active agents with their definitions and versions."""
        return self._s._send(calls.agents_list())

    def versions(self, name: str) -> builtins.list[AgentVersion]:
        """Every version of an agent, newest first."""
        return self._s._send(calls.agents_versions(name))

    def put(self, agent: AgentDef | Obj) -> AgentPutResult:
        """Create or update an agent; a changed definition becomes a new active version."""
        return self._s._send(calls.agents_put(agent))

    def delete(self, name: str) -> None:
        """Retire an agent (pinned conversations keep their version)."""
        self._s._send(calls.agents_delete(name))


class AdminMcpServers:
    """MCP servers and ready connectors."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self) -> builtins.list[McpServerInfo]:
        """The MCP servers (secrets never leave the service)."""
        return self._s._send(calls.mcp_list())

    def put(self, name: str, server: McpServerBody | Obj) -> None:
        """Register or replace an MCP server (a URL, or a ``connector`` with its ``config``)."""
        self._s._send(calls.mcp_put(name, server))

    def delete(self, name: str) -> None:
        """Remove an MCP server."""
        self._s._send(calls.mcp_delete(name))

    def test(self, name: str, credential: str | None = None) -> McpTestResult:
        """Connect and list its tools (``credential``: a user's token, never stored)."""
        return self._s._send(calls.mcp_test(name, credential))

    def connectors(self) -> builtins.list[ConnectorKind]:
        """The ready connectors with their configuration schemas."""
        return self._s._send(calls.mcp_connectors())


class AdminSkills:
    """Skills (SKILL.md)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self) -> builtins.list[SkillInfo]:
        """The skills without their bodies."""
        return self._s._send(calls.skills_list())

    def get(self, name: str) -> SkillDetail:
        """One skill with its body."""
        return self._s._send(calls.skills_get(name))

    def put(self, name: str, skill: SkillBody | Obj) -> None:
        """Create or replace a skill."""
        self._s._send(calls.skills_put(name, skill))

    def file(self, name: str, path: str) -> SkillFile:
        """One file of a skill with its text (``path`` like ``references/terms.md``)."""
        return self._s._send(calls.skill_files_get(name, path))

    def put_file(self, name: str, path: str, content: str) -> None:
        """Add or replace one file of a skill."""
        self._s._send(calls.skill_files_put(name, path, content))

    def delete_file(self, name: str, path: str) -> None:
        """Remove one file of a skill."""
        self._s._send(calls.skill_files_delete(name, path))

    def delete(self, name: str) -> None:
        """Remove a skill."""
        self._s._send(calls.skills_delete(name))


class AdminPrompts:
    """Versioned prompts."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def list(self) -> builtins.list[PromptVersion]:
        """Every version of every prompt."""
        return self._s._send(calls.prompts_list())

    def put(self, name: str, text: str) -> int:
        """Save a prompt; a changed text becomes the next version.

        Returns:
            The version.
        """
        return self._s._send(calls.prompts_put(name, text))


class AdminVoices:
    """The deployment's voice catalogue (list it with ``EvokaiServer.voices``)."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def put(self, provider: str, voice_id: str, voice: VoiceBody | Obj) -> None:
        """Add or replace a voice."""
        self._s._send(calls.voices_put(provider, voice_id, voice))


class AdminSecurity:
    """The security layer: the policy, what applies, the events."""

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def get(self) -> SecurityView:
        """The policy, what applies, the controls and each agent's own policy."""
        return self._s._send(calls.security_get())

    def put(self, policy: SecurityPolicy | Obj) -> SecurityView:
        """Replace the policy (unset fields follow the defaults); effective from the next turn."""
        return self._s._send(calls.security_put(policy))

    def events(
        self,
        *,
        from_: When = None,
        to: When = None,
        control: str | None = None,
        rule: str | None = None,
        action: str | None = None,
        severity: str | None = None,
        agent: str | None = None,
        subject: str | None = None,
        before: When = None,
        limit: int | None = None,
    ) -> SecurityEventPage:
        """Security events, newest first; pass ``next`` back as ``before`` for the next page."""
        query = _security_query(
            from_=from_,
            to=to,
            control=control,
            rule=rule,
            action=action,
            severity=severity,
            agent=agent,
            subject=subject,
            before=before,
            limit=limit,
        )
        return self._s._send(calls.security_events(query))

    def summary(
        self, *, from_: When = None, to: When = None, agent: str | None = None
    ) -> SecuritySummary:
        """Counts of a period by rule, control, action and severity."""
        return self._s._send(calls.security_summary(from_, to, agent))


class AdminCompliance:
    """Compliance packs (AI Act, GDPR, control mapping), the profile, snapshots and their diff.

    A pack is JSON (a :class:`CompliancePack`) or text with ``format="md"`` or ``"html"``;
    ``lang`` is ``en`` or ``it`` (default: the application's locale).
    """

    def __init__(self, server: EvokaiServer) -> None:
        self._s = server

    def profile(self) -> ComplianceProfile:
        """The application's compliance profile."""
        return self._s._send(calls.compliance_profile_get())

    def put_profile(self, profile: ComplianceProfile | Obj) -> ComplianceProfile:
        """Replace the profile (owner); text with personal data or secrets is refused (400)."""
        return self._s._send(calls.compliance_profile_put(profile))

    def pack(
        self,
        pack: Literal["ai-act", "gdpr", "controls"],
        *,
        format: Literal["json", "md", "html"] = "json",
        lang: Literal["en", "it"] | None = None,
    ) -> CompliancePack | str:
        """A pack generated now: JSON, or Markdown or HTML text."""
        return self._s._send(calls.compliance_pack(pack, format, lang))

    def snapshots(self, *, limit: int | None = None) -> builtins.list[ComplianceSnapshot]:
        """The stored snapshots, newest first."""
        return self._s._send(calls.compliance_snapshots(limit))

    def snapshot(self, label: str = "") -> ComplianceSnapshot:
        """Record what is true now (operator)."""
        return self._s._send(calls.compliance_snapshot_create(label))

    def snapshot_pack(
        self,
        snapshot_id: str,
        pack: Literal["ai-act", "gdpr", "controls"],
        *,
        format: Literal["json", "md", "html"] = "json",
        lang: Literal["en", "it"] | None = None,
    ) -> CompliancePack | str:
        """A pack as it was when a snapshot was taken."""
        return self._s._send(calls.compliance_snapshot_pack(snapshot_id, pack, format, lang))

    def diff(
        self, from_id: str, to_id: str, *, lang: Literal["en", "it"] | None = None
    ) -> dict[str, Any]:
        """What changed between two snapshots: statements per pack and facts."""
        return self._s._send(calls.compliance_diff(from_id, to_id, lang))


# Async


class AsyncEvokaiServer:
    """The server and admin API (async); see :class:`EvokaiServer`.

    Args:
        base_url: Where the service is mounted, base path included.
        server_key: The application's server key.
        admin_key: The application's admin key, needed only for ``admin``.
        http_client: An ``httpx.AsyncClient`` to use (not closed by this client).
        timeout: Timeout of each request when this client creates its own HTTP client.
    """

    def __init__(
        self,
        base_url: str,
        server_key: str | None = None,
        *,
        admin_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> None:
        self._keys = _Keys(server_key, admin_key)
        self._transport = AsyncTransport(base_url, http_client=http_client, timeout=timeout)
        self.conversations = AsyncConversations(self)
        self.memories = AsyncMemories(self)
        self.connections = AsyncConnections(self)
        self.privacy = AsyncPrivacy(self)
        self.admin = AsyncAdmin(self)

    async def _send(self, call: Call[T]) -> T:
        return await self._transport.send(call, self._keys.credential(call))

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client, if this client created it."""
        await self._transport.aclose()

    async def mint_session_token(
        self,
        subject: str,
        agent: str,
        *,
        conversation_id: str | None = None,
        tags: Sequence[str] | None = None,
        ttl_seconds: int | None = None,
        forward_credential: str | None = None,
        limits: SubjectLimits | Obj | None = None,
        model: str | None = None,
        voice: str | None = None,
        voice_engine: Literal["browser", "server"] | None = None,
        storage_mode: Literal["evokai", "external"] | None = None,
        consent: Consent | Obj | None = None,
        exit_code: str | None = None,
        groups: Sequence[str] | None = None,
        memory_consent: bool | None = None,
    ) -> SessionToken:
        """Mint a short-lived session token; see :meth:`EvokaiServer.mint_session_token`.

        Returns:
            The minted token.
        """
        req = _token_request(
            subject,
            agent,
            {
                "conversation_id": conversation_id,
                "tags": list(tags) if tags is not None else None,
                "ttl_seconds": ttl_seconds,
                "forward_credential": forward_credential,
                "limits": limits,
                "model": model,
                "voice": voice,
                "voice_engine": voice_engine,
                "storage_mode": storage_mode,
                "consent": consent,
                "exit_code": exit_code,
                "groups": list(groups) if groups is not None else None,
                "memory_consent": memory_consent,
            },
        )
        return await self._send(calls.mint(req))

    async def run(
        self,
        agent: str,
        input: str | Sequence[RunMessage | Obj],
        *,
        subject: str | None = None,
        tags: Sequence[str] | None = None,
        attachments: Sequence[RunAttachment | Obj] | None = None,
        store: bool | None = None,
        groups: Sequence[str] | None = None,
    ) -> RunResult:
        """Run a server agent and wait for its answer; see :meth:`EvokaiServer.run`.

        Returns:
            The run's answer.
        """
        req = _run_request(input, subject, tags, attachments, store, groups)
        return await self._send(calls.run(agent, req))

    async def usage(
        self,
        *,
        from_: When = None,
        to: When = None,
        group_by: str | Sequence[str] | None = None,
        subject: str | None = None,
        tag: str | None = None,
        agent: str | None = None,
    ) -> UsageReport:
        """Usage of the application, with amounts; see :meth:`EvokaiServer.usage`.

        Returns:
            The usage report.
        """
        return await self._send(calls.usage(from_, to, group_by, subject, tag, agent))

    async def agents(self) -> builtins.list[AgentSummary]:
        """The application's active agents."""
        return await self._send(calls.agents())

    async def voices(self, language: str | None = None) -> builtins.list[VoiceInfo]:
        """The text-to-speech voices of the deployment."""
        return await self._send(calls.voices(language))

    def session(self, token: str | SessionToken, **options: Any) -> AsyncEvokaiSession:
        """A session client on the same service and HTTP client.

        Args:
            token: A session token or a minted ``SessionToken``.
            **options: ``replay_attempts``, ``replay_delay``.

        Returns:
            The session.
        """
        return AsyncEvokaiSession(
            self._transport.base_url, token, http_client=self._transport.client, **options
        )


class AsyncConversations:
    """A subject's conversations."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(
        self, subject: str, *, limit: int | None = None
    ) -> builtins.list[ConversationSummary]:
        """A subject's recent conversations, newest first."""
        return await self._s._send(calls.conversations_list(subject, limit))

    async def get(self, conversation_id: str, subject: str) -> ConversationDetail:
        """A subject's conversation with its messages and open confirmation."""
        return await self._s._send(calls.conversations_get(conversation_id, subject))

    async def delete(self, conversation_id: str, subject: str) -> None:
        """Delete a subject's conversation."""
        await self._s._send(calls.conversations_delete(conversation_id, subject))


class AsyncMemories:
    """What the agents remember about a subject."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self, subject: str) -> builtins.list[MemoryFact]:
        """The subject's memories."""
        return await self._s._send(calls.memories_list(subject))

    async def forget(self, subject: str, memory_id: str | None = None) -> int:
        """Forget one fact, or everything about the subject.

        Returns:
            How many facts were deleted.
        """
        return await self._s._send(calls.memories_forget(subject, memory_id))

    async def consent(self, subject: str, granted: bool) -> MemoryConsentResult:
        """Record the subject's answer to long-term memory, asked in your own interface.

        Args:
            subject: Your own opaque id of the end user.
            granted: True to allow memory; False deletes what was remembered (the latest answer
                counts).

        Returns:
            The subject's new memory state and how many memories were deleted.
        """
        return await self._s._send(calls.memories_consent(subject, granted))


class AsyncConnections:
    """A subject's connections (async); see :class:`Connections`."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self, subject: str) -> builtins.list[Connection]:
        """The subject's connections."""
        return await self._s._send(calls.connections_list(subject))

    async def disconnect(self, provider: str, subject: str) -> None:
        """Disconnect the subject from a provider (their tokens are deleted)."""
        await self._s._send(calls.connections_disconnect(provider, subject))


class AsyncPrivacy:
    """Data protection requests about one subject (the subject travels in the body)."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def export(self, subject: str) -> SubjectExport:
        """Everything Evokai keeps about the subject."""
        return await self._s._send(calls.privacy_export(subject))

    async def erase(self, subject: str) -> EraseResult:
        """Erase the subject (right to be forgotten); idempotent."""
        return await self._s._send(calls.privacy_erase(subject))

    async def revoke_sessions(self, subject: str) -> None:
        """Refuse every session token of the subject minted until now."""
        await self._s._send(calls.privacy_revoke_sessions(subject))


class AsyncAdmin:
    """The admin API (admin key)."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server
        self.application = AsyncAdminApplication(server)
        self.keys = AsyncAdminKeys(server)
        self.grants = AsyncAdminGrants(server)
        self.ask_rules = AsyncAdminAskRules(server)
        self.agents = AsyncAdminAgents(server)
        self.mcp_servers = AsyncAdminMcpServers(server)
        self.skills = AsyncAdminSkills(server)
        self.prompts = AsyncAdminPrompts(server)
        self.voices = AsyncAdminVoices(server)
        self.security = AsyncAdminSecurity(server)
        self.compliance = AsyncAdminCompliance(server)

    async def providers(self) -> dict[str, Any]:
        """The provider catalog: who processes data, where, under whose control (no prices)."""
        return await self._s._send(calls.providers())

    async def data_protection(self) -> dict[str, Any]:
        """The data protection profile and every external processor under it, with the reasons."""
        return await self._s._send(calls.data_protection())

    async def bug_reports(self) -> builtins.list[BugReport]:
        """Recent problem reports from sessions."""
        return await self._s._send(calls.bug_reports())

    async def activity(self, *, limit: int | None = None) -> builtins.list[ActivityItem]:
        """What changed lately: agent versions, keys and grants, newest first."""
        return await self._s._send(calls.activity(limit))

    async def usage_summary(
        self,
        *,
        from_: When = None,
        to: When = None,
        tag: str | None = None,
        agent: str | None = None,
    ) -> UsageSummary:
        """Turns, fallback answers, time to the first word, subjects and tags of a period."""
        return await self._s._send(calls.usage_summary(from_, to, tag, agent))

    async def export(self) -> ExportedConfig:
        """The application as a config file (secrets left out)."""
        return await self._s._send(calls.export_config())


class AsyncAdminApplication:
    """The application's settings."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def get(self) -> Application:
        """The application's settings."""
        return await self._s._send(calls.application_get())

    async def patch(self, patch: AppPatch | Obj | None = None, /, **fields: Any) -> None:
        """Change settings: only the fields given are sent (None clears a default).

        Args:
            patch: An ``AppPatch`` or a mapping.
            **fields: Or the fields as keyword arguments.
        """
        await self._s._send(calls.application_patch(patch if patch is not None else fields))


class AsyncAdminKeys:
    """The application's keys."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self) -> builtins.list[ApiKeyInfo]:
        """The keys (never their secrets)."""
        return await self._s._send(calls.keys_list())

    async def create(self, kind: Literal["srv", "adm"] = "srv", label: str = "") -> NewKey:
        """Create a key; the key is returned only now."""
        return await self._s._send(calls.keys_create(KeyRequest(kind=kind, label=label)))

    async def revoke(self, key_id: str) -> None:
        """Revoke a key (not the one making the request)."""
        await self._s._send(calls.keys_revoke(key_id))

    async def rotate(self, key_id: str) -> NewKey:
        """A successor with the same kind and label; the old key stays valid until revoked."""
        return await self._s._send(calls.keys_rotate(key_id))


class AsyncAdminAskRules:
    """Ask rules: tool calls that wait for the person's confirmation (only ever added)."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def get(self) -> AskRulesView:
        """The application's rules, the deployment's before them, each agent's after them."""
        return await self._s._send(calls.ask_rules_get())

    async def put(self, rules: Sequence[AskRule | Obj]) -> AskRulesView:
        """Replace the application's rules (400 ``ask_rules`` explains a refused one).

        Returns:
            The new view.
        """
        return await self._s._send(calls.ask_rules_put(rules))


class AsyncAdminGrants:
    """What the application may use."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def get(self) -> GrantView:
        """Every item with its default, override and value."""
        return await self._s._send(calls.grants_get())

    async def change(self, changes: Sequence[GrantChange | Obj]) -> GrantView:
        """Grant, revoke or reset (value None) items; effective from the next turn.

        Returns:
            The new view.
        """
        return await self._s._send(calls.grants_change(changes))


class AsyncAdminAgents:
    """Agent definitions and their versions."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self) -> builtins.list[AgentDef]:
        """The active agents with their definitions and versions."""
        return await self._s._send(calls.agents_list())

    async def versions(self, name: str) -> builtins.list[AgentVersion]:
        """Every version of an agent, newest first."""
        return await self._s._send(calls.agents_versions(name))

    async def put(self, agent: AgentDef | Obj) -> AgentPutResult:
        """Create or update an agent; a changed definition becomes a new active version."""
        return await self._s._send(calls.agents_put(agent))

    async def delete(self, name: str) -> None:
        """Retire an agent (pinned conversations keep their version)."""
        await self._s._send(calls.agents_delete(name))


class AsyncAdminMcpServers:
    """MCP servers and ready connectors."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self) -> builtins.list[McpServerInfo]:
        """The MCP servers (secrets never leave the service)."""
        return await self._s._send(calls.mcp_list())

    async def put(self, name: str, server: McpServerBody | Obj) -> None:
        """Register or replace an MCP server."""
        await self._s._send(calls.mcp_put(name, server))

    async def delete(self, name: str) -> None:
        """Remove an MCP server."""
        await self._s._send(calls.mcp_delete(name))

    async def test(self, name: str, credential: str | None = None) -> McpTestResult:
        """Connect and list its tools (``credential``: a user's token, never stored)."""
        return await self._s._send(calls.mcp_test(name, credential))

    async def connectors(self) -> builtins.list[ConnectorKind]:
        """The ready connectors with their configuration schemas."""
        return await self._s._send(calls.mcp_connectors())


class AsyncAdminSkills:
    """Skills (SKILL.md)."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self) -> builtins.list[SkillInfo]:
        """The skills without their bodies."""
        return await self._s._send(calls.skills_list())

    async def get(self, name: str) -> SkillDetail:
        """One skill with its body."""
        return await self._s._send(calls.skills_get(name))

    async def put(self, name: str, skill: SkillBody | Obj) -> None:
        """Create or replace a skill."""
        await self._s._send(calls.skills_put(name, skill))

    async def file(self, name: str, path: str) -> SkillFile:
        """One file of a skill with its text (``path`` like ``references/terms.md``)."""
        return await self._s._send(calls.skill_files_get(name, path))

    async def put_file(self, name: str, path: str, content: str) -> None:
        """Add or replace one file of a skill."""
        await self._s._send(calls.skill_files_put(name, path, content))

    async def delete_file(self, name: str, path: str) -> None:
        """Remove one file of a skill."""
        await self._s._send(calls.skill_files_delete(name, path))

    async def delete(self, name: str) -> None:
        """Remove a skill."""
        await self._s._send(calls.skills_delete(name))


class AsyncAdminPrompts:
    """Versioned prompts."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def list(self) -> builtins.list[PromptVersion]:
        """Every version of every prompt."""
        return await self._s._send(calls.prompts_list())

    async def put(self, name: str, text: str) -> int:
        """Save a prompt; a changed text becomes the next version.

        Returns:
            The version.
        """
        return await self._s._send(calls.prompts_put(name, text))


class AsyncAdminVoices:
    """The deployment's voice catalogue."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def put(self, provider: str, voice_id: str, voice: VoiceBody | Obj) -> None:
        """Add or replace a voice."""
        await self._s._send(calls.voices_put(provider, voice_id, voice))


class AsyncAdminSecurity:
    """The security layer: the policy, what applies, the events."""

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def get(self) -> SecurityView:
        """The policy, what applies, the controls and each agent's own policy."""
        return await self._s._send(calls.security_get())

    async def put(self, policy: SecurityPolicy | Obj) -> SecurityView:
        """Replace the policy (unset fields follow the defaults); effective from the next turn."""
        return await self._s._send(calls.security_put(policy))

    async def events(
        self,
        *,
        from_: When = None,
        to: When = None,
        control: str | None = None,
        rule: str | None = None,
        action: str | None = None,
        severity: str | None = None,
        agent: str | None = None,
        subject: str | None = None,
        before: When = None,
        limit: int | None = None,
    ) -> SecurityEventPage:
        """Security events, newest first; pass ``next`` back as ``before`` for the next page."""
        query = _security_query(
            from_=from_,
            to=to,
            control=control,
            rule=rule,
            action=action,
            severity=severity,
            agent=agent,
            subject=subject,
            before=before,
            limit=limit,
        )
        return await self._s._send(calls.security_events(query))

    async def summary(
        self, *, from_: When = None, to: When = None, agent: str | None = None
    ) -> SecuritySummary:
        """Counts of a period by rule, control, action and severity."""
        return await self._s._send(calls.security_summary(from_, to, agent))


class AsyncAdminCompliance:
    """Compliance packs (AI Act, GDPR, control mapping), the profile, snapshots and their diff.

    A pack is JSON (a :class:`CompliancePack`) or text with ``format="md"`` or ``"html"``;
    ``lang`` is ``en`` or ``it`` (default: the application's locale).
    """

    def __init__(self, server: AsyncEvokaiServer) -> None:
        self._s = server

    async def profile(self) -> ComplianceProfile:
        """The application's compliance profile."""
        return await self._s._send(calls.compliance_profile_get())

    async def put_profile(self, profile: ComplianceProfile | Obj) -> ComplianceProfile:
        """Replace the profile (owner); text with personal data or secrets is refused (400)."""
        return await self._s._send(calls.compliance_profile_put(profile))

    async def pack(
        self,
        pack: Literal["ai-act", "gdpr", "controls"],
        *,
        format: Literal["json", "md", "html"] = "json",
        lang: Literal["en", "it"] | None = None,
    ) -> CompliancePack | str:
        """A pack generated now: JSON, or Markdown or HTML text."""
        return await self._s._send(calls.compliance_pack(pack, format, lang))

    async def snapshots(self, *, limit: int | None = None) -> builtins.list[ComplianceSnapshot]:
        """The stored snapshots, newest first."""
        return await self._s._send(calls.compliance_snapshots(limit))

    async def snapshot(self, label: str = "") -> ComplianceSnapshot:
        """Record what is true now (operator)."""
        return await self._s._send(calls.compliance_snapshot_create(label))

    async def snapshot_pack(
        self,
        snapshot_id: str,
        pack: Literal["ai-act", "gdpr", "controls"],
        *,
        format: Literal["json", "md", "html"] = "json",
        lang: Literal["en", "it"] | None = None,
    ) -> CompliancePack | str:
        """A pack as it was when a snapshot was taken."""
        return await self._s._send(calls.compliance_snapshot_pack(snapshot_id, pack, format, lang))

    async def diff(
        self, from_id: str, to_id: str, *, lang: Literal["en", "it"] | None = None
    ) -> dict[str, Any]:
        """What changed between two snapshots: statements per pack and facts."""
        return await self._s._send(calls.compliance_diff(from_id, to_id, lang))
