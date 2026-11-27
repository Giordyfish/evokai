"""Each application sets the longest answer of a model call; the deployment's is the default."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evokai.llm.policy import output_cap
from tests.conftest import Env


def test_the_application_cap_wins_over_the_deployment() -> None:
    assert output_cap(SimpleNamespace(defaults={"max_output_tokens": 4000}), 1200) == 4000
    assert output_cap(SimpleNamespace(defaults={}), 1200) == 1200
    assert output_cap(SimpleNamespace(defaults=None), 1200) == 1200
    assert output_cap(None, 1200) == 1200
    assert output_cap(SimpleNamespace(defaults={"max_output_tokens": True}), 1200) == 1200


def _spy(env: Env) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    original = env.script.model

    def spy(name: str):
        model = original(name)
        inner = model.stream_function
        assert inner is not None

        async def stream(messages, info):
            seen.append(dict(info.model_settings or {}))
            async for x in inner(messages, info):
                yield x

        model.stream_function = stream
        return model

    env.svc.catalog._factory = spy
    env.svc.catalog._models.clear()
    return seen


async def test_a_turn_uses_the_cap_of_its_application(env: Env) -> None:
    adm = {"Authorization": f"Bearer {env.admin_key}"}
    seen = _spy(env)
    env.script.add("function:primary", ["ok"], ["ok"])
    tok = await env.token()
    await env.chat(tok["token"], text="ciao")
    assert seen[-1]["max_tokens"] == env.svc.settings.max_output_tokens
    r = await env.client.patch(
        "/agent/v1/admin/application", headers=adm, json={"max_output_tokens": 4000}
    )
    assert r.status_code == 200, r.text
    tok = await env.token()
    await env.chat(tok["token"], text="ciao")
    assert seen[-1]["max_tokens"] == 4000


async def test_the_cap_is_checked_kept_exported_and_bootstrapped(env: Env, tmp_path: Path) -> None:
    from evokai.agents.bootstrap import bootstrap

    adm = {"Authorization": f"Bearer {env.admin_key}"}
    got = (await env.client.get("/agent/v1/admin/application", headers=adm)).json()
    assert got["max_output_tokens"] is None
    assert got["maxOutputTokensDefault"] == env.svc.settings.max_output_tokens
    for bad in (10, 200_000, "many"):
        r = await env.client.patch(
            "/agent/v1/admin/application", headers=adm, json={"max_output_tokens": bad}
        )
        assert r.status_code in (400, 422), bad
    r = await env.client.patch(
        "/agent/v1/admin/application", headers=adm, json={"max_output_tokens": 3000}
    )
    assert r.status_code == 200, r.text
    text = (await env.client.get("/agent/v1/admin/export", headers=adm)).json()["yaml"]
    assert "max_output_tokens: 3000" in text
    (tmp_path / "copy.yaml").write_text(text.replace("slug: test", "slug: copy"), "utf-8")
    copy_id = await bootstrap(env.svc.store, env.svc.keyring, tmp_path / "copy.yaml", {})
    copy = await env.svc.store.applications.get(copy_id)
    assert copy is not None and copy.defaults["max_output_tokens"] == 3000
    r = await env.client.patch(
        "/agent/v1/admin/application", headers=adm, json={"max_output_tokens": None}
    )
    got = (await env.client.get("/agent/v1/admin/application", headers=adm)).json()
    assert got["max_output_tokens"] is None
