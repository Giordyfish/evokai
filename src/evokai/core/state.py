"""Keys kept in a state directory, generated on the first start (the five-minute install).

With ``EVOKAI_STATE_DIR`` (a volume in Docker Compose), the token signing key, the encryption key
and the operator key are generated once into ``keys.json`` (mode 0600) and read back on every start.
Keys given in the environment always win, so a production deployment that injects them from a
secret store never touches the file. The operator key is printed once, when it is created.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

from evokai.core.config import Settings
from evokai.core.errors import ConfigError

KEYS_FILE = "keys.json"
OPERATOR_PREFIX = "evk_op_"


@dataclass(frozen=True)
class StateResult:
    """What applying the state directory did."""

    settings: Settings
    created: bool
    """The keys file was written now (first start, or a rotated operator key)."""
    operator_key: str | None
    """The operator key, only when it was created now (to print it once)."""


def new_operator_key() -> str:
    """A new deployment-wide operator key.

    Returns:
        ``evk_op_`` and 43 random URL-safe characters.
    """
    return OPERATOR_PREFIX + secrets.token_urlsafe(32)


def _write(path: Path, data: dict[str, str]) -> None:
    """Write the keys file readable by its owner only, atomically."""
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    tmp.replace(path)


def apply_state(settings: Settings, *, rotate_operator: bool = False) -> StateResult:
    """Fill missing keys from the state directory, generating the file on first use.

    Args:
        settings: Settings from the environment.
        rotate_operator: Replace the stored operator key with a new one (printed once).

    Returns:
        The settings with the keys, and whether anything was created.

    Raises:
        ConfigError: The directory is not writable, or the file is malformed.
    """
    if not settings.state_dir:
        return StateResult(settings, False, None)
    folder = Path(settings.state_dir)
    path = folder / KEYS_FILE
    try:
        folder.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = (
            json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        )
    except (OSError, ValueError) as e:
        raise ConfigError(f"state directory {folder}: {type(e).__name__}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: not a JSON object")
    created_key: str | None = None
    changed = False
    if not data.get("signingKey"):
        data["signingKey"] = secrets.token_urlsafe(48)
        changed = True
    if not data.get("encryptionKey"):
        data["encryptionKey"] = Fernet.generate_key().decode()
        changed = True
    if rotate_operator or not data.get("operatorKey"):
        created_key = data["operatorKey"] = new_operator_key()
        changed = True
    if changed:
        try:
            _write(path, data)
        except OSError as e:
            raise ConfigError(f"state directory {folder} is not writable") from e
    update = {
        "signing_key": settings.signing_key or data["signingKey"],
        "encryption_key": settings.encryption_key or data["encryptionKey"],
        "operator_key": settings.operator_key or data["operatorKey"],
    }
    # A key from the environment wins: the stored one was not used, so it is not announced.
    announced = created_key if not settings.operator_key else None
    return StateResult(settings.model_copy(update=update), changed, announced)


def banner(settings: Settings, operator_key: str | None) -> str:
    """The first-start message: where the console is and the operator key, shown once.

    Returns:
        Lines of text for the terminal.
    """
    url = (settings.public_url or f"http://localhost:{settings.port}{settings.base_path}").rstrip(
        "/"
    )
    rule = "=" * 72
    lines = [
        "",
        rule,
        "  Evokai is ready.",
        "",
        f"  Console:       {url}/admin/",
    ]
    if operator_key:
        lines += [
            f"  Operator key:  {operator_key}",
            "",
            "  This key is shown only now. Keep it in a password manager: it signs you in to",
            "  the console and creates applications. Lost it? Run",
            "  `evokai init --rotate-operator-key` in the service's container.",
        ]
    else:
        lines.append("  Sign in with the operator key printed at the first start.")
    lines += [rule, ""]
    return "\n".join(lines)
