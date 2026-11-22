"""The built-in assistant as a system application: created at start, locked, inactive.

- The definition ships inside the package: ``app.yaml``, the agent's ``prompt.md`` and the docs
  skill (``skills/evokai-docs``, generated from ``docs/`` by ``scripts/assistant_docs.py``).
- At start (``ensure_assistant``) the application is created when missing: system, locked, and
  inactive (no model until an owner activates one, ``evokai.assistant.activation``).
- While it is locked, a newer shipped definition (another hash) is applied. An owner who unlocked
  it keeps their changes: the update is only recorded (``system_update_available``), and a reset
  applies the shipped definition again and locks it.
- Applying the shipped definition makes the application exactly that: MCP servers, skills and
  agents it does not ship are removed, its grants and guard are the file's.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from evokai.agents.bootstrap import bootstrap
from evokai.agents.skills import skills_in
from evokai.core.errors import ConfigError

if TYPE_CHECKING:
    from evokai.core.config import Settings
    from evokai.core.security import Keyring
    from evokai.db.repositories import Store

log = logging.getLogger("evokai.assistant")

SHIPPED = Path(__file__).resolve().parent / "app.yaml"
SLUG = "evokai-assistant"
AGENT = "assistant"
MCP_SERVER = "evokai-admin"
UNSET_MODEL = "evokai:unset"
"""The agent's model until an owner activates one (sessions are refused meanwhile)."""
MODEL_KEY = "assistant_model"
"""Where the activated model is kept, in the application's defaults."""
UPDATE_KEY = "system_update_available"


def _files(path: Path) -> list[Path]:
    root = path.parent
    return sorted([path, root / "prompt.md", *(root / "skills").rglob("*")])


def shipped_hash(path: Path = SHIPPED) -> str:
    """The hash of a shipped definition (the file, the prompt and the skills).

    Returns:
        A hex digest.
    """
    digest = hashlib.sha256()
    for f in _files(path):
        if f.is_file():
            digest.update(str(f.relative_to(path.parent)).encode())
            digest.update(f.read_bytes())
    return digest.hexdigest()


def rendered(path: Path, settings: Settings, model: str | None) -> dict[str, Any]:
    """The shipped definition with this deployment's values: model, prompt, MCP server address.

    Returns:
        The application file as data.

    Raises:
        ConfigError: The prompt holds ``${...}`` (the bootstrap would read it as a variable).
    """
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    prompt = (path.parent / "prompt.md").read_text(encoding="utf-8")
    if "${" in prompt:
        raise ConfigError("assistant: the prompt must not contain ${...}")
    for agent in data.get("agents") or []:
        if agent.get("name") == AGENT:
            agent["instructions"] = prompt
            agent["model"] = model or UNSET_MODEL
    for server in data.get("mcp_servers") or []:
        if server.get("name") == MCP_SERVER:
            server["url"] = f"{settings.internal_base}/mcp/admin"
    return data


async def apply_shipped(
    store: Store,
    keyring: Keyring,
    settings: Settings,
    path: Path = SHIPPED,
    *,
    model: str | None = None,
) -> str:
    """Make the system application exactly the shipped definition, and lock it.

    Args:
        store: Repositories.
        keyring: Signing and encryption keys.
        settings: Deployment settings.
        path: The shipped ``app.yaml``.
        model: The activated model (None: inactive).

    Returns:
        The application id.
    """
    data = rendered(path, settings, model)
    # The skills are stored as they are: the bootstrap reads ${...} as environment variables, and
    # the documentation quotes such variables (their values must never end up in a skill).
    skills = skills_in(path.parent / "skills")
    with tempfile.TemporaryDirectory() as tmp:
        file = Path(tmp) / "app.yaml"
        file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
        app_id = await bootstrap(store, keyring, file, {})
    for skill in skills:
        await store.skills.put(
            app_id,
            skill.name,
            skill.description,
            skill.body,
            [(f.path, f.content or "") for f in skill.files],
        )
    shipped_servers = {s["name"] for s in data.get("mcp_servers") or []}
    for server in await store.mcp_servers.all(app_id):
        if server.name not in shipped_servers:
            await store.mcp_servers.delete(app_id, server.name)
    shipped_skills = {s.name for s in skills}
    for record in await store.skills.all(app_id):
        if record.name not in shipped_skills:
            await store.skills.delete(app_id, record.name)
    shipped_agents = {a["name"] for a in data.get("agents") or []}
    for agent in await store.agents.active_all(app_id):
        if agent.name not in shipped_agents:
            await store.agents.deactivate(app_id, agent.name)
    app = await store.applications.get(app_id)
    defaults = {k: v for k, v in ((app.defaults if app else None) or {}).items() if k != UPDATE_KEY}
    await store.applications.update(
        app_id, system=True, locked=True, system_version=shipped_hash(path), defaults=defaults
    )
    return app_id


async def ensure_assistant(
    store: Store, keyring: Keyring, settings: Settings, *, path: Path = SHIPPED
) -> str | None:
    """Create the assistant's system application if missing; apply a shipped update if locked.

    Returns:
        The application id, or None when the assistant is off or the slug belongs to another
        application.
    """
    if settings.assistant == "off":
        return None
    app = await store.applications.by_slug(SLUG)
    if app is None:
        app_id = await apply_shipped(store, keyring, settings, path)
        log.info("assistant_created", extra={"application_id": app_id})
        return app_id
    if not app.system:
        log.warning("assistant_slug_taken", extra={"application": SLUG})
        return None
    version = shipped_hash(path)
    if app.system_version == version:
        return app.id
    model = (app.defaults or {}).get(MODEL_KEY)
    if app.locked:
        await apply_shipped(store, keyring, settings, path, model=model)
        log.info("assistant_updated", extra={"version": version[:12]})
    else:
        defaults = {**(app.defaults or {}), UPDATE_KEY: version}
        await store.applications.update(app.id, defaults=defaults)
        log.info("assistant_update_available", extra={"version": version[:12]})
    return app.id
