"""pr_changed_lines.sh against real git, with a local file:// origin (no network).

The fixture mirrors a GitHub pull request: main advanced after the branch was
cut, and GitHub's merge commit (parents [main tip, PR head]) is what
actions/checkout checks out by default.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pr_changed_lines.sh"
UTF8_NAME = "sp\u00e9.py"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash"
)


def _env(tmp: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(tmp / "gitconfig"),
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
        CQA_PYTHON=sys.executable,
    )
    (tmp / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n[protocol \"file\"]\n\tallow = always\n")
    return env


def _git(env, cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def pr(tmp_path: Path):
    env = _env(tmp_path)
    origin = tmp_path / "origin"
    (origin / "sub").mkdir(parents=True)
    _git(env, origin, "init", "-q")
    (origin / "sub" / "x.py").write_text("a\nb\nc\nd\n")
    (origin / "sub" / "sp ace.py").write_text("q\n")
    (origin / "sub" / UTF8_NAME).write_text("q\n")
    (origin / "sub" / "other.py").write_text("1\n2\n")
    (origin / "outside.py").write_text("o\n")
    _git(env, origin, "add", "-A")
    _git(env, origin, "commit", "-qm", "fork point")
    _git(env, origin, "checkout", "-qb", "feature")
    (origin / "sub" / "x.py").write_text("a\nc\nd\nNEW\n")  # delete b, add NEW at 4
    (origin / "sub" / "sp ace.py").write_text("q\nr\n")
    (origin / "sub" / UTF8_NAME).write_text("q\nr\n")
    (origin / "outside.py").write_text("o\np\n")
    # A PR-controlled attribute that would hide every change as "binary".
    (origin / ".gitattributes").write_text("*.py -diff\n")
    _git(env, origin, "add", "-A")
    _git(env, origin, "commit", "-qm", "pr")
    head = _git(env, origin, "rev-parse", "HEAD")
    _git(env, origin, "checkout", "-q", "main")
    (origin / "sub" / "other.py").write_text("0\n1\n2\n")  # base moves on
    _git(env, origin, "commit", "-qam", "main moves")
    base = _git(env, origin, "rev-parse", "HEAD")
    _git(env, origin, "checkout", "-qb", "pr-merge")
    _git(env, origin, "merge", "-q", "--no-ff", "-m", "merge", head)
    _git(env, origin, "checkout", "-q", "main")
    return env, tmp_path, base, head


def _clone(env, tmp: Path, branch: str, *, shallow: bool) -> Path:
    dest = tmp / f"clone-{branch}-{shallow}"
    args = ["clone", "-q", "--branch", branch]
    if shallow:
        args += ["--depth=1"]
    _git(env, tmp, *args, f"file://{tmp / 'origin'}", str(dest))
    # Hostile local diff config must be ignored too.
    _git(env, dest, "config", "diff.external", "false")
    _git(env, dest, "config", "diff.noprefix", "true")
    return dest


def _run(env, base, head, project: Path, out: Path, *extra: str):
    return subprocess.run(  # noqa: S603 - fixed argv
        ["bash", str(SCRIPT), base, head, str(project), str(out), *extra],
        env=env, capture_output=True, text=True, check=False,
    )


def _files(out: Path) -> dict:
    return {f["path"]: f["ranges"] for f in json.loads(out.read_text())["files"]}


def test_shallow_merge_checkout_diffs_against_its_base_parent(pr):
    env, tmp, base, head = pr
    clone = _clone(env, tmp, "pr-merge", shallow=True)
    out = tmp / "m.json"
    result = _run(env, base, head, clone / "sub", out)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _files(out) == {
        # relative to the analyzed subdirectory; base-only other.py excluded;
        # .gitattributes "-diff" did not hide anything
        "sp ace.py": [{"start_line": 2, "end_line": 2}],
        UTF8_NAME: [{"start_line": 2, "end_line": 2}],
        "x.py": [{"start_line": 4, "end_line": 4}],
    }


def test_include_deletions_anchors_following_line(pr):
    env, tmp, base, head = pr
    clone = _clone(env, tmp, "pr-merge", shallow=True)
    out = tmp / "m.json"
    result = _run(env, base, head, clone / "sub", out, "--include-deletions")
    assert result.returncode == 0, result.stderr
    assert _files(out)["x.py"] == [
        {"start_line": 2, "end_line": 2},
        {"start_line": 4, "end_line": 4},
    ]


def test_full_head_checkout_uses_merge_base_not_two_dot_diff(pr):
    env, tmp, base, head = pr
    clone = _clone(env, tmp, "feature", shallow=False)
    _git(env, clone, "fetch", "-q", "origin", "main")
    out = tmp / "m.json"
    result = _run(env, base, head, clone, out)
    assert result.returncode == 0, result.stderr
    files = _files(out)
    assert "sub/other.py" not in files  # a two-dot base..head diff would list it
    assert files["outside.py"] == [{"start_line": 2, "end_line": 2}]
    assert files["sub/x.py"] == [{"start_line": 4, "end_line": 4}]


def test_shallow_head_checkout_fails_visibly(pr):
    env, tmp, base, head = pr
    clone = _clone(env, tmp, "feature", shallow=True)
    out = tmp / "m.json"
    result = _run(env, base, head, clone, out)
    assert result.returncode == 1
    assert "::error::changed-lines: no merge base" in result.stdout
    assert "fetch-depth: 0" in result.stdout
    assert not out.exists()


def test_unfetchable_base_fails_visibly(pr):
    env, tmp, _, head = pr
    clone = _clone(env, tmp, "feature", shallow=False)
    out = tmp / "m.json"
    result = _run(env, "0" * 40, head, clone, out)
    assert result.returncode == 1
    assert "is not available locally and could not be fetched" in result.stdout


@pytest.mark.parametrize("bad", ["main", "-n", "a" * 39, "A" * 40])
def test_malformed_shas_are_rejected_before_git_sees_them(pr, bad):
    env, tmp, _, head = pr
    result = _run(env, bad, head, tmp / "origin", tmp / "m.json")
    assert result.returncode == 1
    assert "not a full hex object id" in result.stdout
