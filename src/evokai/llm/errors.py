from __future__ import annotations

import re

from pydantic_ai.exceptions import (
    ModelConnectionError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelServerError,
    ModelUnavailableError,
)

_CAPACITY_TEXT = re.compile(
    r"ServiceUnavailable|Throttling|TooManyRequests|ModelNotReady|InternalServer"
    r"|unable to process|timed? ?out",
    re.I,
)


def is_capacity_error(e: BaseException) -> bool:
    """Whether an error means "try another model", not "the request is wrong".

    Returns:
        True for capacity, throttling, server and connection errors.
    """
    if isinstance(
        e,
        (
            ModelRateLimitError,
            ModelQuotaExceededError,
            ModelUnavailableError,
            ModelServerError,
            ModelConnectionError,
        ),
    ):
        return True
    status = getattr(e, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    return bool(_CAPACITY_TEXT.search(f"{type(e).__name__} {e}"))


_OVERFLOW_TEXT = re.compile(
    r"too long|too large for (?:the )?model|context.length|context.window|maximum context"
    r"|prompt is too|too many (?:input )?tokens|input token count|maximum number of tokens"
    r"|exceeds? the (?:maximum|max|limit) (?:number of )?(?:input )?tokens",
    re.I,
)


def is_context_overflow(e: BaseException) -> bool:
    """Whether a provider refused a request because its input does not fit the model's context.

    Bedrock ("Input is too long for requested model"), Anthropic ("prompt is too long"), OpenAI
    ("maximum context length", ``context_length_exceeded``), Mistral ("too large for model"),
    Gemini ("input token count ... exceeds the maximum number of tokens"). Never a capacity error:
    another model of the chain would not help, a shorter history would.

    Returns:
        True for a context overflow.
    """
    if is_capacity_error(e):
        return False
    status = getattr(e, "status_code", None)
    if isinstance(status, int) and status not in (400, 413, 422):
        return False
    body = getattr(e, "body", None)
    return bool(_OVERFLOW_TEXT.search(f"{e} {body or ''}"))
