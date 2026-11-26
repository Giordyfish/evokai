"""The console assistant's conversations: each person's own, owner inspection, retention, notice."""

from __future__ import annotations

import re
import time
from datetime import timedelta

from evokai.assistant.bootstrap import SLUG, ensure_assistant
from evokai.assistant.delegation import mint_delegation
from evokai.core.util import utcnow
from evokai.db.repositories import Who
from evokai.identity import console
from evokai.identity.console import ConsoleLogin
from evokai.privacy import subject_key
from tests.conftest import Env

CONSOLE = {"X-Evokai-Console": "1"}
REASON = "Checking a change to the caps reported by the finance team"


async def _system(env: Env) -> str:
    await ensure_assistant(env.svc.store, env.svc.keyring, env.svc.settings)
    app = await env.svc.store.applications.by_slug(SLUG)
    assert app is not None
    return app.id


async def _user(env: Env, sub: str, roles: dict[str, str]) -> str:
    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", sub, f"{sub}@example.com", sub, roles
    )
    return user.id


def _sign_in(env: Env, user_id: str, sub: str, roles: dict[str, str]) -> None:
    login = ConsoleLogin(
        "sso",
        None,
        int(time.time()) + 3600,
        user_id=user_id,
        email=f"{sub}@example.com",
        roles=roles,
    )
    env.client.cookies.clear()
    env.client.cookies.set(console.COOKIE, console.issue(env.svc.keyring, login), path="/agent")


def _on(app_id: str) -> dict[str, str]:
    return {**CONSOLE, "X-Evokai-Application": app_id}


async def _seed(env: Env, app_id: str, subject: str, text: str) -> tuple[str, str]:
    """A conversation of a subject in the system app, with a message, a trace and their leftovers.

    Returns:
        The conversation id and the trace id.
    """
    who = Who(app_id, subject)
    sess, thread = await env.svc.store.sessions.create(who, "assistant", "v1", "t", [])
    await env.svc.store.messages.add(
        who, sess.id, thread.id, role="user", content=[{"type": "text", "text": text}], text=text
    )
    trace_id = f"tr-{sess.id[:20]}"
    await env.svc.store.traces.add({
        "id": trace_id,
        "application_id": app_id,
        "session_id": sess.id,
        "subject": subject,
        "subject_key": subject_key(env.svc.keyring.signing_key, app_id, subject)[:16],
        "agent_name": "assistant",
    })
    await env.svc.store.bug_reports.add(who, sess.id, text, {})
    await env.svc.store.security_events.add_many([
        {
            "application_id": app_id,
            "control": "input.injection",
            "rule": "r",
            "action": "logged",
            "mode": "monitor",
            "severity": "low",
            "agent_name": "assistant",
            "subject": subject,
            "session_id": sess.id,
            "snippet": text,
        }
    ])
    return sess.id, trace_id


async def _two_people(env: Env) -> dict[str, str]:
    app_id = await _system(env)
    roles_a, roles_b = {SLUG: "viewer"}, {SLUG: "operator"}
    alice = await _user(env, "alice", roles_a)
    bob = await _user(env, "bob", roles_b)
    a_conv, a_trace = await _seed(env, app_id, f"console:{alice}", "alice private words")
    b_conv, b_trace = await _seed(env, app_id, f"console:{bob}", "bob private words")
    return {
        "app": app_id,
        "alice": alice,
        "bob": bob,
        "a_conv": a_conv,
        "b_conv": b_conv,
        "a_trace": a_trace,
        "b_trace": b_trace,
    }


CONTENT_ROUTES = {
    ("GET", "/v1/admin/conversations"),
    ("GET", "/v1/admin/conversations/{conversation_id}"),
    ("POST", "/v1/admin/conversations/{conversation_id}/notes"),
    ("DELETE", "/v1/admin/conversations/{conversation_id}/notes/{note_id}"),
    ("GET", "/v1/admin/traces"),
    ("GET", "/v1/admin/traces/{trace_id}"),
    ("GET", "/v1/conversations"),
    ("GET", "/v1/conversations/{conversation_id}"),
    ("DELETE", "/v1/conversations/{conversation_id}"),
    ("POST", "/v1/privacy/export"),
    ("POST", "/v1/privacy/erase"),
    ("GET", "/v1/admin/bug-reports"),
    ("GET", "/v1/admin/security/events"),
    ("POST", "/v1/admin/assistant/conversations/{conversation_id}/inspect"),
}
"""Every route that returns or touches a conversation's content (checked against the schema)."""

SESSION_ROUTES = re.compile(r"^/v1/session/")
"""A session token is already one subject's: these routes never reach another person."""


async def test_each_login_sees_only_own_assistant_conversations(env: Env) -> None:
    ids = await _two_people(env)
    app_id = ids["app"]
    # The list of routes is complete: a new route that reads conversations must be added here.
    paths = (await env.client.get("/agent/openapi.json")).json()["paths"]
    found = {
        (method.upper(), path.removeprefix("/agent"))
        for path, ops in paths.items()
        for method in ops
        if re.search(r"conversation|trace|privacy/(export|erase)|bug-reports|security/events", path)
        and not SESSION_ROUTES.match(path.removeprefix("/agent"))
        and not path.endswith(("/version-stats", "/notes/{note_id}/x"))
    }
    assert found <= CONTENT_ROUTES, found - CONTENT_ROUTES

    _sign_in(env, ids["alice"], "alice", {SLUG: "viewer"})
    h = _on(app_id)
    mine, theirs = f"console:{ids['alice']}", f"console:{ids['bob']}"

    r = await env.client.get("/agent/v1/admin/conversations", headers=h)
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["items"]] == [ids["a_conv"]]
    r = await env.client.get("/agent/v1/admin/conversations", params={"subject": theirs}, headers=h)
    assert r.json()["items"] == []
    r = await env.client.get(f"/agent/v1/admin/conversations/{ids['b_conv']}", headers=h)
    assert r.status_code == 404
    r = await env.client.get(f"/agent/v1/admin/conversations/{ids['a_conv']}", headers=h)
    assert r.status_code == 200 and "alice private words" in r.text

    r = await env.client.get("/agent/v1/admin/traces", headers=h)
    assert [t["id"] for t in r.json()["items"]] == [ids["a_trace"]]
    r = await env.client.get("/agent/v1/admin/traces", params={"subject": theirs}, headers=h)
    assert r.json()["items"] == []
    r = await env.client.get(f"/agent/v1/admin/traces/{ids['b_trace']}", headers=h)
    assert r.status_code == 404

    r = await env.client.get("/agent/v1/conversations", params={"subject": theirs}, headers=h)
    assert r.status_code in (200, 403) and "bob" not in r.text.replace(ids["bob"], "")
    if r.status_code == 200:
        assert r.json()["items"] == []
    r = await env.client.get(
        f"/agent/v1/conversations/{ids['b_conv']}", params={"subject": theirs}, headers=h
    )
    assert r.status_code in (403, 404)
    r = await env.client.get(
        f"/agent/v1/conversations/{ids['a_conv']}", params={"subject": mine}, headers=h
    )
    assert r.status_code == 200 and "alice private words" in r.text

    r = await env.client.get("/agent/v1/admin/bug-reports", headers=h)
    assert [b["sessionId"] for b in r.json()["items"]] == [ids["a_conv"]]
    r = await env.client.get("/agent/v1/admin/security/events", headers=h)
    assert [e["conversationId"] for e in r.json()["items"]] == [ids["a_conv"]]

    # The operator: notes, export and erase never reach another person's conversation.
    _sign_in(env, ids["bob"], "bob", {SLUG: "operator"})
    # Notes are changes: refused while locked, and on another person's conversation once unlocked.
    await env.svc.store.applications.update(app_id, locked=False)
    r = await env.client.post(
        f"/agent/v1/admin/conversations/{ids['a_conv']}/notes", json={"note": "x"}, headers=h
    )
    assert r.status_code == 404, r.text
    r = await env.client.delete(
        f"/agent/v1/admin/conversations/{ids['a_conv']}/notes/n1", headers=h
    )
    assert r.status_code == 404, r.text
    await env.svc.store.applications.update(app_id, locked=True)
    r = await env.client.post("/agent/v1/privacy/export", json={"subject": mine}, headers=h)
    assert r.status_code == 403 and r.json()["error"] == "own_data", r.text
    r = await env.client.post("/agent/v1/privacy/export", json={"subject": theirs}, headers=h)
    assert r.status_code == 200 and "bob private words" in r.text
    assert "alice private words" not in r.text
    r = await env.client.post("/agent/v1/privacy/erase", json={"subject": mine}, headers=h)
    assert r.status_code == 403, r.text
    r = await env.client.post(
        f"/agent/v1/admin/assistant/conversations/{ids['a_conv']}/inspect",
        json={"reason": REASON},
        headers=h,
    )
    assert r.status_code == 403, r.text
    # Nothing of alice's reached bob on any of these routes.
    for url in ("/agent/v1/admin/conversations", "/agent/v1/admin/traces"):
        assert ids["a_conv"] not in (await env.client.get(url, headers=h)).text


async def test_admin_mcp_cannot_read_others_conversations(env: Env) -> None:
    ids = await _two_people(env)
    login = ConsoleLogin(
        "sso", None, int(time.time()) + 3600, user_id=ids["bob"], roles={SLUG: "operator"}
    )
    token = mint_delegation(env.svc.keyring, login, ids["app"], "operator")
    bearer = {"Authorization": f"Bearer {token}"}
    r = await env.client.get("/agent/v1/admin/conversations", headers=bearer)
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["items"]] == [ids["b_conv"]]
    r = await env.client.get("/agent/v1/admin/traces", headers=bearer)
    assert [t["id"] for t in r.json()["items"]] == [ids["b_trace"]]
    # An owner's delegation reads no more: the assistant never lists other people's conversations.
    owner = env.operator_login()
    token = mint_delegation(env.svc.keyring, owner, ids["app"], "owner")
    r = await env.client.get(
        "/agent/v1/admin/conversations", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json()["items"] == []
    # The MCP tools are the same routes: their allow-list holds lists only, never an inspection.
    from evokai.mcp_admin.tools import ADMIN_TOOLS

    paths = {t.path for t in ADMIN_TOOLS}
    assert "/v1/admin/conversations/{conversation_id}" not in paths
    assert not any("inspect" in p for p in paths)


async def test_inspection_requires_owner_and_reason(env: Env) -> None:
    ids = await _two_people(env)
    url = f"/agent/v1/admin/assistant/conversations/{ids['a_conv']}/inspect"
    h = _on(ids["app"])
    _sign_in(env, ids["bob"], "bob", {SLUG: "operator"})
    assert (await env.client.post(url, json={"reason": REASON}, headers=h)).status_code == 403
    owner = await _user(env, "olga", {SLUG: "owner"})
    _sign_in(env, owner, "olga", {SLUG: "owner"})
    for bad in (
        {},
        {"reason": ""},
        {"reason": "too short"},
        {"reason": " " * 30},
        {"reason": "x" * 501},
    ):
        r = await env.client.post(url, json=bad, headers=h)
        assert r.status_code == 400, (bad, r.text)
    # The owner's own list shows other people's conversations without their content.
    r = await env.client.get(f"/agent/v1/admin/conversations/{ids['a_conv']}", headers=h)
    assert r.status_code == 403 and r.json()["error"] == "inspection_required", r.text
    r = await env.client.post(url, json={"reason": REASON}, headers=h)
    assert r.status_code == 200, r.text
    assert "alice private words" in r.text
    # The assistant acting for an owner cannot inspect.
    login = ConsoleLogin("sso", None, int(time.time()) + 3600, user_id=owner, roles={SLUG: "owner"})
    token = mint_delegation(env.svc.keyring, login, ids["app"], "owner")
    r = await env.client.post(
        url, json={"reason": REASON}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


async def test_inspection_is_audited_and_visible_to_author(env: Env) -> None:
    ids = await _two_people(env)
    owner = await _user(env, "olga", {SLUG: "owner"})
    _sign_in(env, owner, "olga", {SLUG: "owner"})
    url = f"/agent/v1/admin/assistant/conversations/{ids['a_conv']}/inspect"
    r = await env.client.post(url, json={"reason": REASON}, headers=_on(ids["app"]))
    assert r.status_code == 200, r.text
    entries = await env.svc.store.audit.query(ids["app"], action="assistant.conversation.inspect")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor == "sso:olga@example.com"
    assert (entry.after or {})["reason"] == REASON
    assert f"conversation:{ids['a_conv']}" in entry.target
    assert f"console:{ids['alice']}" not in str(entry.after) + entry.target
    # The author sees it; another person does not.
    _sign_in(env, ids["alice"], "alice", {SLUG: "viewer"})
    r = await env.client.get("/agent/v1/console/assistant/privacy", headers=_on(ids["app"]))
    assert r.status_code == 200, r.text
    seen = r.json()["inspections"]
    assert len(seen) == 1
    assert seen[0]["conversationId"] == ids["a_conv"] and seen[0]["reason"] == REASON
    assert seen[0]["by"] == "olga@example.com" and seen[0]["at"]
    _sign_in(env, ids["bob"], "bob", {SLUG: "operator"})
    r = await env.client.get("/agent/v1/console/assistant/privacy", headers=_on(ids["app"]))
    assert r.json()["inspections"] == []


async def test_system_app_ttl_capped_at_30_days(env: Env) -> None:
    import yaml

    from evokai.assistant.bootstrap import SHIPPED

    shipped = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))["application"]
    assert 1 <= shipped["conversation_ttl_days"] <= 30
    app_id = await _system(env)
    await env.svc.store.applications.update(app_id, locked=False)
    owner = await _user(env, "olga", {SLUG: "owner"})
    _sign_in(env, owner, "olga", {SLUG: "owner"})
    for bad in (31, 365, None):
        r = await env.client.patch(
            "/agent/v1/admin/application", json={"conversation_ttl_days": bad}, headers=_on(app_id)
        )
        assert r.status_code == 400 and r.json()["error"] == "system_ttl", (bad, r.text)
    r = await env.client.patch(
        "/agent/v1/admin/application", json={"conversation_ttl_days": 7}, headers=_on(app_id)
    )
    assert r.status_code == 200, r.text
    # Another application keeps its freedom.
    r = await env.client.patch(
        "/agent/v1/admin/application",
        json={"conversation_ttl_days": 365},
        headers={"Authorization": f"Bearer {env.admin_key}"},
    )
    assert r.status_code == 200, r.text


async def test_housekeeping_purges_assistant_conversations_after_ttl(env: Env) -> None:
    ids = await _two_people(env)
    app = await env.svc.store.applications.get(ids["app"])
    assert app is not None and app.conversation_ttl_days == 30
    deleted = await env.svc.store.sessions.purge_expired(now=utcnow() + timedelta(days=31))
    assert deleted == 2
    assert not await env.svc.store.sessions.exists(ids["a_conv"])
    # Within the time to live nothing goes.
    ids2 = await _seed(env, ids["app"], "console:x", "fresh")
    assert await env.svc.store.sessions.purge_expired(now=utcnow() + timedelta(days=29)) == 0
    assert await env.svc.store.sessions.exists(ids2[0])


async def test_author_cannot_delete_within_ttl(env: Env) -> None:
    ids = await _two_people(env)
    mine = f"console:{ids['alice']}"
    alice = {SLUG: "operator"}
    await env.svc.store.console_users.signed_in(
        "https://idp.example", "alice", "alice@example.com", "alice", alice
    )
    _sign_in(env, ids["alice"], "alice", alice)
    h = _on(ids["app"])
    r = await env.client.delete(
        f"/agent/v1/conversations/{ids['a_conv']}", params={"subject": mine}, headers=h
    )
    assert r.status_code == 409 and r.json()["error"] == "retention", r.text
    r = await env.client.post("/agent/v1/privacy/erase", json={"subject": mine}, headers=h)
    assert r.status_code in (403, 409), r.text
    assert await env.svc.store.sessions.exists(ids["a_conv"])


async def test_first_use_notice_is_accepted_once_per_login(env: Env) -> None:
    ids = await _two_people(env)
    _sign_in(env, ids["alice"], "alice", {SLUG: "viewer"})
    h = _on(ids["app"])
    url = "/agent/v1/console/assistant/privacy"
    body = (await env.client.get(url, headers=h)).json()
    assert body["accepted"] is False and body["ttlDays"] == 30 and body["noticeVersion"] >= 1
    r = await env.client.post(url, json={"version": body["noticeVersion"]}, headers=h)
    assert r.status_code == 200, r.text
    assert (await env.client.get(url, headers=h)).json()["accepted"] is True
    entries = await env.svc.store.audit.query(None, everything=True, action="assistant.notice")
    assert entries and entries[0].after == {"version": body["noticeVersion"]}
    # An older version is not an acceptance of this one.
    r = await env.client.post(url, json={"version": 0}, headers=h)
    assert r.status_code == 400
    # Another login has not accepted.
    _sign_in(env, ids["bob"], "bob", {SLUG: "operator"})
    assert (await env.client.get(url, headers=h)).json()["accepted"] is False


def test_online_voice_only_when_data_protection_allows_processors_outside_the_eu() -> None:
    from types import SimpleNamespace

    from evokai.assistant.privacy import online_voice_allowed

    def app(dp: dict | None = None, residency: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(data_protection=dp, residency=residency)

    assert online_voice_allowed(app()) is True
    assert online_voice_allowed(None) is False
    assert online_voice_allowed(app({"residency": {"geographies": ["eu"]}})) is False
    assert online_voice_allowed(app({"residency": {"countries": ["IT", "DE"]}})) is False
    assert online_voice_allowed(app({"residency": {"geographies": ["eu", "us"]}})) is True
    assert online_voice_allowed(app({"jurisdiction": ["eu"]})) is False
    assert online_voice_allowed(app({"retention": "zero_retention"})) is False
    assert online_voice_allowed(app({"browser": {"online_voices": False}})) is False
    assert online_voice_allowed(app({"browser": {"online_voices": True}})) is True
    assert online_voice_allowed(app(residency={"model_regions": ["eu"]})) is False
    assert online_voice_allowed(app(residency={"model_regions": ["global"]})) is True


async def test_privacy_route_says_which_voice(env: Env) -> None:
    ids = await _two_people(env)
    _sign_in(env, ids["alice"], "alice", {SLUG: "viewer"})
    url = "/agent/v1/console/assistant/privacy"
    assert (await env.client.get(url, headers=_on(ids["app"]))).json()["voice"] == "online"
    await env.svc.store.applications.update(
        ids["app"], data_protection={"residency": {"geographies": ["eu"]}}
    )
    assert (await env.client.get(url, headers=_on(ids["app"]))).json()["voice"] == "device"


async def test_a_new_inspection_shows_the_notice_again(env: Env) -> None:
    ids = await _two_people(env)
    url = "/agent/v1/console/assistant/privacy"
    inspect = f"/agent/v1/admin/assistant/conversations/{ids['a_conv']}/inspect"
    _sign_in(env, ids["alice"], "alice", {SLUG: "viewer"})
    r = await env.client.post(url, json={"version": 1}, headers=_on(ids["app"]))
    assert r.status_code == 200
    assert (await env.client.get(url, headers=_on(ids["app"]))).json()["unseen"] == 0
    owner = await _user(env, "olga", {SLUG: "owner"})
    _sign_in(env, owner, "olga", {SLUG: "owner"})
    r = await env.client.post(inspect, json={"reason": REASON}, headers=_on(ids["app"]))
    assert r.status_code == 200
    _sign_in(env, ids["alice"], "alice", {SLUG: "viewer"})
    body = (await env.client.get(url, headers=_on(ids["app"]))).json()
    assert body["accepted"] is True and body["unseen"] == 1
    assert body["inspections"][0]["at"].endswith("+00:00")
    await env.client.post(url, json={"version": 1}, headers=_on(ids["app"]))
    assert (await env.client.get(url, headers=_on(ids["app"]))).json()["unseen"] == 0


async def test_system_app_audit_outlives_its_conversations(env: Env) -> None:
    app_id = await _system(env)
    await env.svc.store.applications.update(app_id, locked=False)
    owner = await _user(env, "olga", {SLUG: "owner"})
    _sign_in(env, owner, "olga", {SLUG: "owner"})
    url = "/agent/v1/admin/application"
    r = await env.client.patch(url, json={"audit_ttl_days": 1}, headers=_on(app_id))
    assert r.status_code == 400 and r.json()["error"] == "system_audit_ttl", r.text
    r = await env.client.patch(url, json={"audit_ttl_days": 30}, headers=_on(app_id))
    assert r.status_code == 200, r.text


async def test_assistant_conversations_go_30_days_after_they_start(env: Env) -> None:
    import sqlalchemy as sa

    from evokai.db import models as m

    ids = await _two_people(env)
    old = await _seed(env, env.app_id, "u-other-app", "kept by its own ttl")
    await env.svc.store.applications.update(env.app_id, conversation_ttl_days=30)
    started = utcnow() - timedelta(days=31)
    async with env.svc.db.session() as s:
        await s.execute(
            sa
            .update(m.Session)
            .where(m.Session.id.in_([ids["a_conv"], old[0]]))
            .values(created_at=started, updated_at=utcnow())
        )
    assert await env.svc.store.sessions.purge_expired() == 1
    assert not await env.svc.store.sessions.exists(ids["a_conv"])
    assert await env.svc.store.sessions.exists(ids["b_conv"])
    assert await env.svc.store.sessions.exists(old[0])


async def test_a_short_deployment_audit_ttl_keeps_the_assistants_inspections_30_days(
    env: Env,
) -> None:
    app_id = await _system(env)
    for owner in (app_id, env.app_id, None):
        await env.svc.store.audit.add(
            application_id=owner,
            actor="sso:olga@example.com",
            actor_kind="sso",
            role="owner",
            action="assistant.conversation.inspect" if owner == app_id else "agent.put",
            target="",
            before=None,
            after=None,
            ip="",
            correlation_id="",
        )
    # EVOKAI_AUDIT_TTL_DAYS=7: the other application's entry and the deployment's go after a week,
    # the inspection stays as long as the conversation it is about may exist.
    assert await env.svc.store.audit.purge(7, now=utcnow() + timedelta(days=10)) == 2
    assert len(await env.svc.store.audit.query(app_id)) == 1
    assert await env.svc.store.audit.purge(7, now=utcnow() + timedelta(days=31)) == 1
    assert await env.svc.store.audit.query(app_id) == []
