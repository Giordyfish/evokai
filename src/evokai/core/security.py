"""Keys, session tokens and encryption.

- API keys: ``evk_<kind>_<prefix>_<secret>``. Only a SHA-256 of the secret is stored; the prefix
  finds the row. Keys are random (256 bits), so a plain hash is enough and comparison is
  constant-time.
- Session tokens: short-lived JWTs (HS256) an application's backend mints for one subject, one
  agent and one conversation; the browser uses them for SSE and the voice socket.
- Secrets at rest (MCP secrets, forwarded end-user credentials): Fernet.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken

from evokai.core.config import Settings
from evokai.core.errors import ConfigError, Unauthorized

log = logging.getLogger("evokai.security")

KEY_KINDS = ("srv", "adm")
ISSUER = "evokai"
WORKER_ISSUER = "evokai-worker"
"""The issuer of a capability worker's call token: never valid as a session token (another
issuer), valid only on ``/v1/workers/model``."""


@dataclass(frozen=True)
class NewKey:
    """A freshly generated key: the full value is shown once, only the hash is stored."""

    value: str
    kind: str
    prefix: str
    secret_hash: str


def hash_secret(secret: str) -> str:
    """Hash a key's secret part.

    Args:
        secret: The random part of the key.

    Returns:
        Hex SHA-256 of the secret.
    """
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_key(kind: str) -> NewKey:
    """Create a new API key.

    Args:
        kind: ``srv`` (server) or ``adm`` (admin).

    Returns:
        The key, to be shown once.

    Raises:
        ValueError: On an unknown kind.
    """
    if kind not in KEY_KINDS:
        raise ValueError(kind)
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return NewKey(f"evk_{kind}_{prefix}_{secret}", kind, prefix, hash_secret(secret))


GATEWAY_KEY = "gw"
"""The kind in a person's gateway key (``evk_gw_...``, ``evokai.gateway``). Stored with the kind
``gateway``; never a server or admin key, so ``parse_key`` does not read it."""
GATEWAY_KIND = "gateway"


def generate_gateway_key() -> NewKey:
    """Create a person's gateway key.

    Returns:
        The key (``evk_gw_<prefix>_<secret>``), to be shown once; its kind is ``gateway``.
    """
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    return NewKey(f"evk_{GATEWAY_KEY}_{prefix}_{secret}", GATEWAY_KIND, prefix, hash_secret(secret))


def parse_gateway_key(value: str) -> tuple[str, str] | None:
    """Split a gateway key into prefix and secret.

    Returns:
        (prefix, secret), or None if it is not a gateway key.
    """
    parts = value.strip().split("_", 3)
    if len(parts) != 4 or parts[0] != "evk" or parts[1] != GATEWAY_KEY or not parts[3]:
        return None
    return parts[2], parts[3]


def parse_key(value: str) -> tuple[str, str, str] | None:
    """Split a key into kind, prefix and secret.

    Args:
        value: The presented key.

    Returns:
        (kind, prefix, secret), or None if it is not an Evokai key.
    """
    parts = value.split("_", 3)
    if len(parts) != 4 or parts[0] != "evk" or parts[1] not in KEY_KINDS:
        return None
    return parts[1], parts[2], parts[3]


def secret_matches(secret: str, stored_hash: str) -> bool:
    """Constant-time check of a secret against its stored hash.

    Args:
        secret: The presented secret.
        stored_hash: The stored hash.

    Returns:
        Whether they match.
    """
    return hmac.compare_digest(hash_secret(secret), stored_hash)


def hash_code(code: str) -> str:
    """Hash a short code (kiosk exit) with a per-code salt; slow on purpose.

    Returns:
        ``scrypt$<salt hex>$<hash hex>``.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(code.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def code_matches(code: str, stored: str) -> bool:
    """Check a short code against its hash, in constant time.

    Returns:
        Whether it matches.
    """
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.scrypt(
        code.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1, dklen=32
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


@dataclass(frozen=True)
class SessionClaims:
    """What a session token grants: one subject, one agent, one conversation, for a few minutes."""

    application_id: str
    subject: str
    agent: str
    conversation_id: str
    token_id: str
    expires_at: int
    tags: list[str] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)
    """Optional per-subject caps the application chose: subject_daily_usd and
    subject_monthly_usd (USD), and caps (a list of caps in the application's currency)."""
    has_credential: bool = False
    model: str | None = None
    """Primary model chosen by the application, if the agent offers it."""
    voice: str | None = None
    """Voice override for this session (else the agent's, else the application's default)."""
    voice_engine: str = "browser"
    """Who does speech: ``browser`` (on-device STT/TTS in the client) or ``server`` (Pipecat)."""
    speech_tts: str | None = None
    """The server speech engine the token chose (else the deployment's ``voice_tts``)."""
    speech_stt: str | None = None
    """The server recognition engine the token chose (else the deployment's ``voice_stt``)."""
    storage_mode: str | None = None
    """Where the conversation lives (``evokai`` or ``external``); None = the application's mode."""
    issued_at: float = 0
    """When the token was minted (a revocation of the subject refuses tokens issued before it)."""
    groups: list[str] = field(default_factory=list)
    """The subject's groups or roles, as the application vouches for them: knowledge documents
    with access groups are found only when one of them matches."""


def _limits(raw: Any) -> dict[str, Any]:
    """A token's subject caps: the old USD fields as numbers, the new ones as a list.

    Returns:
        The caps.
    """
    out: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        if k == "caps":
            out[k] = [dict(c) for c in v or [] if isinstance(c, dict)]
        elif v is not None:
            out[k] = float(v)
    return out


class Keyring:
    """Signing and encryption keys of the process."""

    def __init__(self, settings: Settings) -> None:
        """Load keys; in dev, missing keys are generated for the process lifetime.

        Args:
            settings: Deployment settings.

        Raises:
            ConfigError: In production, when a key is missing or invalid.
        """
        if settings.production and (not settings.signing_key or not settings.encryption_key):
            raise ConfigError("EVOKAI_SIGNING_KEY and EVOKAI_ENCRYPTION_KEY are required")
        if not settings.signing_key or not settings.encryption_key:
            log.warning("ephemeral_keys", extra={"hint": "set EVOKAI_SIGNING_KEY/ENCRYPTION_KEY"})
        self.signing_key = settings.signing_key or secrets.token_urlsafe(48)
        try:
            self.fernet = Fernet(settings.encryption_key or Fernet.generate_key())
        except ValueError as e:
            raise ConfigError("EVOKAI_ENCRYPTION_KEY is not a valid Fernet key") from e
        self.default_ttl = settings.session_token_default_ttl
        self.max_ttl = settings.session_token_max_ttl

    def encrypt(self, text: str) -> str:
        """Encrypt a secret for storage.

        Args:
            text: Plain secret.

        Returns:
            Fernet token.
        """
        return self.fernet.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        """Decrypt a stored secret.

        Args:
            token: Fernet token.

        Returns:
            The plain secret, or None if it cannot be decrypted (rotated key, tampering).
        """
        try:
            return self.fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return None

    def mint(self, claims: dict[str, Any], ttl: int | None) -> tuple[str, int, str]:
        """Sign a session token.

        Args:
            claims: aud (application id), sub, agt, cid, tags, lim, crd, grp.
            ttl: Requested lifetime in seconds (capped by the deployment maximum).

        Returns:
            (token, expires_at epoch seconds, token id).
        """
        now = int(time.time())
        exp = now + max(30, min(ttl or self.default_ttl, self.max_ttl))
        jti = secrets.token_hex(16)
        # ims: the issue time in milliseconds, so a revocation separates tokens of the same second.
        ims = int(time.time() * 1000)
        payload = {**claims, "iss": ISSUER, "iat": now, "ims": ims, "exp": exp, "jti": jti}
        return jwt.encode(payload, self.signing_key, algorithm="HS256"), exp, jti

    def mint_worker(self, claims: dict[str, Any], ttl: int) -> tuple[str, str]:
        """Sign a capability worker's call token (one turn or run, a few minutes at most).

        Args:
            claims: aud (application), sub (the person), cid (conversation), agt, tags, tid (the
                turn or run), srv (the worker), mcl (model calls allowed).
            ttl: Lifetime in seconds (at most 600).

        Returns:
            (token, token id).
        """
        now = int(time.time())
        jti = secrets.token_hex(16)
        exp = now + max(10, min(ttl, 600))
        payload = {**claims, "iss": WORKER_ISSUER, "iat": now, "exp": exp, "jti": jti}
        return jwt.encode(payload, self.signing_key, algorithm="HS256"), jti

    def verify_worker(self, token: str) -> dict[str, Any]:
        """Verify a capability worker's call token.

        Returns:
            Its claims.

        Raises:
            Unauthorized: Expired, forged, malformed, or not a worker token (a session token or a
                key is refused).
        """
        try:
            return jwt.decode(
                token,
                self.signing_key,
                algorithms=["HS256"],
                issuer=WORKER_ISSUER,
                options={
                    "require": ["exp", "iat", "jti", "sub", "aud", "tid", "cid", "agt"],
                    "verify_aud": False,
                },
            )
        except jwt.PyJWTError as e:
            raise Unauthorized("Worker token expired or invalid.", code="token") from e

    def verify(self, token: str) -> SessionClaims:
        """Verify a session token.

        Args:
            token: The presented JWT.

        Returns:
            Its claims.

        Raises:
            Unauthorized: Expired, forged or malformed.
        """
        try:
            p = jwt.decode(
                token,
                self.signing_key,
                algorithms=["HS256"],
                issuer=ISSUER,
                options={"require": ["exp", "iat", "jti", "sub", "aud"], "verify_aud": False},
            )
        except jwt.PyJWTError as e:
            raise Unauthorized("Session expired or invalid.", code="token") from e
        return SessionClaims(
            application_id=str(p["aud"]),
            subject=str(p["sub"]),
            agent=str(p.get("agt", "")),
            conversation_id=str(p.get("cid", "")),
            token_id=str(p["jti"]),
            expires_at=int(p["exp"]),
            tags=[str(t) for t in p.get("tags") or []],
            limits=_limits(p.get("lim")),
            has_credential=bool(p.get("crd")),
            model=str(p["mdl"]) if p.get("mdl") else None,
            voice=str(p["voc"]) if p.get("voc") else None,
            voice_engine="server" if p.get("ven") == "server" else "browser",
            speech_tts=str(p["spt"]) if p.get("spt") else None,
            speech_stt=str(p["sps"]) if p.get("sps") else None,
            storage_mode=str(p["stm"]) if p.get("stm") in {"evokai", "external"} else None,
            issued_at=int(p["ims"]) / 1000 if p.get("ims") else float(p["iat"]),
            groups=[str(x) for x in p.get("grp") or []],
        )
