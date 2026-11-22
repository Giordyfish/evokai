"""Prompt modules: application prompts with parameters that an agent composes into its prompt.

A module is a versioned application prompt (``PromptRecord``) that also carries a parameter
schema, a restricted JSON Schema (``check_parameters_schema``), and optionally a static lookup
table (``data``). Its text is a Handlebars template (``pydantic_handlebars``, as pydantic-ai's
``TemplateStr``) checked against the schema when it is saved (``check_module_template``). An agent
lists the modules it may use with their defaults; the application backend switches them on or off
and fills their parameters when it mints a session token (``validate_params``); the browser never
can.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from pydantic_handlebars import HandlebarsError, check_template_compatibility

from evokai.core.errors import BadRequest

if TYPE_CHECKING:
    from evokai.agents.definitions import AgentDef
    from evokai.db.repositories import Store

MAX_PROPERTIES = 20
"""Parameters of a module."""
MAX_ENUM = 50
"""Values of a choice parameter."""
MAX_ENUM_CHARS = 100
"""Characters of one choice value (and of one key of the data table)."""
MAX_STRING = 200
"""The largest ``maxLength`` of a free-text parameter."""
MAX_ITEMS = 200
"""The largest ``maxItems`` of a multi-choice parameter."""
MAX_DATA_BYTES = 256 * 1024
"""The data table of a module version, serialised."""
RESERVED = ("date", "app", "data")
"""Names every module template gets; a parameter cannot take them."""

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_COMMON = {"type", "default", "description"}
_TOP = {"type", "properties", "required", "additionalProperties"}


class ModuleSchemaError(BadRequest):
    """A parameter schema outside the allowed subset."""

    code = "module_schema"


def _check_enum(name: str, values: Any) -> None:
    if not isinstance(values, list) or not values or len(values) > MAX_ENUM:
        raise ModuleSchemaError(f"Parameter {name}: enum needs 1 to {MAX_ENUM} values.")
    if not all(isinstance(v, str) and 0 < len(v) <= MAX_ENUM_CHARS for v in values):
        raise ModuleSchemaError(
            f"Parameter {name}: every value is a text of 1 to {MAX_ENUM_CHARS} characters."
        )
    if len(set(values)) != len(values):
        raise ModuleSchemaError(f"Parameter {name}: the values must be different.")


def _check_property(name: str, prop: Any) -> None:
    if not _NAME.match(name) or name in RESERVED:
        raise ModuleSchemaError(
            f"Parameter name {name!r}: letters, digits and _, not date, app or data."
        )
    if not isinstance(prop, dict):
        raise ModuleSchemaError(f"Parameter {name}: not a schema.")
    kind = prop.get("type")
    if kind == "string" and "enum" in prop:
        allowed = _COMMON | {"enum"}
        _check_enum(name, prop["enum"])
    elif kind == "string":
        allowed = _COMMON | {"maxLength", "pattern"}
        n = prop.get("maxLength")
        if not isinstance(n, int) or isinstance(n, bool) or not 0 < n <= MAX_STRING:
            raise ModuleSchemaError(
                f"Parameter {name}: a free text needs maxLength from 1 to {MAX_STRING}."
            )
        if "pattern" in prop:
            try:
                re.compile(str(prop["pattern"]))
            except re.error as e:
                raise ModuleSchemaError(
                    f"Parameter {name}: the pattern is not a valid expression."
                ) from e
    elif kind in ("integer", "number"):
        allowed = _COMMON | {"minimum", "maximum"}
        for k in ("minimum", "maximum"):
            v = prop.get(k)
            if v is not None and (not isinstance(v, int | float) or isinstance(v, bool)):
                raise ModuleSchemaError(f"Parameter {name}: {k} must be a number.")
    elif kind == "boolean":
        allowed = _COMMON
    elif kind == "array":
        allowed = _COMMON | {"items", "maxItems"}
        items = prop.get("items")
        if (
            not isinstance(items, dict)
            or set(items) - {"type", "enum"}
            or items.get("type") != ("string")
        ):
            raise ModuleSchemaError(
                f"Parameter {name}: a list holds choices (string items with an enum)."
            )
        _check_enum(name, items.get("enum"))
        n = prop.get("maxItems")
        if not isinstance(n, int) or isinstance(n, bool) or not 0 < n <= MAX_ITEMS:
            raise ModuleSchemaError(
                f"Parameter {name}: a list needs maxItems from 1 to {MAX_ITEMS}."
            )
    else:
        raise ModuleSchemaError(
            f"Parameter {name}: type must be string, integer, number, boolean or array."
        )
    extra = set(prop) - allowed
    if extra:
        raise ModuleSchemaError(f"Parameter {name}: {', '.join(sorted(extra))} not allowed.")
    if "description" in prop and not isinstance(prop["description"], str):
        raise ModuleSchemaError(f"Parameter {name}: description must be a text.")
    if "default" in prop:
        schema = {k: v for k, v in prop.items() if k not in ("default", "description")}
        if not Draft202012Validator(schema).is_valid(prop["default"]):
            raise ModuleSchemaError(f"Parameter {name}: the default does not fit the parameter.")


def check_parameters_schema(schema: dict[str, Any]) -> None:
    """Check that a module's parameter schema stays in the allowed subset.

    The top level is an object with at most ``MAX_PROPERTIES`` properties, an optional
    ``required`` list and ``additionalProperties`` set to false (forced here). Each property is a
    choice (string ``enum``), a short free text (string with ``maxLength``, optional ``pattern``),
    an integer or number (optional ``minimum``/``maximum``), a yes/no (boolean) or a multi-choice
    (array of string ``enum`` with ``maxItems``), with an optional ``default`` and
    ``description``. Anything else is refused.

    Args:
        schema: The schema; ``additionalProperties`` is set to false in place.

    Raises:
        ModuleSchemaError: ``module_schema`` (a ``BadRequest``), naming what is not allowed.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ModuleSchemaError("The parameters are an object schema (type: object).")
    extra = set(schema) - _TOP
    if extra:
        raise ModuleSchemaError(
            f"{', '.join(sorted(extra))} not allowed at the top of the parameters."
        )
    if schema.get("additionalProperties", False) is not False:
        raise ModuleSchemaError("additionalProperties must be false.")
    props = schema.get("properties", {})
    if not isinstance(props, dict) or len(props) > MAX_PROPERTIES:
        raise ModuleSchemaError(f"At most {MAX_PROPERTIES} parameters.")
    for name, prop in props.items():
        _check_property(name, prop)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in props for r in required):
        raise ModuleSchemaError("required lists only declared parameters.")
    schema["additionalProperties"] = False
    schema.setdefault("properties", {})


def check_data(data: dict[str, Any] | None) -> None:
    """Check a module's lookup table: text entries under short keys, at most 256 KB.

    Raises:
        BadRequest: ``module_data``.
    """
    if data is None:
        return
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and 0 < len(k) <= MAX_ENUM_CHARS and isinstance(v, str)
        for k, v in data.items()
    ):
        raise BadRequest("The data table maps short keys to texts.", code="module_data")
    if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_DATA_BYTES:
        raise BadRequest("The data table is larger than 256 KB.", code="module_data")


def template_schema(schema: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
    """The schema a module template is checked against: its parameters, ``date``, ``app``, ``data``.

    Returns:
        A JSON Schema of the values the template is rendered with.
    """
    table = {k: {"type": "string"} for k in data or {}}
    return {
        "type": "object",
        "properties": {
            **schema.get("properties", {}),
            "date": {"type": "string"},
            "app": {"type": "string"},
            "data": {"type": "object", "properties": table, "additionalProperties": False},
        },
        "required": [*schema.get("required", []), "date", "app", "data"],
        "additionalProperties": False,
    }


def check_module_template(
    text: str, schema: dict[str, Any], data: dict[str, Any] | None = None
) -> None:
    """Check that a module's template only uses its parameters, ``date``, ``app`` and ``data``.

    Args:
        text: The Handlebars template.
        schema: The module's parameter schema (already checked).
        data: The module's lookup table, if any.

    Raises:
        BadRequest: ``module_template``, with what the check found.
    """
    try:
        check_template_compatibility(text, template_schema(schema, data), raise_on_error=True)
    except HandlebarsError as e:
        raise BadRequest(f"Template: {e}", code="module_template") from e


def validate_params(
    schema: dict[str, Any], params: dict[str, Any], *, partial: bool = False
) -> dict[str, Any]:
    """Fill the defaults of a module's parameters and validate the values.

    Args:
        schema: The module's parameter schema.
        params: The values given (agent defaults, a session's choice).
        partial: Do not require the ``required`` ones (an agent's defaults, which a session
            token may complete).

    Returns:
        The values with the schema's defaults for the ones not given.

    Raises:
        BadRequest: ``module_params``, naming the first parameter that does not fit (never its
            value, which may be free text).
    """
    if not isinstance(params, dict):
        raise BadRequest("Module parameters are an object.", code="module_params")
    full = dict(params)
    for name, prop in schema.get("properties", {}).items():
        if name not in full and "default" in prop:
            full[name] = prop["default"]
    if partial:
        schema = {k: v for k, v in schema.items() if k != "required"}
    errors = sorted(Draft202012Validator(schema).iter_errors(full), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        if e.validator == "additionalProperties":
            unknown = sorted(set(full) - set(schema.get("properties", {})))
            raise BadRequest(f"Unknown parameter: {', '.join(unknown)}.", code="module_params")
        if e.validator == "required":
            missing = [r for r in schema.get("required", []) if r not in full]
            raise BadRequest(f"Missing parameter: {', '.join(missing)}.", code="module_params")
        where = ".".join(str(p) for p in e.path) or "(all)"
        raise BadRequest(f"Parameter {where} does not fit ({e.validator}).", code="module_params")
    return full


def free_text_params(schema: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """The free-text values: strings of properties without an ``enum``.

    They are the only values a session can give that are not chosen from a list, so the only ones
    the guard must scan.

    Returns:
        The values, in the schema's order.
    """
    out = []
    for name, prop in schema.get("properties", {}).items():
        if prop.get("type") == "string" and "enum" not in prop:
            value = params.get(name)
            if isinstance(value, str) and value:
                out.append(value)
    return out


async def check_agent_modules(store: Store, application_id: str, agent: AgentDef) -> None:
    """Check the modules an agent lists, when it is saved.

    Each names an existing application prompt (at its pinned version, if any). A prompt without a
    parameter schema is a module without parameters; the agent's values must fit the schema
    (required ones may be left to the session).

    Raises:
        BadRequest: ``unknown_module`` or ``module_params``.
    """
    for ref in agent.modules:
        row = await store.prompts.get(application_id, ref.name, ref.version)
        if row is None:
            at = f" v{ref.version}" if ref.version else ""
            raise BadRequest(f"Unknown prompt module: {ref.name}{at}.", code="unknown_module")
        if row.parameters is None:
            if ref.params:
                raise BadRequest(f"Module {ref.name} has no parameters.", code="module_params")
            continue
        try:
            validate_params(row.parameters, ref.params, partial=True)
        except BadRequest as e:
            raise BadRequest(f"Module {ref.name}: {e.message}", code="module_params") from e
