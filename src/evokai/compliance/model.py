"""The shape of a compliance pack: sections of blocks, every statement with its sources.

A pack is a plain JSON document (``Pack.as_json``) that the renderers turn into Markdown and HTML.
Every block, list item and table row carries ``sources``: keys of ``evokai.compliance.texts``
``SOURCES`` naming the setting or table the statement comes from. The renderers number them as
footnotes in order of first use. A block marked ``human`` is a part the controller completes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Item:
    """A list item or a table row: an id stable across snapshots, its text or cells, sources."""

    id: str
    text: str = ""
    cells: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    flag: str | None = None
    """``warn`` (a gap the configuration shows) or ``human`` (to complete)."""

    def as_json(self) -> dict[str, Any]:
        """The item.

        Returns:
            id, text or cells, sources, flag.
        """
        out: dict[str, Any] = {"id": self.id, "sources": self.sources}
        if self.cells:
            out["cells"] = self.cells
        else:
            out["text"] = self.text
        if self.flag:
            out["flag"] = self.flag
        return out


@dataclass
class Block:
    """A paragraph, a list, a table or a set of fields."""

    id: str
    kind: str
    """``p``, ``list``, ``table``, ``fields`` or ``note``."""
    text: str = ""
    title: str = ""
    columns: list[str] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    human: bool = False

    def as_json(self) -> dict[str, Any]:
        """The block.

        Returns:
            Its fields (empty ones left out).
        """
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "sources": self.sources}
        if self.title:
            out["title"] = self.title
        if self.text:
            out["text"] = self.text
        if self.columns:
            out["columns"] = self.columns
        if self.items:
            out["items"] = [i.as_json() for i in self.items]
        if self.human:
            out["human"] = True
        return out


@dataclass
class Section:
    """A titled part of a pack."""

    id: str
    title: str
    blocks: list[Block] = field(default_factory=list)

    def add(self, block: Block) -> Block:
        """Append a block.

        Returns:
            The block.
        """
        self.blocks.append(block)
        return block

    def as_json(self) -> dict[str, Any]:
        """The section.

        Returns:
            id, title, blocks.
        """
        return {"id": self.id, "title": self.title, "blocks": [b.as_json() for b in self.blocks]}


@dataclass
class Pack:
    """A generated pack in one language."""

    pack: str
    lang: str
    title: str
    subtitle: str
    disclaimer: str
    meta: list[tuple[str, str]]
    sections: list[Section] = field(default_factory=list)
    source_labels: dict[str, str] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def section(self, sid: str, title: str) -> Section:
        """Append a section.

        Returns:
            The section.
        """
        s = Section(sid, title)
        self.sections.append(s)
        return s

    def footnotes(self) -> list[dict[str, Any]]:
        """The sources in order of first use, numbered from 1.

        Returns:
            n, key, text.
        """
        order: list[str] = []
        for s in self.sections:
            for b in s.blocks:
                for key in [*b.sources, *(k for i in b.items for k in i.sources)]:
                    if key not in order:
                        order.append(key)
        return [
            {"n": n, "key": k, "text": self.source_labels.get(k, k)} for n, k in enumerate(order, 1)
        ]

    def as_json(self) -> dict[str, Any]:
        """The pack as JSON.

        Returns:
            pack, lang, title, subtitle, disclaimer, meta, sections, footnotes and extras.
        """
        return {
            "pack": self.pack,
            "lang": self.lang,
            "title": self.title,
            "subtitle": self.subtitle,
            "disclaimer": self.disclaimer,
            "meta": [{"label": a, "value": b} for a, b in self.meta],
            "sections": [s.as_json() for s in self.sections],
            "footnotes": self.footnotes(),
            **self.extra,
        }
