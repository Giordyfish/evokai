"""Modules: the product areas a deployment runs on its shared core.

Evokai has two modules, installable and switchable independently:

- ``applications``: agents, knowledge, workflows, channels, evals, sessions, the
  OpenAI-compatible API, the console assistant;
- ``gateway``: gateways, their people, providers, rules, limits and policy tests, and their
  entrances (the base URL door ``/gw/...``).

The core (policies, people and roles, audit, records, the console shell, retention) always runs.

``EVOKAI_MODULES`` (a comma list) pins which modules are on. Without it the deployment owner
switches them in the console; the choice is stored in the deployment settings table (key
``modules``) and both are on until then. At least one module is always on.

A module that is off keeps its routes mounted: :func:`require_module` answers them 404
``module_disabled``, so switching needs no restart. Its background jobs stop and its rows are
invisible through the kind helpers (``evokai.db.kinds``); its data stays, and retention keeps
running on it.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any

from starlette.requests import HTTPConnection

from evokai.core.errors import AppError

if TYPE_CHECKING:
    from evokai.core.config import Settings

MODULES: tuple[str, ...] = ("applications", "gateway")
"""Every module, in the order the console shows them."""
APPLICATIONS, GATEWAY = MODULES
ALL: frozenset[str] = frozenset(MODULES)

SETTING = "modules"
"""The key of the stored choice in the deployment settings table: ``{"enabled": [...]}``."""

NONE_ON = "Turn on at least one of applications, gateway."

CACHE_SECONDS = 2.0
"""How long a stored choice read from the database is reused by this process (a switch made on
this process is seen at once, one made on another instance within this time)."""


class ModulesError(ValueError):
    """An invalid list of modules (an unknown name, or none on)."""


class ModuleDisabled(AppError):
    """The route belongs to a module that is off on this deployment."""

    status, code, message = 404, "module_disabled", "This module is off on this deployment."

    def __init__(self, module: str) -> None:
        """Name the module that is off.

        Args:
            module: ``applications`` or ``gateway``.
        """
        super().__init__(f"The {module} module is off on this deployment.")
        self.module = module

    def body(self) -> dict[str, Any]:
        """The response body: ``{"error": {"code": "module_disabled", "message", "module"}}``.

        Returns:
            The body.
        """
        return {"error": {"code": self.code, "message": self.message, "module": self.module}}


def parse(value: str | Iterable[str]) -> frozenset[str]:
    """Read a list of modules (``"applications,gateway"`` or names).

    Returns:
        The modules.

    Raises:
        ModulesError: An unknown name, or no module at all.
    """
    items = value.split(",") if isinstance(value, str) else list(value)
    names = {str(n).strip().lower() for n in items if str(n).strip()}
    if unknown := sorted(names - ALL):
        raise ModulesError(
            f"Unknown module {', '.join(unknown)}: the modules are {', '.join(MODULES)}."
        )
    if not names:
        raise ModulesError(NONE_ON)
    return frozenset(names)


_settings: Settings | None = None
_visible: frozenset[str] = ALL
_cache: weakref.WeakKeyDictionary[Any, tuple[float, frozenset[str]]] = weakref.WeakKeyDictionary()


def configure(settings: Settings) -> None:
    """Bind the process to its settings (``create_app``, ``build_services``, the CLI).

    ``EVOKAI_MODULES`` naming no module or an unknown one raises ``ModulesError`` (the settings
    refuse it first when read from the environment).
    """
    global _settings, _visible
    _settings = settings
    _cache.clear()
    _visible = pinned(settings) or ALL


def reset() -> None:
    """Forget the process state (tests)."""
    global _settings, _visible
    _settings = None
    _cache.clear()
    _visible = ALL


def _config(settings: Settings | None = None) -> Settings:
    if settings is not None:
        return settings
    if _settings is not None:
        return _settings
    from evokai.core.config import get_settings

    return get_settings()


def pinned(settings: Settings | None = None) -> frozenset[str] | None:
    """The modules ``EVOKAI_MODULES`` turns on.

    Args:
        settings: The deployment settings (default: the process's).

    Returns:
        The modules, or None when the configuration leaves the choice to the console.
    """
    value = _config(settings).modules
    return None if value is None else parse(value)


def _parts(source: Any) -> tuple[Any, Any, Settings | None]:
    """What a source gives: a database session, a store, or the services.

    Returns:
        (an open SQLAlchemy session or None, the store or None, the settings or None).
    """
    store = getattr(source, "store", None)
    if store is not None and hasattr(store, "console_settings"):
        return None, store, getattr(source, "settings", None)
    if hasattr(source, "console_settings"):
        return None, source, None
    return source, None, None


async def stored(session: Any) -> frozenset[str] | None:
    """The choice stored by the console.

    Args:
        session: An open SQLAlchemy ``AsyncSession``, a ``Store`` or the ``Services``.

    Returns:
        The modules, or None when nothing (or nothing valid) is stored.
    """
    from evokai.db import models as m

    db_session, store, _ = _parts(session)
    if store is not None:
        value = await store.console_settings.get(SETTING)
    else:
        row = await db_session.get(m.ConsoleSetting, SETTING)
        value = dict(row.value) if row is not None else None
    if not isinstance(value, dict):
        return None
    try:
        return parse([str(n) for n in value.get("enabled") or []])
    except ModulesError:
        return None


async def enabled(session: Any, settings: Settings | None = None) -> frozenset[str]:
    """The modules that are on: the configuration's if pinned, else the stored choice, else both.

    Args:
        session: An open SQLAlchemy ``AsyncSession``, a ``Store`` or the ``Services``.
        settings: The deployment settings (default: the services' or the process's).

    Returns:
        The modules.
    """
    global _visible
    _, store, own = _parts(session)
    value = pinned(settings or own)
    if value is None:
        hit = _cache.get(store.db) if store is not None else None
        if hit is not None and time.monotonic() - hit[0] < CACHE_SECONDS:
            value = hit[1]
        else:
            value = await stored(session) or ALL
            if store is not None:
                _cache[store.db] = (time.monotonic(), value)
    _visible = value
    return value


async def is_enabled(session: Any, name: str, settings: Settings | None = None) -> bool:
    """Whether a module is on.

    Args:
        session: An open SQLAlchemy ``AsyncSession``, a ``Store`` or the ``Services``.
        name: ``applications`` or ``gateway``.
        settings: The deployment settings (default: the services' or the process's).

    Returns:
        True when it is on.
    """
    return name in await enabled(session, settings)


def require_module(name: str) -> Callable[[HTTPConnection], Awaitable[None]]:
    """A FastAPI dependency that answers 404 ``module_disabled`` while a module is off.

    Args:
        name: ``applications`` or ``gateway``.

    Returns:
        The dependency.

    Raises:
        ValueError: An unknown module.
    """
    if name not in ALL:
        raise ValueError(f"unknown module {name!r}")

    async def check(request: HTTPConnection) -> None:
        if not await is_enabled(request.app.state.services, name):
            raise ModuleDisabled(name)

    check.__name__ = f"require_{name}_module"
    return check


def visible(name: str) -> bool:
    """Whether a module was on at the last read in this process (the kind helpers' view).

    Returns:
        True when it is on.
    """
    return name in _visible


async def save(store: Any, modules: Iterable[str], by: str) -> frozenset[str]:
    """Store the console's choice and apply it to this process at once.

    The caller checks that the configuration does not pin the modules.

    Args:
        store: The ``Store``.
        modules: The modules to turn on.
        by: Who switches them (the audit log's actor).

    Returns:
        The modules now on.
    """
    global _visible
    value = parse(modules)
    await store.console_settings.put(SETTING, {"enabled": sorted(value)}, by)
    _cache[store.db] = (time.monotonic(), value)
    _visible = value
    return value
