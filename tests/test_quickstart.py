"""The five-minute install: keys on first start, migrations at start, the setup wizard's APIs."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from evokai import cli
from evokai.core.config import Settings
from evokai.core.state import KEYS_FILE, apply_state, banner
from evokai.llm.catalog import ModelCatalog, normalize
from evokai.llm.prices import cost_micro_usd
from evokai.llm.suggestions import suggestions
from tests.conftest import MCP_URL, Env

CONSOLE = {"X-Evokai-Console": "1"}
OPERATOR_KEY = "evk_op_test_operator_key_0123456789"  # gitleaks:allow


def test_first_start_writes_the_keys_once_and_reads_them_back(tmp_path: Path) -> None:
    first = apply_state(Settings(state_dir=str(tmp_path)))
    assert first.created and first.operator_key and first.operator_key.startswith("evk_op_")
    s = first.settings
    assert s.signing_key and s.encryption_key and s.operator_key == first.operator_key
    path = tmp_path / KEYS_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    again = apply_state(Settings(state_dir=str(tmp_path)))
    assert not again.created and again.operator_key is None
    assert again.settings.signing_key == s.signing_key
    assert again.settings.operator_key == s.operator_key
    assert "shown only now" in banner(s, first.operator_key)
    assert first.operator_key not in banner(s, None)

    rotated = apply_state(Settings(state_dir=str(tmp_path)), rotate_operator=True)
    assert rotated.operator_key and rotated.operator_key != first.operator_key
    assert rotated.settings.signing_key == s.signing_key


def test_keys_from_the_environment_win_over_the_file(tmp_path: Path) -> None:
    out = apply_state(
        Settings(state_dir=str(tmp_path), signing_key="from-env", operator_key="evk_op_env")
    )
    assert out.settings.signing_key == "from-env"
    assert out.settings.operator_key == "evk_op_env"
    assert out.operator_key is None  # the stored one is not in use: nothing to announce
    assert json.loads((tmp_path / KEYS_FILE).read_text())["signingKey"] != "from-env"


def test_without_a_state_directory_nothing_changes() -> None:
    s = Settings()
    assert apply_state(s).settings is s


def test_init_generates_keys_and_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("EVOKAI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("EVOKAI_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'e.db'}")
    cli.get_settings.cache_clear()
    try:
        assert cli.main(["init"]) == 0
    finally:
        cli.get_settings.cache_clear()
    out = capsys.readouterr().out
    assert "Operator key:  evk_op_" in out and "/agent/admin/" in out
    assert (tmp_path / KEYS_FILE).is_file() and (tmp_path / "e.db").is_file()


def test_local_models_cost_nothing_and_the_test_model_needs_no_provider() -> None:
    assert cost_micro_usd("ollama:qwen3:8b", 10_000, 1_000) == 0
    assert cost_micro_usd("eu.anthropic.claude-sonnet-4-6", 10_000, 1_000) > 0
    catalog = ModelCatalog(["test:*"], "eu-north-1")
    assert normalize("test:canned") == "test:canned"
    assert catalog.get("test:canned").model_name == "test"


async def test_suggestions_follow_the_allow_list_and_the_credentials() -> None:
    def ollama(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(ollama))
    catalog = ModelCatalog(["openai:*", "ollama:*", "test:*"], "eu-north-1")
    items = await suggestions(
        catalog, http, {"OPENAI_API_KEY": "sk-x", "OLLAMA_BASE_URL": "http://h:11434/v1"}
    )
    ids = [str(i["id"]) for i in items]
    assert "ollama:qwen3:8b" in ids and "openai:gpt-5-mini" in ids and "test:canned" in ids
    assert not any(i.startswith(("bedrock", "mistral")) for i in ids)
    assert all(i["ready"] for i in items)
    none = await suggestions(catalog, http, {})
    assert {i["id"]: i["ready"] for i in none}["openai:gpt-5-mini"] is False
    await http.aclose()


async def test_the_wizard_apis(env: Env) -> None:
    env.svc.settings.operator_key = OPERATOR_KEY
    r = await env.client.post("/agent/v1/console/login", json={"key": OPERATOR_KEY})
    assert r.status_code == 204

    models = (await env.client.get("/agent/v1/console/models", headers=CONSOLE)).json()
    assert models["allow"] == env.svc.settings.model_allow
    assert all(env.svc.catalog.allowed(m["id"]) for m in models["items"])

    probe = await env.client.post(
        "/agent/v1/operator/mcp-test", json={"url": MCP_URL, "token": "t"}, headers=CONSOLE
    )
    body = probe.json()
    assert body["ok"] and {t["name"] for t in body["tools"]} >= {"orders_month", "order_refund"}
    assert env.mcp.auth[-1] == "Bearer t"
    env.mcp.down = True
    down = await env.client.post(
        "/agent/v1/operator/mcp-test", json={"url": MCP_URL}, headers=CONSOLE
    )
    assert down.json()["ok"] is False

    text = "---\nname: tone\ndescription: Short: warm, no jargon.\n---\nUse plain words.\n"
    parsed = await env.client.post(
        "/agent/v1/console/skills/parse", json={"text": text}, headers=CONSOLE
    )
    assert parsed.json() == {
        "name": "tone",
        "description": "Short: warm, no jargon.",
        "body": "Use plain words.",
        "size": 16,
    }
    bad = await env.client.post(
        "/agent/v1/console/skills/parse", json={"text": "no front matter"}, headers=CONSOLE
    )
    assert (bad.status_code, bad.json()["error"]) == (400, "skill_format")


async def test_only_the_operator_probes_mcp_servers(env: Env) -> None:
    await env.client.post("/agent/v1/console/login", json={"key": env.admin_key})
    r = await env.client.post("/agent/v1/operator/mcp-test", json={"url": MCP_URL}, headers=CONSOLE)
    assert r.status_code == 401
    anon = await httpx.AsyncClient(transport=env.client._transport, base_url="http://evokai").get(
        "/agent/v1/console/models"
    )
    assert anon.status_code == 401
