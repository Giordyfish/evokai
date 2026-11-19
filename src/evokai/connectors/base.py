"""Ready connectors: MCP servers that ship with Evokai, enabled by configuration.

An application registers a connector like any MCP server (``mcp_servers`` with ``connector:`` and
``config:``); its tools reach the agents through the same toolbox, so the application's grants
apply to them item by item (the server, each tool, and tools that change data only with
confirmations). Every connector:

- is read-only unless ``writes: true``; its write tools are annotated destructive, so a turn stops
  for the user's confirmation before any of them runs, and server runs never get them;
- reads its secrets from environment variables named in the configuration (keys ending in
  ``_env``); a configuration that carries a secret in clear is refused;
- limits what it reaches (tables, operations and hosts, folders) and how much it returns.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from evokai.core.errors import BadRequest, ConfigError
from evokai.mcp.client import McpTool, ToolResult

SECRET_KEY = re.compile(
    r"(secret|password|passwd|token|api_?key|private_?key|credential|dsn)$", re.I
)
"""A configuration key that names a secret: only its ``_env`` form is accepted."""
MAX_RESULT_CHARS = 12_000


def plaintext_secrets(config: Any, path: str = "") -> list[str]:
    """Keys of a configuration that hold a secret in clear (anywhere in it).

    Returns:
        Their dotted paths.
    """
    found: list[str] = []
    if isinstance(config, dict):
        for k, v in config.items():
            here = f"{path}.{k}" if path else str(k)
            if SECRET_KEY.search(str(k)) and not str(k).endswith("_env") and v not in (None, ""):
                found.append(here)
            found += plaintext_secrets(v, here)
    elif isinstance(config, list):
        for i, v in enumerate(config):
            found += plaintext_secrets(v, f"{path}[{i}]")
    return found


def secret(name: str | None, what: str) -> str:
    """A secret from the environment variable a configuration names.

    Returns:
        The value.

    Raises:
        ConfigError: No variable named, or it is empty.
    """
    value = os.environ.get(name or "", "") if name else ""
    if not value:
        raise ConfigError(f"{what}: set the environment variable {name or '(none named)'}")
    return value


class ConnectorConfig(BaseModel):
    """What every connector accepts."""

    model_config = ConfigDict(extra="forbid")

    writes: bool = False
    """Offer the tools that change data (each one asks the user to confirm first)."""
    max_result_chars: int = MAX_RESULT_CHARS

    @model_validator(mode="before")
    @classmethod
    def _no_plaintext_secrets(cls, data: Any) -> Any:
        if bad := plaintext_secrets(data):
            raise ValueError(
                "secrets come from the environment: use "
                + ", ".join(f"{k}_env" for k in bad)
                + " with the name of an environment variable"
            )
        return data


def tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    writes: bool = False,
) -> McpTool:
    """A tool definition with its annotations.

    Returns:
        The tool (read-only, or destructive when it writes).
    """
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return McpTool(
        name=name,
        description=description,
        input_schema=schema,
        read_only=not writes,
        destructive=writes,
    )


def as_text(value: Any, limit: int) -> str:
    """A tool result as text for the model, clipped.

    Returns:
        JSON (or the text itself), at most ``limit`` characters.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 40] + "\n[... cut: result too long]"


class Connector(ABC):
    """A connector: tools listed and called in process, like an MCP server's."""

    kind: ClassVar[str]
    title: ClassVar[str]
    Config: ClassVar[type[ConnectorConfig]]

    def __init__(self, name: str, config: ConnectorConfig) -> None:
        """Bind the connector to its configuration.

        Args:
            name: The MCP server name it is registered under.
            config: Its validated configuration.
        """
        self.name = name
        self.config = config

    @abstractmethod
    def tools(self) -> list[McpTool]:
        """The tools this configuration offers.

        Returns:
            Tools (write tools only with ``writes: true``).
        """

    @abstractmethod
    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool.

        Returns:
            A JSON-able result (or text).
        """

    async def close(self) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] (optional hook)
        """Release connections."""

    # The McpClient interface, so a toolbox routes calls here like to any server.

    async def list_tools(self) -> list[McpTool]:
        """The tools, as an MCP server would list them.

        Returns:
            Tools, with the server's name.
        """
        return [replace(t, server=self.name) for t in self.tools()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Call a tool; a refusal or failure becomes a result the model can read.

        Returns:
            The result.
        """
        known = {t.name: t for t in self.tools()}
        if name not in known:
            return ToolResult(ok=False, text=f"Unknown tool: {name}")
        try:
            out = await self.run(name, arguments or {})
        except (BadRequest, ConfigError, ValueError, PermissionError, LookupError) as e:
            return ToolResult(ok=False, text=f"Error: {getattr(e, 'message', None) or e}")
        except Exception as e:
            return ToolResult(
                ok=False, text=f"Error: the {self.kind} connector failed ({type(e).__name__})."
            )
        return ToolResult(ok=True, text=as_text(out, self.config.max_result_chars))
