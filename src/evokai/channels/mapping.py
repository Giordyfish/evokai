"""How a person of a channel becomes the application's subject.

Each channel names the person its own way: an e-mail address, an Entra object id (Teams), a Slack
user id. The channel's ``mapping`` turns it into the subject Evokai keeps:

- ``hashed`` (the e-mail default): ``email:<hash>``, an HMAC of the id with the deployment's
  signing key, so no address is stored as a subject and the same person always maps the same way;
- ``id`` (the Teams and Slack default): ``teams:<object id>``, ``slack:<team>:<user>``;
- ``webhook``: the application answers with its own subject id, or none for someone it does not
  know (the delivery is then refused). The call is signed like workflow webhooks
  (``X-Evokai-Timestamp``, ``X-Evokai-Signature``) and its answer cached for a few minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from evokai.channels.config import Mapping
from evokai.connectors.base import secret
from evokai.core.util import TtlCache
from evokai.workflows.webhooks import sign

log = logging.getLogger("evokai.channels")


@dataclass(frozen=True)
class ChannelUser:
    """A person as a channel knows them."""

    kind: str
    """``email``, ``teams`` or ``slack``."""
    id: str
    """The channel's own id: the address (lower case), ``<object id>``, ``<team>:<user>``."""
    name: str | None = None
    email: str | None = None
    tenant: str | None = None


class MappingRefused(Exception):
    """The application does not know this person (or its webhook did not answer)."""


class Mapper:
    """Maps channel users to subjects."""

    def __init__(self, http: httpx.AsyncClient, signing_key: str) -> None:
        """Configure.

        Args:
            http: The process's HTTP client.
            signing_key: The deployment's signing key (for hashed subjects).
        """
        self.http = http
        self.signing_key = signing_key
        self._cache: TtlCache[tuple[str, str, str], str] = TtlCache(20_000, 300)

    def hashed(self, application_id: str, user: ChannelUser) -> str:
        """The hashed subject of a person.

        Returns:
            ``<kind>:<32 hex>``.
        """
        mac = hmac.new(
            self.signing_key.encode(),
            f"{application_id}\n{user.kind}\n{user.id}".encode(),
            hashlib.sha256,
        )
        return f"{user.kind}:{mac.hexdigest()[:32]}"

    async def subject(
        self, application_id: str, channel_name: str, mapping: Mapping, user: ChannelUser
    ) -> str:
        """The subject of a person of a channel.

        Returns:
            The subject.

        Raises:
            MappingRefused: The webhook does not know the person, or failed.
        """
        if mapping.strategy == "id":
            return f"{user.kind}:{user.id}"[:200]
        if mapping.strategy == "hashed":
            return self.hashed(application_id, user)
        key = (application_id, channel_name, f"{user.kind}:{user.id}")
        hit = self._cache.get(key)
        if hit is not None:
            if not hit:
                raise MappingRefused("unknown person")
            return hit
        body = json.dumps(
            {
                "channel": user.kind,
                "channelName": channel_name,
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "tenant": user.tenant,
            },
            ensure_ascii=False,
        ).encode()
        ts = int(time.time())
        try:
            r = await self.http.post(
                str(mapping.webhook_url),
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Evokai-Timestamp": str(ts),
                    "X-Evokai-Signature": sign(
                        secret(mapping.webhook_secret_env, "channel mapping webhook"), ts, body
                    ),
                    "User-Agent": "Evokai-Channels/1.0",
                },
                timeout=10,
                follow_redirects=False,
            )
            answer: Any = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            log.warning("mapping_webhook_failed", extra={"error": type(e).__name__})
            raise MappingRefused("the mapping webhook did not answer") from e
        found = str((answer or {}).get("subject") or "")[:200] if isinstance(answer, dict) else ""
        if mapping.cache_seconds:
            self._cache.set(key, found)
        if not found:
            raise MappingRefused("unknown person")
        return found
