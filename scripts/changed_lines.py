"""Turn a unified diff into a cqa-analyzer changed-lines manifest (schema 1.0.0).

The analyzer never invokes Git; CI does. This script reads ``git diff
--unified=0`` output on stdin (or from a file) and writes the manifest the
analyzer's ``--changed-lines-manifest`` expects: project-relative POSIX paths
with inclusive 1-based line ranges of *added or modified* lines in the new
revision. By default a deleted-only hunk contributes nothing; with
``--include-deletions`` it is anchored to the line that now follows the removed
block, matching ``cqa_analyzer.changed_lines.diff_to_manifest``. Stdlib only;
no third-party imports, so it works whatever analyzer version is installed.

Hunk counts drive the parser, so an added line that itself starts with ``++``
can never be mistaken for a file header. A diff that ends inside a hunk, a
hunk longer than its header declares, a changed path the manifest cannot carry
(absolute, ``..``, backslash, URL scheme, over 4096 chars) or a result over the
analyzer's manifest limits is an error (exit 2), never a silently smaller
manifest.

Usage:
    git diff --unified=0 --diff-filter=AMR BASE HEAD | python changed_lines.py > changed-lines.json
    python changed_lines.py --input diff.txt --output changed-lines.json [--include-deletions]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable

SCHEMA_VERSION = "1.0.0"
# The analyzer's manifest limits (cqa_analyzer.changed_lines); exceeding any of
# them is an error here, so an emitted manifest is always one it accepts.
MAX_MANIFEST_BYTES = 5 * 1024 * 1024
MAX_FILES = 20_000
MAX_RANGES = 100_000
MAX_PATH_LENGTH = 4_096
MAX_LINE = 2_147_483_647
_HUNK = re.compile(r"^@@ -\d+(?:,(?P<old>\d+))? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_C_ESCAPE = re.compile(r"\\([abfnrtv\\\"]|[0-7]{1,3})")
_C_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", '"': '"'}


class DiffError(ValueError):
    """The input is not a unified diff this script can follow."""


def _unquote(raw: str) -> str:
    """Undo git's C-style path quoting; octal escapes are UTF-8 bytes."""
    if not (len(raw) >= 2 and raw.startswith('"') and raw.endswith('"')):
        return raw

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        return _C_ESCAPES.get(token) or chr(int(token, 8) & 0xFF)

    decoded = _C_ESCAPE.sub(replace, raw[1:-1])
    try:
        return decoded.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return decoded


def _safe(path: str) -> str | None:
    """Return the path if the analyzer would accept it, else None."""
    if (
        not path
        or len(path) > MAX_PATH_LENGTH
        or "\\" in path
        or "\x00" in path
        or path.startswith("/")
        or _SCHEME.match(path)
    ):
        return None
    if any(part in ("", ".", "..") for part in path.split("/")):
        return None
    return path


def _clean_path(raw: str) -> str | None:
    """Unquote one bare path (no ``a/``/``b/`` prefix); None if unsafe or /dev/null."""
    if raw == "/dev/null":
        return None
    return _safe(_unquote(raw))


def _header_path(value: str) -> str | None:
    """Path named by a ``+++ `` header, or None for /dev/null.

    Raises :class:`DiffError` for a path the analyzer would reject.

    Git appends a tab after names containing a space, and non-git diffs append
    a tab-separated timestamp; the prefix sits inside git's quotes.
    """
    value = value.rstrip("\r").split("\t", 1)[0]
    if value == "/dev/null":
        return None
    value = _unquote(value)
    if value.startswith(("a/", "b/")):
        value = value[2:]
    safe = _safe(value)
    if safe is None:
        # A real changed file the analyzer cannot be told about: gating zero
        # lines for it would read as "clean", so refuse instead.
        raise DiffError(f"changed file has a path the manifest cannot represent: {value!r}")
    return safe


def parse_unified_diff(
    lines: Iterable[str], include_deletions: bool = False
) -> dict[str, list[tuple[int, int]]]:
    """Map new-revision path -> list of inclusive (start, end) changed ranges."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    old_left = new_left = new_line = 0
    for line in lines:
        line = line.removesuffix("\n")
        if old_left > 0 or new_left > 0:
            if line.startswith("\\"):
                continue  # "\ No newline at end of file"
            tag = line[:1]
            touched = None
            if tag == "+":
                new_left -= 1
                touched = new_line
                new_line += 1
            elif tag == "-":
                old_left -= 1
                if include_deletions:
                    touched = max(new_line, 1)
            elif tag in (" ", ""):
                old_left -= 1
                new_left -= 1
                new_line += 1
            else:
                raise DiffError("unified diff hunk contains an unexpected line")
            if old_left < 0 or new_left < 0:
                raise DiffError("unified diff hunk is longer than its header declares")
            if touched is not None and current is not None:
                ranges.setdefault(current, []).append((touched, touched))
            continue
        if line.startswith("+++ "):
            current = _header_path(line[4:])
        elif line.startswith(("diff --git ", "Binary files ", "GIT binary patch")):
            current = None
        else:
            match = _HUNK.match(line)
            if match:
                old_left = int(match.group("old")) if match.group("old") is not None else 1
                new_left = int(match.group("count")) if match.group("count") is not None else 1
                new_line = int(match.group("start"))
                if new_left == 0:
                    new_line += 1  # pure deletion: git names the line *before* the gap
                if new_line + new_left > MAX_LINE + 1:
                    raise DiffError("hunk line numbers exceed the manifest's 32-bit limit")
    if old_left > 0 or new_left > 0:
        raise DiffError("unified diff ends inside a hunk (truncated input?)")
    return ranges


def merge_ranges(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_manifest(diff_lines: Iterable[str], include_deletions: bool = False) -> dict:
    files = []
    for path, spans in sorted(parse_unified_diff(diff_lines, include_deletions).items()):
        files.append(
            {
                "path": path,
                "ranges": [
                    {"start_line": start, "end_line": end} for start, end in merge_ranges(spans)
                ],
            }
        )
    if len(files) > MAX_FILES:
        raise DiffError(f"more than {MAX_FILES} changed files; the analyzer would reject the manifest")
    if sum(len(entry["ranges"]) for entry in files) > MAX_RANGES:
        raise DiffError(f"more than {MAX_RANGES} changed ranges; the analyzer would reject the manifest")
    return {"schema_version": SCHEMA_VERSION, "files": files}


def _decoded_lines(stream) -> Iterable[str]:
    # Split on "\n" only: a lone "\r" or U+2028 inside a content line is still
    # one diff line. Content bytes are never used, so lossy decoding is fine;
    # git quotes every non-ASCII path when core.quotePath is on.
    for raw in stream:
        yield raw.decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", help="unified diff file (default: stdin)")
    parser.add_argument("--output", help="manifest path (default: stdout)")
    parser.add_argument(
        "--include-deletions",
        action="store_true",
        help="anchor deleted-only hunks to the line that now follows them",
    )
    args = parser.parse_args(argv)
    try:
        if args.input:
            with open(args.input, "rb") as handle:
                manifest = build_manifest(_decoded_lines(handle), args.include_deletions)
        else:
            manifest = build_manifest(_decoded_lines(sys.stdin.buffer), args.include_deletions)
    except DiffError as error:
        print(f"changed_lines.py: {error}", file=sys.stderr)
        return 2
    text = json.dumps(manifest, indent=2) + "\n"
    if len(text.encode("utf-8")) > MAX_MANIFEST_BYTES:
        print("changed_lines.py: manifest exceeds the analyzer's 5 MB limit", file=sys.stderr)
        return 2
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
