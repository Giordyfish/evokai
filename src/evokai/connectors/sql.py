"""SQL databases: read-only queries over an allow-list of tables, parameterised by construction.

The agent never writes SQL. It asks for rows of an allowed table with structured filters, sorting
and a limit, or for grouped figures; the connector builds the statement with SQLAlchemy (values are
bound parameters) from the table's reflected columns. Named queries written by the application in
its configuration take typed parameters, bound as well. Every read runs in a read-only transaction
that is rolled back. Writes (insert and update of single tables listed in ``write_tables``) exist
only with ``writes: true`` and always ask the user to confirm.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar, Literal, cast

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from evokai.connectors.base import Connector, ConnectorConfig, secret, tool
from evokai.mcp.client import McpTool

OPS = ("eq", "ne", "lt", "le", "gt", "ge", "like", "in", "is_null", "not_null")
FUNCS = ("count", "sum", "avg", "min", "max")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class NamedQuery(BaseModel):
    """A query the application wrote, offered as a tool."""

    description: str = Field(min_length=1, max_length=500)
    sql: str = Field(min_length=1, max_length=8_000)
    """One SELECT (or WITH ... SELECT) with ``:name`` parameters."""
    params: dict[str, Literal["string", "integer", "number", "boolean", "date"]] = Field(
        default_factory=dict
    )

    @field_validator("sql")
    @classmethod
    def _select_only(cls, v: str) -> str:
        s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", v, flags=re.S).strip().rstrip(";").strip()
        if ";" in s:
            raise ValueError("a named query is one statement")
        if not re.match(r"^(select|with)\b", s, re.I):
            raise ValueError("a named query is a SELECT")
        if re.search(r"\b(insert|update|delete|merge|drop|alter|create|grant|truncate)\b", s, re.I):
            raise ValueError("a named query only reads")
        return s


class SqlConfig(ConnectorConfig):
    """``connector: sql``."""

    dsn_env: str = Field(min_length=1)
    """The environment variable with the database URL (SQLAlchemy, or ``postgres://...``)."""
    tables: list[str] = Field(min_length=1)
    """Allowed tables: ``name``, ``schema.name`` or ``schema.*``."""
    hidden_columns: dict[str, list[str]] = Field(default_factory=dict)
    """Columns never read or shown, per table (personal data, internal ids)."""
    row_limit: int = Field(default=100, ge=1, le=1_000)
    timeout_seconds: float = Field(default=10, gt=0, le=120)
    queries: dict[str, NamedQuery] = Field(default_factory=dict)
    write_tables: list[str] = Field(default_factory=list)
    """Tables the write tools may change (only with ``writes: true``)."""

    @field_validator("queries")
    @classmethod
    def _query_names(cls, v: dict[str, NamedQuery]) -> dict[str, NamedQuery]:
        for name in v:
            if not _NAME.match(name):
                raise ValueError(f"query name {name!r}: letters, digits and _ only")
        return v


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


FILTER = {
    "type": "array",
    "description": "Conditions, all of which must hold.",
    "items": {
        "type": "object",
        "properties": {
            "column": {"type": "string"},
            "op": {"type": "string", "enum": list(OPS)},
            "value": {"description": "A value; a list for op 'in'; nothing for is_null/not_null."},
        },
        "required": ["column", "op"],
    },
}


class SqlConnector(Connector):
    """A SQL database through SQLAlchemy (Postgres, MySQL, SQLite, SQL Server...)."""

    kind: ClassVar[str] = "sql"
    title: ClassVar[str] = "SQL database (read-only)"
    Config: ClassVar[type[ConnectorConfig]] = SqlConfig

    def __init__(self, name: str, config: ConnectorConfig) -> None:
        """Bind to the configuration; the engine opens on first use."""
        super().__init__(name, config)
        self.cfg = cast(SqlConfig, config)
        self._engine: AsyncEngine | None = None
        self._tables: dict[str, sa.Table] | None = None

    def engine(self) -> AsyncEngine:
        """The engine (from the URL in the environment).

        Returns:
            The engine.
        """
        if self._engine is None:
            from evokai.db.engine import async_url

            url = async_url(secret(self.cfg.dsn_env, f"SQL connector {self.name}"))
            pool = {} if url.startswith("sqlite") else {"pool_size": 2, "max_overflow": 2}
            self._engine = create_async_engine(url, pool_pre_ping=True, **pool)
        return self._engine

    async def close(self) -> None:
        """Dispose of the engine."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    # ── tables ───────────────────────────────────────────────────────────────────────────────────

    async def tables(self) -> dict[str, sa.Table]:
        """The allowed tables, reflected once.

        Returns:
            Name (as allowed) to table.
        """
        if self._tables is not None:
            return self._tables

        def reflect(conn: sa.Connection) -> dict[str, sa.Table]:
            insp = sa.inspect(conn)
            md = sa.MetaData()
            out: dict[str, sa.Table] = {}
            for entry in self.cfg.tables:
                schema, _, name = entry.rpartition(".")
                schema = schema or None
                names = insp.get_table_names(schema=schema) if name == "*" else [name]
                for n in names:
                    key = f"{schema}.{n}" if schema else n
                    out[key] = sa.Table(n, md, schema=schema, autoload_with=conn)
            return out

        async with self.engine().connect() as conn:
            self._tables = await conn.run_sync(reflect)
        return self._tables

    async def table(self, name: str) -> sa.Table:
        """An allowed table.

        Returns:
            The table.

        Raises:
            PermissionError: Not in the allow-list.
        """
        t = (await self.tables()).get(str(name))
        if t is None:
            raise PermissionError(f"table {name!r} is not available; use list_tables")
        return t

    def _hidden(self, key: str) -> set[str]:
        return set(self.cfg.hidden_columns.get(key, []))

    def _column(self, t: sa.Table, key: str, column: str) -> sa.ColumnElement[Any]:
        if column in self._hidden(key) or column not in t.c:
            raise LookupError(f"unknown column {column!r} of {key}")
        return t.c[column]

    def _where(self, t: sa.Table, key: str, filters: list[dict[str, Any]] | None) -> list[Any]:
        out: list[Any] = []
        for f in filters or []:
            col = self._column(t, key, str(f.get("column")))
            op, v = f.get("op"), f.get("value")
            if op not in OPS:
                raise ValueError(f"unknown op {op!r}")
            if op == "in":
                if not isinstance(v, list) or not v or len(v) > 200:
                    raise ValueError("'in' takes a list of 1 to 200 values")
                out.append(col.in_(v))
            elif op == "is_null":
                out.append(col.is_(None))
            elif op == "not_null":
                out.append(col.is_not(None))
            elif op == "like":
                out.append(col.like(str(v)))
            else:
                out.append(getattr(col, f"__{op}__")(v))
        return out

    # ── tools ────────────────────────────────────────────────────────────────────────────────────

    def tools(self) -> list[McpTool]:
        """The tools: list, describe, query, aggregate, the named queries, and writes if enabled.

        Returns:
            Tools.
        """
        limit = self.cfg.row_limit
        out = [
            tool("list_tables", "List the tables you can read, with their columns."),
            tool(
                "describe_table",
                "Columns, types and primary key of a table.",
                {"table": {"type": "string"}},
                ["table"],
            ),
            tool(
                "query",
                f"Read rows of a table, filtered and sorted; at most {limit} rows.",
                {
                    "table": {"type": "string"},
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Default: all.",
                    },
                    "filters": FILTER,
                    "order_by": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "desc": {"type": "boolean"},
                            },
                            "required": ["column"],
                        },
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": limit},
                },
                ["table"],
            ),
            tool(
                "aggregate",
                "Grouped figures of a table: count, sum, avg, min or max, by some columns.",
                {
                    "table": {"type": "string"},
                    "group_by": {"type": "array", "items": {"type": "string"}},
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fn": {"type": "string", "enum": list(FUNCS)},
                                "column": {
                                    "type": "string",
                                    "description": "Not needed for count.",
                                },
                            },
                            "required": ["fn"],
                        },
                    },
                    "filters": FILTER,
                },
                ["table", "metrics"],
            ),
        ]
        for name, q in self.cfg.queries.items():
            out.append(
                tool(
                    f"query_{name}",
                    q.description,
                    {p: {"type": "string" if t == "date" else t} for p, t in q.params.items()},
                    list(q.params),
                )
            )
        if self.cfg.writes and self.cfg.write_tables:
            out += [
                tool(
                    "insert_row",
                    "Add a row to a table (the user confirms first).",
                    {
                        "table": {"type": "string", "enum": self.cfg.write_tables},
                        "values": {"type": "object"},
                    },
                    ["table", "values"],
                    writes=True,
                ),
                tool(
                    "update_rows",
                    "Change rows of a table that match the filters (the user confirms first).",
                    {
                        "table": {"type": "string", "enum": self.cfg.write_tables},
                        "values": {"type": "object"},
                        "filters": FILTER,
                    },
                    ["table", "values", "filters"],
                    writes=True,
                ),
            ]
        return out

    async def _read(self, stmt: Any, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a read in a read-only transaction, always rolled back.

        Returns:
            Rows as dicts.
        """
        async with self.engine().connect() as conn:
            if conn.dialect.name == "postgresql":
                await conn.execute(sa.text("SET TRANSACTION READ ONLY"))
                await conn.execute(
                    sa.text(f"SET LOCAL statement_timeout = {int(self.cfg.timeout_seconds * 1000)}")
                )
            elif conn.dialect.name == "sqlite":
                await conn.execute(sa.text("PRAGMA query_only = ON"))
            try:
                res = await conn.execute(stmt, params or {})
                rows = res.mappings().fetchmany(self.cfg.row_limit + 1)
            finally:
                if conn.dialect.name == "sqlite":
                    await conn.execute(sa.text("PRAGMA query_only = OFF"))
                await conn.rollback()
        return [{k: _plain(v) for k, v in r.items()} for r in rows]

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool.

        Returns:
            Its result.

        Raises:
            ValueError: An unknown function, or no metric.
        """
        if tool_name == "list_tables":
            return [
                {"table": k, "columns": [c.name for c in t.c if c.name not in self._hidden(k)]}
                for k, t in (await self.tables()).items()
            ]
        if tool_name == "describe_table":
            key = str(args.get("table"))
            t = await self.table(key)
            return {
                "table": key,
                "columns": [
                    {"name": c.name, "type": str(c.type), "nullable": c.nullable}
                    for c in t.c
                    if c.name not in self._hidden(key)
                ],
                "primaryKey": [c.name for c in t.primary_key.columns],
            }
        if tool_name == "query":
            key = str(args.get("table"))
            t = await self.table(key)
            names = args.get("columns") or [c.name for c in t.c if c.name not in self._hidden(key)]
            cols = [self._column(t, key, str(c)) for c in names]
            stmt = sa.select(*cols).where(*self._where(t, key, args.get("filters")))
            for o in args.get("order_by") or []:
                col = self._column(t, key, str(o.get("column")))
                stmt = stmt.order_by(col.desc() if o.get("desc") else col.asc())
            limit = min(int(args.get("limit") or self.cfg.row_limit), self.cfg.row_limit)
            rows = await self._read(stmt.limit(limit + 1))
            return {"rows": rows[:limit], "more": len(rows) > limit}
        if tool_name == "aggregate":
            key = str(args.get("table"))
            t = await self.table(key)
            groups = [self._column(t, key, str(g)) for g in args.get("group_by") or []]
            metrics: list[Any] = []
            for i, mt in enumerate(args.get("metrics") or []):
                fn = mt.get("fn")
                if fn not in FUNCS:
                    raise ValueError(f"unknown fn {fn!r}")
                col = self._column(t, key, str(mt["column"])) if mt.get("column") else None
                expr = (
                    sa.func.count() if fn == "count" and col is None else getattr(sa.func, fn)(col)
                )
                metrics.append(expr.label(f"{fn}_{mt.get('column') or 'rows'}_{i}"))
            if not metrics:
                raise ValueError("ask for at least one metric")
            stmt = sa.select(*groups, *metrics).where(*self._where(t, key, args.get("filters")))
            if groups:
                stmt = stmt.group_by(*groups).order_by(*groups)
            rows = await self._read(stmt.limit(self.cfg.row_limit + 1))
            return {"rows": rows[: self.cfg.row_limit], "more": len(rows) > self.cfg.row_limit}
        if tool_name.startswith("query_"):
            q = self.cfg.queries[tool_name.removeprefix("query_")]
            params = {p: _coerce(args.get(p), t) for p, t in q.params.items()}
            rows = await self._read(sa.text(q.sql), params)
            return {"rows": rows[: self.cfg.row_limit], "more": len(rows) > self.cfg.row_limit}
        return await self._write(tool_name, args)

    async def _write(self, tool_name: str, args: dict[str, Any]) -> Any:
        key = str(args.get("table"))
        if not self.cfg.writes or key not in self.cfg.write_tables:
            raise PermissionError(f"writing to {key!r} is not allowed")
        t = await self.table(key)
        values = {str(k): v for k, v in (args.get("values") or {}).items()}
        for k in values:
            self._column(t, key, k)
        if not values:
            raise ValueError("give some values")
        async with self.engine().begin() as conn:
            if tool_name == "insert_row":
                await conn.execute(sa.insert(t).values(**values))
                return {"inserted": 1}
            where = self._where(t, key, args.get("filters"))
            if not where:
                raise ValueError("an update needs filters")
            res = await conn.execute(sa.update(t).where(*where).values(**values))
            return {"updated": int(getattr(res, "rowcount", 0) or 0)}


def _coerce(value: Any, kind: str) -> Any:
    if value is None:
        raise ValueError("every parameter is required")
    if kind == "integer":
        return int(value)
    if kind == "number":
        return float(value)
    if kind == "boolean":
        return value if isinstance(value, bool) else str(value).lower() in {"true", "1", "yes"}
    if kind == "date":
        return date.fromisoformat(str(value)[:10])
    return str(value)
