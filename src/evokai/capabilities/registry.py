"""The capability catalog's functions: status, schema, checks and construction."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import os
import typing
from collections.abc import Callable
from functools import cache
from importlib import metadata
from typing import Any

from packaging.requirements import Requirement
from pydantic import TypeAdapter

from evokai.capabilities.catalog import (
    BY_ID,
    ENTRIES,
    HOST_LOCAL,
    HOST_REMOTE,
    HOST_WORKSPACE,
    LITE_LEFT_OUT,
    OWN_SETTINGS,
    Entry,
)

log = logging.getLogger("evokai.capabilities")

SANDBOX_KEY = "sandbox"
"""The setting of a workspace capability that names its remote sandbox."""


class CapabilityError(ValueError):
    """A capability cannot be used as asked (unknown id, bad settings, host access)."""


def all_ids() -> list[str]:
    """Every id of the catalog.

    Returns:
        Ids, in the catalog's order.
    """
    return [e.id for e in ENTRIES]


def get(capability: str) -> Entry:
    """A catalog entry.

    Returns:
        The entry.

    Raises:
        CapabilityError: Unknown id.
    """
    entry = BY_ID.get(capability)
    if entry is None:
        raise CapabilityError(f"unknown capability: {capability}")
    return entry


@cache
def load(capability: str) -> Any:
    """The capability's class (imported once).

    Returns:
        The class.

    Raises:
        CapabilityError: Its dependencies are not installed in this image.
    """
    entry = get(capability)
    try:
        module = importlib.import_module(entry.path)
        return getattr(module, entry.cls)
    except Exception as e:
        raise CapabilityError(f"{capability} is not installed: {type(e).__name__}") from e


@cache
def installed(capability: str) -> bool:
    """Whether the capability's dependencies are in this image, from package metadata alone.

    Nothing is imported: listing the catalog must not load ninety integrations into the service.
    The capability's extra of ``pydantic-ai-harness`` is read from the installed distributions'
    requirements, recursively through extras such as ``pydantic-ai-slim[mcp]``. A class is
    imported only when an agent that enables it builds a run.

    Returns:
        True when every distribution its extra needs on this platform is installed.
    """
    entry = get(capability)
    if not _dist_installed("pydantic-ai-harness"):
        return False
    if entry.extra is None:
        return True
    return _extra_installed("pydantic-ai-harness", entry.extra, set())


def not_installed_reason(capability: str, flavour: str | None) -> str | None:
    """Why a capability is not installed here, for the console.

    Returns:
        ``lite`` when the lite image leaves its extra out, ``missing`` otherwise, None when it is
        installed.
    """
    if installed(capability):
        return None
    entry = get(capability)
    if flavour == "lite" and entry.extra in LITE_LEFT_OUT:
        return "lite"
    return "missing"


def _dist_installed(name: str) -> bool:
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def _extra_installed(dist: str, extra: str, seen: set[tuple[str, str]]) -> bool:
    if (dist, extra) in seen:
        return True
    seen.add((dist, extra))
    for line in metadata.requires(dist) or []:
        req = Requirement(line)
        if req.marker is None or "extra" not in str(req.marker):
            continue
        if not req.marker.evaluate({"extra": extra}):
            continue
        if not _dist_installed(req.name):
            return False
        if not all(_extra_installed(req.name, x, seen) for x in req.extras):
            return False
    return True


def missing_credentials(
    capability: str,
    env: dict[str, str] | None = None,
    stored: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """The credentials it needs that neither the application nor the deployment set.

    Args:
        capability: The id.
        env: The deployment's environment (the process's by default).
        stored: The application's values by provider (``evokai.capabilities.credentials``).

    Returns:
        ``provider.field`` names for a capability whose credentials are kept per application,
        else the environment variables it needs.
    """
    from evokai.capabilities import credentials

    env = dict(os.environ) if env is None else env
    provider = credentials.provider_of(capability)
    if provider is not None:
        return credentials.missing(capability, (stored or {}).get(provider), env)
    return [c for c in get(capability).credentials if not env.get(c)]


def offered(capability: str, deployment: list[str]) -> bool:
    """Whether the deployment offers a capability (``EVOKAI_CAPABILITIES``).

    Returns:
        True when listed, or when the list is ``["*"]``.
    """
    return "*" in deployment or capability in deployment


@cache
def schema(capability: str) -> dict[str, Any]:
    """The JSON Schema of its settings: the constructor's arguments that are plain values.

    Arguments that take functions or objects are left out (Evokai provides them). Each property
    carries its default when it has one.

    Returns:
        A JSON Schema of an object (empty properties when not installed).
    """
    props: dict[str, Any] = {}
    required: list[str] = []
    try:
        cls = load(capability)
    except CapabilityError:
        return {"type": "object", "properties": {}}
    try:
        hints = typing.get_type_hints(cls.__init__) if cls.__init__ else {}
    except Exception:
        hints = {}
    if not hints and dataclasses.is_dataclass(cls):
        try:
            hints = typing.get_type_hints(cls)
        except Exception:
            hints = {}
    try:
        params = inspect.signature(cls.__init__).parameters.values()
    except (TypeError, ValueError):
        params = []
    for p in params:
        if p.name in ("self", "_") or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        hint = hints.get(p.name, Any)
        try:
            prop = TypeAdapter(hint).json_schema()
        except Exception as e:
            log.debug("capability_setting_skipped", extra={"setting": p.name, "error": str(e)[:80]})
            continue
        if _has_callable(prop):
            continue
        if p.default is not p.empty and _plain(p.default):
            prop["default"] = p.default
        if p.default is p.empty:
            required.append(p.name)
        props[p.name] = prop
    entry = get(capability)
    props.update(OWN_SETTINGS.get(capability, {}))
    for name in entry.models:
        props[name] = {"type": "string", "description": "A model id allowed to the application."}
    if entry.host == HOST_WORKSPACE:
        props[SANDBOX_KEY] = {"type": "string", "description": "The remote sandbox capability."}
    out: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        out["required"] = [r for r in required if r in props]
    return out


def check_use(capability: str, config: dict[str, Any]) -> None:
    """Whether an agent may ask for a capability with these settings (checked when it is saved).

    Imports nothing: the settings of a harness capability are checked against its constructor when
    a run builds it (``build``); Evokai's own have a fixed list of settings.

    Raises:
        CapabilityError: Unknown capability, a setting it does not have, or one that would reach
            the service's own machine.
    """
    entry = get(capability)
    if entry.host == HOST_LOCAL:
        raise CapabilityError(
            f"{capability} runs on the Evokai server's own machine and cannot be enabled: use a "
            "remote sandbox"
        )
    if entry.settings is not None:
        unknown = sorted(set(config) - set(entry.settings))
        if unknown:
            raise CapabilityError(f"{capability} has no setting {', '.join(unknown)}")
    if entry.host == HOST_WORKSPACE:
        box = config.get(SANDBOX_KEY)
        if not box or BY_ID.get(str(box)) is None or BY_ID[str(box)].host != HOST_REMOTE:
            raise CapabilityError(
                f"{capability} works on files and commands: set {SANDBOX_KEY} to a remote sandbox "
                "capability (e2b_sandbox, modal_sandbox, daytona_sandbox, kubernetes_sandbox, "
                "render_sandbox, sprites_sandbox, opensandbox, boxd_sandbox, ssh_workspace)"
            )


def build(
    capability: str,
    config: dict[str, Any],
    *,
    model_of: Callable[[str], Any],
    factories: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    enabled: set[str] | None = None,
) -> Any:
    """Construct one capability with its settings.

    Args:
        capability: The id.
        config: Its settings (as saved on the agent).
        model_of: Resolves a model setting to a model the application may use (raises otherwise).
        factories: Evokai's own builders by id (Code Mode, compaction, subagents, and the ones that
            need Evokai objects).
        enabled: The capabilities enabled for the same agent (a workspace one needs its sandbox).

    Returns:
        The capability.

    Raises:
        CapabilityError: It cannot be built as asked.
    """
    check_use(capability, config)
    entry = get(capability)
    if entry.host == HOST_WORKSPACE and str(config.get(SANDBOX_KEY)) not in (enabled or set()):
        raise CapabilityError(f"{capability} needs its sandbox enabled for the agent too")
    if factories and capability in factories:
        return factories[capability](config)
    cls = load(capability)
    known = set(schema(capability)["properties"]) | {SANDBOX_KEY}
    unknown = sorted(set(config) - known)
    if unknown:
        raise CapabilityError(f"{capability} has no setting {', '.join(unknown)}")
    args = {k: v for k, v in config.items() if k != SANDBOX_KEY}
    for name in entry.models:
        if name in args:
            args[name] = model_of(str(args[name]))
    if entry.id == "ssh_workspace" and str(args.get("destination", "")).split("@")[-1] in (
        "localhost",
        "127.0.0.1",
        "::1",
    ):
        raise CapabilityError("ssh_workspace must reach another machine, never the service's own")
    try:
        return cls(**args)
    except Exception as e:
        raise CapabilityError(f"{capability}: {type(e).__name__}: {e}") from e


def construct(
    capability: str,
    config: dict[str, Any],
    *,
    model_of: Callable[[str], Any],
    provided: dict[str, Any] | None = None,
    own: tuple[str, ...] = (),
    cls: Any = None,
) -> Any:
    """Construct a harness capability with its plain settings and the objects Evokai provides.

    The settings an agent saved are checked against the class's constructor, like ``build``; the
    objects Evokai provides (a store, an answerer, a guard function, a client with the application's
    credential) are passed as they are and cannot be set by an agent.

    Args:
        capability: The id.
        config: Its settings (as saved on the agent), without Evokai's own (``own``).
        model_of: Resolves a model setting.
        provided: Constructor arguments Evokai supplies.
        own: Setting names Evokai reads itself (not passed to the class).
        cls: The class to build (a subclass of the catalog's, with Evokai's hooks); the catalog's
            class by default.

    Returns:
        The capability.

    Raises:
        CapabilityError: An unknown setting, or the class refused its arguments.
    """
    entry = get(capability)
    known = set(schema(capability)["properties"]) | {SANDBOX_KEY} | set(own)
    unknown = sorted(set(config) - known)
    if unknown:
        raise CapabilityError(f"{capability} has no setting {', '.join(unknown)}")
    args = {k: v for k, v in config.items() if k != SANDBOX_KEY and k not in own}
    for name in entry.models:
        if name in args:
            args[name] = model_of(str(args[name]))
    args.update(provided or {})
    target = cls if cls is not None else load(capability)
    try:
        return target(**args)
    except Exception as e:
        raise CapabilityError(f"{capability}: {type(e).__name__}: {e}") from e


def _plain(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, list, dict, tuple))


def _has_callable(prop: Any) -> bool:
    if isinstance(prop, dict):
        if prop.get("type") == "callable" or "callable" in str(prop.get("description", "")):
            return True
        return any(_has_callable(v) for v in prop.values())
    if isinstance(prop, list):
        return any(_has_callable(v) for v in prop)
    return False
