"""Prompt caching beyond Anthropic on Bedrock: each provider's own way, or nothing to ask."""

from __future__ import annotations

import json
from typing import Any, cast

import boto3
import httpx2
from anthropic import AsyncAnthropic
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.bedrock import BedrockProvider
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.providers.openai import OpenAIProvider

from evokai.llm.caching import cache_label, cache_settings, cache_style


def _bedrock(model_id: str) -> BedrockConverseModel:
    client = boto3.client(
        "bedrock-runtime",
        region_name="eu-north-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    return BedrockConverseModel(model_id, provider=BedrockProvider(bedrock_client=client))


def test_each_provider_gets_its_own_cache_settings() -> None:
    haiku = _bedrock("eu.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert cache_style(haiku, "bedrock:eu.anthropic.claude-haiku-4-5-20251001-v1:0") == "bedrock"
    nova = _bedrock("eu.amazon.nova-2-lite-v1:0")
    # Amazon Nova caches too: 5 minutes only, and not the tool definitions.
    assert cache_settings(nova, "bedrock:eu.amazon.nova-2-lite-v1:0", "1h", conversation=True) == {
        "bedrock_cache_instructions": "5m",
        "bedrock_cache_messages": "5m",
    }
    llama = _bedrock("eu.meta.llama3-2-3b-instruct-v1:0")
    assert cache_style(llama, "bedrock:eu.meta.llama3-2-3b-instruct-v1:0") == "none"
    assert (
        cache_settings(llama, "bedrock:eu.meta.llama3-2-3b-instruct-v1:0", "5m", conversation=True)
        == {}
    )

    claude = AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(api_key="k"))
    assert cache_settings(claude, "anthropic:claude-haiku-4-5", "1h", conversation=True) == {
        "anthropic_cache_instructions": "1h",
        "anthropic_cache_tool_definitions": "1h",
        "anthropic_cache_messages": "5m",
    }
    explicit = OpenAIResponsesModel("gpt-5.6-sol", provider=OpenAIProvider(api_key="k"))
    assert cache_settings(explicit, "openai:gpt-5.6-sol", "5m", conversation=False) == {
        "cache": {"retention": "5m", "messages": False}
    }
    # OpenAI's older models, Mistral, Gemini: the provider caches by itself; nothing to send.
    older = OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(api_key="k"))
    assert cache_style(older, "openai:gpt-4.1") == "automatic"
    assert cache_settings(older, "openai:gpt-4.1", "1h", conversation=True) == {}
    assert cache_label({}, older, "openai:gpt-4.1", "1h") == "auto"
    mistral = MistralModel("mistral-large-latest", provider=MistralProvider(api_key="k"))
    assert cache_style(mistral, "mistral:mistral-large-latest") == "automatic"
    # Off is off, whatever the provider.
    assert cache_settings(claude, "anthropic:claude-haiku-4-5", "off", conversation=True) == {}
    fn = FunctionModel(lambda *_: ModelResponse(parts=[]))
    assert cache_style(fn, "function:x") == "none"


async def test_the_anthropic_api_receives_cache_breakpoints() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        usage = {
            "input_tokens": 10,
            "output_tokens": 2,
            "cache_read_input_tokens": 4000,
            "cache_creation_input_tokens": 0,
        }
        message = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [],
            "stop_reason": None,
            "usage": usage,
        }
        events = [
            ("message_start", {"type": "message_start", "message": message}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "ok"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": 2},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        sse = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)
        return httpx2.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    client = AsyncAnthropic(
        api_key="k", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    model = AnthropicModel("claude-haiku-4-5", provider=AnthropicProvider(anthropic_client=client))
    settings = cache_settings(model, "anthropic:claude-haiku-4-5", "5m", conversation=False)
    request = ModelRequest(parts=[UserPromptPart("Hi")], instructions="Stable instructions.")
    params = model.customize_request_parameters(ModelRequestParameters())
    response = await model.request([request], cast(Any, settings), params)
    system = bodies[0]["system"]
    assert any("cache_control" in block for block in system)
    # The cached tokens the provider reports are read into the usage Evokai records and prices.
    assert response.usage.cache_read_tokens == 4000
