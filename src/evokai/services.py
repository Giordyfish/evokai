"""Everything the API needs, built once per process (never per request)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from evokai.agents.registry import AgentRegistry
from evokai.core.config import Settings
from evokai.core.security import Keyring
from evokai.core.shared import SharedState, build_shared
from evokai.db.engine import Database
from evokai.db.repositories import Store
from evokai.guard import Guard
from evokai.guard.logfilter import configure as configure_log_masking
from evokai.identity.auth import Authenticator
from evokai.llm.catalog import ModelCatalog
from evokai.mcp.registry import McpRegistry
from evokai.sessions.cancel import TurnRegistry
from evokai.sessions.events import ChannelHub
from evokai.sessions.turns import TurnEngine


@dataclass
class Services:
    """Process-wide services."""

    settings: Settings
    db: Database
    store: Store
    keyring: Keyring
    auth: Authenticator
    http: httpx.AsyncClient
    agents: AgentRegistry
    mcp: McpRegistry
    catalog: ModelCatalog
    hub: ChannelHub
    turns: TurnRegistry
    engine: TurnEngine
    guard: Guard
    shared: SharedState = field(default_factory=lambda: build_shared(None))
    """State every instance shares: rate counters, running turns, sign-in codes (Redis when
    ``EVOKAI_REDIS_URL`` is set, else this process's memory)."""
    voice: Any = None
    """The WebRTC voice server, created on the first voice offer when voice is enabled."""
    evals: Any = None
    """The eval runs (``evokai.evals.runner.EvalService``)."""
    knowledge: Any = None
    """Knowledge bases: ingestion, search, links (``evokai.knowledge.service.KnowledgeService``)."""
    memory: Any = None
    """Long-term memory: consent, consolidation, recall, episodes (``evokai.memory.service``)."""
    redteam: Any = None
    """Red team runs and the publish gate (``evokai.evals.redteam.service.RedTeamService``)."""
    costs: Any = None
    """Forecasts, alerts and model recommendations (``evokai.costs.service.CostService``)."""
    workflows: Any = None
    durable: Any = None
    """Durable agent steps on pgtask (``evokai.workflows.durable``)."""
    """Durable workflows: runs, workers, schedules, approvals
    (``evokai.workflows.engine.WorkflowService``)."""
    code: Any = None
    """Isolated code execution (``evokai.sandbox.tool.CodeService``)."""
    connections: Any = None
    """Per-user OAuth connections (``evokai.connections.service.ConnectionService``)."""
    channels: Any = None
    """E-mail, Teams and Slack (``evokai.channels.service.ChannelService``)."""
    housekeeping: dict[str, Any] = field(default_factory=dict)
    """The purge job's last run: lastRunAt, deleted, nextRunAt, error (epoch seconds)."""

    async def close(self) -> None:
        """Stop running turns and eval runs, then release connections."""
        if self.evals is not None:
            await self.evals.close()
        if self.redteam is not None:
            await self.redteam.close()
        if self.costs is not None:
            await self.costs.close()
        if self.knowledge is not None:
            await self.knowledge.close()
        if self.workflows is not None:
            await self.workflows.close()
        if self.durable is not None:
            await self.durable.close()
        if self.channels is not None:
            await self.channels.close()
        await self.turns.drain()
        self.guard.classifiers.close()
        if self.voice is not None:
            await self.voice.close()
        await self.http.aclose()
        await self.shared.close()
        await self.db.close()


def build_services(
    settings: Settings,
    *,
    http: httpx.AsyncClient | None = None,
    catalog: ModelCatalog | None = None,
    embeddings: Any = None,
    code_interpreter: Any = None,
) -> Services:
    """Build the services.

    Args:
        settings: Deployment settings.
        http: HTTP client (tests pass a mock transport).
        catalog: Model catalog (tests pass fake models).
        embeddings: Embedding catalog (``evokai.knowledge.embeddings.EmbeddingCatalog``; tests).
        code_interpreter: The code sandbox (tests pass a fake); else from the settings.

    Returns:
        The services.
    """
    http = http or httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=5), limits=httpx.Limits(max_connections=100)
    )
    db = Database(settings)
    store = Store(db)
    keyring = Keyring(settings)
    agents = AgentRegistry(store)
    shared = build_shared(settings.redis_url, settings.redis_prefix)
    mcp = McpRegistry(http, store, keyring, shared)
    from evokai.llm import providers

    provider_catalog = providers.configure(
        settings.providers_file, settings.aws_region, settings.price_eur_usd
    )
    catalog = catalog or ModelCatalog(
        settings.model_allow, settings.aws_region, providers=provider_catalog
    )
    hub = ChannelHub(store)
    turns = TurnRegistry()
    guard = Guard(store, settings, keyring, shared)
    configure_log_masking(settings.guard_enabled)
    engine = TurnEngine(
        store=store,
        hub=hub,
        turns=turns,
        agents=agents,
        mcp=mcp,
        catalog=catalog,
        settings=settings,
        keyring=keyring,
        guard=guard,
    )
    built = Services(
        settings=settings,
        db=db,
        store=store,
        keyring=keyring,
        auth=Authenticator(
            store, keyring, shared, revocation_ttl=settings.session_token_max_ttl + 300
        ),
        http=http,
        agents=agents,
        mcp=mcp,
        catalog=catalog,
        hub=hub,
        turns=turns,
        engine=engine,
        guard=guard,
        shared=shared,
    )
    from evokai.evals.runner import EvalService

    built.evals = EvalService(built)
    from evokai.knowledge.service import KnowledgeService

    built.knowledge = KnowledgeService(built, embeddings)
    engine.knowledge = built.knowledge
    from evokai.memory.service import MemoryService

    built.memory = MemoryService(built)
    engine.memory = built.memory
    from evokai.evals.redteam.service import RedTeamService

    built.redteam = RedTeamService(built)
    from evokai.costs.service import CostService

    built.costs = CostService(built)
    from evokai.workflows.engine import WorkflowService

    built.workflows = WorkflowService(built)
    from evokai.workflows.durable import DurableAgents

    built.durable = DurableAgents(built)
    from evokai.sandbox.tool import CodeService

    built.code = engine.code = CodeService(settings, code_interpreter)
    from evokai.connections.service import ConnectionService

    built.connections = ConnectionService(
        store=store, keyring=keyring, http=http, shared=shared, settings=settings
    )
    mcp.connections = built.connections
    from evokai.channels.service import ChannelService

    built.channels = ChannelService(built)
    return built
