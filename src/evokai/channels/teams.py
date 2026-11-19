"""The Microsoft Teams channel's protocol: Bot Framework activities in, replies out.

- **Every request is checked**: the ``Authorization`` bearer token must be a JWT signed by one of
  the Bot Framework's keys (from its OpenID metadata), issued by ``https://api.botframework.com``
  for the bot's app id, not expired, with a ``serviceurl`` claim equal to the activity's
  ``serviceUrl``; a key's endorsements, when it has some, must include the activity's channel.
- **Replies are proactive**: the request is answered at once (200) and the answer is posted to
  the conversation later, with a token the bot asks Entra for (client credentials). The
  ``serviceUrl`` replies go to must be on the allowed hosts.
- **Confirmations and questions** are adaptive cards; pressing a button comes back as a message
  activity whose ``value`` carries the decision.
"""

from __future__ import annotations

import logging
import time
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import quote, urlparse

import httpx
import jwt

from evokai.channels.config import TeamsConfig
from evokai.connectors.base import secret
from evokai.core.util import TtlCache

log = logging.getLogger("evokai.channels")

OPENID = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
BOT_SCOPE = "https://api.botframework.com/.default"
CARD = "application/vnd.microsoft.card.adaptive"


class TeamsRefused(Exception):
    """A request that does not come from the Bot Framework for this bot."""


class Teams:
    """Verifies activities and posts replies, for every Teams channel of the process."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        """Configure.

        Args:
            http: The process's HTTP client.
        """
        self.http = http
        self._meta: TtlCache[str, Any] = TtlCache(10, 3600)
        self._tokens: dict[str, tuple[float, str]] = {}

    async def _keys(self, *, fresh: bool = False) -> list[dict[str, Any]]:
        hit = None if fresh else self._meta.get("keys")
        if hit is None:
            meta = (await self.http.get(OPENID, timeout=10)).json()
            hit = (await self.http.get(str(meta["jwks_uri"]), timeout=10)).json().get("keys", [])
            self._meta.set("keys", hit)
        return hit

    async def verify(self, cfg: TeamsConfig, authorization: str | None, activity: dict) -> None:
        """Check that a request comes from the Bot Framework for this bot.

        Raises:
            TeamsRefused: Anything that does not check.
        """
        if not authorization or not authorization.lower().startswith("bearer "):
            raise TeamsRefused("no token")
        token = authorization[7:].strip()
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise TeamsRefused("malformed token") from e
        if header.get("alg") != "RS256":
            raise TeamsRefused("algorithm")
        kid = header.get("kid")
        try:
            keys = await self._keys()
            key = next((k for k in keys if k.get("kid") == kid), None)
            if key is None:
                keys = await self._keys(fresh=True)
                key = next((k for k in keys if k.get("kid") == kid), None)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise TeamsRefused("the Bot Framework keys could not be read") from e
        if key is None:
            raise TeamsRefused("unknown key")
        endorsements = key.get("endorsements")
        channel = str(activity.get("channelId") or "")
        if endorsements and channel not in endorsements:
            raise TeamsRefused("the key does not endorse this channel")
        try:
            claims = jwt.decode(
                token,
                jwt.PyJWK(key).key,
                algorithms=["RS256"],
                audience=cfg.app_id,
                issuer=ISSUER,
                leeway=300,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as e:
            raise TeamsRefused(type(e).__name__) from e
        service_url = str(activity.get("serviceUrl") or "")
        if claims.get("serviceurl") and claims["serviceurl"].rstrip("/") != service_url.rstrip("/"):
            raise TeamsRefused("serviceUrl mismatch")

    @staticmethod
    def service_url_allowed(cfg: TeamsConfig, url: str) -> bool:
        """Whether replies may be posted to a service URL.

        Returns:
            True for https on an allowed host.
        """
        u = urlparse(url)
        host = (u.hostname or "").lower()
        return u.scheme == "https" and any(
            fnmatchcase(host, h.lower()) for h in cfg.service_url_hosts
        )

    async def token(self, cfg: TeamsConfig) -> str:
        """The bot's own token to post replies.

        Returns:
            The token.

        Raises:
            ValueError: Entra refused the bot's credentials.
        """
        key = f"{cfg.tenant_id}:{cfg.app_id}"
        hit = self._tokens.get(key)
        if hit and hit[0] > time.time() + 60:
            return hit[1]
        tenant = cfg.tenant_id or "botframework.com"
        r = await self.http.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": cfg.app_id,
                "client_secret": secret(cfg.app_password_env, "Teams bot"),
                "scope": BOT_SCOPE,
            },
            timeout=15,
        )
        if r.status_code != 200:
            raise ValueError(f"Microsoft refused the bot's credentials ({r.status_code})")
        body = r.json()
        token = str(body["access_token"])
        self._tokens[key] = (time.time() + int(body.get("expires_in") or 3600), token)
        return token

    async def send(
        self, cfg: TeamsConfig, reference: dict[str, Any], activity: dict[str, Any]
    ) -> None:
        """Post an activity to a conversation (a proactive reply).

        Raises:
            ValueError: A service URL that is not allowed, or the Bot Connector refused it.
        """
        service_url = str(reference.get("serviceUrl") or "")
        if not self.service_url_allowed(cfg, service_url):
            raise ValueError("the conversation's serviceUrl is not an allowed host")
        conv = quote(str(reference.get("conversationId") or ""), safe="")
        reply_to = str(reference.get("activityId") or "")
        url = f"{service_url.rstrip('/')}/v3/conversations/{conv}/activities"
        if reply_to:
            url += f"/{quote(reply_to, safe='')}"
        body = {
            "type": "message",
            "from": {"id": reference.get("botId") or cfg.app_id},
            "conversation": {"id": reference.get("conversationId")},
            **({"replyToId": reply_to} if reply_to else {}),
            **activity,
        }
        r = await self.http.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {await self.token(cfg)}"},
            timeout=20,
        )
        if r.status_code >= 300:
            raise ValueError(f"the Bot Connector refused the reply ({r.status_code})")


def reference_of(activity: dict[str, Any]) -> dict[str, Any]:
    """What a reply needs from an activity.

    Returns:
        serviceUrl, conversationId, activityId, botId, tenantId.
    """
    conv = activity.get("conversation") or {}
    return {
        "serviceUrl": activity.get("serviceUrl"),
        "conversationId": conv.get("id"),
        "activityId": activity.get("id"),
        "botId": (activity.get("recipient") or {}).get("id"),
        "tenantId": conv.get("tenantId")
        or ((activity.get("channelData") or {}).get("tenant") or {}).get("id"),
    }


def text_of(activity: dict[str, Any]) -> str:
    """The person's text, without the bot's @mention.

    Returns:
        The text.
    """
    text = str(activity.get("text") or "")
    for e in activity.get("entities") or []:
        if e.get("type") == "mention" and (e.get("mentioned") or {}).get("id") == (
            activity.get("recipient") or {}
        ).get("id"):
            text = text.replace(str(e.get("text") or ""), "")
    return text.strip()


def card(body: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    """An adaptive card attachment.

    Returns:
        The attachment.
    """
    return {
        "contentType": CARD,
        "content": {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
            "actions": actions,
        },
    }
