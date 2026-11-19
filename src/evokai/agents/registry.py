"""Agents of each application, from the database, cached per version (versions never change)."""

from __future__ import annotations

from evokai.agents.definitions import AgentDef
from evokai.core.errors import NotFound
from evokai.core.util import TtlCache
from evokai.db.repositories import Store


class AgentRegistry:
    """Resolves the active or a pinned version of an application's agent."""

    def __init__(self, store: Store) -> None:
        """Create the registry.

        Args:
            store: Repositories.
        """
        self.store = store
        self._versions: TtlCache[tuple[str, str, str], AgentDef] = TtlCache(500, 3600)
        self._active: TtlCache[tuple[str, str], str] = TtlCache(500, 10)

    async def active(self, application_id: str, name: str) -> AgentDef:
        """The agent's active version (re-checked every few seconds).

        Args:
            application_id: Owner application.
            name: Agent name.

        Returns:
            The definition.

        Raises:
            NotFound: No such active agent.
        """
        version = self._active.get((application_id, name))
        if version:
            hit = self._versions.get((application_id, name, version))
            if hit:
                return hit
        row = await self.store.agents.active(application_id, name)
        if not row:
            raise NotFound("Agente sconosciuto.", code="unknown_agent")
        agent = AgentDef.model_validate({**row.definition, "version": row.version})
        self._active.set((application_id, name), row.version)
        self._versions.set((application_id, name, row.version), agent)
        return agent

    def invalidate(self, application_id: str, name: str) -> None:
        """Forget which version is active (after an admin change)."""
        self._active.pop((application_id, name))

    async def pinned(self, application_id: str, name: str, version: str) -> AgentDef:
        """A specific version, or the active one if it no longer exists.

        Args:
            application_id: Owner application.
            name: Agent name.
            version: Pinned version.

        Returns:
            The definition.
        """
        hit = self._versions.get((application_id, name, version))
        if hit:
            return hit
        row = await self.store.agents.version(application_id, name, version)
        if not row:
            return await self.active(application_id, name)
        agent = AgentDef.model_validate({**row.definition, "version": row.version})
        self._versions.set((application_id, name, version), agent)
        return agent
