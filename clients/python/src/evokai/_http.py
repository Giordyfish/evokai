"""HTTP plumbing shared by the sync and async clients.

Every API method builds a :class:`Call` (method, path, query, body, how to parse the answer); a
sync or async transport sends it with the right credential. Building calls once keeps the two
clients identical by construction.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, Callable, Generator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Generic, NoReturn, TypeVar
from urllib.parse import quote

import httpx

from evokai._version import __version__
from evokai.errors import EvokaiConnectionError, EvokaiError, error_from_response

T = TypeVar("T")

DEFAULT_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
"""Server runs and JSON turns wait for the model; streams get a ping every 10 seconds."""

USER_AGENT = f"evokai-python/{__version__}"


@dataclass(frozen=True)
class Call(Generic[T]):
    """One API call, independent of how it is sent."""

    method: str
    path: str
    parse: Callable[[Any], T]
    params: Mapping[str, Any] | None = None
    body: Any = None
    admin: bool = False
    """Sent with the admin key."""
    raw: bool = False
    """The answer is text (Markdown, HTML), handed to ``parse`` as it is."""


def seg(value: str) -> str:
    """Encode one path segment.

    Returns:
        The segment with every reserved character escaped.
    """
    return quote(str(value), safe="")


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def clean_params(params: Mapping[str, Any] | None) -> dict[str, str]:
    """Drop empty query parameters and render the others as strings.

    Returns:
        The query, without None or empty values.
    """
    if not params:
        return {}
    return {k: _param(v) for k, v in params.items() if v is not None and v != ""}


def _headers(credential: str | None, accept: str, has_body: bool) -> dict[str, str]:
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _network(e: httpx.TransportError) -> NoReturn:
    """Raise the transport failure as an ``EvokaiConnectionError``."""
    code = "timeout" if isinstance(e, httpx.TimeoutException) else "network"
    raise EvokaiConnectionError(0, code, str(e) or type(e).__name__) from e


def _refused(response: httpx.Response) -> NoReturn:
    """Raise an error response as the typed ``EvokaiError``."""
    raise error_from_response(response)


def _parse(call: Call[T], response: httpx.Response) -> T:
    if call.raw:
        return call.parse(response.text)
    if response.status_code == 204 or not response.content:
        return call.parse(None)
    try:
        data = response.json()
    except ValueError as e:
        raise EvokaiError(
            response.status_code, "invalid_response", "The service answered with invalid JSON."
        ) from e
    return call.parse(data)


class SyncTransport:
    """Sends calls with an ``httpx.Client`` (one of yours, or its own)."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.Client | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.client = http_client or httpx.Client(timeout=timeout or DEFAULT_TIMEOUT)
        self.owns_client = http_client is None

    def url(self, path: str) -> str:
        """An absolute URL of a service path.

        Returns:
            The base URL joined with the path.
        """
        return f"{self.base_url}/{path.lstrip('/')}"

    def send(self, call: Call[T], credential: str | None) -> T:
        """Send a call and parse its answer.

        Args:
            call: The call.
            credential: The bearer credential.

        Returns:
            The parsed answer.
        """
        try:
            response = self.client.request(
                call.method,
                self.url(call.path),
                params=clean_params(call.params),
                json=call.body,
                headers=_headers(credential, "application/json", call.body is not None),
            )
        except httpx.TransportError as e:
            _network(e)
        if response.is_error:
            _refused(response)
        return _parse(call, response)

    @contextlib.contextmanager
    def stream(
        self, path: str, body: Any, credential: str | None
    ) -> Generator[httpx.Response, None, None]:
        """POST a body and keep the response open for streaming.

        Args:
            path: The service path.
            body: The JSON body.
            credential: The bearer credential.

        Yields:
            The open response (2xx only).
        """
        request = self.client.build_request(
            "POST",
            self.url(path),
            json=body,
            headers=_headers(credential, "text/event-stream", True),
        )
        try:
            response = self.client.send(request, stream=True)
        except httpx.TransportError as e:
            _network(e)
        try:
            if response.is_error:
                response.read()
                _refused(response)
            yield response
        finally:
            response.close()

    def close(self) -> None:
        """Close the HTTP client, if this transport created it."""
        if self.owns_client:
            self.client.close()


class AsyncTransport:
    """Sends calls with an ``httpx.AsyncClient`` (one of yours, or its own)."""

    def __init__(
        self,
        base_url: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.client = http_client or httpx.AsyncClient(timeout=timeout or DEFAULT_TIMEOUT)
        self.owns_client = http_client is None

    def url(self, path: str) -> str:
        """An absolute URL of a service path.

        Returns:
            The base URL joined with the path.
        """
        return f"{self.base_url}/{path.lstrip('/')}"

    async def send(self, call: Call[T], credential: str | None) -> T:
        """Send a call and parse its answer.

        Args:
            call: The call.
            credential: The bearer credential.

        Returns:
            The parsed answer.
        """
        try:
            response = await self.client.request(
                call.method,
                self.url(call.path),
                params=clean_params(call.params),
                json=call.body,
                headers=_headers(credential, "application/json", call.body is not None),
            )
        except httpx.TransportError as e:
            _network(e)
        if response.is_error:
            _refused(response)
        return _parse(call, response)

    @contextlib.asynccontextmanager
    async def stream(
        self, path: str, body: Any, credential: str | None
    ) -> AsyncGenerator[httpx.Response, None]:
        """POST a body and keep the response open for streaming.

        Args:
            path: The service path.
            body: The JSON body.
            credential: The bearer credential.

        Yields:
            The open response (2xx only).
        """
        request = self.client.build_request(
            "POST",
            self.url(path),
            json=body,
            headers=_headers(credential, "text/event-stream", True),
        )
        try:
            response = await self.client.send(request, stream=True)
        except httpx.TransportError as e:
            _network(e)
        try:
            if response.is_error:
                await response.aread()
                _refused(response)
            yield response
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client, if this transport created it."""
        if self.owns_client:
            await self.client.aclose()
