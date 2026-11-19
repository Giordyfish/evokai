"""Errors that reach clients.

Every one has a stable code and a safe message; internal text never leaks.
"""

from __future__ import annotations


class AppError(Exception):
    """An error with a stable code and a message that is safe to show."""

    status = 500
    code = "internal"
    message = "Something went wrong."

    def __init__(
        self, message: str | None = None, *, code: str | None = None, status: int | None = None
    ) -> None:
        """Override the default message, code or status.

        Args:
            message: Message for the client.
            code: Stable machine code.
            status: HTTP status.
        """
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code
        if status:
            self.status = status


class Unauthorized(AppError):
    """No valid credentials."""

    status, code, message = 401, "unauthorized", "Invalid session."


class Forbidden(AppError):
    """Credentials valid, operation not allowed."""

    status, code, message = 403, "forbidden", "Not allowed."


class NotFound(AppError):
    """Unknown or not owned by the caller."""

    status, code, message = 404, "not_found", "Non trovato."


class BadRequest(AppError):
    """Malformed request."""

    status, code, message = 400, "bad_request", "Invalid request."


class Conflict(AppError):
    """Conflicts with the current state."""

    status, code, message = 409, "conflict", "Operazione in conflitto con lo stato attuale."


class ConfigError(AppError):
    """Invalid configuration (agents, servers, keys)."""

    status, code, message = 500, "config", "Invalid agent configuration."
