"""Inline markup in turns: marks on the spoken segments, raw text kept, users neutralised, guard."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart

from evokai.audio.speech.markup import markup_of
from evokai.audio.speech.speakable import SentenceSplitter
from evokai.guard import output
from tests.conftest import AGENTS_YAML, ORIGIN, Env, make_env

MARKUP_YAML = AGENTS_YAML.replace(
    '  allowed_origins: ["http://app.test"]\n',
    '  allowed_origins: ["http://app.test"]\n'
    "  markup:\n"
    "    cue: {names: [wave, smile, nod]}\n"
    "    speech: {}\n",
)


@pytest.fixture
async def menv(tmp_path: Path):
    async with make_env(tmp_path, agents_yaml=MARKUP_YAML) as e:
        yield e


def _says(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "say"]


def _user_texts(env: Env) -> list[str]:
    _, messages = env.script.seen[-1]
    return [
        p.content
        for m in messages
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, UserPromptPart) and isinstance(p.content, str)
    ]


async def test_cues_become_marks_and_the_text_keeps_them(menv: Env) -> None:
    menv.script.add(
        "function:primary", ["Hi {wa", "ve}! [laughs] It is 1.000 points. ", "Done. {smile}"]
    )
    tok = (await menv.token())["token"]
    events = await menv.chat(tok, text="hello", mode="voice")
    says = _says(events)
    assert [s["text"] for s in says] == ["Hi!", "It is 1.000 points.", "Done.", ""]
    assert says[0]["marks"] == [{"name": "wave", "at": 2}]
    assert says[1]["tagged"] == "[laughs] It is 1.000 points."
    assert says[3]["marks"] == [{"name": "smile", "at": 0}]
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "{wave}" in text and "[laughs]" in text
    conv = await menv.client.get(
        "/agent/v1/session/conversation",
        headers={"Authorization": f"Bearer {tok}", "Origin": ORIGIN},
    )
    stored = [m["text"] for m in conv.json()["messages"] if m["role"] == "assistant"]
    assert "{smile}" in stored[-1]


async def test_a_segment_carries_its_text_as_written(menv: Env) -> None:
    menv.script.add(
        "function:primary",
        ["{wave} Scrivi a **info@example.com** o vai su [il sito](https://example.com). ", "Fine."],
    )
    tok = (await menv.token())["token"]
    says = _says(await menv.chat(tok, text="hello", mode="voice"))
    assert says[0]["raw"] == (
        "{wave} Scrivi a **info@example.com** o vai su [il sito](https://example.com)."
    )
    assert "info@example.com" not in says[0]["text"]
    # A plain segment is shown as written: no raw form.
    assert "raw" not in says[1] and says[1]["text"] == "Fine."


async def test_a_segment_of_cues_alone_hands_its_text_to_the_next(menv: Env) -> None:
    splitter = SentenceSplitter(0, markup=markup_of({"cue": {}}, None, None))
    assert splitter.push("{wave}\n\nCiao a tutti. ") == ["{wave}", "Ciao a tutti."]
    menv.script.add("function:primary", ["{wave}\n\nCiao a **tutti**. "])
    tok = (await menv.token())["token"]
    says = _says(await menv.chat(tok, text="hello", mode="voice"))
    assert says[0]["marks"] == [{"name": "wave", "at": 0}]
    assert says[0]["raw"] == "{wave} Ciao a **tutti**."


async def test_unknown_cues_are_dropped_from_the_marks(menv: Env) -> None:
    menv.script.add("function:primary", ["{dance}Fine {nod}then."])
    tok = (await menv.token())["token"]
    says = _says(await menv.chat(tok, text="hello", mode="voice"))
    assert says == [{**says[0], "text": "Fine then.", "marks": [{"name": "nod", "at": 5}]}]
    admin = {"Authorization": f"Bearer {menv.admin_key}"}
    items = (await menv.client.get("/agent/v1/admin/traces", headers=admin)).json()["items"]
    trace = (
        await menv.client.get(f"/agent/v1/admin/traces/{items[0]['id']}", headers=admin)
    ).json()
    (speech,) = [x for x in trace["spans"] if x["kind"] == "voice"]
    assert speech["attrs"]["markupDropped"] == 1


async def test_a_person_cannot_write_cues(menv: Env) -> None:
    menv.script.add("function:primary", ["ok"])
    tok = (await menv.token())["token"]
    await menv.chat(tok, text='Please {wave} and [laughs] now, {"a": 1}')
    assert _user_texts(menv)[-1] == 'Please \uff5bwave} and \uff3blaughs] now, {"a": 1}'


async def test_text_mode_is_untouched(menv: Env) -> None:
    menv.script.add("function:primary", ["Hi {wave}!"])
    tok = (await menv.token())["token"]
    events = await menv.chat(tok, text="hello")
    assert not _says(events)
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Hi {wave}!"


async def test_session_info_gives_the_delimiters(menv: Env) -> None:
    tok = (await menv.token())["token"]
    r = await menv.client.get(
        "/agent/v1/session", headers={"Authorization": f"Bearer {tok}", "Origin": ORIGIN}
    )
    assert r.json()["markup"] == {
        "cue": {"open": "{", "close": "}"},
        "speech": {"open": "[", "close": "]"},
    }


async def test_the_application_markup_is_patched_and_read(env: Env) -> None:
    admin = {"Authorization": f"Bearer {env.admin_key}"}
    body = {"markup": {"cue": {"open": "<<", "close": ">>", "names": ["wave"]}}}
    r = await env.client.patch("/agent/v1/admin/application", json=body, headers=admin)
    assert r.status_code == 200, r.text
    got = (await env.client.get("/agent/v1/admin/application", headers=admin)).json()["markup"]
    assert got["cue"]["open"] == "<<" and got["cue"]["names"] == ["wave"]
    bad = await env.client.patch(
        "/agent/v1/admin/application", json={"markup": {"cue": {"open": "ab"}}}, headers=admin
    )
    assert bad.status_code in (400, 422)
    r = await env.client.patch("/agent/v1/admin/application", json={"markup": None}, headers=admin)
    assert (await env.client.get("/agent/v1/admin/application", headers=admin)).json()[
        "markup"
    ] is None


def test_the_leak_check_reads_the_answer_without_markup() -> None:
    instructions = (
        "You are the back office assistant of Inkwell Books. Answer in Italian, briefly and "
        "precisely. Never invent data: use the tools or say you do not know."
    )
    m = markup_of({"cue": {}})
    assert m is not None
    leaked = (
        "You {a}are {b}the {c}back {d}office {e}assistant {f}of {g}Inkwell {h}Books. "
        "{i}Answer {j}in Italian."
    )
    plain = output.OutputFilter(
        output.OutputRules(leak=True, prompt_shingles=output.shingles(instructions))
    )
    plain.push(leaked)
    plain.flush()
    assert not plain.blocked  # cues between the words hide it from a check that reads them
    rules = output.OutputRules(
        leak=True, prompt_shingles=output.shingles(instructions), clean=m.clean
    )
    f = output.OutputFilter(rules)
    f.push(leaked)
    f.flush()
    assert f.blocked


def test_the_splitter_never_cuts_inside_a_cue() -> None:
    m = markup_of({"cue": {}})
    s = SentenceSplitter(3, markup=m)
    assert s.push("Look {point:left, high} at it") == []
    assert s.push(". Next") == ["Look {point:left, high} at it."]
    assert s.flush() == ["Next"]
