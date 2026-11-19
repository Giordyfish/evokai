"""The credentials capabilities need, kept per application and encrypted like MCP secrets.

A web search provider, a third-party integration or a remote sandbox needs an account's key. Each
application sets its own (console: Permissions, Capabilities; or ``capability_credentials`` in the
application file), stored encrypted with the deployment's Fernet key in
``evokai_capability_credentials``; a secret is never shown again, only whether it is set. The
deployment's environment variable of the same field is the fallback, as before.

Listing what is missing imports nothing; the provider's client is built only when a run builds the
capability (``kwargs``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Field:
    """One value of a provider's credentials."""

    name: str
    """Its name in the console and the application file (shown as code, never translated)."""
    env: str
    """The deployment's environment variable it falls back to."""
    secret: bool = True
    """Write-only: never returned once set."""
    required: bool = True


PROVIDERS: dict[str, tuple[Field, ...]] = {
    # web search
    "exa": (Field("api_key", "EXA_API_KEY"),),
    "keenable": (Field("api_key", "KEENABLE_API_KEY"),),
    "youdotcom": (Field("api_key", "YDC_API_KEY"),),
    # third-party integrations (hosted MCP servers)
    "github": (Field("token", "GITHUB_TOKEN"),),
    "linear": (Field("token", "LINEAR_ACCESS_TOKEN"),),
    "notion": (Field("token", "NOTION_ACCESS_TOKEN"),),
    "slack": (Field("token", "SLACK_USER_TOKEN"),),
    "google_workspace": (Field("token", "GOOGLE_ACCESS_TOKEN"),),
    "posthog": (Field("token", "POSTHOG_PERSONAL_API_KEY"),),
    "pylon": (Field("token", "PYLON_ACCESS_TOKEN"),),
    "ordinal": (Field("token", "ORDINAL_ACCESS_TOKEN"),),
    "stackone": (Field("api_key", "STACKONE_API_KEY"),),
    "day_ai": (Field("token", "DAY_AI_ACCESS_TOKEN"),),
    "grain": (Field("token", "GRAIN_ACCESS_TOKEN"),),
    "logfire_mcp": (Field("token", "LOGFIRE_API_KEY"),),
    # remote sandboxes
    "e2b": (Field("api_key", "E2B_API_KEY"),),
    "modal": (Field("token_id", "MODAL_TOKEN_ID"), Field("token_secret", "MODAL_TOKEN_SECRET")),
    "daytona": (Field("api_key", "DAYTONA_API_KEY"),),
    "render": (
        Field("api_key", "RENDER_API_KEY"),
        Field("workspace_id", "RENDER_WORKSPACE_ID", secret=False),
    ),
    "sprites": (Field("token", "SPRITE_TOKEN"),),
    "opensandbox": (
        Field("api_key", "OPEN_SANDBOX_API_KEY"),
        Field("domain", "OPEN_SANDBOX_DOMAIN", secret=False),
    ),
    "boxd": (Field("api_key", "BOXD_API_KEY"),),
}
"""Each provider's credential fields."""

PROVIDER_OF: dict[str, str] = {
    "exa_search": "exa",
    "exa_agent": "exa",
    "keenable_search": "keenable",
    "you_search": "youdotcom",
    "you_research": "youdotcom",
    "github": "github",
    "linear": "linear",
    "notion": "notion",
    "slack": "slack",
    "google_workspace": "google_workspace",
    "posthog": "posthog",
    "pylon": "pylon",
    "ordinal": "ordinal",
    "stackone": "stackone",
    "day_ai": "day_ai",
    "grain": "grain",
    "logfire_mcp": "logfire_mcp",
    "e2b_sandbox": "e2b",
    "modal_sandbox": "modal",
    "daytona_sandbox": "daytona",
    "render_sandbox": "render",
    "sprites_sandbox": "sprites",
    "opensandbox": "opensandbox",
    "boxd_sandbox": "boxd",
}
"""The provider whose credentials a capability uses (two capabilities may share one)."""


PERSONAL: dict[str, str | None] = {
    "google_workspace": "google",
    "github": "github",
    "linear": "linear",
    "notion": "notion",
    "slack": "slack",
}
"""Integrations whose service lets each person sign in (per-user OAuth), with the Evokai connection
that supplies the person's token (``evokai.connections``), or None where Evokai has no connection
for that service yet. They act as the signed-in person by default; the application's own
credential (a shared account) only with ``acts_as: application`` on the agent."""

ACTS_AS = ("person", "application")
"""Whom a per-person integration acts as."""


def acts_as(capability: str, config: Mapping[str, Any]) -> str | None:
    """Whom an integration acts as with these settings.

    Returns:
        ``person`` or ``application`` for a per-person integration, None for the others (they use
        the application's credential: an API key of a service without personal sign-in).

    Raises:
        ValueError: An unknown value.
    """
    if capability not in PERSONAL:
        return None
    value = str(config.get("acts_as") or "person")
    if value not in ACTS_AS:
        raise ValueError(f"{capability}: acts_as is person or application")
    return value


def provider_of(capability: str) -> str | None:
    """The provider of a capability's credentials.

    Returns:
        The provider, or None when it needs none kept per application.
    """
    return PROVIDER_OF.get(capability)


def fields(provider: str) -> tuple[Field, ...]:
    """A provider's fields (an unknown provider raises ``KeyError``).

    Returns:
        The fields.
    """
    return PROVIDERS[provider]


def resolve(
    provider: str, stored: Mapping[str, str] | None, env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A provider's values: the application's own, else the deployment's environment.

    Returns:
        Field name to value (only the ones set somewhere).
    """
    env = os.environ if env is None else env
    out: dict[str, str] = {}
    for f in PROVIDERS[provider]:
        value = (stored or {}).get(f.name) or env.get(f.env) or ""
        if value:
            out[f.name] = value
    return out


def missing(
    capability: str, stored: Mapping[str, str] | None, env: Mapping[str, str] | None = None
) -> list[str]:
    """The fields a capability still needs, as the console shows them.

    Returns:
        Field names (``provider.field``); empty when nothing is missing or none is needed.
    """
    provider = provider_of(capability)
    if provider is None or capability in PERSONAL:
        # A per-person integration uses each person's own account by default.
        return []
    got = resolve(provider, stored, env)
    return [f"{provider}.{f.name}" for f in PROVIDERS[provider] if f.required and f.name not in got]


def schema(provider: str) -> dict[str, Any]:
    """A provider's fields as a JSON Schema (the console's form, secrets write-only).

    Returns:
        A JSON Schema of an object.
    """
    return {
        "type": "object",
        "properties": {
            f.name: {
                "type": "string",
                "writeOnly": f.secret,
                "x-env": f.env,
            }
            for f in PROVIDERS[provider]
        },
        "required": [f.name for f in PROVIDERS[provider] if f.required],
    }


def kwargs(capability: str, values: Mapping[str, str]) -> dict[str, Any]:
    """The constructor arguments that hand a capability the application's credentials.

    The provider's client is built here with the application's key (imported only now); a
    capability without credentials kept per application gets nothing.

    Returns:
        Keyword arguments.

    Raises:
        ValueError: A required value is missing.
    """
    provider = provider_of(capability)
    if provider is None:
        return {}
    for f in PROVIDERS[provider]:
        if f.required and not values.get(f.name):
            raise ValueError(f"{capability} has no credential {provider}.{f.name}")
    v = dict(values)
    if provider in (
        "github",
        "linear",
        "notion",
        "slack",
        "google_workspace",
        "posthog",
        "pylon",
        "ordinal",
        "day_ai",
        "grain",
        "logfire_mcp",
    ):
        return {"auth": v["token"]}
    if provider == "stackone":
        return {"api_key": v["api_key"]}
    if provider == "exa":
        from exa_py import AsyncExa

        exa = AsyncExa(api_key=v["api_key"])
        return {"runs": exa.agent.runs} if capability == "exa_agent" else {"client": exa}
    if provider == "keenable":
        from pydantic_ai_harness.keenable._toolset import HttpKeenableClient

        return {"client": HttpKeenableClient(api_key=v["api_key"])}
    if provider == "youdotcom":
        from youdotcom import You

        return {"client": You(api_key_auth=v["api_key"])}
    if provider == "e2b":
        import e2b

        return {"client": e2b.E2B(api_key=v["api_key"])}
    if provider == "modal":
        import modal

        return {"client": modal.Client.from_credentials(v["token_id"], v["token_secret"])}
    if provider == "daytona":
        from daytona import AsyncDaytona, DaytonaConfig

        return {"client": AsyncDaytona(DaytonaConfig(api_key=v["api_key"]))}
    if provider == "render":
        from render import RenderAsync

        return {"client": RenderAsync(token=v["api_key"], owner_id=v.get("workspace_id"))}
    if provider == "sprites":
        from sprites import AsyncSpritesClient

        return {"client": AsyncSpritesClient(token=v["token"])}
    if provider == "opensandbox":
        from opensandbox.config import ConnectionConfig

        return {"connection": ConnectionConfig(api_key=v["api_key"], domain=v.get("domain"))}
    if provider == "boxd":
        import boxd

        return {"client": boxd.AsyncBoxd(api_key=v["api_key"])}
    return {}


# kept per application


async def stored(store: Any, keyring: Any, app_id: str) -> dict[str, dict[str, str]]:
    """Every provider's values the application set, decrypted (never logged, never returned).

    A value that no longer decrypts (a rotated key) reads as not set.

    Returns:
        Provider to field values.
    """
    import json

    out: dict[str, dict[str, str]] = {}
    for row in await store.capability_credentials.all(app_id):
        text = keyring.decrypt(row.values_encrypted)
        if text:
            out[row.provider] = {k: str(v) for k, v in json.loads(text).items() if v}
    return out


async def save(
    store: Any,
    keyring: Any,
    app_id: str,
    provider: str,
    values: Mapping[str, str | None],
) -> list[str]:
    """Set a provider's values: a non-empty value replaces, an empty one keeps, null clears.

    Returns:
        The field names now set.

    Raises:
        ValueError: An unknown provider or field.
    """
    import json

    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    known = {f.name: f for f in PROVIDERS[provider]}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ValueError(f"{provider} has no credential {', '.join(unknown)}")
    current = (await stored(store, keyring, app_id)).get(provider, {})
    for name, value in values.items():
        if value is None:
            current.pop(name, None)
        elif str(value).strip():
            current[name] = str(value).strip()
    if not current:
        await store.capability_credentials.delete(app_id, provider)
        return []
    plain = {k: v for k, v in current.items() if not known[k].secret}
    await store.capability_credentials.put(
        app_id,
        provider,
        values_encrypted=keyring.encrypt(json.dumps(current)),
        names=sorted(current),
        plain=plain or None,
    )
    return sorted(current)


async def view(
    store: Any, app_id: str, env: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Every provider, with what is set and where, for the console (never a secret).

    Returns:
        One item per provider: ``provider``, ``capabilities``, ``fields`` (``name``, ``secret``,
        ``env``, ``set`` by the application, ``fromEnvironment``, the ``value`` when it is not a
        secret), ``schema``, ``updatedAt``.
    """
    env = os.environ if env is None else env
    rows = {r.provider: r for r in await store.capability_credentials.all(app_id)}
    out = []
    for provider, flds in PROVIDERS.items():
        row = rows.get(provider)
        names = set(row.names or []) if row else set()
        plain = (row.plain or {}) if row else {}
        out.append({
            "provider": provider,
            "capabilities": sorted(c for c, p in PROVIDER_OF.items() if p == provider),
            "fields": [
                {
                    "name": f.name,
                    "secret": f.secret,
                    "required": f.required,
                    "env": f.env,
                    "set": f.name in names,
                    "fromEnvironment": f.name not in names and bool(env.get(f.env)),
                    **({"value": plain.get(f.name)} if not f.secret else {}),
                }
                for f in flds
            ],
            "schema": schema(provider),
            "updatedAt": row.updated_at.isoformat() if row else None,
        })
    return out


def stored_names(rows: list[Any]) -> dict[str, dict[str, str]]:
    """Which values each provider has set, from the rows alone (no decryption, for listings).

    Returns:
        Provider to ``{field: "set"}``.
    """
    return {r.provider: dict.fromkeys(r.names or [], "set") for r in rows}
