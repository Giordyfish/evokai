"""The pydantic-ai ``Agent`` of one turn or run.

An agent is built per turn (it is a plain object): the model chain's first model, Evokai's guarded
toolset, Evokai's capability first and then the capabilities the deployment, the application's
grants and the agent turn on. The volatile part of the system prompt (voice rule, memories, open
page) travels as dynamic instructions after the cache point; the stable part is the system prompt
at the head of the history (``evokai.sessions.history.to_model_messages``).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.output import OutputSpec
from pydantic_ai.tools import DeferredToolRequests
from pydantic_ai.toolsets import AbstractToolset

from evokai.engine.capability import EvokaiCapability
from evokai.engine.state import RunState

# pydantic-ai prints a banner on a terminal when no observability is configured: Evokai has its own
# logs and traces, and a service never prints to its terminal.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def build_agent(
    rs: RunState,
    *,
    toolset: AbstractToolset[RunState] | None,
    instructions: str | None = None,
    system_prompt: str | None = None,
    capabilities: Sequence[Any] = (),
    output_type: OutputSpec[Any] | None = None,
    output_retries: int = 1,
) -> Agent[RunState, Any]:
    """An Agent for one turn or run.

    Args:
        rs: The run's state (its models must be set).
        toolset: The guarded tools (None: no tools).
        instructions: The volatile part of the system prompt, when it travels after the cache point.
        system_prompt: The system prompt of a run that starts with no history (a subagent).
        capabilities: Capabilities to add after Evokai's own.
        output_type: The output (text, or text or deferred approvals by default).
        output_retries: Retries of an output that does not validate.

    Returns:
        The agent.
    """
    volatile = instructions

    def dynamic() -> str:
        return volatile or ""

    return Agent(
        rs.models[0][1],
        output_type=output_type if output_type is not None else [str, DeferredToolRequests],
        instructions=[dynamic] if volatile else None,
        system_prompt=system_prompt or (),
        deps_type=RunState,
        toolsets=[toolset] if toolset is not None else None,
        capabilities=[EvokaiCapability(), *capabilities],
        retries={"output": output_retries, "tools": 1},
        name="evokai",
    )
