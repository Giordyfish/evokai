"""Agent definitions: prompt, models, MCP servers, skills, client tools and voice.

They belong to an application and are versioned: the version is a hash of the definition, and a
conversation pins the version it started with.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, field_validator, model_validator

from evokai.guard.ask import AskRule
from evokai.guard.ask import stored as ask_stored
from evokai.llm.caching import PromptCache
from evokai.llm.policy import check_agent_settings


class NavigateDef(BaseModel):
    """The client navigation tool."""

    enabled: bool = True
    description: str = "Take the user to a page of the application."
    path_pattern: str = r"^/[A-Za-z0-9/_?=&.-]*$"
    """Only paths that match are sent to the client; anything else is refused."""


CLIENT_TOOL_NAME = r"^[a-z][a-z0-9_]{1,47}$"
"""The name of a custom client tool (the model sees it as ``client_<name>``)."""
RESERVED_CLIENT_TOOLS = frozenset({"navigate", "chart", "diagram", "ask", "confirm", "memory"})
"""Names of the built-in client tools and grant items, refused for a custom tool (``session``
too: it is the grant of session-declared tools)."""
MAX_CLIENT_TOOL_SCHEMA = 16_384
"""Bytes of a custom client tool's parameters schema (as compact JSON)."""


class ClientToolDef(BaseModel):
    """A custom client tool: the client application runs it (``client_tool`` event)."""

    name: str = Field(pattern=CLIENT_TOOL_NAME)
    description: str = Field(min_length=1, max_length=1_000)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    """The JSON Schema of the arguments (checked as a schema, at most 16 KB)."""
    wait: bool = False
    """False: Evokai sends the event and answers ``ok`` to the model at once. True: the turn waits
    for the client's result (``POST /v1/session/tool-results``), up to the timeout."""

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, v: str) -> str:
        if v in RESERVED_CLIENT_TOOLS or v == "session":
            raise ValueError(f"{v} is reserved for a built-in client tool or grant")
        return v

    @field_validator("parameters")
    @classmethod
    def _schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, separators=(",", ":"))) > MAX_CLIENT_TOOL_SCHEMA:
            raise ValueError("parameters: the schema is larger than 16 KB")
        try:
            Draft202012Validator.check_schema(v)
        except Exception as e:  # jsonschema's SchemaError
            raise ValueError(f"parameters: not a valid JSON Schema ({clip_message(e)})") from e
        return v


def clip_message(e: Exception) -> str:
    """The first line of an error, short.

    Returns:
        At most 200 characters.
    """
    return str(getattr(e, "message", e)).splitlines()[0][:200]


BUILTIN_KINDS = (
    "web_search",
    "web_fetch",
    "code_execution",
    "image_generation",
    "file_search",
    "mcp_server",
    "memory",
)


class BuiltinToolDef(BaseModel):
    """A provider built-in tool (``evokai.engine.builtin_tools``)."""

    kind: Literal[
        "web_search",
        "web_fetch",
        "code_execution",
        "image_generation",
        "file_search",
        "mcp_server",
        "memory",
    ]
    config: dict[str, Any] = Field(default_factory=dict)
    """pydantic-ai's settings of the tool (``max_uses``, ``file_store_ids``...); never a secret."""

    @model_validator(mode="after")
    def _valid(self) -> BuiltinToolDef:
        from evokai.engine.builtin_tools import build

        build(self.kind, self.config)
        return self


class ClientToolsDef(BaseModel):
    """Which client tools the agent has."""

    navigate: NavigateDef = Field(default_factory=NavigateDef)
    chart: bool = True
    diagram: bool = True
    ask: bool = True
    memory: bool = True
    custom: list[ClientToolDef] = Field(default_factory=list, max_length=32)
    """Client tools of the application's own (``docs/client-tools.md``)."""

    @field_validator("custom")
    @classmethod
    def _unique(cls, v: list[ClientToolDef]) -> list[ClientToolDef]:
        names = [t.name for t in v]
        if len(names) != len(set(names)):
            raise ValueError("a custom client tool is declared twice")
        return v


class VoiceDef(BaseModel):
    """How the agent speaks."""

    first_clause_min_chars: int = 48
    """Speak the first clause as soon as it is this long (0 = wait for the first sentence)."""
    fillers: dict[str, str] = Field(default_factory=dict)
    """Tool name (or '*') → what to say while a tool runs longer than the filler delay."""
    prompt_rule: str = (
        "The user is talking to you and your answer is also read aloud: answer in 1-3 short, "
        "natural sentences meant to be heard, with no tables or long lists; key numbers in plain "
        "words; put charts and details in the visuals, not in the text. Do not read codes, "
        "account numbers or personal data aloud: point to the screen instead."
    )
    """Added to the prompt in voice mode. Write it in the agent's language in its definition."""
    voice_id: str = ""
    """Voice for server-side speech."""


class KnowledgeDef(BaseModel):
    """The knowledge bases an agent searches (``evokai.knowledge``)."""

    bases: list[str] = Field(default_factory=list, max_length=20)
    """Names of the application's knowledge bases; the agent gets the ``knowledge_search`` tool."""
    auto: bool = False
    """Search with the user's message before the first model step of every turn."""
    top_k: int | None = Field(default=None, ge=1, le=20)
    """Passages per search (default: the base's own setting)."""

    @field_validator("bases")
    @classmethod
    def _bases(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(x.strip() for x in v if x.strip()))


class MemoryDef(BaseModel):
    """What the agent remembers about a person beyond preferences (``evokai.memory``)."""

    episodes: bool = False
    """Keep a short summary of each finished conversation and recall it when relevant (opt-in)."""
    recall: int = Field(default=6, ge=0, le=20)
    """Semantic facts and episodes recalled per turn (preferences are always recalled)."""


class CodeDef(BaseModel):
    """Isolated code execution for the agent (``evokai.sandbox``): the ``code_run`` tool."""

    enabled: bool = False
    """Offer ``code_run`` (also needs ``EVOKAI_CODE_ENABLED`` and the application's ``code``
    grant)."""


class CapabilityUse(BaseModel):
    """One capability of the catalog (``evokai.capabilities``) as the agent uses it."""

    enabled: bool = False
    """Turned on for this agent (also needs the deployment and the application's grant)."""
    config: dict[str, Any] = Field(default_factory=dict)
    """The capability's settings, checked against its schema in the catalog."""


class PromptRef(BaseModel):
    """A named prompt of the application; without a version, the latest is used."""

    name: str
    version: int | None = None


class ModuleRef(BaseModel):
    """A prompt module the agent may use (``evokai.agents.modules``), with its defaults."""

    name: str = Field(min_length=1, max_length=80)
    """An application prompt; a module when it has a parameter schema."""
    version: int | None = Field(default=None, ge=1)
    """None: the latest version when a conversation starts, then pinned for that conversation."""
    on: bool = True
    """Whether the module is in the prompt unless a session token switches it."""
    params: dict[str, Any] = Field(default_factory=dict)
    """The agent's values for the module's parameters (the schema's defaults fill the rest)."""
    session: bool = False
    """Whether a session token may switch the module or change its parameters."""


Channel = Literal["browser", "server", "email", "teams", "slack"]
TemplateSyntax = Literal["legacy", "handlebars"]
"""How a text is rendered: ``handlebars`` (``{{date}}``), or ``legacy`` for the versions saved
before Handlebars (``{date}``, labels' ``{field|count}``), which keep rendering as they did."""

_HASHED_VOICE_RULE = (
    "The user is talking to you and your answer is also read aloud: answer in 1-3 short, "
    "natural sentences meant to be heard, with no tables or long lists; key numbers in plain "
    "words; put charts and details in the visuals, not in the text. Do not read tax codes, "
    "IBANs, meter codes or personal data aloud: point to the screen instead."
)
"""The default voice rule the first versions were hashed with. An agent that keeps the default
rule is hashed with this text, so rewording the default does not give it a new version."""

_LATER_DEFAULTS: dict[str, Any] = {
    "preload_skills": [],
    "prompt_cache": "off",
    "suggestions": [],
    "security": None,
    "a2a": False,
    "knowledge": None,
    "memory": None,
    "handoff_to": [],
    "code": None,
    "capabilities": {},
    "subagents": [],
    "ask_rules": [],
    "modules": [],
    "template_syntax": "legacy",
    "builtin_tools": [],
    "openai_confirm": False,
}
"""Options added after the first versions, with their defaults (see ``AgentDef.content``)."""


def _browser_only() -> list[Channel]:
    return ["browser"]


class AgentDef(BaseModel):
    """A versioned agent definition."""

    name: str
    title: str = ""
    instructions: str = ""
    """System prompt template (or use `prompt`): Handlebars with ``{{date}}`` and ``{{app}}``."""
    prompt: PromptRef | None = None
    """A versioned prompt of the application, instead of inline instructions."""
    model: str
    fallbacks: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    """Models a session token may pick as primary (besides `model`)."""
    mcp_servers: list[str] = Field(default_factory=list)
    """Names of the application's MCP servers this agent uses."""
    skills: list[str] | None = None
    """Names of the application's skills; None = all of them."""
    preload_skills: list[str] = Field(default_factory=list)
    """Skills whose text is put in the system prompt, so the agent never has to load them (each one
    must be among ``skills``). The others stay on demand."""
    prompt_cache: PromptCache = "off"
    """Prompt caching on Bedrock Anthropic models: ``off``, ``5m`` or ``1h`` (llm.caching)."""
    client_tools: ClientToolsDef = Field(default_factory=ClientToolsDef)
    suggestions: list[str] = Field(default_factory=list, max_length=8)
    """Starter questions the client shows before the first message (in the agent's language)."""
    confirm_labels: dict[str, str | list[str]] = Field(default_factory=dict)
    """Tool name → label template(s) for its confirmation card (evokai.tools.labels)."""
    confirm_values: dict[str, dict[str, str]] = Field(default_factory=dict)
    """Tables of words for ``{{map field "table"}}`` in the label templates."""
    max_tool_calls_per_turn: int = 6
    model_settings: dict[str, Any] = Field(default_factory=dict)
    """Sampling, reasoning effort, priority processing (evokai.llm.policy.ALLOWED_SETTINGS)."""
    voice: VoiceDef = Field(default_factory=VoiceDef)
    temperature: float | None = Field(default=None, ge=0, le=2)
    output_schema: dict[str, Any] | None = None
    """JSON Schema of a structured answer (server runs): the output is validated against it."""
    channels: list[Channel] = Field(default_factory=_browser_only)
    """Where the agent can be used: browser sessions, server runs (``/v1/agents/{name}/run``), and
    the channels beyond the web panel (``email``, ``teams``, ``slack``: ``evokai.channels``)."""
    a2a: bool = False
    """Published to other agents over A2A (an Agent Card and JSON-RPC calls run as server runs),
    when the application is granted the ``a2a`` channel (``evokai.api.a2a_routes``)."""
    security: dict[str, Any] | None = None
    """The agent's security policy, over the application's (``evokai.guard.SecurityPolicy``)."""
    knowledge: KnowledgeDef | None = None
    """Knowledge bases the agent searches, and whether it searches before answering."""
    memory: MemoryDef | None = None
    """Episodes and how many memories a turn recalls (None: the defaults, no episodes). Whether
    anything is remembered at all is the application's ``memory_mode`` and the person's consent."""
    handoff_to: list[str] = Field(default_factory=list, max_length=20)
    """Agents of the same application this agent may hand the conversation to: it gets the
    ``handoff`` tool, and the conversation goes on with the other agent from the next turn."""
    code: CodeDef | None = None
    builtin_tools: list[BuiltinToolDef] = Field(default_factory=list, max_length=len(BUILTIN_KINDS))
    """Provider built-in tools (web search, code execution...), each granted (``builtin``)."""

    @field_validator("builtin_tools")
    @classmethod
    def _one_of_each(cls, v: list[BuiltinToolDef]) -> list[BuiltinToolDef]:
        kinds = [t.kind for t in v]
        if len(kinds) != len(set(kinds)):
            raise ValueError("a built-in tool is listed twice")
        return v

    openai_confirm: bool = False
    """Over the OpenAI-compatible API, offer the tools that change data: a call waits for the
    person's yes in the next request (needs the grant ``openai:confirm``)."""
    """Isolated code execution: ``code_run`` for analysis of data the conversation has."""
    capabilities: dict[str, CapabilityUse] = Field(default_factory=dict)
    """Capabilities of the catalog the agent turns on, with their settings (``code_mode``,
    ``compaction``, ``subagents`` and the pydantic-ai-harness ones): each also needs the deployment
    (``EVOKAI_CAPABILITIES``) and the application's ``capability`` grant."""
    subagents: list[str] = Field(default_factory=list, max_length=10)
    """Agents of the same application this agent may call as tools (``delegate_task``), each with
    its own definition, as the same person; needs the ``subagents`` capability."""
    ask_rules: list[AskRule] = Field(default_factory=list, max_length=100)
    """Rules that make calls of this agent's tools wait for the person's confirmation, on top of
    the deployment's and the application's (in this list the last match wins;
    ``evokai.guard.ask``). They only add confirmations: never lift an ``ask`` of another level."""
    modules: list[ModuleRef] = Field(default_factory=list, max_length=20)
    """Prompt modules rendered after the instructions, in this order (``evokai.agents.modules``):
    each names an application prompt, its default state and parameters, and whether a session
    token may change them."""
    template_syntax: TemplateSyntax = "legacy"
    """The syntax of the instructions and the confirm labels. A definition without it was saved
    before Handlebars and stays ``legacy``; saving through the API or the YAML file makes it
    ``handlebars`` (``evokai.agents.templates``)."""
    version: str = ""

    @field_validator("modules")
    @classmethod
    def _modules(cls, v: list[ModuleRef]) -> list[ModuleRef]:
        names = [m.name for m in v]
        if len(set(names)) != len(names):
            raise ValueError("each module may be listed only once")
        return v

    @field_validator("handoff_to", "subagents")
    @classmethod
    def _handoff(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(x.strip() for x in v if x.strip()))

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, v: dict[str, CapabilityUse]) -> dict[str, CapabilityUse]:
        from evokai.capabilities import check_use

        for name, use in v.items():
            check_use(name, use.config)
        return v

    @field_validator("security")
    @classmethod
    def _security(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        from evokai.guard.config import SecurityPolicy

        return SecurityPolicy.model_validate(v).stored() if v is not None else None

    @field_validator("output_schema")
    @classmethod
    def _schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            Draft202012Validator.check_schema(v)
            if v.get("type") != "object":
                raise ValueError("output_schema must describe an object")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v or not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError("agent name: letters, digits, - and _ only")
        return v

    @field_validator("prompt_cache", mode="before")
    @classmethod
    def _prompt_cache(cls, v: Any) -> Any:
        # YAML reads a bare off as false (and on as true).
        if v is None or v is False:
            return "off"
        if v is True:
            return "5m"
        return v

    @field_validator("preload_skills")
    @classmethod
    def _preload(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @field_validator("model_settings")
    @classmethod
    def _settings(cls, v: dict[str, Any]) -> dict[str, Any]:
        return check_agent_settings(v)

    @model_validator(mode="after")
    def _instructions_or_prompt(self) -> AgentDef:
        if not self.instructions.strip() and not self.prompt:
            raise ValueError("an agent needs instructions or a prompt reference")
        if self.skills is not None:
            missing = [s for s in self.preload_skills if s not in self.skills]
            if missing:
                raise ValueError(f"preload_skills must be among the agent's skills: {missing}")
        return self

    def content(self) -> dict[str, Any]:
        """The definition without its version (what the version is computed from).

        Returns:
            A JSON-able dict. Options added later are left out while at their default, so an agent
            that does not use them keeps its version.
        """
        out = self.model_dump(exclude={"version"})
        out["ask_rules"] = ask_stored(self.ask_rules)
        for key, default in _LATER_DEFAULTS.items():
            if out.get(key) == default:
                out.pop(key, None)
        ct = out.get("client_tools")
        if isinstance(ct, dict) and not ct.get("custom"):
            ct.pop("custom", None)  # custom client tools came later
        return out

    def with_version(self) -> AgentDef:
        """This definition with its content hash as version.

        Returns:
            A copy with `version` set.
        """
        content = self.content()
        voice = content.get("voice")
        if isinstance(voice, dict) and voice.get("prompt_rule") == VoiceDef().prompt_rule:
            content["voice"] = {**voice, "prompt_rule": _HASHED_VOICE_RULE}
        raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
        return self.model_copy(update={"version": hashlib.sha256(raw.encode()).hexdigest()[:12]})
