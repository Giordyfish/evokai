"""The checks a text gets when it is saved: Handlebars, never the old placeholders.

Agent instructions, application prompts and confirm labels are Handlebars templates
(``pydantic_handlebars``, the environment of ``evokai.agents.modules``). The versions saved before
are ``legacy`` and keep rendering as they did; a new save that still writes an old placeholder
(``{date}``, ``{app}``, a label's ``{field}``, ``{field|count}`` or ``{field|map:table}``) is
refused with 400 ``legacy_syntax``, naming the placeholder and what to write instead. Other braces
(JSON examples) are ordinary text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic_handlebars import HandlebarsError, check_template_compatibility

from evokai.core.errors import BadRequest

if TYPE_CHECKING:
    from evokai.agents.definitions import AgentDef
    from evokai.db.repositories import Store

_OLD_TEXT = re.compile(r"(?<!\{)\{(date|app)\}(?!\})")
_OLD_LABEL = re.compile(r"(?<!\{)\{([A-Za-z0-9_.]+)(?:\|(count|map:([A-Za-z0-9_]+)))?\}(?!\})")
TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"date": {"type": "string"}, "app": {"type": "string"}},
    "required": ["date", "app"],
    "additionalProperties": False,
}
"""What instructions and plain prompts may read."""


class LegacySyntax(BadRequest):
    """A text saved with an old placeholder."""

    code = "legacy_syntax"


def legacy_placeholder(text: str, *, labels: bool = False) -> tuple[str, str] | None:
    """The first old placeholder of a text, with its Handlebars form.

    Args:
        text: The text.
        labels: Read it as a confirm label (``{field}``, ``{field|count}``,
            ``{field|map:table}``) rather than instructions (``{date}``, ``{app}``).

    Returns:
        (the placeholder, what to write instead), or None.
    """
    if not labels:
        m = _OLD_TEXT.search(text)
        return (m[0], "{{" + m[1] + "}}") if m else None
    m = _OLD_LABEL.search(text)
    if m is None:
        return None
    field, op, table = m[1], m[2], m[3]
    if op == "count":
        return m[0], "{{count " + field + "}}"
    if table:
        return m[0], "{{map " + field + ' "' + table + '"}}'
    return m[0], "{{" + field + "}}"


def refuse_legacy(text: str, where: str, *, labels: bool = False) -> None:
    """Refuse a text that still has an old placeholder.

    Raises:
        LegacySyntax: 400 ``legacy_syntax``, naming where, the placeholder and its new form.
    """
    found = legacy_placeholder(text, labels=labels)
    if found:
        raise LegacySyntax(
            f"{where}: {found[0]} is the old placeholder syntax, use {found[1]} (Handlebars)."
        )


def check_text(text: str, where: str) -> None:
    """Check instructions or a plain prompt: Handlebars reading only ``date`` and ``app``.

    An old placeholder is refused first (``refuse_legacy``).

    Raises:
        BadRequest: ``prompt_template``, a template that does not parse or reads anything else.
    """
    refuse_legacy(text, where)
    try:
        check_template_compatibility(text, TEXT_SCHEMA, raise_on_error=True)
    except HandlebarsError as e:
        raise BadRequest(f"{where}: {e}", code="prompt_template") from e


def check_prompt(
    text: str, parameters: dict[str, Any] | None, data: dict[str, Any] | None = None
) -> None:
    """Check an application prompt's text: a module's against its schema, else ``check_text``.

    The schema and the data table are already checked (``check_parameters_schema``,
    ``check_data``). An old placeholder is 400 ``legacy_syntax``, a template that does not fit
    ``module_template`` or ``prompt_template``.
    """
    from evokai.agents.modules import check_module_template

    if parameters is None:
        check_text(text, "Prompt")
        return
    refuse_legacy(text, "Prompt")
    check_module_template(text, parameters, data)


async def syntax_to_save(store: Store, application_id: str, body: AgentDef) -> AgentDef:
    """The definition with the syntax it is saved in, when the body does not name one.

    A body that is exactly a stored version read as ``legacy`` (a restore of an older version) is
    ``legacy``; anything else is new content, ``handlebars``.

    Returns:
        The definition with ``template_syntax`` set.
    """
    if "template_syntax" in body.model_fields_set:
        return body
    legacy = body.model_copy(update={"template_syntax": "legacy"})
    known = await store.agents.version(application_id, body.name, legacy.with_version().version)
    return legacy if known else body.model_copy(update={"template_syntax": "handlebars"})


def check_agent_templates(
    agent: AgentDef, current: dict[str, Any] | None, *, stored: bool = False
) -> None:
    """Check an agent's instructions and confirm labels before it is saved.

    A ``handlebars`` agent's texts must be Handlebars. ``legacy`` is accepted only for a version
    that is already stored (a restore), or to save again an active legacy version whose
    instructions, prompt reference and labels stay the same (another model, other settings), so a
    running agent keeps working until someone rewrites its texts. Texts that do not pass are 400
    ``prompt_template`` or ``label_template``, old placeholders 400 ``legacy_syntax``.

    Args:
        agent: The definition to save.
        current: The active version's stored definition, if any.
        stored: Whether this exact version is already stored.

    Raises:
        LegacySyntax: ``legacy`` on new or changed texts.
    """
    from evokai.tools.labels import check_labels

    if agent.template_syntax == "legacy":
        texts = ("instructions", "prompt", "confirm_labels")
        mine = agent.model_dump(include=set(texts))
        live = current is not None and current.get("template_syntax", "legacy") == "legacy"
        same = live and all(mine[k] == (current or {}).get(k, mine[k]) for k in texts)
        if not (stored or same):
            raise LegacySyntax(
                "New or changed instructions and labels are Handlebars: set template_syntax to "
                "handlebars and write {{date}}, {{app}}, {{field}}, {{count field}} and "
                '{{map field "table"}}.'
            )
        return
    if agent.instructions.strip():
        check_text(agent.instructions, "Instructions")
    check_labels(agent.confirm_labels, agent.confirm_values)
