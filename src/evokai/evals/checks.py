"""The checks an eval case puts on an answer.

A check passes, fails, or is skipped (a rubric when the application has no judge model). A case
passes when no check failed and the turn did not end in an error.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, Field, TypeAdapter, field_validator


class Contains(BaseModel):
    """The answer contains a text."""

    type: Literal["contains"]
    value: str = Field(min_length=1, max_length=2_000)
    case_sensitive: bool = False


class NotContains(BaseModel):
    """The answer does not contain a text."""

    type: Literal["not_contains"]
    value: str = Field(min_length=1, max_length=2_000)
    case_sensitive: bool = False


class Regex(BaseModel):
    """The answer matches a regular expression (anywhere in it)."""

    type: Literal["regex"]
    pattern: str = Field(min_length=1, max_length=2_000)
    ignore_case: bool = False

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class JsonSchema(BaseModel):
    """The answer is JSON (whole, or in a code block) valid against a schema."""

    type: Literal["json_schema"]
    schema_: dict[str, Any] = Field(alias="schema")

    @field_validator("schema_")
    @classmethod
    def _valid(cls, v: dict[str, Any]) -> dict[str, Any]:
        Draft202012Validator.check_schema(v)
        return v


class ToolCalled(BaseModel):
    """The agent called a tool (optionally with these arguments)."""

    type: Literal["tool_called"]
    tool: str = Field(min_length=1, max_length=120)
    args: dict[str, Any] | None = None
    """Every key given must have this value in at least one call."""


class ToolNotCalled(BaseModel):
    """The agent did not call a tool."""

    type: Literal["tool_not_called"]
    tool: str = Field(min_length=1, max_length=120)


class Rubric(BaseModel):
    """A model judges the answer against a rubric (only when the application has a judge model)."""

    type: Literal["rubric"]
    rubric: str = Field(min_length=1, max_length=4_000)


Check = Annotated[
    Contains | NotContains | Regex | JsonSchema | ToolCalled | ToolNotCalled | Rubric,
    Field(discriminator="type"),
]
CHECKS: TypeAdapter[list[Check]] = TypeAdapter(list[Check])


def parse_checks(raw: list[dict[str, Any]]) -> list[Check]:
    """Validate a case's checks.

    Returns:
        The checks.

    Raises:
        ValueError: A check that is not valid (pydantic's error), or an invalid regex or schema.
    """
    try:
        return CHECKS.validate_python(raw)
    except (re.error, SchemaError) as e:
        raise ValueError(str(e)) from e


def json_in(text: str) -> Any:
    """The JSON an answer holds: the whole text, a fenced block, or the first object or array.

    Returns:
        The value.

    Raises:
        ValueError: No JSON in it.
    """
    candidates = [text.strip()]
    candidates += re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m := re.search(r"[\[{].*[\]}]", text, re.S):
        candidates.append(m.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except ValueError:
            continue
    raise ValueError("the answer holds no JSON")


Judge = Callable[[str, str, str], Awaitable[tuple[bool, str]]]
"""(rubric, input, answer) → (passed, reason)."""


async def run_checks(
    checks: list[Check],
    *,
    answer: str,
    calls: list[dict[str, Any]],
    question: str,
    judge: Judge | None,
) -> list[dict[str, Any]]:
    """Every check's outcome.

    Args:
        checks: The case's checks.
        answer: The text the agent answered.
        calls: The tool calls it made: ``{"name", "args"}``.
        question: The case's input (for the judge).
        judge: The judge of rubrics, None when the application has none.

    Returns:
        One dict per check: the check, ``outcome`` (passed, failed, skipped) and ``detail``.
    """
    out: list[dict[str, Any]] = []
    names = [c["name"] for c in calls]
    for check in checks:
        spec = check.model_dump(by_alias=True)
        outcome, detail = "failed", ""
        match check:
            case Contains() | NotContains():
                hay, needle = (
                    (answer, check.value)
                    if check.case_sensitive
                    else (answer.casefold(), check.value.casefold())
                )
                found = needle in hay
                ok = found if isinstance(check, Contains) else not found
                outcome = "passed" if ok else "failed"
                detail = "" if ok else ("not in the answer" if not found else "in the answer")
            case Regex():
                flags = re.I if check.ignore_case else 0
                ok = re.search(check.pattern, answer, flags) is not None
                outcome, detail = ("passed", "") if ok else ("failed", "no match")
            case JsonSchema():
                try:
                    value = json_in(answer)
                    errors = list(Draft202012Validator(check.schema_).iter_errors(value))
                    if errors:
                        detail = "; ".join(
                            f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
                            for e in errors[:5]
                        )
                    else:
                        outcome = "passed"
                except ValueError as e:
                    detail = str(e)
            case ToolCalled():
                hits = [c for c in calls if c["name"] == check.tool]
                if check.args:
                    hits = [
                        c
                        for c in hits
                        if all((c.get("args") or {}).get(k) == v for k, v in check.args.items())
                    ]
                outcome = "passed" if hits else "failed"
                detail = "" if hits else f"called: {', '.join(names) or 'nothing'}"
            case ToolNotCalled():
                ok = check.tool not in names
                outcome, detail = ("passed", "") if ok else ("failed", "it was called")
            case Rubric():
                if judge is None:
                    outcome, detail = "skipped", "no judge model set for this application"
                else:
                    try:
                        ok, why = await judge(check.rubric, question, answer)
                        outcome, detail = ("passed" if ok else "failed"), why[:500]
                    except Exception as e:
                        outcome, detail = (
                            "skipped",
                            f"the judge did not answer ({type(e).__name__})",
                        )
        out.append({"check": spec, "outcome": outcome, "detail": detail})
    return out
