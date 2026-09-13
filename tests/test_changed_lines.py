"""changed_lines.py contract: diff → manifest the analyzer accepts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import changed_lines  # noqa: E402

DIFF = """\
diff --git a/src/a.py b/src/a.py
index 1..2 100644
--- a/src/a.py
+++ b/src/a.py
@@ -10,0 +11,3 @@ def f():
+x = 1
+y = 2
+z = 3
@@ -20 +23 @@ def g():
-old
+new
@@ -30,2 +33,0 @@ def h():
-gone
-gone
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+a = 1
+b = 2
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a
-b
diff --git a/bin/blob b/bin/blob
Binary files differ
"""


def test_added_and_modified_lines_only_deletions_and_binaries_ignored():
    manifest = changed_lines.build_manifest(DIFF.splitlines())
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["files"] == [
        {"path": "new.py", "ranges": [{"start_line": 1, "end_line": 2}]},
        {
            "path": "src/a.py",
            "ranges": [
                {"start_line": 11, "end_line": 13},
                {"start_line": 23, "end_line": 23},
            ],
        },
    ]


def test_adjacent_and_overlapping_ranges_merge():
    assert changed_lines.merge_ranges([(5, 6), (1, 2), (3, 4), (6, 9)]) == [(1, 9)]


@pytest.mark.parametrize(
    "raw", ["/abs/x.py", "a/../b.py", "./x.py", "a//b.py", "win\\path.py", "/dev/null"]
)
def test_unsafe_paths_are_skipped(raw):
    assert changed_lines._clean_path(raw) is None


def test_quoted_git_paths_are_unquoted():
    assert changed_lines._clean_path('"src/sp ace.py"') == "src/sp ace.py"


def test_empty_diff_is_a_valid_empty_manifest():
    assert changed_lines.build_manifest([]) == {"schema_version": "1.0.0", "files": []}


def test_cli_round_trip(tmp_path: Path):
    diff_file = tmp_path / "d.txt"
    diff_file.write_text(DIFF)
    out = tmp_path / "m.json"
    assert changed_lines.main(["--input", str(diff_file), "--output", str(out)]) == 0
    assert json.loads(out.read_text())["files"][0]["path"] == "new.py"


def test_real_analyzer_accepts_the_manifest(tmp_path: Path):
    """Integration: the installed analyzer must accept what we generate."""
    pytest.importorskip("cqa_analyzer")
    project = tmp_path / "p"
    project.mkdir()
    (project / "app.py").write_text(
        "def q(cur, n):\n"
        '    cur.execute("SELECT * FROM t WHERE n = " + n)\n'
        "\n"
        "def other(cur, n):\n"
        '    cur.execute("SELECT * FROM t WHERE n = " + n)\n'
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            changed_lines.build_manifest(
                ["+++ b/app.py", "@@ -0,0 +4,2 @@", "+def other", "+    cur.execute"]
            )
        )
    )
    result = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable, "-m", "cqa_analyzer", str(project), "--offline", "-f", "json",
            "--changed-lines-manifest", str(manifest), "--fail-on", "warning",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 4, result.stderr
    report = json.loads(result.stdout)
    # Only the finding on the changed lines (5) is selected; line 2 is not.
    assert [f["location"]["line"] for f in report["findings"]] == [5]
