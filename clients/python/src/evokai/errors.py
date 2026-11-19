"""Errors the client raises: one hierarchy, with the HTTP status and the service's stable code.

The service answers every failure with ``{"error": "<code>", "message": "<safe text>"}`` (plus
``fields`` for invalid requests and other keys for some codes, e.g. ``problems`` for
``not_granted``). The code is what programs should branch on; the message is safe to show.
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = [
    "EvokaiAuthError",
    "EvokaiConflictError",
    "EvokaiConnectionError",
    "EvokaiError",
    "EvokaiNotFoundError",
    "EvokaiRateLimitError",
    "EvokaiServerError",
    "EvokaiValidationError",
    "error_from_response",
    "error_from_status",
    "is_expired_conversation",
]

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "bad_request",
    429: "too_many_requests",
    500: "internal",
    502: "bad_gateway",
    503: "unavailable",
    504: "timeout",
}


class EvokaiError(Exception):
    """An error the service returned, or a transport failure (status 0, code ``network``).

    Attributes:
        status: HTTP status, 0 when the request never got an answer.
        code: The service's stable machine code (``unauthorized``, ``not_found``, ``limit``,
            ``channel``, ``model_not_granted``...), derived from the status when the body has none.
        message: A message that is safe to show.
        fields: Fields that failed validation, for ``bad_request``.
        body: The parsed JSON body, when there was one.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        fields: list[str] | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields
        self.body = body

    def __repr__(self) -> str:
        name = type(self).__name__
        return f"{name}(status={self.status}, code={self.code!r}, message={self.message!r})"


class EvokaiConnectionError(EvokaiError):
    """The request never got an answer (DNS, refused connection, timeout, broken stream)."""


class EvokaiAuthError(EvokaiError):
    """401 or 403: a missing, wrong or revoked credential, or an operation not allowed."""


class EvokaiNotFoundError(EvokaiError):
    """404: unknown, or not owned by the caller."""


class EvokaiConflictError(EvokaiError):
    """409: conflicts with the current state (a closed session, another storage mode...)."""


class EvokaiValidationError(EvokaiError):
    """400 or 422: the request was refused as invalid (see ``fields``)."""


class EvokaiRateLimitError(EvokaiError):
    """429: a spending cap (``limit``) or a request rate was reached.

    Attributes:
        retry_after: Seconds to wait, from the ``Retry-After`` header, when the service sent one.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        fields: list[str] | None = None,
        body: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(status, code, message, fields=fields, body=body)
        self.retry_after = retry_after


class EvokaiServerError(EvokaiError):
    """5xx: the service failed (``internal``), or no valid model answer (``invalid_output``)."""


def error_from_status(status: int, body: Any, *, retry_after: float | None = None) -> EvokaiError:
    """Build the typed error for a status and a parsed body.

    Args:
        status: HTTP status.
        body: The parsed JSON body, or None.
        retry_after: The ``Retry-After`` header in seconds, if any.

    Returns:
        The most specific error class for the status.
    """
    b: dict[str, Any] = body if isinstance(body, dict) else {}
    code = b.get("error") if isinstance(b.get("error"), str) else None
    code = code or _STATUS_CODES.get(status, "error")
    message = b.get("message") if isinstance(b.get("message"), str) else None
    message = message or f"HTTP {status}"
    raw_fields = b.get("fields")
    fields = [str(f) for f in raw_fields] if isinstance(raw_fields, list) else None
    if status == 429:
        return EvokaiRateLimitError(
            status, code, message, fields=fields, body=body, retry_after=retry_after
        )
    cls: type[EvokaiError] = EvokaiError
    if status in {401, 403}:
        cls = EvokaiAuthError
    elif status == 404:
        cls = EvokaiNotFoundError
    elif status == 409:
        cls = EvokaiConflictError
    elif status in {400, 422}:
        cls = EvokaiValidationError
    elif status >= 500:
        cls = EvokaiServerError
    return cls(status, code, message, fields=fields, body=body)


def error_from_response(response: httpx.Response) -> EvokaiError:
    """Read an error response (already read into memory) into a typed error.

    Args:
        response: The response with a 4xx or 5xx status.

    Returns:
        The typed error.
    """
    try:
        body = response.json() if response.content else None
    except ValueError:
        body = None
    retry_after: float | None = None
    header = response.headers.get("retry-after")
    if header:
        try:
            retry_after = float(header)
        except ValueError:
            retry_after = None
    return error_from_status(response.status_code, body, retry_after=retry_after)


def is_expired_conversation(error: BaseException) -> bool:
    """Whether the conversation passed its retention (start a new one).

    Returns:
        True for an ``expired_conversation`` error.
    """
    return isinstance(error, EvokaiError) and error.code == "expired_conversation"
