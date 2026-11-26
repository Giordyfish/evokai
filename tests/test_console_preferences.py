"""Console preferences: per console identity, validated against an allow-list, audited."""

from __future__ import annotations

import time

import pytest

from evokai.identity import console
from evokai.identity.preferences import DEFAULTS, Preferences, identity_of, read
from tests.conftest import Env

CONSOLE = {"X-Evokai-Console": "1"}
OPERATOR_KEY = "evk_op_test_operator_key_0123456789"  # gitleaks:allow
URL = "/agent/v1/console/me/preferences"


async def _login(env: Env, key: str) -> None:
    r = await env.client.post("/agent/v1/console/login", json={"key": key})
    assert r.status_code == 204


def _sso_cookie(env: Env, user_id: str, email: str) -> str:
    who = console.ConsoleLogin(
        "sso",
        None,
        int(time.time()) + 600,
        user_id=user_id,
        email=email,
        roles={"test": "viewer"},
    )
    return console.issue(env.svc.keyring, who)


async def test_defaults_until_saved_then_the_saved_copy(env: Env) -> None:
    await _login(env, env.admin_key)
    first = (await env.client.get(URL, headers=CONSOLE)).json()
    assert first == {"preferences": DEFAULTS.document(), "updatedAt": None}
    assert first["preferences"]["version"] == 2
    assert first["preferences"]["mascot"] == "hidden"
    assert first["preferences"]["motion"] == "system"

    doc = {
        **DEFAULTS.document(),
        "language": "it",
        "theme": "dark",
        "textSize": 125,
        "contrast": "more",
        "focusAlways": True,
        "mascot": "still",
    }
    put = await env.client.put(URL, json=doc, headers=CONSOLE)
    assert put.status_code == 200
    assert put.json()["preferences"] == doc and put.json()["updatedAt"]
    again = (await env.client.get(URL, headers=CONSOLE)).json()
    assert again["preferences"] == doc


async def test_needs_the_console_login_and_its_header(env: Env) -> None:
    assert (await env.client.get(URL, headers=CONSOLE)).status_code == 401
    await _login(env, env.admin_key)
    no_header = await env.client.get(URL)
    assert (no_header.status_code, no_header.json()["error"]) == (403, "console_header")
    put = await env.client.put(URL, json=DEFAULTS.document())
    assert put.status_code == 403
    # Preferences belong to a console login: an API key alone has none.
    env.client.cookies.clear()
    as_bearer = await env.client.get(URL, headers={"Authorization": f"Bearer {env.admin_key}"})
    assert as_bearer.status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"theme": "purple"},
        {"textSize": 150},
        {"textSize": "125"},
        {"language": "de"},
        {"focusAlways": "yes"},
        {"motion": None},
        {"version": 3},
        {"fontFamily": "Comic Sans"},
        {"tours": {"First Steps": 1}},
        {"tours": {"first-steps": -1}},
        {"tours": {"first-steps": 100}},
        {"tours": {"first-steps": "3"}},
        {"tours": {f"tour-{n}": 0 for n in range(21)}},
        {"tourInvite": "later"},
    ],
)
async def test_values_outside_the_allow_list_are_refused(
    env: Env, change: dict[str, object]
) -> None:
    await _login(env, env.admin_key)
    r = await env.client.put(URL, json={**DEFAULTS.document(), **change}, headers=CONSOLE)
    assert (r.status_code, r.json()["error"]) == (400, "bad_request")
    assert (await env.client.get(URL, headers=CONSOLE)).json()["updatedAt"] is None


async def test_each_identity_has_its_own(env: Env) -> None:
    await _login(env, env.admin_key)
    await env.client.put(URL, json={**DEFAULTS.document(), "theme": "dark"}, headers=CONSOLE)

    env.svc.settings.operator_key = OPERATOR_KEY
    env.client.cookies.clear()
    await _login(env, OPERATOR_KEY)
    op = (await env.client.get(URL, headers=CONSOLE)).json()
    assert op["preferences"]["theme"] == "system" and op["updatedAt"] is None
    await env.client.put(URL, json={**DEFAULTS.document(), "language": "en"}, headers=CONSOLE)

    user = await env.svc.store.console_users.signed_in(
        "https://idp.example", "ada", "ada@inkwell-books.example", "Ada", {"test": "viewer"}
    )
    env.client.cookies.clear()
    env.client.cookies.set(console.COOKIE, _sso_cookie(env, user.id, "ada@inkwell-books.example"))
    sso = (await env.client.get(URL, headers=CONSOLE)).json()
    assert sso["updatedAt"] is None
    # A viewer keeps its own preferences: they change nothing in any application.
    r = await env.client.put(URL, json={**DEFAULTS.document(), "mascot": "hidden"}, headers=CONSOLE)
    assert r.status_code == 200

    op_row = await env.svc.store.console_preferences.get("operator")
    sso_row = await env.svc.store.console_preferences.get(f"sso:{user.id}")
    assert op_row is not None and op_row.value["language"] == "en"
    assert sso_row is not None and sso_row.value["mascot"] == "hidden"


async def test_changes_are_audited_with_only_what_changed_and_reset_forgets(env: Env) -> None:
    env.svc.settings.operator_key = OPERATOR_KEY
    await _login(env, OPERATOR_KEY)
    await env.client.put(URL, json={**DEFAULTS.document(), "theme": "dark"}, headers=CONSOLE)
    # Saving the same document again writes nothing.
    await env.client.put(URL, json={**DEFAULTS.document(), "theme": "dark"}, headers=CONSOLE)
    await env.client.put(
        URL, json={**DEFAULTS.document(), "theme": "dark", "textSize": 112}, headers=CONSOLE
    )
    log = (
        await env.client.get(
            "/agent/v1/console/audit", params={"action": "preferences."}, headers=CONSOLE
        )
    ).json()["items"]
    assert [e["action"] for e in log] == ["preferences.update", "preferences.update"]
    newest, oldest = log
    assert newest["before"] == {"textSize": 100} and newest["after"] == {"textSize": 112}
    assert oldest["before"] is None and oldest["after"]["theme"] == "dark"
    assert newest["actor"] == "operator-key" and newest["target"] == "operator-key"

    reset = await env.client.delete(URL, headers=CONSOLE)
    assert reset.json() == {"preferences": DEFAULTS.document(), "updatedAt": None}
    assert (await env.client.get(URL, headers=CONSOLE)).json()["updatedAt"] is None
    log = (
        await env.client.get(
            "/agent/v1/console/audit", params={"action": "preferences."}, headers=CONSOLE
        )
    ).json()["items"]
    assert log[0]["action"] == "preferences.reset"


def test_the_mascot_is_off_unless_a_person_chose_it() -> None:
    """Version 2: the default is the logo. A version 1 ``on`` was the default, not a choice."""
    assert DEFAULTS.mascot == "hidden" and DEFAULTS.version == 2
    assert read({"version": 1, "mascot": "on"}).mascot == "hidden"
    assert read({"version": 1}).mascot == "hidden"
    assert read({"version": 1, "mascot": "still"}).mascot == "still"
    assert read({"version": 1, "mascot": "hidden"}).mascot == "hidden"
    assert read({"version": 2, "mascot": "on"}).mascot == "on"
    assert read({"version": 1, "mascot": "on"}).document()["version"] == 2


def test_a_stored_document_that_no_longer_validates_reads_as_the_defaults() -> None:
    assert read({"version": 1, "theme": "dark"}).theme == "dark"
    assert read({"version": 9, "theme": "dark"}) == DEFAULTS
    assert read("nonsense") == DEFAULTS
    assert Preferences(text_size=112).document()["textSize"] == 112


def test_identities() -> None:
    assert identity_of(console.ConsoleLogin("op", None, 0)) == "operator"
    assert identity_of(console.ConsoleLogin("adm", "a1", 0, "k1")) == "admin-key:k1"
    assert identity_of(console.ConsoleLogin("adm", "a1", 0)) == "admin-app:a1"
    sso = console.ConsoleLogin("sso", None, 0, user_id="u1", email="x@y")
    assert identity_of(sso) == "sso:u1"


async def test_tour_progress_and_the_invite_are_kept(env: Env) -> None:
    """The console's guided tours: where each stopped, and whether the invite was dismissed."""
    assert DEFAULTS.document()["tours"] == {} and DEFAULTS.document()["tourInvite"] == "show"
    await _login(env, env.admin_key)
    tours = {"first-steps": 3, "security": 6}
    doc = {**DEFAULTS.document(), "tours": tours, "tourInvite": "dismissed"}
    put = await env.client.put(URL, json=doc, headers=CONSOLE)
    assert put.status_code == 200
    assert (await env.client.get(URL, headers=CONSOLE)).json()["preferences"] == doc
    # A document saved before the tours existed reads with none started and the invite to show.
    assert read({"version": 2, "theme": "dark"}).document()["tours"] == {}
