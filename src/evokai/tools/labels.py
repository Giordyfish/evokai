"""What a confirmation card says for a tool call that changes data.

Labels are data in the agent's definition, in the agent's language, written in Handlebars:

```yaml
confirm_labels:
  return_close: "Close the return {{returnId}}"
  order_refund:
    - 'Refund {{count orderIds}} orders as {{map reason "reasons"}}'
    - 'Refund order {{orderId}} as {{map reason "reasons"}}'
confirm_values:
  reasons: {damaged: "Damaged in transit", wrong_item: "Wrong item sent"}
```

The first template whose fields are all present and not empty wins. ``{{field}}`` prints the
argument (lists joined with commas, dotted paths reach inside objects), ``{{count field}}`` the
length of a list, ``{{map field "table"}}`` the argument through a table of ``confirm_values``.
Without a template the card names the tool, in the application's language.

Agent versions saved before Handlebars (``template_syntax: legacy``) keep the older grammar,
``{field}``, ``{field|count}`` and ``{field|map:table}``, as do workflow approvals
(``render_label``).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic_handlebars import HandlebarsError, extract_dependencies

from evokai.agents.definitions import AgentDef
from evokai.agents.modules import LABEL_HELPERS, LABEL_TABLES, LABELS
from evokai.agents.templates import refuse_legacy
from evokai.core.errors import BadRequest
from evokai.i18n import message

_FIELD = re.compile(r"\{([A-Za-z0-9_.]+)(?:\|(count|map:[A-Za-z0-9_]+))?\}")
_MAP_TABLE = re.compile(r"(?:\{\{~?|\()\s*map\s+[^\s(){}]+\s+([^\s(){}~]+)")
"""The table argument of each ``map`` call (a mustache or a subexpression)."""


def _value(args: dict[str, Any], path: str) -> Any:
    value: Any = args
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _render(template: str, args: dict[str, Any], tables: dict[str, dict[str, str]]) -> str | None:
    missing = False

    def field(m: re.Match[str]) -> str:
        nonlocal missing
        value = _value(args, m.group(1))
        if value is None or value == "" or value == []:
            missing = True
            return ""
        op = m.group(2) or ""
        if op == "count":
            return str(len(value)) if isinstance(value, list) else "1"
        items = value if isinstance(value, list) else [value]
        if op.startswith("map:"):
            table = tables.get(op[4:], {})
            items = [table.get(str(v), str(v)) for v in items]
        return ", ".join(str(v) for v in items)

    text = _FIELD.sub(field, template)
    return None if missing else text


class _Items(list):
    """A list argument: printed as its items joined with commas."""

    def __str__(self) -> str:
        return ", ".join(str(v) for v in self)


def _present(value: Any) -> Any:
    """The arguments without their empty values (None, "" and []), lists printable.

    Returns:
        The value, its empty fields left out.
    """
    if isinstance(value, dict):
        return {k: _present(v) for k, v in value.items() if not (v is None or v == "" or v == [])}
    if isinstance(value, list):
        return _Items(value)
    return value


def render_handlebars_label(
    template: str, args: dict[str, Any], tables: dict[str, dict[str, str]]
) -> str | None:
    """One Handlebars label template over a call's arguments.

    Rendered strictly over the arguments without their empty values, so a field that is missing
    or empty makes the template incomplete.

    Returns:
        The text, or None when a field it reads is missing or empty.
    """
    context = {**_present(args), LABEL_TABLES: tables}
    try:
        # The arguments are JSON already. The compiled function is called directly because the
        # public render turns every list back into a plain one, and a list must print joined.
        return LABELS._compile_fn(template)(context)
    except HandlebarsError:
        return None


def render_label(
    template: str, args: dict[str, Any], tables: dict[str, dict[str, str]]
) -> str | None:
    """One label template of the older grammar over a call's arguments (workflow approvals).

    Returns:
        The text, or None when a field it needs is missing.
    """
    return _render(template, args, tables)


def _templates(labels: str | list[str]) -> list[str]:
    return [labels] if isinstance(labels, str) else list(labels)


def check_labels(labels: dict[str, str | list[str]], tables: dict[str, dict[str, str]]) -> None:
    """Check an agent's Handlebars label templates when it is saved.

    Each template parses, calls only ``count``, ``map`` and the block helpers, and maps only
    through a table of ``confirm_values`` named in quotes. An old placeholder
    (``{field|count}``...) is 400 ``legacy_syntax``.

    Raises:
        BadRequest: ``label_template``, naming the tool and what is wrong.
    """
    for tool, value in labels.items():
        for template in _templates(value):
            where = f"Confirm label of {tool}"
            refuse_legacy(template, where, labels=True)
            try:
                LABELS.compile(template)
                deps = extract_dependencies(template, helpers=set(LABEL_HELPERS))
            except HandlebarsError as e:
                raise BadRequest(f"{where}: {e}", code="label_template") from e
            for name in deps:
                # A name that is no longer a field once known as a helper is only ever called.
                if name not in extract_dependencies(template, helpers={*LABEL_HELPERS, name}):
                    raise BadRequest(
                        f"{where}: unknown helper {name} (count and map are available).",
                        code="label_template",
                    )
            for arg in _MAP_TABLE.findall(template):
                table = arg[1:-1] if len(arg) > 1 and arg[0] == arg[-1] and arg[0] in "\"'" else ""
                if table not in tables:
                    raise BadRequest(
                        f"{where}: map needs a table of confirm_values in quotes, not {arg}.",
                        code="label_template",
                    )


def confirm_label(agent: AgentDef, tool: str, args: dict[str, Any], app: Any = None) -> str:
    """The confirmation card text for a tool call.

    Returns:
        The first complete template of the agent for this tool, else the default naming the tool.
    """
    render = _render if agent.template_syntax == "legacy" else render_handlebars_label
    for template in _templates(agent.confirm_labels.get(tool) or []):
        text = render(template, args, agent.confirm_values)
        if text:
            return text
    return message(app, "confirm.default", tool=tool)
