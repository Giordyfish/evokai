"""Signed webhooks: ``notify`` steps call out, webhook triggers are called in.

Both directions sign the same way. The sender puts the time (Unix seconds) in
``X-Evokai-Timestamp`` and ``v1=<hex>`` in ``X-Evokai-Signature``, where the hex is the HMAC-SHA256,
with the shared secret, of ``<timestamp>.`` followed by the raw body. The receiver recomputes it,
compares in constant time and refuses a timestamp too far from now (a replay).

Out-going calls are safe against server-side request forgery the way knowledge fetching is
(``evokai.knowledge.fetch``): only https (or http) addresses on an allowed domain, resolved to
public addresses only, the request pinned to the checked address, no redirects followed, no
cookies or credentials sent.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from evokai.knowledge.fetch import Fetcher, FetchRefused, Resolver, resolve

USER_AGENT = "Evokai-Workflows/1.0"
TIMEOUT = httpx.Timeout(20, connect=5)
MAX_BODY = 256_000


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The signature of a body.

    Returns:
        ``v1=<hex>``.
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"v1={mac.hexdigest()}"


def verify(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    tolerance: int,
    now: float,
) -> bool:
    """Whether a signed request is genuine and recent.

    Returns:
        True when the signature matches and the timestamp is within the tolerance.
    """
    if not secret or not timestamp or not signature or not timestamp.isdigit():
        return False
    ts = int(timestamp)
    if abs(now - ts) > tolerance:
        return False
    expected = sign(secret, ts, body)
    return any(hmac.compare_digest(expected, part.strip()) for part in signature.split(","))


@dataclass
class Sent:
    """What a notify call got back."""

    status: int
    ok: bool


class Notifier:
    """Sends notify steps' webhooks within the deployment's allow-list."""

    def __init__(
        self, http: httpx.AsyncClient, allow: list[str], resolver: Resolver = resolve
    ) -> None:
        """Configure.

        Args:
            http: The process's HTTP client.
            allow: Allowed domains (``EVOKAI_WORKFLOW_NOTIFY_ALLOW``).
            resolver: DNS resolution (tests replace it).
        """
        self.http = http
        self.fetcher = Fetcher(http, allow, MAX_BODY, resolver)

    async def send(
        self,
        url: str,
        body: Any,
        *,
        secret: str | None,
        idempotency_key: str,
        headers: dict[str, str] | None = None,
    ) -> Sent:
        """Post a JSON body.

        Returns:
            The status (2xx is ok).

        Raises:
            FetchRefused: The address is not allowed or not public (an ``httpx.HTTPError`` means
                the call failed and a retry may pass).
        """
        if not self.fetcher.allow:
            raise FetchRefused(
                "Notify steps are off in this deployment (EVOKAI_WORKFLOW_NOTIFY_ALLOW)."
            )
        target, host = await self.fetcher.check(url)
        raw = json.dumps(body, ensure_ascii=False, default=str).encode()
        if len(raw) > MAX_BODY:
            raise FetchRefused("The notification body is too large.")
        ts = int(time.time())
        h = {
            "Host": host,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "X-Evokai-Timestamp": str(ts),
            **(headers or {}),
        }
        if secret:
            h["X-Evokai-Signature"] = sign(secret, ts, raw)
        request = self.http.build_request(
            "POST",
            target,
            content=raw,
            headers=h,
            extensions={"sni_hostname": host, "timeout": TIMEOUT.as_dict()},
        )
        response = await self.http.send(request, follow_redirects=False)
        await response.aclose()
        return Sent(status=response.status_code, ok=200 <= response.status_code < 300)
