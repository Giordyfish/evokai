"""Compliance packs generated from the live configuration: EU AI Act, GDPR and a control mapping.

``facts`` reads the configuration and activity counts (never personal data), ``packs`` writes the
three packs from them in English or Italian with every statement's sources, ``render`` turns a
pack into Markdown or printable HTML, ``store`` keeps the per-application profile and the
snapshots, ``diff`` compares two snapshots. A support for the controller's assessment, not legal
advice.
"""

from __future__ import annotations

from evokai.compliance.packs import PACKS, build
from evokai.compliance.service import current_facts, take_snapshot

__all__ = ["PACKS", "build", "current_facts", "take_snapshot"]
