"""Cost alerts: a forecast that crosses a threshold of a cap fires once per month and threshold.

An alert is a row (``evokai_cost_alerts``, unique per application, scope, month and threshold, so
it fires once however often the forecast is checked), a log line ``cost_alert``, the console's
banner and, when the application set one, a webhook:

- only ``https`` addresses on a domain of ``EVOKAI_ALERT_WEBHOOK_ALLOW`` (a domain allows its
  subdomains), resolving to public addresses only, never following redirects;
- the body is JSON with shares only (``forecastPct``, ``usedPct``, ``threshold``), never amounts;
- it is signed: ``X-Evokai-Signature: sha256=<hex>``, the HMAC-SHA256 of ``<timestamp>.<body>``
  with the application's ``cost_alert_secret``, and ``X-Evokai-Timestamp`` (epoch seconds) so the
  receiver can refuse old deliveries. Without a secret no webhook is called.

Evokai has no e-mail sender of its own: a webhook is how an application forwards alerts to e-mail,
chat or paging.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from evokai.core.errors import BadRequest
from evokai.guard.content import host_allowed
from evokai.knowledge.fetch import public, resolve

DEFAULT_THRESHOLDS = (75, 90, 100)
TIMEOUT = httpx.Timeout(5, connect=3)


def thresholds_of(defaults: dict[str, Any] | None) -> list[int]:
    """The application's alert thresholds.

    Returns:
        Shares of a cap, ascending.
    """
    raw = (defaults or {}).get("cost_alert_thresholds") or DEFAULT_THRESHOLDS
    return sorted({int(x) for x in raw if 0 < int(x) <= 200})


def crossed(forecast_pct: float | None, thresholds: list[int], fired: set[int]) -> list[int]:
    """The thresholds a forecast reaches that have not fired yet this month.

    Returns:
        Thresholds, ascending.
    """
    if forecast_pct is None:
        return []
    return [t for t in thresholds if forecast_pct >= t and t not in fired]


def check_webhook(url: str, allow: list[str]) -> str:
    """Validate a webhook address against the deployment's allow-list.

    Returns:
        The address.

    Raises:
        BadRequest: Not https, or a host outside ``EVOKAI_ALERT_WEBHOOK_ALLOW``.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise BadRequest("A cost alert webhook must be an https address.", code="webhook")
    if parts.username or parts.password:
        raise BadRequest("A webhook address may not carry credentials.", code="webhook")
    if not allow or not host_allowed(parts.hostname, allow):
        raise BadRequest(
            "The webhook's domain is not in EVOKAI_ALERT_WEBHOOK_ALLOW.", code="webhook_domain"
        )
    return url


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The signature of a delivery.

    Returns:
        ``sha256=<hex>`` of HMAC-SHA256(secret, ``<timestamp>.<body>``).
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def deliver(
    http: httpx.AsyncClient,
    url: str,
    secret: str | None,
    payload: dict[str, Any],
    allow: list[str],
    *,
    resolver: Any = None,
) -> dict[str, Any]:
    """Post an alert to the application's webhook.

    Returns:
        How it went: status (an HTTP status, or ``refused``, ``no_secret``, ``error``) and when.
    """
    at = int(time.time())
    if not secret:
        return {"status": "no_secret", "at": at}
    try:
        check_webhook(url, allow)
        host = urlsplit(url).hostname or ""
        addresses = await (resolver or resolve)(host, 443)
        if not addresses or not all(public(ip) for ip in addresses):
            return {"status": "refused", "at": at, "reason": "address"}
    except BadRequest as e:
        return {"status": "refused", "at": at, "reason": e.code}
    except OSError:
        return {"status": "error", "at": at, "reason": "dns"}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Evokai-Alerts/1.0",
        "X-Evokai-Timestamp": str(at),
        "X-Evokai-Signature": sign(secret, at, body),
        "X-Evokai-Event": "cost_alert",
    }
    try:
        r = await http.post(
            url, content=body, headers=headers, timeout=TIMEOUT, follow_redirects=False
        )
        return {"status": r.status_code, "at": at}
    except httpx.HTTPError as e:
        return {"status": "error", "at": at, "reason": type(e).__name__}
