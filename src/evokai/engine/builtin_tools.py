"""Provider built-in tools: pydantic-ai's native tools, run by the model's provider.

An agent lists them in ``builtin_tools`` (``web_search``, ``web_fetch``, ``code_execution``,
``image_generation``, ``file_search``, ``mcp_server``, ``memory``), the application is granted each
one (grant kind ``builtin``, off by default) and they reach the model as ``NativeTool``
capabilities. The provider runs them, so Evokai's own checks apply around them:

- tools that keep data at the provider (``file_search`` with the provider's vector stores,
  ``memory``) are refused unless the application's data protection says ``provider_tool_storage``;
- the egress allow-list of the security policy (``tools.egress``) becomes the allowed domains of
  web search and web fetch, and an MCP server's address must be on it;
- what they return reaches the person only through the model's answer, which the output guard
  filters as always; the turn shows each call as a ``tool`` event and every web source as a
  ``citation``.

A model whose provider does not offer a listed tool fails the call (pydantic-ai refuses it); a
definition's settings are checked when the agent is saved.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from pydantic_ai.capabilities import NativeTool

from evokai.guard.content import host_allowed

log = logging.getLogger("evokai.builtin_tools")

KINDS = (
    "web_search",
    "web_fetch",
    "code_execution",
    "image_generation",
    "file_search",
    "mcp_server",
    "memory",
)
STORING = frozenset({"file_search", "memory"})
"""Tools that keep data at the provider."""
SECRET_FIELDS = frozenset({"authorization_token", "headers"})
"""Settings that would put a secret in an agent definition (refused)."""


def storage_allowed(profile: dict[str, Any] | None) -> bool:
    """Whether the application's data protection lets built-in tools keep data at the provider.

    Returns:
        True only when ``provider_tool_storage`` is set.
    """
    dp = (profile or {}).get("data_protection") or {}
    return bool(dp.get("provider_tool_storage"))


def build(kind: str, config: dict[str, Any], egress_allow: list[str] | None = None) -> NativeTool:
    """The capability of one built-in tool, with the egress allow-list applied.

    Returns:
        The capability.

    Raises:
        ValueError: Settings pydantic-ai refuses, a secret, or an MCP server outside the egress
            allow-list.
    """
    cfg = dict(config or {})
    if secrets := SECRET_FIELDS & set(cfg):
        raise ValueError(f"{kind}: {', '.join(sorted(secrets))} would put a secret in the agent")
    if egress_allow is not None and kind in ("web_search", "web_fetch"):
        # Within the egress allow-list: the agent may narrow it, never widen it.
        asked = cfg.get("allowed_domains")
        domains = (
            [d for d in asked if host_allowed(str(d), egress_allow)]
            if isinstance(asked, list)
            else list(egress_allow)
        )
        if not domains:
            raise ValueError(f"{kind}: no domain is inside the egress allow-list")
        cfg["allowed_domains"] = domains
    if egress_allow is not None and kind == "mcp_server":
        host = urlparse(str(cfg.get("url") or "")).hostname or ""
        if not host_allowed(host, egress_allow):
            where = host or "the address"
            raise ValueError(f"mcp_server: {where} is outside the egress allow-list")
    try:
        return NativeTool.from_spec(kind=kind, **cfg)
    except Exception as e:
        raise ValueError(f"{kind}: {str(e).splitlines()[0][:200]}") from e


def capabilities(
    defs: Iterable[Any],
    grants: Any,
    profile: dict[str, Any] | None,
    egress_allow: list[str] | None,
) -> list[NativeTool]:
    """The built-in tools a turn or run offers: granted, allowed by the data protection, valid.

    Returns:
        Their capabilities (one that cannot be built is left out with a warning).
    """
    out: list[NativeTool] = []
    for d in defs:
        if not grants.builtin(d.kind):
            continue
        if d.kind in STORING and not storage_allowed(profile):
            log.warning("builtin_tool_storage_refused", extra={"tool": d.kind})
            continue
        try:
            out.append(build(d.kind, d.config, egress_allow))
        except ValueError as e:
            log.warning("builtin_tool_unavailable", extra={"tool": d.kind, "error": str(e)})
    return out


def sources(content: Any) -> list[dict[str, str]]:
    """The web pages a built-in tool's result names (each provider shapes it its own way).

    Returns:
        ``url`` and ``title`` of each page, once, in order.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            url = x.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                found.append({"url": url, "title": str(x.get("title") or url)[:300]})
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(content)
    return found[:20]
