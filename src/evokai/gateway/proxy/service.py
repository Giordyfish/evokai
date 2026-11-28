"""What the proxy decides about a request: who, the organisation, the gateway's checks, the verdict.

Free of the network engine: ``evokai.gateway.proxy.engine`` hands it neutral requests
(``adapters.Request``) and applies what it returns. The checks, the records and the security
events are the base URL door's (``evokai.gateway.service.GatewayService``).

In order, for a request an adapter handles:

1. the adapter reads it; a shape it does not know is refused (``proxy.format_unknown``);
2. the session's key and its binding (``identity``); the organisation must be one of
   ``allowed_orgs`` for the provider when that is set (``proxy.org_refused``);
3. the binding's e-mail is a gateway person; when it is not (or no binding is known yet), the
   ``unidentified`` setting blocks (``proxy.unidentified``) or, in ``record``, lets it through on
   the default gateway (``EVOKAI_PROXY_DEFAULT_GATEWAY``), whose rules, records and retention then
   apply; ``record`` without a default gateway blocks;
4. the gateway's checks (provider, model, data protection, rate, caps, the input checks on the new
   text); a refusal is the adapter's (a synthetic answer or a rewrite);
5. one usage row per model request, when its answer ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from evokai import policies
from evokai.core.util import TtlCache
from evokai.db import models as m
from evokai.db.kinds import GATEWAY
from evokai.gateway import checks, scan
from evokai.gateway.proxy import catalogue
from evokai.gateway.proxy.adapters import (
    Adapter,
    Answer,
    FormatUnknown,
    Frame,
    FrameAdapter,
    Inspection,
    Request,
    Rewrite,
    Verdict,
    providers_with_adapters,
    registry,
)
from evokai.gateway.proxy.adapters import anthropic as anthropic_adapters
from evokai.gateway.proxy.adapters import claude as claude_adapters
from evokai.gateway.proxy.identity import (
    Binding,
    Claims,
    IdentityBook,
    Key,
    claude_ai_key,
    from_claude_account,
    from_copilot_token,
    from_copilot_user,
    from_github_user,
    from_oauth_profile,
    from_token_response,
    github_key,
    mask_email,
)
from evokai.gateway.proxy.texts import Texts
from evokai.gateway.relay import Usage
from evokai.identity.models import GatewayPerson

log = logging.getLogger("evokai.gateway.proxy")

SETTING = "proxy"
"""The console setting that overrides ``allowed_orgs``, ``unidentified`` and ``default_gateway``."""
REFRESH_SECONDS = 30
"""How often the providers enabled on gateways and the console setting are read again."""
EVENT_ONCE_SECONDS = 300
PERSON_TTL = 30
LEARN_CLAUDE = "claude_account"
LEARN_PROFILE = "oauth_profile"
LEARN_SIGNIN = "oauth_signin"
SIGNIN_HOST = "platform.claude.com"
SIGNIN_PATH = "/v1/oauth/token"
"""Claude Code's OAuth token endpoint (``TOKEN_URL`` in the Claude Code 2.1.332 bundle)."""
WS_STATUS = 101
"""The status of a model request made on a WebSocket (the upgrade's: its answers have none)."""
GITHUB_HOST = "api.github.com"
LEARN_GITHUB_USER = "github_user"
LEARN_COPILOT_USER = "copilot_user"
LEARN_COPILOT_MINT = "copilot_mint"
GITHUB_LEARN = {
    "/user": LEARN_GITHUB_USER,
    "/copilot_internal/user": LEARN_COPILOT_USER,
    "/copilot_internal/v2/token": LEARN_COPILOT_MINT,
}
"""``api.github.com``'s answers that name a Copilot user (best effort, not measured); everything
else on that host passes unread."""


@dataclass
class Pending:
    """A forwarded model request, recorded when its answer ends."""

    gw: m.Application
    person: GatewayPerson
    provider: str
    model: str
    surface: str
    size: int
    t0: float
    usage: bool = False


@dataclass
class Decision:
    """What to do with a request: forward it (maybe rewritten) or answer it."""

    verdict: Verdict | None = None
    pending: Pending | None = None
    adapter: str = ""


@dataclass
class Who:
    """A request's person and gateway, once resolved.

    An unidentified request has the default gateway (and a person named after its account), or
    no gateway at all when none is set.
    """

    gw: m.Application | None
    person: GatewayPerson | None
    identified: bool
    binding: Binding | None = None


@dataclass
class Settings:
    """The proxy's settings that the console may override."""

    allowed_orgs: dict[str, list[str]] = field(default_factory=dict)
    unidentified: str = "block"
    default_gateway: str | None = None
    """The default gateway's id, once resolved from its slug or id."""


class ProxyService:
    """The proxy's decisions, on the gateway's services."""

    def __init__(
        self,
        svc: Any,
        *,
        adapters: list[Adapter] | None = None,
        frames: list[FrameAdapter] | None = None,
    ) -> None:
        """Wire the proxy.

        Args:
            svc: Services (the gateway's, the guard's, the store).
            adapters: HTTP adapters (default: this build's, ``adapters.registry``).
            frames: WebSocket adapters.
        """
        self.svc = svc
        self.gws = svc.gateway
        s = svc.settings
        http, ws = registry()
        self.adapters = adapters if adapters is not None else http
        self.frames = frames if frames is not None else ws
        self.book = IdentityBook(svc.db)
        self.max_body = int(s.proxy_max_body_mb) * 1024 * 1024
        self.locale = s.proxy_locale
        self.contact = s.proxy_contact
        self.settings = Settings(
            allowed_orgs={k: list(v) for k, v in (s.proxy_allowed_orgs or {}).items()},
            unidentified=s.proxy_unidentified,
        )
        self.floor = m.Application(
            id="",
            slug="",
            name="",
            status="active",
            kind=GATEWAY,
            gateway={"providers": []},
            allowed_models=[],
            security=checks.initial_policy(),
            data_protection=None,
            residency=None,
            defaults={"locale": self.locale},
        )
        """Never stored: the policy (the system policy's floors) that masks a write of an account
        no gateway owns."""
        self.providers: list[str] = []
        """The catalogue providers served now (enabled on a gateway, with adapters)."""
        self.hosts: tuple[catalogue.Host, ...] = ()
        self._people: TtlCache[str, tuple[str, str, str, str | None] | None] = TtlCache(
            max_items=20_000, ttl_seconds=PERSON_TTL
        )
        self.clock = time.time
        self._tids: TtlCache[str, str] = TtlCache(max_items=20_000, ttl_seconds=3600)
        """The Copilot token ``tid`` last minted for a GitHub token (by its key's digest)."""

    # Settings and the catalogue

    async def refresh(self) -> None:
        """Read again the providers enabled on active gateways and the console setting.

        While the gateway module is off no provider is served: every host is refused.
        """
        from evokai.core import modules

        enabled: set[str] = set()
        gateways: list[m.Application] = []
        if await modules.is_enabled(self.svc, modules.GATEWAY):
            gateways = list(await self.svc.store.applications.all(GATEWAY))
            for app in gateways:
                if app.status == "active":
                    enabled.update((app.gateway or {}).get("providers") or [])
        self.providers = catalogue.served(enabled, providers_with_adapters())
        self.hosts = catalogue.intercepted(self.providers)
        stored = await self.svc.store.console_settings.get(SETTING)
        value = stored
        s = self.svc.settings
        orgs = s.proxy_allowed_orgs or {}
        mode = s.proxy_unidentified
        default = s.proxy_default_gateway
        if isinstance(value, dict):
            if isinstance(value.get("allowed_orgs"), dict):
                orgs = value["allowed_orgs"]
            if value.get("unidentified") in ("block", "record"):
                mode = value["unidentified"]
            if isinstance(value.get("default_gateway"), str):
                default = value["default_gateway"]
        default_id = None
        if default and default.strip():
            name = default.strip()
            hit = next((g for g in gateways if name in (g.id, g.slug)), None)
            if hit is None:
                log.warning("proxy_default_gateway_unknown")
            default_id = hit.id if hit is not None else None
        self.settings = Settings(
            allowed_orgs={str(k): [str(x) for x in v or []] for k, v in orgs.items()},
            unidentified=mode,
            default_gateway=default_id,
        )

    async def run_refresh(self) -> None:
        """Keep the settings, the system policy and the bindings current (a background task)."""
        last_purge = 0.0
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            try:
                await self.refresh()
                await policies.load_system(self.svc.store)
                if time.monotonic() - last_purge > 3600:
                    last_purge = time.monotonic()
                    await self.book.purge()
            except Exception as e:
                log.warning("proxy_refresh_failed", extra={"error_type": type(e).__name__})

    def connect(self, host: str, port: int) -> str | None:
        """What a ``CONNECT`` (or a plain request) to a host gets.

        Returns:
            ``intercept``, ``tunnel``, ``check`` or None (refused).
        """
        host = host.lower().rstrip(".")
        if host == catalogue.CHECK_HOST:
            return "check"
        if port != 443:
            return None
        if any(h.matches(host) for h in self.hosts):
            return "intercept"
        if catalogue.is_tunnelled(host):
            return "tunnel"
        return None

    def adapter(self, method: str, host: str, path: str) -> Adapter | None:
        """The adapter of a request on an intercepted host.

        Returns:
            The adapter, or None (the request passes unread: a read, a static asset, a host opened
            only to learn who signed in).
        """
        host = host.lower()
        if host in catalogue.IDENTITY_ONLY:
            return None
        return next((a for a in self.adapters if a.matches(method, host, path)), None)

    def frame_adapter(self, host: str, path: str) -> FrameAdapter | None:
        """The adapter of a WebSocket.

        Returns:
            The adapter, or None.
        """
        return next((a for a in self.frames if a.matches(host.lower(), path)), None)

    @staticmethod
    def learns(method: str, host: str, path: str) -> str | None:
        """Whether a request's answer names an account (it is then read whole, it is small).

        Returns:
            Which reader, or None.
        """
        if method == "POST" and host == SIGNIN_HOST and path == SIGNIN_PATH:
            return LEARN_SIGNIN
        if method != "GET":
            return None
        if host == claude_adapters.HOST and path == claude_adapters.ACCOUNT_PATH:
            return LEARN_CLAUDE
        if host == anthropic_adapters.HOST and path == anthropic_adapters.PROFILE_PATH:
            return LEARN_PROFILE
        if host == GITHUB_HOST:
            return GITHUB_LEARN.get(path)
        return None

    # Identity

    async def learn(self, kind: str, req: Request, status: int, body: bytes) -> None:
        """Bind the account an answer names (and a claude.ai session cookie) to its e-mail.

        The person is keyed on the Anthropic account uuid: every Claude Code request carries it,
        so an account learned from Desktop's ``/api/account`` is recognised on the CLI too. Never
        logs the answer (the token answer holds the tokens).
        """
        if status != 200 or not body:
            return
        try:
            data = Request("GET", "", "", [], body).json()
        except FormatUnknown:
            return
        if kind in (LEARN_GITHUB_USER, LEARN_COPILOT_USER, LEARN_COPILOT_MINT):
            await self._learn_github(kind, req, data)
            return
        keys: list[Key] = []
        if kind == LEARN_CLAUDE:
            email, account, org = from_claude_account(data)
            if session := claude_ai_key(req.all("cookie"), None):
                keys.append(session)
        elif kind == LEARN_SIGNIN:
            email, account, org = from_token_response(data)
        else:
            email, account, org = from_oauth_profile(data)
        if account:
            keys.append(Key("anthropic", "account", account))
        if not keys or not email:
            return
        for key in keys:
            try:
                b = await self.book.bind(key, email, account, org)
            except Exception as e:
                log.warning("proxy_identity_not_bound", extra={"error_type": type(e).__name__})
                return
            self.book.seen(req.client, b, self.clock())
        log.info("proxy_identity_learned", extra={"kind": kind})

    async def _learn_github(self, kind: str, req: Request, data: Any) -> None:
        """Bind a GitHub token to its account, and the Copilot token it minted to the same one.

        Never logs the answer (the token answer holds the Copilot token).
        """
        gh = github_key(req.header("authorization"))
        if gh is None:
            return
        try:
            if kind == LEARN_COPILOT_MINT:
                tid = from_copilot_token(data)
                if not tid:
                    return
                self._tids.set(gh.digest, tid)
                b = await self.book.lookup(gh)
                if b is not None and (b.email or b.account_id):
                    tb = await self.book.bind(
                        Key("github", "tid", tid), b.email, b.account_id, b.org_id
                    )
                    self.book.seen(req.client, tb, self.clock())
                return
            tracking = None
            if kind == LEARN_COPILOT_USER:
                email, login, orgs, tracking = from_copilot_user(data)
            else:
                email, login = from_github_user(data)
                orgs = None
            if not email and not login:
                return
            b = await self.book.bind(gh, email, login, orgs)
            self.book.seen(req.client, b, self.clock())
            for tid in {t for t in (tracking, self._tids.get(gh.digest)) if t}:
                await self.book.bind(Key("github", "tid", tid), b.email, b.account_id, b.org_id)
        except Exception as e:
            log.warning("proxy_identity_not_bound", extra={"error_type": type(e).__name__})
            return
        log.info("proxy_identity_learned", extra={"kind": kind})

    async def _person(self, email: str | None) -> tuple[m.Application, GatewayPerson] | None:
        """The gateway person of an e-mail.

        Returns:
            (gateway, person), or None when no live key (or more than one) has it.
        """
        if not email:
            return None
        hit = self._people.get(email)
        if hit is None and email not in self._people._items:
            rows = await self.svc.store.applications.gateway_keys_by_email(email)
            row = rows[0] if len(rows) == 1 else None
            hit = (row.application_id, row.id, row.label, row.email) if row else None
            self._people.set(email, hit)
        if hit is None:
            return None
        gw = await self.gws.gateway(hit[0])
        if gw is None:
            return None
        return gw, GatewayPerson(
            application_id=gw.id, key_id=hit[1], label=hit[2], email=hit[3] or email
        )

    async def _unidentified(self, b: Binding | None, key: Key | None) -> Who:
        """An account no gateway person has: on the default gateway, when one is set.

        Returns:
            Who (without a gateway when no default gateway is set).
        """
        gw = None
        if self.settings.default_gateway:
            gw = await self.gws.gateway(self.settings.default_gateway)
        if gw is None:
            return Who(None, None, identified=False, binding=b)
        digest = (b.key_hash if b else key.digest if key else "")[:16]
        label = f"unidentified {b.email}" if b and b.email else "unidentified"
        person = GatewayPerson(
            application_id=gw.id,
            key_id=f"proxy:{digest}" if digest else "proxy:anonymous",
            label=label[:120],
            email=b.email if b else None,
        )
        return Who(gw, person, identified=False, binding=b)

    def texts(self, gw: m.Application | None) -> Texts:
        """The texts of a gateway (or the deployment's).

        Returns:
            The texts.
        """
        if gw is None or not gw.id:
            return Texts(None, locale=self.locale, contact=self.contact)
        return Texts(gw, contact=self.contact)

    async def _event(self, who: Who, control: str, rule: str, once: str, **details: Any) -> None:
        """A proxy event, once per subject and rule every few minutes, on the request's gateway.

        ``proxy.format_unknown``, ``proxy.org_refused`` or ``proxy.unidentified``. A request with
        no gateway (unidentified, no default gateway) is only logged: an event belongs to a gateway.
        """
        if who.gw is None:
            log.warning(control.replace(".", "_"), extra={"rule": rule, **details})
            return
        if not await self.svc.shared.set_once(
            f"proxy:event:{control}:{rule}:{once}", "1", EVENT_ONCE_SECONDS
        ):
            return
        label = who.person.label if who.person else ""
        if not who.identified and who.binding and who.binding.email:
            label = who.binding.email
        row = checks.gateway_event(who.gw.id, label, control, rule, details)
        self.gws._later(self.svc.guard.store_events([row]))

    async def _binding(
        self, req: Request, key: Key | None, claims: Claims | None
    ) -> Binding | None:
        """A key's binding; a request that states its account (a ChatGPT JWT) binds it when new.

        Returns:
            The binding, or None.
        """
        binding = await self.book.lookup(key) if key else None
        if key is not None and claims is not None and claims.email:
            stale = binding is None or (binding.email, binding.org_id) != (claims.email, claims.org)
            if stale:
                try:
                    binding = await self.book.bind(key, claims.email, claims.account, claims.org)
                except Exception as e:
                    log.warning("proxy_identity_not_bound", extra={"error_type": type(e).__name__})
        if binding is not None:
            self.book.seen(req.client, binding, self.clock())
        return binding

    async def _identify(self, req: Request, key: Key | None, claims: Claims | None = None) -> Who:
        """Who sends a request, from its key's binding.

        Returns:
            Who.
        """
        return await self._who(await self._binding(req, key, claims), key)

    def _org_refused(self, provider: str, key: Key | None, binding: Binding | None) -> str | None:
        """The organisation of a request when ``allowed_orgs`` does not list it for the provider.

        A GitHub account names its organisations joined by commas: one listed is enough.

        Returns:
            The organisation refused, or None.
        """
        org = (key.org if key else None) or (binding.org_id if binding else None)
        allowed = self.settings.allowed_orgs.get(provider) or []
        if not allowed or not org or set(org.split(",")) & set(allowed):
            return None
        return org

    # Requests

    def too_large(self, adapter: Adapter, req: Request) -> Answer:
        """The refusal of a body over ``EVOKAI_PROXY_MAX_BODY_MB`` (fail closed).

        Returns:
            The answer.
        """
        log.warning("proxy_body_too_large", extra={"adapter": adapter.name})
        return adapter.failed(req, self.texts(None).get("too_large", mb=self.max_body // 1048576))

    async def decide(self, req: Request, adapter: Adapter) -> Decision:
        """Check a request an adapter handles.

        Returns:
            The decision.
        """
        t0 = time.perf_counter()
        decision = Decision(adapter=adapter.name)
        key = adapter.key(req)
        who = await self._identify(req, key, adapter.claims(req))
        binding = who.binding
        texts = self.texts(who.gw)
        once = key.digest if key else req.client
        try:
            found = adapter.inspect(req)
        except FormatUnknown as e:
            await self._event(
                who, "proxy.format_unknown", adapter.name, once, why=e.why, host=req.host
            )
            log.warning("proxy_format_unknown", extra={"adapter": adapter.name, "why": e.why})
            decision.verdict = adapter.failed(
                req, texts.get("format_unknown", contact=texts.contact)
            )
            return decision
        if found is None:
            return decision
        # ``mask`` serves every provider's hosts: its checks and organisations are the host's.
        host_provider = adapter.provider or catalogue.provider_of(req.host) or ""
        provider = catalogue.GATEWAY_PROVIDER.get(host_provider, adapter.gateway_provider)
        # The organisation: stated by the request (claude.ai, a ChatGPT login), else by the
        # account's answers.
        if org := self._org_refused(host_provider, key, binding):
            await self._event(
                who,
                "proxy.org_refused",
                host_provider,
                once,
                org=org[:80],
                surface=found.surface,
            )
            decision.verdict = adapter.failed(req, texts.get("org_refused", contact=texts.contact))
            return decision
        if adapter.mask:
            decision.verdict = await self._mask(req, found, who)
            return decision
        if not who.identified and (self.settings.unidentified == "block" or who.gw is None):
            await self._event(
                who,
                "proxy.unidentified",
                "no_person" if binding and binding.email else "no_binding",
                once,
                provider=provider,
                surface=found.surface,
            )
            decision.verdict = adapter.failed(req, texts.get("unidentified", contact=texts.contact))
            return decision
        if who.gw is None or who.person is None:  # identified implies both; keeps the types honest
            return decision
        if who.gw.status != "active":
            decision.verdict = adapter.failed(
                req, texts.get("refusal_other", reason="gateway disabled", contact=texts.contact)
            )
            return decision
        size = len(req.body)
        checked = await self.gws._controls(
            who.gw,
            who.person,
            provider,
            found.model,
            found.data if isinstance(found.data, dict) else {},
            count=found.count,
            text=found.text,
            check_model=found.check_model,
        )
        checked.fingerprint = (
            found.fingerprint
            or hashlib.sha256(found.text.encode("utf-8", "surrogatepass")).hexdigest()[:24]
        )
        if events := await self.gws._first_seen(who.person, checked):
            for e in events:
                e["details"]["surface"] = found.surface
            self.gws._later(self.svc.guard.store_events(events))
        if checked.block is not None:
            block = checked.block
            if found.parts:
                found.offending = (
                    self._offending(who.gw, found.parts, provider, who.person.own()) or None
                )
            verdict = adapter.refuse(req, found, texts, texts.refusal(block.control, found.model))
            decision.verdict = verdict
            if found.record:
                # Off the request's path, as every record: the database never delays a person.
                self.gws._later(
                    self.gws._record(
                        who.gw,
                        who.person,
                        provider,
                        found.model,
                        "blocked",
                        verdict.status if isinstance(verdict, Answer) else 200,
                        size,
                        t0,
                        control=block.control,
                        surface=found.surface,
                    )
                )
            log.info(
                "proxy_refused",
                extra={"adapter": adapter.name, "control": block.control, "surface": found.surface},
            )
            return decision
        if found.record:
            decision.pending = Pending(
                who.gw, who.person, provider, found.model, found.surface, size, t0, found.usage
            )
        return decision

    async def _who(self, binding: Binding | None, key: Key | None) -> Who:
        found = await self._person(binding.email if binding else None)
        if found is None and binding is not None and not binding.email:
            # A GitHub account without a public e-mail: a person labelled with its login.
            if binding.provider == "github" and binding.account_id:
                found = await self._person(binding.account_id)
        if found is None:
            return await self._unidentified(binding, key)
        return Who(found[0], found[1], identified=True, binding=binding)

    def _offending(
        self, gw: m.Application, parts: list[str], provider: str, own: tuple[str, ...] = ()
    ) -> set[int]:
        """The parts that a check refuses on their own (no events: those were recorded).

        Returns:
            Their indices.
        """
        policy = self.svc.guard.policy(gw)
        known = self.svc.guard.known_secrets
        return {
            i
            for i, part in enumerate(parts)
            if checks.text_checks(policy, part, provider=provider, known=known, own=own) is not None
        }

    async def _mask(self, req: Request, found: Inspection, who: Who) -> Verdict | None:
        """Mask what the policy enforces in a write, record what it only monitors.

        A write of an account no gateway owns is masked under the system policy's floors, and
        its findings are only logged.

        Returns:
            The rewritten request, or None (unchanged).
        """
        gw = who.gw or self.floor
        own = who.person.own() if who.person else ()
        policy = self.svc.guard.policy(gw)
        known = self.svc.guard.known_secrets
        s = policy.settings
        types = None
        if policy.on("pii.model"):
            from evokai.guard.pii import PII_TYPES

            types = {t for t in ((s.pii.types if s.pii else None) or PII_TYPES) if t in PII_TYPES}
        secrets_on = policy.on("input.secrets")
        if not secrets_on and not types:
            return None
        enforce_secrets = secrets_on and policy.enforced("input.secrets")
        enforce_pii = bool(types) and policy.enforced("pii.model")
        seen: list[str] = []

        def mask(text: str) -> str:
            # Everything the policy looks at is found; only what it enforces is masked.
            new, rules = scan.mask(
                text,
                secrets=enforce_secrets,
                known=known if enforce_secrets else (),
                pii_types=types if enforce_pii else None,
                allow=own,
            )
            seen.extend(rules)
            return new

        is_json = "json" in req.header("content-type").lower()
        new = claude_adapters.masked(found.text, is_json, mask)
        monitor_secrets = (
            secrets_on and not enforce_secrets and scan.find_secrets(found.text, known)
        )
        monitor_pii = (
            types
            and not enforce_pii
            and scan.find_pii(found.text, types, limit=checks.PII_LIMIT, allow=own)
        )
        secret_names = {r for r, _ in scan.SECRET_RULES} | {"deployment_secret"}
        secret_rules = sorted({r for r in seen if r in secret_names})
        pii_kinds = sorted({r for r in seen if r not in secret_names})
        label = who.person.label if who.person else ""
        events, record = checks.event_rows(gw.id, label, "", "")

        def add(control: str, rule: str, action: str, details: dict[str, Any]) -> None:
            record(control, rule, action, {**details, "surface": "mask", "host": req.host})

        if secret_rules:
            add("input.secrets", secret_rules[0], "redacted", {"rules": secret_rules})
        if monitor_secrets:
            add(
                "input.secrets",
                sorted(set(monitor_secrets))[0],
                "logged",
                {"count": len(monitor_secrets)},
            )
        if pii_kinds:
            add("pii.model", pii_kinds[0], "redacted", {"types": pii_kinds})
        if monitor_pii:
            add("pii.model", sorted(monitor_pii)[0], "logged", {"types": monitor_pii})
        for e in events:
            if e["action"] == "redacted":
                e["mode"] = "enforce"
        if events and not gw.id:
            log.info("proxy_masked_without_gateway", extra={"count": len(events)})
        elif events:
            once = hashlib.sha256(found.text.encode("utf-8", "surrogatepass")).hexdigest()[:24]
            subject = who.person.key_id if who.person else req.client
            if await self.svc.shared.set_once(
                f"proxy:mask:{subject}:{once}", "1", EVENT_ONCE_SECONDS
            ):
                self.gws._later(self.svc.guard.store_events(events))
        if new == found.text:
            return None
        return Rewrite(new.encode("utf-8"))

    def done(
        self,
        pending: Pending,
        status: int,
        usage: Usage | None,
        outcome: str,
        first_byte_ms: int | None,
    ) -> None:
        """Record a forwarded model request once its answer ended.

        Written off the path: the engine runs a connection's hooks one after the other, so an
        awaited write would hold the connection's next request.
        """
        self.gws._later(
            self.gws._record(
                pending.gw,
                pending.person,
                pending.provider,
                pending.model,
                outcome,
                status,
                pending.size,
                pending.t0,
                usage=usage,
                first_byte_ms=first_byte_ms,
                surface=pending.surface,
            )
        )

    def usage(self, pending: Pending, content_type: str, encoding: str) -> Usage | None:
        """A reader of the answer's token counts, when the surface's answers carry them.

        Returns:
            The reader, or None.
        """
        return Usage(pending.provider, content_type, encoding) if pending.usage else None

    # WebSocket messages

    async def frame(self, upgrade: Request, adapter: FrameAdapter, content: bytes) -> Frame:
        """Check a message from the app on a WebSocket an adapter reads.

        Args:
            upgrade: The request that opened the WebSocket (its headers and the PC's address).
            adapter: The adapter.
            content: The message.

        Returns:
            What to do with it (and, for a model request, what to record when it ends).
        """
        t0 = time.perf_counter()
        key = adapter.key(upgrade)
        binding = await self._binding(upgrade, key, adapter.claims(upgrade))
        if binding is None and adapter.by_address:
            # No session of its own (the bridge): the identity seen last from the same PC.
            recent = self.book.recent(upgrade.client, self.clock())
            binding = recent[0] if recent else None
        who = await self._who(binding, key)
        texts = self.texts(who.gw)
        once = key.digest if key else upgrade.client
        try:
            found = adapter.inspect(content)
        except FormatUnknown as e:
            await self._event(who, "proxy.format_unknown", adapter.name, once, why=e.why)
            log.warning("proxy_format_unknown", extra={"adapter": adapter.name, "why": e.why})
            return adapter.failed(
                content, None, texts, texts.get("format_unknown", contact=texts.contact)
            )
        if found is None:
            return Frame()
        provider = adapter.gateway_provider
        if org := self._org_refused(adapter.provider or provider, key, binding):
            await self._event(
                who,
                "proxy.org_refused",
                adapter.provider or provider,
                once,
                org=org[:80],
                surface=found.surface,
            )
            return adapter.failed(
                content, found, texts, texts.get("org_refused", contact=texts.contact)
            )
        if not who.identified and (self.settings.unidentified == "block" or who.gw is None):
            await self._event(
                who,
                "proxy.unidentified",
                "no_person" if binding and binding.email else "no_binding",
                once,
                provider=provider,
                surface=found.surface,
            )
            return adapter.failed(
                content, found, texts, texts.get("unidentified", contact=texts.contact)
            )
        if who.gw is None or who.person is None:
            return Frame()
        if who.gw.status != "active":
            return adapter.failed(
                content,
                found,
                texts,
                texts.get("refusal_other", reason="gateway disabled", contact=texts.contact),
            )
        checked = await self.gws._controls(
            who.gw,
            who.person,
            provider,
            found.model,
            found.data if isinstance(found.data, dict) else {},
            count=found.count,
            text=found.text,
            check_model=found.check_model,
        )
        checked.fingerprint = (
            found.fingerprint
            or hashlib.sha256(found.text.encode("utf-8", "surrogatepass")).hexdigest()[:24]
        )
        if events := await self.gws._first_seen(who.person, checked):
            for e in events:
                e["details"]["surface"] = adapter.event_surface or found.surface
            self.gws._later(self.svc.guard.store_events(events))
        size = len(content)
        if checked.block is None:
            if not found.record:
                return Frame()
            return Frame(
                pending=Pending(
                    who.gw, who.person, provider, found.model, found.surface, size, t0, found.usage
                )
            )
        block = checked.block
        if found.parts:
            found.offending = (
                self._offending(who.gw, found.parts, provider, who.person.own()) or None
            )
        notice = adapter.notice(texts, block.control, found.model)
        log.info("proxy_refused", extra={"adapter": adapter.name, "control": block.control})
        if found.record:
            self.gws._later(
                self.gws._record(
                    who.gw,
                    who.person,
                    provider,
                    found.model,
                    "blocked",
                    WS_STATUS,
                    size,
                    t0,
                    control=block.control,
                    surface=found.surface,
                )
            )
        return Frame(replace=adapter.refuse(content, found, texts, notice))

    # The check host

    async def check_page(self, req: Request, *, tls: bool) -> Answer:
        """``evokai.check``: does this PC go through the proxy, and as whom.

        Returns:
            A small HTML page (no-store).
        """
        it = self.locale.startswith("it")
        recent = self.book.recent(req.client, self.clock())
        lines = [
            "Questo PC passa dal proxy aziendale di Evokai."
            if it
            else "This PC goes through the company's Evokai proxy.",
            (
                "Certificato del proxy: attendibile (HTTPS)."
                if it
                else "Proxy certificate: trusted (HTTPS)."
            )
            if tls
            else (
                "Pagina aperta in HTTP: apri https://evokai.check per provare il certificato."
                if it
                else "Opened over HTTP: open https://evokai.check to test the certificate."
            ),
        ]
        if recent:
            for b in recent[:3]:
                gateway = await self.gateway_name(b)
                lines.append(
                    ("Identità: " if it else "Identity: ")
                    + (mask_email(b.email) or "?")
                    + (
                        f" (gateway: {gateway})"
                        if gateway
                        else (" (in nessun gateway)" if it else " (in no gateway)")
                    )
                )
        else:
            lines.append(
                "Identità: nessuna ancora (apri Claude o riavvialo)."
                if it
                else "Identity: none yet (open Claude, or restart it)."
            )
        lines.append(
            ("Provider serviti: " if it else "Providers served: ")
            + (", ".join(self.providers) or "-")
        )
        body = "".join(f"<p>{html.escape(x)}</p>" for x in lines)
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Evokai proxy</title></head>"
            "<body style='font-family:system-ui,sans-serif;max-width:40rem;margin:2rem auto;"
            f"padding:0 1rem'><h1>Evokai proxy</h1>{body}</body></html>"
        )
        return Answer(
            200,
            {"content-type": "text/html; charset=utf-8", "cache-control": "no-store"},
            page.encode(),
        )

    async def gateway_name(self, b: Binding | None) -> str | None:
        """The gateway a binding's person belongs to (for the check page).

        Returns:
            Its name, or None.
        """
        with contextlib.suppress(Exception):
            found = await self._person(b.email if b else None)
            if found:
                return found[0].name
        return None
