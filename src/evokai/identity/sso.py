"""Single sign-on to the console with any OpenID Connect provider (authorization code with PKCE).

```
console  -> GET /v1/console/sso/start -> the provider
            (state, nonce and the S256 challenge's verifier in an encrypted cookie)
provider -> GET /v1/console/sso/callback?code&state -> the code traded with the verifier (and the
            client secret, when there is one) -> the ID token checked: signature (the provider's
            keys), issuer, audience, expiry, nonce -> e-mail allowed? MFA in amr? roles from claims
         -> the console cookie
```

Multi-factor authentication is the provider's: with ``require_mfa`` the ID token's ``amr`` must name
one of ``mfa_values`` (``mfa``, ``otp`` by default). Roles come from a claim (``groups`` by default)
or the e-mail, rule by rule, over application slugs or ``*`` (every application); the highest wins.
A person with no role is refused.

The configuration comes from the console (stored, the client secret encrypted) or from the
environment (``EVOKAI_SSO`` as JSON and ``EVOKAI_SSO_CLIENT_SECRET``); the console's wins.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from evokai.core.util import TtlCache
from evokai.identity.roles import highest

SETTING = "sso"
SIGNING_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
)
_metadata: TtlCache[str, dict[str, Any]] = TtlCache(50, 600)


class RoleRule(BaseModel):
    """A role given to people who have a claim value, or whose e-mail matches."""

    model_config = ConfigDict(extra="forbid")
    claim: str | None = Field(default=None, max_length=300)
    """A value of the role claim (a group name or id)."""
    email: str | None = Field(default=None, max_length=320)
    """An e-mail or a pattern (``*@example.com``)."""
    role: Literal["owner", "operator", "viewer"]
    applications: list[str] = Field(default_factory=lambda: ["*"])
    """Application slugs, or ``*`` for every application."""


class SsoConfig(BaseModel):
    """The identity provider of the console."""

    model_config = ConfigDict(extra="forbid")
    issuer: str = Field(min_length=8, max_length=500)
    """The provider's issuer URL (its ``/.well-known/openid-configuration`` is read)."""
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, max_length=2_000)
    """For confidential clients; public clients rely on PKCE alone."""
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])
    allowed_domains: list[str] = Field(default_factory=list)
    """E-mail domains that may sign in (empty: any, then the role rules decide)."""
    allowed_emails: list[str] = Field(default_factory=list)
    role_claim: str = Field(default="groups", max_length=100)
    roles: list[RoleRule] = Field(default_factory=list)
    require_mfa: bool = False
    mfa_values: list[str] = Field(default_factory=lambda: ["mfa", "otp"])
    """``amr`` values that count as multi-factor."""
    label: str = Field(default="Single sign-on", max_length=60)
    """The button of the sign-in page."""
    redirect_uri: str | None = Field(default=None, max_length=500)
    """Where the provider sends people back; default ``{EVOKAI_PUBLIC_URL}/v1/console/sso/callback``
    or the address the console was reached at."""

    @field_validator("issuer")
    @classmethod
    def _issuer(cls, v: str) -> str:
        parts = urlsplit(v)
        local = parts.hostname in {"localhost", "127.0.0.1"} or (parts.hostname or "").endswith(
            ".test"
        )
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("the issuer must be an https URL")
        return v.rstrip("/")

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        return v if "openid" in v else ["openid", *v]

    def public(self) -> dict[str, Any]:
        """The configuration without its secret.

        Returns:
            The fields, and hasClientSecret.
        """
        out = self.model_dump(mode="json", exclude={"client_secret"})
        out["hasClientSecret"] = bool(self.client_secret)
        return out


class SsoError(Exception):
    """A sign-in refused; the code is shown on the sign-in page."""

    def __init__(self, code: str, detail: str = "") -> None:
        """Create the error.

        Args:
            code: ``state``, ``provider``, ``token``, ``email_unverified``, ``not_allowed``,
                ``mfa_required``, ``no_role``, ``disabled``.
            detail: For the log only.
        """
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class Identity:
    """Who signed in, and their roles."""

    issuer: str
    sub: str
    email: str
    name: str
    roles: dict[str, str]
    amr: list[str]


def pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge.

    Returns:
        (verifier, challenge).
    """
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def metadata(http: httpx.AsyncClient, issuer: str) -> dict[str, Any]:
    """The provider's OpenID configuration (cached ten minutes).

    Returns:
        The discovery document.

    Raises:
        SsoError: Unreachable, or its issuer is not the configured one.
    """
    hit = _metadata.get(issuer)
    if hit is not None:
        return hit
    try:
        r = await http.get(f"{issuer}/.well-known/openid-configuration", timeout=10)
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SsoError("provider", type(e).__name__) from e
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise SsoError("provider", "issuer mismatch")
    _metadata.set(issuer, doc)
    return doc


async def _keys(http: httpx.AsyncClient, uri: str, *, fresh: bool = False) -> dict[str, Any]:
    hit = None if fresh else _metadata.get(f"jwks:{uri}")
    if hit is None:
        try:
            r = await http.get(uri, timeout=10)
            r.raise_for_status()
            hit = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise SsoError("provider", type(e).__name__) from e
        _metadata.set(f"jwks:{uri}", hit)
    return hit


async def authorization_url(
    http: httpx.AsyncClient,
    cfg: SsoConfig,
    redirect_uri: str,
    state: str,
    nonce: str,
    challenge: str,
) -> str:
    """Where to send the browser to sign in.

    Returns:
        The provider's authorization URL with the request.
    """
    doc = await metadata(http, cfg.issuer)
    query = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(cfg.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    endpoint = str(doc["authorization_endpoint"])
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(query)}"


async def exchange(
    http: httpx.AsyncClient, cfg: SsoConfig, code: str, verifier: str, redirect_uri: str
) -> str:
    """Trade the code for tokens.

    Returns:
        The ID token.

    Raises:
        SsoError: The provider refused the code.
    """
    doc = await metadata(http, cfg.issuer)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": cfg.client_id,
        "code_verifier": verifier,
    }
    auth = None
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if cfg.client_secret:
        if "client_secret_basic" in methods:
            auth = httpx.BasicAuth(cfg.client_id, cfg.client_secret)
        else:
            form["client_secret"] = cfg.client_secret
    try:
        r = await http.post(str(doc["token_endpoint"]), data=form, auth=auth, timeout=15)  # ty: ignore[invalid-argument-type]
    except httpx.HTTPError as e:
        raise SsoError("provider", type(e).__name__) from e
    if r.status_code != 200:
        raise SsoError("token", f"token endpoint {r.status_code}")
    token = (r.json() or {}).get("id_token")
    if not token:
        raise SsoError("token", "no id_token")
    return str(token)


async def verify(
    http: httpx.AsyncClient, cfg: SsoConfig, id_token: str, nonce: str
) -> dict[str, Any]:
    """Check an ID token: signature with the provider's keys, issuer, audience, expiry, nonce.

    Returns:
        Its claims.

    Raises:
        SsoError: Anything that does not check.
    """
    doc = await metadata(http, cfg.issuer)
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise SsoError("token", "malformed") from e
    offered = doc.get("id_token_signing_alg_values_supported") or ["RS256"]
    alg = header.get("alg")
    if alg not in SIGNING_ALGORITHMS or alg not in offered:
        raise SsoError("token", f"algorithm {alg}")
    key = await _signing_key(http, str(doc["jwks_uri"]), header.get("kid"), alg)
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[alg],
            audience=cfg.client_id,
            issuer=cfg.issuer,
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise SsoError("token", type(e).__name__) from e
    if claims.get("nonce") != nonce:
        raise SsoError("token", "nonce")
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != cfg.client_id:
        raise SsoError("token", "azp")
    return claims


async def _signing_key(http: httpx.AsyncClient, jwks_uri: str, kid: str | None, alg: str) -> Any:
    for fresh in (False, True):
        keys = (await _keys(http, jwks_uri, fresh=fresh)).get("keys") or []
        for k in keys:
            if (kid is None or k.get("kid") == kid) and k.get("use", "sig") == "sig":
                try:
                    return jwt.PyJWK(k, algorithm=alg).key
                except jwt.PyJWTError:
                    continue
    raise SsoError("token", "unknown key")


def identity(cfg: SsoConfig, claims: dict[str, Any]) -> Identity:
    """Who the claims describe and what they may do.

    Returns:
        The identity with its roles.

    Raises:
        SsoError: The e-mail is unverified or not allowed, MFA is missing, or no role applies.
    """
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if claims.get("email_verified") is False:
        raise SsoError("email_unverified")
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    if (cfg.allowed_domains or cfg.allowed_emails) and not (
        domain in {d.lower().lstrip("@") for d in cfg.allowed_domains}
        or email in {e.lower() for e in cfg.allowed_emails}
    ):
        raise SsoError("not_allowed", domain)
    amr = claims.get("amr") or []
    amr = [str(x) for x in amr] if isinstance(amr, list) else [str(amr)]
    if cfg.require_mfa and not set(amr) & set(cfg.mfa_values):
        raise SsoError("mfa_required")
    values = claims.get(cfg.role_claim) or []
    values = {str(x) for x in values} if isinstance(values, list) else {str(values)}
    roles: dict[str, str] = {}
    for rule in cfg.roles:
        hit = (rule.claim is not None and rule.claim in values) or (
            rule.email is not None
            and bool(email)
            and fnmatch.fnmatchcase(email, rule.email.lower())
        )
        if hit:
            for app in rule.applications:
                roles[app] = highest(roles.get(app), rule.role) or rule.role
    if not roles:
        raise SsoError("no_role")
    name = str(claims.get("name") or claims.get("given_name") or email)[:200]
    return Identity(str(claims["iss"]), str(claims["sub"]), email, name, roles, amr)


async def configured(
    store: Any, settings: Any, keyring: Any
) -> tuple[SsoConfig | None, str | None]:
    """The sign-in provider: the console's setting, else the environment's.

    Returns:
        (config, source) where source is ``console`` or ``environment``; (None, None) when off.
    """
    stored = await store.console_settings.get(SETTING)
    if stored:
        raw = dict(stored)
        enc = raw.pop("client_secret_encrypted", None)
        raw["client_secret"] = keyring.decrypt(enc) if enc else None
        return SsoConfig.model_validate(raw), "console"
    env = getattr(settings, "sso", None)
    if env:
        raw = dict(env)
        if getattr(settings, "sso_client_secret", None):
            raw["client_secret"] = settings.sso_client_secret
        return SsoConfig.model_validate(raw), "environment"
    return None, None


def forget_metadata() -> None:
    """Drop the cached discovery documents and keys (after a configuration change)."""
    _metadata.clear()
