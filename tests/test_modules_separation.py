"""Applications and gateways share one table: every query by kind goes through the kind helpers.

A gateway is an application row with ``kind = "gateway"`` (``evokai.db.kinds``). This test walks
the source and fails when a query over ``Application`` does not go through ``applications_only``
or ``gateways_only``, when code compares ``Application.kind`` itself, or when a caller asks the
repository for both kinds at once, outside the few core places that must see both (audit,
retention, roles, slugs shared by both kinds).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "evokai"
HELPERS = {"applications_only", "gateways_only"}
QUERY_CALLS = {"select", "join", "outerjoin", "exists", "join_from"}

ALLOW_QUERY: dict[str, str] = {
    "db/repositories.py:ApplicationRepository.update": "by id: the policy of the row just changed",
    "db/repositories.py:SecurityEventRepository.purge": "retention, every row's own time to live",
    "db/repositories.py:AuditRepository.purge": "audit retention covers every row",
    "db/repositories.py:SessionRepository.purge_expired": "retention of conversations",
    "db/repositories.py:TraceRepository.purge": "retention of traces, every row's time to live",
    "db/repositories.py:_cutoff": "retention: one row's time to live, by id",
}
"""Functions that query application rows of both kinds, with the reason (core places only)."""

ALLOW_BOTH_KINDS: dict[str, str] = {
    "api/console_routes.py:create_application": "slugs are unique across both kinds",
    "api/gateway_admin_routes.py:create_gateway": "slugs are unique across both kinds",
    "api/sso_routes.py:deployment_audit": "the audit log names rows of both kinds",
    "api/policy_routes.py:_may_copy": "policies are core: copied from either kind",
    "api/policy_routes.py:system_copy": "policies are core: copied from either kind",
}
"""Functions that ask the repository for both kinds (``kind=None``), with the reason."""


def _mentions_application(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id == "Application":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "Application":
            return True
    return False


def _is_application(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "Application") or (
        isinstance(node, ast.Attribute) and node.attr == "Application"
    )


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""


class _Walker(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.scope: list[str] = []
        self.problems: list[str] = []
        self.queries: dict[str, list[int]] = {}
        self.helpers: set[str] = set()

    def _where(self) -> str:
        return f"{self.rel}:{'.'.join(self.scope) or '<module>'}"

    def _scoped(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scoped

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "kind" and _is_application(node.value):
            self.problems.append(
                f"{self._where()}:{node.lineno}: Application.kind outside evokai.db.kinds"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node)
        where = self._where()
        if name in HELPERS:
            self.helpers.add(where)
        if name in QUERY_CALLS and any(_mentions_application(a) for a in node.args):
            self.queries.setdefault(where, []).append(node.lineno)
        if name in {"all", "by_slug"}:
            for kw in node.keywords:
                both = isinstance(kw.value, ast.Constant) and kw.value.value is None
                if kw.arg == "kind" and both and where not in ALLOW_BOTH_KINDS:
                    self.problems.append(
                        f"{where}:{node.lineno}: asks for both kinds (kind=None); use one kind "
                        "or add the place to ALLOW_BOTH_KINDS with its reason"
                    )
        self.generic_visit(node)


def _walk() -> tuple[list[str], set[str]]:
    problems: list[str] = []
    seen: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        if rel == "db/kinds.py" or rel.startswith("assistant/skills/"):
            continue
        w = _Walker(rel)
        w.visit(ast.parse(path.read_text(encoding="utf-8")))
        problems.extend(w.problems)
        for where, lines in w.queries.items():
            seen.add(where)
            if where not in w.helpers and where not in ALLOW_QUERY:
                problems.append(
                    f"{where}:{lines[0]}: a query over Application without applications_only or "
                    "gateways_only (evokai.db.kinds)"
                )
    return problems, seen


def test_every_query_by_kind_goes_through_the_helpers() -> None:
    problems, _ = _walk()
    assert not problems, "\n".join(problems)


def test_the_allow_lists_name_real_places() -> None:
    _, seen = _walk()
    stale = sorted(set(ALLOW_QUERY) - seen)
    assert not stale, f"ALLOW_QUERY names places with no Application query: {stale}"
    for where in ALLOW_BOTH_KINDS:
        rel, _, func = where.partition(":")
        text = (SRC / rel).read_text(encoding="utf-8")
        assert f"def {func.rsplit('.', 1)[-1]}(" in text, f"{where} does not exist"


def test_the_walker_catches_a_bypass() -> None:
    source = (
        "async def bad(s):\n"
        "    return await s.execute(sa.select(m.Application).where(m.Application.kind == 'x'))\n"
        "async def both(store):\n"
        "    return await store.applications.all(kind=None)\n"
        "async def good(s):\n"
        "    return await s.execute(gateways_only(sa.select(m.Application)))\n"
    )
    w = _Walker("x.py")
    w.visit(ast.parse(source))
    assert any("Application.kind" in p for p in w.problems)
    assert any("x.py:both" in p for p in w.problems)
    assert "x.py:bad" in w.queries and "x.py:bad" not in w.helpers
    assert "x.py:good" in w.helpers
