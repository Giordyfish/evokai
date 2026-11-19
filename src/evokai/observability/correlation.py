"""Correlation id: one per request (or socket), carried across awaits by a context variable."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

correlation_id_context: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def generate_correlation_id() -> str:
    """Generate a new correlation id.

    Returns:
        A UUID4 string.
    """
    return str(uuid.uuid4())


def get_correlation_id() -> str | None:
    """The current correlation id.

    Returns:
        The id, or None outside a request.
    """
    return correlation_id_context.get()


def set_correlation_id(correlation_id: str) -> None:
    """Set the current correlation id."""
    correlation_id_context.set(correlation_id)
