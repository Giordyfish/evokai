"""The gateway proxy: catalogue, CA constraints, identity, adapters, decisions, end to end.

Fixtures are sanitised shapes of the 2026-11-27 captures (``tests/proxy_fixtures.py``). The end to
end tests run the real engine (mitmproxy) against a local fake upstream on TLS
(``tests/proxy_harness.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] (openssl verify, fixed arguments)
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import websockets
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from mitmproxy import certs

from evokai.gateway import scan
from evokai.gateway.proxy import ca, catalogue, identity
from evokai.gateway.proxy.adapters import Answer, FormatUnknown, Request, Rewrite
from evokai.gateway.proxy.adapters import anthropic as a_anthropic
from evokai.gateway.proxy.adapters import claude as a_claude
from evokai.gateway.proxy.engine import Engine
from evokai.gateway.proxy.run import Refused, preflight
from evokai.gateway.proxy.service import ProxyService
from evokai.gateway.proxy.texts import Texts
from tests import proxy_fixtures as fx
from tests.conftest import Env, make_env
from tests.proxy_harness import (
    UPSTREAM_HOSTS,
    FakeUpstream,
    TestCa,
    client_ssl,
    h2_exchange,
    proxy_ca,
    serve_upstream,
)

OPERATOR_KEY = "evk_op_gateway_tests_operator_key_0123456789"  # gitleaks:allow
OP = {"Authorization": f"Bearer {OPERATOR_KEY}"}
GW = "/agent/v1/admin/gateways"


def req(method: str, host: str, path: str, body: Any = b"", **headers: str) -> Request:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return Request(
        method,
        host,
        path,
        [(k.replace("_", "-").lower(), v) for k, v in headers.items()],
        raw,
        client="10.0.0.5",
    )


def chat_path() -> str:
    return f"/api/organizations/{fx.ORG}/chat_conversations/{fx.CONV}/completion"


def cookie() -> str:
    return f"sessionKey={fx.SESSION_KEY}"


# The catalogue and the PAC


def test_only_providers_enabled_and_served_by_adapters_are_intercepted() -> None:
    assert catalogue.served({"anthropic"}, {"anthropic"}) == ["anthropic"]
    assert catalogue.served({"openai"}, {"anthropic"}) == []  # no OpenAI adapters in this build
    assert catalogue.served(set(), {"anthropic"}) == []
    hosts = catalogue.intercepted(["anthropic"])
    assert any(h.matches("bridge.claudeusercontent.com") for h in hosts)
    assert not any(h.matches("downloads.claude.ai") for h in hosts)
    assert catalogue.is_tunnelled("downloads.claude.ai")
    assert catalogue.provider_of("claude.ai") == "anthropic"
    assert catalogue.provider_of("example.com") is None


def test_the_pac_names_only_the_catalogue_and_the_check_host() -> None:
    hosts = catalogue.intercepted(["anthropic"])
    block = catalogue.pac(hosts, "proxy.example.com:3128")
    assert 'if (host == "claude.ai") return "PROXY proxy.example.com:3128";' in block
    assert 'dnsDomainIs(host, ".claudeusercontent.com")' in block
    assert '"evokai.check"' in block
    assert "chatgpt.com" not in block
    assert block.rstrip().endswith('return "DIRECT";\n}'.rstrip())
    direct = catalogue.pac(hosts, "proxy.example.com:3128", fallback="direct")
    assert "PROXY proxy.example.com:3128; DIRECT" in direct


# The CA


def _leaf(authority: ca.Authority, host: str) -> x509.Certificate:
    """A leaf minted the way the proxy mints it (mitmproxy's certificate store)."""
    entry = certs.dummy_cert(authority.key, authority.cert, host, [x509.DNSName(host)])
    return entry.to_cryptography()


def _pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
def test_a_leaf_verifies_for_the_catalogue_and_never_for_another_site(tmp_path: Path) -> None:
    test = proxy_ca(tmp_path)
    authority = ca.load(test.key, test.cert)
    inter = tmp_path / "inter.pem"
    inter.write_bytes(_pem(authority.cert))

    def verify(host: str) -> subprocess.CompletedProcess[str]:
        leaf = tmp_path / f"{host}.pem"
        leaf.write_bytes(_pem(_leaf(authority, host)))
        cmd = ["openssl", "verify", "-CAfile", str(test.root), "-untrusted", str(inter), str(leaf)]
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    assert verify("claude.ai").returncode == 0
    assert verify("api.anthropic.com").returncode == 0
    assert verify("bridge.claudeusercontent.com").returncode == 0
    refused = verify("example.com")
    assert refused.returncode != 0
    assert "subtree" in (refused.stdout + refused.stderr).lower()


def test_init_ca_writes_keys_for_the_owner_only_and_never_overwrites(tmp_path: Path) -> None:
    files = ca.init(tmp_path / "ca")
    assert files["key"].stat().st_mode & 0o777 == 0o600
    assert files["root_key"].stat().st_mode & 0o777 == 0o600
    chain = x509.load_pem_x509_certificates(files["cert"].read_bytes())
    assert len(chain) == 2 and chain[1].subject == chain[1].issuer
    assert ca.permitted(chain[0]) == list(catalogue.NAME_CONSTRAINTS)
    with pytest.raises(ca.CaError):
        ca.init(tmp_path / "ca")


def test_the_proxy_refuses_a_missing_mismatched_or_unconstrained_ca(tmp_path: Path) -> None:
    test = proxy_ca(tmp_path)
    other = proxy_ca(tmp_path / "other")
    with pytest.raises(ca.CaError, match="not set"):
        ca.load(None, test.cert)
    with pytest.raises(ca.CaError, match="does not belong"):
        ca.load(other.key, test.cert)
    # The root is a CA without Name Constraints: never accepted as the signing CA.
    root_key = (test.dir / "root.key").read_text()
    with pytest.raises(ca.CaError, match="Name Constraints"):
        ca.load(root_key, test.root.read_text())
    # A file path works as well as the PEM itself.
    assert ca.load(str(test.dir / "intermediate.key"), str(test.dir / "intermediate.crt"))


def test_evokai_proxy_refuses_to_start_without_the_switch_or_a_ca(env: Env, tmp_path: Path) -> None:
    s = env.svc.settings
    with pytest.raises(Refused, match="EVOKAI_PROXY is not on"):
        preflight(s)
    s.proxy = "on"
    with pytest.raises(Refused, match="no usable proxy CA"):
        preflight(s)
    test = proxy_ca(tmp_path)
    s.proxy_ca_key, s.proxy_ca_cert = test.key, test.cert
    assert preflight(s).permitted


# Identity


def test_the_session_cookie_is_read_from_every_cookie_header() -> None:
    split = ["lastActiveOrg=x", f"anthropic-device-id=d; sessionKey={fx.SESSION_KEY}", "a=b"]
    key = identity.claude_ai_key(split, fx.ORG)
    assert key is not None and key.value == fx.SESSION_KEY and key.org == fx.ORG
    assert identity.claude_ai_key(["lastActiveOrg=x"], None) is None
    # Only a digest is kept, never the value.
    assert fx.SESSION_KEY not in key.digest and len(key.digest) == 64


def test_the_account_comes_from_metadata_user_id_in_both_spellings() -> None:
    assert identity.account_from_user_id(fx.messages_body("x")) == fx.ACCOUNT
    old = {"metadata": {"user_id": f"user_{'a' * 64}_account_{fx.ACCOUNT}_session_x"}}
    assert identity.account_from_user_id(old) == fx.ACCOUNT
    assert identity.account_from_user_id({"metadata": {"user_id": "nothing"}}) is None
    assert identity.account_from_user_id({}) is None


def test_accounts_are_read_from_the_profile_and_the_account_answers() -> None:
    assert identity.from_oauth_profile(fx.oauth_profile()) == (fx.EMAIL, fx.ACCOUNT, fx.ORG)
    assert identity.from_claude_account(fx.claude_account()) == (fx.EMAIL, fx.ACCOUNT, fx.ORG)
    assert identity.from_oauth_profile([]) == (None, None, None)
    assert identity.mask_email("ada@example.com") == "a***@example.com"


# Adapters


def test_messages_reads_only_the_new_text_and_refuses_with_a_synthetic_answer() -> None:
    a = a_anthropic.Messages()
    r = req(
        "POST",
        "api.anthropic.com",
        "/v1/messages",
        fx.messages_body("newest"),
        user_agent=fx.CLI_UA,
    )
    assert a.matches("POST", "api.anthropic.com", "/v1/messages")
    found = a.inspect(r)
    assert found is not None and found.text == "newest" and found.surface == "claude_code"
    assert a.key(r) == identity.Key("anthropic", "account", fx.ACCOUNT)
    sse = a.refuse(r, found, Texts(), "no")
    assert isinstance(sse, Answer)
    assert sse.status == 200 and sse.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[6:]) for line in sse.body.decode().splitlines() if line.startswith("data: ")
    ]
    assert events[0]["type"] == "message_start"
    assert events[2]["delta"]["text"] == "no" and events[-1]["type"] == "message_stop"
    r2 = req(
        "POST",
        "api.anthropic.com",
        "/v1/messages",
        fx.messages_body("x", stream=False),
        user_agent=fx.DESKTOP_CODE_UA,
    )
    found2 = a.inspect(r2)
    assert found2 is not None and found2.surface == "desktop_code"
    full = json.loads(a.refuse(r2, found2, Texts(), "no").body)
    assert full["content"] == [{"type": "text", "text": "no"}] and full["stop_reason"] == "end_turn"


def test_count_tokens_is_checked_and_refused_with_an_estimate() -> None:
    a = a_anthropic.CountTokens()
    r = req("POST", "api.anthropic.com", "/v1/messages/count_tokens", fx.messages_body(fx.SECRET))
    found = a.inspect(r)
    assert found is not None and found.count and not found.record and fx.SECRET in found.text
    answer = a.refuse(r, found, Texts(), "no")
    assert json.loads(answer.body)["input_tokens"] > 0
    assert fx.SECRET.encode() not in answer.body


def test_chat_reads_prompt_and_attachments_and_rewrites_both() -> None:
    a = a_claude.Chat()
    body = fx.chat_body(
        "hello", attachments=[{"file_name": "n.txt", "extracted_content": "file text"}]
    )
    r = req("POST", "claude.ai", chat_path(), body, cookie=cookie(), x_organization_uuid=fx.ORG)
    assert a.matches("POST", "claude.ai", chat_path())
    found = a.inspect(r)
    assert found is not None and "hello" in found.text and "file text" in found.text
    assert found.model == "claude-opus-5-5"
    key = a.key(r)
    assert key is not None and key.org == fx.ORG
    out = a.refuse(r, found, Texts(None, locale="it"), "BLOCCATO")
    assert isinstance(out, Rewrite)
    new = json.loads(out.body)
    assert (
        new["prompt"].startswith("Rispondi ripetendo esattamente") and "BLOCCATO" in new["prompt"]
    )
    assert new["attachments"] == [] and new["files"] == []
    assert new["turn_message_uuids"] == body["turn_message_uuids"]  # the conversation goes on


def test_cowork_rewrites_only_the_user_events_that_break_the_policy() -> None:
    a = a_claude.Cowork()
    path = f"/v1/code/sessions/{fx.SESSION}/events"
    body = fx.cowork_body(f"my key {fx.SECRET}", with_reminder=True)
    r = req("POST", "claude.ai", path, body, cookie=cookie())
    found = a.inspect(r)
    assert found is not None and found.parts is not None and len(found.parts) == 2
    found.offending = {1}
    new = json.loads(a.refuse(r, found, Texts(), "no").body)
    users = [e["payload"] for e in new["events"] if e["payload"].get("type") == "user"]
    assert users[0]["message"]["content"].startswith("<system-reminder>")
    assert users[1]["message"]["content"].startswith("[Message removed by the company policy]")
    assert fx.SECRET not in json.dumps(new)
    assert new["events"][0]["payload"]["type"] == "control_request"
    # An events request without a user message carries nothing to check.
    assert a.inspect(req("POST", "claude.ai", path, {"events": [new["events"][0]]})) is None


def test_the_bridge_replaces_a_tool_result_with_a_notice() -> None:
    a = a_claude.Bridge()
    assert a.matches("bridge.claudeusercontent.com", "/x")
    frame = fx.bridge_result(f"Nota.\nChiave: {fx.SECRET}\n")
    found = a.inspect(frame)
    assert found is not None and fx.SECRET in found.text
    new = json.loads(a.refuse(frame, found, Texts(), "[blocked]"))
    assert new == {
        "result": {"content": [{"type": "text", "text": "[blocked]"}]},
        "jsonrpc": "2.0",
        "id": 7,
    }
    assert a.inspect(b'{"jsonrpc":"2.0","method":"ping"}') is None


@pytest.mark.parametrize(
    ("adapter", "path", "body"),
    [
        (a_anthropic.Messages(), "/v1/messages", {"model": "m", "prompt": "old shape"}),
        (a_anthropic.Messages(), "/v1/messages", b"not json"),
        (a_claude.Chat(), chat_path(), {"text": "renamed field"}),
        (a_claude.Chat(), chat_path(), {"prompt": "x", "attachments": "not a list"}),
        (a_claude.Cowork(), "/v1/code/sessions/s/events", {"items": []}),
        (
            a_claude.Cowork(),
            "/v1/code/sessions/s/events",
            {"events": [{"payload": {"type": "user", "message": {"text": "renamed"}}}]},
        ),
    ],
)
def test_a_changed_shape_is_a_format_unknown(adapter: Any, path: str, body: Any) -> None:
    with pytest.raises(FormatUnknown):
        adapter.inspect(req("POST", "x", path, body))


def test_masking_replaces_secrets_and_leaves_credential_fields() -> None:
    def mask(text: str) -> str:
        return scan.mask(text, secrets=True)[0]

    body = json.dumps({"message_content": f"key {fx.SECRET} please", "csrf_token": fx.SECRET})
    new = json.loads(a_claude.masked(body, True, mask))
    assert new["message_content"] == "key [bloccato] please"
    assert new["csrf_token"] == fx.SECRET
    text, rules = scan.mask("mail ada@example.com", secrets=False, pii_types={"email"})
    assert text == "mail [bloccato]" and rules == ["email"]


def test_refusals_speak_the_gateways_language_and_never_repeat_the_value() -> None:
    en = Texts().refusal("input.secrets")
    assert "a secret or credential" in en and fx.SECRET not in en
    it = Texts(None, locale="it").refusal("input.secrets")
    assert it.startswith("⛔ Richiesta bloccata dalla policy aziendale")

    gw = SimpleNamespace(
        defaults={"locale": "it"},
        gateway={"contact": "helpdesk@example.com", "proxy_messages": {"marker": "[via]"}},
    )
    t = Texts(gw)
    assert "helpdesk@example.com" in t.refusal("pii.model") and t.get("marker") == "[via]"


# Decisions (no network)


@dataclass
class Gate:
    env: Env
    id: str
    proxy: ProxyService

    async def policy(self, security: dict[str, Any]) -> None:
        r = await self.env.client.patch(f"{GW}/{self.id}", json={"security": security}, headers=OP)
        assert r.status_code == 200, r.text
        self.env.svc.gateway.forget(self.id)

    async def events(self, application_id: str | None = None) -> list[Any]:
        await self.env.svc.gateway.idle()
        return list(await self.env.svc.store.security_events.query(application_id or self.id))

    async def records(self, application_id: str | None = None) -> list[Any]:
        await self.env.svc.gateway.idle()
        return list(await self.env.svc.store.gateway.recent(application_id or self.id))

    async def learn_cli(self, email: str = fx.EMAIL, org: str = fx.ORG) -> None:
        body = json.dumps(fx.oauth_profile(email=email, org=org)).encode()
        await self.proxy.learn(
            "oauth_profile", req("GET", "api.anthropic.com", "/api/oauth/profile"), 200, body
        )

    async def learn_desktop(self) -> None:
        body = json.dumps(fx.claude_account()).encode()
        await self.proxy.learn(
            "claude_account", req("GET", "claude.ai", "/api/account", cookie=cookie()), 200, body
        )

    async def send(self, r: Request) -> Any:
        adapter = self.proxy.adapter(r.method, r.host, r.path)
        assert adapter is not None
        return await self.proxy.decide(r, adapter)


async def _gate(env: Env, providers: tuple[str, ...] = ("anthropic",), **settings: Any) -> Gate:
    env.svc.settings.operator_key = OPERATOR_KEY
    for k, v in settings.items():
        setattr(env.svc.settings, k, v)
    r = await env.client.post(
        GW, json={"slug": "dev", "name": "Dev", "providers": list(providers)}, headers=OP
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    r = await env.client.post(
        f"{GW}/{gid}/people", json={"label": "Ada", "email": fx.EMAIL.upper()}, headers=OP
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == fx.EMAIL
    proxy = ProxyService(env.svc)
    await proxy.refresh()
    return Gate(env, gid, proxy)


@pytest.fixture
async def gate(env: Env) -> Gate:
    return await _gate(env)


def cli_message(text: str, **kw: Any) -> Request:
    return req(
        "POST",
        "api.anthropic.com",
        "/v1/messages",
        fx.messages_body(text, **kw),
        user_agent=fx.CLI_UA,
    )


async def test_an_email_belongs_to_one_live_key_across_gateways(gate: Gate) -> None:
    env = gate.env
    r = await env.client.post(
        GW, json={"slug": "other", "name": "Other", "providers": ["anthropic"]}, headers=OP
    )
    r = await env.client.post(
        f"{GW}/{r.json()['id']}/people", json={"label": "Ada", "email": fx.EMAIL}, headers=OP
    )
    assert r.status_code == 409 and r.json()["error"] == "email_taken"
    listed = (await env.client.get(f"{GW}/{gate.id}/people", headers=OP)).json()["items"]
    assert listed[0]["email"] == fx.EMAIL


async def test_an_unknown_account_is_blocked_with_a_restart_message(gate: Gate) -> None:
    d = await gate.send(cli_message("hello"))
    assert d.verdict is not None and d.pending is None
    assert b"does not recognise this account yet" in d.verdict.body
    # No default gateway: the event has no gateway to belong to (it is logged only).
    assert await gate.events() == []


async def test_unidentified_events_go_to_the_default_gateway(env: Env) -> None:
    gate = await _gate(env, proxy_default_gateway="dev")
    await gate.learn_cli(email="stranger@example.com")
    d = await gate.send(cli_message("hello"))
    assert d.verdict is not None  # block is the default mode
    events = await gate.events()
    assert [(e.control, e.rule, e.subject) for e in events] == [
        ("proxy.unidentified", "no_person", "stranger@example.com")
    ]


async def test_an_account_learned_on_desktop_is_recognised_on_the_cli(gate: Gate) -> None:
    # claude.ai's account uuid is the one Claude Code sends in metadata.user_id.
    await gate.learn_desktop()
    d = await gate.send(cli_message("hello"))
    assert d.verdict is None and d.pending is not None and d.pending.person.label == "Ada"


async def test_the_oauth_token_answer_identifies_a_signed_in_cli(gate: Gate) -> None:
    host, path = "platform.claude.com", "/v1/oauth/token"
    assert gate.proxy.connect(host, 443) == "intercept"
    assert gate.proxy.adapter("POST", host, path) is None  # identity only: never read or masked
    assert gate.proxy.adapter("POST", host, "/api/anything") is None
    kind = gate.proxy.learns("POST", host, path)
    assert kind == "oauth_signin"
    await gate.proxy.learn(
        kind, req("POST", host, path), 200, json.dumps(fx.token_answer()).encode()
    )
    d = await gate.send(cli_message("hello"))
    assert d.verdict is None and d.pending is not None and d.pending.person.label == "Ada"


async def test_the_requesters_own_email_is_not_personal_data(gate: Gate) -> None:
    await gate.learn_cli()
    await gate.policy({"modes": {"pii.model": "enforce"}, "pii": {"types": ["email"]}})
    own = await gate.send(cli_message(f"my e-mail is {fx.EMAIL.upper()}, fix the config"))
    assert own.verdict is None
    other = await gate.send(cli_message("write to bob@example.org about it"))
    assert other.verdict is not None and b"personal data" in other.verdict.body


async def test_a_learned_account_passes_and_its_secret_is_refused(gate: Gate) -> None:
    await gate.learn_cli()
    ok = await gate.send(cli_message("hello"))
    assert ok.verdict is None and ok.pending is not None
    assert ok.pending.surface == "claude_code" and ok.pending.person.label == "Ada"
    # Monitor (the default): recorded, not refused.
    seen = await gate.send(cli_message(f"key {fx.SECRET}"))
    assert seen.verdict is None
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    refused = await gate.send(cli_message(f"key {fx.SECRET}"))
    assert refused.verdict is not None and refused.verdict.status == 200
    assert (
        b"a secret or credential" in refused.verdict.body
        and fx.SECRET.encode() not in refused.verdict.body
    )
    events = await gate.events()
    assert {(e.control, e.action) for e in events} == {
        ("input.secrets", "logged"),
        ("input.secrets", "blocked"),
    }
    assert all(e.details["surface"] == "claude_code" for e in events)
    rows = await gate.records()
    assert [(r.outcome, r.control, r.surface) for r in rows] == [
        ("blocked", "input.secrets", "claude_code")
    ]


async def test_count_tokens_with_a_secret_is_answered_without_forwarding(gate: Gate) -> None:
    await gate.learn_cli()
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    r = req(
        "POST",
        "api.anthropic.com",
        "/v1/messages/count_tokens",
        fx.messages_body(fx.SECRET),
        user_agent=fx.CLI_UA,
    )
    d = await gate.send(r)
    assert d.verdict is not None and "input_tokens" in json.loads(d.verdict.body)
    assert await gate.records() == []  # a count is not a model request


async def test_an_account_outside_the_organisation_is_refused(env: Env) -> None:
    gate = await _gate(env, proxy_allowed_orgs={"anthropic": [fx.ORG]})
    await gate.learn_desktop()
    body = fx.chat_body("hello")
    other = req(
        "POST",
        "claude.ai",
        chat_path(),
        body,
        cookie=cookie(),
        x_organization_uuid=fx.OTHER_ORG,
        user_agent=fx.ELECTRON_UA,
    )
    d = await gate.send(other)
    assert d.verdict is not None and d.verdict.status == 403
    assert b"Account outside the company" in d.verdict.body
    events = await gate.events()
    assert [(e.control, e.details["org"]) for e in events] == [("proxy.org_refused", fx.OTHER_ORG)]
    ours = req(
        "POST",
        "claude.ai",
        chat_path(),
        body,
        cookie=cookie(),
        x_organization_uuid=fx.ORG,
        user_agent=fx.ELECTRON_UA,
    )
    assert (await gate.send(ours)).verdict is None
    # Claude Code: the organisation its account's profile named.
    await gate.learn_cli(org=fx.OTHER_ORG)
    assert (await gate.send(cli_message("hello"))).verdict is not None


async def test_record_mode_without_a_default_gateway_blocks(env: Env) -> None:
    gate = await _gate(env, proxy_unidentified="record")
    await gate.learn_cli(email="stranger@example.com")
    d = await gate.send(cli_message("hello"))
    assert d.verdict is not None and b"does not recognise this account yet" in d.verdict.body


async def test_record_mode_puts_unidentified_requests_on_the_default_gateway(env: Env) -> None:
    gate = await _gate(env, proxy_unidentified="record", proxy_default_gateway="dev")
    await gate.learn_cli(email="stranger@example.com")
    d = await gate.send(cli_message("hello"))
    assert d.verdict is None and d.pending is not None
    assert d.pending.gw.id == gate.id
    assert d.pending.person.label == "unidentified stranger@example.com"
    # The default gateway's rules apply to them.
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    refused = await gate.send(cli_message(f"k {fx.SECRET}"))
    assert refused.verdict is not None
    rows = await gate.records()
    assert [(r.outcome, r.person) for r in rows] == [
        ("blocked", "unidentified stranger@example.com")
    ]


async def test_the_console_setting_overrides_the_environment(gate: Gate) -> None:
    await gate.env.svc.store.console_settings.put(
        "proxy",
        {
            "unidentified": "record",
            "allowed_orgs": {"anthropic": [fx.ORG]},
            "default_gateway": gate.id,
        },
        "test",
    )
    await gate.proxy.refresh()
    assert gate.proxy.settings.unidentified == "record"
    assert gate.proxy.settings.allowed_orgs == {"anthropic": [fx.ORG]}
    assert gate.proxy.settings.default_gateway == gate.id


async def test_a_title_write_is_masked_when_secrets_are_enforced(gate: Gate) -> None:
    await gate.learn_desktop()
    await gate.policy({"modes": {"input.secrets": "enforce"}})
    path = f"/api/organizations/{fx.ORG}/chat_conversations/{fx.CONV}/title"
    body = {"message_content": f"la mia chiave è {fx.SECRET}", "recent_titles": []}
    d = await gate.send(
        req("POST", "claude.ai", path, body, cookie=cookie(), content_type="application/json")
    )
    assert isinstance(d.verdict, Rewrite)
    assert json.loads(d.verdict.body)["message_content"] == "la mia chiave è [bloccato]"
    events = await gate.events()
    assert [(e.control, e.action) for e in events] == [("input.secrets", "redacted")]
    # Sign-in paths are never touched.
    assert gate.proxy.adapter("POST", "claude.ai", "/api/auth/verify_google") is None


async def test_a_changed_shape_is_refused_and_raises_an_alert(gate: Gate) -> None:
    await gate.learn_desktop()
    d = await gate.send(
        req("POST", "claude.ai", chat_path(), {"message": "renamed"}, cookie=cookie())
    )
    assert d.verdict is not None and b"Format not recognised" in d.verdict.body
    events = await gate.events()
    assert [(e.control, e.rule) for e in events] == [("proxy.format_unknown", "claude.chat")]


async def test_the_pac_and_the_root_are_served_by_the_api(gate: Gate, tmp_path: Path) -> None:
    env = gate.env
    assert (await env.client.get("/agent/proxy.pac")).status_code == 404
    test = proxy_ca(tmp_path)
    env.svc.settings.proxy_public_address = "proxy.example.com:3128"
    env.svc.settings.proxy_ca_cert = test.cert
    r = await env.client.get("/agent/proxy.pac")
    assert r.status_code == 200 and r.headers["content-type"].startswith(
        "application/x-ns-proxy-autoconfig"
    )
    assert '"claude.ai"' in r.text and "PROXY proxy.example.com:3128" in r.text
    r = await env.client.get("/agent/proxy/ca.crt")
    assert r.status_code == 200 and r.content == test.root.read_bytes()
    der = await env.client.get("/agent/proxy/ca.crt?format=der")
    assert (
        x509.load_der_x509_certificate(der.content).subject
        == x509.load_pem_x509_certificate(r.content).subject
    )


# End to end: the engine, a fake upstream on TLS, PCs that trust the proxy's root


@dataclass
class Stack:
    gate: Gate
    up: FakeUpstream
    engine: Engine
    ca: TestCa

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.engine.port}"

    def client(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(proxy=self.url, verify=client_ssl(self.ca.root), timeout=10, **kw)


@contextlib.asynccontextmanager
async def stack(
    tmp_path: Path, providers: tuple[str, ...] = ("anthropic",), **settings: Any
) -> AsyncGenerator[Stack]:
    async with make_env(tmp_path) as env:
        gate = await _gate(env, providers, **settings)
        test = proxy_ca(tmp_path)
        up = FakeUpstream()
        async with serve_upstream(tmp_path, up) as (port, upstream_ca):
            engine = Engine(
                gate.proxy,
                ca.load(test.key, test.cert),
                host="127.0.0.1",
                port=0,
                trusted_ca=str(upstream_ca),
                overrides=dict.fromkeys(UPSTREAM_HOSTS, ("127.0.0.1", port)),
            )
            task = asyncio.create_task(engine.run())
            await asyncio.wait_for(engine.started.wait(), 10)
            try:
                yield Stack(gate, up, engine, test)
            finally:
                engine.stop()
                await asyncio.wait_for(task, 10)


async def test_end_to_end_claude_code_identity_usage_and_refusal(tmp_path: Path) -> None:
    async with stack(tmp_path) as s, s.client() as pc:
        await s.gate.policy({"modes": {"input.secrets": "enforce"}})
        r = await pc.get(
            "https://api.anthropic.com/api/oauth/profile", headers={"authorization": "Bearer fake"}
        )
        assert r.status_code == 200
        r = await pc.post(
            "https://api.anthropic.com/v1/messages",
            json=fx.messages_body("hello"),
            headers={"user-agent": fx.CLI_UA},
        )
        assert r.status_code == 200 and r.content == b"".join(fx.ANTHROPIC_SSE)
        r = await pc.post(
            "https://api.anthropic.com/v1/messages",
            json=fx.messages_body(f"k {fx.SECRET}"),
            headers={"user-agent": fx.CLI_UA},
        )
        assert r.status_code == 200 and b"a secret or credential" in r.content
        assert r.headers["x-evokai-proxy"] == "refused"
        sent = [x for x in s.up.seen if x.path == "/v1/messages"]
        assert len(sent) == 1 and fx.SECRET.encode() not in sent[0].body
        rows = await s.gate.records()
        assert sorted((x.outcome, x.input_tokens, x.output_tokens) for x in rows) == [
            ("blocked", 0, 0),
            ("forwarded", 142, 7),
        ]


async def test_end_to_end_a_token_refresh_identifies_the_cli(tmp_path: Path) -> None:
    async with stack(tmp_path) as s, s.client() as pc:
        r = await pc.post(
            "https://platform.claude.com/v1/oauth/token",
            json={"grant_type": "refresh_token", "refresh_token": "fake-refresh"},
        )
        assert r.status_code == 200 and r.json() == fx.token_answer()  # passed on unchanged
        sent = next(x for x in s.up.seen if x.path == "/v1/oauth/token")
        assert json.loads(sent.body)["refresh_token"] == "fake-refresh"  # never masked
        r = await pc.post(
            "https://api.anthropic.com/v1/messages",
            json=fx.messages_body("hello"),
            headers={"user-agent": fx.CLI_UA},
        )
        assert r.content == b"".join(fx.ANTHROPIC_SSE)


async def test_end_to_end_other_hosts_are_refused_and_the_check_host_answers(
    tmp_path: Path,
) -> None:
    async with stack(tmp_path) as s:
        async with s.client() as pc:
            with pytest.raises(httpx.ProxyError, match="403"):
                await pc.get("https://example.com/")
            r = await pc.get("https://evokai.check/")
            assert r.status_code == 200 and "goes through the company" in r.text
            assert "trusted (HTTPS)" in r.text
        async with httpx.AsyncClient(proxy=s.url, timeout=10) as plain:
            r = await plain.get("http://evokai.check/")
            assert r.status_code == 200 and "Opened over HTTP" in r.text
            r = await plain.get("http://claude.ai/")
            assert r.status_code == 403


async def test_end_to_end_http2_split_cookies_chat_rewrite_and_cowork(tmp_path: Path) -> None:
    async with stack(tmp_path) as s:
        await s.gate.policy({"modes": {"input.secrets": "enforce"}})
        # Over HTTP/2 every cookie is a header of its own, as Electron sends them.
        h = [
            ("cookie", "lastActiveOrg=" + fx.ORG),
            ("cookie", cookie()),
            ("cookie", "anthropic-device-id=fake"),
            ("user-agent", fx.ELECTRON_UA),
            ("x-organization-uuid", fx.ORG),
        ]
        body = fx.gz(fx.chat_body(f"la mia chiave è {fx.SECRET}"))
        gz = [("content-encoding", "gzip"), ("content-type", "application/json")]
        (account, completion) = await h2_exchange(
            s.engine.port,
            client_ssl(s.ca.root),
            "claude.ai",
            [("GET", "/api/account", h, b""), ("POST", chat_path(), h + gz, body)],
        )
        assert account[0] == 200
        assert completion[0] == 200 and b"message_stop" in gzip.decompress(completion[2])
        chat = next(x for x in s.up.seen if x.path.endswith("/completion"))
        sent = json.loads(chat.body)
        assert fx.SECRET not in json.dumps(sent) and "a secret or credential" in sent["prompt"]
        assert chat.headers.get("content-encoding") == "gzip"  # re-encoded as it came
        cowork = json.dumps(fx.cowork_body(f"chiave {fx.SECRET}", with_reminder=True)).encode()
        path = f"/v1/code/sessions/{fx.SESSION}/events"
        json_type = [("content-type", "application/json")]
        (ack,) = await h2_exchange(
            s.engine.port,
            client_ssl(s.ca.root),
            "claude.ai",
            [("POST", path, h + json_type, cowork)],
        )
        assert ack[0] == 200
        ev = json.loads(next(x for x in s.up.seen if x.path.endswith("/events")).body)
        users = [
            e["payload"]["message"]["content"]
            for e in ev["events"]
            if e["payload"]["type"] == "user"
        ]
        assert users[0].startswith("<system-reminder>") and users[1].startswith("[Message removed")
        rows = await s.gate.records()
        assert {(x.surface, x.outcome) for x in rows} == {
            ("desktop_chat", "blocked"),
            ("desktop_cowork", "blocked"),
        }


async def test_end_to_end_a_never_ending_stream_is_not_buffered(tmp_path: Path) -> None:
    async with stack(tmp_path) as s, s.client() as pc:
        url = f"https://claude.ai/v1/code/sessions/{fx.SESSION}/events/stream"
        async with pc.stream("GET", url, headers={"cookie": cookie()}) as r:
            first = await asyncio.wait_for(anext(r.aiter_raw()), 5)
        assert b"client_event" in first


async def test_end_to_end_the_bridge_tool_result_is_replaced(tmp_path: Path) -> None:
    async with stack(tmp_path) as s:
        await s.gate.policy({"modes": {"input.secrets": "enforce"}})
        async with s.client() as pc:
            await pc.get("https://claude.ai/api/account", headers={"cookie": cookie()})
        ctx = client_ssl(s.ca.root)
        async with websockets.connect(
            "wss://bridge.claudeusercontent.com/bridge", proxy=s.url, ssl=ctx
        ) as ws:
            await ws.send(fx.bridge_result("plain file").decode())
            assert "plain file" in str(await asyncio.wait_for(ws.recv(), 5))
            await ws.send(fx.bridge_result(f"Chiave finta: {fx.SECRET}").decode())
            back = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert (
            fx.SECRET not in json.dumps(back)
            and "blocked by the company policy" in back["result"]["content"][0]["text"]
        )
        assert all(fx.SECRET not in f for f in s.up.frames)


async def test_end_to_end_a_body_over_the_limit_is_refused(tmp_path: Path) -> None:
    async with stack(tmp_path, proxy_max_body_mb=1) as s, s.client() as pc:
        s.gate.proxy.max_body = 1024 * 1024
        await pc.get("https://api.anthropic.com/api/oauth/profile")
        big = fx.messages_body("x" * (2 * 1024 * 1024))
        r = await pc.post(
            "https://api.anthropic.com/v1/messages", json=big, headers={"user-agent": fx.CLI_UA}
        )
        assert r.status_code == 200 and b"too large for the company policy" in r.content
        assert not [x for x in s.up.seen if x.path == "/v1/messages"]


async def test_the_gateway_module_off_turns_the_proxy_and_its_files_off(
    gate: Gate, tmp_path: Path
) -> None:
    from evokai.core import modules
    from evokai.gateway.proxy.run import gateway_module_on

    env = gate.env
    env.svc.settings.proxy_public_address = "proxy.example.com:3128"
    env.svc.settings.proxy_ca_cert = proxy_ca(tmp_path).cert
    assert await gateway_module_on(env.svc)
    await modules.save(env.svc.store, ["applications"], "test")
    assert not await gateway_module_on(env.svc)
    for path in ("/agent/proxy.pac", "/agent/proxy/ca.crt"):
        r = await env.client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "module_disabled"
    await gate.proxy.refresh()
    assert gate.proxy.providers == [] and gate.proxy.connect("claude.ai", 443) is None


async def test_end_to_end_a_body_that_cannot_be_decoded_is_refused(tmp_path: Path) -> None:
    async with stack(tmp_path) as s, s.client() as pc:
        await pc.get("https://api.anthropic.com/api/oauth/profile")
        r = await pc.post(
            "https://api.anthropic.com/v1/messages",
            content=b"not gzip at all",
            headers={"content-encoding": "gzip", "content-type": "application/json"},
        )
        assert r.status_code == 200 and b"Format not recognised" in r.content
        assert not [x for x in s.up.seen if x.path == "/v1/messages"]
