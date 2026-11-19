"""Repository rules that a linter does not know."""

from __future__ import annotations

import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] (git ls-files only)
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EM_DASH = chr(0x2014)  # written as a code point: the character itself is banned


def _tracked() -> list[Path]:
    git = shutil.which("git") or "/usr/bin/git"
    out = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] (fixed command)
        [git, "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if line and line != "LICENSE"]


def test_no_em_dash_in_tracked_files() -> None:
    offenders = []
    for path in _tracked():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        offenders += [
            f"{path.relative_to(ROOT)}:{i}"
            for i, line in enumerate(text.splitlines(), 1)
            if EM_DASH in line
        ]
    assert offenders == [], "use a comma, colon, parentheses or two sentences"
