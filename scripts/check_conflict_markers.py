#!/usr/bin/env python3
"""Fail when source files contain unresolved Git merge-conflict markers."""

from __future__ import annotations

import re
import sys
from pathlib import Path


SOURCE_SUFFIXES = {
    ".bash",
    ".cjs",
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".mjs",
    ".py",
    ".pyi",
    ".sass",
    ".scss",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
IGNORED_DIRECTORIES = {
    ".git",
    ".pytest_cache",
    ".pythonlibs",
    ".venv",
    "__pycache__",
    "attached_assets",
    "node_modules",
}
CONFLICT_MARKER = re.compile(
    r"^(?:<{7,}\s*.*|\|{7,}\s*.*|={7,}\s*|>{7,}\s*.*)\s*$"
)


def find_conflict_markers(root: Path) -> list[tuple[Path, int, str]]:
    """Return each marker line found in source files under *root*."""
    found: list[tuple[Path, int, str]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            print(f"Cannot check source file {path}: {exc}", file=sys.stderr)
            raise
        for line_number, line in enumerate(lines, start=1):
            if CONFLICT_MARKER.fullmatch(line):
                found.append((path, line_number, line.strip()))
    return found


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    markers = find_conflict_markers(root)
    if not markers:
        print("No unresolved merge conflict markers found.")
        return 0

    print("Unresolved merge conflict markers found in source files:", file=sys.stderr)
    for path, line_number, marker in markers:
        print(f"  {path.relative_to(root)}:{line_number}: {marker}", file=sys.stderr)
    print("Remove the conflict markers before building.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
