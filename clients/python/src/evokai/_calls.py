"""Every API call, built once for the sync and the async clients."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from evokai._http import Call, seg
from evokai.events import ChatResult, ConversationDetail, Event, SessionConversation, parse_events
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
    AskRulesBody,
    AskRulesView,
    BugReport,
    CompliancePack,
    ComplianceProfile,
    ComplianceSnapshot,
    Connection,
    ConnectionStart,
    ConnectionStarted,
    ConnectorKind,
    ConversationSummary,
    EraseResult,
    ExportedConfig,
    GrantChange,
    GrantChanges,
    GrantView,
    KeyRequest,
    McpServerBody,
    McpServerInfo,
    McpTestResult,
    Memory,
    MemoryConsentResult,
    MemoryFact,
    MemoryList,
    NewKey,
    PreviewBody,
    PromptBody,
    PromptVersion,
    RunRequest,
    RunResult,
    SecurityEventPage,
    SecurityPolicy,
    SecuritySummary,
    SecurityView,
    SessionInfo,
    SessionToken,
    SkillBody,
    SkillDetail,
    SkillFile,
    SkillInfo,
    SnapshotBody,
    SubjectExport,
    SubjectRequest,
    TokenRequest,
    UsageReport,
    UsageSummary,
    VoiceBody,
    VoiceInfo,
)

M = TypeVar("M", bound=BaseModel)
When = datetime | str | None


def body(model: type[M], value: M | Mapping[str, Any]) -> dict[str, Any]:
    """A request body: only the fields that were set, with the service's names.

    Args:
        model: The request model.
        value: An instance, or a mapping of attribute or wire names.

    Returns:
        The JSON body.
    """
    instance = value if isinstance(value, model) else model.model_validate(value)
    return instance.model_dump(mode="json", by_alias=True, exclude_unset=True)


def one(model: type[M]) -> Callable[[Any], M]:
    """Parse an object into a model.

    Returns:
        The parser.
    """
    return model.model_validate


def items(model: type[M]) -> Callable[[Any], list[M]]:
    """Parse ``{"items": [...]}`` into a list of models.

    Returns:
        The parser.
    """

    def parse(data: Any) -> list[M]:
        return [model.model_validate(x) for x in (data or {}).get("items", [])]

    return parse


def nothing(_: Any) -> None:
    """Ignore an answer (``{"ok": true}`` or 204)."""


# Server API (server key)


def mint(req: TokenRequest) -> Call[SessionToken]:
    return Call("POST", "/v1/session-tokens", one(SessionToken), body=body(TokenRequest, req))


def run(agent: str, req: RunRequest) -> Call[RunResult]:
    return Call("POST", f"/v1/agents/{seg(agent)}/run", one(RunResult), body=body(RunRequest, req))


def conversations_list(subject: str, limit: int | None) -> Call[list[ConversationSummary]]:
    return Call(
        "GET",
        "/v1/conversations",
        items(ConversationSummary),
        params={"subject": subject, "limit": limit},
    )


def conversations_get(conversation_id: str, subject: str) -> Call[ConversationDetail]:
    return Call(
        "GET",
        f"/v1/conversations/{seg(conversation_id)}",
        one(ConversationDetail),
        params={"subject": subject},
    )


def conversations_delete(conversation_id: str, subject: str) -> Call[None]:
    return Call(
        "DELETE",
        f"/v1/conversations/{seg(conversation_id)}",
        nothing,
        params={"subject": subject},
    )


def _group_by(value: str | Sequence[str] | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return ",".join(value)


def usage(
    from_: When,
    to: When,
    group_by: str | Sequence[str] | None,
    subject: str | None,
    tag: str | None,
    agent: str | None,
) -> Call[UsageReport]:
    return Call(
        "GET",
        "/v1/usage",
        one(UsageReport),
        params={
            "from": from_,
            "to": to,
            "groupBy": _group_by(group_by),
            "subject": subject,
            "tag": tag,
            "agent": agent,
        },
    )


def memories_list(subject: str) -> Call[list[MemoryFact]]:
    return Call("GET", "/v1/memories", items(MemoryFact), params={"subject": subject})


def memories_forget(subject: str, memory_id: str | None) -> Call[int]:
    return Call(
        "DELETE",
        "/v1/memories",
        lambda d: int((d or {}).get("deleted", 0)),
        params={"subject": subject, "id": memory_id},
    )


def _connections(d: Any) -> list[Connection]:
    return [Connection.model_validate(x) for x in (d or {}).get("connections", [])]


def connections_list(subject: str) -> Call[list[Connection]]:
    return Call("GET", "/v1/connections", _connections, params={"subject": subject})


def connections_disconnect(provider: str, subject: str) -> Call[None]:
    return Call(
        "DELETE",
        f"/v1/connections/{seg(provider)}",
        lambda _: None,
        params={"subject": subject},
    )


def memories_consent(subject: str, granted: bool) -> Call[MemoryConsentResult]:
    return Call(
        "POST",
        "/v1/memories/consent",
        one(MemoryConsentResult),
        body={"subject": subject, "granted": granted},
    )


def agents() -> Call[list[AgentSummary]]:
    return Call("GET", "/v1/agents", items(AgentSummary))


def voices(language: str | None) -> Call[list[VoiceInfo]]:
    return Call("GET", "/v1/voices", items(VoiceInfo), params={"language": language})


def privacy_export(subject: str) -> Call[SubjectExport]:
    return Call(
        "POST",
        "/v1/privacy/export",
        one(SubjectExport),
        body=body(SubjectRequest, {"subject": subject}),
    )


def privacy_erase(subject: str) -> Call[EraseResult]:
    return Call(
        "POST",
        "/v1/privacy/erase",
        one(EraseResult),
        body=body(SubjectRequest, {"subject": subject}),
    )


def privacy_revoke_sessions(subject: str) -> Call[None]:
    return Call(
        "POST",
        "/v1/privacy/revoke-sessions",
        nothing,
        body=body(SubjectRequest, {"subject": subject}),
    )


# Admin API (admin key)


def _admin(method: str, path: str, parse: Callable[[Any], Any], **kw: Any) -> Call[Any]:
    return Call(method, f"/v1/admin{path}", parse, admin=True, **kw)


def application_get() -> Call[Application]:
    return _admin("GET", "/application", one(Application))


def application_patch(patch: AppPatch | Mapping[str, Any]) -> Call[None]:
    return _admin("PATCH", "/application", nothing, body=body(AppPatch, patch))


def keys_list() -> Call[list[ApiKeyInfo]]:
    return _admin("GET", "/keys", items(ApiKeyInfo))


def keys_create(req: KeyRequest) -> Call[NewKey]:
    return _admin("POST", "/keys", one(NewKey), body=body(KeyRequest, req))


def keys_revoke(key_id: str) -> Call[None]:
    return _admin("DELETE", f"/keys/{seg(key_id)}", nothing)


def keys_rotate(key_id: str) -> Call[NewKey]:
    return _admin("POST", f"/keys/{seg(key_id)}/rotate", one(NewKey))


def ask_rules_get() -> Call[AskRulesView]:
    return _admin("GET", "/ask-rules", one(AskRulesView))


def ask_rules_put(rules: Sequence[AskRule | Mapping[str, Any]]) -> Call[AskRulesView]:
    return _admin(
        "PUT",
        "/ask-rules",
        one(AskRulesView),
        body=body(AskRulesBody, {"rules": [body(AskRule, r) for r in rules]}),
    )


def grants_get() -> Call[GrantView]:
    return _admin("GET", "/grants", one(GrantView))


def grants_change(changes: Sequence[GrantChange | Mapping[str, Any]]) -> Call[GrantView]:
    return _admin(
        "PATCH",
        "/grants",
        one(GrantView),
        body=body(GrantChanges, {"changes": [body(GrantChange, c) for c in changes]}),
    )


def agents_list() -> Call[list[AgentDef]]:
    return _admin("GET", "/agents", items(AgentDef))


def agents_versions(name: str) -> Call[list[AgentVersion]]:
    return _admin("GET", f"/agents/{seg(name)}/versions", items(AgentVersion))


def agents_put(agent: AgentDef | Mapping[str, Any]) -> Call[AgentPutResult]:
    data = body(AgentDef, agent)
    return _admin("PUT", f"/agents/{seg(data['name'])}", one(AgentPutResult), body=data)


def agents_delete(name: str) -> Call[None]:
    return _admin("DELETE", f"/agents/{seg(name)}", nothing)


def mcp_list() -> Call[list[McpServerInfo]]:
    return _admin("GET", "/mcp-servers", items(McpServerInfo))


def mcp_put(name: str, server: McpServerBody | Mapping[str, Any]) -> Call[None]:
    return _admin("PUT", f"/mcp-servers/{seg(name)}", nothing, body=body(McpServerBody, server))


def mcp_delete(name: str) -> Call[None]:
    return _admin("DELETE", f"/mcp-servers/{seg(name)}", nothing)


def mcp_test(name: str, credential: str | None) -> Call[McpTestResult]:
    return _admin(
        "POST",
        f"/mcp-servers/{seg(name)}/test",
        one(McpTestResult),
        body={"credential": credential} if credential else {},
    )


def mcp_connectors() -> Call[list[ConnectorKind]]:
    return _admin("GET", "/connectors", items(ConnectorKind))


def skills_list() -> Call[list[SkillInfo]]:
    return _admin("GET", "/skills", items(SkillInfo))


def skills_get(name: str) -> Call[SkillDetail]:
    return _admin("GET", f"/skills/{seg(name)}", one(SkillDetail))


def skills_put(name: str, skill: SkillBody | Mapping[str, Any]) -> Call[None]:
    return _admin("PUT", f"/skills/{seg(name)}", nothing, body=body(SkillBody, skill))


def skills_delete(name: str) -> Call[None]:
    return _admin("DELETE", f"/skills/{seg(name)}", nothing)


def _file_path(path: str) -> str:
    return "/".join(seg(p) for p in path.split("/"))


def skill_files_get(name: str, path: str) -> Call[SkillFile]:
    return _admin("GET", f"/skills/{seg(name)}/files/{_file_path(path)}", one(SkillFile))


def skill_files_put(name: str, path: str, content: str) -> Call[None]:
    return _admin(
        "PUT",
        f"/skills/{seg(name)}/files/{_file_path(path)}",
        nothing,
        body={"content": content},
    )


def skill_files_delete(name: str, path: str) -> Call[None]:
    return _admin("DELETE", f"/skills/{seg(name)}/files/{_file_path(path)}", nothing)


def prompts_list() -> Call[list[PromptVersion]]:
    return _admin("GET", "/prompts", items(PromptVersion))


def prompts_put(name: str, text: str, fields: Mapping[str, Any] | None = None) -> Call[int]:
    return _admin(
        "PUT",
        f"/prompts/{seg(name)}",
        lambda d: int((d or {})["version"]),
        body=body(PromptBody, {"text": text, **(fields or {})}),
    )


def prompts_preview(name: str, fields: Mapping[str, Any]) -> Call[str]:
    return _admin(
        "POST",
        f"/prompts/{seg(name)}/preview",
        lambda d: str((d or {})["text"]),
        body=body(PreviewBody, fields),
    )


def voices_put(provider: str, voice_id: str, voice: VoiceBody | Mapping[str, Any]) -> Call[None]:
    return _admin(
        "PUT", f"/voices/{seg(provider)}/{seg(voice_id)}", nothing, body=body(VoiceBody, voice)
    )


def bug_reports() -> Call[list[BugReport]]:
    return _admin("GET", "/bug-reports", items(BugReport))


def providers() -> Call[dict[str, Any]]:
    return _admin("GET", "/providers", dict)


def data_protection() -> Call[dict[str, Any]]:
    return _admin("GET", "/data-protection", dict)


def security_get() -> Call[SecurityView]:
    return _admin("GET", "/security", one(SecurityView))


def security_put(policy: SecurityPolicy | Mapping[str, Any]) -> Call[SecurityView]:
    return _admin("PUT", "/security", one(SecurityView), body=body(SecurityPolicy, policy))


def security_events(params: Mapping[str, Any]) -> Call[SecurityEventPage]:
    query = {("from" if k == "from_" else k): v for k, v in params.items()}
    return _admin("GET", "/security/events", one(SecurityEventPage), params=query)


def security_summary(from_: When, to: When, agent: str | None) -> Call[SecuritySummary]:
    return _admin(
        "GET",
        "/security/summary",
        one(SecuritySummary),
        params={"from": from_, "to": to, "agent": agent},
    )


def activity(limit: int | None) -> Call[list[ActivityItem]]:
    return _admin("GET", "/activity", items(ActivityItem), params={"limit": limit})


def usage_summary(from_: When, to: When, tag: str | None, agent: str | None) -> Call[UsageSummary]:
    return _admin(
        "GET",
        "/usage/summary",
        one(UsageSummary),
        params={"from": from_, "to": to, "tag": tag, "agent": agent},
    )


def export_config() -> Call[ExportedConfig]:
    return _admin("GET", "/export", one(ExportedConfig))


def compliance_profile_get() -> Call[ComplianceProfile]:
    return _admin(
        "GET", "/compliance/profile", lambda d: ComplianceProfile.model_validate(d["profile"])
    )


def compliance_profile_put(
    profile: ComplianceProfile | Mapping[str, Any],
) -> Call[ComplianceProfile]:
    return _admin(
        "PUT",
        "/compliance/profile",
        lambda d: ComplianceProfile.model_validate(d["profile"]),
        body=body(ComplianceProfile, profile),
    )


def _pack_call(path: str, fmt: str, lang: str | None) -> Call[Any]:
    params = {"format": fmt, "lang": lang}
    if fmt == "json":
        return _admin("GET", path, one(CompliancePack), params=params)
    return _admin("GET", path, lambda text: text, params=params, raw=True)


def compliance_pack(pack: str, fmt: str, lang: str | None) -> Call[Any]:
    return _pack_call(f"/compliance/{seg(pack)}", fmt, lang)


def compliance_snapshots(limit: int | None) -> Call[list[ComplianceSnapshot]]:
    return _admin(
        "GET", "/compliance/snapshots", items(ComplianceSnapshot), params={"limit": limit}
    )


def compliance_snapshot_create(label: str) -> Call[ComplianceSnapshot]:
    return _admin(
        "POST",
        "/compliance/snapshots",
        one(ComplianceSnapshot),
        body=body(SnapshotBody, {"label": label}),
    )


def compliance_snapshot_pack(snapshot_id: str, pack: str, fmt: str, lang: str | None) -> Call[Any]:
    return _pack_call(f"/compliance/snapshots/{seg(snapshot_id)}/{seg(pack)}", fmt, lang)


def compliance_diff(from_id: str, to_id: str, lang: str | None) -> Call[dict[str, Any]]:
    return _admin(
        "GET",
        "/compliance/snapshots/diff",
        lambda d: dict(d),
        params={"from": from_id, "to": to_id, "lang": lang},
    )


# Session API (session token)


def session_info() -> Call[SessionInfo]:
    return Call("GET", "/v1/session", one(SessionInfo))


def session_conversation() -> Call[SessionConversation]:
    return Call("GET", "/v1/session/conversation", one(SessionConversation))


def session_events(after: int) -> Call[list[Event]]:
    return Call(
        "GET",
        "/v1/session/events",
        lambda d: parse_events((d or {}).get("events", [])),
        params={"after": after},
    )


def session_cancel(turn_id: str) -> Call[bool]:
    return Call(
        "POST",
        f"/v1/session/turns/{seg(turn_id)}/cancel",
        lambda d: bool((d or {}).get("cancelled", False)),
    )


def session_feedback(message_id: str, value: int | None) -> Call[None]:
    return Call(
        "POST",
        f"/v1/session/messages/{seg(message_id)}/feedback",
        nothing,
        body={"value": value},
    )


def session_tool_result(call_id: str, result: Any, error: str | None) -> Call[None]:
    body: dict[str, Any] = {"callId": call_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return Call("POST", "/v1/session/tool-results", nothing, body=body)


def session_exit(code: str) -> Call[None]:
    return Call("POST", "/v1/session/exit", nothing, body={"code": code})


def session_bug_report(description: str, context: Mapping[str, Any] | None) -> Call[str]:
    return Call(
        "POST",
        "/v1/session/bug-reports",
        lambda d: str((d or {})["id"]),
        body={"description": description, "context": dict(context or {})},
    )


def session_connections() -> Call[list[Connection]]:
    return Call("GET", "/v1/session/connections", _connections)


def session_connection_start(provider: str, return_to: str | None) -> Call[ConnectionStarted]:
    return Call(
        "POST",
        f"/v1/session/connections/{seg(provider)}/start",
        one(ConnectionStarted),
        body=ConnectionStart(return_to=return_to).model_dump(by_alias=True, exclude_none=True),
    )


def session_disconnect(provider: str) -> Call[None]:
    return Call("DELETE", f"/v1/session/connections/{seg(provider)}", lambda _: None)


def session_memories() -> Call[MemoryList]:
    return Call("GET", "/v1/session/memories", one(MemoryList))


def session_memory_consent(granted: bool) -> Call[MemoryConsentResult]:
    return Call(
        "POST",
        "/v1/session/memory-consent",
        one(MemoryConsentResult),
        body={"granted": granted},
    )


def session_memory_edit(memory_id: str, text: str) -> Call[Memory]:
    return Call(
        "PATCH",
        f"/v1/session/memories/{seg(memory_id)}",
        one(Memory),
        body={"text": text},
    )


def session_memory_forget(memory_id: str | None) -> Call[int]:
    path = f"/v1/session/memories/{seg(memory_id)}" if memory_id else "/v1/session/memories"
    return Call("DELETE", path, lambda d: int((d or {}).get("deleted", 0)))


def session_chat_json(chat_body: dict[str, Any]) -> Call[ChatResult]:
    return Call("POST", "/v1/session/chat", one(ChatResult), body=chat_body)
