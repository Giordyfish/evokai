"""Bootstrap from a mounted config file: idempotent, keys from the environment, skills beside it."""

from __future__ import annotations

from pathlib import Path

import pytest

from evokai.agents.bootstrap import bootstrap
from evokai.core.errors import ConfigError
from evokai.core.security import generate_key
from tests.conftest import Env

CONFIG = """
application:
  slug: mounted
  name: Mounted App
  server_key: "${MOUNTED_SERVER_KEY}"
  conversation_ttl_days: 7
skills_dir: skills
agents:
  - name: helper
    model: "function:primary"
    skills: [first]
    instructions: "You are an assistant."
"""


def _write(root: Path, body: str = "Step one.") -> Path:
    for name in ("first", "second"):
        (root / "skills" / name).mkdir(parents=True, exist_ok=True)
        (root / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The {name} skill.\n---\n{body}\n",
            encoding="utf-8",
        )
    (root / "app.yaml").write_text(CONFIG, encoding="utf-8")
    return root / "app.yaml"


async def test_a_mounted_config_is_applied_idempotently(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = generate_key("srv").value
    monkeypatch.setenv("MOUNTED_SERVER_KEY", key)
    root = tmp_path / "mounted"
    path = _write(root)
    store, keyring = env.svc.store, env.svc.keyring
    app_id = await bootstrap(store, keyring, path, {})
    assert await bootstrap(store, keyring, path, {}) == app_id
    versions = await store.agents.versions(app_id, "helper")
    assert len(versions) == 1
    skills = {s.name: s.body for s in await store.skills.all(app_id)}
    assert set(skills) == {"first", "second"}
    app = await store.applications.get(app_id)
    assert app is not None and app.conversation_ttl_days == 7

    r = await env.client.post(
        "/agent/v1/session-tokens",
        json={"subject": "u1", "agent": "helper"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200

    _write(root, "Step one, revised.")
    await bootstrap(store, keyring, path, {})
    assert "revised" in {s.name: s.body for s in await store.skills.all(app_id)}["first"]
    assert len(await store.agents.versions(app_id, "helper")) == 1


MODULES = """
application:
  slug: modular
  name: Modular App
prompts:
  - name: tone
    description: How long the answers are.
    text: "Answer in a {{style}} way."
    parameters:
      type: object
      properties:
        style: {type: string, enum: [brief, detailed], default: brief}
  - name: shelf
    text: "{{lookup data section}}"
    parameters:
      type: object
      properties:
        section: {type: string, enum: [poetry, crime], default: poetry}
    data: {poetry: "You look after the poetry shelf.", crime: "You look after the crime shelf."}
  - name: house
    text: "Be kind."
grants:
  modules: {shelf: false}
agents:
  - name: helper
    model: "function:primary"
    instructions: "You are an assistant."
    modules:
      - {name: tone, session: true}
      - {name: shelf, params: {section: crime}}
      - {name: house}
"""


async def test_bootstrap_loads_module_prompts_and_agent_modules(env: Env, tmp_path: Path) -> None:
    path = tmp_path / "modular.yaml"
    path.write_text(MODULES, encoding="utf-8")
    store, keyring = env.svc.store, env.svc.keyring
    app_id = await bootstrap(store, keyring, path, {})
    tone = await store.prompts.get(app_id, "tone")
    assert tone is not None and tone.version == 1
    assert tone.description == "How long the answers are."
    assert tone.parameters is not None and tone.parameters["additionalProperties"] is False
    shelf = await store.prompts.get(app_id, "shelf")
    assert shelf is not None and shelf.data == {
        "poetry": "You look after the poetry shelf.",
        "crime": "You look after the crime shelf.",
    }
    house = await store.prompts.get(app_id, "house")
    assert house is not None and house.parameters is None
    row = await store.agents.active(app_id, "helper")
    assert row is not None
    assert [m["name"] for m in row.definition["modules"]] == ["tone", "shelf", "house"]
    grants = {(g.kind, g.name): g.value for g in await store.grants.all(app_id)}
    assert grants["module", "shelf"] is False
    # Applied again: nothing changes.
    await bootstrap(store, keyring, path, {})
    tone = await store.prompts.get(app_id, "tone")
    assert tone is not None and tone.version == 1


async def test_bootstrap_refuses_bad_module_template(env: Env, tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(MODULES.replace("{{style}}", "{{stlye}}"), encoding="utf-8")
    with pytest.raises(ConfigError, match="tone"):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
    path.write_text(MODULES.replace("section: crime}", "section: drama}"), encoding="utf-8")
    with pytest.raises(ConfigError, match="shelf"):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})
    path.write_text(MODULES.replace("{name: house}", "{name: index}"), encoding="utf-8")
    with pytest.raises(ConfigError, match="index"):
        await bootstrap(env.svc.store, env.svc.keyring, path, {})


async def test_the_bookshop_config_declares_its_modules(env: Env) -> None:
    path = Path(__file__).resolve().parents[1] / "config" / "bookshop.yaml"
    app_id = await bootstrap(env.svc.store, env.svc.keyring, path, {})
    row = await env.svc.store.agents.active(app_id, "bookshop")
    assert row is not None
    modules = {m["name"]: m for m in row.definition["modules"]}
    assert modules["tone"]["session"] and modules["reader_level"]["session"]
    tone = await env.svc.store.prompts.get(app_id, "tone")
    assert tone is not None and tone.data is not None and set(tone.data) == {"brief", "detailed"}
