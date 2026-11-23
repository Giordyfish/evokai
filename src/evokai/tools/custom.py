"""Custom client tools: declared by the agent, the session token or a server run.

The client application runs them: Evokai sends a ``client_tool`` event and, for a tool that waits,
gets the result back from ``POST /v1/session/tool-results``. The model sees each one as
``client_<name>``, a namespace of their own beside MCP tools and Evokai's tools.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evokai.agents.definitions import ClientToolDef
from evokai.core.errors import BadRequest

PREFIX = "client_"
"""The model-facing name of a custom client tool is its name with this prefix."""
MAX_TOOLS = 32
"""Custom client tools of one session or run (the agent's and the session's together)."""
MAX_RESULT_CHARS = 32_768
"""A client's result, as JSON text: larger is refused (413)."""
OK_TEXT = "ok: the client application received the call."
"""What the model reads back from a tool that does not wait."""


def check_tools(tools: Sequence[ClientToolDef]) -> None:
    """The limits of a set of custom client tools.

    Raises:
        BadRequest: More than 32 tools, or one name twice (``client_tools``).
    """
    if len(tools) > MAX_TOOLS:
        raise BadRequest(f"Give at most {MAX_TOOLS} client tools.", code="client_tools")
    names = [t.name for t in tools]
    if len(names) != len(set(names)):
        raise BadRequest("A client tool is declared twice.", code="client_tools")


def model_name(name: str) -> str:
    """The name the model sees.

    Returns:
        ``client_<name>``.
    """
    return f"{PREFIX}{name}"


def merged(static: Sequence[ClientToolDef], extra: Sequence[Any] | None) -> list[ClientToolDef]:
    """The agent's tools, then the session's (or run's) under names the agent does not use.

    Returns:
        The tools, at most ``MAX_TOOLS``.
    """
    out = list(static)
    names = {t.name for t in out}
    for raw in extra or []:
        t = raw if isinstance(raw, ClientToolDef) else ClientToolDef.model_validate(raw)
        if t.name not in names:
            out.append(t)
            names.add(t.name)
    return out[:MAX_TOOLS]
