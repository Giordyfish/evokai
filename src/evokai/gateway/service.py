"""A gateway request from start to end: who, the controls, the forward, the record."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Coroutine
from datetime import datetime
from typing import Any

import httpx
from fastapi import Request
from starlette.responses import Response

from evokai import protection
from evokai.core.errors import Unauthorized
from evokai.core.util import TtlCache, utcnow
from evokai.db import models as m
from evokai.db.repositories import GATEWAY
from evokai.gateway import checks
from evokai.gateway.checks import ANTHROPIC, PROVIDER_NAMES, Block
from evokai.gateway.relay import (
    KEY_HEADER,
    Relay,
    Usage,
    anthropic_error,
    build_request,
    elapsed_ms,
    openai_error,
)
from evokai.identity.models import GatewayPerson

log = logging.getLogger("evokai.gateway")

APP_TTL = 5
"""Seconds a process trusts its copy of a gateway's settings and policy."""
EVENT_ONCE_SECONDS = 300
"""A refused request sent again within this time (a tool's retries) records no new event."""
USED_TTL = 30
"""Seconds a process trusts its sum of a person's tokens (token caps)."""


class GatewayService:
    """The gateways' doors: one HTTP client to the providers, short caches of settings."""

    def __init__(self, svc: Any, client: httpx.AsyncClient | None = None) -> None:
        """Wire the gateway.

        Args:
            svc: Services.
            client: The client to the providers (tests pass one with a fake transport).
        """
        self.svc = svc
        s = svc.settings
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=s.gateway_timeout_seconds, write=120, pool=60),
            limits=httpx.Limits(max_connections=None, max_keepalive_connections=256),
            follow_redirects=False,
        )
        self.upstreams: dict[str, str] = {ANTHROPIC: s.gateway_anthropic_url.rstrip("/")}
        self._apps: TtlCache[str, m.Application] = TtlCache(max_items=500, ttl_seconds=APP_TTL)
        self._used: TtlCache[str, int] = TtlCache(max_items=10_000, ttl_seconds=USED_TTL)
        self._verdicts: TtlCache[str, protection.Verdict] = TtlCache(
            max_items=1_000, ttl_seconds=APP_TTL
        )

        self._writes: set[asyncio.Task[None]] = set()

    def _later(self, write: Coroutine[Any, Any, None]) -> None:
        """Write a record off the request's path (the forward does not wait for the database)."""
        task = asyncio.create_task(write)
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    async def idle(self) -> None:
        """Wait for the records still being written."""
        while self._writes:
            await asyncio.gather(*list(self._writes), return_exceptions=True)

    async def close(self) -> None:
        """Finish the records being written and release the connections."""
        await self.idle()
        await self.client.aclose()

    async def gateway(self, gateway_id: str) -> m.Application | None:
        """A gateway's row (cached for ``APP_TTL`` seconds).

        Returns:
            The row, or None when it is not a gateway.
        """
        hit = self._apps.get(gateway_id)
        if hit is None:
            hit = await self.svc.store.applications.get(gateway_id)
            if hit is None or hit.kind != GATEWAY:
                return None
            self._apps.set(gateway_id, hit)
        return hit

    def forget(self, gateway_id: str) -> None:
        """Read a gateway again on its next request (after a change here)."""
        self._apps.pop(gateway_id)

    # The door

    async def relay(
        self, request: Request, provider: str, path: str, *, upstream: str | None = None
    ) -> Response:
        """Check a request and forward it, or refuse it in the provider's error shape.

        Args:
            request: The tool's request.
            provider: ``anthropic`` or ``openai``.
            path: The provider's path (``/v1/messages``).
            upstream: The provider's base URL, when it is not the provider's default.

        Returns:
            The provider's answer, streamed, or the refusal.
        """
        t0 = time.perf_counter()
        error = anthropic_error if provider == ANTHROPIC else openai_error
        try:
            person = await self.svc.auth.gateway(request.headers.get(KEY_HEADER, ""))
        except Unauthorized as e:
            return error(401, "authentication", _key_message(provider, e.code))
        gw = await self.gateway(person.application_id)
        if gw is None or gw.status != "active":
            return error(401, "authentication", "Evokai gateway: this gateway is not active.")
        body = await request.body()
        size = len(body)
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            return error(400, "invalid_request", "Evokai gateway: the request body is not JSON.")
        model = str(parsed.get("model") or "")
        count = path.endswith("/count_tokens")
        found = await self._controls(gw, person, provider, model, parsed, count=count)
        if events := await self._first_seen(person, found):
            self._later(self.svc.guard.store_events(events))
        if found.block is not None:
            b = found.block
            if not count:
                await self._record(
                    gw,
                    person,
                    provider,
                    model,
                    "blocked",
                    b.status,
                    size,
                    t0,
                    control=b.control,
                )
            return error(b.status, b.kind, b.message, **_refusal_headers(provider, b))
        base = upstream or self.upstreams[provider]
        query = request.url.query
        url = f"{base}{path}" + (f"?{query}" if query else "")
        req = build_request(self.client, url, request.headers.raw, body)
        try:
            answer = await self.client.send(req, stream=True)
        except httpx.HTTPError as e:
            log.warning("gateway_upstream_unreachable", extra={"error_type": type(e).__name__})
            if not count:
                await self._record(gw, person, provider, model, "failed", 502, size, t0)
            return error(
                504 if isinstance(e, httpx.TimeoutException) else 502,
                "api",
                f"Evokai gateway: {PROVIDER_NAMES[provider]} could not be reached "
                f"({type(e).__name__}). Try again.",
            )
        first_byte = elapsed_ms(t0)
        usage = Usage(
            provider,
            answer.headers.get("content-type", ""),
            answer.headers.get("content-encoding", ""),
        )

        async def done(outcome: str) -> None:
            if count:
                return
            await self._record(
                gw,
                person,
                provider,
                model,
                outcome,
                answer.status_code,
                size,
                t0,
                usage=usage,
                first_byte_ms=first_byte,
            )

        return Relay(answer, usage, done)

    # The controls

    async def _controls(
        self,
        gw: m.Application,
        person: GatewayPerson,
        provider: str,
        model: str,
        body: dict[str, Any],
        *,
        count: bool,
    ) -> checks.Checked:
        """Every control of a request, in order; the first that refuses ends them.

        A token count (``count_tokens``) only passes the provider, model and data protection
        checks: it is not a model call, and its content is checked when it is sent for real.

        Returns:
            The events and the block, if any.
        """
        out = checks.Checked(fingerprint=model[:120])
        settings = gw.gateway or {}
        name = PROVIDER_NAMES.get(provider, provider)

        def refuse(control: str, rule: str, block: Block, **details: Any) -> checks.Checked:
            out.events.append(
                checks.gateway_event(
                    gw.id,
                    person.label,
                    control,
                    rule,
                    {**details, "provider": provider, "model": model[:120]},
                )
            )
            out.block = block
            return out

        if provider not in (settings.get("providers") or []):
            return refuse(
                "gateway.provider",
                "provider_disabled",
                Block(
                    "gateway.provider",
                    400,
                    "invalid_request",
                    f"Evokai gateway: {name} is not enabled on this gateway.",
                ),
            )
        if not checks.model_allowed(model, gw.allowed_models):
            allowed = ", ".join(str(x) for x in gw.allowed_models or [])
            return refuse(
                "gateway.model",
                "model_not_allowed",
                Block(
                    "gateway.model",
                    400,
                    "invalid_request",
                    f"Evokai gateway: the model {model or '(none)'} is not allowed on this "
                    f"gateway. Allowed: {allowed}.",
                ),
            )
        verdict = self._verdict(gw, provider, model)
        if verdict is not None and not verdict.allowed:
            reasons = "; ".join(_reason_text(r) for r in verdict.reasons)
            return refuse(
                "gateway.protection",
                verdict.reasons[0].dimension if verdict.reasons else "profile",
                Block(
                    "gateway.protection",
                    400,
                    "invalid_request",
                    f"Evokai gateway: the data protection profile of this gateway does not let "
                    f"data reach {name} ({model}): {reasons}.",
                ),
                reasons=[r.view() for r in verdict.reasons],
            )
        if count:
            return out
        policy = self.svc.guard.policy(gw)
        events, record = checks.event_rows(gw.id, person.label, provider, model)
        if block := await self._rate(person, policy, record):
            out.events += events
            out.block = block
            return out
        if capped := await self._caps(person, settings):
            return refuse("gateway.cap", capped[0], capped[1])
        text = (
            checks.anthropic_new_text(body)
            if provider == ANTHROPIC
            else checks.openai_new_text(body)
        )
        out.fingerprint = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:24]
        out.block = checks.text_checks(
            policy, text, provider=provider, known=self.svc.guard.known_secrets, record=record
        )
        out.events += events
        return out

    def _verdict(self, gw: m.Application, provider: str, model: str) -> protection.Verdict | None:
        """The data protection verdict on the provider's model (cached like the gateway's row).

        Returns:
            The verdict, or None when neither the gateway nor the system policy sets a profile.
        """
        if protection.profile_of(gw).empty:
            return None
        key = f"{gw.id}:{provider}:{model}:{protection.system_profile()!r}:{gw.data_protection!r}"
        hit = self._verdicts.get(key)
        if hit is None:
            hit = protection.model_verdict(gw, f"{provider}:{model}", self.svc.settings.aws_region)
            self._verdicts.set(key, hit)
        return hit

    async def _first_seen(
        self, person: GatewayPerson, found: checks.Checked
    ) -> list[dict[str, Any]]:
        """The events of a request, but those its same request already recorded in the last minutes.

        Returns:
            The events to store.
        """
        out = []
        for e in found.events:
            key = f"gw:event:{person.key_id}:{e['control']}:{e['action']}:{found.fingerprint}"
            if await self.svc.shared.set_once(key, "1", EVENT_ONCE_SECONDS):
                out.append(e)
        return out

    async def _rate(self, person: GatewayPerson, policy: Any, record: Any) -> Block | None:
        """The person's requests per minute (``abuse.requests_per_minute``, every instance).

        In monitor the event is recorded once per person and minute.

        Returns:
            The block, when enforced and over.
        """
        rate = policy.settings.abuse.requests_per_minute if policy.settings.abuse else None
        if not policy.on("abuse.rate") or not rate:
            return None
        minute = int(time.time() // 60)
        n = await self.svc.shared.incr(f"gw:rate:{person.key_id}:{minute}", 120)
        if n <= rate:
            return None
        enforce = policy.enforced("abuse.rate")
        first = await self.svc.shared.set_once(f"gw:rate-noted:{person.key_id}:{minute}", "1", 90)
        if enforce or first:
            record(
                "abuse.rate",
                "requests_per_minute",
                "blocked" if enforce else "logged",
                {"count": n, "limit": rate},
            )
        if not enforce:
            return None
        return Block(
            "abuse.rate",
            429,
            "rate_limit",
            f"Evokai gateway: more than {rate} requests in a minute from you (abuse.rate). "
            "Wait a moment.",
            retry_after=max(1, 60 - int(time.time()) % 60),
        )

    async def _caps(
        self, person: GatewayPerson, settings: dict[str, Any]
    ) -> tuple[str, Block] | None:
        """The person's token caps on this gateway, per day and per month (UTC), when set.

        Returns:
            The cap's setting and the block, for the first cap reached; or None.
        """
        now = utcnow()
        for field, label, since in (
            ("tokens_per_day", "daily", now.replace(hour=0, minute=0, second=0, microsecond=0)),
            (
                "tokens_per_month",
                "monthly",
                now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            ),
        ):
            cap = settings.get(field)
            if not cap:
                continue
            if await self._used_since(person.key_id, field, since) >= int(cap):
                return field, Block(
                    "gateway.cap",
                    429,
                    "rate_limit",
                    f"Evokai gateway: you have used your {label} token cap on this gateway "
                    f"({int(cap):,} tokens). Ask your administrator if you need more.",
                    retry_after=3600,
                )
        return None

    async def _used_since(self, key_id: str, field: str, since: datetime) -> int:
        cache = f"{key_id}:{field}:{since.isoformat()}"
        hit = self._used.get(cache)
        if hit is None:
            hit = await self.svc.store.gateway.tokens_since(key_id, since)
            self._used.set(cache, hit)
        return hit

    # The record

    async def _record(
        self,
        gw: m.Application,
        person: GatewayPerson,
        provider: str,
        model: str,
        outcome: str,
        status: int,
        request_bytes: int,
        t0: float,
        *,
        control: str | None = None,
        usage: Usage | None = None,
        first_byte_ms: int | None = None,
    ) -> None:
        """One row for the request: who, what, tokens, timings, outcome (never content)."""
        try:
            await self.svc.store.gateway.add(
                application_id=gw.id,
                key_id=person.key_id,
                person=person.label[:120],
                provider=provider,
                model=model[:120],
                outcome=outcome,
                control=control,
                status=status,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                cache_read_tokens=usage.cache_read if usage else 0,
                cache_write_tokens=usage.cache_write if usage else 0,
                request_bytes=request_bytes,
                response_bytes=usage.bytes if usage else 0,
                first_byte_ms=first_byte_ms,
                duration_ms=elapsed_ms(t0),
            )
        except Exception as e:
            log.warning("gateway_not_recorded", extra={"error_type": type(e).__name__})


def _key_message(provider: str, code: str) -> str:
    """What a person reads when their gateway key is missing or refused.

    Returns:
        The message.
    """
    how = (
        'ANTHROPIC_CUSTOM_HEADERS="x-evokai-key: evk_gw_..."'
        if provider == ANTHROPIC
        else 'env_http_headers = { "x-evokai-key" = "EVOKAI_GATEWAY_KEY" } in the Codex provider'
    )
    if code == "gateway":
        return "Evokai gateway: this gateway is not active. Ask your administrator."
    return (
        "Evokai gateway: no valid gateway key. Send your personal key in the x-evokai-key header "
        f"({how}); ask your administrator for one if you have none, or if yours was revoked."
    )


def _refusal_headers(provider: str, b: Block) -> dict[str, str]:
    """Headers of a refusal: whether the tool should retry, and when.

    Returns:
        The headers.
    """
    out: dict[str, str] = {}
    if provider == ANTHROPIC:
        out["x-should-retry"] = "true" if b.status == 429 and b.retry_after else "false"
    if b.retry_after:
        out["retry-after"] = str(b.retry_after)
    return out


def _reason_text(r: protection.Reason) -> str:
    """A data protection reason as a person reads it.

    Returns:
        e.g. ``jurisdiction outside (US)``.
    """
    value = f" ({r.value})" if r.value not in (None, "", []) else ""
    source = " [system policy]" if r.source == "system" else ""
    return f"{r.dimension} {r.code}{value}{source}"
