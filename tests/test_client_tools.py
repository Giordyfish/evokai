"""Custom client tools: declared by the agent, the session token or a server run; answered by the
client (``client_tool`` event, ``POST /v1/session/tool-results``)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from evokai.agents.definitions import AgentDef, ClientToolDef
from evokai.core.errors import BadRequest
from evokai.grants import Grants, check_value
from evokai.tools.custom import MAX_TOOLS, check_tools
from tests.conftest import Env

LIGHT = {
    "name": "kiosk_light",
    "description": "Turn the kiosk light on or off.",
    "parameters": {
        "type": "object",
        "properties": {"on": {"type": "boolean"}},
        "required": ["on"],
    },
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _grant(env: Env, *changes: tuple[str, str, Any]) -> None:
    r = await env.client.patch(
        "/agent/v1/admin/grants",
        json={"changes": [{"kind": k, "name": n, "value": v} for k, n, v in changes]},
        headers=_adm(env),
    )
    assert r.status_code == 200, r.text


async def _mint(env: Env, **body: Any) -> httpx.Response:
    payload = {"subject": "u1", "agent": "helper", **body}
    return await env.client.post(
        "/agent/v1/session-tokens",
        json=payload,
        headers={"Authorization": f"Bearer {env.server_key}"},
    )


# Definitions


@pytest.mark.parametrize(
    "change",
    [
        {"name": "Kiosk"},
        {"name": "k"},
        {"name": "x" * 49},
        {"name": "chart"},
        {"name": "session"},
        {"description": "d" * 1001},
        {"parameters": {"type": 3}},
        {"parameters": {"type": "object", "description": "p" * 17_000}},
    ],
)
def test_a_client_tool_definition_is_checked(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ClientToolDef.model_validate({**LIGHT, **change})


def test_a_valid_client_tool_and_its_limits() -> None:
    tool = ClientToolDef.model_validate({**LIGHT, "wait": True})
    assert tool.wait is True and tool.name == "kiosk_light"
    check_tools([tool])
    with pytest.raises(BadRequest, match="at most"):
        check_tools([
            ClientToolDef.model_validate({**LIGHT, "name": f"t_{i}"}) for i in range(MAX_TOOLS + 1)
        ])
    with pytest.raises(BadRequest, match="twice"):
        check_tools([tool, tool])


def test_an_agent_declares_custom_client_tools() -> None:
    agent = AgentDef.model_validate({
        "name": "a",
        "model": "function:x",
        "instructions": "x",
        "client_tools": {"custom": [LIGHT]},
    })
    assert [t.name for t in agent.client_tools.custom] == ["kiosk_light"]
    with pytest.raises(ValidationError):
        AgentDef.model_validate({
            "name": "a",
            "model": "function:x",
            "instructions": "x",
            "client_tools": {"custom": [LIGHT, LIGHT]},
        })


# Grants


def test_grants_of_custom_client_tools() -> None:
    g = Grants()
    assert g.client_tool("kiosk_light", static=True) is True
    assert g.client_tool("kiosk_light", static=False) is False
    g = Grants(overrides={("client", "session"): True})
    assert g.client_tool("kiosk_light", static=False) is True
    g = Grants(overrides={("client", "session"): True, ("client", "kiosk_*"): False})
    assert g.client_tool("kiosk_light", static=False) is False
    assert g.client_tool("kiosk_light", static=True) is False
    assert g.client_tool("other_tool", static=True) is True
    g = Grants(overrides={("client", "kiosk_*"): False, ("client", "kiosk_light"): True})
    assert g.client_tool("kiosk_light", static=True) is True  # an exact name wins
    assert check_value("client", "kiosk_*", True) is True
    assert check_value("client", "session", True) is True
    with pytest.raises(BadRequest):
        check_value("client", "BAD NAME", True)


# The session token


async def test_a_session_token_declares_tools_with_the_session_grant(env: Env) -> None:
    r = await _mint(env, clientTools=[LIGHT])
    assert (r.status_code, r.json()["error"]) == (403, "client_not_granted"), r.text
    await _grant(env, ("client", "session", True))
    r = await _mint(env, clientTools=[LIGHT])
    assert r.status_code == 200, r.text
    cid = r.json()["conversationId"]
    sess = await env.svc.store.sessions.get(_who(env), cid)
    assert sess is not None and sess.client_tools == [{**LIGHT, "wait": False}]
    # A continued conversation replaces them when sent, keeps them when not.
    r = await _mint(env, conversationId=cid)
    assert r.status_code == 200
    assert (await env.svc.store.sessions.get(_who(env), cid)).client_tools  # ty: ignore[possibly-missing-attribute]
    r = await _mint(env, conversationId=cid, clientTools=[])
    assert r.status_code == 200
    assert not (await env.svc.store.sessions.get(_who(env), cid)).client_tools  # ty: ignore[possibly-missing-attribute]
    await _grant(env, ("client", "kiosk_light", False))
    r = await _mint(env, clientTools=[LIGHT])
    assert (r.status_code, r.json()["error"]) == (403, "client_not_granted")


async def test_a_session_token_refuses_too_many_or_invalid_tools(env: Env) -> None:
    await _grant(env, ("client", "session", True))
    many = [{**LIGHT, "name": f"t_{i}"} for i in range(MAX_TOOLS + 1)]
    r = await _mint(env, clientTools=many)
    assert r.status_code == 400, r.text
    r = await _mint(env, clientTools=[{**LIGHT, "parameters": {"type": 3}}])
    assert r.status_code == 400, r.text


async def test_the_grants_view_lists_custom_client_tools(env: Env) -> None:
    await _grant(env, ("client", "kiosk_*", False))
    view = (await env.client.get("/agent/v1/admin/grants", headers=_adm(env))).json()
    items = next(g["items"] for g in view["groups"] if g["id"] == "clientCustom")
    by = {x["name"]: x for x in items}
    assert by["session"]["value"] is False and by["session"]["default"] is False
    assert by["kiosk_*"]["pattern"] is True and by["kiosk_*"]["value"] is False


def _who(env: Env) -> Any:
    from evokai.db.repositories import Who

    return Who(env.app_id, "u1")
