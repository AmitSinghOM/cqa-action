"""Turn a unified diff into a cqa-analyzer changed-lines manifest (schema 1.0.0).

The analyzer never invokes Git; CI does. This script reads ``git diff
--unified=0`` output on stdin (or from a file) and writes the manifest the
analyzer's ``--changed-lines-manifest`` expects: project-relative POSIX paths
with inclusive 1-based line ranges of *added or modified* lines in the new
revision. Deleted-only hunks contribute nothing (there is no new line to
carry a finding). Stdlib only; no third-party imports.

Usage:
    git diff --unified=0 --diff-filter=AMR BASE HEAD | python changed_lines.py > changed-lines.json
    python changed_lines.py --input diff.txt --output changed-lines.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable

SCHEMA_VERSION = "1.0.0"
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
_NEW_FILE = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+?)\s*$")


def _clean_path(raw: str) -> str | None:
    """Return a manifest-safe project-relative path, or None to skip the file."""
    path = raw.strip()
    if path.startswith('"') and path.endswith('"'):
        # git quotes paths with unusual bytes; decode the C-style escapes.
        path = path[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    if path == "/dev/null":
        return None
    if "\\" in path or "\x00" in path or path.startswith("/"):
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return path


def parse_unified_diff(lines: Iterable[str]) -> dict[str, list[tuple[int, int]]]:
    """Map new-revision path -> list of inclusive (start, end) added/modified ranges."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("+++ "):
            current = _clean_path(_NEW_FILE.match(line).group("path")) if _NEW_FILE.match(line) else None
            continue
        if current is None:
            continue
        match = _HUNK.match(line)
        if not match:
            continue
        start = int(match.group("start"))
        count = int(match.group("count")) if match.group("count") is not None else 1
        if count == 0:
            continue  # pure deletion: no new lines
        ranges.setdefault(current, []).append((start, start + count - 1))
    return ranges


def merge_ranges(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_manifest(diff_lines: Iterable[str]) -> dict:
    files = []
    for path, spans in sorted(parse_unified_diff(diff_lines).items()):
        files.append(
            {
                "path": path,
                "ranges": [
                    {"start_line": start, "end_line": end} for start, end in merge_ranges(spans)
                ],
            }
        )
    return {"schema_version": SCHEMA_VERSION, "files": files}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", help="unified diff file (default: stdin)")
    parser.add_argument("--output", help="manifest path (default: stdout)")
    args = parser.parse_args(argv)
    if args.input:
        with open(args.input, encoding="utf-8", errors="replace") as handle:
            manifest = build_manifest(handle)
    else:
        manifest = build_manifest(sys.stdin)
    text = json.dumps(manifest, indent=2) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
