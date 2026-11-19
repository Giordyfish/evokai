"""Every capability Evokai offers: its own and all of pydantic-ai-harness.

One row per capability: its id, where it comes from, the extra that installs its dependencies, the
environment variables it needs, its group, and whether it reaches the host (a shell, files,
containers, a kernel, ssh). The description is the class's own docstring. ``wiring`` says what
Evokai still has to provide before an agent can rely on it beyond being enabled and constructed
(the follow-up wave): an answerer, a store, credentials kept per application, a worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HOST_LOCAL = "local"
"""Runs on the machine of the Evokai service (a local container, a VM, a kernel): never enabled."""
HOST_WORKSPACE = "workspace"
"""Gives the agent a shell, files or code search on the run's workspace: allowed only with a
remote sandbox (``sandbox`` in its settings) supplying that workspace."""
HOST_REMOTE = "remote"
"""A remote sandbox: supplies the workspace from outside the Evokai service."""


@dataclass(frozen=True)
class Entry:
    """One capability of the catalog."""

    id: str
    cls: str
    """Class name in its module."""
    module: str
    """``pydantic_ai_harness.<module>``, or ``evokai.<module>`` for Evokai's own."""
    group: str
    """``evokai``, ``context``, ``safety``, ``flow``, ``search``, ``integration``, ``workspace``,
    ``sandbox``, ``durability``, ``data``."""
    extra: str | None = None
    """The ``pydantic-ai-harness`` extra with its dependencies (None: none needed)."""
    credentials: tuple[str, ...] = ()
    """Environment variables it needs (any one of a ``a|b`` pair)."""
    host: str | None = None
    """``local``, ``workspace``, ``remote`` (see the constants) or None."""
    sample: dict[str, Any] = field(default_factory=dict)
    """Settings enough to construct it (the smoke test, the console's starting point)."""
    wiring: str = ""
    """What Evokai still provides before agents rely on it (empty: nothing)."""
    models: tuple[str, ...] = ()
    """Setting names that hold a model: Evokai resolves them within the allow-lists and grants."""
    settings: tuple[str, ...] | None = None
    """The settings of Evokai's own capabilities (None: the harness class's constructor says)."""

    @property
    def description(self) -> str:
        """What it does, in one line (static text: listing the catalog imports nothing).

        Returns:
            The description.
        """
        return DESCRIPTIONS.get(self.id, "")

    @property
    def path(self) -> str:
        """The importable module.

        Returns:
            The dotted path.
        """
        if self.module.startswith("evokai."):
            return self.module
        return f"pydantic_ai_harness.{self.module}"


def _h(
    id: str,
    cls: str,
    module: str,
    group: str,
    *,
    extra: str | None = None,
    credentials: tuple[str, ...] = (),
    host: str | None = None,
    sample: dict[str, Any] | None = None,
    wiring: str = "",
    models: tuple[str, ...] = (),
    settings: tuple[str, ...] | None = None,
) -> Entry:
    return Entry(
        id=id,
        cls=cls,
        module=module,
        group=group,
        extra=extra,
        credentials=credentials,
        host=host,
        sample=sample or {},
        wiring=wiring,
        models=models,
        settings=settings,
    )


_MODEL = {"model": "test"}
"""A sample model setting (resolved by Evokai: the agent's own model when ``test``)."""

ENTRIES: tuple[Entry, ...] = (
    # Evokai's own, wired end to end
    _h(
        "code_mode",
        "EvokaiCodeMode",
        "evokai.engine.code_mode",
        "evokai",
        extra="codemode",
        settings=("timeout_seconds", "memory_mb", "tool_calls"),
    ),
    _h(
        "compaction",
        "EvokaiCompaction",
        "evokai.engine.compaction",
        "evokai",
        settings=("keep_tokens", "buffer", "model"),
    ),
    _h("subagents", "EvokaiSubAgents", "evokai.engine.subagents", "evokai", settings=()),
    # Context and history
    _h(
        "clamp_oversized_messages",
        "ClampOversizedMessages",
        "compaction",
        "context",
        sample={"max_part_chars": 50_000},
    ),
    _h("clear_tool_results", "ClearToolResults", "compaction", "context"),
    _h(
        "compact_on_request",
        "CompactOnRequest",
        "compaction",
        "context",
        wiring="a strategy object (built from a summarizing compaction in the settings)",
    ),
    _h(
        "deduplicate_file_reads",
        "DeduplicateFileReads",
        "compaction",
        "context",
        wiring="a file key function (only meaningful with file tools)",
    ),
    _h(
        "fallback_compaction",
        "FallbackCompaction",
        "compaction",
        "context",
        wiring="a chain of strategy objects",
    ),
    _h(
        "relevance_compaction",
        "RelevanceCompaction",
        "compaction",
        "context",
        sample={**_MODEL, "max_fraction": 0.8},
        models=("model",),
    ),
    _h(
        "report_context_usage",
        "ReportContextUsage",
        "compaction",
        "context",
        wiring="a callback to the console",
    ),
    _h("sliding_window_compaction", "SlidingWindowCompaction", "compaction", "context"),
    _h(
        "summarizing_compaction",
        "SummarizingCompaction",
        "compaction",
        "context",
        wiring="the summary lives in the run only: Evokai's own compaction stores it",
    ),
    _h(
        "tiered_compaction",
        "TieredCompaction",
        "compaction",
        "context",
        wiring="tiers of strategy objects",
    ),
    _h(
        "warn_near_limits", "WarnNearLimits", "compaction", "context", sample={"max_iterations": 20}
    ),
    _h(
        "tool_output_limits",
        "ToolOutputLimits",
        "tool_output_limits",
        "context",
    ),
    _h(
        "system_reminders",
        "SystemReminders",
        "system_reminders",
        "context",
        sample={"reminders": [{"content": "Stay on the person's request."}]},
    ),
    _h("warn_on_cache_busts", "WarnOnCacheBusts", "warn_on_cache_busts", "context"),
    # Safety and review
    _h(
        "approval_reviewer",
        "ApprovalReviewer",
        "approval_reviewer",
        "safety",
        sample={**_MODEL, "question": "Is this call safe for the person who asked?"},
        models=("model",),
        wiring="acts only inside what the agent is granted; its decisions are traced",
    ),
    _h(
        "permissions",
        "Permissions",
        "permissions",
        "safety",
        wiring="Evokai's confirmation stays the rule; its modes apply to workspace tools only",
    ),
    _h(
        "tool_call_judge",
        "ToolCallJudge",
        "tool_call_judge",
        "safety",
        sample={**_MODEL, "question": "Does this call match what the person asked?"},
        models=("model",),
    ),
    _h(
        "trajectory_judge",
        "TrajectoryJudge",
        "trajectory_judge",
        "safety",
        sample=_MODEL,
        models=("model",),
    ),
    _h(
        "input_guardrail",
        "InputGuardrail",
        "guardrails",
        "safety",
    ),
    _h(
        "output_guardrail",
        "OutputGuardrail",
        "guardrails",
        "safety",
    ),
    _h("tool_guardrail", "ToolGuardrail", "guardrails", "safety"),
    _h(
        "prompt_injection_defender",
        "PromptInjectionDefender",
        "prompt_injection_defender",
        "safety",
        extra="prompt-injection-defender",
    ),
    _h("artifact_scanner", "ArtifactScanner", "artifact_scanning", "safety"),
    _h("detect_loops", "DetectLoops", "detect_loops", "safety"),
    _h("tool_budgets", "ToolBudgets", "tool_budgets", "safety"),
    _h(
        "spend_limits",
        "SpendLimits",
        "spend_limits",
        "safety",
        wiring="Evokai's caps stay the rule; budgets are shares of caps or tokens",
    ),
    # Flow and reasoning
    _h("advisor", "Advisor", "advisor", "flow", sample=_MODEL, models=("model",)),
    _h("planning", "Planning", "planning", "flow"),
    _h(
        "refine_output",
        "RefineOutput",
        "refine_output",
        "flow",
        wiring="server runs only: a turn's answer is shown while it is written",
    ),
    _h(
        "repair_tool_arguments",
        "RepairToolArguments",
        "repair_tool_arguments",
        "flow",
        extra="repair-tool-arguments",
    ),
    _h("background_tools", "BackgroundTools", "background_tools", "flow"),
    _h(
        "dynamic_workflow",
        "DynamicWorkflow",
        "dynamic_workflow",
        "flow",
        extra="dynamic-workflow",
        wiring="named agents (Evokai agents of the application)",
    ),
    _h(
        "ask_user",
        "AskUser",
        "ask_user",
        "flow",
    ),
    _h(
        "memory",
        "Memory",
        "memory",
        "flow",
    ),
    _h(
        "conversation_search",
        "ConversationSearch",
        "conversation_search",
        "flow",
    ),
    _h(
        "step_persistence",
        "StepPersistence",
        "step_persistence",
        "flow",
    ),
    _h("peers", "Peers", "peers", "flow", wiring="a peer network between sessions"),
    _h(
        "capability_creation",
        "CapabilityCreation",
        "capability_creation",
        "flow",
        host=HOST_WORKSPACE,
        sample={"directory": "capabilities"},
        wiring="writes Python modules: only in a remote sandbox",
    ),
    _h(
        "managed_prompt",
        "ManagedPrompt",
        "logfire",
        "flow",
        extra="logfire",
        credentials=("LOGFIRE_TOKEN",),
        sample={"name": "assistant", "default": "You are an assistant."},
    ),
    # Search and research
    _h("exa_search", "ExaSearch", "exa", "search", extra="exa", credentials=("EXA_API_KEY",)),
    _h("exa_agent", "ExaAgent", "exa", "search", extra="exa", credentials=("EXA_API_KEY",)),
    _h(
        "keenable_search", "KeenableSearch", "keenable", "search", credentials=("KEENABLE_API_KEY",)
    ),
    _h(
        "you_search",
        "YouSearch",
        "youdotcom",
        "search",
        extra="youdotcom",
        credentials=("YDC_API_KEY",),
    ),
    _h(
        "you_research",
        "YouResearch",
        "youdotcom",
        "search",
        extra="youdotcom",
        credentials=("YDC_API_KEY",),
    ),
    _h("researcher", "Researcher", "researcher", "search", extra="researcher"),
    _h(
        "smart_search",
        "SmartSearch",
        "smart_search",
        "search",
        host=HOST_WORKSPACE,
        sample=_MODEL,
        models=("model",),
    ),
    _h("pydantic_ai_docs", "PydanticAIDocs", "pydantic_ai_docs", "search"),
    # Integrations (MCP servers of third parties)
    _h(
        "day_ai",
        "DayAI",
        "day_ai",
        "integration",
        extra="day-ai",
        credentials=("DAY_AI_ACCESS_TOKEN",),
    ),
    _h(
        "grain", "Grain", "grain", "integration", extra="grain", credentials=("GRAIN_ACCESS_TOKEN",)
    ),
    _h(
        "ordinal",
        "Ordinal",
        "ordinal",
        "integration",
        extra="ordinal",
        credentials=("ORDINAL_ACCESS_TOKEN",),
    ),
    _h(
        "posthog",
        "PostHog",
        "posthog",
        "integration",
        extra="posthog",
        credentials=("POSTHOG_PERSONAL_API_KEY",),
    ),
    _h(
        "pylon", "Pylon", "pylon", "integration", extra="pylon", credentials=("PYLON_ACCESS_TOKEN",)
    ),
    _h(
        "stackone",
        "StackOne",
        "stackone",
        "integration",
        extra="stackone",
        credentials=("STACKONE_API_KEY",),
        sample={"account_id": "account"},
    ),
    _h("github", "GitHub", "github", "integration", extra="github", credentials=("GITHUB_TOKEN",)),
    _h(
        "linear",
        "Linear",
        "linear",
        "integration",
        extra="linear",
        credentials=("LINEAR_ACCESS_TOKEN",),
    ),
    _h(
        "notion",
        "Notion",
        "notion",
        "integration",
        extra="notion",
        credentials=("NOTION_ACCESS_TOKEN",),
    ),
    _h("slack", "Slack", "slack", "integration", extra="slack", credentials=("SLACK_USER_TOKEN",)),
    _h(
        "google_workspace",
        "GoogleWorkspace",
        "google_workspace",
        "integration",
        extra="google-workspace",
        credentials=("GOOGLE_ACCESS_TOKEN",),
        sample={"services": ["drive"]},
    ),
    _h(
        "logfire_mcp",
        "LogfireMCP",
        "logfire_mcp",
        "integration",
        extra="logfire-mcp",
        credentials=("LOGFIRE_API_KEY",),
    ),
    # Data and media
    _h(
        "pixeltable",
        "Pixeltable",
        "pixeltable",
        "data",
        extra="pixeltable",
        wiring="a Pixeltable home outside the read-only image",
    ),
    _h(
        "browser_use",
        "BrowserUse",
        "browser_use",
        "data",
        extra="browser-use",
        host=HOST_LOCAL,
        wiring="in the capability worker (workers/capability, browser image), an MCP server",
    ),
    _h("localstack", "LocalStack", "localstack", "data", host=HOST_LOCAL),
    _h(
        "playwright",
        "PlaywrightBrowser",
        "playwright",
        "data",
        extra="playwright",
        host=HOST_LOCAL,
        wiring="in the capability worker (workers/capability, browser image), an MCP server",
    ),
    # Workspaces: shell, files and code on the run's workspace (remote sandbox only)
    _h("shell", "Shell", "shell", "workspace", host=HOST_WORKSPACE),
    _h("file_system", "FileSystem", "file_system", "workspace", host=HOST_WORKSPACE),
    _h("coder", "Coder", "coder", "workspace", extra="coder", host=HOST_WORKSPACE),
    _h("repo_context", "RepoContext", "repo_context", "workspace", host=HOST_WORKSPACE),
    _h(
        "skills_dirs",
        "Skills",
        "skills",
        "workspace",
        extra="skills",
        host=HOST_WORKSPACE,
        sample={"directories": ["skills"]},
        wiring="harness skills from workspace folders (Evokai's own skills are separate)",
    ),
    _h("macroscope", "Macroscope", "macroscope", "workspace", host=HOST_WORKSPACE),
    _h(
        "ipython_code_mode",
        "IPythonCodeMode",
        "ipython_code_mode",
        "workspace",
        extra="ipython",
        host=HOST_LOCAL,
    ),
    # Sandboxes: local ones run on the service's machine and are never enabled
    _h("docker_container", "DockerContainer", "docker_container", "sandbox", host=HOST_LOCAL),
    _h("docker_sandbox", "DockerSandbox", "docker_sandbox", "sandbox", host=HOST_LOCAL),
    _h(
        "bubblewrap_workspace",
        "BubblewrapWorkspace",
        "bubblewrap_sandbox",
        "sandbox",
        host=HOST_LOCAL,
    ),
    _h(
        "boxlite_sandbox",
        "BoxLiteSandbox",
        "boxlite_sandbox",
        "sandbox",
        extra="boxlite",
        host=HOST_LOCAL,
    ),
    _h("smol_sandbox", "SmolSandbox", "smol_sandbox", "sandbox", extra="smol", host=HOST_LOCAL),
    _h(
        "ssh_workspace",
        "SSHWorkspace",
        "ssh_workspace",
        "sandbox",
        host=HOST_REMOTE,
        sample={"destination": "sandbox.example"},
    ),
    _h(
        "e2b_sandbox",
        "E2BSandbox",
        "e2b_sandbox",
        "sandbox",
        extra="e2b",
        credentials=("E2B_API_KEY",),
        host=HOST_REMOTE,
    ),
    _h(
        "modal_sandbox",
        "ModalSandbox",
        "modal_sandbox",
        "sandbox",
        extra="modal",
        credentials=("MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"),
        host=HOST_REMOTE,
    ),
    _h(
        "daytona_sandbox",
        "DaytonaSandbox",
        "daytona_sandbox",
        "sandbox",
        extra="daytona",
        credentials=("DAYTONA_API_KEY",),
        host=HOST_REMOTE,
    ),
    _h(
        "kubernetes_sandbox",
        "KubernetesSandbox",
        "kubernetes_sandbox",
        "sandbox",
        extra="kubernetes",
        host=HOST_REMOTE,
        wiring="in the capability worker (workers/capability, CAPABILITY=kubernetes), an MCP server",
    ),
    _h(
        "render_sandbox",
        "RenderSandbox",
        "render_sandbox",
        "sandbox",
        extra="render",
        credentials=("RENDER_API_KEY", "RENDER_WORKSPACE_ID"),
        host=HOST_REMOTE,
    ),
    _h(
        "sprites_sandbox",
        "SpritesSandbox",
        "sprites_sandbox",
        "sandbox",
        extra="sprites",
        credentials=("SPRITE_TOKEN",),
        host=HOST_REMOTE,
    ),
    _h(
        "opensandbox",
        "OpenSandbox",
        "opensandbox_sandbox",
        "sandbox",
        extra="opensandbox",
        credentials=("OPEN_SANDBOX_API_KEY", "OPEN_SANDBOX_DOMAIN"),
        host=HOST_REMOTE,
    ),
    _h(
        "boxd_sandbox",
        "BoxdSandbox",
        "boxd_sandbox",
        "sandbox",
        extra="boxd",
        credentials=("BOXD_API_KEY",),
        host=HOST_REMOTE,
    ),
    # Durable execution
    _h(
        "absurd_durability",
        "AbsurdDurability",
        "absurd",
        "durability",
        extra="absurd",
        wiring="runs only inside an Absurd task: an Absurd queue and worker running Evokai runs",
    ),
    _h(
        "azure_durable_task",
        "AzureDurableTaskDurability",
        "azure_durable_task",
        "durability",
        extra="azure-durable-task",
        wiring="runs only inside an orchestration: a Task Hub and a worker in Azure",
    ),
    _h(
        "pgtask_durability",
        "PGTaskDurability",
        "pgtask",
        "durability",
        extra="pgtask",
        wiring="through workflows: durable agent steps (Postgres, EVOKAI_WORKFLOW_DURABLE_AGENTS)",
    ),
    _h(
        "aws_lambda_durability",
        "AWSLambdaDurability",
        "aws_lambda",
        "durability",
        extra="aws-lambda",
        wiring="runs only inside a durable Lambda function holding Evokai's code",
    ),
    _h(
        "render_workflows",
        "RenderWorkflows",
        "render",
        "durability",
        extra="render",
        credentials=("RENDER_API_KEY",),
        wiring="runs only inside a Render Workflows app and its service",
    ),
)
"""The catalog, in the order the console lists it."""

BY_ID: dict[str, Entry] = {e.id: e for e in ENTRIES}

LITE_LEFT_OUT: frozenset[str] = frozenset({
    "pixeltable",
    "prompt-injection-defender-ml",
    "playwright",
    "boxlite",
    "smol",
    "temporal",
    "ipython",
    "daytona",
})
"""The harness extras the lite image leaves out (over 30 MB each; ``harness-lite`` in
pyproject.toml): their capabilities show as not installed there, «not in the lite image»."""

OWN_SETTINGS: dict[str, dict[str, dict[str, Any]]] = {
    "detect_loops": {
        "action": {
            "type": "string",
            "enum": ["nudge", "stop"],
            "default": "nudge",
            "description": "nudge: tell the model and go on; stop: end the turn (max_steps).",
        }
    },
    "system_reminders": {
        "reminders": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["content"],
                "properties": {
                    "content": {"type": "string", "maxLength": 2000},
                    "interval": {"type": "integer", "minimum": 1, "default": 1},
                    "first_after": {"type": "integer", "minimum": 1},
                    "max_fires": {"type": "integer", "minimum": 1},
                    "scope": {"type": "string", "enum": ["request", "conversation"]},
                    "skip_unchanged": {"type": "boolean"},
                },
            },
        }
    },
    "tool_output_limits": {
        "over": {
            "type": "integer",
            "minimum": 200,
            "description": "Characters of a tool result above which it is reduced.",
        },
        "action": {
            "type": "string",
            "enum": ["spill", "truncate"],
            "default": "spill",
            "description": "spill: kept in Evokai's database and readable on demand; truncate.",
        },
    },
}
"""Settings Evokai reads itself for a harness capability (merged into its schema)."""

_ACTS_AS = {
    "acts_as": {
        "type": "string",
        "enum": ["person", "application"],
        "default": "person",
        "description": "person: the signed-in person's own account; application: the application's "
        "shared account (every write waits for the person's confirmation).",
    }
}
for _id in ("google_workspace", "github", "linear", "notion", "slack"):
    OWN_SETTINGS[_id] = _ACTS_AS


DESCRIPTIONS: dict[str, str] = {
    "code_mode": "One run_code tool: a Python program (Monty, in process) calls the read-only tools as functions.",
    "compaction": "Older messages of a conversation stored by Evokai folded into a summary kept in its database.",
    "subagents": "Calls other agents of the application for one task (delegate_task).",
    "clamp_oversized_messages": "Zero-cost head/tail truncation of any single oversized message part.",
    "clear_tool_results": "Zero-cost in-place clearing of old tool results.",
    "compact_on_request": "Give the model a compact_context tool to ask for a compaction when its task reaches a break.",
    "deduplicate_file_reads": "Zero-cost in-place clearing of superseded file reads.",
    "fallback_compaction": "Try compaction strategies in order, falling back when one raises.",
    "relevance_compaction": "Compact by judging each older step of the history: keep it verbatim, reduce it, or drop it.",
    "report_context_usage": "Report context usage to the application before each model request.",
    "sliding_window_compaction": "Zero-cost sliding-window trimmer.",
    "summarizing_compaction": "Compact the conversation by summarizing older messages with a model call.",
    "tiered_compaction": "Escalation orchestrator over a sequence of compaction strategies.",
    "warn_near_limits": "Warns the model when the run approaches configured limits.",
    "tool_output_limits": "Reduce oversized tool returns when they are produced, persisting the reduction.",
    "system_reminders": "Inject periodic or conditional reminders to counter instruction fade in long sessions.",
    "warn_on_cache_busts": "Warn when a conversation's prompt cache hit collapses between requests.",
    "approval_reviewer": "Ask a second model about each call waiting for approval, and approve the low-risk ones.",
    "permissions": "Ask before tool calls that write files or run shell commands, according to a permission mode.",
    "tool_call_judge": "Ask a second model whether a tool call may run, and block it before its body does.",
    "trajectory_judge": "Review a live run with a second model on a cadence, and steer it mid-run.",
    "input_guardrail": "Validate the user prompt before it reaches the model.",
    "output_guardrail": "Validate the agent output as it is produced.",
    "tool_guardrail": "Validate tool arguments before execution and tool results after it.",
    "prompt_injection_defender": "Classify tool results for indirect prompt injection and withhold the risky ones.",
    "artifact_scanner": "Scan agent-written artifacts, such as memory files and skills, for attack patterns.",
    "detect_loops": "Detect when an agent repeats the same actions without making progress, and interrupt it.",
    "tool_budgets": "Cap tool calls per tool and per run, and refuse exact repeats, by answering the model instead of raising.",
    "spend_limits": "Accumulate spend per window and refuse a request once a window is exhausted.",
    "advisor": "Let an agent consult another model through a provider-native tool or local fallback.",
    "planning": "Structured task planning, with the current plan kept in front of the model.",
    "refine_output": "Send the agent back to review and revise its output before the run ends.",
    "repair_tool_arguments": "Repair malformed JSON tool arguments before they are checked.",
    "background_tools": "Run selected tools in the background while the agent continues.",
    "dynamic_workflow": "Capability that lets the model orchestrate named sub-agents from a Python script.",
    "ask_user": "Let the model ask the user multiple-choice questions mid-run.",
    "memory": "Persistent agent memory across sessions.",
    "conversation_search": "Search persisted conversation history with a dependency-free BM25 tool.",
    "step_persistence": "Append-only step log + continuable snapshots + tool-effect ledger.",
    "peers": "Let an agent list and message other sessions over a host-provided network.",
    "capability_creation": "Create Pydantic AI capabilities during one run for activation on the next.",
    "managed_prompt": "Back an agent's instructions with a Logfire-managed prompt.",
    "exa_search": "Web research for agents, backed by the Exa search API.",
    "exa_agent": "Delegation of deep research tasks to the Exa Agent API.",
    "keenable_search": "Web research for agents, backed by the Keenable search API.",
    "you_search": "Web research for agents, backed by the You.com search API.",
    "you_research": "Cited answers and multi-step research, backed by the You.com APIs.",
    "researcher": "Web research with search, page fetching, a research delegate, and tool-output limits.",
    "smart_search": "Find code by the behavior a plain-English query describes.",
    "pydantic_ai_docs": "Locate and return Pydantic AI documentation on demand.",
    "day_ai": "Let an agent search and update the Day AI CRM.",
    "grain": "Let an agent search and read Grain meetings, transcripts, and notes, and create clips and tag meetings.",
    "ordinal": "Let an agent draft, schedule, and analyze social posts in Ordinal.",
    "posthog": "Let an agent query PostHog product analytics and manage its resources.",
    "pylon": "Let an agent manage support issues, look up and update accounts, and look up contacts in Pylon.",
    "stackone": "Actions on the user's SaaS account (HRIS, ATS, CRM, and more) via StackOne.",
    "github": "Use GitHub's hosted tools with the permissions of the connected credential.",
    "linear": "Give an agent the tools of Linear's hosted MCP server, with the permissions of the connected user.",
    "notion": "Give the agent the tools of Notion's hosted MCP server, with the permissions of the connected user.",
    "slack": "Give an agent Slack's hosted tools, acting as the connected user.",
    "google_workspace": "Give an agent the tools of Google's hosted Workspace MCP servers for the selected products.",
    "logfire_mcp": "Query Logfire telemetry and manage observability resources through its hosted tools.",
    "pixeltable": "Read-only tools over existing Pixeltable tables.",
    "browser_use": "A browser the agent drives.",
    "localstack": "Access to an emulated AWS environment via LocalStack.",
    "playwright": "A real, stateful Chromium browser the agent drives.",
    "shell": "Shell command execution for agents.",
    "file_system": "File system access to the run's workspace, scoped to a root directory.",
    "coder": "Autonomous coding with six tools, delegation, and context management, in the run's workspace.",
    "repo_context": "Discover and load a repo's accumulated coding-assistant context engineering.",
    "skills_dirs": "Offer Agent Skill instructions from the run's workspace as deferred capabilities.",
    "macroscope": "Runs the macroscope CLI code review and hands the findings to the agent.",
    "ipython_code_mode": "Capability that gives the model a run_code tool backed by a persistent IPython kernel.",
    "docker_container": "Supply a local Docker container as the run's workspace, created on first use.",
    "docker_sandbox": "Deprecated name for DockerContainer.",
    "bubblewrap_workspace": "A [Workspace][pydantic_ai.workspaces.Workspace] that runs commands in a bubblewrap (bwrap) sandbox.",
    "boxlite_sandbox": "Supply a BoxLite micro-VM on this machine as the run's workspace.",
    "smol_sandbox": "Supply a smolvm microVM on this machine as the run's workspace.",
    "ssh_workspace": "Gives runs a workspace on a remote host over SSH, using your ssh client and its configuration.",
    "e2b_sandbox": "Supply an isolated E2B sandbox as the run's workspace.",
    "modal_sandbox": "Supply a Modal sandbox as the run's workspace.",
    "daytona_sandbox": "Supply an isolated Daytona sandbox as the run's workspace.",
    "kubernetes_sandbox": "A sandbox pod per run in a Kubernetes cluster.",
    "render_sandbox": "Supply an isolated Render sandbox as the run's workspace.",
    "sprites_sandbox": "Run the agent's workspace in a persistent Fly.io Sprite.",
    "opensandbox": "Supply an OpenSandbox sandbox as the run's workspace, created on first use.",
    "boxd_sandbox": "Supply an isolated boxd machine as the run's workspace.",
    "absurd_durability": "Capability that makes an agent durable by checkpointing its I/O into Absurd steps.",
    "azure_durable_task": "Run agent operations as activities in user-written Azure Durable Task orchestrations.",
    "pgtask_durability": "Capability that makes an agent durable by checkpointing its I/O into pgtask steps.",
    "aws_lambda_durability": "Capability that checkpoints an agent's I/O into AWS Lambda durable steps.",
    "render_workflows": "Route supported agent operations through an explicit Workflows app.",
}
"""What each capability does, in one line (static: the console lists the catalog without importing any capability)."""
