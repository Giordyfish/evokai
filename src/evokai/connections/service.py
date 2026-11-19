"""Connecting a person to a provider, and the tokens their tools use.

```
panel or application ── POST /v1/session/connections/{provider}/start ──▶ authorize URL
browser ── the provider's sign-in and consent ──▶ GET /v1/oauth/callback?code&state
Evokai ── code + PKCE verifier ──▶ the provider's token endpoint ──▶ tokens, encrypted, stored
tool call ── the person's access token (refreshed when it is about to expire) ──▶ the provider
```

- The **state** is random, single use and short-lived (ten minutes): it is kept server-side in the
  shared state (Redis with several instances) with the application, the subject, the provider,
  the session's conversation and the PKCE verifier, so the browser never holds the verifier and a
  callback cannot be replayed or bound to someone else.
- The client secret comes from the environment variable the connector names; it is never stored.
- Tokens are encrypted with the deployment's key (``EVOKAI_ENCRYPTION_KEY``). A refresh the
  provider refuses marks the connection ``expired``: the next tool call asks the person to connect
  again.
- «Disconnect», «revoke all» and the subject's erasure delete the tokens, and revoke them at the
  provider when it can (Google, GitHub, Linear, Notion, Slack; Microsoft has no per-token
  revocation, the deleted token simply ages out).
- A token without expiry and without a refresh token (a GitHub OAuth App, Slack without token
  rotation) stays usable until it is revoked or the provider refuses it: a 401 marks the
  connection ``expired`` (``ConnectionService.expired``) and the person connects again.

Every provider is data (``evokai.connections.providers``): the service has no provider of its own.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt

from evokai.connections.providers import PROVIDERS, Provider, provider_of_kind
from evokai.connections.store import ConnectionRepository
from evokai.connectors.base import secret
from evokai.core.errors import AppError, BadRequest, ConfigError, Forbidden, NotFound
from evokai.core.util import utcnow
from evokai.db import models as m
from evokai.grants import NotGranted, grants_of
from evokai.identity.sso import pkce

log = logging.getLogger("evokai.connections")

STATE_TTL = 600
"""Seconds a started connection waits for the callback."""
REFRESH_EARLY = timedelta(seconds=90)
"""Refresh an access token this long before it expires."""
CALLBACK_PATH = "/v1/oauth/callback"


class ProviderRefused(AppError):
    """The provider refused the code or the refresh."""

    status, code = 502, "connection"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _account(tokens: dict[str, Any]) -> str | None:
    """The account an ID token names (the token came straight from the provider over TLS).

    Returns:
        Its e-mail or user name, or None.
    """
    raw = tokens.get("id_token")
    if not raw:
        return None
    try:
        claims = jwt.decode(str(raw), options={"verify_signature": False})
    except jwt.PyJWTError:
        return None
    for key in ("email", "preferred_username", "upn", "name"):
        if claims.get(key):
            return str(claims[key])
    return None


def _expires(tokens: dict[str, Any]) -> datetime | None:
    try:
        seconds = int(tokens.get("expires_in") or 0)
    except (TypeError, ValueError):
        return None
    return utcnow() + timedelta(seconds=seconds) if seconds > 0 else None


def _granted(tokens: dict[str, Any]) -> list[str]:
    """The scopes a token response says were granted.

    Space or comma separated; Slack puts them under ``authed_user``.

    Returns:
        Scopes, or an empty list when the response names none.
    """
    raw = tokens.get("scope")
    if not raw and isinstance(tokens.get("authed_user"), dict):
        raw = tokens["authed_user"].get("scope")
    return [x for x in str(raw or "").replace(",", " ").split() if x]


def _dig(body: Any, path: str) -> str | None:
    for key in path.split("."):
        if not isinstance(body, dict):
            return None
        body = body.get(key)
    return str(body) if body not in (None, "") else None


@dataclass(frozen=True)
class AppConfig:
    """An application's OAuth app at a provider, the secret decrypted (never in a repr)."""

    client_id: str
    client_secret: str = field(repr=False)
    writes: bool = False
    families: tuple[str, ...] = ()
    tenant: str | None = None


def _client(provider: Provider, cfg: Any) -> tuple[str, str, str | None]:
    """Who Evokai is at the provider, from a connector's configuration or an OAuth app.

    A connector's secret variable that is not set raises ``ConfigError``.

    Returns:
        (client id, client secret, tenant).
    """
    if isinstance(cfg, AppConfig):
        return cfg.client_id, cfg.client_secret, None
    return (
        cfg.client_id,
        secret(cfg.client_secret_env, f"{provider.title} connection"),
        getattr(cfg, "tenant", None),
    )


class ProviderSetup:
    """A provider as an application configured it (through a delegated connector)."""

    source = "connector"

    def __init__(self, provider: Provider, server: m.McpServerRecord) -> None:
        """Read the connector's configuration.

        Args:
            provider: The provider.
            server: The MCP server record of the delegated connector.
        """
        from evokai.connectors import validate

        self.provider = provider
        self.server = server
        self.cfg: Any = None
        self.error: str | None = None
        try:
            self.cfg = validate(server.connector or "", server.config)
        except AppError as e:
            self.error = e.message

    @property
    def secret_set(self) -> bool:
        """Whether the client secret's environment variable has a value.

        Returns:
            True when it does.
        """
        import os

        return bool(self.cfg is not None and os.environ.get(self.cfg.client_secret_env))

    @property
    def tenant(self) -> str | None:
        """The Microsoft tenant (None for Google).

        Returns:
            The tenant.
        """
        return getattr(self.cfg, "tenant", None)

    @property
    def server_name(self) -> str | None:
        """The MCP server of the delegated connector.

        Returns:
            Its name.
        """
        return self.server.name

    def scopes(self) -> list[str]:
        """The scopes this configuration asks.

        Returns:
            Scopes.
        """
        if self.cfg is None:
            return list(self.provider.base_scopes)
        return self.provider.scopes(list(self.cfg.families), bool(self.cfg.writes))


class AppSetup:
    """A provider as an application configured it (its OAuth app, ``evokai_oauth_apps``)."""

    source = "application"
    server = None
    error: str | None = None

    def __init__(self, provider: Provider, cfg: AppConfig | None, error: str | None = None) -> None:
        """Keep the OAuth app.

        Args:
            provider: The provider.
            cfg: The app, its secret decrypted (None when it cannot be decrypted).
            error: Why it is not usable.
        """
        self.provider = provider
        self.cfg = cfg
        self.error = error

    @property
    def secret_set(self) -> bool:
        """Whether the client secret is stored.

        Returns:
            True when it is.
        """
        return bool(self.cfg is not None and self.cfg.client_secret)

    @property
    def tenant(self) -> str | None:
        """No tenant for these providers.

        Returns:
            None.
        """
        return None

    @property
    def server_name(self) -> str | None:
        """The harness integration that uses the connection (no MCP server configures it).

        Returns:
            Its capability id.
        """
        return self.provider.integration

    def scopes(self) -> list[str]:
        """The scopes this configuration asks.

        Returns:
            Scopes.
        """
        return self.provider.scopes([], bool(self.cfg is not None and self.cfg.writes))


class ConnectionService:
    """The authorization flow, the token vault and revocation."""

    def __init__(
        self, *, store: Any, keyring: Any, http: httpx.AsyncClient, shared: Any, settings: Any
    ) -> None:
        """Wire the service.

        Args:
            store: Repositories.
            keyring: Encrypts and decrypts tokens.
            http: The process's HTTP client.
            shared: State shared by the instances (pending flows).
            settings: Deployment settings (``public_url``).
        """
        self.store = store
        self.repo: ConnectionRepository = store.connections
        self.keyring = keyring
        self.http = http
        self.shared = shared
        self.settings = settings
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    # configuration

    async def setups(self, application_id: str) -> list[ProviderSetup | AppSetup]:
        """The providers an application configured, one per provider.

        The first connector of a connector provider, or the application's OAuth app.

        Returns:
            Setups.
        """
        out: dict[str, ProviderSetup | AppSetup] = {}
        for server in await self.store.mcp_servers.all(application_id):
            provider = provider_of_kind(server.connector)
            if provider is not None and provider.id not in out:
                out[provider.id] = ProviderSetup(provider, server)
        for app in await self.repo.apps(application_id):
            provider = PROVIDERS.get(app.provider)
            if provider is None or provider.configured_by != "application":
                continue
            try:
                value = self.keyring.decrypt(app.client_secret_encrypted)
            except Exception:
                value = ""
            if not value:
                out[provider.id] = AppSetup(
                    provider, None, "The client secret cannot be read: set it again."
                )
                continue
            out[provider.id] = AppSetup(
                provider,
                AppConfig(client_id=app.client_id, client_secret=value, writes=bool(app.writes)),
            )
        return list(out.values())

    async def setup(self, application_id: str, provider_id: str) -> ProviderSetup | AppSetup:
        """One provider's configuration.

        Returns:
            The setup.

        Raises:
            NotFound: The application has no connector for this provider.
        """
        for s in await self.setups(application_id):
            if s.provider.id == provider_id:
                return s
        raise NotFound(f"No {provider_id} connection is configured.", code="unknown_provider")

    def redirect_uri(self, fallback_base: str | None = None) -> str:
        """Where the provider sends the browser back (registered at the provider).

        Returns:
            ``{EVOKAI_PUBLIC_URL}/v1/oauth/callback`` (or the request's own base without one).

        Raises:
            ConfigError: No public URL and no request to take the base from.
        """
        base = self.settings.public_url or fallback_base
        if not base:
            raise ConfigError("Set EVOKAI_PUBLIC_URL to connect people to providers.")
        return f"{base.rstrip('/')}{CALLBACK_PATH}"

    # the flow

    async def start(
        self,
        app: m.Application,
        subject: str,
        provider_id: str,
        *,
        redirect_uri: str,
        conversation_id: str | None = None,
        return_to: str | None = None,
        via: str = "session",
    ) -> dict[str, Any]:
        """Start connecting a person: the provider's authorize URL, with a fresh state.

        Args:
            app: The application.
            subject: The person.
            provider_id: A provider id (``microsoft365``, ``google``, ``github``, ``linear``,
                ``notion``, ``slack``).
            redirect_uri: The callback URL (registered at the provider).
            conversation_id: The session's conversation (bound to the state).
            return_to: Where to send the browser after the callback (one of the application's
                origins), else a page that says it can be closed.
            via: Who started it (``session``, ``server``, ``teams``, ``slack``), for the logs.

        Returns:
            authorizeUrl, expiresAt (epoch seconds), provider.

        Raises:
            NotFound: An unknown provider.
            NotGranted: The provider is not granted to the application.
            Forbidden: The application's data protection profile excludes the provider.
            BadRequest: A ``return_to`` outside the application's origins.
            ConfigError: The connector's configuration is not usable.
        """
        if provider_id not in PROVIDERS:
            raise NotFound(f"Unknown provider {provider_id}.", code="unknown_provider")
        grants = await grants_of(self.store, app)
        if not grants.connection(provider_id):
            raise NotGranted("connection", f"{provider_id} connections are not granted.")
        from evokai.protection import CONNECTIONS, processor_verdict, protection

        verdict = processor_verdict(protection(app), CONNECTIONS.get(provider_id, provider_id))
        if not verdict.allowed:
            raise Forbidden(
                f"{provider_id} is outside the application's data protection profile.",
                code="data_protection",
            )
        s = await self.setup(app.id, provider_id)
        if s.cfg is None:
            raise ConfigError(f"The {provider_id} connector is not valid: {s.error}")
        if return_to:
            origin = urlparse(return_to)
            allowed = {o.rstrip("/") for o in app.allowed_origins or []}
            if f"{origin.scheme}://{origin.netloc}" not in allowed:
                raise BadRequest("returnTo must be on one of the application's origins.")
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce()
        expires = int(time.time()) + STATE_TTL
        flow = {
            "a": app.id,
            "s": subject,
            "p": provider_id,
            "v": verifier,
            "r": redirect_uri,
            "c": conversation_id,
            "t": return_to,
            "e": expires,
        }
        await self.shared.set(f"oauth:state:{state}", json.dumps(flow), STATE_TTL)
        p = s.provider
        query = {
            "client_id": s.cfg.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **p.extra_params(),
        }
        if s.scopes():
            query["scope"] = p.scope_separator.join(s.scopes())
        log.info("connection_started", extra={"provider": provider_id, "via": via})
        return {
            "provider": provider_id,
            "authorizeUrl": f"{p.authorize_url(s.tenant)}?{urlencode(query)}",
            "expiresAt": expires,
        }

    async def callback(self, state: str, code: str | None, error: str | None) -> dict[str, Any]:
        """Finish a connection: trade the code for tokens and keep them encrypted.

        Returns:
            provider, title, account, returnTo.

        Raises:
            BadRequest: An unknown, used or expired state, or the person declined.
            ProviderRefused: The provider refused the code.
            ConfigError: The connector's configuration is no longer valid.
        """
        raw = await self.shared.take(f"oauth:state:{state}") if state else None
        if not raw:
            raise BadRequest("This link has expired: start connecting again.", code="state")
        flow = json.loads(raw)
        if int(flow.get("e") or 0) < time.time():
            raise BadRequest("This link has expired: start connecting again.", code="state")
        provider = PROVIDERS[flow["p"]]
        if error or not code:
            raise BadRequest(
                f"{provider.title} was not connected ({(error or 'no code')[:60]}).",
                code="declined",
            )
        s = await self.setup(flow["a"], provider.id)
        if s.cfg is None:
            raise ConfigError(f"The {provider.id} connector is not valid: {s.error}")
        client_id, client_secret, tenant = _client(provider, s.cfg)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": flow["r"],
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": flow["v"],
        }
        tokens = await self._token_call(provider, tenant, form)
        if not tokens.get("access_token"):
            raise ProviderRefused(f"{provider.title} did not return a token.")
        granted = _granted(tokens) or s.scopes()
        account = _account(tokens) or await self._account_of(provider, str(tokens["access_token"]))
        await self.repo.put(
            flow["a"],
            flow["s"],
            provider.id,
            account=account,
            scopes=granted,
            access=self.keyring.encrypt(str(tokens["access_token"])),
            refresh=self.keyring.encrypt(str(tokens["refresh_token"]))
            if tokens.get("refresh_token")
            else None,
            expires_at=_expires(tokens),
        )
        log.info("connection_made", extra={"provider": provider.id})
        return {
            "provider": provider.id,
            "title": provider.title,
            "account": account,
            "returnTo": flow.get("t"),
        }

    async def _token_call(
        self, provider: Provider, tenant: str | None, form: dict[str, str]
    ) -> dict[str, Any]:
        """A request to the token endpoint.

        GitHub answers form-encoded unless JSON is asked; GitHub and Slack answer a refusal with
        200 and ``error`` (Slack ``ok: false``), so the body decides, not only the status.

        Returns:
            The token response.

        Raises:
            ProviderRefused: The provider could not be reached or refused.
        """
        if provider.resource:
            form = {**form, "resource": provider.resource}
        try:
            r = await self.http.post(
                provider.token_url(tenant),
                data=form,
                headers={"Accept": "application/json"},
                timeout=20,
            )
        except httpx.HTTPError as e:
            raise ProviderRefused(f"{provider.title} could not be reached.") from e
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if r.status_code != 200 or body.get("error") or body.get("ok") is False:
            err = str(body.get("error") or r.status_code)
            raise ProviderRefused(f"{provider.title} refused the request ({err[:60]}).")
        return body

    async def _account_of(self, provider: Provider, token: str) -> str | None:
        """The account a person connected, asked of the provider with their new token.

        Returns:
            The account (login, e-mail, user and workspace), or None when it cannot be told.
        """
        spec = provider.account
        if spec is None:
            return None
        try:
            r = await self.http.request(
                spec.method,
                spec.url,
                json=spec.json,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=10,
            )
            body = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        parts = [v for v in (_dig(body, p) for p in spec.paths) if v]
        return " @ ".join(parts) or None

    # the vault

    async def access_token(
        self, application_id: str, subject: str, provider_id: str, cfg: Any = None
    ) -> str | None:
        """A person's access token, refreshed when it is about to expire.

        Args:
            application_id: The application.
            subject: The person.
            provider_id: The provider.
            cfg: The client configuration (a connector's, or an ``AppConfig``); None reads the
                application's setup when a refresh needs it.

        Returns:
            The token, or None without a usable connection.
        """
        row = await self.repo.get(application_id, subject, provider_id)
        if row is None or row.status != "active":
            return None
        expires = _aware(row.expires_at)
        if expires is None or expires - REFRESH_EARLY > utcnow():
            if row.last_used_at is None or utcnow() - _aware(row.last_used_at) > timedelta(  # ty: ignore[unsupported-operator]
                minutes=5
            ):
                await self.repo.used(row.id)
            return self.keyring.decrypt(row.access_token_encrypted)
        key = (application_id, subject, provider_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            fresh = await self.repo.get(application_id, subject, provider_id)
            if fresh is None or fresh.status != "active":
                return None
            exp = _aware(fresh.expires_at)
            if exp is not None and exp - REFRESH_EARLY > utcnow():
                return self.keyring.decrypt(fresh.access_token_encrypted)
            if cfg is None:
                try:
                    cfg = (await self.setup(application_id, provider_id)).cfg
                except NotFound:
                    cfg = None
            if cfg is None:
                return None
            token = await self._refresh(fresh, cfg)
        if len(self._locks) > 10_000:
            self._locks.clear()
        return token

    async def _refresh(self, row: m.OAuthConnection, cfg: Any) -> str | None:
        provider = PROVIDERS[row.provider]
        refresh = self.keyring.decrypt(row.refresh_token_encrypted or "")
        if not refresh:
            await self.repo.mark(row.id, "expired")
            return None
        client_id, client_secret, tenant = _client(provider, cfg)
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        try:
            tokens = await self._token_call(provider, tenant, form)
        except ProviderRefused:
            log.info("connection_refresh_refused", extra={"provider": provider.id})
            await self.repo.mark(row.id, "expired")
            return None
        access = str(tokens.get("access_token") or "")
        if not access:
            await self.repo.mark(row.id, "expired")
            return None
        await self.repo.refreshed(
            row.id,
            access=self.keyring.encrypt(access),
            refresh=self.keyring.encrypt(str(tokens["refresh_token"]))
            if tokens.get("refresh_token")
            else None,
            expires_at=_expires(tokens),
            scopes=_granted(tokens) or None,
        )
        return access

    async def expired(self, application_id: str, subject: str, provider_id: str) -> None:
        """The provider refused a token (a 401): the person must connect again.

        For a token that never expires (a GitHub OAuth App, Slack without token rotation) this
        is the only way Evokai learns it was revoked at the provider.
        """
        row = await self.repo.get(application_id, subject, provider_id)
        if row is not None:
            await self.repo.mark(row.id, "expired")

    async def person(
        self, app: m.Application, subject: str | None, provider_id: str
    ) -> dict[str, Any] | None:
        """The signed-in person's connection to a provider, for a tool acting as them.

        What the harness integrations (``github``, ``linear``, ``notion``, ``slack``,
        ``google_workspace``) need to act as the person.

        Returns:
            ``{connected, token, title, server}`` (``token`` None while the person has not
            connected, or must connect again), or None when nobody is signed in or the
            application has no such provider configured and granted.
        """
        if not subject:
            return None
        grants = await grants_of(self.store, app)
        if not grants.connection(provider_id):
            return None
        try:
            s = await self.setup(app.id, provider_id)
        except NotFound:
            return None
        token = await self.access_token(app.id, subject, provider_id, s.cfg) if s.cfg else None
        return {
            "connected": bool(token),
            "token": token,
            "title": s.provider.title,
            "server": s.server_name,
        }

    async def _revoke_at_provider(self, row: m.OAuthConnection, cfg: Any = None) -> None:
        """Revoke a connection's token at the provider, the way that provider does it.

        A failure is logged and never stops the deletion: the tokens are gone from Evokai.
        """
        provider = PROVIDERS.get(row.provider)
        if provider is None or not provider.revoke_url:
            return
        access = self.keyring.decrypt(row.access_token_encrypted)
        token = self.keyring.decrypt(row.refresh_token_encrypted or "") or access
        if not token:
            return
        url, style = provider.revoke_url, provider.revoke_style
        try:
            if style == "bearer":
                await self.http.post(url, headers={"Authorization": f"Bearer {access}"}, timeout=10)
            elif style == "form":
                await self.http.post(url, data={"token": token}, timeout=10)
            else:
                if cfg is None:
                    try:
                        cfg = (await self.setup(row.application_id, provider.id)).cfg
                    except NotFound:
                        return
                if cfg is None:
                    return
                client_id, client_secret, _ = _client(provider, cfg)
                if style == "github_grant":
                    await self.http.request(
                        "DELETE",
                        url.format(client_id=client_id),
                        json={"access_token": access},
                        auth=(client_id, client_secret),
                        headers={"Accept": "application/vnd.github+json"},
                        timeout=10,
                    )
                else:
                    await self.http.post(
                        url,
                        data={
                            "token": token,
                            "client_id": client_id,
                            "client_secret": client_secret,
                        },
                        timeout=10,
                    )
        except (httpx.HTTPError, ConfigError) as e:
            log.info("connection_revoke_failed", extra={"error": type(e).__name__})

    async def disconnect(self, application_id: str, subject: str, provider_id: str) -> bool:
        """Delete a person's connection (and revoke it at the provider when it can be).

        Returns:
            Whether there was one.
        """
        row = await self.repo.get(application_id, subject, provider_id)
        if row is None:
            return False
        await self._revoke_at_provider(row)
        return await self.repo.delete(application_id, subject, provider_id) > 0

    async def revoke_all(self, application_id: str, provider_id: str) -> int:
        """Delete every connection of an application to a provider.

        Returns:
            How many were deleted.
        """
        rows = await self.repo.all_of_provider(application_id, provider_id)
        cfg = None
        if rows:
            try:
                cfg = (await self.setup(application_id, provider_id)).cfg
            except NotFound:
                cfg = None
        for row in rows:
            await self._revoke_at_provider(row, cfg)
        return await self.repo.delete_provider(application_id, provider_id)

    async def forget_subject(self, application_id: str, subject: str) -> int:
        """Erase a subject's connections (revoked at the provider when it can be).

        Returns:
            How many were deleted.
        """
        for row in await self.repo.of_subject(application_id, subject):
            await self._revoke_at_provider(row)
        return await self.repo.delete(application_id, subject)

    # the application's OAuth apps

    async def put_app(
        self,
        application_id: str,
        provider_id: str,
        *,
        client_id: str,
        client_secret: str | None,
        writes: bool,
    ) -> None:
        """Set the application's OAuth app at a provider (the secret encrypted; None keeps it).

        Raises:
            NotFound: An unknown provider.
            BadRequest: A provider a connector configures, or a new app without its secret.
        """
        p = PROVIDERS.get(provider_id)
        if p is None:
            raise NotFound(f"Unknown provider {provider_id}.", code="unknown_provider")
        if p.configured_by != "application":
            raise BadRequest(f"{p.title} is configured by its {p.connector} connector.")
        client_id = client_id.strip()
        if not client_id:
            raise BadRequest("clientId is required.")
        try:
            await self.repo.put_app(
                application_id,
                provider_id,
                client_id=client_id,
                secret=self.keyring.encrypt(client_secret) if client_secret else None,
                writes=writes,
            )
        except ValueError as e:
            raise BadRequest("A new OAuth app needs its client secret.") from e

    async def delete_app(self, application_id: str, provider_id: str) -> bool:
        """Remove the application's OAuth app at a provider.

        People's connections stay until revoked; without the app they are not used.

        Returns:
            Whether there was one.
        """
        return await self.repo.delete_app(application_id, provider_id) > 0

    # views

    async def view(self, app: m.Application, subject: str) -> list[dict[str, Any]]:
        """A person's connections, for the panel: every granted provider, connected or not.

        Returns:
            provider, title, status (``connected``, ``expired``, ``not_connected``), account,
            scopes, families, connectedAt, lastUsedAt.
        """
        grants = await grants_of(self.store, app)
        rows = {r.provider: r for r in await self.repo.of_subject(app.id, subject)}
        out = []
        for s in await self.setups(app.id):
            p = s.provider
            if not grants.connection(p.id):
                continue
            r = rows.get(p.id)
            status = (
                "not_connected"
                if r is None
                else ("connected" if r.status == "active" else "expired")
            )
            families = list(s.cfg.families) if s.cfg is not None else []
            out.append({
                "provider": p.id,
                "title": p.title,
                "status": status,
                "account": r.account if r else None,
                "scopes": list(r.scopes or []) if r else [],
                "families": [f for f in families if grants.connection(p.id, f)],
                "connectedAt": r.created_at.isoformat() if r else None,
                "lastUsedAt": r.last_used_at.isoformat() if r and r.last_used_at else None,
            })
        return out

    async def admin_view(self, app: m.Application, redirect_uri: str | None) -> dict[str, Any]:
        """The application's providers, for the console: configuration, status, counts.

        Returns:
            providers (with configuredBy, configured, valid, secretSet, granted, families,
            scopes, server, connected and expired counts; never a secret) and redirectUri.
        """
        grants = await grants_of(self.store, app)
        counts = await self.repo.counts(app.id)
        setups = {s.provider.id: s for s in await self.setups(app.id)}
        out = []
        for pid, p in PROVIDERS.items():
            s = setups.get(pid)
            c = counts.get(pid, {})
            cfg = s.cfg if s is not None else None
            out.append({
                "provider": pid,
                "title": p.title,
                "configuredBy": p.configured_by,
                "integration": p.integration,
                "configured": s is not None,
                "server": s.server.name if s is not None and s.server is not None else None,
                "valid": cfg is not None,
                "error": s.error if s else None,
                "secretEnv": getattr(cfg, "client_secret_env", None),
                "secretSet": bool(s and s.secret_set),
                "clientId": cfg.client_id if cfg is not None else None,
                "tenant": s.tenant if s else None,
                "writes": bool(cfg is not None and cfg.writes),
                "granted": grants.connection(pid),
                "families": [
                    {"family": f, "granted": grants.connection(pid, f)}
                    for f in (cfg.families if cfg is not None else [])
                ],
                "scopes": s.scopes() if s else list(p.base_scopes),
                "connected": int(c.get("active", 0)),
                "expired": int(c.get("expired", 0)),
            })
        return {"providers": out, "redirectUri": redirect_uri}
