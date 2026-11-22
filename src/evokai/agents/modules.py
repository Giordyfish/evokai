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
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from pydantic import BaseModel
from pydantic_handlebars import HandlebarsEnvironment, HandlebarsError, check_template_compatibility

from evokai.core.errors import BadRequest

if TYPE_CHECKING:
    from evokai.agents.definitions import AgentDef
    from evokai.db.repositories import Store
    from evokai.grants import Grants

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

log = logging.getLogger(__name__)

_HANDLEBARS = HandlebarsEnvironment(auto_escape=False)
"""Prompts are not HTML: values go in as they are."""
_LEGACY = re.compile(r"\{(date|app)\}")


@dataclass(frozen=True)
class RenderedModule:
    """A module resolved for a turn: its pinned version, template and the values it gets."""

    name: str
    version: int
    text: str
    """The module's template (rendered by ``system_prompt`` with ``date``, ``app``, ``data``)."""
    params: dict[str, Any]
    """The parameter values (defaults filled, validated)."""
    data: dict[str, Any] | None = field(default=None)
    """The version's lookup table."""


def render_template(text: str, values: dict[str, Any]) -> str:
    """Render a prompt text.

    A text with ``{{`` is a Handlebars template (``pydantic_handlebars``, no HTML escaping).
    Any other text keeps the older single-brace placeholders: ``{date}`` and ``{app}`` are
    replaced and every other brace stays as it is (JSON examples in prompts are common).

    Args:
        text: The prompt text.
        values: What the template may read (``date``, ``app`` and, for a module, ``data`` and
            its parameters).

    Returns:
        The rendered text. A Handlebars text that cannot be rendered falls back to the
        single-brace path rather than failing the turn.
    """
    if "{{" in text:
        try:
            return _HANDLEBARS.render(text, values)
        except HandlebarsError as e:
            log.warning("prompt template not rendered: %s", type(e).__name__)
    return _LEGACY.sub(lambda m: str(values.get(m[1], m[0])), text)


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


def free_text_items(schema: dict[str, Any], params: dict[str, Any]) -> list[tuple[str, str]]:
    """The free-text parameters with their values (see ``free_text_params``).

    Returns:
        (parameter name, value), in the schema's order.
    """
    out = []
    for name, prop in schema.get("properties", {}).items():
        if prop.get("type") == "string" and "enum" not in prop:
            value = params.get(name)
            if isinstance(value, str) and value:
                out.append((name, value))
    return out


def free_text_params(schema: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """The free-text values: strings of properties without an ``enum``.

    They are the only values a session can give that are not chosen from a list, so the only ones
    the guard must scan.

    Returns:
        The values, in the schema's order.
    """
    return [value for _name, value in free_text_items(schema, params)]


class ModuleChoice(BaseModel):
    """A session's choice for one prompt module (server key only, at token mint)."""

    on: bool | None = None
    """Switch the module on or off; omitted keeps the agent's default."""
    params: dict[str, Any] | None = None
    """Parameter values over the agent's defaults; omitted keeps the agent's values."""


def choose_modules(
    agent: AgentDef,
    choice: dict[str, ModuleChoice],
    grants: Grants,
    stored: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """The modules of a session: the agent's defaults with the session's choice over them.

    A choice replaces any earlier one of the conversation; only the versions it already pinned
    are kept.

    Args:
        agent: The agent definition.
        choice: Module name to the session's choice.
        grants: The application's grants (``module`` kind).
        stored: The modules stored on the conversation so far, if any.

    Returns:
        Module name to ``{"on", "params"}`` (and ``version`` once pinned).

    Raises:
        BadRequest: ``unknown_module`` (not one of the agent's) or ``module_locked`` (the agent
            does not let a session change it).
        NotGranted: 403 ``module_not_granted``.
    """
    from evokai.grants import NotGranted

    refs = {r.name: r for r in agent.modules}
    out: dict[str, dict[str, Any]] = {
        r.name: {"on": r.on, "params": dict(r.params)} for r in agent.modules
    }
    for name, entry in (stored or {}).items():
        if name in out and isinstance(entry, dict) and entry.get("version"):
            out[name]["version"] = entry["version"]
    for name, c in choice.items():
        ref = refs.get(name)
        if ref is None:
            raise BadRequest(f"Unknown prompt module: {name}.", code="unknown_module")
        if not ref.session:
            raise BadRequest(
                f"The agent does not let a session change {name}.", code="module_locked"
            )
        if not grants.module(name):
            raise NotGranted("module", f"Session choices of the module {name} are not granted.")
        if c.on is not None:
            out[name]["on"] = c.on
        if c.params is not None:
            out[name]["params"] = {**ref.params, **c.params}
    return out


async def check_session_modules(
    store: Store, application_id: str, agent: AgentDef, modules: dict[str, Any] | None
) -> dict[str, list[tuple[str, str]]]:
    """Validate the parameters of a session's modules that are on, against their versions.

    Args:
        store: Repositories.
        application_id: Owner application.
        agent: The agent definition.
        modules: The session's modules (``choose_modules``), or None for the agent's defaults.

    Returns:
        Module name to its free-text parameters (name, value), for the guard.

    Raises:
        BadRequest: ``unknown_module`` (the prompt is gone) or ``module_params``.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for ref in agent.modules:
        entry: dict[str, Any] = (modules or {}).get(ref.name) or {
            "on": ref.on,
            "params": ref.params,
        }
        if not entry.get("on"):
            continue
        version = entry.get("version") or ref.version
        row = await store.prompts.get(application_id, ref.name, int(version) if version else None)
        if row is None:
            raise BadRequest(f"Unknown prompt module: {ref.name}.", code="unknown_module")
        params: dict[str, Any] = dict(entry.get("params") or {})
        if row.parameters is None:
            if params:
                raise BadRequest(f"Module {ref.name} has no parameters.", code="module_params")
            continue
        try:
            full = validate_params(row.parameters, params)
        except BadRequest as e:
            raise BadRequest(f"Module {ref.name}: {e.message}", code="module_params") from e
        out[ref.name] = free_text_items(row.parameters, full)
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


async def scan_module_texts(
    guard: Any,
    app: Any,
    agent: AgentDef,
    texts: dict[str, list[tuple[str, str]]],
    *,
    subject: str | None,
    session_id: str | None,
    channel: str = "browser",
) -> None:
    """Run the guard's input checks on a session's free-text module parameters.

    The guard of the application and agent is built as for a turn, and each value is checked as
    a person's message would be. The security events keep the control and the module and
    parameter names only, never the value.

    Args:
        guard: The guard engine (``Services.guard``).
        app: The application.
        agent: The agent definition.
        texts: Module name to (parameter, value) (``check_session_modules``).
        subject: The session's subject.
        session_id: The conversation, when it already exists.
        channel: ``browser`` (session tokens) or ``server`` (server runs).

    Raises:
        BadRequest: 400 ``module_param_rejected`` when an enforced control refused a value.
    """
    if not any(texts.values()):
        return
    turn = guard.turn(
        app, agent, subject=subject, session_id=session_id, turn_id=None, channel=channel
    )
    refused: tuple[str, str] | None = None
    try:
        for module, items in texts.items():
            for param, value in items:
                seen = len(turn.events)
                decision = await turn.check_input(value)
                for row in turn.events[seen:]:
                    row["snippet"] = ""
                    row["details"] = {"module": module, "parameter": param}
                if decision.blocked:
                    refused = (module, param)
                    break
            if refused:
                break
    finally:
        turn.checked.clear()
        await turn.flush()
    if refused:
        raise BadRequest(
            f"Module {refused[0]}: the guard refused the parameter {refused[1]}.",
            code="module_param_rejected",
        )
