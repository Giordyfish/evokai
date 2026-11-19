"""What a confirmation card says for a tool call that changes data.

Labels are data in the agent's definition, in the agent's language:

```yaml
confirm_labels:
  return_close: "Close the return {returnId}"
  order_refund:
    - "Refund {orderIds|count} orders as {reason|map:reasons}"
    - "Refund order {orderId} as {reason|map:reasons}"
confirm_values:
  reasons: {damaged: "Damaged in transit", wrong_item: "Wrong item sent"}
```

The first template whose fields are all present wins. ``{field}`` prints the argument (lists
joined with commas), ``{field|count}`` the length of a list, ``{field|map:table}`` the argument
through a table of ``confirm_values``. Without a template the card names the tool, in the
application's language.
"""

from __future__ import annotations

import re
from typing import Any

from evokai.agents.definitions import AgentDef
from evokai.i18n import message

_FIELD = re.compile(r"\{([A-Za-z0-9_.]+)(?:\|(count|map:[A-Za-z0-9_]+))?\}")


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


def render_label(
    template: str, args: dict[str, Any], tables: dict[str, dict[str, str]]
) -> str | None:
    """One label template over a call's arguments (workflow approvals use them too).

    Returns:
        The text, or None when a field it needs is missing.
    """
    return _render(template, args, tables)


def confirm_label(agent: AgentDef, tool: str, args: dict[str, Any], app: Any = None) -> str:
    """The confirmation card text for a tool call.

    Returns:
        The first complete template of the agent for this tool, else the default naming the tool.
    """
    templates = agent.confirm_labels.get(tool) or []
    for template in [templates] if isinstance(templates, str) else templates:
        text = _render(template, args, agent.confirm_values)
        if text:
            return text
    return message(app, "confirm.default", tool=tool)
