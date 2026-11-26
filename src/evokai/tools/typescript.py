"""TypeScript declarations of an agent's tools, from their JSON Schemas.

``GET /v1/admin/agents/{name}/types`` and ``evokai types`` give an application a ``.d.ts`` with
the arguments and results of the agent's tools, so the code that draws a tool's card
(``toolRenderers``), handles a client tool or reads a structured answer is typed:

- each MCP tool: ``<Name>Args`` from its ``inputSchema``, ``<Name>Result`` from its
  ``outputSchema`` (``unknown`` without one);
- each custom client tool of the agent: ``<Name>Args`` from its ``parameters`` (what the page
  receives in ``client_tool``);
- the maps ``ToolArgs``, ``ToolResults`` and ``ClientToolArgs`` (tool name to type), and
  ``Output`` for an agent with an ``output_schema``.

The conversion covers what tool schemas use: types and type lists, ``enum``, ``const``,
objects (``properties``, ``required``, ``additionalProperties``), arrays and tuples
(``items``, ``prefixItems``), ``anyOf``, ``oneOf``, ``allOf``, ``nullable`` and local ``$ref``
(``#/$defs/...``, ``#/definitions/...``, inlined; a recursive one is ``unknown``). Anything else
is ``unknown``: a type is never narrower than the schema.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_MAX_DEPTH = 12


@dataclass(frozen=True)
class ToolTypes:
    """One tool to declare."""

    name: str
    description: str = ""
    args: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    changes_data: bool = False


def type_name(name: str, suffix: str) -> str:
    """A tool name as a TypeScript type name (``orders_month`` to ``OrdersMonthArgs``).

    Returns:
        The name.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    base = "".join(w[:1].upper() + w[1:] for w in words) or "Tool"
    if base[0].isdigit():
        base = f"T{base}"
    return base + suffix


def _comment(text: str, indent: str) -> str:
    text = " ".join(str(text).split()).replace("*/", "*\\/")
    return f"{indent}/** {text} */\n" if text else ""


def _sentence(text: str) -> str:
    text = " ".join(str(text).split())
    return text if not text or text[-1] in ".!?" else f"{text}."


def _key(name: str) -> str:
    return name if _IDENT.match(name) else json.dumps(name)


def _group(t: str) -> str:
    """A type that goes before ``[]``: unions and intersections need parentheses.

    Returns:
        The type, parenthesised when needed.
    """
    return f"({t})" if (" | " in t or " & " in t) and not t.startswith("{") else t


class _Converter:
    def __init__(self, root: dict[str, Any]) -> None:
        self.root = root

    def resolve(self, ref: str) -> dict[str, Any] | None:
        if not ref.startswith("#/"):
            return None
        node: Any = self.root
        for piece in ref[2:].split("/"):
            piece = piece.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or piece not in node:
                return None
            node = node[piece]
        return node if isinstance(node, dict) else None

    def ts(self, schema: Any, indent: str = "", seen: tuple[str, ...] = ()) -> str:
        if schema is True or schema == {}:
            return "unknown"
        if not isinstance(schema, dict) or len(seen) > _MAX_DEPTH:
            return "unknown" if schema is not False else "never"
        if "$ref" in schema:
            ref = str(schema["$ref"])
            target = self.resolve(ref)
            if target is None or ref in seen:
                return "unknown"
            return self.ts(target, indent, (*seen, ref))
        out = self._base(schema, indent, seen)
        if schema.get("nullable") is True and out != "unknown":
            out = f"{out} | null"
        return out

    def _base(self, schema: dict[str, Any], indent: str, seen: tuple[str, ...]) -> str:
        if "const" in schema:
            return json.dumps(schema["const"], ensure_ascii=False)
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            return " | ".join(json.dumps(v, ensure_ascii=False) for v in schema["enum"])
        for key, sep in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
            subs = schema.get(key)
            if isinstance(subs, list) and subs:
                parts = list(dict.fromkeys(self.ts(s, indent, seen) for s in subs))
                if sep == " & ":
                    parts = [p for p in parts if p != "unknown"] or ["unknown"]
                elif "unknown" in parts:
                    return "unknown"
                return sep.join(_group(p) if sep == " & " else p for p in parts)
        kind = schema.get("type")
        if isinstance(kind, list):
            parts = [self._typed(schema, str(k), indent, seen) for k in kind]
            return " | ".join(dict.fromkeys(parts)) or "unknown"
        if kind is None:
            if "properties" in schema or "additionalProperties" in schema:
                kind = "object"
            elif "items" in schema or "prefixItems" in schema:
                kind = "array"
            else:
                return "unknown"
        return self._typed(schema, str(kind), indent, seen)

    def _typed(self, schema: dict[str, Any], kind: str, indent: str, seen: tuple[str, ...]) -> str:
        if kind == "string":
            return "string"
        if kind in ("number", "integer"):
            return "number"
        if kind == "boolean":
            return "boolean"
        if kind == "null":
            return "null"
        if kind == "array":
            prefix = schema.get("prefixItems")
            if isinstance(prefix, list) and prefix:
                return "[" + ", ".join(self.ts(s, indent, seen) for s in prefix) + "]"
            items = schema.get("items")
            if isinstance(items, list):  # draft-07 tuples
                return "[" + ", ".join(self.ts(s, indent, seen) for s in items) + "]"
            return f"{_group(self.ts(items if items is not None else True, indent, seen))}[]"
        if kind == "object":
            return self._object(schema, indent, seen)
        return "unknown"

    def _object(self, schema: dict[str, Any], indent: str, seen: tuple[str, ...]) -> str:
        props = schema.get("properties")
        props = props if isinstance(props, dict) else {}
        required = set(schema.get("required") or [])
        extra = schema.get("additionalProperties")
        if not props:
            if extra is False:
                return "Record<string, never>"
            value = "unknown" if extra in (None, True) else self.ts(extra, indent, seen)
            return f"Record<string, {value}>"
        inner = indent + "  "
        lines = ["{"]
        for name, sub in props.items():
            desc = sub.get("description") if isinstance(sub, dict) else None
            if desc:
                lines.append(_comment(desc, inner).rstrip("\n"))
            optional = "" if name in required else "?"
            lines.append(f"{inner}{_key(name)}{optional}: {self.ts(sub, inner, seen)}")
        if isinstance(extra, dict) or extra is True:
            lines.append(f"{inner}[key: string]: unknown")
        lines.append(f"{indent}}}")
        return "\n".join(lines)


def to_typescript(schema: Any) -> str:
    """One JSON Schema as a TypeScript type.

    Returns:
        The type (``unknown`` where the schema says nothing narrower).
    """
    return _Converter(schema if isinstance(schema, dict) else {}).ts(schema)


def _declare(name: str, schema: dict[str, Any] | None, doc: str = "") -> str:
    t = to_typescript(schema) if schema is not None else "unknown"
    head = _comment(doc, "")
    if t.startswith("{"):
        return f"{head}export interface {name} {t}\n"
    return f"{head}export type {name} = {t}\n"


def _map(name: str, doc: str, entries: list[tuple[str, str]]) -> str:
    body = "".join(f"  {_key(k)}: {v}\n" for k, v in entries)
    return f"{_comment(doc, '')}export interface {name} {{\n{body}}}\n"


def declarations(
    agent: str,
    version: str,
    tools: Sequence[ToolTypes],
    client_tools: Sequence[ToolTypes] = (),
    output: dict[str, Any] | None = None,
    missing: Sequence[str] = (),
) -> str:
    """A ``.d.ts`` for an agent's tools.

    Args:
        agent: The agent's name.
        version: Its active version.
        tools: Its MCP tools.
        client_tools: Its custom client tools.
        output: Its ``output_schema``.
        missing: MCP servers that could not be listed (their tools are not in the file).

    Returns:
        The declarations.
    """
    out = [
        f"// The tools of the agent {agent} (version {version}), as TypeScript types.\n",
        "// Generated by Evokai from the tools' JSON Schemas: do not edit, generate it again.\n",
    ]
    if missing:
        out.append(f"// Not listed (their tools are missing): {', '.join(missing)}.\n")
    out.append("\n")
    used: set[str] = set()

    def unique(name: str) -> str:
        n, i = name, 2
        while n in used:
            n, i = f"{name}{i}", i + 1
        used.add(n)
        return n

    args: list[tuple[str, str]] = []
    results: list[tuple[str, str]] = []
    for t in tools:
        a, r = unique(type_name(t.name, "Args")), unique(type_name(t.name, "Result"))
        doc = _sentence(t.description) + (
            " Changes data: waits for the person's yes." if t.changes_data else ""
        )
        out.append(_declare(a, t.args or {"type": "object"}, f"Arguments of {t.name}. {doc}"))
        out.append(_declare(r, t.result, f"What {t.name} returns (its structured content)."))
        out.append("\n")
        args.append((t.name, a))
        results.append((t.name, r))
    client: list[tuple[str, str]] = []
    for t in client_tools:
        a = unique(type_name(t.name, "Args"))
        out.append(
            _declare(
                a,
                t.args or {"type": "object"},
                f"Arguments of {t.name}. {_sentence(t.description)}",
            )
        )
        out.append("\n")
        client.append((t.name, a))
    out.append(_map("ToolArgs", "Arguments of each tool, by name (a tool card's args).", args))
    out.append(_map("ToolResults", "What each tool returns, by name.", results))
    out.append(_map("ClientToolArgs", "Arguments of each client tool, by name.", client))
    out.append("export type ToolName = keyof ToolArgs\n")
    if output is not None:
        out.append("\n")
        out.append(_declare("Output", output, "The agent's structured answer (output_schema)."))
    return "".join(out)
