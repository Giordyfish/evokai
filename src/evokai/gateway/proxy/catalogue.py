"""The hosts the proxy serves, per provider, and the PAC file that sends PCs to it.

Three sets:

- **intercepted**: TLS is opened and the adapters read the traffic. Only the hosts of providers
  enabled on some gateway and served by at least one adapter in this build: a provider without
  adapters is refused rather than tunnelled unread (fail closed).
- **tunnelled**: static assets and updates of the same apps, passed without opening TLS when a PC
  sends them anyway (Claude Code with ``HTTPS_PROXY`` sends everything). The PAC never lists them.
- the **check host** ``evokai.check``: the proxy answers it itself (is this PC going through the
  proxy, and as whom).

Any other host is refused with 403: the proxy is never an open proxy. The CA's Name Constraints
(``evokai.gateway.proxy.ca``) cover every provider's domains here plus the check host, so turning a
provider on later needs no new CA.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

CHECK_HOST = "evokai.check"
"""Not a real domain: the PAC lists it and the proxy answers it."""

ANTHROPIC = "anthropic"
OPENAI = "openai"
GITHUB = "github"


@dataclass(frozen=True)
class Host:
    """A host of the catalogue: an exact name, or every subdomain of a domain (``*.name``)."""

    name: str
    wildcard: bool = False

    def matches(self, host: str) -> bool:
        """Whether a host name is this entry.

        Returns:
            True when it is.
        """
        host = host.lower().rstrip(".")
        if self.wildcard:
            return host.endswith("." + self.name)
        return host == self.name


INTERCEPTED: dict[str, tuple[Host, ...]] = {
    ANTHROPIC: (
        Host("api.anthropic.com"),
        Host("claude.ai"),
        Host("claudeusercontent.com", wildcard=True),
        Host("claude.com"),
        Host("platform.claude.com"),
    ),
    OPENAI: (Host("chatgpt.com"), Host("api.openai.com")),
    GITHUB: (
        Host("api.githubcopilot.com"),
        Host("api.individual.githubcopilot.com"),
        Host("api.business.githubcopilot.com"),
        Host("api.enterprise.githubcopilot.com"),
        Host("copilot-proxy.githubusercontent.com"),
        Host("api.github.com"),
    ),
}
"""Per provider, the hosts whose traffic is read (best effort for GitHub Copilot: not measured)."""

IDENTITY_ONLY = frozenset({"platform.claude.com", "api.github.com"})
"""Hosts opened only to read who signed in: Claude Code's OAuth token answer
(``POST platform.claude.com/v1/oauth/token`` names the account and its organisation), and GitHub's
user and Copilot token answers (``GET api.github.com/user``, ``/copilot_internal/user``,
``/copilot_internal/v2/token``). No adapter reads or masks anything else there, and no token is
ever logged or kept."""

TUNNELLED: tuple[Host, ...] = (
    Host("downloads.claude.ai"),
    Host("s-cdn.anthropic.com"),
    Host("assets-proxy.anthropic.com"),
)
"""Static assets and updates: passed without opening TLS when a PC sends them."""

NAME_CONSTRAINTS: tuple[str, ...] = (
    "api.anthropic.com",
    "claude.ai",
    "claudeusercontent.com",
    "claude.com",
    "chatgpt.com",
    "api.openai.com",
    "githubcopilot.com",
    "copilot-proxy.githubusercontent.com",
    "api.github.com",
    CHECK_HOST,
)
"""The DNS subtrees the intermediate CA may sign for (each covers its subdomains too)."""

GATEWAY_PROVIDER = {ANTHROPIC: ANTHROPIC, OPENAI: OPENAI, GITHUB: GITHUB}
"""The gateway provider whose switch turns a catalogue provider on (GitHub Copilot: ``github``,
served by the proxy only, it has no base URL door)."""


def intercepted(providers: Iterable[str]) -> tuple[Host, ...]:
    """The hosts read for these catalogue providers.

    Returns:
        The hosts.
    """
    return tuple(h for p in providers for h in INTERCEPTED.get(p, ()))


def served(enabled: Iterable[str], with_adapters: Iterable[str]) -> list[str]:
    """The catalogue providers the proxy serves.

    Args:
        enabled: Gateway providers enabled on at least one active gateway.
        with_adapters: Catalogue providers at least one adapter of this build handles.

    Returns:
        Catalogue providers, in catalogue order.
    """
    on = set(enabled)
    have = set(with_adapters)
    return [p for p in INTERCEPTED if p in have and GATEWAY_PROVIDER[p] in on]


def provider_of(host: str) -> str | None:
    """The catalogue provider a host belongs to.

    Returns:
        The provider, or None.
    """
    return next(
        (p for p, hosts in INTERCEPTED.items() if any(h.matches(host) for h in hosts)), None
    )


def is_tunnelled(host: str) -> bool:
    """Whether a host is passed without opening TLS.

    Returns:
        True when it is.
    """
    return any(h.matches(host) for h in TUNNELLED)


def pac(hosts: Iterable[Host], address: str, *, fallback: str = "block") -> str:
    """The PAC file: the catalogue's hosts and the check host go to the proxy, the rest direct.

    Args:
        hosts: The intercepted hosts.
        address: The proxy's ``host:port`` as PCs reach it.
        fallback: ``block`` (only the proxy) or ``direct`` (direct when it cannot be reached).

    Returns:
        The JavaScript of the PAC file.
    """
    target = f"PROXY {address}" + ("; DIRECT" if fallback == "direct" else "")
    exact = sorted({h.name for h in hosts if not h.wildcard} | {CHECK_HOST})
    domains = sorted({h.name for h in hosts if h.wildcard})
    lines = [
        "// Evokai gateway proxy: AI hosts go through the company proxy, everything else direct.",
        "function FindProxyForURL(url, host) {",
        "  host = host.toLowerCase();",
    ]
    lines += [f'  if (host == "{name}") return "{target}";' for name in exact]
    lines += [f'  if (dnsDomainIs(host, ".{name}")) return "{target}";' for name in domains]
    lines += ['  return "DIRECT";', "}", ""]
    return "\n".join(lines)
