"""Ask rules: ordered rules that make a tool call wait for the person's confirmation.

Borrowed from OpenCode's permissions, with one limit: a rule can only **add** a confirmation. A
tool that changes data (MCP ``destructiveHint``, or not ``readOnlyHint``, or the guard's
``tools.confirm``) always waits for the person, whatever the rules say; a rule can make a read-only
tool, or a call with certain arguments, wait too.

A rule is ``{action, resource, effect}``:

- ``action``: the tool's name, a whole-value pattern where ``*`` is any run of characters and
  ``?`` one character (``crm_*``, ``*_export``);
- ``argument`` and ``resource``: a pattern over one argument the tool declares (``customer_code``,
  ``filters.region``); ``part: host`` matches the host of a URL in that argument instead of the
  whole value. A list matches when one of its items does. ``resource: "*"`` (the default) matches
  every call of the tool;
- ``effect``: ``ask`` (wait for the person) or ``default`` (a narrower exception: it cancels an
  ``ask`` above it in the same list, never one of another level and never the confirmation of a
  tool that changes data). ``allow`` and ``deny`` are refused: allow and deny lists belong to the
  security policy, and a rule never removes a confirmation.

Optional: ``id`` (shown in traces), ``label`` (``{en, it}``: why the person is asked, shown on the
confirmation) and ``tags`` (patterns over the subject's tags, e.g. ``team:ops``: the rule applies
only to people with one of them).

Levels: the deployment's defaults (``EVOKAI_ASK_RULES``), the application's
(``PUT /v1/admin/ask-rules``, the console's Permissions page), the agent's (``ask_rules`` in its
definition). Each level is read on its own, and in a level **the last rule that matches a call
wins**; a call waits when any level says ``ask``. So a lower level can only add confirmations: it
can never lift an ``ask`` of a higher one. A ``default`` rule with no ``ask`` above it in its own
list that could match the same tools would only make sense as lifting another level, so it is
refused when the rules are saved (``check_rules``); one that slipped through does nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

LEVELS = ("deployment", "application", "agent")
EFFECTS = ("ask", "default")
REASON_RULE = "confirm.why.rule"
"""The i18n key of a confirmation asked by a rule (clients show the rule's label with it)."""
REASON_WRITES = "confirm.why.writes"
"""The i18n key of a confirmation of a tool that changes data."""
REASON_POLICY = "confirm.why.policy"
"""The i18n key of a confirmation the security policy forces (``tools.confirm``)."""

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,59}$")
_ARG = re.compile(r"^[A-Za-z0-9_$-]+(\.[A-Za-z0-9_$-]+)*$")


@cache
def _pattern(p: str) -> re.Pattern[str]:
    """A whole-value pattern: ``*`` any run of characters, ``?`` one, everything else literal.

    Returns:
        The compiled regular expression.
    """
    out = "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in p)
    return re.compile(rf"\A{out}\Z", re.S)


def wildcard(pattern: str, value: str) -> bool:
    """Whether a value matches a whole-value pattern (``*`` and ``?``).

    Returns:
        True on a match.
    """
    return bool(_pattern(pattern).match(value))


class AskRule(BaseModel):
    """One rule (see the module)."""

    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=60)
    """A short name for traces and the console (default ``<level>:<position>``)."""
    action: str = Field(min_length=1, max_length=200)
    """The tool's name, a whole-value pattern (``*``, ``?``)."""
    argument: str | None = Field(default=None, max_length=200)
    """The argument ``resource`` is matched against (dots reach into objects)."""
    part: Literal["value", "host"] = "value"
    """What of the argument is matched: the whole value, or the host of a URL."""
    resource: str = Field(default="*", min_length=1, max_length=500)
    """A whole-value pattern over the argument (``*``: every call of the tool)."""
    effect: Literal["ask", "default"] = "ask"
    label: dict[str, str] = Field(default_factory=dict)
    """Why the person is asked, per language (``en``, ``it``), shown on the confirmation."""
    tags: list[str] = Field(default_factory=list, max_length=20)
    """Patterns over the subject's tags: the rule applies only when one of them matches."""

    @field_validator("effect", mode="before")
    @classmethod
    def _effect(cls, v: Any) -> Any:
        if isinstance(v, str) and v not in EFFECTS:
            raise ValueError(
                f"effect {v!r} is not allowed: a rule can only ask for the person's confirmation "
                "(ask) or leave the tool as it is (default). A rule never removes the confirmation "
                "of a tool that changes data; allow and deny lists are in the security policy."
            )
        return v

    @field_validator("id")
    @classmethod
    def _id(cls, v: str | None) -> str | None:
        if v is not None and not _ID.match(v):
            raise ValueError("rule id: letters, digits and _ . : - only")
        return v

    @field_validator("argument")
    @classmethod
    def _argument(cls, v: str | None) -> str | None:
        v = (v or "").strip() or None
        if v is not None and not _ARG.match(v):
            raise ValueError("argument: a name, or names joined by dots for nested values")
        return v

    @field_validator("label")
    @classmethod
    def _label(cls, v: dict[str, str]) -> dict[str, str]:
        out = {k: t.strip() for k, t in v.items() if t and t.strip()}
        if bad := [k for k in out if k not in ("en", "it")]:
            raise ValueError(f"label languages are en and it, not {bad}")
        if any(len(t) > 200 for t in out.values()):
            raise ValueError("a label has at most 200 characters")
        return out

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(t.strip() for t in v if t.strip()))

    @model_validator(mode="after")
    def _resource_needs_argument(self) -> AskRule:
        if self.resource != "*" and self.argument is None:
            raise ValueError("a resource other than * needs the argument it is matched against")
        if self.part == "host" and self.argument is None:
            raise ValueError("part: host needs the argument that holds the URL")
        return self


RULES = TypeAdapter(list[AskRule])


def parse_rules(raw: Any) -> list[AskRule]:
    """Rules as stored or written (a list of objects).

    A rule that is not valid (an effect other than ask or default among them) raises pydantic's
    ``ValidationError``.

    Returns:
        The rules.
    """
    if not raw:
        return []
    return RULES.validate_python(raw)


def explain(e: ValidationError) -> str:
    """A rules' validation error as one clear sentence per problem, with the rule's position.

    Returns:
        The text (English, for the console and the API).
    """
    return explain_errors(list(e.errors()))


def explain_errors(errors: list[Any]) -> str:
    """Validation errors of rules as one clear sentence per problem (see :func:`explain`).

    Returns:
        The text.
    """
    out = []
    for err in errors[:5]:
        loc = list(err.get("loc", ()))
        if "ask_rules" in loc:
            loc = loc[loc.index("ask_rules") + 1 :]
        where = f"rule {int(loc[0]) + 1}" if loc and isinstance(loc[0], int) else "rules"
        field = ".".join(str(p) for p in loc[1:] if not isinstance(p, int))
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        out.append(f"{where}{f' ({field})' if field else ''}: {msg}")
    return "; ".join(out)


def overlap(a: str, b: str) -> bool:
    """Whether two whole-value patterns (``*``, ``?``) can match the same name.

    Returns:
        True when some name matches both.
    """

    @cache
    def ov(i: int, j: int) -> bool:
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and a[i] == "*":
            return ov(i + 1, j) or (j < len(b) and ov(i, j + 1))
        if j < len(b) and b[j] == "*":
            return ov(i, j + 1) or (i < len(a) and ov(i + 1, j))
        if i < len(a) and j < len(b) and (a[i] == b[j] or "?" in (a[i], b[j])):
            return ov(i + 1, j + 1)
        return False

    return ov(0, 0)


def check_rules(rules: list[AskRule], mutating: set[str]) -> list[str]:
    """Rules of one level that read as removing a confirmation they cannot remove.

    - a ``default`` rule that names a tool that changes data exactly (no wildcard): such a
      confirmation is never removed;
    - a ``default`` rule with no ``ask`` rule above it in the same list that could match the same
      tools: it could only lift an ``ask`` of a higher level, which a rule never does.

    Args:
        rules: The rules of one level, in order.
        mutating: The names of the tools known to change data.

    Returns:
        One problem per such rule (empty when none).
    """
    out = []
    for i, r in enumerate(rules):
        if r.effect != "default":
            continue
        if not any(c in r.action for c in "*?") and r.action in mutating:
            out.append(
                f"rule {i + 1}: {r.action} changes data, its confirmation cannot be removed (a "
                "rule can only add a confirmation)"
            )
        elif not any(x.effect == "ask" and overlap(x.action, r.action) for x in rules[:i]):
            out.append(
                f"rule {i + 1}: default only cancels an ask above it in the same list; it never "
                "lifts an ask of the deployment or the application (a rule can only add a "
                "confirmation)"
            )
    return out


def stored(rules: list[AskRule]) -> list[dict[str, Any]]:
    """Rules as they are stored (defaults left out).

    Returns:
        JSON-able dicts.
    """
    return [
        r.model_dump(mode="json", exclude_defaults=True) | {"action": r.action, "effect": r.effect}
        for r in rules
    ]


@dataclass(frozen=True)
class Placed:
    """A rule at its level and position."""

    rule: AskRule
    level: str
    index: int

    @property
    def id(self) -> str:
        """The rule's id, or ``<level>:<position>`` (1-based).

        Returns:
            The id.
        """
        return self.rule.id or f"{self.level}:{self.index + 1}"


@dataclass(frozen=True)
class AskMatch:
    """The rule that makes a call wait for the person."""

    placed: Placed

    @property
    def rule(self) -> str:
        """The rule's id.

        Returns:
            The id.
        """
        return self.placed.id

    @property
    def level(self) -> str:
        """Where the rule was set.

        Returns:
            ``deployment``, ``application`` or ``agent``.
        """
        return self.placed.level

    def label(self, locale: str) -> str:
        """Why the person is asked, in a language (else English, else the other one).

        Returns:
            The label, or an empty text.
        """
        lab = self.placed.rule.label
        return lab.get(locale.split("-")[0]) or lab.get("en") or next(iter(lab.values()), "")

    def reason(self, locale: str) -> dict[str, Any]:
        """The reason carried by a ``confirm`` event.

        Returns:
            ``{key, rule, level, label?}``.
        """
        out: dict[str, Any] = {"key": REASON_RULE, "rule": self.rule, "level": self.level}
        if lab := self.label(locale):
            out["label"] = lab
        return out


def _values(args: Any, path: str) -> list[Any]:
    """The values at a dotted path of the arguments (a list contributes each of its items).

    Returns:
        The values found (none when the argument is absent).
    """
    current: list[Any] = [args]
    for key in path.split("."):
        nxt: list[Any] = []
        for v in current:
            if isinstance(v, dict) and key in v:
                x = v[key]
                nxt.extend(x if isinstance(x, list) else [x])
            elif isinstance(v, list):
                nxt.extend(i[key] for i in v if isinstance(i, dict) and key in i)
        current = nxt
    return [v for v in current if v is not None and not isinstance(v, (dict, list))]


def _host(value: str) -> str:
    v = value.strip()
    try:
        return (urlsplit(v if "://" in v else f"https://{v}").hostname or "").lower()
    except ValueError:
        return ""


def _text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


@dataclass
class AskRules:
    """The rules that apply to one turn or run (every level, for its subject's tags)."""

    rules: list[Placed] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        *,
        deployment: Any = None,
        application: Any = None,
        agent: Any = None,
        tags: list[str] | None = None,
    ) -> AskRules:
        """The rules of every level, in order (a level that fails to parse is left out).

        Returns:
            The rules.
        """
        placed: list[Placed] = []
        for level, raw in zip(LEVELS, (deployment, application, agent), strict=True):
            try:
                rules = parse_rules(raw)
            except ValueError:
                # Rules are checked when they are saved; a stored rule that no longer parses must
                # not stop turns (it adds nothing).
                continue
            placed += [Placed(r, level, i) for i, r in enumerate(rules)]
        return cls(placed, list(tags or []))

    def with_agent(self, agent: Any) -> AskRules:
        """The same deployment and application rules with another agent's (a subagent).

        Returns:
            The rules.
        """
        base = [p for p in self.rules if p.level != "agent"]
        try:
            own = parse_rules(getattr(agent, "ask_rules", None))
        except ValueError:
            own = []
        return AskRules(base + [Placed(r, "agent", i) for i, r in enumerate(own)], self.tags)

    def __bool__(self) -> bool:
        """Whether any rule applies.

        Returns:
            True when there is at least one rule.
        """
        return bool(self.rules)

    def _applies(self, p: Placed, name: str) -> bool:
        r = p.rule
        if not wildcard(r.action, name):
            return False
        return not r.tags or any(wildcard(pat, t) for pat in r.tags for t in self.tags)

    def _level(self, level: str) -> list[Placed]:
        return [p for p in self.rules if p.level == level]

    def _matches(self, p: Placed, name: str, args: dict[str, Any]) -> bool:
        if not self._applies(p, name):
            return False
        r = p.rule
        if r.resource == "*" and r.part == "value":
            return True
        assert r.argument is not None  # ruff: ignore[assert] (checked by the model)
        found = _values(args, r.argument)
        texts = [_host(_text(v)) if r.part == "host" else _text(v) for v in found]
        return any(wildcard(r.resource, t) for t in texts if t)

    def match(self, name: str, args: dict[str, Any]) -> AskMatch | None:
        """The rule that makes this call wait for the person, if any.

        Each level on its own: its last matching rule decides for it. The call waits when any
        level says ``ask``; the highest such level is reported (deployment, application, agent).

        Returns:
            The match, or None when the call follows the tool's own behaviour.
        """
        for level in LEVELS:
            last = None
            for p in self._level(level):
                if self._matches(p, name, args):
                    last = p
            if last is not None and last.rule.effect == "ask":
                return AskMatch(last)
        return None

    def askable(self, name: str) -> bool:
        """Whether some call of this tool may wait for the person (whatever its arguments).

        Such a tool is no longer read-only for Code Mode, subagents and server runs: it leaves
        ``run_code`` and subagents and stays a normal call, with its confirmation.

        Returns:
            True when a rule of some level could ask.
        """
        for level in LEVELS:
            maybe = False
            for p in self._level(level):
                if not self._applies(p, name):
                    continue
                if p.rule.resource == "*" and p.rule.part == "value":
                    maybe = p.rule.effect == "ask"
                elif p.rule.effect == "ask":
                    maybe = True
            if maybe:
                return True
        return False

    def view(self) -> list[dict[str, Any]]:
        """Every rule with its level and id, in order (the console).

        Returns:
            Rules.
        """
        return [
            {**p.rule.model_dump(mode="json"), "level": p.level, "ruleId": p.id} for p in self.rules
        ]
