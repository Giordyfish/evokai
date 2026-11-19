from __future__ import annotations

import httpx
import pytest
import respx

from evokai import (
    AsyncEvokaiServer,
    EvokaiAuthError,
    EvokaiConflictError,
    EvokaiConnectionError,
    EvokaiError,
    EvokaiNotFoundError,
    EvokaiRateLimitError,
    EvokaiServer,
    EvokaiServerError,
    EvokaiValidationError,
    is_expired_conversation,
)
from evokai.errors import error_from_status
from tests.support import ADMIN_KEY, BASE, SERVER_KEY


@pytest.mark.parametrize(
    ("status", "cls", "default_code"),
    [
        (400, EvokaiValidationError, "bad_request"),
        (401, EvokaiAuthError, "unauthorized"),
        (403, EvokaiAuthError, "forbidden"),
        (404, EvokaiNotFoundError, "not_found"),
        (409, EvokaiConflictError, "conflict"),
        (422, EvokaiValidationError, "bad_request"),
        (429, EvokaiRateLimitError, "too_many_requests"),
        (500, EvokaiServerError, "internal"),
        (502, EvokaiServerError, "bad_gateway"),
        (418, EvokaiError, "error"),
    ],
)
def test_status_maps_to_class_and_default_code(
    status: int, cls: type[EvokaiError], default_code: str
) -> None:
    err = error_from_status(status, None)
    assert type(err) is cls
    assert (err.status, err.code, err.message) == (status, default_code, f"HTTP {status}")


def test_service_body_gives_code_message_fields_and_body() -> None:
    body = {"error": "bad_request", "message": "Invalid request.", "fields": ["body.subject"]}
    err = error_from_status(400, body)
    assert (err.code, err.message, err.fields, err.body) == (
        "bad_request",
        "Invalid request.",
        ["body.subject"],
        body,
    )
    assert str(err) == "Invalid request."
    assert "EvokaiValidationError(status=400" in repr(err)


@pytest.mark.parametrize(
    ("status", "body", "code", "cls"),
    [
        (
            403,
            {"error": "model_not_granted", "message": "No."},
            "model_not_granted",
            EvokaiAuthError,
        ),
        (403, {"error": "channel", "message": "Server only."}, "channel", EvokaiAuthError),
        (429, {"error": "limit", "message": "Cap reached."}, "limit", EvokaiRateLimitError),
        (502, {"error": "invalid_output", "message": "Bad."}, "invalid_output", EvokaiServerError),
        (
            400,
            {"error": "not_granted", "message": "No.", "problems": ["model x"]},
            "not_granted",
            EvokaiValidationError,
        ),
    ],
)
def test_service_codes_reach_the_caller(
    router: respx.MockRouter, status: int, body: dict, code: str, cls: type[EvokaiError]
) -> None:
    router.post("/v1/agents/a/run").mock(return_value=httpx.Response(status, json=body))
    with EvokaiServer(BASE, SERVER_KEY) as c, pytest.raises(cls) as info:
        c.run("a", "x")
    assert info.value.code == code
    assert info.value.status == status
    assert info.value.body == body


def test_non_json_error_body(router: respx.MockRouter) -> None:
    router.get("/v1/agents").mock(return_value=httpx.Response(503, text="<html>down</html>"))
    with EvokaiServer(BASE, SERVER_KEY) as c, pytest.raises(EvokaiServerError) as info:
        c.agents()
    assert (info.value.code, info.value.body) == ("unavailable", None)


def test_retry_after_that_is_not_a_number(router: respx.MockRouter) -> None:
    router.get("/v1/agents").mock(
        return_value=httpx.Response(429, json={}, headers={"Retry-After": "Wed, 21 Oct"})
    )
    with EvokaiServer(BASE, SERVER_KEY) as c, pytest.raises(EvokaiRateLimitError) as info:
        c.agents()
    assert info.value.retry_after is None


def test_expired_conversation_helper() -> None:
    assert is_expired_conversation(error_from_status(409, {"error": "expired_conversation"}))
    assert not is_expired_conversation(error_from_status(409, {"error": "closed"}))
    assert not is_expired_conversation(ValueError("x"))


@pytest.mark.parametrize(
    ("exc", "code"),
    [(httpx.ConnectError("refused"), "network"), (httpx.ReadTimeout("slow"), "timeout")],
)
async def test_transport_failures(router: respx.MockRouter, exc: Exception, code: str) -> None:
    router.get("/v1/agents").mock(side_effect=exc)
    with EvokaiServer(BASE, SERVER_KEY) as c, pytest.raises(EvokaiConnectionError) as info:
        c.agents()
    assert (info.value.status, info.value.code) == (0, code)
    async with AsyncEvokaiServer(BASE, SERVER_KEY) as a:
        with pytest.raises(EvokaiConnectionError):
            await a.agents()


def test_invalid_json_success(router: respx.MockRouter) -> None:
    router.get("/v1/agents").mock(return_value=httpx.Response(200, text="{nope"))
    with EvokaiServer(BASE, SERVER_KEY) as c, pytest.raises(EvokaiError) as info:
        c.agents()
    assert info.value.code == "invalid_response"


async def test_admin_call_without_admin_key_never_leaves(router: respx.MockRouter) -> None:
    route = router.get("/v1/admin/application")
    with EvokaiServer(BASE, SERVER_KEY) as c, pytest.raises(EvokaiError) as info:
        c.admin.application.get()
    assert (info.value.status, info.value.code) == (0, "no_admin_key")
    async with AsyncEvokaiServer(BASE, SERVER_KEY) as a:
        with pytest.raises(EvokaiError):
            await a.admin.skills.list()
    assert route.call_count == 0


def test_admin_key_alone_serves_server_routes(router: respx.MockRouter) -> None:
    route = router.get("/v1/agents").mock(return_value=httpx.Response(200, json={"items": []}))
    with EvokaiServer(BASE, admin_key=ADMIN_KEY) as c:
        assert c.agents() == []
    assert route.calls.last.request.headers["authorization"] == f"Bearer {ADMIN_KEY}"


def test_constructor_needs_a_key_and_a_url() -> None:
    with pytest.raises(ValueError):
        EvokaiServer(BASE)
    with pytest.raises(ValueError):
        EvokaiServer("", SERVER_KEY)


def test_trailing_slash_in_base_url(router: respx.MockRouter) -> None:
    route = router.get("/v1/agents").mock(return_value=httpx.Response(200, json={"items": []}))
    with EvokaiServer(BASE + "/", SERVER_KEY) as c:
        c.agents()
    assert route.calls.last.request.url.raw_path == b"/agent/v1/agents"


def test_own_http_client_is_left_open() -> None:
    http = httpx.Client()
    EvokaiServer(BASE, SERVER_KEY, http_client=http).close()
    assert not http.is_closed
    http.close()
