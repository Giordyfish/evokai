"""Hard rule: conversations are never stored at model providers.

Every request carries the full history from Evokai's store (or the application's); no provider-side
threads, conversations, stored responses, response chaining or memory features are ever used.

- ``openai_store`` is forced to False on every call (OpenAI Chat and Responses).
- Settings that chain or keep provider-side state are refused in agent definitions and stripped
  from every request.

Prompt caching (``evokai.llm.caching``, the agent's ``prompt_cache``) is not provider-side state in
this sense: it is a short-lived cache of the request's own prefix, with no id to address it, and
every request still sends the whole history. Its Bedrock settings are added by Evokai per model,
never taken from an agent's ``model_settings``.
"""

from __future__ import annotations

from typing import Any

FORBIDDEN_SETTINGS = frozenset({
    "openai_previous_response_id",
    "openai_conversation_id",
    "openai_store",
    "openai_truncation",
    "anthropic_container",
    "google_cached_content",
})
"""Keys that would create or reuse provider-side conversation state."""

ALLOWED_SETTINGS = frozenset({
    "temperature",
    "top_p",
    "max_tokens",
    "seed",
    "parallel_tool_calls",
    "timeout",
    "openai_reasoning_effort",
    "openai_reasoning_summary",
    "openai_service_tier",
    "anthropic_thinking",
    "anthropic_effort",
    "bedrock_performance_configuration",
    "bedrock_additional_model_requests_fields",
    "google_thinking_config",
    "thinking",
})
"""Per-agent options: sampling, reasoning effort, priority/flex processing."""

ENFORCED: dict[str, Any] = {"openai_store": False}


def check_agent_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Validate an agent's model settings.

    Args:
        settings: The agent's settings.

    Returns:
        The settings.

    Raises:
        ValueError: A storage-related or unknown key.
    """
    bad = set(settings) & FORBIDDEN_SETTINGS
    if bad:
        raise ValueError(f"provider-side storage is not allowed: {sorted(bad)}")
    unknown = set(settings) - ALLOWED_SETTINGS
    if unknown:
        raise ValueError(f"unknown model settings: {sorted(unknown)}")
    return settings


def request_settings(agent_settings: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """The settings of one model request: the agent's options, then the enforced ones.

    Args:
        agent_settings: The agent's (validated) settings.
        max_tokens: Default output cap.

    Returns:
        Settings with storage forced off and no chaining keys.
    """
    out = {"max_tokens": max_tokens, **agent_settings}
    for key in FORBIDDEN_SETTINGS:
        out.pop(key, None)
    out.update(ENFORCED)
    return out


def enforce_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """The settings of one attempt at a model call, whatever added them (an agent, a capability).

    Applied by the engine just before each attempt (``evokai.engine.capability``), after every
    other contribution, so no capability can turn provider-side storage or chaining on.

    Returns:
        The settings without chaining keys and with storage off.
    """
    out = {k: v for k, v in settings.items() if k not in FORBIDDEN_SETTINGS}
    out.update(ENFORCED)
    return out


def agent_settings(agent: Any) -> dict[str, Any]:
    """An agent's model settings with its temperature, if set.

    Returns:
        Settings for :func:`request_settings`.
    """
    out = dict(agent.model_settings)
    if agent.temperature is not None:
        out["temperature"] = agent.temperature
    return out
