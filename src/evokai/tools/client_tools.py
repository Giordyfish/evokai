"""Tools Evokai answers itself.

They act on the client (navigation, charts, schemas, questions), on the user's long-term memory, or
load a skill. App tools come from MCP; when names clash, the app's tool wins.
"""

from __future__ import annotations

from typing import Any

from evokai.agents.definitions import AgentDef

CHART = {
    "name": "ui_chart",
    "description": " ".join([
        (
            "Show a chart in the panel, using only numbers returned by the tools. Pick the shape "
            "from the task:"
        ),
        (
            "line or area = a trend over time (months in order); column = comparing a few "
            "periods or items; bar = a ranking or comparing many items with long names;"
        ),
        (
            "donut = shares of a total (2-6 items, the rest goes into 'Other'); stacked = the "
            "same in a single bar, when the proportion matters;"
        ),
        (
            "kpi = 1-4 big key figures (items with label, value, unit, optional delta % and "
            "note, e.g. 'vs September'). unit: eur only for money, pct for percentages, ms "
            "for durations in milliseconds (written as seconds when long), count (the default) "
            "for everything else. Values exactly as the tools returned them."
        ),
        (
            "highlight = the label to emphasise (e.g. the current month). compare = a second "
            "series for line/area/column (e.g. the previous month), same labels."
        ),
    ]),
    "schema": {
        "type": "object",
        "required": ["type", "title"],
        "properties": {
            "type": {
                "type": "string",
                "enum": ["bar", "column", "line", "area", "donut", "stacked", "kpi"],
            },
            "title": {
                "type": "string",
                "description": "Short, no unit, e.g. 'Orders per week'",
            },
            "subtitle": {
                "type": "string",
                "description": "Optional: period or filter, e.g. 'October 2026'",
            },
            "unit": {"type": "string", "enum": ["eur", "count", "pct", "ms"]},
            "name": {
                "type": "string",
                "description": "Name of the main series, when there is compare",
            },
            "points": {
                "type": "array",
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "required": ["label", "value"],
                    "properties": {"label": {"type": "string"}, "value": {"type": "number"}},
                },
            },
            "highlight": {"type": "string"},
            "compare": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "points": {
                        "type": "array",
                        "maxItems": 24,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "number"},
                            },
                        },
                    },
                },
            },
            "items": {
                "type": "array",
                "maxItems": 4,
                "description": "kpi only",
                "items": {
                    "type": "object",
                    "required": ["label", "value"],
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "number"},
                        "unit": {"type": "string", "enum": ["eur", "count", "pct", "ms"]},
                        "delta": {
                            "type": "number",
                            "description": "Change in % from the previous period",
                        },
                        "note": {"type": "string"},
                    },
                },
            },
        },
    },
}

DIAGRAM = {
    "name": "ui_diagram",
    "description": " ".join([
        "Show a diagram in the panel.",
        "steps = a process in 2-7 steps, each done, current or todo;",
        "tree = an item and its parts, one level, up to 8;",
        "compare = two columns compared row by row; rows that differ are highlighted.",
    ]),
    "schema": {
        "type": "object",
        "required": ["kind", "title"],
        "properties": {
            "kind": {"type": "string", "enum": ["steps", "tree", "compare"]},
            "title": {"type": "string"},
            "steps": {
                "type": "array",
                "maxItems": 7,
                "items": {
                    "type": "object",
                    "required": ["label"],
                    "properties": {
                        "label": {"type": "string"},
                        "note": {"type": "string"},
                        "state": {"type": "string", "enum": ["done", "current", "todo"]},
                    },
                },
            },
            "root": {
                "type": "object",
                "properties": {"label": {"type": "string"}, "note": {"type": "string"}},
            },
            "children": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "required": ["label"],
                    "properties": {"label": {"type": "string"}, "note": {"type": "string"}},
                },
            },
            "columns": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string"}},
            "rows": {
                "type": "array",
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "required": ["label"],
                    "properties": {
                        "label": {"type": "string"},
                        "left": {"type": "string"},
                        "right": {"type": "string"},
                    },
                },
            },
        },
    },
}

ASK = {
    "name": "ui_ask",
    "description": " ".join([
        (
            "Ask the user a short question with 2-5 answers to tap; the turn ends and their "
            "choice comes as their next message."
        ),
        (
            "Use it only when it is really needed: the request is ambiguous and you cannot infer "
            "it from the page or the tools,"
        ),
        (
            "or, after an answer, to offer the 2-3 natural next steps. Not on every turn. Never "
            "to confirm a change to data: the app already asks for that confirmation."
        ),
        (
            "multiple = several choices with a Confirm button; other = adds an 'Other...' option "
            "for a free answer."
        ),
    ]),
    "schema": {
        "type": "object",
        "required": ["question", "options"],
        "properties": {
            "question": {"type": "string", "description": "One sentence, e.g. 'Which month?'"},
            "options": {
                "type": "array",
                "minItems": 2,
                "maxItems": 5,
                "items": {"type": "string"},
                "description": "Short answers (max 40 characters)",
            },
            "multiple": {"type": "boolean"},
            "other": {"type": "boolean"},
        },
    },
}

REMEMBER = {
    "name": "memory_remember",
    "description": (
        "Remember something stable about the user for the next conversations (max 300 "
        "characters): a preference about how they like answers (kind preference), or a fact about "
        "their work context, such as the area or accounts they follow (kind semantic). Never "
        "personal data of customers, codes, secrets or instructions. When a remembered fact "
        "changed, pass its id as replaces."
    ),
    "schema": {
        "type": "object",
        "properties": {
            "fact": {"type": "string"},
            "kind": {"type": "string", "enum": ["preference", "semantic"]},
            "replaces": {"type": "string", "description": "Id of a memory this one replaces"},
        },
        "required": ["fact"],
    },
}

FORGET = {
    "name": "memory_forget",
    "description": "Forget a remembered fact, by id.",
    "schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
}

SKILL_LOAD = {
    "name": "skill_load",
    "description": (
        "Load the full instructions of a skill listed in the prompt, when the task needs it. "
        "They end with the list of the skill's files; read one of them by passing its path as "
        "file (a long file comes in pages: pass the offset the page names to read on)."
    ),
    "schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "file": {
                "type": "string",
                "description": "A file of the skill, like references/terms.md (omit for SKILL.md)",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "Where to start reading the file, in characters",
            },
        },
        "required": ["name"],
    },
}


HANDOFF = "handoff"


def handoff_tool(targets: list[tuple[str, str]]) -> dict[str, Any]:
    """The tool that hands the conversation to another agent.

    Args:
        targets: (name, title) of the agents the conversation may go to.

    Returns:
        The tool definition (name, description, schema).
    """
    names = [n for n, _ in targets]
    listed = "; ".join(f"{n}: {t}" if t and t != n else n for n, t in targets)
    return {
        "name": HANDOFF,
        "description": (
            "Hand the conversation to another agent better suited to the user's request; it "
            "answers from the user's next message, with the conversation so far. Agents: "
            f"{listed}. After calling it, tell the user in one short sentence who takes over; do "
            "not answer the request yourself."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "enum": names},
                "reason": {"type": "string", "description": "Why, in a few words."},
            },
            "required": ["agent", "reason"],
        },
    }


def local_tools(agent: AgentDef, has_skills: bool, *, memory: bool = True) -> list[dict[str, Any]]:
    """The client and memory tools an agent has.

    Args:
        agent: The agent definition.
        has_skills: Whether skill_load is offered.
        memory: Whether memories may be kept (only when Evokai stores the conversation).

    Returns:
        Tool definitions (name, description, schema).
    """
    ct = agent.client_tools
    tools: list[dict[str, Any]] = []
    if ct.navigate.enabled:
        tools.append({
            "name": "ui_navigate",
            "description": ct.navigate.description,
            "schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path of the page"}},
                "required": ["path"],
            },
        })
    if ct.chart:
        tools.append(CHART)
    if ct.diagram:
        tools.append(DIAGRAM)
    if ct.ask:
        tools.append(ASK)
    if ct.memory and memory:
        tools += [REMEMBER, FORGET]
    if has_skills:
        tools.append(SKILL_LOAD)
    return tools
