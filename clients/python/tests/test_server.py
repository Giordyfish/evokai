"""Every server and admin method: the request it sends and how it reads the answer.

Each case runs on both the sync and the async client.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
import respx

from evokai import (
    AgentDef,
    AsyncEvokaiServer,
    ConversationDetail,
    EvokaiServer,
    SessionToken,
    TextEvent,
)
from evokai.models import (
    ActivityItem,
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
    ConnectorKind,
    ConversationSummary,
    EraseResult,
    ExportedConfig,
    GrantChange,
    GrantView,
    McpServerBody,
    McpServerInfo,
    McpTestResult,
    MemoryConsentResult,
    MemoryFact,
    NewKey,
    PromptVersion,
    RunAttachment,
    RunMessage,
    RunResult,
    SecurityEventPage,
    SecurityPolicy,
    SecuritySummary,
    SecurityView,
    SkillDetail,
    SkillFile,
    SkillInfo,
    SubjectExport,
    UsageReport,
    UsageSummary,
    VoiceInfo,
)
from tests.support import ADMIN_KEY, BASE, SERVER_KEY

SHARES = {"dayPct": 10.0, "monthPct": None, "companyPct": 2.5, "monthOut": False}


@dataclass
class Case:
    id: str
    call: Callable[[Any], Any]
    method: str
    path: str
    response: Any = field(default_factory=lambda: {"ok": True})
    params: dict[str, str] | None = None
    body: Any = None
    admin: bool = False
    check: Callable[[Any], object] = lambda _: None
    status: int = 200


def _token(r: SessionToken) -> None:
    assert isinstance(r, SessionToken)
    assert r.token == "tok"
    assert r.conversation_id == "c1"
    assert r.usage.day_pct == pytest.approx(10.0)
    assert r.suggestions == ["Hi?"]


def _token_memory(r: SessionToken) -> None:
    _token(r)
    assert r.memory is not None
    assert (r.memory.mode, r.memory.consent, r.memory.active, r.memory.ask) == (
        "ask",
        "granted",
        True,
        False,
    )


def _memories(r: list[MemoryFact]) -> None:
    assert [type(x) for x in r] == [MemoryFact, MemoryFact]
    assert r[0].fact == "Prefers mornings"
    assert r[0].kind is None
    assert r[1].kind == "preference"
    assert r[1].source == "person"
    assert r[1].text == "Short answers"
    assert r[1].updated_at == datetime(2026, 11, 10, 8, tzinfo=timezone.utc)
    assert r[1].uses == 3


def _consent(r: MemoryConsentResult) -> None:
    assert isinstance(r, MemoryConsentResult)
    assert r.memory.consent == "declined"
    assert r.memory.active is False
    assert r.deleted == 2


def _run(r: RunResult) -> None:
    assert isinstance(r, RunResult)
    assert r.output == {"label": "spam"}
    assert r.usage.cost_usd == pytest.approx(0.0012)
    assert r.usage.cache_read_tokens == 0
    assert r.conversation_id is None


def _conversation(r: ConversationDetail) -> None:
    assert isinstance(r, ConversationDetail)
    assert isinstance(r.messages[0].events[0], TextEvent)
    assert r.pending is not None
    assert r.pending.items == ["Delete"]


def _usage(r: UsageReport) -> None:
    assert isinstance(r, UsageReport)
    assert r.rows[0].cost_usd == pytest.approx(1.5)
    assert r.rows[0].tags == ["team:ops"]
    assert r.application.monthly_cap_usd == 100


def _application(r: Application) -> None:
    assert isinstance(r, Application)
    assert r.has_eval_credential is True
    assert r.message_defaults == {"limit.app": "Over."}
    assert r.storage_mode == "external"


def _type(cls: type) -> Callable[[Any], None]:
    def check(r: Any) -> None:
        if isinstance(r, list):
            assert r
            assert all(isinstance(x, cls) for x in r)
        else:
            assert isinstance(r, cls)

    return check


def _eq(value: Any) -> Callable[[Any], None]:
    def check(r: Any) -> None:
        assert r == value

    return check


AGENT = {"name": "triage", "model": "bedrock:eu.anthropic.claude", "channels": ["server"]}

CASES = [
    # Server API
    Case(
        "mint",
        lambda c: c.mint_session_token(
            "user-1",
            "assistant",
            conversation_id="c1",
            tags=["team:ops"],
            ttl_seconds=600,
            forward_credential="user-jwt",
            limits={"subject_daily_usd": 1.5},
            model="bedrock:eu.anthropic.claude",
            voice="Bianca",
            voice_engine="server",
            storage_mode="external",
            consent={"version": "v2", "accepted_at": datetime(2026, 11, 1, tzinfo=timezone.utc)},
            exit_code="1234",
        ),
        "POST",
        "/v1/session-tokens",
        {
            "token": "tok",
            "expiresAt": 1_900_000_000,
            "conversationId": "c1",
            "voiceEngine": "server",
            "storageMode": "external",
            "usage": SHARES,
            "suggestions": ["Hi?"],
        },
        body={
            "subject": "user-1",
            "agent": "assistant",
            "conversationId": "c1",
            "tags": ["team:ops"],
            "ttlSeconds": 600,
            "forwardCredential": "user-jwt",
            "limits": {"subject_daily_usd": 1.5},
            "model": "bedrock:eu.anthropic.claude",
            "voice": "Bianca",
            "voiceEngine": "server",
            "storageMode": "external",
            "consent": {"version": "v2", "acceptedAt": "2026-11-01T00:00:00Z"},
            "exitCode": "1234",
        },
        check=_token,
    ),
    Case(
        "mint-minimal",
        lambda c: c.mint_session_token("user-1", "assistant"),
        "POST",
        "/v1/session-tokens",
        {
            "token": "tok",
            "expiresAt": 1,
            "conversationId": "c1",
            "usage": SHARES,
            "suggestions": ["Hi?"],
        },
        body={"subject": "user-1", "agent": "assistant"},
        check=_token,
    ),
    Case(
        "mint-modules",
        lambda c: c.mint_session_token(
            "user-1",
            "assistant",
            modules={
                "tone": {"params": {"style": "detailed", "shelf_name": "New arrivals"}},
                "reader_level": {"on": False},
            },
            locale="it-IT",
        ),
        "POST",
        "/v1/session-tokens",
        {
            "token": "tok",
            "expiresAt": 1,
            "conversationId": "c1",
            "usage": SHARES,
            "suggestions": ["Hi?"],
        },
        body={
            "subject": "user-1",
            "agent": "assistant",
            "modules": {
                "tone": {"params": {"style": "detailed", "shelf_name": "New arrivals"}},
                "reader_level": {"on": False},
            },
            "locale": "it-IT",
        },
        check=_token,
    ),
    Case(
        "mint-memory",
        lambda c: c.mint_session_token("user-1", "assistant", memory_consent=True),
        "POST",
        "/v1/session-tokens",
        {
            "token": "tok",
            "expiresAt": 1,
            "conversationId": "c1",
            "usage": SHARES,
            "suggestions": ["Hi?"],
            "memory": {"mode": "ask", "consent": "granted", "active": True, "ask": False},
        },
        body={"subject": "user-1", "agent": "assistant", "memoryConsent": True},
        check=_token_memory,
    ),
    Case(
        "run-text",
        lambda c: c.run("mail triage", "Classify this"),
        "POST",
        "/v1/agents/mail%20triage/run",
        {
            "runId": "r1",
            "output": {"label": "spam"},
            "model": "m",
            "usage": {"inputTokens": 10, "outputTokens": 2, "costUsd": 0.0012, "toolCalls": 0},
            "conversationId": None,
        },
        body={"input": "Classify this"},
        check=_run,
    ),
    Case(
        "run-full",
        lambda c: c.run(
            "triage",
            [RunMessage(role="user", text="a"), {"role": "assistant", "text": "b"}],
            subject="customer-9",
            tags=["mail"],
            attachments=[RunAttachment(name="mail.txt", text="body"), {"name": "x", "text": "y"}],
            store=True,
        ),
        "POST",
        "/v1/agents/triage/run",
        {
            "runId": "r1",
            "output": {"label": "spam"},
            "model": "m",
            "usage": {"inputTokens": 1, "outputTokens": 1, "costUsd": 0.0012, "toolCalls": 1},
        },
        body={
            "input": [{"role": "user", "text": "a"}, {"role": "assistant", "text": "b"}],
            "subject": "customer-9",
            "tags": ["mail"],
            "attachments": [{"name": "mail.txt", "text": "body"}, {"name": "x", "text": "y"}],
            "store": True,
        },
        check=_run,
    ),
    Case(
        "conversations.list",
        lambda c: c.conversations.list("user/1", limit=5),
        "GET",
        "/v1/conversations",
        {"items": [{"id": "c1", "title": "T", "agent": "a", "updatedAt": "2026-11-01T00:00:00"}]},
        params={"subject": "user/1", "limit": "5"},
        check=_type(ConversationSummary),
    ),
    Case(
        "conversations.get",
        lambda c: c.conversations.get("c/1", "user-1"),
        "GET",
        "/v1/conversations/c%2F1",
        {
            "id": "c/1",
            "title": "T",
            "agent": "a",
            "messages": [
                {
                    "id": "m1",
                    "role": "assistant",
                    "text": "x",
                    "events": [{"type": "text", "delta": "x"}],
                    "feedback": None,
                }
            ],
            "pending": {"actionId": "a1", "items": ["Delete"]},
        },
        params={"subject": "user-1"},
        check=_conversation,
    ),
    Case(
        "conversations.delete",
        lambda c: c.conversations.delete("c1", "user-1"),
        "DELETE",
        "/v1/conversations/c1",
        params={"subject": "user-1"},
        check=_eq(None),
    ),
    Case(
        "usage",
        lambda c: c.usage(
            from_=datetime(2026, 11, 1, tzinfo=timezone.utc),
            to="2026-11-15T00:00:00Z",
            group_by=["subject", "tags"],
            subject="u",
            tag="team:ops",
            agent="a",
        ),
        "GET",
        "/v1/usage",
        {
            "rows": [{"subject": "u", "tags": ["team:ops"], "costUsd": 1.5, "inputTokens": 3}],
            "application": {
                "monthlyCapUsd": 100,
                "dailyCapUsd": None,
                "monthUsd": 3,
                "dayUsd": 1,
            },
        },
        params={
            "from": "2026-11-01T00:00:00+00:00",
            "to": "2026-11-15T00:00:00Z",
            "groupBy": "subject,tags",
            "subject": "u",
            "tag": "team:ops",
            "agent": "a",
        },
        check=_usage,
    ),
    Case(
        "usage-default",
        lambda c: c.usage(),
        "GET",
        "/v1/usage",
        {
            "rows": [{"subject": "u", "tags": ["team:ops"], "costUsd": 1.5}],
            "application": {"monthlyCapUsd": 100},
        },
        params={},
        check=_usage,
    ),
    Case(
        "memories.list",
        lambda c: c.memories.list("u"),
        "GET",
        "/v1/memories",
        {
            "items": [
                {"id": "f1", "fact": "Prefers mornings"},
                {
                    "id": "f2",
                    "fact": "Short answers",
                    "text": "Short answers",
                    "kind": "preference",
                    "agent": "helper",
                    "source": "person",
                    "createdAt": "2026-11-01T08:00:00Z",
                    "updatedAt": "2026-11-10T08:00:00Z",
                    "uses": 3,
                },
            ],
            "memory": {"mode": "on", "consent": "unknown", "active": True, "ask": False},
        },
        params={"subject": "u"},
        check=_memories,
    ),
    Case(
        "memories.consent",
        lambda c: c.memories.consent("u", False),
        "POST",
        "/v1/memories/consent",
        {
            "memory": {"mode": "on", "consent": "declined", "active": False, "ask": False},
            "deleted": 2,
        },
        body={"subject": "u", "granted": False},
        check=_consent,
    ),
    Case(
        "memories.forget-one",
        lambda c: c.memories.forget("u", "f1"),
        "DELETE",
        "/v1/memories",
        {"deleted": 1},
        params={"subject": "u", "id": "f1"},
        check=_eq(1),
    ),
    Case(
        "memories.forget-all",
        lambda c: c.memories.forget("u"),
        "DELETE",
        "/v1/memories",
        {"deleted": 4},
        params={"subject": "u"},
        check=_eq(4),
    ),
    Case(
        "agents",
        lambda c: c.agents(),
        "GET",
        "/v1/agents",
        {"items": [{"name": "a", "version": "v1", "title": "A"}]},
        check=_type(AgentSummary),
    ),
    Case(
        "voices",
        lambda c: c.voices(language="it-IT"),
        "GET",
        "/v1/voices",
        {
            "items": [
                {
                    "provider": "polly",
                    "voiceId": "Bianca",
                    "language": "it-IT",
                    "name": "Bianca",
                    "gender": "female",
                    "region": None,
                }
            ]
        },
        params={"language": "it-IT"},
        check=_type(VoiceInfo),
    ),
    Case(
        "privacy.export",
        lambda c: c.privacy.export("u"),
        "POST",
        "/v1/privacy/export",
        {"subject": "u", "exportedAt": "2026-11-01", "memories": [{"id": "f", "fact": "x"}]},
        body={"subject": "u"},
        check=_type(SubjectExport),
    ),
    Case(
        "privacy.erase",
        lambda c: c.privacy.erase("u"),
        "POST",
        "/v1/privacy/erase",
        {
            "ok": True,
            "conversations": 2,
            "memories": 1,
            "bugReports": 0,
            "usageDeidentified": 5,
            "securityEventsDeidentified": 1,
        },
        body={"subject": "u"},
        check=lambda r: (
            _type(EraseResult)(r),
            _eq((2, 5))((r.conversations, r.usage_deidentified)),
        ),
    ),
    Case(
        "privacy.revoke_sessions",
        lambda c: c.privacy.revoke_sessions("u"),
        "POST",
        "/v1/privacy/revoke-sessions",
        body={"subject": "u"},
        check=_eq(None),
    ),
    # Admin API
    Case(
        "admin.application.get",
        lambda c: c.admin.application.get(),
        "GET",
        "/v1/admin/application",
        {
            "id": "app1",
            "slug": "bookshop",
            "name": "Inkwell Books",
            "monthly_cap_usd": 100,
            "storage_mode": "external",
            "hasEvalCredential": True,
            "messageDefaults": {"limit.app": "Over."},
        },
        admin=True,
        check=_application,
    ),
    Case(
        "admin.application.patch-model",
        lambda c: c.admin.application.patch(AppPatch(daily_cap_usd=None, locale="it")),
        "PATCH",
        "/v1/admin/application",
        body={"daily_cap_usd": None, "locale": "it"},
        admin=True,
        check=_eq(None),
    ),
    Case(
        "admin.application.patch-kwargs",
        lambda c: c.admin.application.patch(allowed_origins=["https://inkwell-books.example"]),
        "PATCH",
        "/v1/admin/application",
        body={"allowed_origins": ["https://inkwell-books.example"]},
        admin=True,
    ),
    Case(
        "admin.keys.list",
        lambda c: c.admin.keys.list(),
        "GET",
        "/v1/admin/keys",
        {"items": [{"id": "k1", "kind": "srv", "prefix": "abc", "label": "", "lastUsedAt": None}]},
        admin=True,
        check=_type(ApiKeyInfo),
    ),
    Case(
        "admin.keys.create",
        lambda c: c.admin.keys.create("adm", "ci"),
        "POST",
        "/v1/admin/keys",
        {"id": "k2", "key": "evk_adm_x_y"},
        body={"kind": "adm", "label": "ci"},
        admin=True,
        check=_type(NewKey),
    ),
    Case(
        "admin.keys.revoke",
        lambda c: c.admin.keys.revoke("k1"),
        "DELETE",
        "/v1/admin/keys/k1",
        admin=True,
    ),
    Case(
        "admin.keys.rotate",
        lambda c: c.admin.keys.rotate("k1"),
        "POST",
        "/v1/admin/keys/k1/rotate",
        {"id": "k3", "key": "evk_srv_x_y", "replaces": "k1"},
        admin=True,
        check=lambda r: _eq("k1")(r.replaces),
    ),
    Case(
        "admin.grants.get",
        lambda c: c.admin.grants.get(),
        "GET",
        "/v1/admin/grants",
        {
            "groups": [
                {
                    "id": "mcp",
                    "items": [
                        {
                            "kind": "mcp",
                            "name": "crm",
                            "value": True,
                            "default": True,
                            "override": None,
                            "children": [{"kind": "tool", "name": "crm/search", "value": True}],
                        }
                    ],
                }
            ],
            "summary": {"serverRuns": True},
            "overrides": 0,
            "modelAllow": ["bedrock:*"],
            "applicationModels": [],
            "storageMode": "evokai",
        },
        admin=True,
        check=lambda r: _eq("crm/search")(r.groups[0].items[0].children[0].name),
    ),
    Case(
        "admin.grants.change",
        lambda c: c.admin.grants.change([
            GrantChange(kind="model", name="openai:gpt", value="off"),
            {"kind": "tool", "name": "crm/delete", "value": None},
        ]),
        "PATCH",
        "/v1/admin/grants",
        {"groups": [], "summary": {}, "overrides": 2},
        body={
            "changes": [
                {"kind": "model", "name": "openai:gpt", "value": "off"},
                {"kind": "tool", "name": "crm/delete", "value": None},
            ]
        },
        admin=True,
        check=_type(GrantView),
    ),
    Case(
        "admin.ask_rules.get",
        lambda c: c.admin.ask_rules.get(),
        "GET",
        "/v1/admin/ask-rules",
        {
            "rules": [{"action": "crm_*", "effect": "ask"}],
            "deployment": [],
            "agents": [],
            "tools": [{"name": "crm_get", "server": "crm", "mutating": False}],
        },
        admin=True,
        check=lambda r: (
            _type(AskRulesView)(r),
            _eq(("crm_*", "ask", "*"))((r.rules[0].action, r.rules[0].effect, r.rules[0].resource)),
        ),
    ),
    Case(
        "admin.ask_rules.put",
        lambda c: c.admin.ask_rules.put([
            AskRule(action="crm_get", argument="code", resource="VIP-*", label={"en": "Key"}),
            {"action": "*", "effect": "default", "tags": ["team:ops"]},
        ]),
        "PUT",
        "/v1/admin/ask-rules",
        {"rules": [], "deployment": [], "agents": [], "tools": []},
        body={
            "rules": [
                {
                    "action": "crm_get",
                    "argument": "code",
                    "resource": "VIP-*",
                    "label": {"en": "Key"},
                },
                {"action": "*", "effect": "default", "tags": ["team:ops"]},
            ]
        },
        admin=True,
        check=_type(AskRulesView),
    ),
    Case(
        "admin.agents.list",
        lambda c: c.admin.agents.list(),
        "GET",
        "/v1/admin/agents",
        {"items": [{**AGENT, "version": "abc", "title": "Triage"}]},
        admin=True,
        check=_type(AgentDef),
    ),
    Case(
        "admin.agents.versions",
        lambda c: c.admin.agents.versions("triage"),
        "GET",
        "/v1/admin/agents/triage/versions",
        {
            "items": [
                {
                    "version": "abc",
                    "active": True,
                    "createdAt": "2026-11-01",
                    "createdBy": "config",
                    "definition": AGENT,
                }
            ]
        },
        admin=True,
        check=lambda r: (_type(AgentVersion)(r), _type(AgentDef)(r[0].definition)),
    ),
    Case(
        "admin.agents.put",
        lambda c: c.admin.agents.put(
            AgentDef.model_validate({**AGENT, "instructions": "Sort mail."})
        ),
        "PUT",
        "/v1/admin/agents/triage",
        {"version": "def", "created": True, "evalRunId": "e1"},
        body={**AGENT, "instructions": "Sort mail."},
        admin=True,
        check=lambda r: (_type(AgentPutResult)(r), _eq("e1")(r.eval_run_id)),
    ),
    Case(
        "admin.agents.put-dict",
        lambda c: c.admin.agents.put({"name": "a b", "model": "m"}),
        "PUT",
        "/v1/admin/agents/a%20b",
        {"version": "def", "created": False},
        body={"name": "a b", "model": "m"},
        admin=True,
    ),
    Case(
        "admin.agents.delete",
        lambda c: c.admin.agents.delete("triage"),
        "DELETE",
        "/v1/admin/agents/triage",
        admin=True,
    ),
    Case(
        "admin.mcp_servers.list",
        lambda c: c.admin.mcp_servers.list(),
        "GET",
        "/v1/admin/mcp-servers",
        {
            "items": [
                {
                    "name": "crm",
                    "url": "https://crm.example/mcp",
                    "auth": "forward_user",
                    "hasSecret": False,
                    "timeout_seconds": 30,
                    "tool_prefix": "crm_",
                    "status": "ok",
                    "latencyMs": 12,
                    "tools": [{"name": "search", "description": "", "readOnly": True}],
                }
            ]
        },
        admin=True,
        check=lambda r: (
            _type(McpServerInfo)(r),
            _eq((30, "crm_", True))((
                r[0].timeout_seconds,
                r[0].tool_prefix,
                r[0].tools[0].read_only,
            )),
        ),
    ),
    Case(
        "admin.mcp_servers.put",
        lambda c: c.admin.mcp_servers.put(
            "crm", McpServerBody(url="https://crm.example/mcp", auth="secret", secret="s")
        ),
        "PUT",
        "/v1/admin/mcp-servers/crm",
        body={"url": "https://crm.example/mcp", "auth": "secret", "secret": "s"},
        admin=True,
    ),
    Case(
        "admin.mcp_servers.put-connector",
        lambda c: c.admin.mcp_servers.put("db", {"connector": "sql", "config": {"dsn_env": "DB"}}),
        "PUT",
        "/v1/admin/mcp-servers/db",
        body={"connector": "sql", "config": {"dsn_env": "DB"}},
        admin=True,
    ),
    Case(
        "admin.mcp_servers.delete",
        lambda c: c.admin.mcp_servers.delete("crm"),
        "DELETE",
        "/v1/admin/mcp-servers/crm",
        admin=True,
    ),
    Case(
        "admin.mcp_servers.test",
        lambda c: c.admin.mcp_servers.test("crm", "user-jwt"),
        "POST",
        "/v1/admin/mcp-servers/crm/test",
        {"ok": True, "latencyMs": 40, "tools": [{"name": "search"}]},
        body={"credential": "user-jwt"},
        admin=True,
        check=_type(McpTestResult),
    ),
    Case(
        "admin.mcp_servers.test-no-credential",
        lambda c: c.admin.mcp_servers.test("crm"),
        "POST",
        "/v1/admin/mcp-servers/crm/test",
        {"ok": False, "error": "refused"},
        body={},
        admin=True,
    ),
    Case(
        "admin.mcp_servers.connectors",
        lambda c: c.admin.mcp_servers.connectors(),
        "GET",
        "/v1/admin/connectors",
        {"items": [{"kind": "sql", "title": "SQL", "schema": {"type": "object"}}]},
        admin=True,
        check=lambda r: (_type(ConnectorKind)(r), _eq({"type": "object"})(r[0].schema_)),
    ),
    Case(
        "admin.skills.list",
        lambda c: c.admin.skills.list(),
        "GET",
        "/v1/admin/skills",
        {"items": [{"name": "s", "description": "d", "size": 3, "updatedAt": "x"}]},
        admin=True,
        check=_type(SkillInfo),
    ),
    Case(
        "admin.skills.get",
        lambda c: c.admin.skills.get("s"),
        "GET",
        "/v1/admin/skills/s",
        {"name": "s", "description": "d", "body": "Do it.", "size": 6},
        admin=True,
        check=lambda r: (_type(SkillDetail)(r), _eq("Do it.")(r.body)),
    ),
    Case(
        "admin.skills.put",
        lambda c: c.admin.skills.put("s", {"description": "d", "body": "Do it."}),
        "PUT",
        "/v1/admin/skills/s",
        body={"description": "d", "body": "Do it."},
        admin=True,
    ),
    Case(
        "admin.skills.delete",
        lambda c: c.admin.skills.delete("s"),
        "DELETE",
        "/v1/admin/skills/s",
        admin=True,
    ),
    Case(
        "admin.skills.file",
        lambda c: c.admin.skills.file("s", "references/terms.md"),
        "GET",
        "/v1/admin/skills/s/files/references/terms.md",
        {"path": "references/terms.md", "kind": "reference", "size": 2, "content": "# T"},
        admin=True,
        check=lambda r: (_type(SkillFile)(r), _eq("# T")(r.content)),
    ),
    Case(
        "admin.skills.put_file",
        lambda c: c.admin.skills.put_file("s", "data/a.csv", "x\n1\n"),
        "PUT",
        "/v1/admin/skills/s/files/data/a.csv",
        body={"content": "x\n1\n"},
        admin=True,
    ),
    Case(
        "admin.skills.delete_file",
        lambda c: c.admin.skills.delete_file("s", "data/a.csv"),
        "DELETE",
        "/v1/admin/skills/s/files/data/a.csv",
        admin=True,
    ),
    Case(
        "admin.prompts.list",
        lambda c: c.admin.prompts.list(),
        "GET",
        "/v1/admin/prompts",
        {"items": [{"name": "p", "version": 2, "createdAt": "x"}]},
        admin=True,
        check=_type(PromptVersion),
    ),
    Case(
        "admin.prompts.put",
        lambda c: c.admin.prompts.put("p", "Be brief."),
        "PUT",
        "/v1/admin/prompts/p",
        {"version": 3},
        body={"text": "Be brief."},
        admin=True,
        check=_eq(3),
    ),
    Case(
        "admin.prompts.put-module",
        lambda c: c.admin.prompts.put(
            "tone",
            "{{lookup data style}}",
            parameters={
                "type": "object",
                "properties": {"style": {"type": "string", "enum": ["a"]}},
            },
            description="How long the answers are.",
            data={"a": "Brief."},
        ),
        "PUT",
        "/v1/admin/prompts/tone",
        {"version": 1},
        body={
            "text": "{{lookup data style}}",
            "parameters": {
                "type": "object",
                "properties": {"style": {"type": "string", "enum": ["a"]}},
            },
            "description": "How long the answers are.",
            "data": {"a": "Brief."},
        },
        admin=True,
        check=_eq(1),
    ),
    Case(
        "admin.prompts.preview",
        lambda c: c.admin.prompts.preview("tone", params={"style": "a"}),
        "POST",
        "/v1/admin/prompts/tone/preview",
        {"text": "Brief."},
        body={"params": {"style": "a"}},
        admin=True,
        check=_eq("Brief."),
    ),
    Case(
        "admin.voices.put",
        lambda c: c.admin.voices.put("polly", "Bianca", {"language": "it-IT", "name": "Bianca"}),
        "PUT",
        "/v1/admin/voices/polly/Bianca",
        body={"language": "it-IT", "name": "Bianca"},
        admin=True,
    ),
    Case(
        "admin.bug_reports",
        lambda c: c.admin.bug_reports(),
        "GET",
        "/v1/admin/bug-reports",
        {"items": [{"id": "b1", "subject": "u", "sessionId": "c1", "description": "Broken"}]},
        admin=True,
        check=_type(BugReport),
    ),
    Case(
        "admin.providers",
        lambda c: c.admin.providers(),
        "GET",
        "/v1/admin/providers",
        {"version": 1, "providers": [{"id": "scaleway"}], "processors": []},
        admin=True,
        check=lambda r: _eq(1)(r["version"]),
    ),
    Case(
        "admin.data_protection",
        lambda c: c.admin.data_protection(),
        "GET",
        "/v1/admin/data-protection",
        {"profile": {"jurisdiction": ["eu"]}, "processors": [], "empty": False},
        admin=True,
        check=lambda r: _eq(["eu"])(r["profile"]["jurisdiction"]),
    ),
    Case(
        "admin.security.get",
        lambda c: c.admin.security.get(),
        "GET",
        "/v1/admin/security",
        {"enabled": True, "policy": {}, "eventTtlDays": 30, "defaultClassifier": None},
        admin=True,
        check=lambda r: (_type(SecurityView)(r), _eq(30)(r.event_ttl_days)),
    ),
    Case(
        "admin.security.put",
        lambda c: c.admin.security.put(
            SecurityPolicy(mode="enforce", modes={"input.injection": "monitor"})
        ),
        "PUT",
        "/v1/admin/security",
        {"enabled": True},
        body={"mode": "enforce", "modes": {"input.injection": "monitor"}},
        admin=True,
        check=_type(SecurityView),
    ),
    Case(
        "admin.security.put-nested",
        lambda c: c.admin.security.put({
            "tools": {"deny": ["crm/delete"]},
            "abuse": {"concurrent_turns": 2},
        }),
        "PUT",
        "/v1/admin/security",
        {"enabled": True},
        body={"tools": {"deny": ["crm/delete"]}, "abuse": {"concurrent_turns": 2}},
        admin=True,
    ),
    Case(
        "admin.security.events",
        lambda c: c.admin.security.events(
            from_="2026-11-01", control="input.", severity="high", before="2026-11-10", limit=50
        ),
        "GET",
        "/v1/admin/security/events",
        {
            "items": [{"id": "e1", "at": "x", "control": "input.injection", "conversationId": "c"}],
            "next": "y",
        },
        params={
            "from": "2026-11-01",
            "control": "input.",
            "severity": "high",
            "before": "2026-11-10",
            "limit": "50",
        },
        admin=True,
        check=lambda r: (_type(SecurityEventPage)(r), _eq("c")(r.items[0].conversation_id)),
    ),
    Case(
        "admin.security.summary",
        lambda c: c.admin.security.summary(agent="a"),
        "GET",
        "/v1/admin/security/summary",
        {"total": 3, "byRule": [{"rule": "x", "count": 3}], "byControl": {}, "byAction": {}},
        params={"agent": "a"},
        admin=True,
        check=_type(SecuritySummary),
    ),
    Case(
        "admin.compliance.profile",
        lambda c: c.admin.compliance.profile(),
        "GET",
        "/v1/admin/compliance/profile",
        {"profile": {"system_name": "S", "ai_act": {"annex_iii": ["education"]}}, "saved": True},
        admin=True,
        check=lambda r: (_type(ComplianceProfile)(r), _eq(["education"])(r.ai_act.annex_iii)),
    ),
    Case(
        "admin.compliance.put_profile",
        lambda c: c.admin.compliance.put_profile({"controller": "Acme S.p.A."}),
        "PUT",
        "/v1/admin/compliance/profile",
        {"profile": {"controller": "Acme S.p.A."}},
        body={"controller": "Acme S.p.A."},
        admin=True,
        check=lambda r: _eq("Acme S.p.A.")(r.controller),
    ),
    Case(
        "admin.compliance.pack",
        lambda c: c.admin.compliance.pack("gdpr", lang="it"),
        "GET",
        "/v1/admin/compliance/gdpr",
        {"pack": "gdpr", "lang": "it", "title": "Fascicolo GDPR", "disclaimer": "d"},
        params={"format": "json", "lang": "it"},
        admin=True,
        check=lambda r: (_type(CompliancePack)(r), _eq("it")(r.lang)),
    ),
    Case(
        "admin.compliance.pack-md",
        lambda c: c.admin.compliance.pack("ai-act", format="md"),
        "GET",
        "/v1/admin/compliance/ai-act",
        {"not": "parsed"},
        params={"format": "md"},
        admin=True,
        check=lambda r: _eq(True)(isinstance(r, str) and '"not"' in r),
    ),
    Case(
        "admin.compliance.snapshots",
        lambda c: c.admin.compliance.snapshots(limit=5),
        "GET",
        "/v1/admin/compliance/snapshots",
        {"items": [{"id": "2026111610000000", "createdAt": "x", "classification": "minimal_risk"}]},
        params={"limit": "5"},
        admin=True,
        check=_type(ComplianceSnapshot),
    ),
    Case(
        "admin.compliance.snapshot",
        lambda c: c.admin.compliance.snapshot("before release"),
        "POST",
        "/v1/admin/compliance/snapshots",
        {"id": "2026111610000000", "createdAt": "x", "label": "before release"},
        body={"label": "before release"},
        admin=True,
        check=lambda r: _eq("before release")(r.label),
    ),
    Case(
        "admin.compliance.snapshot_pack",
        lambda c: c.admin.compliance.snapshot_pack("s1", "controls", format="html", lang="en"),
        "GET",
        "/v1/admin/compliance/snapshots/s1/controls",
        {"html": True},
        params={"format": "html", "lang": "en"},
        admin=True,
        check=lambda r: _eq(True)(isinstance(r, str)),
    ),
    Case(
        "admin.compliance.diff",
        lambda c: c.admin.compliance.diff("s1", "s2"),
        "GET",
        "/v1/admin/compliance/snapshots/diff",
        {"counts": {"statements": 2, "facts": 1}},
        params={"from": "s1", "to": "s2"},
        admin=True,
        check=lambda r: _eq(2)(r["counts"]["statements"]),
    ),
    Case(
        "admin.activity",
        lambda c: c.admin.activity(limit=10),
        "GET",
        "/v1/admin/activity",
        {"items": [{"at": "x", "kind": "agent", "name": "a", "detail": "v1", "by": None}]},
        params={"limit": "10"},
        admin=True,
        check=_type(ActivityItem),
    ),
    Case(
        "admin.usage_summary",
        lambda c: c.admin.usage_summary(from_="2026-11-01", tag="t"),
        "GET",
        "/v1/admin/usage/summary",
        {"turns": 9, "fallbackTurns": 1, "subjects": 2, "tags": ["t"], "from": "a", "to": "b"},
        params={"from": "2026-11-01", "tag": "t"},
        admin=True,
        check=lambda r: (_type(UsageSummary)(r), _eq(("a", 1))((r.from_, r.fallback_turns))),
    ),
    Case(
        "admin.export",
        lambda c: c.admin.export(),
        "GET",
        "/v1/admin/export",
        {"yaml": "slug: bookshop\n", "filename": "bookshop.yaml"},
        admin=True,
        check=_type(ExportedConfig),
    ),
]


def _clients() -> tuple[EvokaiServer, AsyncEvokaiServer]:
    return (
        EvokaiServer(BASE, SERVER_KEY, admin_key=ADMIN_KEY),
        AsyncEvokaiServer(BASE, SERVER_KEY, admin_key=ADMIN_KEY),
    )


async def _invoke(fn: Callable[[Any], Any], client: Any) -> Any:
    result = fn(client)
    return await result if inspect.isawaitable(result) else result


@pytest.mark.parametrize("flavour", ["sync", "async"])
@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_request_and_parsing(case: Case, flavour: str, router: respx.MockRouter) -> None:
    route = router.request(case.method, case.path).mock(
        return_value=httpx.Response(case.status, json=case.response)
    )
    sync, aio = _clients()
    client = sync if flavour == "sync" else aio
    try:
        result = await _invoke(case.call, client)
    finally:
        sync.close()
        await aio.aclose()
    assert route.call_count == 1
    request = route.calls.last.request
    assert request.method == case.method
    assert request.url.raw_path.split(b"?")[0] == ("/agent" + case.path).encode()
    key = ADMIN_KEY if case.admin else SERVER_KEY
    assert request.headers["authorization"] == f"Bearer {key}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"].startswith("evokai-python/")
    if case.params is not None:
        assert dict(request.url.params) == case.params
    if case.body is None:
        assert request.content == b""
    else:
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == case.body
    case.check(result)


NAMESPACES = {
    "": (),
    "conversations": (),
    "memories": (),
    "privacy": (),
    "admin": (
        "application",
        "keys",
        "grants",
        "agents",
        "mcp_servers",
        "skills",
        "prompts",
        "voices",
        "security",
        "compliance",
    ),
}


def _methods(client: Any) -> set[str]:
    out: set[str] = set()
    for attr, subs in NAMESPACES.items():
        base = getattr(client, attr) if attr else client
        for sub in ("", *subs):
            obj = getattr(base, sub) if sub else base
            path = ".".join(p for p in (attr, sub) if p)
            for name, _ in inspect.getmembers(obj, inspect.ismethod):
                if name.startswith("_") or (not path and name in {"close", "aclose", "session"}):
                    continue
                out.add(f"{path}.{name}" if path else name)
    return out


async def test_every_public_method_has_a_case() -> None:
    covered = {c.id.split("-")[0] for c in CASES}
    covered |= {"mint_session_token", "run"} - {"mint", "run"}
    covered = {"mint_session_token" if x == "mint" else x for x in covered}
    sync, aio = _clients()
    try:
        assert _methods(sync) == _methods(aio)
        assert _methods(sync) - covered == set()
    finally:
        sync.close()
        await aio.aclose()


def test_raw_paths_are_encoded(router: respx.MockRouter) -> None:
    route = router.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"name": "a"}))
    with EvokaiServer(BASE, admin_key=ADMIN_KEY) as c:
        c.admin.skills.get("a/b?c")
    assert route.calls.last.request.url.raw_path == b"/agent/v1/admin/skills/a%2Fb%3Fc"


def test_template_syntax_is_left_to_the_service_unless_set() -> None:
    from evokai._calls import body

    sent = body(AgentDef, AgentDef(name="a", model="m", instructions="Today is {{date}}."))
    assert "template_syntax" not in sent
    kept = body(AgentDef, {"name": "a", "model": "m", "template_syntax": "legacy"})
    assert kept["template_syntax"] == "legacy"
    version = PromptVersion.model_validate({"name": "p", "version": 1, "templateSyntax": "legacy"})
    assert version.template_syntax == "legacy"
