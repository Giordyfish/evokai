"""Provider built-in tools: pydantic-ai's native tools, run by the model's provider.

An agent lists them in ``builtin_tools`` (``web_search``, ``web_fetch``, ``code_execution``,
``image_generation``, ``file_search``, ``mcp_server``, ``memory``), the application is granted each
one (grant kind ``builtin``, off by default) and they reach the model as ``NativeTool``
capabilities. The provider runs them, so Evokai's own checks apply around them:

- tools that keep data at the provider (``file_search`` with the provider's vector stores,
  ``memory``) are refused unless the application's data protection says ``provider_tool_storage``;
- the tool policy applies to each under the names ``builtin:<kind>`` and ``<kind>``: a denied one,
  or one outside an allow-list, is not offered. The provider runs them, so nothing can wait for the
  person's confirmation before a call: a tool the policy confirms (``tools.confirm``) or an ask
  rule may ask for is refused, never run without the confirmation;
- an MCP server (``mcp_server``) must list the tools it may call (``allowed_tools``): each runs
  without a confirmation, and a listed tool the policy denies or confirms, or an ask rule may ask
  for, is taken off the list;
- the egress allow-list of the security policy (``tools.egress``) becomes the allowed domains of
  web search and web fetch, and an MCP server's address must be on it (whenever an allow-list is
  set and the control is not off: the provider's calls to it cannot be checked one by one);
- an answer that called a tool that can change data (``CHANGING``) cannot be regenerated;
- what they return reaches the person only through the model's answer, which the output guard
  filters as always; the turn shows each call as a ``tool`` event and every web source as a
  ``citation``.

A model whose provider does not offer a listed tool fails the call (pydantic-ai refuses it); a
definition's settings are checked when the agent is saved.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
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
CHANGING = frozenset({"mcp_server", "memory", "code_execution"})
"""Tools whose calls can change data: an MCP server's tools, the provider's memory, and code run in
the provider's sandbox (which can keep files between requests). An answer that called one is not
regenerated (``evokai.sessions.rewrite``)."""
REACHING = frozenset({"web_search", "web_fetch", "mcp_server"})
"""Tools that reach addresses on the network (``tools.egress``)."""
POLICY_PREFIX = "builtin:"
Gate = Callable[[tuple[str, ...]], str | None]
"""Why the security policy refuses a tool the provider runs, by its names, or None
(``TurnGuard.provider_tool_refusal``)."""


def kind_of(tool_name: str) -> str:
    """The kind of a built-in tool from the name its call carries (``mcp_server:crm``).

    Returns:
        The kind.
    """
    return tool_name.split(":", 1)[0]


def policy_names(kind: str) -> tuple[str, str]:
    """The names the tool policy and ask rules know a built-in tool by.

    Returns:
        ``builtin:<kind>`` and ``<kind>``.
    """
    return f"{POLICY_PREFIX}{kind}", kind


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
    listed = cfg.get("allowed_tools")
    if kind == "mcp_server" and not (
        isinstance(listed, list) and listed and all(isinstance(t, str) and t for t in listed)
    ):
        raise ValueError(
            "mcp_server: allowed_tools must list the tools it may call (each runs without a "
            "confirmation)"
        )
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
    *,
    gate: Gate | None = None,
    mcp_allow: list[str] | None = None,
) -> list[NativeTool]:
    """The built-in tools a turn or run offers: granted, allowed by data protection and policy.

    Args:
        defs: The agent's built-in tools.
        grants: The application's grants.
        profile: The application's data protection.
        egress_allow: The allow-list web search and web fetch follow (``tools.egress`` enforced).
        gate: The tool policy and ask rules (None: no policy).
        mcp_allow: The allow-list an MCP server's address must be on (any allow-list while
            ``tools.egress`` is not off).

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
        if gate is not None and (why := gate(policy_names(d.kind))):
            log.warning("builtin_tool_policy_refused", extra={"tool": d.kind, "reason": why})
            continue
        config = dict(d.config or {})
        if d.kind == "mcp_server" and gate is not None:
            listed = config.get("allowed_tools")
            if isinstance(listed, list):
                config["allowed_tools"] = [t for t in listed if gate((str(t),)) is None]
                if not config["allowed_tools"]:
                    log.warning("builtin_tool_policy_refused", extra={"tool": d.kind})
                    continue
        try:
            out.append(build(d.kind, config, mcp_allow if d.kind == "mcp_server" else egress_allow))
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
