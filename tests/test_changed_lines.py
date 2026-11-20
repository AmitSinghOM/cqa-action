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


def test_git_c_quoted_utf8_path_is_decoded_and_prefix_stripped():
    diff = ['+++ "b/src/sp\\303\\251c\\tab.py"', "@@ -0,0 +1 @@", "+x"]
    assert changed_lines.build_manifest(diff)["files"] == [
        {"path": "src/sp\u00e9c\tab.py", "ranges": [{"start_line": 1, "end_line": 1}]}
    ]


def test_git_trailing_tab_after_spaced_name_is_not_part_of_the_path():
    diff = ["+++ b/a dir/x y.py\t", "@@ -0,0 +1 @@", "+x"]
    assert changed_lines.build_manifest(diff)["files"][0]["path"] == "a dir/x y.py"


def test_content_line_that_looks_like_a_header_stays_content():
    # An added line "++ evil.py" is emitted as "+++ evil.py"; it must not
    # switch the current file.
    diff = ["+++ b/a.py", "@@ -0,0 +1,2 @@", "+++ b/evil.py", "+second"]
    assert changed_lines.build_manifest(diff)["files"] == [
        {"path": "a.py", "ranges": [{"start_line": 1, "end_line": 2}]}
    ]


def test_deletions_are_anchored_to_the_following_line_only_when_asked():
    with_deletions = changed_lines.build_manifest(DIFF.splitlines(), include_deletions=True)
    assert with_deletions["files"] == [
        {"path": "new.py", "ranges": [{"start_line": 1, "end_line": 2}]},
        {
            "path": "src/a.py",
            "ranges": [
                {"start_line": 11, "end_line": 13},
                {"start_line": 23, "end_line": 23},
                {"start_line": 34, "end_line": 34},
            ],
        },
    ]
    top = ["+++ b/a.py", "@@ -1,2 +0,0 @@", "-x", "-y"]
    assert changed_lines.build_manifest(top, include_deletions=True)["files"] == [
        {"path": "a.py", "ranges": [{"start_line": 1, "end_line": 1}]}
    ]
    assert changed_lines.build_manifest(top)["files"] == []


@pytest.mark.parametrize(
    "diff",
    [
        ["+++ b/a.py", "@@ -0,0 +1,2 @@", "+only one"],  # truncated
        ["+++ b/a.py", "@@ -1 +1 @@", "+b", "+c"],  # more "+" lines than declared
        ["+++ b/a.py", "@@ -0,0 +1,2 @@", "*bogus", "+b"],  # unexpected line
    ],
)
def test_malformed_diffs_fail_instead_of_shrinking_the_manifest(diff):
    with pytest.raises(changed_lines.DiffError):
        changed_lines.build_manifest(diff)


def test_cli_reports_malformed_diff_with_exit_2(tmp_path: Path, capsys):
    diff_file = tmp_path / "d.txt"
    diff_file.write_text("+++ b/a.py\n@@ -0,0 +1,2 @@\n+x\n")
    assert changed_lines.main(["--input", str(diff_file)]) == 2
    assert "ends inside a hunk" in capsys.readouterr().err


def test_lone_cr_and_line_separators_inside_content_do_not_split_lines(tmp_path: Path):
    diff_file = tmp_path / "d.txt"
    diff_file.write_bytes(
        b"+++ b/a.py\n@@ -0,0 +1,2 @@\n+x = '\r'\n+y = '\xe2\x80\xa8' \x0c\n+++ b/b.py\n@@ -0,0 +5 @@\n+z\n"
    )
    out = tmp_path / "m.json"
    assert changed_lines.main(["--input", str(diff_file), "--output", str(out)]) == 0
    assert json.loads(out.read_text())["files"] == [
        {"path": "a.py", "ranges": [{"start_line": 1, "end_line": 2}]},
        {"path": "b.py", "ranges": [{"start_line": 5, "end_line": 5}]},
    ]


def test_paths_the_analyzer_would_reject_are_none_from_clean_path():
    assert changed_lines._clean_path("https://x/y.py") is None
    assert changed_lines._clean_path("a" * 4097) is None


@pytest.mark.parametrize(
    "header",
    ["+++ b/win\\path.py", "+++ b/../up.py", "+++ /abs.py", "+++ b/c:x.py", '+++ "b/a\\000b.py"', "+++ b/" + "a" * 4097],
)
def test_changed_file_with_unrepresentable_path_fails_visibly(header):
    with pytest.raises(changed_lines.DiffError, match="cannot represent"):
        changed_lines.build_manifest([header, "@@ -0,0 +1 @@", "+x"])


def test_deleted_file_header_dev_null_is_not_an_error():
    assert changed_lines.build_manifest(["+++ /dev/null", "@@ -1 +0,0 @@", "-x"])["files"] == []


def test_line_numbers_beyond_32_bits_fail():
    with pytest.raises(changed_lines.DiffError, match="32-bit"):
        changed_lines.build_manifest(["+++ b/a.py", "@@ -0,0 +2147483648 @@", "+x"])
    edge = changed_lines.build_manifest(["+++ b/a.py", "@@ -0,0 +2147483647 @@", "+x"])
    assert edge["files"][0]["ranges"] == [{"start_line": 2147483647, "end_line": 2147483647}]


def test_file_and_range_count_limits(monkeypatch):
    monkeypatch.setattr(changed_lines, "MAX_FILES", 1)
    two_files = ["+++ b/a.py", "@@ -0,0 +1 @@", "+x", "+++ b/b.py", "@@ -0,0 +1 @@", "+x"]
    with pytest.raises(changed_lines.DiffError, match="changed files"):
        changed_lines.build_manifest(two_files)
    monkeypatch.setattr(changed_lines, "MAX_FILES", 10)
    monkeypatch.setattr(changed_lines, "MAX_RANGES", 1)
    with pytest.raises(changed_lines.DiffError, match="changed ranges"):
        changed_lines.build_manifest(two_files)


def test_cli_refuses_manifest_over_size_limit(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(changed_lines, "MAX_MANIFEST_BYTES", 10)
    diff_file = tmp_path / "d.txt"
    diff_file.write_text("+++ b/a.py\n@@ -0,0 +1 @@\n+x\n")
    assert changed_lines.main(["--input", str(diff_file), "--output", str(tmp_path / "m.json")]) == 2
    assert "5 MB" in capsys.readouterr().err
    assert not (tmp_path / "m.json").exists()


PARITY_DIFFS = [
    DIFF,
    '+++ "b/sp\\303\\251c.py"\n@@ -3,2 +2,0 @@\n-a\n-b\n@@ -9 +8,2 @@\n-c\n+d\n+e\n',
    "+++ b/x y.py\t\n@@ -1,0 +2 @@ ctx\n+++ not a header\n\\ No newline at end of file\n",
    "--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n",
]


@pytest.mark.parametrize("include_deletions", [False, True])
@pytest.mark.parametrize("diff", PARITY_DIFFS)
def test_parity_with_the_core_converter(diff, include_deletions):
    """Same manifest as cqa_analyzer.changed_lines.diff_to_manifest (3.1.0+)."""
    core = pytest.importorskip("cqa_analyzer.changed_lines")
    if not hasattr(core, "diff_to_manifest"):
        pytest.skip("installed analyzer predates diff_to_manifest")
    expected = core.diff_to_manifest(diff, include_deletions=include_deletions)
    ours = changed_lines.build_manifest(diff.split("\n"), include_deletions=include_deletions)
    assert ours == expected


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
