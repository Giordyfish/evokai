"""A tiny, safe expression language for workflow conditions and templates.

Workflows never run code. Conditions (``if: steps.triage.output.priority >= 2``) and templates
(``"Order {{ input.order_id }}"``) are parsed by a hand-written parser into a small tree and
evaluated over plain JSON values (objects, lists, strings, numbers, booleans and null):

- **values**: numbers, ``'strings'`` or ``"strings"``, ``true``, ``false``, ``null``, lists
  ``[a, b]``;
- **names**: the roots ``input``, ``steps``, ``run``, ``step``, ``trigger``, ``event`` and ``now``,
  then ``.key`` or ``[index]`` on objects and lists only (a missing key or index is ``null``); a
  name that starts with an underscore is refused, and nothing reaches a Python attribute;
- **operators**: ``== != < <= > >=``, ``in``, ``not in``, ``and``, ``or``, ``not``, ``+ - * / %``
  on numbers, ``+`` on strings and lists, parentheses;
- **filters**: ``value | name`` or ``value | name(arg, ...)`` from a fixed list (``FILTERS``).

There is no call syntax other than filters, no attribute access, no indexing of strings beyond
their characters, no loops. Sizes are bounded: an expression is at most ``MAX_EXPRESSION`` long
and ``MAX_DEPTH`` deep, and a string or list built while evaluating at most ``MAX_VALUE`` long.

A template is a string with ``{{ expression }}`` parts. A string that is exactly one part keeps the
value's type (an object, a number); otherwise each part is printed (objects and lists as JSON).
Rendering is a single pass: text that comes from a value (a ticket's body that contains
``{{ ... }}``) is never evaluated.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any

MAX_EXPRESSION = 500
MAX_DEPTH = 24
MAX_VALUE = 100_000
ROOTS = frozenset({"input", "steps", "run", "step", "trigger", "event", "now"})
"""The names an expression may start from."""

_TEMPLATE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>==|!=|<=|>=|<|>|\(|\)|\[|\]|\.|,|\||\+|-|\*|/|%)
    """,
    re.VERBOSE,
)
_KEYWORDS = frozenset({"and", "or", "not", "in", "true", "false", "null"})


class ExpressionError(ValueError):
    """An expression that cannot be parsed or evaluated."""


Node = tuple[Any, ...]


def _tokens(text: str) -> list[tuple[str, str]]:
    if len(text) > MAX_EXPRESSION:
        raise ExpressionError(f"expression longer than {MAX_EXPRESSION} characters")
    out: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise ExpressionError(f"unexpected character {text[pos]!r} at {pos + 1}")
        pos = m.end()
        kind = m.lastgroup or ""
        if kind == "ws":
            continue
        value = m.group()
        if kind == "name" and value in _KEYWORDS:
            kind = "kw"
        out.append((kind, value))
    return out


def _unquote(raw: str) -> str:
    body = raw[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)


class _Parser:
    """Recursive descent over the tokens; builds tuples, never code."""

    def __init__(self, text: str) -> None:
        self.toks = _tokens(text)
        self.i = 0
        self.depth = 0

    def peek(self, value: str | None = None, kind: str | None = None) -> bool:
        if self.i >= len(self.toks):
            return False
        k, v = self.toks[self.i]
        return (value is None or v == value) and (kind is None or k == kind)

    def take(self, value: str | None = None, kind: str | None = None) -> tuple[str, str]:
        if not self.peek(value, kind):
            got = self.toks[self.i][1] if self.i < len(self.toks) else "the end"
            want = value or kind or "a value"
            raise ExpressionError(f"expected {want}, found {got!r}")
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def nest(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExpressionError("expression nested too deeply")

    def parse(self) -> Node:
        if not self.toks:
            raise ExpressionError("empty expression")
        node = self.or_()
        if self.i != len(self.toks):
            raise ExpressionError(f"unexpected {self.toks[self.i][1]!r}")
        return node

    def or_(self) -> Node:
        self.nest()
        node = self.and_()
        while self.peek("or", "kw"):
            self.take()
            node = ("or", node, self.and_())
        self.depth -= 1
        return node

    def and_(self) -> Node:
        node = self.not_()
        while self.peek("and", "kw"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self) -> Node:
        if self.peek("not", "kw"):
            self.take()
            self.nest()
            node = ("not", self.not_())
            self.depth -= 1
            return node
        return self.cmp()

    def cmp(self) -> Node:
        left = self.add()
        for op in ("==", "!=", "<=", ">=", "<", ">"):
            if self.peek(op, "op"):
                self.take()
                return ("cmp", op, left, self.add())
        if self.peek("in", "kw"):
            self.take()
            return ("in", left, self.add())
        if (
            self.peek("not", "kw")
            and self.i + 1 < len(self.toks)
            and self.toks[self.i + 1][1] == "in"
        ):
            self.take()
            self.take()
            return ("not", ("in", left, self.add()))
        return left

    def add(self) -> Node:
        node = self.mul()
        while self.peek("+", "op") or self.peek("-", "op"):
            op = self.take()[1]
            node = ("arith", op, node, self.mul())
        return node

    def mul(self) -> Node:
        node = self.unary()
        while self.peek("*", "op") or self.peek("/", "op") or self.peek("%", "op"):
            op = self.take()[1]
            node = ("arith", op, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.peek("-", "op"):
            self.take()
            self.nest()
            node = ("neg", self.unary())
            self.depth -= 1
            return node
        return self.pipe()

    def pipe(self) -> Node:
        node = self.postfix()
        while self.peek("|", "op"):
            self.take()
            name = self.take(kind="name")[1]
            if name not in FILTERS:
                raise ExpressionError(f"unknown filter {name!r}")
            args: list[Node] = []
            if self.peek("(", "op"):
                self.take()
                if not self.peek(")", "op"):
                    args.append(self.or_())
                    while self.peek(",", "op"):
                        self.take()
                        args.append(self.or_())
                self.take(")")
            node = ("filter", name, node, tuple(args))
        return node

    def postfix(self) -> Node:
        node = self.primary()
        while True:
            if self.peek(".", "op"):
                self.take()
                tok = self.take()
                if tok[0] not in {"name", "kw"}:
                    raise ExpressionError(f"expected a name after '.', found {tok[1]!r}")
                _check_name(tok[1])
                node = ("get", node, ("lit", tok[1]))
            elif self.peek("[", "op"):
                self.take()
                key = self.or_()
                self.take("]")
                node = ("get", node, key)
            else:
                return node

    def primary(self) -> Node:
        kind, value = self.take()
        if kind == "num":
            return ("lit", float(value) if "." in value else int(value))
        if kind == "str":
            return ("lit", _unquote(value))
        if kind == "kw" and value in {"true", "false", "null"}:
            return ("lit", {"true": True, "false": False, "null": None}[value])
        if kind == "name":
            _check_name(value)
            if value not in ROOTS:
                raise ExpressionError(
                    f"unknown name {value!r} (use one of {', '.join(sorted(ROOTS))})"
                )
            return ("var", value)
        if value == "(":
            self.nest()
            node = self.or_()
            self.depth -= 1
            self.take(")")
            return node
        if value == "[":
            items: list[Node] = []
            if not self.peek("]", "op"):
                items.append(self.or_())
                while self.peek(",", "op"):
                    self.take()
                    items.append(self.or_())
            self.take("]")
            return ("list", tuple(items))
        raise ExpressionError(f"unexpected {value!r}")


def _check_name(name: str) -> None:
    if name.startswith("_"):
        raise ExpressionError(f"names starting with an underscore are not allowed: {name!r}")


@lru_cache(maxsize=2048)
def compile_expression(text: str) -> Node:
    """Parse an expression.

    Returns:
        Its tree (cached); an invalid expression raises ``ExpressionError``.
    """
    return _Parser(text.strip()).parse()


# evaluation


def _bounded(value: Any) -> Any:
    if isinstance(value, (str, list)) and len(value) > MAX_VALUE:
        raise ExpressionError(f"a value longer than {MAX_VALUE} was built")
    return value


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _get(container: Any, key: Any) -> Any:
    if isinstance(container, dict):
        if not isinstance(key, str):
            key = str(key)
        if key.startswith("_"):
            raise ExpressionError(f"names starting with an underscore are not allowed: {key!r}")
        return container.get(key)
    if isinstance(container, (list, str)) and _number(key) and float(key).is_integer():
        i = int(key)
        return container[i] if -len(container) <= i < len(container) else None
    return None


def _arith(op: str, a: Any, b: Any) -> Any:
    if op == "+":
        if isinstance(a, str) and isinstance(b, str):
            return _bounded(a + b)
        if isinstance(a, list) and isinstance(b, list):
            return _bounded(a + b)
    if not (_number(a) and _number(b)):
        raise ExpressionError(f"'{op}' needs numbers")
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if b == 0:
        raise ExpressionError("division by zero")
    return a / b if op == "/" else a % b


def _compare(op: str, a: Any, b: Any) -> bool:
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if a is None or b is None:
        return False
    if not ((_number(a) and _number(b)) or (isinstance(a, str) and isinstance(b, str))):
        raise ExpressionError(f"'{op}' compares two numbers or two strings")
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def truthy(value: Any) -> bool:
    """Whether a value counts as true (empty strings, lists, objects, 0 and null do not).

    Returns:
        True or False.
    """
    return bool(value)


def evaluate(node: Node, ctx: dict[str, Any]) -> Any:
    """Evaluate a parsed expression over a context of JSON values.

    Returns:
        The value.

    Raises:
        ExpressionError: A type error, a division by zero, or a value too large.
    """
    tag = node[0]
    if tag == "lit":
        return node[1]
    if tag == "var":
        return ctx.get(node[1])
    if tag == "get":
        return _get(evaluate(node[1], ctx), evaluate(node[2], ctx))
    if tag == "list":
        return _bounded([evaluate(x, ctx) for x in node[1]])
    if tag == "and":
        left = evaluate(node[1], ctx)
        return evaluate(node[2], ctx) if truthy(left) else left
    if tag == "or":
        left = evaluate(node[1], ctx)
        return left if truthy(left) else evaluate(node[2], ctx)
    if tag == "not":
        return not truthy(evaluate(node[1], ctx))
    if tag == "neg":
        v = evaluate(node[1], ctx)
        if not _number(v):
            raise ExpressionError("'-' needs a number")
        return -v
    if tag == "cmp":
        return _compare(node[1], evaluate(node[2], ctx), evaluate(node[3], ctx))
    if tag == "in":
        item, seq = evaluate(node[1], ctx), evaluate(node[2], ctx)
        if isinstance(seq, str):
            return isinstance(item, str) and item in seq
        if isinstance(seq, (list, dict)):
            return item in seq
        return False
    if tag == "arith":
        return _arith(node[1], evaluate(node[2], ctx), evaluate(node[3], ctx))
    if tag == "filter":
        fn = FILTERS[node[1]]
        args = [evaluate(a, ctx) for a in node[3]]
        try:
            return _bounded(fn(evaluate(node[2], ctx), *args))
        except ExpressionError:
            raise
        except (TypeError, ValueError, OverflowError) as e:
            raise ExpressionError(f"filter {node[1]}: {e}") from None
    raise ExpressionError(f"unknown node {tag}")


def evaluate_text(text: str, ctx: dict[str, Any]) -> Any:
    """Parse and evaluate an expression.

    Returns:
        The value.
    """
    return evaluate(compile_expression(text), ctx)


# filters


def _length(v: Any) -> int:
    return len(v) if isinstance(v, (str, list, dict)) else 0


def _default(v: Any, fallback: Any = "") -> Any:
    return fallback if v is None or v == "" else v


def _join(v: Any, sep: Any = ", ") -> str:
    if not isinstance(v, list):
        return _string(v)
    return str(sep).join(_string(x) for x in v)


def _string(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def _round(v: Any, digits: Any = 0) -> Any:
    if not _number(v) or not _number(digits):
        raise ExpressionError("round needs a number")
    out = round(float(v), int(digits))
    return int(out) if int(digits) == 0 else out


def _int(v: Any) -> int:
    if isinstance(v, str):
        v = float(v.strip().replace(",", "."))
    if not _number(v) or not math.isfinite(float(v)):
        raise ExpressionError("int needs a number")
    return int(v)


def _float(v: Any) -> float:
    if isinstance(v, str):
        return float(v.strip().replace(",", "."))
    if not _number(v):
        raise ExpressionError("float needs a number")
    return float(v)


def _date(v: Any, fmt: Any = "%Y-%m-%d") -> str:
    fmt = str(fmt)
    if len(fmt) > 40:
        raise ExpressionError("date format longer than 40 characters")
    if isinstance(v, str):
        try:
            d: date = datetime.fromisoformat(v)
        except ValueError:
            d = date.fromisoformat(v)
    else:
        raise ExpressionError("date needs an ISO date or time")
    return d.strftime(fmt)


def _list_of_numbers(v: Any) -> list[float]:
    if not isinstance(v, list) or not all(_number(x) for x in v):
        raise ExpressionError("needs a list of numbers")
    return v


FILTERS: dict[str, Any] = {
    "length": _length,
    "lower": lambda v: _string(v).lower(),
    "upper": lambda v: _string(v).upper(),
    "trim": lambda v: _string(v).strip(),
    "default": _default,
    "json": lambda v: json.dumps(v, ensure_ascii=False),
    "string": _string,
    "join": _join,
    "first": lambda v: v[0] if isinstance(v, (list, str)) and v else None,
    "last": lambda v: v[-1] if isinstance(v, (list, str)) and v else None,
    "round": _round,
    "int": _int,
    "float": _float,
    "abs": lambda v: abs(v) if _number(v) else None,
    "sum": lambda v: sum(_list_of_numbers(v)),
    "min": lambda v: min(_list_of_numbers(v)) if v else None,
    "max": lambda v: max(_list_of_numbers(v)) if v else None,
    "keys": lambda v: sorted(v) if isinstance(v, dict) else [],
    "date": _date,
}
"""The only functions an expression can apply, as ``value | name(args)``."""


# templates


def expressions_in(text: str) -> list[str]:
    """The ``{{ ... }}`` parts of a template string.

    Returns:
        Their expressions.
    """
    return [m.group(1) for m in _TEMPLATE.finditer(text)]


def check_template(value: Any) -> list[tuple[str, str]]:
    """Every expression of a template that does not parse.

    A template is a string, or strings inside objects and lists.

    Returns:
        (path inside the value, message) for each problem.
    """
    out: list[tuple[str, str]] = []

    def walk(v: Any, path: str) -> None:
        if isinstance(v, str):
            if "{{" in v and not _TEMPLATE.search(v):
                out.append((path, "an opening '{{' without its closing '}}'"))
            for expr in expressions_in(v):
                try:
                    compile_expression(expr)
                except ExpressionError as e:
                    out.append((path, f"{{{{ {expr.strip()} }}}}: {e}"))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else str(k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")

    walk(value, "")
    return out


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Render a template: strings with ``{{ }}`` parts, inside objects and lists too.

    Returns:
        The value: a string that is exactly one ``{{ expression }}`` becomes the expression's
        value; any other string has each part printed; everything else is copied. An expression
        that does not parse or evaluate raises ``ExpressionError``.
    """
    if isinstance(value, str):
        if "{{" not in value:
            return value
        whole = _TEMPLATE.fullmatch(value.strip())
        if whole is not None:
            return evaluate_text(whole.group(1), ctx)
        out = _TEMPLATE.sub(lambda m: _string(evaluate_text(m.group(1), ctx)), value)
        return _bounded(out)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def render_text(value: Any, ctx: dict[str, Any]) -> str:
    """Render a template to text (objects and lists printed as JSON).

    Returns:
        The text.
    """
    return _string(render(value, ctx))
