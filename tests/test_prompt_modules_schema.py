"""Prompt modules: the parameter schema subset, the template check and the parameter values."""

from __future__ import annotations

from typing import Any

import pytest

from evokai.agents.modules import (
    check_module_template,
    check_parameters_schema,
    free_text_params,
    validate_params,
)
from evokai.core.errors import BadRequest
from tests.conftest import Env

PROMPTS = "/agent/v1/admin/prompts"

PERSONA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "maxLength": 40, "description": "Who the reader talks to"},
        "style": {"type": "string", "enum": ["brief", "detailed"], "default": "brief"},
        "formal": {"type": "boolean", "default": False},
        "shelf": {"type": "integer", "minimum": 1, "maximum": 9},
        "genres": {
            "type": "array",
            "items": {"type": "string", "enum": ["poetry", "crime", "history"]},
            "maxItems": 3,
        },
    },
    "required": ["name"],
}


def _adm(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.admin_key}"}


def _code(fn: Any, *args: Any) -> str:
    with pytest.raises(BadRequest) as e:
        fn(*args)
    return e.value.code


def test_schema_accepts_the_subset() -> None:
    check_parameters_schema(PERSONA)
    check_parameters_schema({"type": "object", "properties": {}})
    check_parameters_schema({"type": "object", "properties": {"n": {"type": "number"}}})


def test_schema_refuses_nested_objects() -> None:
    nested = {"type": "object", "properties": {"who": {"type": "object", "properties": {}}}}
    assert _code(check_parameters_schema, nested) == "module_schema"
    arr = {"type": "object", "properties": {"x": {"type": "array", "items": {"type": "object"}}}}
    assert _code(check_parameters_schema, arr) == "module_schema"
    assert _code(check_parameters_schema, {"type": "string"}) == "module_schema"
    ref = {"type": "object", "properties": {"x": {"$ref": "#/defs/x"}}}
    assert _code(check_parameters_schema, ref) == "module_schema"


def test_schema_requires_maxlength_on_free_strings() -> None:
    free = {"type": "object", "properties": {"name": {"type": "string"}}}
    assert _code(check_parameters_schema, free) == "module_schema"
    long = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 201}}}
    assert _code(check_parameters_schema, long) == "module_schema"
    many = {"type": "object", "properties": {"s": {"type": "string", "enum": list(range(51))}}}
    assert _code(check_parameters_schema, many) == "module_schema"
    props = {f"p{i}": {"type": "boolean"} for i in range(21)}
    assert (
        _code(check_parameters_schema, {"type": "object", "properties": props}) == "module_schema"
    )
    bad_req = {"type": "object", "properties": {"a": {"type": "boolean"}}, "required": ["b"]}
    assert _code(check_parameters_schema, bad_req) == "module_schema"


def test_schema_forces_no_additional_properties() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "boolean"}}}
    check_parameters_schema(schema)
    assert schema["additionalProperties"] is False
    opened = {"type": "object", "properties": {}, "additionalProperties": True}
    assert _code(check_parameters_schema, opened) == "module_schema"


def test_template_with_undeclared_variable_is_refused() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 40}}}
    check_module_template("Hi {{name}}", schema)
    assert _code(check_module_template, "Hi {{nmae}}", schema) == "module_template"
    assert _code(check_module_template, "Hi {{> partial}}", schema) == "module_template"


def test_template_may_use_date_and_app() -> None:
    check_module_template("Today is {{date}} in {{app}}.", {"type": "object", "properties": {}})


def test_template_may_look_up_its_data() -> None:
    schema = {"type": "object", "properties": {"shelf": {"type": "string", "enum": ["a", "b"]}}}
    check_module_template("{{lookup data shelf}}", schema, {"a": "Poetry", "b": "Crime"})


def test_params_fill_defaults_and_reject_unknown_keys() -> None:
    check_parameters_schema(PERSONA)
    got = validate_params(PERSONA, {"name": "Ada"})
    assert got == {"name": "Ada", "style": "brief", "formal": False}
    with pytest.raises(BadRequest) as e:
        validate_params(PERSONA, {"name": "Ada", "colour": "red"})
    assert e.value.code == "module_params"
    with pytest.raises(BadRequest) as e:
        validate_params(PERSONA, {"name": "Ada", "style": "chatty"})
    assert e.value.code == "module_params" and "style" in e.value.message
    with pytest.raises(BadRequest) as e:
        validate_params(PERSONA, {"name": "x" * 41})
    assert "name" in e.value.message


def test_free_text_params_skip_enums() -> None:
    values = {"name": "Ada", "style": "brief", "formal": True, "genres": ["crime"], "shelf": 2}
    assert free_text_params(PERSONA, values) == ["Ada"]


async def test_put_prompt_with_bad_template_is_400(env: Env) -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string", "maxLength": 40}}}
    r = await env.client.put(
        f"{PROMPTS}/greet",
        json={"text": "Hi {{nmae}}", "parameters": schema},
        headers=_adm(env),
    )
    assert r.status_code == 400 and r.json()["error"] == "module_template", r.text
    r = await env.client.put(
        f"{PROMPTS}/greet",
        json={"text": "Hi {{name}}", "parameters": {"type": "object", "properties": {"x": {}}}},
        headers=_adm(env),
    )
    assert r.status_code == 400 and r.json()["error"] == "module_schema", r.text
    r = await env.client.put(
        f"{PROMPTS}/greet",
        json={"text": "Hi {{name}}", "parameters": schema, "description": "Greets the reader"},
        headers=_adm(env),
    )
    assert r.status_code == 200 and r.json()["version"] == 1, r.text
    r = await env.client.get(PROMPTS, headers=_adm(env))
    item = next(x for x in r.json()["items"] if x["name"] == "greet")
    assert item["description"] == "Greets the reader"
    assert item["parameters"]["properties"]["name"]["maxLength"] == 40
    assert item["parameters"]["additionalProperties"] is False
    assert item["text"] == "Hi {{name}}"


async def test_prompt_preview_renders(env: Env) -> None:
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "maxLength": 40},
            "shelf": {"type": "string", "enum": ["a", "b"], "default": "a"},
        },
    }
    body = {
        "text": "Hi {{name}} at {{app}}: {{lookup data shelf}}",
        "parameters": schema,
        "data": {"a": "poetry", "b": "crime"},
    }
    r = await env.client.put(f"{PROMPTS}/greet", json=body, headers=_adm(env))
    assert r.status_code == 200, r.text
    r = await env.client.post(
        f"{PROMPTS}/greet/preview", json={"params": {"name": "R&D <team>"}}, headers=_adm(env)
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "Hi R&D <team> at Test App: poetry"
    r = await env.client.post(
        f"{PROMPTS}/greet/preview", json={"params": {"shelf": "z"}}, headers=_adm(env)
    )
    assert r.status_code == 400 and r.json()["error"] == "module_params", r.text
    # A draft is checked like a save, then rendered.
    draft = {**body, "text": "Bye {{nmae}}", "params": {}}
    r = await env.client.post(f"{PROMPTS}/greet/preview", json=draft, headers=_adm(env))
    assert r.status_code == 400 and r.json()["error"] == "module_template", r.text
    draft = {**body, "text": "Bye {{name}}", "params": {"name": "Ada"}}
    r = await env.client.post(f"{PROMPTS}/greet/preview", json=draft, headers=_adm(env))
    assert r.json()["text"] == "Bye Ada"
    r = await env.client.post(f"{PROMPTS}/missing/preview", json={}, headers=_adm(env))
    assert r.status_code == 404
