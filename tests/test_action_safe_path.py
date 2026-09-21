"""action.yml runs Python inside the untrusted PR checkout.

`python -m`, `python -c` and `python -` all prepend the current directory
to sys.path, so a pull request committing `json/__init__.py` (or
`pip/__init__.py`) would execute inside the gate job. `-P` (Python 3.11+)
disables that entry. Every such invocation in action.yml must carry it;
`python "$ACTION_PATH/script.py"` is exempt because the script's own
directory, not the cwd, is what gets prepended.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ACTION = Path(__file__).resolve().parents[1] / "action.yml"

# `python` followed by options, where the program comes from -m / -c / - .
_CWD_EXPOSED = re.compile(r"\bpython(?P<opts>(?:\s+-[A-Za-z]+)*)\s+(?:-m\b|-c\b|-\s)")


def _invocations() -> list[tuple[int, str, str]]:
    out = []
    for lineno, line in enumerate(ACTION.read_text().splitlines(), 1):
        for match in _CWD_EXPOSED.finditer(line):
            out.append((lineno, line.strip(), match.group("opts")))
    return out


def test_action_has_cwd_exposed_python_invocations():
    # Guard against the regex silently matching nothing.
    assert len(_invocations()) >= 4


def test_every_cwd_exposed_invocation_carries_P():
    missing = [
        (lineno, line)
        for lineno, line, opts in _invocations()
        if "P" not in opts.replace("-", "")
    ]
    assert missing == [], f"python invocations without -P: {missing}"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="-P is 3.11+")
def test_P_defeats_stdlib_shadowing_from_cwd(tmp_path: Path):
    (tmp_path / "json").mkdir()
    (tmp_path / "json" / "__init__.py").write_text(
        "def dumps(_):\n    return 'SHADOWED'\n"
    )
    program = "import json; print(json.dumps({}))"

    hijacked = subprocess.run(
        [sys.executable, "-", ], input=program, text=True,
        capture_output=True, cwd=tmp_path, check=True,
    )
    assert hijacked.stdout.strip() == "SHADOWED"  # the attack is real

    safe = subprocess.run(
        [sys.executable, "-P", "-"], input=program, text=True,
        capture_output=True, cwd=tmp_path, check=True,
    )
    assert safe.stdout.strip() == "{}"
