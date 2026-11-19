"""Workflow definitions: the safe expression language, templates, cron and validation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evokai.workflows import definitions as wd
from evokai.workflows import expressions as ex
from evokai.workflows.cron import Cron, CronError, zone

CTX = {
    "input": {"period": "2026-10", "amount": 12.5, "ids": [1, 2, 3], "name": "Ada"},
    "steps": {"triage": {"output": {"category": "order", "priority": 2}, "status": "done"}},
    "run": {"id": "r1"},
}


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("steps.triage.output.priority >= 2", True),
        ("steps.triage.output.category == 'order' and input.amount > 10", True),
        ("not (input.amount < 10) or false", True),
        ("input.ids | length", 3),
        ("input.ids[0] + input.ids[-1]", 4),
        ("input['period']", "2026-10"),
        ("input.name | upper", "ADA"),
        ("'Hello ' + input.name", "Hello Ada"),
        ("input.missing.deep", None),
        ("input.missing | default('none')", "none"),
        ("2 in input.ids", True),
        ("'ord' in steps.triage.output.category", True),
        ("4 not in input.ids", True),
        ("input.amount * 2 | round", 25),
        ("input.ids | sum", 6),
        ("(input.period + '-01') | date('%m/%Y')", "10/2026"),
        ("(10 % 4) - -1", 3),
        ("[1, 'a', null]", [1, "a", None]),
        ("steps.unknown.output == null", True),
        ("input.ids | join('-')", "1-2-3"),
    ],
)
def test_expressions_evaluate_over_json_values(expr: str, value: object) -> None:
    assert ex.evaluate_text(expr, CTX) == value


@pytest.mark.parametrize(
    "attack",
    [
        "__import__('os').system('id')",
        "input.__class__",
        "input.__class__.__mro__[1].__subclasses__()",
        "input['__globals__']",
        "().__class__",
        "''.join(['a'])",
        "eval('1+1')",
        "exec('x=1')",
        "getattr(input, 'x')",
        "open('/etc/passwd')",
        "lambda: 1",
        "input.name()",
        "input | attr('__class__')",
        "input.name.upper()",
        "[x for x in input.ids]",
        "os.environ",
        "{{ input.name }}",
        "input.name if true else 1",
        "a = 1",
        "input; 1",
        "`id`",
        "$(id)",
        "${input}",
        "(" * 40 + "1" + ")" * 40,
        "1 + " * 300 + "1",
    ],
)
def test_code_injection_is_refused(attack: str) -> None:
    with pytest.raises(ex.ExpressionError):
        ex.evaluate_text(attack, CTX)


def test_values_cannot_grow_without_bound_or_reach_python_objects() -> None:
    with pytest.raises(ex.ExpressionError):
        ex.evaluate_text("'a' * 100000000", CTX)
    with pytest.raises(ex.ExpressionError):
        ex.evaluate_text("1 / 0", CTX)
    with pytest.raises(ex.ExpressionError):
        ex.evaluate_text("input.name < 3", CTX)
    # A key written in the data cannot open an underscore name either.
    with pytest.raises(ex.ExpressionError):
        ex.evaluate_text("input[k]", {"input": {}, "k": "__class__"})


def test_templates_keep_types_and_never_evaluate_what_values_contain() -> None:
    assert ex.render("{{ input.ids }}", CTX) == [1, 2, 3]
    assert ex.render("Period {{ input.period }}, {{ input.ids | length }} items", CTX) == (
        "Period 2026-10, 3 items"
    )
    assert ex.render({"a": ["{{ input.amount }}", "x"]}, CTX) == {"a": [12.5, "x"]}
    # A message body that carries a template is data: rendering is a single pass.
    sneaky = {"input": {"body": "{{ run.id }} {{ __import__('os') }}"}, "run": {"id": "secret"}}
    assert ex.render("Message: {{ input.body }}", sneaky) == (
        "Message: {{ run.id }} {{ __import__('os') }}"
    )
    # Other template languages are plain text.
    assert ex.render("{% for x in y %}{{ input.name }}{% endfor %}", CTX) == (
        "{% for x in y %}Ada{% endfor %}"
    )
    problems = ex.check_template({"q": "{{ input.name( }}", "ok": "{{ input.name }}"})
    assert [path for path, _ in problems] == ["q"]


def test_cron_reads_wall_clock_time_in_the_zone() -> None:
    rome = zone("Europe/Rome")
    c = Cron.parse("0 9 1 * *")
    winter = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert c.previous(winter, rome) == datetime(2026, 11, 1, 8, tzinfo=UTC)  # 9:00 CET
    assert c.next(winter, rome) == datetime(2026, 12, 1, 8, tzinfo=UTC)
    summer = datetime(2026, 7, 2, tzinfo=UTC)
    assert c.previous(summer, rome) == datetime(2026, 7, 1, 7, tzinfo=UTC)  # 9:00 CEST
    weekdays = Cron.parse("*/15 8-18 * * mon-fri")
    saturday = datetime(2026, 11, 14, 10, tzinfo=UTC)
    assert weekdays.next(saturday, rome) == datetime(2026, 11, 16, 7, tzinfo=UTC)
    assert Cron.parse("@daily").next(winter, zone("UTC")) == datetime(2026, 11, 17, tzinfo=UTC)
    for bad in ["* * *", "61 * * * *", "* * * * mon-xyz", "*/0 * * * *", "5-1 * * * *"]:
        with pytest.raises(CronError):
            Cron.parse(bad)
    with pytest.raises(CronError):
        zone("Mars/Olympus")


TRIAGE_YAML = """
name: mail-triage
title: Mail triage
input_schema:
  type: object
  required: [text]
  properties: {text: {type: string}}
steps:
  - id: classify
    kind: agent
    agent: triage
    input: "Message: {{ input.text }}"
  - id: route
    kind: condition
    if: "steps.classify.output.category == 'order'"
    then: order
    else: technical
  - id: order
    kind: agent
    agent: order
    input: "{{ input.text }}"
    next: approve
  - id: technical
    kind: agent
    agent: technical
    input: "{{ input.text }}"
  - id: approve
    kind: approval
    question: "Send the reply?"
  - id: send
    kind: tool
    server: back
    tool: reply
    approval: approve
    arguments: {text: "{{ steps.order.output.reply }}"}
triggers:
  - kind: schedule
    cron: "0 8 * * mon-fri"
"""


def test_a_valid_definition_parses_with_a_version_and_an_outline() -> None:
    defn, problems, raw = wd.parse_text(TRIAGE_YAML)
    assert problems == [] and defn is not None
    assert len(defn.version) == 12
    again, _, _ = wd.parse_text(TRIAGE_YAML)
    assert again is not None and again.version == defn.version
    flow = wd.outline(defn, raw)
    assert [s["id"] for s in flow] == [
        "classify",
        "route",
        "order",
        "technical",
        "approve",
        "send",
    ]
    assert flow[1]["then"] == "order" and flow[1]["else"] == "technical"


@pytest.mark.parametrize(
    ("change", "path", "fragment"),
    [
        (("then: order", "then: classify"), "steps[1].then", "does not come after"),
        (("next: approve", "next: nowhere"), "steps[2].next", "unknown step nowhere"),
        (("id: technical", "id: order"), "steps[3]", "duplicate step id"),
        (("approval: approve", "approval: route"), "steps[5].approval", "not an approval step"),
        (('if: "steps', 'if: "steps..'), "steps[1].if", "expected"),
        (
            ('{{ input.text }}"\n  - id: route', '{{ input.text "\n  - id: route'),
            "steps[0].input",
            "'{{'",
        ),
        (('cron: "0 8 * * mon-fri"', 'cron: "0 25 * * *"'), "triggers[0].cron", "outside"),
        (("kind: approval", "kind: magic"), "steps[4]", "kind"),
    ],
)
def test_problems_point_at_the_path_and_line(
    change: tuple[str, str], path: str, fragment: str
) -> None:
    text = TRIAGE_YAML.replace(*change, 1)
    defn, problems, _ = wd.parse_text(text)
    assert defn is None
    hit = [p for p in problems if p.path.startswith(path)]
    assert hit, problems
    assert any(fragment in p.message for p in hit), hit
    assert all(p.line for p in hit)


def test_yaml_syntax_errors_carry_their_line() -> None:
    defn, problems, _ = wd.parse_text("name: x\nsteps:\n  - id: a\n   kind: wait")
    assert defn is None and problems[0].line == 4


def test_branches_jump_only_inside_their_own_list() -> None:
    raw = {
        "name": "fan",
        "steps": [
            {
                "id": "fan",
                "kind": "parallel",
                "branches": [
                    {"name": "a", "steps": [{"id": "w1", "kind": "wait", "seconds": 1}]},
                    {
                        "name": "b",
                        "steps": [{"id": "w2", "kind": "wait", "seconds": 1, "next": "w1"}],
                    },
                ],
            }
        ],
    }
    defn, problems = wd.parse(raw)
    assert defn is None
    assert problems[0].path == "steps[0].branches[1].steps[0].next"


def test_reserved_names_and_wait_without_a_duration_are_refused() -> None:
    _, problems = wd.parse({"name": "runs", "steps": [{"id": "w", "kind": "wait", "seconds": 1}]})
    assert problems and problems[0].path == "name"
    _, problems = wd.parse({"name": "ok", "steps": [{"id": "w", "kind": "wait"}]})
    assert problems and "seconds or until" in problems[0].message


def test_the_examples_of_the_docs_are_valid_definitions() -> None:
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs" / "workflows.md").read_text(
        encoding="utf-8"
    )
    blocks = [
        b
        for b in re.findall(r"```yaml\n(.*?)```", text, re.S)
        if b.startswith("name:") and "- id:" in b
    ]
    assert len(blocks) == 3
    for block in blocks:
        defn, problems, _ = wd.parse_text(block)
        assert problems == [] and defn is not None, problems
