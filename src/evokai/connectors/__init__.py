"""Ready connectors: SQL, REST/OpenAPI, Google Drive, SharePoint and OneDrive, IMAP."""

from evokai.connectors.base import Connector
from evokai.connectors.catalog import KINDS, URL_SCHEME, build, describe, validate

__all__ = ["KINDS", "URL_SCHEME", "Connector", "build", "describe", "validate"]
