"""Prompt modules in turns and server runs: rendered, pinned for the conversation, traced."""

from __future__ import annotations

from typing import Any

from evokai.db.repositories import Who
from tests.conftest import Env, system_text

AGENTS = "/agent/v1/admin/agents"
PROMPTS = "/agent/v1/admin/prompts"
TONE: dict[str, Any] = {
    "type": "object",
    "properties": {"style": {"type": "string", "enum": ["brief", "detailed"], "default": "brief"}},
}
PERSONA: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string", "maxLength": 40}},
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


async def _prompt(env: Env, name: str, text: str, schema: dict[str, Any]) -> int:
    r = await env.client.put(
        f"{PROMPTS}/{name}", json={"text": text, "parameters": schema}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    return r.json()["version"]


async def _agent(env: Env, name: str = "helper", **extra: Any) -> None:
    r = await env.client.get(AGENTS, headers=_adm(env))
    agent = next(x for x in r.json()["items"] if x["name"] == "helper")
    agent.pop("version")
    r = await env.client.put(
        f"{AGENTS}/{name}", json={**agent, "name": name, **extra}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text


async def _setup(env: Env) -> None:
    await _prompt(env, "tone", "Answer in a {{style}} way.", TONE)
    await _prompt(env, "persona", "Your name is {{name}}.", PERSONA)
    await _agent(
        env,
        modules=[
            {"name": "tone", "session": True},
            {"name": "persona", "on": False, "session": True},
        ],
    )


def _last_system(env: Env) -> str:
    return system_text(env.script.seen[-1][1])


async def test_turn_prompt_contains_session_module(env: Env) -> None:
    await _setup(env)
    tok = await env.token(
        modules={
            "tone": {"params": {"style": "detailed"}},
            "persona": {"on": True, "params": {"name": "Ada"}},
        }
    )
    await env.chat(tok["token"], text="Which poetry books do you have?")
    system = _last_system(env)
    assert "Answer in a detailed way.\n\nYour name is Ada." in system
    assert system.index("You are an assistant.") < system.index("Answer in a detailed way.")
    # Without a choice: the agent's defaults (tone brief, persona off).
    tok = await env.token()
    await env.chat(tok["token"], text="Hello")
    system = _last_system(env)
    assert "Answer in a brief way." in system and "Your name is" not in system


async def test_module_version_pinned_for_the_conversation(env: Env) -> None:
    await _setup(env)
    tok = await env.token()
    await env.chat(tok["token"], text="Hello")
    assert "Answer in a brief way." in _last_system(env)
    sess = await env.svc.store.sessions.get(Who(env.app_id, "u1"), tok["conversationId"])
    assert sess is not None and sess.prompt_options is not None
    assert sess.prompt_options["modules"]["tone"]["version"] == 1
    assert await _prompt(env, "tone", "Answer {{style}}ly, version two.", TONE) == 2
    await env.chat(tok["token"], text="And now?")
    assert "Answer in a brief way." in _last_system(env)
    assert "version two" not in _last_system(env)
    # A new conversation takes the latest version.
    fresh = await env.token()
    await env.chat(fresh["token"], text="Hello")
    assert "Answer briefly, version two." in _last_system(env)
    # A later token with a choice keeps the pinned version.
    again = await env.token(
        conversationId=tok["conversationId"], modules={"tone": {"params": {"style": "detailed"}}}
    )
    await env.chat(again["token"], text="Once more")
    assert "Answer in a detailed way." in _last_system(env)


async def test_session_locale_drives_speech_rules(env: Env) -> None:
    env.script.add("function:primary", ["Ci sono 1.000 libri."], ["There are 1,000 books."])
    tok = await env.token(locale="it-IT")
    events = await env.chat(tok["token"], text="Quanti libri?", mode="voice")
    say = next(e for e in events if e["type"] == "say")
    assert say["spoken"] == "Ci sono mille libri."
    tok = await env.token()
    events = await env.chat(tok["token"], text="How many books?", mode="voice")
    say = next(e for e in events if e["type"] == "say")
    assert "spoken" not in say  # English: no Italian spelling


async def test_trace_lists_modules_without_params(env: Env) -> None:
    await _setup(env)
    tok = await env.token(modules={"persona": {"on": True, "params": {"name": "Ada Lovelace"}}})
    await env.chat(tok["token"], text="Hello")
    r = await env.client.get("/agent/v1/admin/traces", headers=_adm(env))
    trace_id = r.json()["items"][0]["id"]
    r = await env.client.get(f"/agent/v1/admin/traces/{trace_id}", headers=_adm(env))
    root = r.json()["spans"][0]
    assert root["attrs"]["prompt.modules"] == ["tone@1", "persona@1"]
    assert "Ada Lovelace" not in r.text


async def test_server_run_accepts_modules(env: Env) -> None:
    await _prompt(env, "tone", "Answer in a {{style}} way.", TONE)
    await _agent(
        env,
        "clerk",
        channels=["server"],
        modules=[{"name": "tone", "session": True}],
    )
    run = {
        "input": "List new crime novels.",
        "modules": {"tone": {"params": {"style": "detailed"}}},
    }
    srv = {"Authorization": f"Bearer {env.server_key}"}
    r = await env.client.post("/agent/v1/agents/clerk/run", json=run, headers=srv)
    assert r.status_code == 200, r.text
    assert "Answer in a detailed way." in _last_system(env)
    r = await env.client.post(
        "/agent/v1/agents/clerk/run", json={"input": "Again.", "locale": "it"}, headers=srv
    )
    assert r.status_code == 200, r.text
    assert "Answer in a brief way." in _last_system(env)
    bad = {"input": "x", "modules": {"tone": {"params": {"style": "chatty"}}}}
    r = await env.client.post("/agent/v1/agents/clerk/run", json=bad, headers=srv)
    assert r.status_code == 400 and r.json()["error"] == "module_params", r.text
    r = await env.client.post(
        "/agent/v1/agents/clerk/run", json={"input": "x", "locale": "ja"}, headers=srv
    )
    assert r.status_code == 400 and r.json()["error"] == "unknown_locale", r.text
