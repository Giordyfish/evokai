"""Capability grants: what an application may use.

The client application decides its runtime choices (voice engine, voice, storage mode, model);
grants only say which choices exist for it. Every capability is either inherited from the default
or explicitly overridden for the application, and an override can be reset.

Defaults keep what an application could do before grants existed: every model the deployment
and the application allow (as primary or fallback), every registered MCP server and tool, every
skill, every client tool, both voice engines with every speech-to-text engine and voice, the
browser and server channels, and the application's own storage mode. The ``a2a`` channel (agents
published over A2A) is the one item that is off until granted.

Enforced in four places:

- **session token mint**: the browser channel, a requested model, voice engine, voice, storage
  mode and prompt module choice (403 with a ``*_not_granted`` code);
- **turns and server runs**: the agent only gets granted MCP servers, tools, skills and client
  tools; the model chain skips models that are off, and fallback-only models never answer first;
- **voice offers**: server voice and its speech-to-text engine;
- **agent save**: a definition may not reference anything that is not granted.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evokai.agents.definitions import AgentDef
from evokai.core.errors import AppError, BadRequest
from evokai.llm.catalog import bare, normalize

if TYPE_CHECKING:
    from evokai.db import models as m
    from evokai.db.repositories import Store

MODEL = "model"
MCP = "mcp"
TOOL = "tool"
SKILL = "skill"
CLIENT = "client"
VOICE = "voice"
STT = "stt"
TTS = "tts"
STORAGE = "storage"
CHANNEL = "channel"
KNOWLEDGE = "knowledge"
WORKFLOW = "workflow"
CODE = "code"
CONNECTION = "connection"
CAPABILITY = "capability"
MODULE = "module"
KINDS = (
    MODEL,
    MCP,
    TOOL,
    SKILL,
    CLIENT,
    VOICE,
    STT,
    TTS,
    STORAGE,
    CHANNEL,
    KNOWLEDGE,
    WORKFLOW,
    CODE,
    CONNECTION,
    CAPABILITY,
    MODULE,
)
KNOWLEDGE_ALL = "*"
"""The knowledge grant of the whole capability (each base is also an item of its own)."""

WORKFLOW_ITEMS = ("*", "autonomous")
"""Workflows as a whole (default on) and ``autonomous``: tool steps a definition marks ``safe``
may change data without an approval (default off)."""

CODE_ITEMS = ("*",)
"""Isolated code execution (``code_run``) as a whole, off until granted."""

CLIENT_TOOLS = ("navigate", "chart", "diagram", "ask", "confirm", "memory")
VOICE_ENGINES = ("browser", "server")
STORAGE_MODES = ("evokai", "external")
CHANNELS = ("browser", "server", "a2a", "email", "teams", "slack")
MESSAGING_CHANNELS = ("email", "teams", "slack")
"""Channels beyond the web panel (``evokai.channels``)."""
CONNECTION_PROVIDERS = ("microsoft365", "google", "github", "linear", "notion", "slack")
CONNECTION_FAMILIES = ("mail", "calendar", "files")
CONNECTION_FAMILIES_OF: dict[str, tuple[str, ...]] = {
    "microsoft365": CONNECTION_FAMILIES,
    "google": CONNECTION_FAMILIES,
}
"""The tool families of each provider (GitHub, Linear, Notion and Slack have none: the provider
is the whole grant)."""
CONNECTION_ITEMS = CONNECTION_PROVIDERS + tuple(
    f"{p}/{f}" for p in CONNECTION_PROVIDERS for f in CONNECTION_FAMILIES_OF.get(p, ())
)
"""Per-user OAuth connections: a provider as a whole (off until granted) and each of its tool
families (on once the provider is)."""
OFF_BY_DEFAULT = frozenset(
    {(CHANNEL, "a2a"), (WORKFLOW, "autonomous")}
    | {(CHANNEL, c) for c in MESSAGING_CHANNELS}
    | {(CONNECTION, p) for p in CONNECTION_PROVIDERS}
    | {(CODE, "*")}
)
"""Items an application does not have until it is granted them: A2A publishes agents to anyone
holding the server key, autonomous workflows change data without a person deciding, channels
beyond the web panel let outsiders write to an agent, connections let an agent act as a person in
a third-party system, and code execution sends conversation data to a sandbox, so all of them are
opt-in. Every ``capability`` of the catalog is off until granted too (``Grants.default``)."""
MODEL_LEVELS = ("any", "fallback", "off")

YAML_SECTIONS = {
    "models": MODEL,
    "mcp_servers": MCP,
    "tools": TOOL,
    "skills": SKILL,
    "client_tools": CLIENT,
    "voice": VOICE,
    "stt": STT,
    "tts": TTS,
    "storage": STORAGE,
    "channels": CHANNEL,
    "knowledge": KNOWLEDGE,
    "workflows": WORKFLOW,
    "code": CODE,
    "connections": CONNECTION,
    "capabilities": CAPABILITY,
    "modules": MODULE,
}
"""Sections of ``grants:`` in an application YAML (and ``EVOKAI_BOOTSTRAP_APPS``) → kinds."""

BROWSER_VOICE_REGIONS = frozenset({"device", "local", "browser"})
"""A catalogue voice whose region is one of these is synthesised on the device (browser speech);
any other region is a server voice."""


class NotGranted(AppError):
    """A request for something the application is not granted."""

    def __init__(self, what: str, message: str) -> None:
        """Create the error.

        Args:
            what: model, voice_engine, voice, stt, storage_mode or channel.
            message: For the application's developers.
        """
        super().__init__(message, code=f"{what}_not_granted", status=403)


def normalize_name(kind: str, name: str) -> str:
    """The stored form of an item's name (models get their provider).

    Returns:
        The name.
    """
    name = name.strip()
    return normalize(name) if kind == MODEL else name


def check_value(kind: str, name: str, value: Any) -> Any:
    """Validate an override.

    Returns:
        The value.

    Raises:
        BadRequest: Unknown kind or item, or a value of the wrong type.
    """
    if kind not in KINDS:
        raise BadRequest(f"Unknown grant kind: {kind}", code="grant")
    if not name or len(name) > 300:
        raise BadRequest("A grant needs a name (up to 300 characters).", code="grant")
    fixed = {
        CLIENT: CLIENT_TOOLS,
        VOICE: VOICE_ENGINES,
        STORAGE: STORAGE_MODES,
        CHANNEL: CHANNELS,
        WORKFLOW: WORKFLOW_ITEMS,
        CODE: CODE_ITEMS,
        CONNECTION: CONNECTION_ITEMS,
    }
    if kind in fixed and name not in fixed[kind]:
        raise BadRequest(f"Unknown {kind}: {name}", code="grant")
    if kind == CAPABILITY:
        from evokai.capabilities import all_ids

        if name not in all_ids():
            raise BadRequest(f"Unknown capability: {name}", code="grant")
    if kind == TOOL and "/" not in name:
        raise BadRequest("A tool grant is named server/tool.", code="grant")
    if kind == TTS and name.partition("/")[0] not in VOICE_ENGINES:
        raise BadRequest("A voice grant is named browser/<voice> or server/<voice>.", code="grant")
    if kind == MODEL:
        if value not in MODEL_LEVELS:
            raise BadRequest("A model grant is any, fallback or off.", code="grant")
    elif not isinstance(value, bool):
        raise BadRequest(f"A {kind} grant is true or false.", code="grant")
    return value


def _is_pattern(name: str) -> bool:
    return any(c in name for c in "*?[")


@dataclass(frozen=True)
class Grants:
    """The effective grants of one application: its overrides over the defaults."""

    overrides: Mapping[tuple[str, str], Any] = field(default_factory=dict)
    storage_mode: str = "evokai"
    """The application's own storage mode (the default grant of storage)."""

    @classmethod
    def of(cls, rows: Iterable[Any], storage_mode: str | None) -> Grants:
        """Build from stored rows (``kind``, ``name``, ``value``).

        Returns:
            The grants.
        """
        return cls(
            overrides={(r.kind, r.name): r.value for r in rows},
            storage_mode=storage_mode or "evokai",
        )

    def override(self, kind: str, name: str) -> Any:
        """The explicit value for an item, if any.

        Returns:
            The value, or None when the item follows the default.
        """
        return self.overrides.get((kind, name))

    def default(self, kind: str, name: str) -> Any:
        """What an item is without an override.

        Returns:
            ``any`` for models, the application's own mode for storage, False for the A2A
            channel, autonomous workflows and code execution, True otherwise.
        """
        if (kind, name) in OFF_BY_DEFAULT or kind == CAPABILITY:
            return False
        if kind == MODEL:
            return "any"
        if kind == STORAGE:
            return name == self.storage_mode
        return True

    def _flag(self, kind: str, name: str) -> bool:
        value = self.override(kind, name)
        return bool(self.default(kind, name) if value is None else value)

    def model(self, model: str) -> str:
        """How a model may be used: as primary and fallback, as fallback only, or not at all.

        An exact override wins; else the longest matching pattern (``bedrock:eu.anthropic.*``).

        Returns:
            ``any``, ``fallback`` or ``off``.
        """
        name = normalize(model)
        exact = self.override(MODEL, name)
        if exact is not None:
            return str(exact)
        patterns = sorted(
            (n for k, n in self.overrides if k == MODEL and _is_pattern(n)), key=len, reverse=True
        )
        for p in patterns:
            if fnmatch.fnmatchcase(name, p):
                return str(self.overrides[MODEL, p])
        return "any"

    def mcp(self, server: str) -> bool:
        """Whether an MCP server may be used.

        Returns:
            True if granted.
        """
        return self._flag(MCP, server)

    def tool(self, server: str, tool: str) -> bool:
        """Whether one tool of an MCP server may be used (its server must be granted too).

        Args:
            server: The server's name.
            tool: The tool's own name (without the server's prefix).

        Returns:
            True if granted.
        """
        return self.mcp(server) and self._flag(TOOL, f"{server}/{tool}")

    def skill(self, name: str) -> bool:
        """Whether a skill may be enabled.

        Returns:
            True if granted.
        """
        return self._flag(SKILL, name)

    def client(self, name: str) -> bool:
        """Whether a client tool may be used (navigate, chart, diagram, ask, confirm, memory).

        Returns:
            True if granted.
        """
        return self._flag(CLIENT, name)

    def voice(self, engine: str) -> bool:
        """Whether a voice engine may be used (browser or server speech).

        Returns:
            True if granted.
        """
        return self._flag(VOICE, engine)

    def stt(self, engine: str) -> bool:
        """Whether a server speech-to-text engine may be used.

        Returns:
            True if server voice and this engine are granted.
        """
        return self.voice("server") and self._flag(STT, engine)

    def tts(self, engine: str, voice: str) -> bool:
        """Whether a voice may be used with an engine.

        Returns:
            True if the engine and the voice are granted.
        """
        return self.voice(engine) and self._flag(TTS, f"{engine}/{voice}")

    def storage(self, mode: str) -> bool:
        """Whether conversations may live in this storage mode.

        Returns:
            True if granted.
        """
        return self._flag(STORAGE, mode)

    def knowledge(self, base: str | None = None) -> bool:
        """Whether knowledge bases may be searched (one base, or the capability as a whole).

        Returns:
            True when the capability (and the base, when named) is granted.
        """
        return self._flag(KNOWLEDGE, KNOWLEDGE_ALL) and (
            base is None or self._flag(KNOWLEDGE, base)
        )

    def workflow(self, name: str = "*") -> bool:
        """Whether workflows run (``*``), or may change data without an approval (``autonomous``).

        ``autonomous`` needs ``*`` too.

        Returns:
            True if granted.
        """
        return self._flag(WORKFLOW, "*") and (name == "*" or self._flag(WORKFLOW, name))

    def code(self) -> bool:
        """Whether agents may run code in the sandbox (``code_run``); off until granted.

        Returns:
            True if granted.
        """
        return self._flag(CODE, "*")

    def capability(self, name: str) -> bool:
        """Whether agents may use a capability of the catalog (``evokai.capabilities``).

        Every capability is off until granted.

        Returns:
            True if granted.
        """
        return self._flag(CAPABILITY, name)

    def connection(self, provider: str, family: str | None = None) -> bool:
        """Whether people may connect a provider (and its tools of one family may be used).

        A family needs its provider too.

        Returns:
            True if granted.
        """
        return self._flag(CONNECTION, provider) and (
            family is None or self._flag(CONNECTION, f"{provider}/{family}")
        )

    def module(self, name: str) -> bool:
        """Whether session tokens may switch a prompt module or change its parameters.

        Returns:
            True if granted (the default).
        """
        return self._flag(MODULE, name)

    def channel(self, name: str) -> bool:
        """Whether agents may be reached this way.

        Browser sessions, server runs, A2A, e-mail, Teams or Slack.

        Returns:
            True if granted.
        """
        return self._flag(CHANNEL, name)

    # Applying grants

    def restrict(self, agent: AgentDef) -> AgentDef:
        """The agent as this application may run it: ungranted MCP servers and client tools off.

        Tools of granted servers, skills and models are filtered where they are used (toolbox,
        skills, model chain).

        Returns:
            A copy of the definition (same version).
        """
        ct = agent.client_tools
        tools = ct.model_copy(
            update={
                "navigate": ct.navigate.model_copy(
                    update={"enabled": ct.navigate.enabled and self.client("navigate")}
                ),
                "chart": ct.chart and self.client("chart"),
                "diagram": ct.diagram and self.client("diagram"),
                "ask": ct.ask and self.client("ask"),
                "memory": ct.memory and self.client("memory"),
            }
        )
        knowledge = agent.knowledge
        if knowledge is not None:
            knowledge = knowledge.model_copy(
                update={"bases": [b for b in knowledge.bases if self.knowledge(b)]}
            )
        code = agent.code
        if code is not None and code.enabled and not self.code():
            code = code.model_copy(update={"enabled": False})
        return agent.model_copy(
            update={
                "client_tools": tools,
                "mcp_servers": [s for s in agent.mcp_servers if self.mcp(s)],
                "knowledge": knowledge,
                "code": code,
            }
        )

    def chain(self, models: list[str]) -> list[str]:
        """A model chain as granted.

        Models that are off are dropped, and a fallback-only model never answers first (it is
        skipped until a model granted as primary leads the chain).

        Returns:
            The models to try, in order.
        """
        out: list[str] = []
        for model in models:
            level = self.model(model)
            if level == "off" or (level == "fallback" and not out):
                continue
            if bare(model) not in {bare(x) for x in out}:
                out.append(model)
        return out

    def problems(self, agent: AgentDef, explicit_client_tools: set[str] | None = None) -> list[str]:
        """What an agent definition references without a grant.

        Args:
            agent: The definition.
            explicit_client_tools: Client tools the definition sets itself (defaults that are
                not granted are simply not offered); None = every enabled one counts.

        Returns:
            One line per problem (empty when everything is granted).
        """
        out = []
        if self.model(agent.model) != "any":
            out.append(f"model {agent.model} is not granted as primary")
        out += [
            f"model {m} is not granted as primary" for m in agent.models if self.model(m) != "any"
        ]
        out += [f"fallback {m} is not granted" for m in agent.fallbacks if self.model(m) == "off"]
        out += [f"MCP server {s} is not granted" for s in agent.mcp_servers if not self.mcp(s)]
        out += [f"skill {s} is not granted" for s in agent.skills or [] if not self.skill(s)]
        if agent.skills is None:
            out += [f"skill {s} is not granted" for s in agent.preload_skills if not self.skill(s)]
        ct = agent.client_tools
        enabled = {
            "navigate": ct.navigate.enabled,
            "chart": ct.chart,
            "diagram": ct.diagram,
            "ask": ct.ask,
            "memory": ct.memory,
        }
        for name, on in enabled.items():
            if not on or self.client(name):
                continue
            if explicit_client_tools is None or name in explicit_client_tools:
                out.append(f"client tool {name} is not granted")
        bases = agent.knowledge.bases if agent.knowledge else []
        out += [f"knowledge base {b} is not granted" for b in bases if not self.knowledge(b)]
        if "server" in agent.channels and not self.channel("server"):
            out.append("server runs are not granted")
        if "browser" in agent.channels and not self.channel("browser"):
            out.append("browser sessions are not granted")
        if agent.a2a and not self.channel("a2a"):
            out.append("A2A is not granted")
        if agent.code is not None and agent.code.enabled and not self.code():
            out.append("code execution is not granted")
        out += [
            f"capability {name} is not granted"
            for name, use in agent.capabilities.items()
            if use.enabled and not self.capability(name)
        ]
        out += [
            f"the {c} channel is not granted"
            for c in MESSAGING_CHANNELS
            if c in agent.channels and not self.channel(c)
        ]
        return out


def explicit_client_tools(raw: Mapping[str, Any] | AgentDef) -> set[str]:
    """Client tools an agent definition sets itself (as opposed to defaults).

    Returns:
        Their names.
    """
    if isinstance(raw, AgentDef):
        return set(raw.client_tools.model_fields_set)
    tools = raw.get("client_tools")
    return set(tools) if isinstance(tools, Mapping) else set()


def from_yaml(section: Mapping[str, Any]) -> dict[tuple[str, str], Any]:
    """Grants from the ``grants:`` section of an application YAML.

    Returns:
        (kind, name) → value.

    Raises:
        BadRequest: An unknown section or a malformed value.
    """
    out: dict[tuple[str, str], Any] = {}
    for key, items in (section or {}).items():
        kind = YAML_SECTIONS.get(str(key))
        if kind is None:
            raise BadRequest(f"grants: unknown section {key}", code="grant")
        if not isinstance(items, Mapping):
            raise BadRequest(f"grants.{key} must be a mapping", code="grant")
        for name, value in items.items():
            n = normalize_name(kind, str(name))
            if kind == MODEL and isinstance(value, bool):
                value = "any" if value else "off"  # YAML 1.1 reads a bare off as false
            out[kind, n] = check_value(kind, n, value)
    return out


async def grants_of(store: Store, app: m.Application) -> Grants:
    """The effective grants of an application.

    Returns:
        Its grants.
    """
    return Grants.of(await store.grants.all(app.id), app.storage_mode)
