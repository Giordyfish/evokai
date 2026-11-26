"""Azure models without keys: Azure OpenAI, the Foundry v1 endpoint, Claude in Foundry, embeddings.

No Azure account is used: every request goes to a mock transport shaped from the official REST
references (Azure OpenAI data plane, the Foundry ``/anthropic`` Messages API), and the Entra ID
token comes from a fake credential.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx2
import pytest
from azure.core.credentials import AccessToken
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel

from evokai.core import azure
from evokai.core.errors import BadRequest, ConfigError
from evokai.knowledge import embeddings as emb
from evokai.llm import catalog
from evokai.llm import providers as pc
from evokai.llm.catalog import ModelCatalog, anthropic_foundry_model, azure_provider
from evokai.protection import DataProtection, model_verdict

REGION = "eu-north-1"


class FakeCredential:
    """A token per scope, like ``DefaultAzureCredential`` with a managed identity."""

    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def get_token(self, *scopes: str, **_: Any) -> AccessToken:
        self.scopes += scopes
        return AccessToken(f"tok:{scopes[0]}", int(time.time()) + 3600)

    async def close(self) -> None:
        return None


@pytest.fixture
def cred() -> Any:
    fake = FakeCredential()
    azure.set_credential(fake)
    yield fake
    azure.set_credential(None)


def _chat(seen: list[httpx2.Request]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-5.4",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ciao"},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def _ask(model: Any) -> str:
    return (await Agent(model).run("hi")).output


async def test_azure_openai_dated_endpoint_with_a_managed_identity(
    cred: FakeCredential, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    seen: list[httpx2.Request] = []
    provider = azure_provider(
        "https://res.openai.azure.com", "2024-10-21", None, http_client=_chat(seen)
    )
    assert await _ask(OpenAIChatModel("gpt-54", provider=provider)) == "ciao"
    (req,) = seen
    assert req.url.path == "/openai/deployments/gpt-54/chat/completions"
    assert req.url.params["api-version"] == "2024-10-21"
    assert req.headers["authorization"] == f"Bearer tok:{azure.COGNITIVE_SERVICES}"
    assert "api-key" not in req.headers
    assert cred.scopes == [azure.COGNITIVE_SERVICES]


@pytest.mark.usefixtures("cred")
async def test_foundry_v1_endpoint_takes_no_api_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    seen: list[httpx2.Request] = []
    provider = azure_provider(
        "https://res.services.ai.azure.com/openai/v1/", None, None, http_client=_chat(seen)
    )
    # A Foundry model sold by Azure (DeepSeek, Llama, Mistral...) on the same endpoint.
    assert await _ask(OpenAIChatModel("DeepSeek-V3.2", provider=provider)) == "ciao"
    (req,) = seen
    assert str(req.url) == "https://res.services.ai.azure.com/openai/v1/chat/completions"
    assert json.loads(req.content)["model"] == "DeepSeek-V3.2"
    assert req.headers["authorization"] == f"Bearer tok:{azure.COGNITIVE_SERVICES}"
    with pytest.raises(ConfigError, match="takes no api_version"):
        azure_provider("https://res.openai.azure.com/openai/v1", "2024-10-21", None)
    with pytest.raises(ConfigError, match="needs an api_version"):
        azure_provider("https://res.openai.azure.com", None, None)


async def test_a_key_still_wins_over_entra(cred: FakeCredential) -> None:
    seen: list[httpx2.Request] = []
    provider = azure_provider(
        "https://res.openai.azure.com", "2024-10-21", "secret", http_client=_chat(seen)
    )
    assert await _ask(OpenAIChatModel("gpt-54", provider=provider)) == "ciao"
    assert seen[0].headers["api-key"] == "secret" and "authorization" not in seen[0].headers
    assert cred.scopes == []


def _messages(seen: list[httpx2.Request]) -> httpx2.AsyncClient:
    # The Messages API as server-sent events (pydantic-ai streams): message_start, one text block,
    # message_delta, message_stop.
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [],
            "stop_reason": None,
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
        events = [
            {"type": "message_start", "message": message},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ciao"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        sse = "".join(f"event: {d['type']}\ndata: {json.dumps(d)}\n\n" for d in events)
        return httpx2.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def test_claude_in_foundry_with_a_managed_identity_or_a_key(cred: FakeCredential) -> None:
    base = "https://res.services.ai.azure.com/anthropic"
    seen: list[httpx2.Request] = []
    model = anthropic_foundry_model("claude-sonnet-5-5", base, None, http_client=_messages(seen))
    assert isinstance(model, AnthropicModel)
    assert await _ask(model) == "ciao"
    (req,) = seen
    assert str(req.url) == f"{base}/v1/messages"
    assert req.headers["authorization"] == f"Bearer tok:{azure.FOUNDRY}"
    assert "x-api-key" not in req.headers
    assert json.loads(req.content)["model"] == "claude-sonnet-5-5"

    seen.clear()
    keyed = anthropic_foundry_model("claude-haiku-5-5", base, "k", http_client=_messages(seen))
    assert await _ask(keyed) == "ciao"
    assert seen[0].headers["x-api-key"] == "k"
    assert cred.scopes == [azure.FOUNDRY]


FOUNDRY = """
providers:
  - id: anthropic-foundry
    title: Claude in our Foundry resource
    endpoints:
      - id: ours-us
        like: foundry-claude-azure-datazone-us
        models: ["anthropic-foundry:*"]
        api: anthropic-foundry
        base_url: https://ours.services.ai.azure.com/anthropic
        declared_by: operator
  - id: azure
    title: Azure OpenAI
    endpoints:
      - id: ours-sweden
        like: azure-datazone-eu
        models: ["azure:*"]
        api: azure
        base_url: https://ours.openai.azure.com/openai/v1
        declared_by: operator
"""


@pytest.mark.usefixtures("cred")
def test_the_catalog_builds_both_and_a_profile_tells_them_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    path = tmp_path / "providers.yaml"
    path.write_text(FOUNDRY, encoding="utf-8")
    cat = pc.configure(path, REGION)
    try:
        models = ModelCatalog(["*"], REGION, providers=cat)
        claude = models.get("anthropic-foundry:claude-sonnet-5-5")
        assert isinstance(claude, AnthropicModel) and claude.system == "anthropic"
        assert str(claude.client.base_url).startswith(
            "https://ours.services.ai.azure.com/anthropic"
        )
        gpt = models.get("azure:gpt-54")
        assert isinstance(gpt, OpenAIChatModel)
        assert gpt.base_url.startswith("https://ours.openai.azure.com/openai/v1")

        facts = cat.model_facts("anthropic-foundry:claude-sonnet-5-5")
        assert facts is not None and facts.jurisdiction == "US" and facts.geography == "us"
        eu = {
            "data_protection": DataProtection.model_validate({
                "residency": {"geographies": ["eu"]}
            }).stored()
        }
        # Claude in Foundry has no EU data zone: an EU-only profile keeps it out, the Azure
        # OpenAI EU data zone passes.
        assert not model_verdict(eu, "anthropic-foundry:claude-sonnet-5-5", REGION).allowed
        assert model_verdict(eu, "azure:gpt-54", REGION).allowed
        us = {"data_protection": DataProtection.model_validate({"jurisdiction": ["eu"]}).stored()}
        assert not model_verdict(us, "anthropic-foundry:claude-sonnet-5-5", REGION).allowed
    finally:
        pc.configure(None, REGION)


def test_the_built_in_entry_says_where_each_hosting_option_runs() -> None:
    p = pc.ProviderCatalog.load().providers["anthropic-foundry"]
    assert p.entity.name == "Anthropic, PBC" and p.entity.controlled_from == "US"
    places = {e.id: e.place.geography for e in p.endpoints}
    assert places == {
        "foundry-claude-azure-global": "global",
        "foundry-claude-azure-datazone-us": "us",
        "foundry-claude-anthropic-hosted": "global",
    }
    assert "not verified against a live azure account" in (p.notes or "").lower()


@pytest.mark.usefixtures("cred")
def test_models_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://env.openai.azure.com")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-10-21")
    gpt = catalog.from_environment("azure:gpt-54")
    assert isinstance(gpt, OpenAIChatModel) and "env.openai.azure.com" in gpt.base_url
    # With a key, pydantic-ai's own construction applies.
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    assert catalog.from_environment("azure:gpt-54") is None

    with pytest.raises(ConfigError, match="ANTHROPIC_FOUNDRY_BASE_URL"):
        catalog.from_environment("anthropic-foundry:claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "envres")
    claude = catalog.from_environment("anthropic-foundry:claude-haiku-5-5")
    assert isinstance(claude, AnthropicModel)
    assert str(claude.client.base_url) == "https://envres.services.ai.azure.com/anthropic/"
    assert catalog.from_environment("bedrock:x") is None


def test_without_the_azure_extra_the_error_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    azure.set_credential(None)
    monkeypatch.setitem(sys.modules, "azure.identity.aio", None)
    with pytest.raises(ConfigError, match="'azure' extra"):
        azure.token_provider(azure.COGNITIVE_SERVICES)


@pytest.mark.usefixtures("cred")
async def test_azure_embeddings_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        n = len(json.loads(request.content)["input"])
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-large",
                "data": [
                    {"object": "embedding", "index": i, "embedding": [0.1] * 256} for i in range(n)
                ],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            },
        )

    real = catalog.azure_provider
    monkeypatch.setattr(
        catalog,
        "azure_provider",
        lambda *a, **k: real(
            *a, **k, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        ),
    )
    endpoint = pc.Endpoint(
        id="ours", api="azure", base_url="https://ours.openai.azure.com", api_version="2024-10-21"
    )
    embedder = emb.openai_embedder("azure:te3", 256, endpoint)
    assert embedder is not None
    out = await embedder.embed(["uno", "due"], "document")
    assert len(out.vectors) == 2 and len(out.vectors[0]) == 256 and out.tokens == 7
    (req,) = seen
    assert req.url.path == "/openai/deployments/te3/embeddings"
    assert json.loads(req.content)["dimensions"] == 256
    assert req.headers["authorization"] == f"Bearer tok:{azure.COGNITIVE_SERVICES}"
    # Nothing connects a model of another provider.
    assert emb.openai_embedder("cohere:embed", 256, None) is None


async def test_a_self_hosted_embedder_must_make_the_base_size() -> None:
    class Model:
        async def embed(self, texts: list[str], **_: Any) -> Any:
            from types import SimpleNamespace

            return SimpleNamespace(
                embeddings=[[0.0] * 768 for _ in texts], usage=SimpleNamespace(input_tokens=1)
            )

    endpoint = pc.Endpoint(id="tei", api="openai-compatible", base_url="http://tei:8080/v1")
    built = emb.openai_embedder("our-tei:bge-m3", 1024, endpoint)
    assert isinstance(built, emb.OpenAIEmbedder) and not built.ask_dimensions
    built.model = Model()
    with pytest.raises(BadRequest, match="768 dimensions"):
        await built.embed(["x"], "query")
