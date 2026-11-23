"""The hand-written models stay in step with the service's OpenAPI schema.

Only the schemas this client models are checked, and only in the direction that would break it:

- every field a model sends or reads exists in its schema, under the same wire name;
- a request model requires exactly what the schema requires;
- a response model requires nothing the schema leaves optional;
- a field typed with another mapped model references that model's schema;
- a ``Literal`` field offers only values the schema's enum accepts.

A field the service adds to a schema never fails this test: the client ignores it until someone
models it.
"""

from __future__ import annotations

import json
import types
import typing
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel

from evokai import _calls as calls
from evokai import events, models
from evokai.session import chat_body

OPENAPI = Path(__file__).resolve().parents[2] / "typescript" / "openapi.json"

REQUESTS: dict[type[BaseModel], str] = {
    models.TokenRequest: "TokenRequest",
    models.SubjectLimits: "SubjectLimits",
    models.Consent: "Consent",
    models.RunRequest: "RunRequest",
    models.RunMessage: "RunMessage",
    models.RunAttachment: "RunAttachment",
    models.PageContext: "PageContext",
    models.HistoryMessage: "HistoryMessage",
    models.TextBlock: "TextBlock",
    models.FileBlock: "FileBlock",
    models.FileRef: "FileRef",
    models.ToolUseBlock: "ToolUseBlock",
    models.ToolUse: "ToolUse",
    models.ToolResultBlock: "ToolResultBlock",
    models.ToolResult: "ToolResult",
    models.SubjectRequest: "SubjectRequest",
    models.AppPatch: "AppPatch",
    models.Residency: "Residency",
    models.DataProtection: "DataProtection",
    models.ResidencyRule: "ResidencyRule",
    models.BrowserDeclaration: "BrowserDeclaration",
    models.KeyRequest: "KeyRequest",
    models.AgentDef: "AgentDef",
    models.PromptRef: "PromptRef",
    models.VoiceDef: "VoiceDef",
    models.NavigateDef: "NavigateDef",
    models.ClientToolsDef: "ClientToolsDef",
    models.ClientToolDef: "ClientToolDef",
    models.McpServerBody: "McpServerBody",
    models.SkillBody: "SkillBody",
    models.SkillFileBody: "SkillFileBody",
    models.PromptBody: "PromptBody",
    models.VoiceBody: "VoiceBody",
    models.GrantChange: "GrantChange",
    models.GrantChanges: "GrantChanges",
    models.SecurityPolicy: "SecurityPolicy",
    models.AbuseSettings: "AbuseSettings",
    models.InjectionSettings: "InjectionSettings",
    models.ModerationSettings: "ModerationSettings",
    models.InputSettings: "InputSettings",
    models.LinkSettings: "LinkSettings",
    models.PiiSettings: "PiiSettings",
    models.ScopeSettings: "ScopeSettings",
    models.ToolSettings: "ToolSettings",
    models.UntrustedSettings: "UntrustedSettings",
    models.CodeDef: "CodeDef",
    models.ComplianceProfile: "ComplianceProfile",
    models.AiActAnswers: "AiActAnswers",
    models.GdprAnswers: "GdprAnswers",
    models.SnapshotBody: "SnapshotBody",
    models.ConnectionStart: "ConnectionStart",
    models.AskRule: "AskRule",
}

RESPONSES: dict[type[BaseModel], str] = {
    models.RunResult: "RunResponse",
    models.RunUsage: "RunUsage",
    events.ChatResult: "ChatResult",
    models.Connection: "Connection",
    models.ConnectionStarted: "ConnectionStarted",
}

MAPPED = {**REQUESTS, **RESPONSES}


@pytest.fixture(scope="module")
def schemas() -> dict[str, Any]:
    if not OPENAPI.exists():
        pytest.skip("clients/typescript/openapi.json is not next to this package")
    return json.loads(OPENAPI.read_text())["components"]["schemas"]


def wire_fields(model: type[BaseModel]) -> dict[str, bool]:
    """Wire name -> required."""
    return {(f.alias or name): f.is_required() for name, f in model.model_fields.items()}


def _models_in(annotation: Any) -> set[type[BaseModel]]:
    out: set[type[BaseModel]] = set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        out.add(annotation)
    for arg in get_args(annotation):
        out |= _models_in(arg)
    return out


def _literals_in(annotation: Any) -> set[Any]:
    if get_origin(annotation) is Literal:
        return set(get_args(annotation))
    out: set[Any] = set()
    if get_origin(annotation) in {typing.Union, types.UnionType, list, dict}:
        for arg in get_args(annotation):
            out |= _literals_in(arg)
    return out


def _walk(node: Any, key: str) -> set[Any]:
    found: set[Any] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                found |= set(v) if isinstance(v, list) else {v}
            else:
                found |= _walk(v, key)
    elif isinstance(node, list):
        for v in node:
            found |= _walk(v, key)
    return found


@pytest.mark.parametrize("model", list(MAPPED), ids=lambda m: m.__name__)
def test_fields_exist_in_the_schema(model: type[BaseModel], schemas: dict[str, Any]) -> None:
    schema = schemas[MAPPED[model]]
    unknown = set(wire_fields(model)) - set(schema.get("properties", {}))
    assert not unknown, f"{model.__name__} has fields the schema does not: {sorted(unknown)}"


@pytest.mark.parametrize("model", list(REQUESTS), ids=lambda m: m.__name__)
def test_requests_require_what_the_schema_requires(
    model: type[BaseModel], schemas: dict[str, Any]
) -> None:
    required = {name for name, req in wire_fields(model).items() if req}
    assert required == set(schemas[REQUESTS[model]].get("required", []))


@pytest.mark.parametrize("model", list(RESPONSES), ids=lambda m: m.__name__)
def test_responses_require_nothing_optional(
    model: type[BaseModel], schemas: dict[str, Any]
) -> None:
    required = {name for name, req in wire_fields(model).items() if req}
    assert required <= set(schemas[RESPONSES[model]].get("required", []))


@pytest.mark.parametrize("model", list(MAPPED), ids=lambda m: m.__name__)
def test_nested_models_and_enums_match(model: type[BaseModel], schemas: dict[str, Any]) -> None:
    props = schemas[MAPPED[model]].get("properties", {})
    for name, f in model.model_fields.items():
        prop = props.get(f.alias or name)
        if prop is None:
            continue  # reported by test_fields_exist_in_the_schema
        refs = {r.rsplit("/", 1)[-1] for r in _walk(prop, "$ref")}
        for nested in _models_in(f.annotation):
            if nested in MAPPED:
                assert MAPPED[nested] in refs, f"{model.__name__}.{name} -> {nested.__name__}"
        literals = _literals_in(f.annotation)
        enum = _walk(prop, "enum") | {s for r in refs for s in schemas[r].get("enum", [])}
        if literals and enum:
            assert literals <= enum, f"{model.__name__}.{name}: {literals - enum}"


def test_hand_built_bodies_match_their_schemas(schemas: dict[str, Any]) -> None:
    body = chat_body(
        text="x",
        page={"path": "/"},
        mode="voice",
        history=[{"role": "user", "text": "x"}],
        end_of_speech_at=1.0,
    )
    assert set(body) <= set(schemas["ChatBody"]["properties"])
    confirm = chat_body(confirm=("a", True))["confirm"]
    assert set(confirm) == set(schemas["Confirm"]["required"])
    assert set(schemas["ChatBody"]["properties"]["mode"]["enum"]) == {"text", "voice"}
    built = {
        "Feedback": calls.session_feedback("m", 1).body,
        "ExitBody": calls.session_exit("1234").body,
        "BugReportBody": calls.session_bug_report("x", {}).body,
        "McpTest": calls.mcp_test("s", "token").body,
        "ConnectionStart": calls.session_connection_start("google", "https://a.test").body,
        "ConsentBody": calls.memories_consent("u", True).body,
        "MemoryConsentBody": calls.session_memory_consent(True).body,
        "MemoryEdit": calls.session_memory_edit("m1", "x").body,
    }
    for name, value in built.items():
        props = schemas[name]["properties"]
        assert set(value) <= set(props), name
        assert set(schemas[name].get("required", [])) <= set(value), name


def test_routes_the_client_calls_exist() -> None:
    if not OPENAPI.exists():
        pytest.skip("clients/typescript/openapi.json is not next to this package")
    paths = json.loads(OPENAPI.read_text())["paths"]
    base = "/agent"
    called = [
        ("post", "/v1/session-tokens"),
        ("post", "/v1/agents/{name}/run"),
        ("get", "/v1/conversations"),
        ("get", "/v1/conversations/{conversation_id}"),
        ("delete", "/v1/conversations/{conversation_id}"),
        ("get", "/v1/usage"),
        ("get", "/v1/memories"),
        ("delete", "/v1/memories"),
        ("post", "/v1/memories/consent"),
        ("get", "/v1/agents"),
        ("get", "/v1/voices"),
        ("post", "/v1/privacy/export"),
        ("post", "/v1/privacy/erase"),
        ("post", "/v1/privacy/revoke-sessions"),
        ("get", "/v1/admin/application"),
        ("patch", "/v1/admin/application"),
        ("get", "/v1/admin/keys"),
        ("post", "/v1/admin/keys"),
        ("delete", "/v1/admin/keys/{key_id}"),
        ("post", "/v1/admin/keys/{key_id}/rotate"),
        ("get", "/v1/admin/grants"),
        ("patch", "/v1/admin/grants"),
        ("get", "/v1/admin/agents"),
        ("get", "/v1/admin/agents/{name}/versions"),
        ("put", "/v1/admin/agents/{name}"),
        ("delete", "/v1/admin/agents/{name}"),
        ("get", "/v1/admin/mcp-servers"),
        ("put", "/v1/admin/mcp-servers/{name}"),
        ("delete", "/v1/admin/mcp-servers/{name}"),
        ("post", "/v1/admin/mcp-servers/{name}/test"),
        ("get", "/v1/admin/connectors"),
        ("get", "/v1/admin/skills"),
        ("get", "/v1/admin/skills/{name}"),
        ("put", "/v1/admin/skills/{name}"),
        ("delete", "/v1/admin/skills/{name}"),
        ("get", "/v1/admin/prompts"),
        ("put", "/v1/admin/prompts/{name}"),
        ("put", "/v1/admin/voices/{provider}/{voice_id}"),
        ("get", "/v1/admin/bug-reports"),
        ("get", "/v1/admin/security"),
        ("put", "/v1/admin/security"),
        ("get", "/v1/admin/security/events"),
        ("get", "/v1/admin/security/summary"),
        ("get", "/v1/admin/activity"),
        ("get", "/v1/admin/usage/summary"),
        ("get", "/v1/admin/export"),
        ("get", "/v1/admin/compliance/profile"),
        ("put", "/v1/admin/compliance/profile"),
        ("get", "/v1/admin/compliance/{pack}"),
        ("get", "/v1/admin/compliance/snapshots"),
        ("post", "/v1/admin/compliance/snapshots"),
        ("get", "/v1/admin/compliance/snapshots/{snapshot_id}/{pack}"),
        ("get", "/v1/admin/compliance/snapshots/diff"),
        ("get", "/v1/session"),
        ("get", "/v1/session/conversation"),
        ("get", "/v1/session/events"),
        ("post", "/v1/session/chat"),
        ("post", "/v1/session/turns/{turn_id}/cancel"),
        ("post", "/v1/session/messages/{message_id}/feedback"),
        ("post", "/v1/session/exit"),
        ("post", "/v1/session/bug-reports"),
        ("get", "/v1/session/connections"),
        ("post", "/v1/session/connections/{provider}/start"),
        ("delete", "/v1/session/connections/{provider}"),
        ("get", "/v1/connections"),
        ("delete", "/v1/connections/{provider}"),
        ("get", "/v1/session/memories"),
        ("delete", "/v1/session/memories"),
        ("post", "/v1/session/memory-consent"),
        ("patch", "/v1/session/memories/{memory_id}"),
        ("delete", "/v1/session/memories/{memory_id}"),
    ]
    missing = [(m, p) for m, p in called if m not in paths.get(base + p, {})]
    assert not missing
