"""The capability catalog: Evokai's own capabilities and every pydantic-ai-harness one.

A capability changes what an agent run can do (Code Mode, compaction, subagents, a judge, a web
search, a sandbox). Each one is off until three switches say yes, like ``code_run``:

1. the deployment offers it (``EVOKAI_CAPABILITIES``, a JSON list of ids or ``["*"]``);
2. the application is granted it (grant kind ``capability``, item = the id; off by default);
3. the agent turns it on (``capabilities: {id: {enabled: true, config: {...}}}``).

On a turn or run the engine builds the enabled ones (``build``) and adds them to the agent after
Evokai's own rules, which always hold: the guard, the grants, caps and usage, traces, MCP as the
user, no provider-side state (every attempt's settings pass ``evokai.llm.policy.enforce_settings``),
and confirmation before a change (a capability never approves a call by itself unless an admin
turned on ``approval_reviewer`` for that agent, and then only within what the agent is granted).

Host access: a capability that would give an agent a shell, files, containers, a kernel or ssh on
the Evokai service's own machine is never built (``host = local``); one that works on the run's
workspace (``host = workspace``) is built only with a remote sandbox in its settings (``sandbox``:
the id of an enabled ``host = remote`` capability), and every run that has one checks that its
workspace is not the local machine (``NoLocalWorkspace``).
"""

from evokai.capabilities.catalog import Entry
from evokai.capabilities.registry import (
    SANDBOX_KEY,
    CapabilityError,
    all_ids,
    build,
    check_use,
    construct,
    get,
    installed,
    load,
    missing_credentials,
    not_installed_reason,
    offered,
    schema,
)

__all__ = [
    "SANDBOX_KEY",
    "CapabilityError",
    "Entry",
    "all_ids",
    "build",
    "check_use",
    "construct",
    "get",
    "installed",
    "load",
    "missing_credentials",
    "not_installed_reason",
    "offered",
    "schema",
]
