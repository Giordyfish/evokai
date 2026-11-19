"""The ``knowledge_search`` tool an agent gets with its knowledge bases, and how results reach it.

Results are untrusted content. Each passage travels between ``@@source knowledge:<base>/<doc>``
and ``@@end`` lines inside the tool result; when the result goes to the model the guard replaces
each such block with the passage spotlighted under that source (``evokai.guard``), so the model
reads every passage as data from a named document. Passages are numbered across a turn; the model
cites them with ``[n]`` and the client receives a ``citation`` event for each number.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Generator, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evokai.db import models as m
    from evokai.knowledge.service import Found

TOOL_NAME = "knowledge_search"

CITE_NOTE = (
    "Knowledge: search the knowledge bases with knowledge_search before answering questions they "
    "cover, and answer from the passages found. After each statement that uses a passage, cite it "
    "with its number in square brackets, like [1] or [2][3]. Never invent a number; when nothing "
    "relevant is found, say so instead of guessing."
)
"""Added to the stable part of the system prompt when the agent has knowledge bases."""

_BLOCK = re.compile(r"(?ms)^@@source (\S+)\n(.*?)\n@@end$")


def tool_def(bases: Sequence[m.KnowledgeBase]) -> dict[str, Any]:
    """The tool's definition for an agent's bases.

    Returns:
        name, description, schema.
    """
    lines = [
        "Search the knowledge bases for passages that answer a question (meaning and words). "
        "Returns numbered passages with their document, page and section; cite them as [n].",
        "Bases:",
    ]
    lines += [f"- {b.name}: {b.description or b.title or b.name}"[:300] for b in bases]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for, as a full question or the key terms",
            },
            "kb": {
                "type": "string",
                "enum": [b.name for b in bases],
                "description": "Search one base only (default: all of them)",
            },
            "filters": {
                "type": "object",
                "description": (
                    "Metadata filters: field to a value, a list of values, or {gte, lte}"
                ),
            },
        },
        "required": ["query"],
    }
    return {"name": TOOL_NAME, "description": "\n".join(lines), "schema": schema}


def _defuse(text: str) -> str:
    return re.sub(r"(?m)^@@", " @@", text)


def format_results(query: str, found: Sequence[tuple[int, Found, str]]) -> str:
    """The tool result: numbered passages, each in its source block.

    Args:
        query: The question searched.
        found: (number, passage, its text as scanned by the guard).

    Returns:
        The text for the model.
    """
    if not found:
        return f'No passage of the knowledge bases matches "{query}". Say so; do not guess.'
    from evokai.knowledge.service import location_label

    out = [f'Passages for "{query}". Cite each one you use with its number, like [{found[0][0]}].']
    for n, f, text in found:
        where = location_label(f.location())
        title = f.document.title or f.document.source
        out.append(f"[{n}] {title}" + (f" ({where})" if where else ""))
        out.append(f"@@source {f.source}\n{_defuse(text)}\n@@end")
    return "\n".join(out)


@contextlib.contextmanager
def retrieval_span(trace: Any, bases: Sequence[Any], query: str) -> Generator[Any]:
    """A ``retrieval`` span of the turn's trace around a search (nothing without a trace).

    Yields:
        The span, or None.
    """
    if trace is None:
        yield None
        return
    names = ",".join(b.name for b in bases)
    with trace.span("retrieval", f"knowledge_search {names}", source=names) as span:
        trace.put(span, query=query)
        yield span


def record_found(trace: Any, span: Any, found: Sequence[Found]) -> None:
    """What a search found, on its span: counts, and the passages as the trace may keep them."""
    if trace is None or span is None:
        return
    span.set(results=len(found), documents=len({f.document.id for f in found}))
    trace.put(span, results=[f"{f.source} {f.score}" for f in found])


def mark_sources(text: str, mark: Callable[[str, str], str]) -> str:
    """Replace every source block of a result with its passage as ``mark`` makes it.

    Args:
        text: The tool result.
        mark: (passage, source) to the text the model gets (spotlighted, or as is).

    Returns:
        The result without its block lines.
    """
    return _BLOCK.sub(lambda mt: mark(mt.group(2), mt.group(1)), text)
