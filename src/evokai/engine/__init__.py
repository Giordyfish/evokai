"""The agent engine: Evokai's turns and runs on pydantic-ai's ``Agent``.

pydantic-ai drives the loop (model request, tool calls, deferred approvals, retries); Evokai plugs
in what it owns through public extension points:

- ``tools``: every tool of a turn (MCP as the user, client tools, knowledge, code, skills, handoff,
  memory) as one toolset, wrapped by the guard (tool policy, arguments, egress, loop, rate) and by
  the confirmation rule (a mutating tool raises ``ApprovalRequired``);
- ``capability``: the per-request work (the history the model sees, windowed and guarded from the
  provider-neutral store; prompt caching per model; the fallback rule; caps before each call; usage,
  traces and model health per attempt);
- ``messages``: the provider-neutral history converted back from pydantic-ai messages.

See ``docs/pydantic-ai-agent.md`` for how every feature maps and what stays Evokai's own code.
"""
