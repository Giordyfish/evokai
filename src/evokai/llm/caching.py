"""Prompt caching: an agent option, applied to every provider that supports it.

With ``prompt_cache: 5m`` or ``1h`` an agent's requests ask the provider to cache what is stable:
the tool definitions, the stable part of the system prompt (``evokai.agents.prompts``) and, when the
agent has tools so that one call can be followed by another, the conversation so far (always with
the 5 minute TTL: that part is only read again seconds or minutes later). How depends on the
provider (``cache_style``):

- ``bedrock``: Bedrock cache points (``bedrock_cache_*``) on every Bedrock model whose profile
  supports prompt caching: Anthropic Claude (tools too, 5 minutes or 1 hour where the model offers
  it) and Amazon Nova (5 minutes, no tool caching).
- ``anthropic``: Anthropic's ``cache_control`` breakpoints (``anthropic_cache_*``) on the Anthropic
  API, and on Anthropic models reached through it on other clouds (Vertex), 5 minutes or 1 hour.
- ``unified``: pydantic-ai's provider-neutral ``cache`` setting, for the models whose profile
  accepts it (OpenAI's models with explicit breakpoints, Azure OpenAI, Anthropic models through
  OpenRouter); the TTL snaps to the provider's nearest tier.
- ``automatic``: providers that cache a repeated prefix by themselves (OpenAI's other models,
  Gemini's implicit caching, DeepSeek, Mistral, Groq, Scaleway): nothing is sent, the cached tokens
  are read from the usage.
- ``none``: nothing to ask, nothing reported.

This is a short-lived prefix cache, not provider-side conversation state: nothing is addressed by an
id, every request still carries the whole history, and an entry expires by itself. Gemini's
*explicit* context caching is a cached content resource created at the provider and addressed by its
name (``google_cached_content``): the hard rule of ``evokai.llm.policy`` refuses it, so Gemini is
cached only implicitly.

A fallback chain that mixes models just gets each model's own style. Cache read and write tokens are
recorded for every provider that reports them (pydantic-ai's ``RequestUsage``): usage, traces and
prices (``evokai.llm.prices``).
"""

from __future__ import annotations

from typing import Any, Literal

from evokai.llm.catalog import bare, normalize, provider_of

PromptCache = Literal["off", "5m", "1h"]
CacheStyle = Literal["bedrock", "anthropic", "unified", "automatic", "none"]

CONVERSATION_TTL = "5m"
"""TTL of the cache point at the end of the conversation (read again in the same turn or run)."""

AUTOMATIC = frozenset({
    "openai",
    "openai-chat",
    "openai-responses",
    "openai-eu",
    "azure",
    "google-gla",
    "google-vertex",
    "google",
    "google-cloud",
    "deepseek",
    "mistral",
    "mistral-eu",
    "mistral-us",
    "groq",
    "scaleway",
    "openrouter",
})
"""Providers that cache a repeated prefix without being asked and report the cached tokens (the
provider catalog's ``caching`` facts)."""


def _profile(model: Any) -> dict[str, Any]:
    return dict(getattr(model, "profile", None) or {})


def cache_style(model: Any, name: str) -> CacheStyle:
    """How Evokai asks this model for prompt caching.

    Args:
        model: The pydantic-ai model object.
        name: Its name (``bedrock:eu.anthropic...``).

    Returns:
        The style (see the module).
    """
    profile = _profile(model)
    system = getattr(model, "system", None)
    if normalize(name).startswith("bedrock:") or system == "bedrock":
        return "bedrock" if profile.get("bedrock_supports_prompt_caching", False) else "none"
    if system == "anthropic":
        return "anthropic"
    if profile.get("supports_cache", False):
        return "unified"
    if provider_of(name) in AUTOMATIC:
        return "automatic"
    return "none"


def caches(model: Any, name: str) -> bool:
    """Whether Evokai asks this model for prompt caching (a style that takes settings).

    Returns:
        True for the ``bedrock``, ``anthropic`` and ``unified`` styles.
    """
    return cache_style(model, name) in ("bedrock", "anthropic", "unified")


def _ttl(profile: dict[str, Any], prompt_cache: str) -> str:
    supported = tuple(profile.get("supported_cache_retentions") or ("5m",))
    return prompt_cache if prompt_cache in supported else "5m"


def cache_settings(
    model: Any, name: str, prompt_cache: PromptCache, *, conversation: bool
) -> dict[str, Any]:
    """The model settings that turn prompt caching on for one request.

    Args:
        model: The pydantic-ai model object.
        name: Its name.
        prompt_cache: The agent's option.
        conversation: Also cache the conversation so far (the agent has tools to call).

    Returns:
        The provider's cache settings, or nothing when caching is off or takes no settings.
    """
    if prompt_cache == "off":
        return {}
    style = cache_style(model, name)
    profile = _profile(model)
    if style == "bedrock":
        ttl = _ttl(profile, prompt_cache)
        out: dict[str, Any] = {"bedrock_cache_instructions": ttl}
        if profile.get("bedrock_supports_tool_caching", False):
            out["bedrock_cache_tool_definitions"] = ttl
        if conversation:
            out["bedrock_cache_messages"] = CONVERSATION_TTL
        return out
    if style == "anthropic":
        ttl = prompt_cache if prompt_cache in ("5m", "1h") else "5m"
        out = {"anthropic_cache_instructions": ttl, "anthropic_cache_tool_definitions": ttl}
        if conversation:
            out["anthropic_cache_messages"] = CONVERSATION_TTL
        return out
    if style == "unified":
        return {"cache": {"retention": prompt_cache, "messages": conversation}}
    return {}


def cache_label(settings: dict[str, Any], model: Any, name: str, prompt_cache: str) -> str:
    """What the trace records as the call's prompt cache.

    Returns:
        The TTL asked for, ``auto`` for a provider that caches by itself, or ``off``.
    """
    for key in ("bedrock_cache_instructions", "anthropic_cache_instructions"):
        if key in settings:
            return str(settings[key])
    if "cache" in settings:
        return str(settings["cache"].get("retention", prompt_cache))
    if cache_style(model, name) == "automatic":
        return "auto"
    return "off"


def anthropic_family(name: str) -> bool:
    """Whether a model is one of Anthropic's (wherever it runs).

    Returns:
        True for Claude models.
    """
    b = bare(name)
    return "anthropic." in b or b.startswith("claude") or "/claude" in b
