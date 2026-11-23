"""Build the built-in assistant's docs skill from docs/*.md (``--check``: fail when it is stale).

The skill lives in ``src/evokai/assistant/skills/evokai-docs``: a ``SKILL.md`` that lists the pages
and one reference file per page. Run it after changing the documentation:

    uv run python scripts/assistant_docs.py           # write the skill
    uv run python scripts/assistant_docs.py --check   # CI: exit 1 when it differs from the docs
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SKILL = ROOT / "src" / "evokai" / "assistant" / "skills" / "evokai-docs"
LEFT_OUT = {"releasing.md"}
"""Pages for Evokai's maintainers, not for people operating a deployment."""

HEADER = """---
name: evokai-docs
description: Evokai's documentation, one page per topic. Read the page of a topic before you \
explain how something works or propose a change you are not sure about.
---
Evokai's documentation, generated from the repository's docs/ folder. Each page is a file of this
skill: read it with the file tool (`references/<page>.md`). Pages:

"""


def pages() -> list[Path]:
    """The documentation pages that go into the skill.

    Returns:
        Paths, sorted.
    """
    found = sorted([*DOCS.glob("*.md"), *DOCS.glob("*/*.md")])
    return [p for p in found if p.name not in LEFT_OUT]


def title(text: str, fallback: str) -> str:
    """A page's first heading.

    Returns:
        The heading, or the fallback.
    """
    m = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else fallback


def reference(page: Path) -> str:
    """A page's file name inside the skill.

    Returns:
        ``references/<folder>-<name>.md`` (folders flattened).
    """
    rel = page.relative_to(DOCS).with_suffix("")
    return "references/" + "-".join(rel.parts).lower().replace("_", "-") + ".md"


def build() -> dict[str, str]:
    """The skill's files.

    Returns:
        Path inside the skill to content.
    """
    files: dict[str, str] = {}
    index = []
    for page in pages():
        text = page.read_text(encoding="utf-8")
        ref = reference(page)
        files[ref] = text
        index.append(f"- `{ref}`: {title(text, page.stem)}")
    files["SKILL.md"] = HEADER + "\n".join(index) + "\n"
    return files


def current() -> dict[str, str]:
    """The skill's files as they are on disk.

    Returns:
        Path inside the skill to content.
    """
    if not SKILL.is_dir():
        return {}
    return {
        str(p.relative_to(SKILL)): p.read_text(encoding="utf-8")
        for p in sorted(SKILL.rglob("*"))
        if p.is_file()
    }


def main() -> int:
    """Write or check the skill.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when the skill is stale")
    args = parser.parse_args()
    wanted = build()
    if args.check:
        if current() != wanted:
            print(
                "The assistant's docs skill is stale: run uv run python scripts/assistant_docs.py",
                file=sys.stderr,
            )
            return 1
        return 0
    for path in set(current()) - set(wanted):
        (SKILL / path).unlink()
    for path, text in wanted.items():
        target = SKILL / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    print(f"{len(wanted)} files in {SKILL.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
