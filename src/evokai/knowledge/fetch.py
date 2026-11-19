"""Documents fetched by address, safely (no server-side request forgery).

- Only ``https`` (and ``http``) addresses on an allowed domain (``EVOKAI_KNOWLEDGE_FETCH_ALLOW``;
  a domain allows its subdomains) are fetched; nothing is fetched when the list is empty.
- The host name is resolved first and every address it resolves to must be a public one: loopback,
  private, link-local (the cloud metadata service), shared, multicast and reserved addresses are
  refused. The request then goes to the checked address itself (the TLS name is still the host's),
  so a second, different DNS answer cannot redirect it.
- Redirects are followed by hand, at most three, each one checked the same way.
- The body is read up to the size cap and no further; credentials and cookies are never sent.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from evokai.core.errors import BadRequest

MAX_REDIRECTS = 3
TIMEOUT = httpx.Timeout(20, connect=5)
USER_AGENT = "Evokai-Knowledge/1.0"

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchRefused(BadRequest):
    """An address that may not be fetched, or a response that cannot be used."""

    def __init__(self, message: str) -> None:
        """Create the error.

        Args:
            message: Why, for the person who gave the address.
        """
        super().__init__(message, code="fetch")


@dataclass
class Fetched:
    """A fetched document."""

    url: str
    """The final address (after redirects)."""
    data: bytes
    mime: str
    name: str


async def resolve(host: str, port: int) -> list[str]:
    """The addresses a host name resolves to.

    Returns:
        IP addresses.
    """
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(i[4][0]) for i in infos})


def public(ip: str) -> bool:
    """Whether an address is a public one.

    Returns:
        False for loopback, private, link-local, shared, multicast, reserved and unspecified
        addresses (and IPv4 addresses mapped into IPv6 are judged as IPv4).
    """
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def domain_allowed(host: str, allow: list[str]) -> bool:
    """Whether a host is on an allowed domain (or a subdomain of one).

    Returns:
        True when allowed.
    """
    host = host.lower().rstrip(".")
    for d in allow:
        d = d.lower().strip().lstrip("*.").rstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False


class Fetcher:
    """Fetches documents by address within the deployment's allow-list."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        allow: list[str],
        max_bytes: int,
        resolver: Resolver = resolve,
    ) -> None:
        """Configure.

        Args:
            http: The process's HTTP client.
            allow: Allowed domains.
            max_bytes: The size cap.
            resolver: DNS resolution (tests replace it).
        """
        self.http = http
        self.allow = allow
        self.max_bytes = max_bytes
        self.resolver = resolver

    async def check(self, url: str) -> tuple[httpx.URL, str]:
        """Check an address and pick the public address to connect to.

        Returns:
            The address to request (the host replaced by a checked IP) and the host name.

        Raises:
            FetchRefused: Not allowed, or not a public address.
        """
        if not self.allow:
            raise FetchRefused("Fetching documents by address is off in this deployment.")
        parts = urlsplit(url.strip())
        if parts.scheme not in {"https", "http"} or not parts.hostname:
            raise FetchRefused("Give an http or https address.")
        if parts.username or parts.password:
            raise FetchRefused("Addresses with credentials are not fetched.")
        host = parts.hostname
        try:
            ipaddress.ip_address(host)
            raise FetchRefused("Give a domain name, not an IP address.")
        except ValueError:
            pass
        if not domain_allowed(host, self.allow):
            raise FetchRefused(f"The domain {host} is not allowed (EVOKAI_KNOWLEDGE_FETCH_ALLOW).")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            ips = await self.resolver(host, port)
        except OSError as e:
            raise FetchRefused(f"The host {host} could not be resolved.") from e
        if not ips or not all(public(ip) for ip in ips):
            raise FetchRefused(f"The host {host} resolves to an address that is not public.")
        ip = ips[0]
        netloc = f"[{ip}]" if ":" in ip else ip
        if parts.port:
            netloc += f":{parts.port}"
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
        return httpx.URL(pinned), host

    async def fetch(self, url: str) -> Fetched:
        """Fetch a document.

        Returns:
            The document.

        Raises:
            FetchRefused: Not allowed, too large, too many redirects or an error status.
        """
        current = url.strip()
        for _ in range(MAX_REDIRECTS + 1):
            target, host = await self.check(current)
            request = self.http.build_request(
                "GET",
                target,
                headers={"Host": host, "User-Agent": USER_AGENT, "Accept": "*/*"},
                extensions={"sni_hostname": host, "timeout": TIMEOUT.as_dict()},
            )
            try:
                response = await self.http.send(request, stream=True, follow_redirects=False)
            except httpx.HTTPError as e:
                raise FetchRefused(f"The address could not be fetched ({type(e).__name__}).") from e
            try:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchRefused("A redirect without a location.")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise FetchRefused(f"The address answered {response.status_code}.")
                length = response.headers.get("content-length")
                if length and length.isdigit() and int(length) > self.max_bytes:
                    raise FetchRefused("The document is larger than the size cap.")
                chunks: list[bytes] = []
                size = 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > self.max_bytes:
                        raise FetchRefused("The document is larger than the size cap.")
                    chunks.append(part)
            finally:
                await response.aclose()
            path = urlsplit(current).path
            name = path.rsplit("/", 1)[-1] or urlsplit(current).hostname or "document"
            mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
            return Fetched(current, b"".join(chunks), mime, name)
        raise FetchRefused("Too many redirects.")
