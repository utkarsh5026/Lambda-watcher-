#!/usr/bin/env python3
"""Run the two CI lint gates on a Python file as soon as Claude edits it.

Claude Code runs this after every ``Edit`` / ``Write`` (see ``.claude/settings.json``).
It runs ``ruff check`` on any ``.py`` file in the repository, and
``tools/check_docstrings.py`` as well when the file is under ``src/``, which is the
tree CI holds at 100% docstring coverage. A problem goes back to Claude on stderr
with exit status 2, so it gets fixed in the same turn rather than three minutes
later in CI.

It fails open on purpose. A missing ruff, an unreadable payload, or a file outside
the repository is a reason to say nothing, never a reason to block an edit: this is a
convenience in front of CI, and CI stays the gate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2]).resolve()


def edited_file() -> Path | None:
    """The ``.py`` file the tool call just wrote, or None when there is nothing to check."""
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return None
    raw = (payload.get("tool_input") or {}).get("file_path")
    if not raw:
        return None
    path = Path(raw)
    path = (path if path.is_absolute() else ROOT / path).resolve()
    if path.suffix != ".py" or not path.is_file() or not path.is_relative_to(ROOT):
        return None
    return path


def find_ruff() -> str | None:
    """The project venv's ruff, else one on ``PATH``, else None.

    The venv copy comes first because it is the version ``pyproject.toml`` pins,
    and a newer global ruff can flag rules this codebase has not opted into.
    """
    for candidate in (ROOT / ".venv" / "bin" / "ruff", ROOT / ".venv" / "Scripts" / "ruff.exe"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("ruff")


def complaints(command: list[str]) -> str:
    """What ``command`` printed if it failed, or ``""`` if it passed or could not run."""
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout + proc.stderr).strip() if proc.returncode else ""


def main() -> int:
    """Check the edited file, and report anything wrong with exit status 2."""
    path = edited_file()
    if path is None:
        return 0
    rel = path.relative_to(ROOT).as_posix()

    problems: list[str] = []
    ruff = find_ruff()
    if ruff:
        out = complaints([ruff, "check", "--quiet", "--output-format=concise", rel])
        if out:
            problems.append(f"ruff check {rel}:\n{out}")
    if rel.startswith("src/"):
        out = complaints([sys.executable, "tools/check_docstrings.py", rel])
        if out:
            problems.append(
                f"Missing docstrings (CLAUDE.md: every function, method, class and property):\n{out}"
            )

    if problems:
        print("\n\n".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
