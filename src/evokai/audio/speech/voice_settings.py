"""How an engine speaks with a catalogue voice: the voice's settings, checked per engine.

An operator tunes each voice of the deployment's catalogue once (``PUT /v1/admin/voices/{provider}/
{voice_id}``) and every application speaking with it gets the same voice. The settings are an
engine-neutral core, ``model`` and ``speed``, and the engine's own options under its name:

    {"model": "eleven_multilingual_v2", "speed": 1.05,
     "elevenlabs": {"stability": 0.4, "similarityBoost": 0.8, "style": 0.2, "speakerBoost": true}}
    {"model": "gpt-4o-mini-tts", "openai": {"instructions": "Warm and unhurried."}}

Anything an engine does not take is refused. This module has no speech dependency: the admin API
checks settings with it, and the engines (``evokai.audio.voice.providers``) apply them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ElevenLabsOptions(BaseModel):
    """The ElevenLabs options of a catalogue voice."""

    model_config = ConfigDict(extra="forbid")
    stability: float | None = Field(default=None, ge=0, le=1)
    similarityBoost: float | None = Field(default=None, ge=0, le=1)
    style: float | None = Field(default=None, ge=0, le=1)
    speakerBoost: bool | None = None


class OpenAIOptions(BaseModel):
    """The OpenAI options of a catalogue voice."""

    model_config = ConfigDict(extra="forbid")
    instructions: str | None = Field(default=None, min_length=1, max_length=4_000)
    """How the voice speaks (tone, pace, accent), for the models that follow instructions."""


class _Core(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = Field(
        default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._:/-]+$"
    )
    speed: float | None = Field(default=None, gt=0)


@dataclass(frozen=True)
class VoiceSettingsRule:
    """The settings a catalogue voice of one engine may carry."""

    core: frozenset[str] = frozenset({"model", "speed"})
    """The engine-neutral settings it takes."""
    speed: tuple[float, float] = (0.25, 4.0)
    """The speeds it accepts (1 is the voice's own pace)."""
    options: tuple[str, type[BaseModel]] | None = None
    """Its own options: their key in the settings (the engine's name) and their schema."""


TTS_SETTINGS: dict[str, VoiceSettingsRule] = {
    "fake": VoiceSettingsRule(),
    "elevenlabs": VoiceSettingsRule(speed=(0.7, 1.2), options=("elevenlabs", ElevenLabsOptions)),
    "openai": VoiceSettingsRule(options=("openai", OpenAIOptions)),
}
"""Engines not listed take no voice settings."""


def _check[M: BaseModel](schema: type[M], value: Any, prefix: str) -> M:
    try:
        return schema.model_validate(value)
    except ValidationError as e:
        first = e.errors()[0]
        where = prefix + ".".join(str(x) for x in first["loc"])
        raise ValueError(f"{where.rstrip('.') or 'settings'}: {first['msg']}") from e


def check_voice_settings(engine: str, settings: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Check a catalogue voice's settings against its engine.

    Settings are an engine-neutral core (``model``, ``speed``) and the engine's own options under
    its name (``{"elevenlabs": {"stability": 0.4}}``); anything else is refused.

    Returns:
        The settings without empty values, or None when there are none.

    Raises:
        ValueError: A setting the engine does not take, or a value out of range (the message says
            which).
    """
    if not settings:
        return None
    rule = TTS_SETTINGS.get(engine)
    if rule is None:
        raise ValueError(f"the {engine} engine takes no voice settings")
    allowed = set(rule.core) | ({rule.options[0]} if rule.options else set())
    unknown = sorted(set(settings) - allowed)
    if unknown:
        raise ValueError(f"the {engine} engine does not take {', '.join(unknown)}")
    core = _check(_Core, {k: v for k, v in settings.items() if k in rule.core}, "")
    options = None
    if rule.options and settings.get(rule.options[0]) is not None:
        name, schema = rule.options
        options = _check(schema, settings[name], f"{name}.")
    low, high = rule.speed
    if core.speed is not None and not low <= core.speed <= high:
        raise ValueError(f"speed: the {engine} engine takes {low} to {high}")
    out: dict[str, Any] = core.model_dump(exclude_none=True)
    if rule.options and options is not None:
        own = options.model_dump(exclude_none=True)
        if own:
            out[rule.options[0]] = own
    return out or None
