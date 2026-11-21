"""MkDocs hook for the documentation site (mkdocs.yml).

The pages under docs/ link to files elsewhere in the repository with relative paths (`../src/...`,
`../CHANGELOG.md`), which work on GitHub but point nowhere on the site. This hook turns those
links, and only those, into links to the file on GitHub; links between pages are left to MkDocs.
"""

import posixpath
import re
from pathlib import Path

REPO_URL = "https://github.com/Giordyfish/evokai"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent.parent

# An inline Markdown link or image target: ](target) or ](target "title").
LINK = re.compile(r"\]\((?P<target>[^)\s]+)(?P<rest>(?:\s+\"[^\"]*\")?\))")
FENCE = re.compile(r"^\s*(```|~~~)")


def _outside(target: str, page_dir: str) -> str | None:
    """Return the GitHub URL for a relative target that leaves docs/, or None to keep the link."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith(("#", "/")):
        return None
    path, _, fragment = target.partition("#")
    if not path:
        return None
    repo_path = posixpath.normpath(posixpath.join("docs", page_dir, path))
    if repo_path.startswith(("docs/", "..")):
        return None
    kind = "tree" if (ROOT / repo_path).is_dir() else "blob"
    url = f"{REPO_URL}/{kind}/{BRANCH}/{repo_path}"
    return f"{url}#{fragment}" if fragment else url


def on_page_markdown(markdown, page, **_):
    """Rewrite links that leave docs/ to GitHub links, outside fenced code blocks.

    Returns:
        The page's Markdown with those links rewritten.
    """
    page_dir = posixpath.dirname(page.file.src_uri)

    def swap(m: re.Match) -> str:
        url = _outside(m["target"], page_dir)
        return m.group(0) if url is None else f"]({url}{m['rest']}"

    lines, fenced = [], False
    for line in markdown.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            line = LINK.sub(swap, line)
        lines.append(line)
    return "\n".join(lines)
