"""The Slack channel's protocol: Events API and interactivity in, Web API out.

- **Every request is checked**: ``X-Slack-Signature`` must be ``v0=`` and the HMAC-SHA256, with
  the app's signing secret, of ``v0:<X-Slack-Request-Timestamp>:<body>``, and the timestamp within
  five minutes of now.
- Direct messages to the app and mentions of it start or continue a conversation; the answer goes
  in the message's thread. Messages from bots (the app's own included) and edits are ignored, and a
  delivery Slack retries is handled once.
- Confirmations and questions are buttons; pressing one comes back on the interactivity URL.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any

import httpx

from evokai.channels.config import SlackConfig
from evokai.connectors.base import secret

TOLERANCE = 300


def verify(signing_secret: str, timestamp: str | None, signature: str | None, body: bytes) -> bool:
    """Whether a request is Slack's and recent.

    Returns:
        True when the signature matches and the timestamp is within five minutes.
    """
    if not signing_secret or not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs(time.time() - int(timestamp)) > TOLERANCE:
        return False
    mac = hmac.new(
        signing_secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"v0={mac}", signature)


def sign(signing_secret: str, timestamp: int, body: bytes) -> str:
    """A Slack signature (tests and the smoke send deliveries with it).

    Returns:
        ``v0=<hex>``.
    """
    mac = hmac.new(signing_secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256)
    return f"v0={mac.hexdigest()}"


def text_of(event: dict[str, Any]) -> str:
    """The person's text, without mentions of the app.

    Returns:
        The text.
    """
    return re.sub(r"<@[A-Z0-9]+>", "", str(event.get("text") or "")).strip()


class Slack:
    """Calls Slack's Web API for the Slack channels of the process."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        """Configure.

        Args:
            http: The process's HTTP client.
        """
        self.http = http

    async def call(self, cfg: SlackConfig, method: str, body: dict[str, Any]) -> dict[str, Any]:
        """Call a Web API method with the bot token.

        Returns:
            Slack's answer.

        Raises:
            ValueError: Slack answered ``ok: false`` or an error status.
        """
        r = await self.http.post(
            f"{cfg.api_url.rstrip('/')}/{method}",
            content=json.dumps(body, ensure_ascii=False).encode(),
            headers={
                "Authorization": f"Bearer {secret(cfg.bot_token_env, 'Slack bot token')}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=20,
        )
        try:
            answer = r.json()
        except ValueError:
            answer = {}
        if r.status_code >= 300 or not answer.get("ok"):
            raise ValueError(f"Slack {method}: {answer.get('error') or r.status_code}")
        return answer


def button(text: str, action_id: str, value: dict[str, Any], style: str | None = None) -> dict:
    """A button block element.

    Returns:
        The element.
    """
    out: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text[:75]},
        "action_id": action_id,
        "value": json.dumps(value)[:2000],
    }
    if style:
        out["style"] = style
    return out
