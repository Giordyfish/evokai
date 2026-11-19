"""The rule for capabilities that work on files and commands: never on the Evokai server itself.

A capability of the catalog marked ``host = workspace`` (a shell, files, code search) is built
only with a remote sandbox in its settings (``evokai.capabilities``), and every run that has one
gets ``NoLocalWorkspace``: before the run starts it refuses a workspace whose backend is the local
machine (pydantic-ai's ``LocalWorkspaceBackend`` or host workspace), whatever supplied it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability

from evokai.core.errors import AppError

LOCAL_BACKENDS = ("LocalWorkspaceBackend", "HostWorkspaceBackend", "WindowsWorkspaceBackend")
"""Backends that run commands and touch files on the machine of the process."""


class LocalWorkspaceRefused(AppError):
    """A capability tried to work on the Evokai server's own machine."""

    status, code = 500, "local_workspace"


def is_local(workspace: Any) -> bool:
    """Whether a run's workspace is the local machine.

    Returns:
        True for a local or host backend.
    """
    if workspace is None or not getattr(workspace, "attached", False):
        return False
    backend = getattr(workspace, "backend", None)
    return type(backend).__name__ in LOCAL_BACKENDS


@dataclass
class NoLocalWorkspace(AbstractCapability[Any]):
    """Refuses a run whose workspace is the Evokai server itself."""

    async def before_run(self, ctx: RunContext[Any]) -> None:
        """Check the run's workspace before anything runs.

        Raises:
            LocalWorkspaceRefused: The workspace is the local machine.
        """
        if is_local(ctx.workspace):
            raise LocalWorkspaceRefused(
                "A capability asked for a workspace on the Evokai server: only remote sandboxes "
                "are allowed."
            )
