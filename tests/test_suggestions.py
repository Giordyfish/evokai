"""Starter questions come from the agent's definition, not from each client."""

from __future__ import annotations

from tests.conftest import ORIGIN, Env


async def test_the_token_and_the_session_carry_the_agent_suggestions(env: Env) -> None:
    tok = await env.token()
    assert tok["suggestions"] == ["How did the last month go?", "What is due this week?"]
    r = await env.client.get(
        "/agent/v1/session", headers={"Authorization": f"Bearer {tok['token']}", "Origin": ORIGIN}
    )
    assert r.status_code == 200, r.text
    assert r.json()["suggestions"] == tok["suggestions"]
