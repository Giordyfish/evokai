"""Skills: a name, a description, a body and, optionally, files (progressive disclosure).

Only names and descriptions go into the prompt. The agent loads a body with ``skill_load`` when the
task needs it; the body comes with the list of the skill's files (path, size, first heading), and
the agent reads one with ``skill_load`` and ``file`` (in pages of ``MAX_READ_CHARS``). Scripts
(``scripts/*.py``) run with ``skill_run`` in the isolated code sandbox, only when code execution is
on for the deployment, the application and the agent (``evokai.sandbox.skill_scripts``).

A skill is a folder with a ``SKILL.md`` (front matter ``name`` and ``description``, then the body)
and, beside it, text files:

- references: any ``.md``, ``.txt``, ``.yaml`` or ``.yml`` file (``references/*.md`` by habit);
- data and templates: files under ``assets/`` or ``data/`` (``.csv``, ``.tsv``, ``.json``, ``.md``,
  ``.txt``...), readable like references and copied read-only into the sandbox with a script;
- scripts: ``.py`` files under ``scripts/``.

The limits below are checked when a skill is imported or saved; a skill over one is refused with
an error that names it (nothing is cut).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

log = logging.getLogger("evokai.skills")

MAX_DESCRIPTION_CHARS = 1_024
"""A description (Anthropic's limit). It goes into every prompt: keep it to a sentence or two."""
MAX_BODY_CHARS = 20_000
"""The body of SKILL.md (what ``skill_load`` returns), front matter excluded."""
MAX_READ_CHARS = 20_000
"""Characters of a file one ``skill_load`` returns; a longer file is read in pages (``offset``)."""
MAX_SKILL_BYTES = 1_000_000
"""UTF-8 bytes of a whole skill: SKILL.md and every file."""
MAX_SKILL_FILES = 50
"""Files of a skill besides SKILL.md."""
MAX_SCRIPT_BYTES = 50_000
"""UTF-8 bytes of one script."""
MAX_PATH_CHARS = 200
"""Characters of a file's path inside the skill."""
MAX_NAME_CHARS = 80
"""Characters of a skill's name."""
TEXT_SUFFIXES = (".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml")
"""Extensions of the files a skill may hold, besides scripts."""
SCRIPT_DIR = "scripts"
SCRIPT_SUFFIX = ".py"
DATA_DIRS = ("assets", "data")
"""Folders whose files a script finds next to it in the sandbox."""
SKILL_FILE = "SKILL.md"

_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
_IGNORED = {"__pycache__", "node_modules"}


class SkillError(ValueError):
    """A skill that breaks a limit or a path rule; the message names the skill and the rule."""


@dataclass(frozen=True)
class SkillFile:
    """A file of a skill: its path inside the folder and, when loaded, its text."""

    path: str
    content: str | None = None
    size: int = 0
    """Characters of the text."""
    title: str = ""
    """Its first Markdown heading, or a script's first docstring line."""

    @property
    def kind(self) -> str:
        """``script``, ``data`` or ``reference``.

        Returns:
            The kind.
        """
        return kind_of(self.path)


@dataclass(frozen=True)
class Skill:
    """A skill as the agent sees it."""

    name: str
    description: str
    body: str
    files: tuple[SkillFile, ...] = field(default_factory=tuple)

    @property
    def scripts(self) -> list[SkillFile]:
        """The skill's scripts.

        Returns:
            Files of kind ``script``.
        """
        return [f for f in self.files if f.kind == "script"]

    def file(self, path: str) -> SkillFile | None:
        """One of the skill's files, by path.

        Returns:
            The file, or None.
        """
        return next((f for f in self.files if f.path == path), None)


def kind_of(path: str) -> str:
    """What a file of a skill is, from its path.

    Returns:
        ``script`` (``scripts/*.py``), ``data`` (under ``assets/`` or ``data/``) or ``reference``.
    """
    first = path.split("/", 1)[0]
    if first == SCRIPT_DIR and path.endswith(SCRIPT_SUFFIX):
        return "script"
    if first in DATA_DIRS:
        return "data"
    return "reference"


def title_of(path: str, content: str) -> str:
    """A file's title: its first Markdown heading, or a script's first docstring line.

    Returns:
        The title (at most 120 characters), or an empty string.
    """
    if path.endswith(".md"):
        for line in content.splitlines()[:40]:
            if line.startswith("#"):
                return line.lstrip("#").strip()[:120]
        return ""
    if path.endswith(SCRIPT_SUFFIX):
        lines = [x.strip() for x in content.splitlines()[:40]]
        code = [x for x in lines if x and not x.startswith("#")]
        if code and code[0].lstrip("rRuU")[:3] in ('"""', "'''"):
            first = code[0].lstrip("rRuU")[3:].strip(" \"'")
            if not first and len(code) > 1:
                first = code[1].strip(" \"'")
            return first[:120]
    return ""


def check_path(path: str) -> str:
    """Check a file's path inside a skill.

    Relative POSIX paths only, without ``..``, empty or hidden parts; letters, digits, ``.``, ``-``
    and ``_``; an allowed extension; ``.py`` only under ``scripts/``.

    Returns:
        The path.

    Raises:
        SkillError: The path breaks a rule.
    """
    if not isinstance(path, str) or not path:
        raise SkillError("a file needs a path")
    if len(path) > MAX_PATH_CHARS:
        raise SkillError(f"{path[:60]}...: a path has at most {MAX_PATH_CHARS} characters")
    if path.startswith("/") or "\\" in path or ":" in path:
        raise SkillError(f"{path}: the path must be relative, with / between folders")
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise SkillError(f"{path}: no empty, . or .. parts in a path")
        if not _SEGMENT.match(part):
            raise SkillError(
                f"{path}: a path has letters, digits, '.', '-' and '_' only, and no hidden parts"
            )
    if path == SKILL_FILE:
        raise SkillError(f"{path}: SKILL.md is the skill itself, not one of its files")
    lower = path.lower()
    if lower.endswith(SCRIPT_SUFFIX):
        if parts[0] != SCRIPT_DIR or len(parts) < 2:
            raise SkillError(f"{path}: Python files go under {SCRIPT_DIR}/")
    elif not lower.endswith(TEXT_SUFFIXES):
        allowed = ", ".join((*TEXT_SUFFIXES, SCRIPT_SUFFIX))
        raise SkillError(f"{path}: not an allowed file type ({allowed})")
    return path


def check_skill(
    name: str, description: str, body: str, files: list[tuple[str, str]] | None = None
) -> None:
    """Check a skill against the limits.

    Args:
        name: Its name.
        description: Its description.
        body: The body of SKILL.md.
        files: (path, text) of its files.

    Raises:
        SkillError: A limit or a path rule is broken; the message says which.
    """
    if not name or len(name) > MAX_NAME_CHARS:
        raise SkillError(f"a skill name has 1 to {MAX_NAME_CHARS} characters")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillError(
            f"{name}: the description has {len(description):,} characters, the limit is "
            f"{MAX_DESCRIPTION_CHARS:,} (it goes into every prompt: keep it short)"
        )
    if len(body) > MAX_BODY_CHARS:
        raise SkillError(
            f"{name}: SKILL.md has {len(body):,} characters after its front matter, the limit is "
            f"{MAX_BODY_CHARS:,} (move detail into reference files)"
        )
    files = files or []
    if len(files) > MAX_SKILL_FILES:
        raise SkillError(f"{name}: {len(files)} files, the limit is {MAX_SKILL_FILES}")
    total = len(description.encode()) + len(body.encode())
    seen: set[str] = set()
    for path, content in files:
        try:
            check_path(path)
        except SkillError as e:
            raise SkillError(f"{name}: {e}") from None
        if path in seen:
            raise SkillError(f"{name}: {path} is there twice")
        seen.add(path)
        size = len(content.encode())
        if kind_of(path) == "script" and size > MAX_SCRIPT_BYTES:
            raise SkillError(
                f"{name}: {path} is {size:,} bytes, a script is at most {MAX_SCRIPT_BYTES:,}"
            )
        total += size
    if total > MAX_SKILL_BYTES:
        raise SkillError(
            f"{name}: the skill is {total:,} bytes with its files, the limit is {MAX_SKILL_BYTES:,}"
        )


def parse_skill_file(path: Path) -> Skill | None:
    """Read a SKILL.md.

    Args:
        path: The file.

    Returns:
        The skill, or None if the front-matter lacks a name or description (``SkillError`` when
        it is over a limit).
    """
    return parse_skill_text(path.read_text(encoding="utf-8"))


def parse_skill_text(text: str) -> Skill | None:
    """Read the text of a SKILL.md (front matter ``name`` and ``description``, then the body).

    Args:
        text: The file's text.

    Returns:
        The skill, or None if the front-matter lacks a name or description (``SkillError`` when
        the description or the body is over its limit).
    """
    match = _FRONT.match(text.lstrip("﻿"))
    if not match:
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        # A description with a colon in it is not valid YAML; read plain "key: value" lines.
        meta = {
            k.strip(): v.strip().strip("\"'")
            for k, _, v in (line.partition(":") for line in match.group(1).splitlines())
            if v
        }
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name") or "").strip()
    description = " ".join(str(meta.get("description") or "").split())
    if not name or not description:
        return None
    body = match.group(2).strip()
    check_skill(name, description, body)
    return Skill(name=name, description=description, body=body)


def make_file(path: str, content: str) -> SkillFile:
    """A file with its size and title.

    Returns:
        The file.
    """
    return SkillFile(path, content, len(content), title_of(path, content))


@dataclass
class FolderSkill:
    """A skill read from a folder, with what was left out of it."""

    skill: Skill
    skipped: list[str] = field(default_factory=list)
    """Files of the folder that are not part of the skill (another type, a link inside it)."""


def read_folder(folder: Path) -> FolderSkill | None:
    """Read a skill folder: SKILL.md and its files.

    Hidden files and folders and ``__pycache__`` are ignored; files of another type are skipped and
    reported (``skipped``); a link that leaves the folder, a file that is not UTF-8 text, or a
    limit broken is an error.

    Args:
        folder: The skill's folder.

    Returns:
        The skill, or None without a SKILL.md with a name and a description.

    Raises:
        SkillError: A file breaks a rule or the skill a limit; the message names the folder.
    """
    root = folder.resolve()
    md = folder / SKILL_FILE
    if not md.is_file():
        return None
    if md.is_symlink() and not _inside(md, root):
        raise SkillError(f"{folder}: SKILL.md links outside the folder")
    try:
        base = parse_skill_file(md)
    except SkillError as e:
        raise SkillError(f"{folder}: {e}") from None
    if base is None:
        return None
    files: list[tuple[str, str]] = []
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            if d.startswith(".") or d in _IGNORED:
                continue
            p = here / d
            if p.is_symlink():
                if not _inside(p, root):
                    raise SkillError(f"{folder}: {_rel(p, folder)} links outside the folder")
                skipped.append(f"{_rel(p, folder)}/ (a link: not followed)")
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            p = here / name
            rel = _rel(p, folder)
            if name.startswith(".") or rel == SKILL_FILE:
                continue
            if p.is_symlink() and not _inside(p, root):
                raise SkillError(f"{folder}: {rel} links outside the folder")
            try:
                check_path(rel)
            except SkillError as e:
                skipped.append(f"{rel} ({e})")
                continue
            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise SkillError(f"{folder}: {rel} is not UTF-8 text") from None
            files.append((rel, content))
    try:
        check_skill(base.name, base.description, base.body, files)
    except SkillError as e:
        raise SkillError(f"{folder}: {e}") from None
    skill = Skill(
        base.name, base.description, base.body, tuple(make_file(p, c) for p, c in sorted(files))
    )
    return FolderSkill(skill, skipped)


def _inside(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _rel(p: Path, folder: Path) -> str:
    return PurePosixPath(p.relative_to(folder)).as_posix()


def skills_in(folder: Path) -> list[Skill]:
    """All skills under a folder (one sub-folder per skill), with their files.

    Files that are not part of a skill are logged as ``skill_files_skipped``.

    Args:
        folder: The skills folder.

    Returns:
        Skills, by folder name (``SkillError`` when one breaks a limit or a path rule).
    """
    if not folder.is_dir():
        return []
    out = []
    for md in sorted(folder.glob(f"*/{SKILL_FILE}")):
        read = read_folder(md.parent)
        if read is None:
            continue
        if read.skipped:
            log.warning(
                "skill_files_skipped",
                extra={"skill": read.skill.name, "files": read.skipped[:20]},
            )
        out.append(read.skill)
    return out


def read_page(content: str, offset: int = 0) -> str:
    """A page of a file for the model, saying where it is and how to go on.

    Args:
        content: The file's text.
        offset: Where the page starts (characters).

    Returns:
        The page, with a note when the file has more.
    """
    total = len(content)
    offset = max(0, min(offset, total))
    end = min(total, offset + MAX_READ_CHARS)
    page = content[offset:end]
    if offset == 0 and end == total:
        return page
    note = f"[characters {offset:,}-{end:,} of {total:,}"
    note += f"; read on with offset {end}]" if end < total else "; end of the file]"
    return f"{page}\n\n{note}"


def files_listing(skill: Skill, *, runnable: bool) -> str:
    """The list of a skill's files that comes with its body (paths and sizes, never contents).

    Args:
        skill: The skill.
        runnable: Whether ``skill_run`` is offered (code execution on for this agent).

    Returns:
        The listing, or an empty string for a skill without files.
    """
    if not skill.files:
        return ""

    def line(f: SkillFile) -> str:
        return f"- {f.path} ({f.size:,} characters)" + (f": {f.title}" if f.title else "")

    readable = [f for f in skill.files if f.kind != "script"]
    scripts = skill.scripts
    out: list[str] = []
    if readable:
        out += [
            f'Files of skill {skill.name} (read one with skill_load {{"name": "{skill.name}", '
            '"file": "<path>"}, only when you need it):',
            *(line(f) for f in readable),
        ]
    if scripts:
        if out:
            out.append("")
        if runnable:
            out += [
                f'Scripts of skill {skill.name} (run one with skill_run {{"name": "{skill.name}", '
                '"script": "<path>", "args": {...}}; it gets args as JSON on stdin and finds the '
                "skill's data files next to it):",
                *(line(f) for f in scripts),
            ]
        else:
            out += [
                f"Scripts of skill {skill.name} (not available: code execution is off for this "
                "agent; do not try to run them):",
                *(f"- {f.path}" for f in scripts),
            ]
    return "\n".join(out)


async def load(
    skills: dict[str, Skill],
    args: dict[str, Any],
    read_file: Callable[[str, str], Awaitable[str | None]],
    *,
    runnable: bool,
) -> tuple[bool, str]:
    """Run ``skill_load``: a skill's body with its files listed, or a page of one file.

    Args:
        skills: The skills the agent may load.
        args: name, and optionally file and offset.
        read_file: ``(skill, path) -> text``: a stored file's text.
        runnable: Whether ``skill_run`` is offered.

    Returns:
        (ok, text for the model).
    """
    skill = skills.get(str(args.get("name") or ""))
    if skill is None:
        return False, "Unknown skill."
    path = args.get("file")
    if not path or path == SKILL_FILE:
        listing = files_listing(skill, runnable=runnable)
        return True, f"{skill.body}\n\n{listing}" if listing else skill.body
    entry = skill.file(str(path))
    if entry is None:
        listed = ", ".join(f.path for f in skill.files) or "none"
        return False, f"Skill {skill.name} has no file {path}; its files: {listed}."
    content = (
        entry.content if entry.content is not None else await read_file(skill.name, entry.path)
    )
    if content is None:
        return False, f"The file {path} is not available."
    try:
        offset = int(args.get("offset") or 0)
    except (TypeError, ValueError):
        return False, "offset must be a number of characters."
    if offset < 0 or (offset and offset >= len(content)):
        return False, f"offset must be between 0 and {max(len(content) - 1, 0):,}."
    return True, read_page(content, offset)
