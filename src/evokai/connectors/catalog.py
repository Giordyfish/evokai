"""The connector kinds, their configurations and how a registered server becomes a connector."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from evokai.connectors.base import Connector, ConnectorConfig
from evokai.connectors.delegated import DelegatedConnector
from evokai.connectors.google_drive import GoogleDriveConnector
from evokai.connectors.google_workspace import GoogleWorkspaceConnector
from evokai.connectors.imap import ImapConnector
from evokai.connectors.microsoft_365 import Microsoft365Connector
from evokai.connectors.microsoft_files import MicrosoftFilesConnector
from evokai.connectors.openapi import OpenApiConnector
from evokai.connectors.sql import SqlConnector
from evokai.core.errors import BadRequest

KINDS: dict[str, type[Connector]] = {
    c.kind: c
    for c in (
        SqlConnector,
        OpenApiConnector,
        GoogleDriveConnector,
        MicrosoftFilesConnector,
        ImapConnector,
        Microsoft365Connector,
        GoogleWorkspaceConnector,
    )
}
URL_SCHEME = "connector://"
"""The URL a connector is registered under (``connector://<kind>``)."""


def validate(kind: str, config: dict[str, Any] | None) -> ConnectorConfig:
    """A connector's configuration, checked.

    Returns:
        The validated configuration.

    Raises:
        BadRequest: An unknown kind, a secret in clear, or an invalid configuration.
    """
    cls = KINDS.get(kind)
    if cls is None:
        raise BadRequest(
            f"Unknown connector {kind!r}: one of {', '.join(sorted(KINDS))}.", code="connector"
        )
    try:
        return cls.Config.model_validate(config or {})
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, x['loc'])) or kind}: {x['msg']}" for x in e.errors()[:6]
        )
        raise BadRequest(
            f"The {kind} connector's configuration is not valid: {problems}.", code="connector"
        ) from e


def build(
    name: str, kind: str, config: dict[str, Any] | None, http: httpx.AsyncClient | None = None
) -> Connector:
    """A connector, ready to list and call its tools.

    Returns:
        The connector.
    """
    cfg = validate(kind, config)
    if kind == OpenApiConnector.kind:
        return OpenApiConnector(name, cfg, http)
    if kind == GoogleDriveConnector.kind:
        return GoogleDriveConnector(name, cfg, http)
    if kind == MicrosoftFilesConnector.kind:
        return MicrosoftFilesConnector(name, cfg, http)
    if issubclass(KINDS[kind], DelegatedConnector):
        return KINDS[kind](name, cfg, http)  # ty: ignore[too-many-positional-arguments]
    return KINDS[kind](name, cfg)


def describe() -> list[dict[str, Any]]:
    """Every connector kind with its configuration schema (for the console and the CLI).

    Returns:
        kind, title, schema.
    """
    return [
        {"kind": k, "title": c.title, "schema": c.Config.model_json_schema()}
        for k, c in sorted(KINDS.items())
    ]
