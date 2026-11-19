"""Bootstrap from a mounted config file: idempotent, keys from the environment, skills beside it."""

from __future__ import annotations

from pathlib import Path

import pytest

from evokai.agents.bootstrap import bootstrap
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
