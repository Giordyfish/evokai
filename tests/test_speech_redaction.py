"""An application may speak personal identifiers as written (``speech_redaction: false``)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from evokai.audio.speech.speakable import speakable
from evokai.i18n import speech_redaction, speech_rules
from tests.conftest import Env


def test_identifiers_are_redacted_unless_the_application_says_otherwise() -> None:
    text = "Scrivi a info@example.com."
    assert "info@example.com" not in speakable(text, speech_rules("it"))
    assert "info@example.com" in speakable(text, speech_rules("it", redact=False))


def test_the_setting_is_read_from_the_application_defaults() -> None:
    def app(defaults: dict | None) -> SimpleNamespace:
        return SimpleNamespace(defaults=defaults)

    assert speech_redaction(app(None)) is True
    assert speech_redaction(app({})) is True
    assert speech_redaction(app({"speech_redaction": True})) is True
    assert speech_redaction(app({"speech_redaction": False})) is False


async def test_a_turn_speaks_as_written_when_redaction_is_off(env: Env, tmp_path: Path) -> None:
    from evokai.agents.bootstrap import bootstrap

    adm = {"Authorization": f"Bearer {env.admin_key}"}
    got = (await env.client.get("/agent/v1/admin/application", headers=adm)).json()
    assert got["speech_redaction"] is True
    env.script.add("function:primary", ["Scrivi a info@example.com. "])
    tok = (await env.token())["token"]
    says = [e for e in await env.chat(tok, text="ciao", mode="voice") if e["type"] == "say"]
    assert "info@example.com" not in says[0]["text"]
    r = await env.client.patch(
        "/agent/v1/admin/application", headers=adm, json={"speech_redaction": False}
    )
    assert r.status_code == 200, r.text
    env.script.add("function:primary", ["Scrivi a info@example.com. "])
    tok = (await env.token())["token"]
    says = [e for e in await env.chat(tok, text="ciao", mode="voice") if e["type"] == "say"]
    assert "info@example.com" in says[0]["text"]
    text = (await env.client.get("/agent/v1/admin/export", headers=adm)).json()["yaml"]
    assert "speech_redaction: false" in text
    (tmp_path / "copy.yaml").write_text(text.replace("slug: test", "slug: copy"), "utf-8")
    copy_id = await bootstrap(env.svc.store, env.svc.keyring, tmp_path / "copy.yaml", {})
    copy = await env.svc.store.applications.get(copy_id)
    assert copy is not None and copy.defaults["speech_redaction"] is False
